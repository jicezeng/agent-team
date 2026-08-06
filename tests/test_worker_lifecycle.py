from __future__ import annotations

import asyncio
import fcntl
import json
import os
import pty
import subprocess
import sys
import termios
import tty
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_team.adapters.base import LaunchSpec
from agent_team.adapters.codex import CodexAdapter
from agent_team.bootstrap import initialize_run, start_run
from agent_team.config import (
    REQUIRED_AUDIT_PAYLOAD_SECTIONS,
    ObservabilityPolicy,
    Role,
    make_team,
)
from agent_team.errors import AgentTeamError, IntegrityError
from agent_team.journal import scan_journal
from agent_team.management import cancel_run, recover_run, unlock_workspace
from agent_team.observation import derive_observation
from agent_team.origin import origin_action, wait_origin
from agent_team.ownership import release_terminal_owner_locked
from agent_team.processes import current_identity
from agent_team.state import locked_run, read_owner, state_paths
from agent_team.supervisor import (
    StreamRecorder,
    _base_snapshot,
    _fresh_interactive_session_candidate,
    _relay_terminal_input,
    supervise_turn,
    validate_supervisor,
)
from agent_team.tmux_runtime import session_name
from agent_team.turns import (
    create_business_turn_locked,
    iter_runtimes,
    load_runtime,
    render_turn_prompt,
    save_runtime,
    stage_external_action_locked,
    validate_runtime,
)
from agent_team.util import atomic_json, atomic_write, read_json, rfc3339
from agent_team.worker import (
    _authorize_launch_locked,
    _launch_turn,
    finalize_external_turn_locked,
)

PROFILE = "test-noninteractive"
PROFILE_HASH = "0" * 64
NONCE = "test-launch-nonce"
SUPERVISOR_PID = 700_001
RUNNER_PID = 700_002


def test_interactive_terminal_input_is_raw_and_tty_state_is_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_master, source_slave = pty.openpty()
    destination_master, destination_slave = pty.openpty()
    original_termios = termios.tcgetattr(source_slave)
    original_flags = fcntl.fcntl(source_slave, fcntl.F_GETFL)
    tty.setraw(destination_slave, when=termios.TCSANOW)
    os.set_blocking(destination_slave, False)
    monkeypatch.setattr(
        "agent_team.supervisor.sys",
        SimpleNamespace(stdin=SimpleNamespace(fileno=lambda: source_slave)),
    )

    async def exercise() -> bytes:
        task = asyncio.create_task(_relay_terminal_input(destination_master))
        try:
            for _ in range(100):
                current = termios.tcgetattr(source_slave)
                if not current[3] & termios.ICANON and not current[3] & termios.ECHO:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("terminal input relay did not enter raw mode")
            payload = b"\r\x1b[A\x03"
            os.write(source_master, payload)
            received = b""
            for _ in range(100):
                try:
                    received += os.read(destination_slave, 4096)
                except BlockingIOError:
                    pass
                if len(received) >= len(payload):
                    return received
                await asyncio.sleep(0.01)
            raise AssertionError(f"terminal input relay returned {received!r}")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    try:
        assert asyncio.run(exercise()) == b"\r\x1b[A\x03"
        restored_termios = termios.tcgetattr(source_slave)
        # Darwin may add the transient PENDIN state bit when tcsetattr restores
        # canonical input. It is not a configured terminal mode.
        pendin = getattr(termios, "PENDIN", 0)
        restored_termios[3] &= ~pendin
        original_termios[3] &= ~pendin
        assert restored_termios == original_termios
        assert fcntl.fcntl(source_slave, fcntl.F_GETFL) == original_flags
    finally:
        for fd in (
            source_master,
            source_slave,
            destination_master,
            destination_slave,
        ):
            os.close(fd)


class _BootstrapAdapter:
    def __init__(self, launch_mode: str = "headless") -> None:
        self.launch_mode = launch_mode

    def probe(self) -> SimpleNamespace:
        return SimpleNamespace(
            authenticated=True,
            launcher_stays_in_process_group=True,
        )

    def assert_profile(
        self,
        profile: str,
        session_policy: str,
        expected_hash: str,
        launch_mode: str = "headless",
    ) -> None:
        assert profile == PROFILE
        assert session_policy == "fresh"
        assert expected_hash == PROFILE_HASH
        assert launch_mode == self.launch_mode

    def assert_launch_options(self, options: Any) -> None:
        assert options.model is None
        assert options.reasoning_effort is None
        assert options.fast_mode is None

    def prepare_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> None:
        assert run_dir.name.startswith("at-")
        assert role_id == "developer"
        assert launch_mode == self.launch_mode

    def finalize_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> None:
        assert run_dir.name.startswith("at-")
        assert role_id == "developer"
        assert launch_mode == self.launch_mode


class _Logger:
    def write(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _external_run(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str,
    max_turns: int = 4,
    max_wall_time_seconds: int = 300,
    include_origin_reviewer: bool = False,
    observability: ObservabilityPolicy | None = None,
    launch_mode: str = "headless",
) -> tuple[Path, dict[str, Any]]:
    request, protocol = request_protocol
    adapter = _BootstrapAdapter(launch_mode)
    monkeypatch.setattr("agent_team.bootstrap.get_adapter", lambda _adapter: adapter)
    monkeypatch.setattr("agent_team.bootstrap.tmux_executable", lambda: "/bin/true")
    empty_tmux = {
        "session": "test-session",
        "created": [],
        "existing": ["developer"],
    }
    monkeypatch.setattr(
        "agent_team.bootstrap.ensure_workers",
        lambda _run_dir, _team: empty_tmux,
    )
    monkeypatch.setattr(
        "agent_team.management.ensure_workers",
        lambda _run_dir, _team: empty_tmux,
    )
    monkeypatch.setattr("agent_team.bootstrap.signal_change", lambda *_args: False)
    monkeypatch.setattr("agent_team.management.signal_change", lambda *_args: False)
    roles = {
        "developer": Role(
            "developer",
            "external",
            "codex",
            "fresh",
            PROFILE,
            PROFILE_HASH,
            launch_mode=launch_mode,
        )
    }
    if include_origin_reviewer:
        roles["reviewer"] = Role("reviewer", "origin")
    team = make_team(
        run_id=run_id,
        workspace=workspace,
        origin_harness="codex",
        roles=roles,
        initial_role="developer",
        max_turns=max_turns,
        max_wall_time_seconds=max_wall_time_seconds,
        observability=observability,
    )
    run_dir = initialize_run(
        team=team,
        request_path=request,
        protocol_path=protocol,
    )
    start_run(run_dir)
    identity = current_identity()
    atomic_json(
        run_dir / "roles" / "developer.json",
        {
            "schema_version": 1,
            "role_id": "developer",
            "worker_pid": identity.pid,
            "worker_start_id": identity.start_id,
            "tmux_session": session_name(run_id),
            "tmux_pane_id": "%test",
            "updated_at": rfc3339(),
        },
    )
    with locked_run(run_dir, exclusive=True):
        runtime, continuity_error = create_business_turn_locked(
            run_dir,
            role_id="developer",
            executor="worker",
        )
    assert runtime is not None
    assert continuity_error is None
    return run_dir, runtime


def test_external_turn_prompt_disambiguates_skill_from_formal_cli(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-prompt-formal-cli",
    )

    prompt = render_turn_prompt(
        run_dir,
        runtime,
        cli_path="/opt/agent-team/bin/agent-team",
        session_ref=None,
    )

    assert "documentation, not an action interface" in prompt
    assert "has no `--complete`, `--summary`" in prompt
    assert "Do not invoke the Skill again to transition state" in prompt
    assert (
        "`/opt/agent-team/bin/agent-team handoff "
        "--to <role-id> --file <payload>`"
    ) in prompt


def test_init_rejects_launcher_that_detaches_from_managed_group(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, protocol = request_protocol
    adapter = _BootstrapAdapter()
    monkeypatch.setattr(
        adapter,
        "probe",
        lambda: SimpleNamespace(
            authenticated=True,
            launcher_stays_in_process_group=False,
        ),
    )
    monkeypatch.setattr("agent_team.bootstrap.get_adapter", lambda _adapter: adapter)
    team = make_team(
        run_id="at-worker-detaching-launcher",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "fresh",
                PROFILE,
                PROFILE_HASH,
            )
        },
        initial_role="developer",
        max_turns=4,
        max_wall_time_seconds=300,
    )

    with pytest.raises(AgentTeamError) as rejected:
        initialize_run(
            team=team,
            request_path=request,
            protocol_path=protocol,
        )

    assert rejected.value.code == "HARNESS_PROCESS_MODEL_UNSUPPORTED"
    assert not (workspace / ".agent-team" / "runs" / team.run_id).exists()


def test_init_rejects_unauthenticated_external_harness(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, protocol = request_protocol
    adapter = _BootstrapAdapter()
    monkeypatch.setattr(
        adapter,
        "probe",
        lambda: SimpleNamespace(
            authenticated=False,
            launcher_stays_in_process_group=True,
        ),
    )
    monkeypatch.setattr("agent_team.bootstrap.get_adapter", lambda _adapter: adapter)
    team = make_team(
        run_id="at-worker-unauthenticated",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "fresh",
                PROFILE,
                PROFILE_HASH,
            )
        },
        initial_role="developer",
        max_turns=4,
        max_wall_time_seconds=300,
    )

    with pytest.raises(AgentTeamError) as rejected:
        initialize_run(
            team=team,
            request_path=request,
            protocol_path=protocol,
        )

    assert rejected.value.code == "HARNESS_NOT_AUTHENTICATED"
    assert not (workspace / ".agent-team").exists()


def _launch_spec(run_dir: Path, runtime: dict[str, Any]) -> LaunchSpec:
    turn_id = runtime["turn_id"]
    return LaunchSpec(
        adapter_id="codex",
        argv=("/bin/true",),
        cwd=str(run_dir.parent.parent.parent),
        env={
            "AGENT_TEAM_RUN_ID": run_dir.name,
            "AGENT_TEAM_ROLE_ID": "developer",
            "AGENT_TEAM_TURN_ID": turn_id,
            "AGENT_TEAM_RUN_DIR": str(run_dir),
            "AGENT_TEAM_TURN_DIR": str(run_dir / "turns" / turn_id),
            "AGENT_TEAM_CLI": "/bin/true",
        },
        stdin="test prompt\n",
        launch_profile=PROFILE,
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
    )


def _configure_supervisor_child_state(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    directory_name: str,
) -> None:
    state_dir = read_json(workspace / ".agent-team" / "root.json")[
        "state_dir_realpath"
    ]
    child_bootstrap = workspace.parent / directory_name
    child_bootstrap.mkdir()
    (child_bootstrap / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        "import agent_team.state as _agent_team_state\n"
        f"_agent_team_state.fixed_state_dir = lambda: Path({state_dir!r})\n",
        encoding="utf-8",
    )
    inherited_pythonpath = os.environ.get("PYTHONPATH")
    monkeypatch.setenv(
        "PYTHONPATH",
        str(child_bootstrap)
        if not inherited_pythonpath
        else f"{child_bootstrap}{os.pathsep}{inherited_pythonpath}",
    )


def _persist_process_chain(
    run_dir: Path,
    runtime: dict[str, Any],
    *,
    supervisor_state: str = "finished",
    supervisor_has_runner: bool = True,
    write_runner: bool = True,
    write_authorization: bool = True,
    execution_started: bool = True,
    runtime_phase: str = "running",
    runtime_has_identities: bool = True,
) -> tuple[LaunchSpec, dict[str, Any]]:
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700)
    launch = _launch_spec(run_dir, runtime)
    runtime["launch_nonce"] = NONCE
    runtime["phase"] = runtime_phase
    if runtime_has_identities:
        runtime.update(
            {
                "supervisor_pid": SUPERVISOR_PID,
                "supervisor_start_id": "supervisor-start",
                "runner_pid": RUNNER_PID,
                "runner_pgid": RUNNER_PID,
                "runner_start_id": "runner-start",
            }
        )
    save_runtime(turn_dir, runtime, team=scan_journal(run_dir).team)
    atomic_json(process_dir / "launch.json", launch.to_json(), immutable=True)
    runner = {
        "schema_version": 1,
        "turn_id": runtime["turn_id"],
        "launch_nonce": NONCE,
        "runner_pid": RUNNER_PID,
        "runner_pgid": RUNNER_PID,
        "runner_start_id": "runner-start",
        "created_at": rfc3339(),
    }
    if write_runner:
        atomic_json(process_dir / "runner.json", runner, immutable=True)
    supervisor = _base_snapshot(runtime["turn_id"], NONCE)
    supervisor.update(
        {
            "state": supervisor_state,
            "supervisor_pid": SUPERVISOR_PID,
            "supervisor_start_id": "supervisor-start",
            "runner_pid": RUNNER_PID if supervisor_has_runner else None,
            "runner_pgid": RUNNER_PID if supervisor_has_runner else None,
            "runner_start_id": ("runner-start" if supervisor_has_runner else None),
            "agent_execution_started": execution_started,
            "adapter_completed": execution_started,
            "observed_session_ref": (
                f"thread-{runtime['turn_id']}" if execution_started else None
            ),
            "process_exit_code": 0 if supervisor_state == "finished" else None,
            "termination_kind": ("normal" if supervisor_state == "finished" else None),
            "group_quiescent": supervisor_state == "finished",
            "updated_at": rfc3339(),
        }
    )
    atomic_json(
        process_dir / "supervisor.json",
        supervisor,
        immutable=True,
    )
    if write_authorization:
        atomic_json(
            process_dir / "launch-authorized.json",
            {
                "schema_version": 1,
                "turn_id": runtime["turn_id"],
                "launch_nonce": NONCE,
                "supervisor_pid": SUPERVISOR_PID,
                "supervisor_start_id": "supervisor-start",
                "runner_pid": RUNNER_PID,
                "runner_pgid": RUNNER_PID,
                "runner_start_id": "runner-start",
                "launch_profile": PROFILE,
                "launch_profile_sha256": PROFILE_HASH,
                "authorized_at": rfc3339(),
            },
            immutable=True,
        )
    return launch, supervisor


def test_finished_supervisor_missing_runner_is_corruption(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-missing-runner",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            runtime,
            write_runner=False,
            write_authorization=False,
            runtime_phase="starting",
            runtime_has_identities=False,
        )
        with pytest.raises(IntegrityError, match="Runner identity is missing"):
            finalize_external_turn_locked(run_dir, runtime)
    with pytest.raises(IntegrityError, match="Runner identity is missing"):
        derive_observation(run_dir)


def test_execution_evidence_without_authorization_is_corruption(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-missing-authorization",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            runtime,
            write_authorization=False,
            runtime_phase="starting",
        )
        with pytest.raises(IntegrityError, match="unique launch authorization"):
            finalize_external_turn_locked(run_dir, runtime)
    with pytest.raises(IntegrityError, match="unique launch authorization"):
        derive_observation(run_dir)


def test_corrupt_outbox_on_unique_turn_becomes_recovery_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-corrupt-outbox",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        atomic_json(
            run_dir / "turns" / runtime["turn_id"] / "outbox.json",
            {"schema_version": 1},
            immutable=True,
        )
        event = finalize_external_turn_locked(run_dir, runtime)
    assert event is not None
    assert event["block_reason"] == "recovery"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "failed"
    observation = derive_observation(run_dir)
    assert observation["run_status"] == "BLOCKED"
    assert observation["health"] == "attention"
    assert observation["recommended_action"] == "CLAIM_ORIGIN_EVENT"


def test_corrupt_before_facts_on_unique_turn_becomes_recovery_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-corrupt-before-facts",
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        (turn_dir / "workspace-facts-before.json").write_text(
            '{"schema_version": 1}\n',
            encoding="utf-8",
        )
        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )
    assert event is not None
    assert event["block_reason"] == "recovery"
    persisted = load_runtime(turn_dir, team=scan_journal(run_dir).team)
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "failed"
    observation = derive_observation(run_dir)
    assert observation["run_status"] == "BLOCKED"
    assert observation["health"] == "attention"


def test_orphaned_outbox_payload_becomes_recovery_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-orphaned-outbox-payload",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        atomic_write(
            run_dir / "turns" / runtime["turn_id"] / "outbox-payload.md",
            b"# Frozen action payload\n",
            immutable=True,
        )
    observation = derive_observation(run_dir)
    assert observation["health"] == "recovery_required"
    assert observation["recommended_action"] == "RUN_RECOVER"
    with locked_run(run_dir, exclusive=True):
        event = finalize_external_turn_locked(run_dir, runtime)
    assert event is not None
    assert event["block_reason"] == "recovery"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "failed"


@pytest.mark.parametrize("with_finished_process", [False, True])
def test_unique_damaged_runtime_becomes_recovery_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    with_finished_process: bool,
) -> None:
    suffix = "finished" if with_finished_process else "prelaunch"
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id=f"at-worker-corrupt-runtime-{suffix}",
    )
    with locked_run(run_dir, exclusive=True):
        if with_finished_process:
            _persist_process_chain(run_dir, runtime)
        role_path = run_dir / "roles" / "developer.json"
        role_snapshot = read_json(role_path)
        role_snapshot.update(
            {
                "worker_pid": 700_003,
                "worker_start_id": "gone-worker",
            }
        )
        atomic_json(role_path, role_snapshot)
        runtime_path = run_dir / "turns" / runtime["turn_id"] / "runtime.json"
        damaged = read_json(runtime_path)
        damaged.pop("phase")
        runtime_path.write_text(
            json.dumps(damaged, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    result = recover_run(run_dir)

    assert result["status"] == "BLOCKED"
    assert result["actions"] == [
        f"runtime-recovery-block:{runtime['turn_id']}:block-0002"
    ]
    projection = scan_journal(run_dir)
    assert projection.tail["event_type"] == "block"
    assert projection.tail["block_reason"] == "recovery"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=projection.team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "failed"
    assert persisted["group_quiescent"] is True
    assert derive_observation(run_dir)["run_status"] == "BLOCKED"

    repeated = recover_run(run_dir)
    assert repeated["status"] == "BLOCKED"
    assert repeated["actions"] == []
    assert len(scan_journal(run_dir).events) == 2


def test_multiple_damaged_turn_identities_remain_corrupted(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-ambiguous-corrupt-runtime",
    )
    with locked_run(run_dir, exclusive=True):
        role_path = run_dir / "roles" / "developer.json"
        role_snapshot = read_json(role_path)
        role_snapshot.update(
            {
                "worker_pid": 700_003,
                "worker_start_id": "gone-worker",
            }
        )
        atomic_json(role_path, role_snapshot)
        first_runtime = run_dir / "turns" / runtime["turn_id"] / "runtime.json"
        first_runtime.write_text('{"schema_version": 1}\n', encoding="utf-8")
        second = run_dir / "turns" / "turn-0002"
        second.mkdir(mode=0o700)
        atomic_write(
            second / "input.md",
            (run_dir / "turns" / runtime["turn_id"] / "input.md").read_bytes(),
            immutable=True,
        )
        atomic_json(
            second / "runtime.json",
            {"schema_version": 1},
            immutable=True,
        )

    with pytest.raises(IntegrityError):
        recover_run(run_dir)

    assert len(
        list((run_dir / "events").glob("*.json"))
    ) == 1


def test_repeated_start_converges_unique_damaged_runtime(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-start-corrupt-runtime",
    )
    with locked_run(run_dir, exclusive=True):
        role_path = run_dir / "roles" / "developer.json"
        role_snapshot = read_json(role_path)
        role_snapshot.update(
            {
                "worker_pid": 700_003,
                "worker_start_id": "gone-worker",
            }
        )
        atomic_json(role_path, role_snapshot)
        runtime_path = run_dir / "turns" / runtime["turn_id"] / "runtime.json"
        damaged = read_json(runtime_path)
        damaged.pop("phase")
        runtime_path.write_text(
            json.dumps(damaged, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    result = start_run(run_dir)

    assert result["status"] == "BLOCKED"
    assert result["kickoff_event"] is None
    assert result["recovery_actions"] == [
        f"runtime-recovery-block:{runtime['turn_id']}:block-0002"
    ]
    assert len(scan_journal(run_dir).events) == 2


def test_finished_supervisor_is_not_exited_until_process_is_gone(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-finished-supervisor-live",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
    worker = current_identity()
    monkeypatch.setattr(
        "agent_team.observation.list_windows",
        lambda _run_id: {
            "developer": {
                "tmux_pane_id": "%test",
                "pane_pid": worker.pid,
            }
        },
    )
    monkeypatch.setattr(
        "agent_team.observation.process_identity_state",
        lambda pid, _start_id: (
            "match" if pid in {worker.pid, SUPERVISOR_PID} else "gone"
        ),
    )

    observation = derive_observation(run_dir)

    assert observation["active_turn"]["managed_process_state"] == "stopping"
    assert observation["health"] == "ok"
    assert observation["recommended_action"] == "WAIT"


def test_unknown_supervisor_identity_activates_recovery_gate(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-supervisor-identity-unknown",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            runtime,
            supervisor_state="running",
        )
    worker = current_identity()
    monkeypatch.setattr(
        "agent_team.observation.list_windows",
        lambda _run_id: {
            "developer": {
                "tmux_pane_id": "%test",
                "pane_pid": worker.pid,
            }
        },
    )
    monkeypatch.setattr(
        "agent_team.observation.process_identity_state",
        lambda pid, _start_id: "unknown" if pid == SUPERVISOR_PID else "match",
    )

    observation = derive_observation(run_dir)

    assert observation["active_turn"]["managed_process_state"] == "identity_unknown"
    assert observation["recovery_required"] is True
    assert observation["health"] == "recovery_required"
    assert observation["recommended_action"] == "RUN_RECOVER"


def test_tmux_query_failure_is_recoverable_runtime_loss(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, _runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-tmux-query-failure",
    )

    def fail_list_windows(_run_id: str) -> dict[str, dict[str, Any]]:
        raise AgentTeamError("TMUX_COMMAND_FAILED", "tmux server unavailable")

    monkeypatch.setattr(
        "agent_team.observation.list_windows",
        fail_list_windows,
    )

    observation = derive_observation(run_dir)

    assert observation["run_status"] == "RUNNING"
    assert observation["health"] == "attention"
    assert observation["recommended_action"] == "RUN_RECOVER"
    assert observation["roles"][0]["tmux_pane_id"] is None


def test_observation_rejects_unexpected_tmux_role_window(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, _runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-unexpected-tmux-window",
    )
    monkeypatch.setattr(
        "agent_team.observation.list_windows",
        lambda _run_id: {
            "intruder": {
                "tmux_pane_id": "%unexpected",
                "pane_pid": 900_001,
                "pane_dead": False,
            }
        },
    )

    with pytest.raises(IntegrityError, match="unexpected role windows"):
        derive_observation(run_dir)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_generation", True),
        ("group_quiescent", 0),
        ("supervisor_pid", True),
        ("executor", []),
        ("phase", []),
        ("outcome", []),
        ("termination_kind", []),
    ],
)
def test_runtime_rejects_boolean_values_in_typed_fields(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    _run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id=f"at-worker-runtime-type-{field.replace('_', '-')}",
    )
    runtime[field] = value
    if field == "supervisor_pid":
        runtime["supervisor_start_id"] = "supervisor-start"

    with pytest.raises(IntegrityError):
        validate_runtime(runtime)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", []),
        ("termination_kind", []),
    ],
)
def test_supervisor_rejects_unhashable_discriminators(
    field: str,
    value: object,
) -> None:
    snapshot = _base_snapshot("turn-0001", NONCE)
    snapshot[field] = value

    with pytest.raises(IntegrityError):
        validate_supervisor(snapshot)


def test_session_snapshot_must_reference_same_role_runtime_lineage(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, _runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-session-lineage",
    )
    atomic_json(
        run_dir / "sessions" / "developer.json",
        {
            "schema_version": 1,
            "role_id": "developer",
            "adapter": "codex",
            "generation": 1,
            "status": "available",
            "session_ref": "thread-one",
            "effective_launch_profile": PROFILE,
            "effective_launch_profile_sha256": PROFILE_HASH,
            "created_turn_id": "turn-9999",
            "updated_turn_id": "turn-9999",
            "unavailable_reason": None,
            "updated_at": rfc3339(),
        },
    )

    with pytest.raises(IntegrityError, match="unknown Turn"):
        scan_journal(run_dir)


def test_runtime_git_boundary_violation_never_appends_an_event(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-git-boundary",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "add",
            "-f",
            ".agent-team/root.json",
        ],
        check=True,
    )
    with pytest.raises(IntegrityError, match="Git workspace boundary"):
        derive_observation(run_dir)
    with locked_run(run_dir, exclusive=True):
        with pytest.raises(IntegrityError, match="Git workspace boundary"):
            finalize_external_turn_locked(run_dir, runtime)
    assert scan_journal(run_dir).tail["event_type"] == "kickoff"


def test_normal_exit_without_outbox_commits_stalled_no_action(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-no-action",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )
    assert event is not None
    assert event["event_type"] == "block"
    assert event["block_reason"] == "no_action"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "stalled"
    assert persisted["workspace_facts_after_sha256"] is not None


def test_external_action_enforces_audited_rationale_and_evidence_contract(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-payload-contract",
        observability=ObservabilityPolicy(
            required_payload_sections=REQUIRED_AUDIT_PAYLOAD_SECTIONS,
        ),
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    payload = turn_dir / "completion-source.md"
    payload.write_text("# Completion\n\nDone.\n", encoding="utf-8")

    with locked_run(run_dir, exclusive=True):
        with pytest.raises(AgentTeamError) as rejected:
            stage_external_action_locked(
                run_dir,
                runtime=runtime,
                action="complete",
                source_file=payload,
                to_role=None,
            )
        assert rejected.value.code == "PAYLOAD_CONTRACT_VIOLATION"
        payload.write_text(
            "# Completion\n\n"
            "## Decision rationale\n\n"
            "The requested change is complete.\n\n"
            "## Evidence\n\n"
            "The targeted tests pass.\n",
            encoding="utf-8",
        )
        accepted = stage_external_action_locked(
            run_dir,
            runtime=runtime,
            action="complete",
            source_file=payload,
            to_role=None,
        )

    assert accepted["code"] == "ACTION_ACCEPTED"


def test_full_audit_mode_blocks_a_turn_when_capture_is_truncated(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-full-audit-truncated",
        observability=ObservabilityPolicy(
            audit_mode="full",
            max_trace_bytes=1024,
            required_payload_sections=REQUIRED_AUDIT_PAYLOAD_SECTIONS,
        ),
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        atomic_json(
            turn_dir / "process" / "capture.json",
            {
                "schema_version": 1,
                "source_bytes": 2048,
                "stored_source_bytes": 1024,
                "dropped_source_bytes": 1024,
                "chunks_observed": 2,
                "chunks_stored": 1,
                "truncated": True,
                "closed_at": rfc3339(),
            },
            immutable=True,
        )
        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )

    assert event is not None
    assert event["event_type"] == "block"
    assert event["block_reason"] == "recovery"
    persisted = load_runtime(
        turn_dir,
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["trace_manifest_sha256"] is not None
    assert derive_observation(run_dir)["run_status"] == "BLOCKED"


def test_external_handoff_on_final_business_turn_blocks_without_staging_outbox(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-final-turn-handoff",
        max_turns=1,
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    payload = turn_dir / "handoff-source.md"
    payload.write_text("# Handoff\n\nContinue development.\n", encoding="utf-8")

    with locked_run(run_dir, exclusive=True):
        blocked = stage_external_action_locked(
            run_dir,
            runtime=runtime,
            action="handoff",
            source_file=payload,
            to_role="developer",
        )

    assert blocked["code"] == "TEAM_BLOCKED"
    assert blocked["event"]["block_reason"] == "limit"
    assert blocked["event"]["limit_reason"] == "max_turns"
    assert scan_journal(run_dir).status == "BLOCKED"
    assert not (turn_dir / "outbox.json").exists()
    business = [
        item
        for item in iter_runtimes(run_dir)
        if item["business_turn_seq"] is not None
    ]
    assert len(business) == 1


def test_external_handoff_to_origin_is_idempotent_claimable_and_completable(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-handoff-to-origin",
        include_origin_reviewer=True,
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    payload = turn_dir / "handoff-source.md"
    payload.write_text("# Handoff\n\nPlease review the current workspace.\n")

    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        with pytest.raises(AgentTeamError) as missing:
            stage_external_action_locked(
                run_dir,
                runtime=runtime,
                action="handoff",
                source_file=payload,
                to_role="missing",
            )
        assert missing.value.code == "ROLE_NOT_FOUND"
        accepted = stage_external_action_locked(
            run_dir,
            runtime=runtime,
            action="handoff",
            source_file=payload,
            to_role="reviewer",
        )
        repeated = stage_external_action_locked(
            run_dir,
            runtime=runtime,
            action="handoff",
            source_file=payload,
            to_role="reviewer",
        )
        assert accepted["code"] == "ACTION_ACCEPTED"
        assert repeated["code"] == "ACTION_ALREADY_ACCEPTED"
        with pytest.raises(AgentTeamError) as conflict:
            stage_external_action_locked(
                run_dir,
                runtime=runtime,
                action="complete",
                source_file=payload,
                to_role=None,
            )
        assert conflict.value.code == "TURN_ACTION_CONFLICT"
        handoff = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )

    assert handoff is not None
    assert handoff["event_type"] == "handoff"
    assert handoff["from_role"] == "developer"
    assert handoff["to_role"] == "reviewer"
    claim = wait_origin(run_dir, timeout=0)
    assert claim["code"] == "HANDOFF_TO_ORIGIN_ROLE"
    assert claim["role_id"] == "reviewer"
    assert claim["event"]["event_id"] == handoff["event_id"]

    completion = run_dir / "turns" / claim["turn_id"] / "completion.md"
    completion.write_text("# Completion\n\nReview complete.\n")
    completed = origin_action(
        run_dir,
        action="complete",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=completion,
    )
    assert completed["code"] == "TEAM_COMPLETED"
    delivered = wait_origin(
        run_dir,
        timeout=0,
        claim=claim["claim"],
    )
    assert delivered["code"] == "TEAM_COMPLETED"
    assert read_owner(workspace) is None


def test_terminal_owner_release_rechecks_supervisor_and_runner_group(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-release-liveness",
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    completion = turn_dir / "completion-source.md"
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        atomic_write(completion, b"# Completion\n\nDone.\n", immutable=True)
        stage_external_action_locked(
            run_dir,
            runtime=runtime,
            action="complete",
            source_file=completion,
            to_role=None,
        )
        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )
    assert event is not None and event["event_type"] == "complete"

    monkeypatch.setattr(
        "agent_team.ownership.process_identity_state",
        lambda pid, *_args, **_kwargs: (
            "match" if pid == SUPERVISOR_PID else "gone"
        ),
    )
    monkeypatch.setattr(
        "agent_team.ownership.process_group_exists",
        lambda _pgid: False,
    )
    with locked_run(run_dir, exclusive=True):
        assert not release_terminal_owner_locked(run_dir)
    assert read_owner(workspace) is not None

    monkeypatch.setattr(
        "agent_team.ownership.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    monkeypatch.setattr(
        "agent_team.ownership.process_group_exists",
        lambda _pgid: True,
    )
    with locked_run(run_dir, exclusive=True):
        assert not release_terminal_owner_locked(run_dir)
    assert read_owner(workspace) is not None

    monkeypatch.setattr(
        "agent_team.ownership.process_group_exists",
        lambda _pgid: False,
    )
    with locked_run(run_dir, exclusive=True):
        assert release_terminal_owner_locked(run_dir)
    assert read_owner(workspace) is None


def test_unlock_uses_process_evidence_when_runtime_schema_is_damaged(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-unlock-damaged-runtime",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
    runtime_path = run_dir / "turns" / runtime["turn_id"] / "runtime.json"
    damaged = read_json(runtime_path)
    damaged["schema_version"] = 999
    runtime_path.write_text(
        json.dumps(damaged, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("agent_team.management.has_session", lambda _run: False)
    monkeypatch.setattr(
        "agent_team.management.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    monkeypatch.setattr(
        "agent_team.management.process_group_exists",
        lambda _pgid: True,
    )
    with pytest.raises(AgentTeamError) as live:
        unlock_workspace(
            workspace,
            expect_run=run_dir.name,
            confirm_origin_stopped=False,
        )
    assert live.value.code == "PROCESS_IDENTITY_UNKNOWN"
    assert read_owner(workspace) is not None

    monkeypatch.setattr(
        "agent_team.management.process_group_exists",
        lambda _pgid: False,
    )
    result = unlock_workspace(
        workspace,
        expect_run=run_dir.name,
        confirm_origin_stopped=False,
    )

    assert result["code"] == "WORKSPACE_UNLOCKED"
    assert read_owner(workspace) is None


def test_recovered_normal_exit_without_after_facts_becomes_recovery_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-recovered-missing-after",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        event = finalize_external_turn_locked(run_dir, runtime)

    assert event is not None
    assert event["block_reason"] == "recovery"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "failed"
    assert persisted["workspace_facts_after_sha256"] is None
    assert not (
        run_dir / "turns" / runtime["turn_id"] / "workspace-facts-after.json"
    ).exists()
    assert persisted["group_quiescent"] is True


def test_finished_snapshot_waits_for_supervisor_process_exit(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-finished-supervisor-live",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        monkeypatch.setattr(
            "agent_team.worker.process_identity_state",
            lambda *_args, **_kwargs: "match",
        )
        assert finalize_external_turn_locked(run_dir, runtime) is None
    assert scan_journal(run_dir).tail["event_type"] == "kickoff"

    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            run_dir / "turns" / runtime["turn_id"],
            team=scan_journal(run_dir).team,
        )
        event = finalize_external_turn_locked(
            run_dir,
            current,
            allow_after_capture=True,
        )
    assert event is not None
    assert event["block_reason"] == "no_action"


def test_unknown_finished_supervisor_identity_sets_recovery_gate(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-finished-supervisor-unknown",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        monkeypatch.setattr(
            "agent_team.worker.process_identity_state",
            lambda *_args, **_kwargs: "unknown",
        )
        event = finalize_external_turn_locked(run_dir, runtime)
    assert event is not None
    assert event["block_reason"] == "recovery"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "recovery_required"
    assert persisted["group_quiescent"] is True

    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    with locked_run(run_dir, exclusive=True):
        finalized = finalize_external_turn_locked(run_dir, persisted)
    assert finalized is not None
    assert finalized["event_id"] == event["event_id"]
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"


def test_cancel_during_launch_preparation_never_starts_supervisor(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-cancel-race",
    )

    class _CancellingAdapter:
        def prepare_launch(self, _context: Any) -> LaunchSpec:
            cancel_run(run_dir)
            return _launch_spec(run_dir, runtime)

    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _CancellingAdapter(),
    )
    monkeypatch.setattr("agent_team.worker._cli_path", lambda: "/bin/true")
    monkeypatch.setattr(
        "agent_team.worker.subprocess",
        SimpleNamespace(
            DEVNULL=subprocess.DEVNULL,
            Popen=lambda *_args, **_kwargs: pytest.fail(
                "Supervisor must not be started"
            ),
        ),
    )
    event = _launch_turn(run_dir, runtime, _Logger())
    assert event is not None
    assert event["event_type"] == "cancel"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "cancelled"
    assert persisted["group_quiescent"] is True
    assert not (run_dir / "turns" / runtime["turn_id"] / "process").exists()


def test_cancel_precedes_recovery_when_supervisor_disappears(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-cancel-before-recovery",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            runtime,
            supervisor_state="running",
        )
    cancelled = cancel_run(run_dir)
    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    monkeypatch.setattr(
        "agent_team.worker.process_group_exists",
        lambda _pgid: False,
    )

    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            run_dir / "turns" / runtime["turn_id"],
            team=scan_journal(run_dir).team,
        )
        finalized = finalize_external_turn_locked(run_dir, current)

    assert finalized is not None
    assert finalized["event_id"] == cancelled["event_id"]
    projection = scan_journal(run_dir)
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=projection.team,
    )
    assert projection.status == "CANCELLED"
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "cancelled"
    assert persisted["group_quiescent"] is True


def test_supervisor_spawn_failure_is_a_finalized_start_failure(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-supervisor-spawn-failure",
    )

    class _LaunchingAdapter:
        def prepare_launch(self, _context: Any) -> LaunchSpec:
            return _launch_spec(run_dir, runtime)

    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _LaunchingAdapter(),
    )
    monkeypatch.setattr("agent_team.worker._cli_path", lambda: "/bin/true")

    def fail_spawn(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected spawn failure")

    monkeypatch.setattr(
        "agent_team.worker.subprocess",
        SimpleNamespace(DEVNULL=subprocess.DEVNULL, Popen=fail_spawn),
    )
    event = _launch_turn(run_dir, runtime, _Logger())
    assert event is not None
    assert event["block_reason"] == "start_failure"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "failed"
    assert persisted["group_quiescent"] is True


def test_supervisor_exit_before_identity_snapshot_is_start_failure(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-supervisor-pre-snapshot-exit",
    )

    class _LaunchingAdapter:
        def prepare_launch(self, _context: Any) -> LaunchSpec:
            return _launch_spec(run_dir, runtime)

    class _ExitedSupervisor:
        def poll(self) -> int:
            return 72

        def wait(self, **_kwargs: Any) -> int:
            return 72

    supervisor_argv: list[str] = []

    def exited_supervisor(argv: list[str], **_kwargs: Any) -> _ExitedSupervisor:
        supervisor_argv.extend(argv)
        return _ExitedSupervisor()

    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _LaunchingAdapter(),
    )
    monkeypatch.setattr("agent_team.worker._cli_path", lambda: "/bin/true")
    monkeypatch.setattr(
        "agent_team.worker.random_token",
        lambda: "-option-like-launch-nonce",
    )
    monkeypatch.setattr(
        "agent_team.worker.subprocess",
        SimpleNamespace(
            DEVNULL=subprocess.DEVNULL,
            TimeoutExpired=subprocess.TimeoutExpired,
            Popen=exited_supervisor,
        ),
    )

    event = _launch_turn(run_dir, runtime, _Logger())

    assert event is not None
    assert event["block_reason"] == "start_failure"
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "failed"
    assert persisted["group_quiescent"] is True
    assert "--nonce=-option-like-launch-nonce" in supervisor_argv
    assert "--nonce" not in supervisor_argv


def test_recover_does_not_finalize_turn_claimed_by_live_worker(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-live-recover",
    )
    result = recover_run(run_dir)
    assert result["actions"] == []
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "starting"
    assert persisted["terminal_event_id"] is None


def test_recover_finalizes_prelaunch_turn_only_after_worker_is_gone(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-gone-recover",
    )
    monkeypatch.setattr(
        "agent_team.management.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    result = recover_run(run_dir)
    assert result["actions"] == [f"start-failure:{runtime['turn_id']}"]
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "failed"
    assert persisted["group_quiescent"] is True
    assert scan_journal(run_dir).tail["block_reason"] == "start_failure"


def test_recover_finalizes_nonce_without_launch_after_worker_crash(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-nonce-before-launch-crash",
    )
    with locked_run(run_dir, exclusive=True):
        runtime["launch_nonce"] = NONCE
        save_runtime(
            run_dir / "turns" / runtime["turn_id"],
            runtime,
            team=scan_journal(run_dir).team,
        )
    observation = derive_observation(run_dir)
    assert observation["health"] == "recovery_required"
    assert observation["recommended_action"] == "RUN_RECOVER"
    monkeypatch.setattr(
        "agent_team.management.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )

    result = recover_run(run_dir)

    assert result["actions"] == [f"start-failure:{runtime['turn_id']}"]
    assert scan_journal(run_dir).tail["block_reason"] == "start_failure"


def test_authorization_write_failure_leaves_runtime_starting(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-authorization-order",
    )
    with locked_run(run_dir, exclusive=True):
        launch, supervisor = _persist_process_chain(
            run_dir,
            runtime,
            supervisor_state="waiting_authorization",
            write_authorization=False,
            execution_started=False,
            runtime_phase="starting",
            runtime_has_identities=False,
        )
    monkeypatch.setattr(
        "agent_team.worker.identity_matches",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _BootstrapAdapter(),
    )

    def fail_authorization(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("injected authorization write failure")

    monkeypatch.setattr("agent_team.worker.atomic_json", fail_authorization)
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            run_dir / "turns" / runtime["turn_id"],
            team=scan_journal(run_dir).team,
        )
        with pytest.raises(OSError, match="authorization write failure"):
            _authorize_launch_locked(
                run_dir,
                current,
                supervisor,
                expected_launch=launch,
            )
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "starting"
    assert persisted["supervisor_pid"] == SUPERVISOR_PID
    assert persisted["runner_pid"] == RUNNER_PID


def test_supervisor_runner_pipeline_reaches_verified_normal_completion(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_team.supervisor as supervisor_module

    stream_closed = False
    original_close = StreamRecorder.close
    original_save_snapshot = supervisor_module._save_snapshot

    async def tracked_close(recorder: StreamRecorder) -> None:
        nonlocal stream_closed
        await original_close(recorder)
        stream_closed = True

    def tracked_save_snapshot(*args: Any, **kwargs: Any) -> None:
        snapshot = args[2]
        if snapshot["state"] == "finished":
            assert stream_closed
        original_save_snapshot(*args, **kwargs)

    monkeypatch.setattr(StreamRecorder, "close", tracked_close)
    monkeypatch.setattr(
        supervisor_module,
        "_save_snapshot",
        tracked_save_snapshot,
    )
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-supervisor-runner-pipeline",
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700)
    harness = (
        "import json,sys;"
        "sys.stdin.read();"
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'pipeline-thread'}),flush=True);"
        "print(json.dumps({'type':'turn.completed'}),flush=True)"
    )
    launch = LaunchSpec(
        adapter_id="codex",
        argv=(sys.executable, "-c", harness),
        cwd=str(workspace),
        env={
            "AGENT_TEAM_RUN_ID": run_dir.name,
            "AGENT_TEAM_ROLE_ID": "developer",
            "AGENT_TEAM_TURN_ID": runtime["turn_id"],
            "AGENT_TEAM_RUN_DIR": str(run_dir),
            "AGENT_TEAM_TURN_DIR": str(turn_dir),
            "AGENT_TEAM_CLI": "/bin/true",
        },
        stdin="exercise the supervised pipeline\n",
        launch_profile=PROFILE,
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
    )
    option_like_nonce = "-option-like-launch-nonce"
    with locked_run(run_dir, exclusive=True):
        runtime["launch_nonce"] = option_like_nonce
        save_runtime(
            turn_dir,
            runtime,
            team=scan_journal(run_dir).team,
        )
        atomic_json(
            process_dir / "launch.json",
            launch.to_json(),
            immutable=True,
        )
    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _BootstrapAdapter(),
    )
    _configure_supervisor_child_state(
        workspace,
        monkeypatch,
        directory_name="child-bootstrap",
    )

    async def run_pipeline() -> int:
        task = asyncio.create_task(
            supervise_turn(
                run_dir,
                runtime["turn_id"],
                option_like_nonce,
                launch.content_sha256(),
            )
        )
        for _ in range(400):
            if task.done():
                result = await task
                raise AssertionError(
                    f"Supervisor exited before launch authorization: {result}"
                )
            snapshot_path = process_dir / "supervisor.json"
            if snapshot_path.exists():
                with locked_run(run_dir, exclusive=True):
                    snapshot = validate_supervisor(read_json(snapshot_path))
                    if snapshot["state"] == "waiting_authorization":
                        current = load_runtime(
                            turn_dir,
                            team=scan_journal(run_dir).team,
                        )
                        assert _authorize_launch_locked(
                            run_dir,
                            current,
                            snapshot,
                            expected_launch=launch,
                        )
                        break
            await asyncio.sleep(0.025)
        else:
            task.cancel()
            raise AssertionError("Supervisor never reached launch authorization")
        return await asyncio.wait_for(task, timeout=15)

    assert asyncio.run(run_pipeline()) == 0
    final_snapshot = validate_supervisor(read_json(process_dir / "supervisor.json"))
    assert final_snapshot["state"] == "finished"
    assert final_snapshot["agent_execution_started"] is True
    assert final_snapshot["adapter_completed"] is True
    assert final_snapshot["observed_session_ref"] == "pipeline-thread"
    assert final_snapshot["group_quiescent"] is True

    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: CodexAdapter(),
    )
    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            turn_dir,
            team=scan_journal(run_dir).team,
        )
        event = finalize_external_turn_locked(
            run_dir,
            current,
            allow_after_capture=True,
        )

    assert event is not None
    assert event["block_reason"] == "no_action"
    finalized = load_runtime(
        turn_dir,
        team=scan_journal(run_dir).team,
    )
    assert finalized["phase"] == "finalized"
    assert finalized["outcome"] == "stalled"
    assert finalized["workspace_facts_after_sha256"] is not None


def test_interactive_supervisor_uses_pty_and_stops_after_durable_action(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-interactive-pipeline",
        launch_mode="interactive",
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700)
    prompt_path = process_dir / "prompt.md"
    prompt = "exercise the native interactive pipeline\n"
    harness = (
        "import os,sys,time;"
        "assert all(os.isatty(fd) for fd in (0,1,2));"
        f"assert {str(prompt_path)!r} in sys.argv[-1];"
        "assert 'authoritative prompt' in sys.argv[-1];"
        "print('\\x1b[32mNATIVE_TUI_READY\\x1b[0m',flush=True);"
        "time.sleep(60)"
    )
    launch = LaunchSpec(
        adapter_id="codex",
        argv=(sys.executable, "-c", harness),
        cwd=str(workspace),
        env={
            "AGENT_TEAM_RUN_ID": run_dir.name,
            "AGENT_TEAM_ROLE_ID": "developer",
            "AGENT_TEAM_TURN_ID": runtime["turn_id"],
            "AGENT_TEAM_RUN_DIR": str(run_dir),
            "AGENT_TEAM_TURN_DIR": str(turn_dir),
            "AGENT_TEAM_CLI": "/bin/true",
        },
        stdin=prompt,
        launch_profile=PROFILE,
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
        launch_mode="interactive",
        prompt_file=str(prompt_path),
        expected_session_ref="interactive-thread",
    )
    option_like_nonce = "-interactive-launch-nonce"
    with locked_run(run_dir, exclusive=True):
        runtime["launch_nonce"] = option_like_nonce
        save_runtime(
            turn_dir,
            runtime,
            team=scan_journal(run_dir).team,
        )
        atomic_write(prompt_path, prompt.encode("utf-8"), immutable=True)
        atomic_json(process_dir / "launch.json", launch.to_json(), immutable=True)
    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _BootstrapAdapter("interactive"),
    )
    _configure_supervisor_child_state(
        workspace,
        monkeypatch,
        directory_name="interactive-child-bootstrap",
    )

    async def run_pipeline() -> int:
        task = asyncio.create_task(
            supervise_turn(
                run_dir,
                runtime["turn_id"],
                option_like_nonce,
                launch.content_sha256(),
            )
        )
        authorized = False
        for _ in range(600):
            if task.done():
                result = await task
                raise AssertionError(
                    f"interactive Supervisor exited before action staging: {result}"
                )
            snapshot_path = process_dir / "supervisor.json"
            if snapshot_path.exists():
                running = False
                with locked_run(run_dir, exclusive=True):
                    snapshot = validate_supervisor(read_json(snapshot_path))
                    if (
                        snapshot["state"] == "waiting_authorization"
                        and not authorized
                    ):
                        current = load_runtime(
                            turn_dir,
                            team=scan_journal(run_dir).team,
                        )
                        assert _authorize_launch_locked(
                            run_dir,
                            current,
                            snapshot,
                            expected_launch=launch,
                        )
                        authorized = True
                    elif snapshot["state"] == "running":
                        running = True
                if running:
                    payload = turn_dir / "completion.md"
                    payload.write_text(
                        "# Completion\n\nNative interactive action staged.\n",
                        encoding="utf-8",
                    )
                    with locked_run(run_dir, exclusive=True):
                        current = load_runtime(
                            turn_dir,
                            team=scan_journal(run_dir).team,
                        )
                        staged = stage_external_action_locked(
                            run_dir,
                            runtime=current,
                            action="complete",
                            source_file=payload,
                            to_role=None,
                        )
                    assert staged["code"] == "ACTION_ACCEPTED"
                    break
            await asyncio.sleep(0.025)
        else:
            task.cancel()
            raise AssertionError("interactive Supervisor never reached running")
        return await asyncio.wait_for(task, timeout=15)

    assert asyncio.run(run_pipeline()) == 0
    final_snapshot = validate_supervisor(read_json(process_dir / "supervisor.json"))
    assert final_snapshot["state"] == "finished"
    assert final_snapshot["agent_execution_started"] is True
    assert final_snapshot["adapter_completed"] is True
    assert final_snapshot["observed_session_ref"] == "interactive-thread"
    assert final_snapshot["termination_kind"] == "action"
    assert final_snapshot["process_exit_code"] != 0
    assert final_snapshot["group_quiescent"] is True
    stream = [
        json.loads(line)
        for line in (process_dir / "stream.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert any(item["source"] == "terminal" for item in stream)
    assert any("NATIVE_TUI_READY" in item["data"] for item in stream)

    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: CodexAdapter(),
    )
    monkeypatch.setattr(
        CodexAdapter,
        "finalize_run_state",
        lambda _self, **_kwargs: None,
    )
    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            turn_dir,
            team=scan_journal(run_dir).team,
        )
        event = finalize_external_turn_locked(
            run_dir,
            current,
            allow_after_capture=True,
        )

    assert event is not None
    assert event["event_type"] == "complete"
    assert scan_journal(run_dir).status == "COMPLETED"


def test_fresh_interactive_session_candidate_fails_closed() -> None:
    class SessionAdapter:
        refs: set[str] = {"prior", "current"}

        def interactive_session_refs(self, _launch: LaunchSpec) -> set[str]:
            return self.refs

    adapter = SessionAdapter()
    launch = LaunchSpec(
        adapter_id="codex",
        argv=("/bin/codex",),
        cwd="/worktree",
        env={},
        stdin="prompt",
        launch_profile="default",
        launch_profile_sha256="0" * 64,
        starts_new_session=True,
        launch_mode="interactive",
        prompt_file="/run/turn/process/prompt.md",
    )

    assert _fresh_interactive_session_candidate(
        adapter,
        launch,
        baseline={"prior"},
        observed=None,
    ) == "current"

    adapter.refs = {"prior", "current", "ambiguous"}
    with pytest.raises(IntegrityError, match="multiple candidate"):
        _fresh_interactive_session_candidate(
            adapter,
            launch,
            baseline={"prior"},
            observed="current",
        )

    adapter.refs = {"prior"}
    with pytest.raises(IntegrityError, match="disappeared"):
        _fresh_interactive_session_candidate(
            adapter,
            launch,
            baseline={"prior"},
            observed="current",
        )


@pytest.mark.parametrize(
    ("trigger", "max_wall_time_seconds"),
    [
        ("cancel", 300),
        ("deadline", 2),
    ],
)
def test_supervisor_terminates_active_runner_after_cancel_or_deadline(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    trigger: str,
    max_wall_time_seconds: int,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id=f"at-worker-supervisor-{trigger}",
        max_wall_time_seconds=max_wall_time_seconds,
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    process_dir = turn_dir / "process"
    process_dir.mkdir(mode=0o700)
    harness = (
        "import json,sys,time;"
        "sys.stdin.read();"
        "print(json.dumps({'type':'thread.started',"
        "'thread_id':'cancel-thread'}),flush=True);"
        "time.sleep(60)"
    )
    launch = LaunchSpec(
        adapter_id="codex",
        argv=(sys.executable, "-c", harness),
        cwd=str(workspace),
        env={
            "AGENT_TEAM_RUN_ID": run_dir.name,
            "AGENT_TEAM_ROLE_ID": "developer",
            "AGENT_TEAM_TURN_ID": runtime["turn_id"],
            "AGENT_TEAM_RUN_DIR": str(run_dir),
            "AGENT_TEAM_TURN_DIR": str(turn_dir),
            "AGENT_TEAM_CLI": "/bin/true",
        },
        stdin="wait until cancelled\n",
        launch_profile=PROFILE,
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
    )
    with locked_run(run_dir, exclusive=True):
        runtime["launch_nonce"] = NONCE
        save_runtime(
            turn_dir,
            runtime,
            team=scan_journal(run_dir).team,
        )
        atomic_json(
            process_dir / "launch.json",
            launch.to_json(),
            immutable=True,
        )
    monkeypatch.setattr(
        "agent_team.worker.get_adapter",
        lambda _adapter: _BootstrapAdapter(),
    )
    _configure_supervisor_child_state(
        workspace,
        monkeypatch,
        directory_name="cancel-child-bootstrap",
    )

    async def run_and_cancel() -> tuple[int, dict[str, Any]]:
        task = asyncio.create_task(
            supervise_turn(
                run_dir,
                runtime["turn_id"],
                NONCE,
                launch.content_sha256(),
            )
        )
        authorized = False
        for _ in range(400):
            if task.done():
                result = await task
                raise AssertionError(
                    f"Supervisor exited before cancellation: {result}"
                )
            snapshot_path = process_dir / "supervisor.json"
            if snapshot_path.exists():
                running = False
                with locked_run(run_dir, exclusive=True):
                    snapshot = validate_supervisor(read_json(snapshot_path))
                    if (
                        snapshot["state"] == "waiting_authorization"
                        and not authorized
                    ):
                        current = load_runtime(
                            turn_dir,
                            team=scan_journal(run_dir).team,
                        )
                        assert _authorize_launch_locked(
                            run_dir,
                            current,
                            snapshot,
                            expected_launch=launch,
                        )
                        authorized = True
                    elif snapshot["state"] == "running":
                        running = True
                if running:
                    if trigger == "cancel":
                        terminal = cancel_run(run_dir)
                    else:
                        result = await asyncio.wait_for(task, timeout=15)
                        terminal = scan_journal(run_dir).tail
                        assert terminal is not None
                        return result, terminal
                    return await asyncio.wait_for(task, timeout=15), terminal
            await asyncio.sleep(0.025)
        task.cancel()
        raise AssertionError("Supervisor never observed active Harness execution")

    result, terminal = asyncio.run(run_and_cancel())
    assert result == 0
    final_snapshot = validate_supervisor(read_json(process_dir / "supervisor.json"))
    assert final_snapshot["state"] == "finished"
    assert final_snapshot["agent_execution_started"] is True
    expected_termination = "cancelled" if trigger == "cancel" else "deadline"
    assert final_snapshot["termination_kind"] == expected_termination
    assert final_snapshot["group_quiescent"] is True
    if trigger == "cancel":
        assert terminal["event_type"] == "cancel"
    else:
        assert terminal["event_type"] == "block"
        assert terminal["block_reason"] == "limit"
        assert terminal["limit_reason"] == "deadline"

    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            turn_dir,
            team=scan_journal(run_dir).team,
        )
        event = finalize_external_turn_locked(run_dir, current)

    assert event is not None
    assert event["event_id"] == terminal["event_id"]
    finalized = load_runtime(
        turn_dir,
        team=scan_journal(run_dir).team,
    )
    assert finalized["phase"] == "finalized"
    assert finalized["outcome"] == "cancelled"
    assert finalized["termination_kind"] == expected_termination


def test_corrupted_team_only_terminates_verified_managed_processes(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-corrupted-cleanup",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
    event_names_before = sorted(path.name for path in (run_dir / "events").iterdir())
    team_value = read_json(run_dir / "team.json")
    team_value["workspace"] = str(workspace.parent)
    atomic_json(run_dir / "team.json", team_value)

    group_calls: list[tuple[int, int, str]] = []
    process_calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "agent_team.management.process_group_exists",
        lambda pgid: pgid == RUNNER_PID,
    )
    monkeypatch.setattr(
        "agent_team.management.process_identity_state",
        lambda *_args, **_kwargs: "match",
    )

    def terminate_group(
        *,
        runner_pid: int,
        runner_pgid: int,
        runner_start_id: str,
        **_kwargs: Any,
    ) -> bool:
        group_calls.append((runner_pid, runner_pgid, runner_start_id))
        return True

    def terminate_process(
        *,
        pid: int,
        start_id: str,
        **_kwargs: Any,
    ) -> bool:
        process_calls.append((pid, start_id))
        return True

    monkeypatch.setattr(
        "agent_team.management.terminate_verified_group",
        terminate_group,
    )
    monkeypatch.setattr(
        "agent_team.management.terminate_verified_process",
        terminate_process,
    )

    with pytest.raises(IntegrityError, match="verified cleanup actions"):
        recover_run(run_dir)

    assert group_calls == [(RUNNER_PID, RUNNER_PID, "runner-start")]
    assert process_calls == [(SUPERVISOR_PID, "supervisor-start")]
    assert sorted(path.name for path in (run_dir / "events").iterdir()) == (
        event_names_before
    )
    assert not (
        run_dir / "turns" / runtime["turn_id"] / "workspace-facts-after.json"
    ).exists()


def test_corrupted_cleanup_retains_supervisor_when_runner_group_is_ambiguous(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-corrupted-ambiguous-group",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
    team_value = read_json(run_dir / "team.json")
    team_value["workspace"] = str(workspace.parent)
    atomic_json(run_dir / "team.json", team_value)

    process_calls: list[tuple[int, str]] = []
    monkeypatch.setattr(
        "agent_team.management.process_group_exists",
        lambda pgid: pgid == RUNNER_PID,
    )
    monkeypatch.setattr(
        "agent_team.management.process_identity_state",
        lambda pid, _start_id, **_kwargs: (
            "gone" if pid == RUNNER_PID else "match"
        ),
    )
    monkeypatch.setattr(
        "agent_team.management.terminate_verified_group",
        lambda **_kwargs: pytest.fail(
            "an unverifiable Runner leader must not authorize killpg"
        ),
    )

    def terminate_process(
        *,
        pid: int,
        start_id: str,
        **_kwargs: Any,
    ) -> bool:
        process_calls.append((pid, start_id))
        return True

    monkeypatch.setattr(
        "agent_team.management.terminate_verified_process",
        terminate_process,
    )

    with pytest.raises(
        IntegrityError,
        match="retained-for-runner-cleanup",
    ):
        recover_run(run_dir)

    assert (SUPERVISOR_PID, "supervisor-start") not in process_calls


def test_repeated_start_with_mismatched_owner_cleans_without_rebuilding_workers(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, _runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-owner-corrupted-cleanup",
    )
    _, _, owner_path = state_paths(workspace)
    owner = read_json(owner_path)
    owner["run_id"] = "at-other-owner"
    atomic_json(owner_path, owner)
    event_names_before = sorted(path.name for path in (run_dir / "events").iterdir())
    monkeypatch.setattr(
        "agent_team.management.ensure_workers",
        lambda *_args, **_kwargs: pytest.fail(
            "corrupted ownership must not rebuild Workers"
        ),
    )

    with pytest.raises(IntegrityError, match="exact ownership"):
        start_run(run_dir)

    assert read_json(owner_path)["run_id"] == "at-other-owner"
    assert sorted(path.name for path in (run_dir / "events").iterdir()) == (
        event_names_before
    )

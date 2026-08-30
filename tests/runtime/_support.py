from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_team.adapters.base import LaunchSpec
from agent_team.bootstrap import initialize_run, start_run
from agent_team.config import (
    ObservabilityPolicy,
    Role,
    make_team,
)
from agent_team.journal import scan_journal
from agent_team.processes import current_identity
from agent_team.state import locked_run
from agent_team.supervisor import (
    _base_snapshot,
)
from agent_team.tmux_runtime import session_name
from agent_team.turns import (
    create_business_turn_locked,
    save_runtime,
)
from agent_team.util import atomic_json, atomic_write, read_json, rfc3339

PROFILE = "test-noninteractive"
PROFILE_HASH = "0" * 64
NONCE = "test-launch-nonce"
SUPERVISOR_PID = 700_001
RUNNER_PID = 700_002


class _BootstrapAdapter:
    def __init__(
        self,
        launch_mode: str = "headless",
        session_policy: str = "fresh",
    ) -> None:
        self.launch_mode = launch_mode
        self.session_policy = session_policy

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
        assert session_policy == self.session_policy
        assert expected_hash == PROFILE_HASH
        assert launch_mode == self.launch_mode

    def assert_launch_options(self, options: Any) -> None:
        assert options.model is None
        assert options.reasoning_effort is None
        assert options.fast_mode is None

    def ensure_launch_dependencies(self, options: Any) -> None:
        self.assert_launch_options(options)

    def assert_launch_prerequisites(self, options: Any) -> None:
        self.assert_launch_options(options)

    def authentication_required(self, options: Any) -> bool:
        self.assert_launch_options(options)
        return True

    def prepare_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
        session_generation: int = 1,
    ) -> None:
        assert run_dir.name.startswith("at-")
        assert role_id in {"developer", "reviewer"}
        assert launch_mode == self.launch_mode
        assert session_generation >= 1

    def finalize_run_state(
        self,
        *,
        run_dir: Path,
        role_id: str,
        launch_mode: str,
    ) -> None:
        assert run_dir.name.startswith("at-")
        assert role_id in {"developer", "reviewer"}
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
    include_external_reviewer: bool = False,
    observability: ObservabilityPolicy | None = None,
    launch_mode: str = "headless",
    developer_session_policy: str = "fresh",
) -> tuple[Path, dict[str, Any]]:
    request, protocol = request_protocol
    adapter = _BootstrapAdapter(launch_mode, developer_session_policy)
    monkeypatch.setattr("agent_team.bootstrap.get_adapter", lambda _adapter: adapter)
    monkeypatch.setattr("agent_team.ownership.get_adapter", lambda _adapter: adapter)
    monkeypatch.setattr("agent_team.bootstrap.tmux_executable", lambda: "/bin/true")
    empty_tmux = {
        "session": "test-session",
        "created": [],
        "existing": ["developer"],
    }
    monkeypatch.setattr(
        "agent_team.bootstrap.ensure_workers",
        lambda _run_dir, _team, **_kwargs: empty_tmux,
    )
    monkeypatch.setattr(
        "agent_team.management.ensure_workers",
        lambda _run_dir, _team, **_kwargs: empty_tmux,
    )
    monkeypatch.setattr("agent_team.bootstrap.signal_change", lambda *_args: False)
    monkeypatch.setattr("agent_team.management.signal_change", lambda *_args: False)
    roles = {
        "developer": Role(
            "developer",
            "external",
            "codex",
            developer_session_policy,
            PROFILE,
            PROFILE_HASH,
            launch_mode=launch_mode,
        )
    }
    if include_origin_reviewer:
        roles["reviewer"] = Role("reviewer", "origin")
    if include_external_reviewer:
        roles["reviewer"] = Role(
            "reviewer",
            "external",
            "codex",
            "fresh",
            PROFILE,
            PROFILE_HASH,
            launch_mode=launch_mode,
        )
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


def _launch_spec(
    run_dir: Path,
    runtime: dict[str, Any],
    *,
    launch_mode: str = "headless",
) -> LaunchSpec:
    turn_id = runtime["turn_id"]
    turn_dir = run_dir / "turns" / turn_id
    return LaunchSpec(
        adapter_id="codex",
        argv=("/bin/true",),
        cwd=str(run_dir.parent.parent.parent),
        env={
            "AGENT_TEAM_RUN_ID": run_dir.name,
            "AGENT_TEAM_ROLE_ID": runtime["role_id"],
            "AGENT_TEAM_TURN_ID": turn_id,
            "AGENT_TEAM_RUN_DIR": str(run_dir),
            "AGENT_TEAM_TURN_DIR": str(run_dir / "turns" / turn_id),
            "AGENT_TEAM_CLI": "/bin/true",
        },
        stdin="test prompt\n",
        launch_profile=PROFILE,
        launch_profile_sha256=PROFILE_HASH,
        starts_new_session=True,
        launch_mode=launch_mode,
        prompt_file=(
            str(turn_dir / "process" / "prompt.md")
            if launch_mode == "interactive"
            else None
        ),
    )


def _configure_supervisor_child_state(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    directory_name: str,
) -> None:
    state_dir = read_json(workspace / ".agent-team" / "root.json")["state_dir_realpath"]
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
    write_capture: bool = True,
    adapter_completed: bool | None = None,
    process_exit_code: int | None = None,
    termination_kind: str | None = None,
    observed_session_ref: str | None = None,
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
    completed = execution_started if adapter_completed is None else adapter_completed
    effective_exit_code = (
        (0 if supervisor_state == "finished" else None)
        if process_exit_code is None
        else process_exit_code
    )
    effective_termination_kind = (
        ("normal" if supervisor_state == "finished" else None)
        if termination_kind is None
        else termination_kind
    )
    effective_session_ref = (
        f"thread-{runtime['turn_id']}"
        if observed_session_ref is None and execution_started
        else observed_session_ref
    )
    supervisor.update(
        {
            "state": supervisor_state,
            "supervisor_pid": SUPERVISOR_PID,
            "supervisor_start_id": "supervisor-start",
            "runner_pid": RUNNER_PID if supervisor_has_runner else None,
            "runner_pgid": RUNNER_PID if supervisor_has_runner else None,
            "runner_start_id": ("runner-start" if supervisor_has_runner else None),
            "agent_execution_started": execution_started,
            "adapter_completed": completed,
            "observed_session_ref": effective_session_ref,
            "process_exit_code": effective_exit_code,
            "termination_kind": effective_termination_kind,
            "group_quiescent": supervisor_state == "finished",
            "updated_at": rfc3339(),
        }
    )
    atomic_json(
        process_dir / "supervisor.json",
        supervisor,
        immutable=True,
    )
    if supervisor_state == "finished" and write_capture:
        atomic_write(process_dir / "stream.jsonl", b"", immutable=True)
        atomic_write(process_dir / "stderr.log", b"", immutable=True)
        atomic_json(
            process_dir / "capture.json",
            {
                "schema_version": 1,
                "source_bytes": 0,
                "stored_source_bytes": 0,
                "dropped_source_bytes": 0,
                "chunks_observed": 0,
                "chunks_stored": 0,
                "truncated": False,
                "closed_at": rfc3339(),
            },
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

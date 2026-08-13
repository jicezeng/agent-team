from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_team.adapters.base import LaunchSpec
from agent_team.journal import scan_journal
from agent_team.management import cancel_run, recover_run
from agent_team.observation import derive_observation
from agent_team.state import locked_run
from agent_team.turns import (
    load_runtime,
    save_runtime,
)
from agent_team.util import atomic_json, atomic_write, rfc3339
from agent_team.worker import (
    _authorize_launch_locked,
    _launch_turn,
    finalize_external_turn_locked,
)

from ._support import (
    NONCE,
    RUNNER_PID,
    SUPERVISOR_PID,
    _BootstrapAdapter,
    _Logger,
    _external_run,
    _launch_spec,
    _persist_process_chain,
)


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
        process_dir = run_dir / "turns" / runtime["turn_id"] / "process"
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


@pytest.mark.parametrize("launch_mode", ["headless", "interactive"])
def test_supervisor_exit_before_identity_snapshot_is_start_failure(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    launch_mode: str,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id=f"at-worker-supervisor-pre-snapshot-exit-{launch_mode}",
        launch_mode=launch_mode,
    )

    class _LaunchingAdapter:
        def prepare_launch(self, _context: Any) -> LaunchSpec:
            return _launch_spec(run_dir, runtime, launch_mode=launch_mode)

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

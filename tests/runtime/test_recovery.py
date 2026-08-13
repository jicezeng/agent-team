from __future__ import annotations

from dataclasses import replace
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_team.bootstrap import start_run
from agent_team.errors import AgentTeamError, IntegrityError
from agent_team.journal import scan_journal
from agent_team.management import recover_run
from agent_team.observation import derive_observation
from agent_team.processes import current_identity
from agent_team.state import locked_run
from agent_team.supervisor import (
    _base_snapshot,
    validate_supervisor,
)
from agent_team.turns import (
    load_runtime,
    validate_runtime,
)
from agent_team.util import atomic_json, atomic_write, read_json, rfc3339
from agent_team.worker import (
    finalize_external_turn_locked,
)

from ._support import (
    NONCE,
    PROFILE,
    PROFILE_HASH,
    SUPERVISOR_PID,
    _external_run,
    _persist_process_chain,
)


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
    finalize_calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        "agent_team.management._finalize_adapter_run_state",
        lambda recovered_run, *, role: finalize_calls.append(
            (recovered_run, role.role_id)
        ),
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
    assert finalize_calls == [(run_dir, "developer")]

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

    assert len(list((run_dir / "events").glob("*.json"))) == 1


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


def test_live_stopping_supervisor_does_not_finalize_adapter_state(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-stopping-supervisor-live",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            runtime,
            supervisor_state="stopping",
        )
    finalize_calls: list[str] = []
    monkeypatch.setattr(
        "agent_team.worker.process_identity_state",
        lambda *_args, **_kwargs: "match",
    )
    monkeypatch.setattr(
        "agent_team.worker._finalize_adapter_run_state",
        lambda _run_dir, *, role: finalize_calls.append(role.role_id),
    )

    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            run_dir / "turns" / runtime["turn_id"],
            team=scan_journal(run_dir).team,
        )
        event = finalize_external_turn_locked(run_dir, current)

    assert event is None
    assert finalize_calls == []
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "running"


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
        ("schema_version", True),
        ("schema_version", 1.0),
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


def test_runtime_trace_anchor_compatibility_is_limited_to_legacy_team_schema(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-runtime-trace-compat",
    )
    team = scan_journal(run_dir).team
    runtime.pop("trace_manifest_sha256")

    with pytest.raises(IntegrityError, match="trace_manifest_sha256"):
        validate_runtime(runtime, team=team)
    with pytest.raises(IntegrityError, match="trace_manifest_sha256"):
        validate_runtime(runtime)

    legacy = replace(team, config_schema_version=1)
    normalized = validate_runtime(runtime, team=legacy)

    assert normalized["trace_manifest_sha256"] is None
    assert "trace_manifest_sha256" not in runtime


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", []),
        ("termination_kind", []),
        ("schema_version", True),
        ("schema_version", 1.0),
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

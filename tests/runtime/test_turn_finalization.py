from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team.config import (
    REQUIRED_AUDIT_PAYLOAD_SECTIONS,
    ObservabilityPolicy,
)
from agent_team.errors import AgentTeamError
from agent_team.journal import scan_journal
from agent_team.management import unlock_workspace
from agent_team.observation import derive_observation
from agent_team.origin import origin_action, wait_origin
from agent_team.ownership import release_terminal_owner_locked
from agent_team.state import locked_run, read_owner
from agent_team.trace import finalize_turn_trace
from agent_team.turns import (
    iter_runtimes,
    load_runtime,
    stage_external_action_locked,
)
from agent_team.util import atomic_json, atomic_write, read_json, rfc3339
from agent_team.worker import (
    finalize_external_turn_locked,
)

from ._support import (
    SUPERVISOR_PID,
    _external_run,
    _persist_process_chain,
)


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
        _persist_process_chain(run_dir, runtime, write_capture=False)
        raw_chunk = {
            "schema_version": 1,
            "seq": 1,
            "observed_at": rfc3339(),
            "source": "stdout",
            "encoding": "utf-8",
            "data": "x" * 1024,
        }
        atomic_write(
            turn_dir / "process" / "stream.jsonl",
            (json.dumps(raw_chunk, separators=(",", ":")) + "\n").encode(),
            immutable=True,
        )
        atomic_write(
            turn_dir / "process" / "stderr.log",
            b"",
            immutable=True,
        )
        atomic_json(
            turn_dir / "process" / "capture.json",
            {
                "schema_version": 1,
                "source_bytes": 2048,
                "stored_source_bytes": 1024,
                "dropped_source_bytes": 1024,
                "chunks_observed": 1,
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


def test_finalize_reuses_manifest_committed_before_runtime_anchor(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-trace-anchor-retry",
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    policy = scan_journal(run_dir).team.observability
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        manifest, digest = finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id=runtime["role_id"],
            adapter_id="codex",
            policy=policy,
        )
        assert runtime["trace_manifest_sha256"] is None

        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )

    assert event is not None
    assert event["block_reason"] == "no_action"
    persisted = load_runtime(turn_dir, team=scan_journal(run_dir).team)
    assert persisted["trace_manifest_sha256"] == digest
    assert read_json(turn_dir / "trace-manifest.json") == manifest
    assert not (turn_dir / "process" / "trace-finalization.json").exists()


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
        item for item in iter_runtimes(run_dir) if item["business_turn_seq"] is not None
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
        lambda pid, *_args, **_kwargs: "match" if pid == SUPERVISOR_PID else "gone",
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


def test_terminal_owner_release_finalizes_unvisited_external_roles(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-release-all-adapters",
        include_external_reviewer=True,
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

    finalized: list[tuple[str, str]] = []

    class _TerminalAdapter:
        def finalize_run_state(
            self,
            *,
            run_dir: Path,
            role_id: str,
            launch_mode: str,
        ) -> None:
            assert run_dir.name == "at-worker-release-all-adapters"
            finalized.append((role_id, launch_mode))

    monkeypatch.setattr(
        "agent_team.ownership.get_adapter",
        lambda _adapter: _TerminalAdapter(),
    )
    monkeypatch.setattr(
        "agent_team.ownership.process_identity_state",
        lambda *_args, **_kwargs: "gone",
    )
    monkeypatch.setattr(
        "agent_team.ownership.process_group_exists",
        lambda _pgid: False,
    )
    with locked_run(run_dir, exclusive=True):
        assert release_terminal_owner_locked(run_dir)

    assert finalized == [
        ("developer", "headless"),
        ("reviewer", "headless"),
    ]
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

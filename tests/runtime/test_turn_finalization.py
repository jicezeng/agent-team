from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team.adapters.base import ExitInfo
from agent_team.config import (
    REQUIRED_AUDIT_PAYLOAD_SECTIONS,
    ObservabilityPolicy,
)
from agent_team.errors import AgentTeamError, IntegrityError, RoutePreflightError
from agent_team.journal import scan_journal
from agent_team.management import unlock_workspace
from agent_team.observation import derive_observation
from agent_team.origin import origin_action, wait_origin
from agent_team.ownership import release_terminal_owner_locked
from agent_team.state import locked_run, read_owner
from agent_team.trace import finalize_turn_trace
from agent_team.turns import (
    create_business_turn_locked,
    iter_runtimes,
    load_runtime,
    load_session,
    session_launch_state,
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


class _OutputLimitAdapter:
    def __init__(self, prepared_generations: list[int]) -> None:
        self.prepared_generations = prepared_generations

    @staticmethod
    def finalize_run_state(**_kwargs: object) -> None:
        return None

    @staticmethod
    def classify_result(_result: object, _evidence: object) -> ExitInfo:
        return ExitInfo(False, "output budget reached")

    @staticmethod
    def recoverable_termination_kind(result: object, evidence: object) -> str | None:
        if (
            getattr(result, "process_exit_code", None) == 75
            and getattr(result, "group_quiescent", False)
            and getattr(evidence, "agent_execution_started", False)
            and not getattr(evidence, "adapter_completed", True)
            and getattr(evidence, "observed_session_ref", None)
        ):
            return "output_limit"
        return None

    @staticmethod
    def assert_profile(*_args: object, **_kwargs: object) -> None:
        return None

    def prepare_run_state(self, *, session_generation: int, **_kwargs: object) -> None:
        self.prepared_generations.append(session_generation)


class _RoutePreflightAdapter:
    def __init__(self, rejected_role: str | None = None) -> None:
        self.rejected_role = rejected_role

    @staticmethod
    def assert_profile(*_args: object, **_kwargs: object) -> None:
        return None

    def prepare_run_state(self, *, role_id: str, **_kwargs: object) -> None:
        if role_id == self.rejected_role:
            raise RoutePreflightError(
                "DSH_PLUGIN_INVALID",
                "workspace DSH plugin is not an installable bundle",
            )


class _CandidateActivationAdapter:
    @staticmethod
    def assert_profile(*_args: object, **_kwargs: object) -> None:
        return None

    @staticmethod
    def prepare_run_state(**_kwargs: object) -> None:
        return None

    @staticmethod
    def finalize_run_state(**_kwargs: object) -> None:
        return None

    @staticmethod
    def classify_result(result: object, evidence: object) -> ExitInfo:
        normal = (
            getattr(result, "process_exit_code", None) == 0
            and getattr(result, "termination_kind", None) == "normal"
            and getattr(result, "group_quiescent", False)
            and getattr(evidence, "agent_execution_started", False)
            and getattr(evidence, "adapter_completed", False)
        )
        return ExitInfo(normal, "normal_completion" if normal else "abnormal_exit")

    @staticmethod
    def recoverable_termination_kind(
        _result: object,
        _evidence: object,
    ) -> str | None:
        return None

    @staticmethod
    def candidate_activation_failure(
        *,
        result: object,
        **_kwargs: object,
    ) -> str | None:
        if getattr(result, "termination_kind", None) != "crash":
            return None
        return "candidate-bound Harness exited before Session initialization"


def _install_output_limit_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> list[int]:
    prepared_generations: list[int] = []
    adapter = _OutputLimitAdapter(prepared_generations)
    monkeypatch.setattr("agent_team.worker.get_adapter", lambda _adapter: adapter)
    monkeypatch.setattr("agent_team.turns.get_adapter", lambda _adapter: adapter)
    return prepared_generations


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


def test_output_limit_automatically_continues_available_resume_session(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _install_output_limit_adapter(monkeypatch)
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-output-limit-continuation",
        max_turns=4,
        developer_session_policy="resume",
    )
    session_ref = f"thread-{runtime['turn_id']}"

    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            runtime,
            adapter_completed=False,
            process_exit_code=75,
            termination_kind="output_limit",
            observed_session_ref=session_ref,
        )
        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )
        next_runtime, continuity_error = create_business_turn_locked(
            run_dir,
            role_id="developer",
            executor="worker",
        )

    assert event is not None
    assert event["event_type"] == "handoff"
    assert event["from_role"] == event["to_role"] == "developer"
    assert event["continuation_reason"] == "output_limit"
    assert "continuation_no_progress_count" not in event
    assert prepared
    assert set(prepared) == {1}
    persisted = load_runtime(
        run_dir / "turns" / runtime["turn_id"],
        team=scan_journal(run_dir).team,
    )
    assert persisted["phase"] == "finalized"
    assert persisted["outcome"] == "success"
    assert persisted["workspace_facts_after_sha256"] is not None
    assert next_runtime is not None
    assert continuity_error is None
    assert next_runtime["session_generation"] == runtime["session_generation"]
    assert derive_observation(run_dir)["journal_tail"]["continuation_reason"] == (
        "output_limit"
    )
    assert "continuation_no_progress_count" not in derive_observation(run_dir)[
        "journal_tail"
    ]
    assert "output budget" in (
        run_dir / "turns" / next_runtime["turn_id"] / "input.md"
    ).read_text(encoding="utf-8")

    event_path = (
        run_dir
        / "events"
        / f"{event['event_seq']:04d}-{event['event_id']}.json"
    )
    legacy_event = read_json(event_path)
    legacy_event["continuation_no_progress_count"] = 1
    atomic_json(event_path, legacy_event)
    assert scan_journal(run_dir).status == "RUNNING"
    assert derive_observation(run_dir)["journal_tail"][
        "continuation_no_progress_count"
    ] == 1

    source_runtime_path = run_dir / "turns" / runtime["turn_id"] / "runtime.json"
    tampered_runtime = read_json(source_runtime_path)
    tampered_runtime["termination_kind"] = "crash"
    atomic_json(source_runtime_path, tampered_runtime)
    with pytest.raises(
        IntegrityError,
        match="automatic continuation process evidence is invalid",
    ):
        scan_journal(run_dir)


def test_repeated_output_limits_continue_until_configured_limits(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_output_limit_adapter(monkeypatch)
    run_dir, first_runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-output-limit-configured-limits",
        max_turns=4,
        developer_session_policy="resume",
    )
    session_ref = f"thread-{first_runtime['turn_id']}"

    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            first_runtime,
            adapter_completed=False,
            process_exit_code=75,
            termination_kind="output_limit",
            observed_session_ref=session_ref,
        )
        first_event = finalize_external_turn_locked(
            run_dir,
            first_runtime,
            allow_after_capture=True,
        )
        second_runtime, continuity_error = create_business_turn_locked(
            run_dir,
            role_id="developer",
            executor="worker",
        )
        assert second_runtime is not None
        _persist_process_chain(
            run_dir,
            second_runtime,
            adapter_completed=False,
            process_exit_code=75,
            termination_kind="output_limit",
            observed_session_ref=session_ref,
        )
        second_event = finalize_external_turn_locked(
            run_dir,
            second_runtime,
            allow_after_capture=True,
        )

    assert first_event is not None
    assert first_event["event_type"] == "handoff"
    assert continuity_error is None
    assert second_event is not None
    assert second_event["event_type"] == "handoff"
    assert second_event["continuation_reason"] == "output_limit"
    assert scan_journal(run_dir).status == "RUNNING"


def test_output_limit_respects_turn_limit(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_output_limit_adapter(monkeypatch)
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-output-limit-turn-limit",
        max_turns=1,
        developer_session_policy="resume",
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            runtime,
            adapter_completed=False,
            process_exit_code=75,
            termination_kind="output_limit",
        )
        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )

    assert event is not None
    assert event["event_type"] == "block"
    assert event["block_reason"] == "limit"
    assert event.get("limit_reason") == "max_turns"


def test_output_limit_continues_fresh_role_in_new_session_generation(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _install_output_limit_adapter(monkeypatch)
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-output-limit-fresh-continuation",
        max_turns=4,
        developer_session_policy="fresh",
    )
    session_ref = f"thread-{runtime['turn_id']}"

    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            runtime,
            adapter_completed=False,
            process_exit_code=75,
            termination_kind="output_limit",
            observed_session_ref=session_ref,
        )
        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )
        next_runtime, continuity_error = create_business_turn_locked(
            run_dir,
            role_id="developer",
            executor="worker",
        )

    assert event is not None
    assert event["event_type"] == "handoff"
    assert event["continuation_reason"] == "output_limit"
    assert prepared
    assert set(prepared) == {2}
    assert next_runtime is not None
    assert continuity_error is None

    assert next_runtime["session_generation"] == 2
    input_payload = (
        run_dir / "turns" / next_runtime["turn_id"] / "input.md"
    ).read_text(encoding="utf-8")
    assert "Fresh policy creates a new Session generation" in input_payload


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


def test_fresh_external_self_handoff_prepares_next_session_generation(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-generation-aware-handoff",
    )
    prepared: list[int] = []

    class RecordingAdapter:
        @staticmethod
        def assert_profile(*_args: object, **_kwargs: object) -> None:
            return None

        @staticmethod
        def prepare_run_state(
            *,
            run_dir: Path,
            role_id: str,
            launch_mode: str,
            session_generation: int,
        ) -> None:
            assert run_dir.name == "at-worker-generation-aware-handoff"
            assert role_id == "developer"
            assert launch_mode == "headless"
            prepared.append(session_generation)

    monkeypatch.setattr(
        "agent_team.turns.get_adapter",
        lambda _adapter_id: RecordingAdapter(),
    )
    payload = run_dir / "turns" / runtime["turn_id"] / "handoff-source.md"
    payload.write_text("# Handoff\n\nReview generation two.\n", encoding="utf-8")

    with locked_run(run_dir, exclusive=True):
        accepted = stage_external_action_locked(
            run_dir,
            runtime=runtime,
            action="handoff",
            source_file=payload,
            to_role="developer",
        )

    assert accepted["code"] == "ACTION_ACCEPTED"
    assert prepared == [2]


def test_fixable_route_preflight_rejection_keeps_turn_actionable(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-route-preflight-rejected",
        include_external_reviewer=True,
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    payload = turn_dir / "handoff-source.md"
    payload.write_text("# Handoff\n\nThe target artifact needs repair.\n")
    monkeypatch.setattr(
        "agent_team.turns.get_adapter",
        lambda _adapter_id: _RoutePreflightAdapter("reviewer"),
    )

    with locked_run(run_dir, exclusive=True):
        with pytest.raises(AgentTeamError) as rejected:
            stage_external_action_locked(
                run_dir,
                runtime=runtime,
                action="handoff",
                source_file=payload,
                to_role="reviewer",
            )
        projection = scan_journal(run_dir)
        assert rejected.value.code == "ROUTE_PREFLIGHT_REJECTED"
        assert "No Outbox or Handoff Event was staged" in rejected.value.message
        assert projection.status == "RUNNING"
        assert projection.current_role == "developer"
        assert projection.tail == projection.kickoff
        assert not (turn_dir / "outbox.json").exists()
        assert not (turn_dir / "outbox-payload.md").exists()

        redirected = stage_external_action_locked(
            run_dir,
            runtime=runtime,
            action="handoff",
            source_file=payload,
            to_role="developer",
        )

    assert redirected["code"] == "ACTION_ACCEPTED"
    assert redirected["outbox"]["to_role"] == "developer"


def test_candidate_activation_crash_returns_finding_to_sending_role(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, developer_runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-candidate-activation-finding",
        include_external_reviewer=True,
    )
    adapter = _CandidateActivationAdapter()
    monkeypatch.setattr("agent_team.worker.get_adapter", lambda _adapter: adapter)
    monkeypatch.setattr("agent_team.turns.get_adapter", lambda _adapter: adapter)
    developer_payload = (
        run_dir / "turns" / developer_runtime["turn_id"] / "handoff-source.md"
    )
    developer_payload.write_text(
        "# Handoff\n\nPlease activate and validate the candidate.\n",
        encoding="utf-8",
    )

    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, developer_runtime)
        stage_external_action_locked(
            run_dir,
            runtime=developer_runtime,
            action="handoff",
            source_file=developer_payload,
            to_role="reviewer",
        )
        first_handoff = finalize_external_turn_locked(
            run_dir,
            developer_runtime,
            allow_after_capture=True,
        )
        reviewer_runtime, continuity_error = create_business_turn_locked(
            run_dir,
            role_id="reviewer",
            executor="worker",
        )

    assert first_handoff is not None
    assert first_handoff["to_role"] == "reviewer"
    assert reviewer_runtime is not None
    assert continuity_error is None

    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(
            run_dir,
            reviewer_runtime,
            adapter_completed=False,
            process_exit_code=1,
            termination_kind="crash",
            observed_session_ref="candidate-session",
        )
        activation_handoff = finalize_external_turn_locked(
            run_dir,
            reviewer_runtime,
            allow_after_capture=True,
        )

    assert activation_handoff is not None
    assert activation_handoff["event_type"] == "handoff"
    assert activation_handoff["from_role"] == "reviewer"
    assert activation_handoff["to_role"] == "developer"
    assert (
        activation_handoff["system_handoff_reason"]
        == "candidate_activation_failed"
    )
    assert "candidate-activation-to-developer" in activation_handoff["payload_path"]
    payload = (run_dir / activation_handoff["payload_path"]).read_text(
        encoding="utf-8"
    )
    assert "Agent-Team Candidate Activation Finding" in payload
    assert "did not parse terminal prose" in payload
    reviewer = scan_journal(run_dir).team.roles["reviewer"]
    failed_session = load_session(run_dir, reviewer)
    assert failed_session is not None
    assert failed_session["generation"] == 1
    assert failed_session["status"] == "unavailable"
    assert failed_session["session_ref"] is None
    assert failed_session["unavailable_reason"] == "candidate_activation_failed"
    assert session_launch_state(run_dir, reviewer) == (2, None)
    projection = scan_journal(run_dir)
    assert projection.status == "RUNNING"
    assert projection.current_role == "developer"
    assert derive_observation(run_dir)["journal_tail"][
        "system_handoff_reason"
    ] == "candidate_activation_failed"

    with locked_run(run_dir, exclusive=True):
        next_runtime, continuity_error = create_business_turn_locked(
            run_dir,
            role_id="developer",
            executor="worker",
        )

    assert next_runtime is not None
    assert continuity_error is None

    event_path = (
        run_dir
        / "events"
        / (
            f"{activation_handoff['event_seq']:04d}-"
            f"{activation_handoff['event_id']}.json"
        )
    )
    invalid_event = read_json(event_path)
    invalid_event["system_handoff_reason"] = "untrusted_payload_heading"
    atomic_json(event_path, invalid_event)
    with pytest.raises(IntegrityError, match="invalid system Handoff reason"):
        scan_journal(run_dir)


def test_route_preflight_change_after_outbox_staging_still_blocks(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-route-preflight-changed-after-stage",
        include_external_reviewer=True,
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    payload = turn_dir / "handoff-source.md"
    payload.write_text("# Handoff\n\nThe reviewed artifact is ready.\n")
    monkeypatch.setattr(
        "agent_team.turns.get_adapter",
        lambda _adapter_id: _RoutePreflightAdapter(),
    )
    with locked_run(run_dir, exclusive=True):
        _persist_process_chain(run_dir, runtime)
        accepted = stage_external_action_locked(
            run_dir,
            runtime=runtime,
            action="handoff",
            source_file=payload,
            to_role="reviewer",
        )
    assert accepted["code"] == "ACTION_ACCEPTED"

    monkeypatch.setattr(
        "agent_team.turns.get_adapter",
        lambda _adapter_id: _RoutePreflightAdapter("reviewer"),
    )
    with locked_run(run_dir, exclusive=True):
        event = finalize_external_turn_locked(
            run_dir,
            runtime,
            allow_after_capture=True,
        )

    assert event is not None
    assert event["event_type"] == "block"
    assert event["block_reason"] == "profile_changed"
    assert scan_journal(run_dir).status == "BLOCKED"


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


@pytest.mark.parametrize(
    ("reviewer_prepared", "expected_roles"),
    [
        (False, ["developer"]),
        (True, ["developer", "reviewer"]),
    ],
)
def test_terminal_owner_release_finalizes_only_prepared_external_roles(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    reviewer_prepared: bool,
    expected_roles: list[str],
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
        def has_prepared_run_state(
            self,
            *,
            run_dir: Path,
            role_id: str,
            launch_mode: str,
        ) -> bool:
            assert run_dir.name == "at-worker-release-all-adapters"
            assert launch_mode == "headless"
            return role_id == "reviewer" and reviewer_prepared

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

    assert finalized == [(role_id, "headless") for role_id in expected_roles]
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

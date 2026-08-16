from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_team import observation
from agent_team.bootstrap import initialize_run, start_run
from agent_team.config import Role, make_team
from agent_team.errors import AgentTeamError, IntegrityError
from agent_team.gitfacts import capture_workspace_facts, write_workspace_facts
from agent_team.journal import scan_journal
from agent_team.management import cancel_run, unlock_workspace
from agent_team.observation import derive_observation
from agent_team.origin import origin_action, origin_resume, wait_origin
from agent_team.state import read_owner, release_owner
from agent_team.turns import iter_runtimes


def make_origin_run(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    *,
    run_id: str = "at-test-origin",
    max_turns: int = 4,
) -> Path:
    request, protocol = request_protocol
    team = make_team(
        run_id=run_id,
        workspace=workspace,
        origin_harness="codex",
        roles={"reviewer": Role("reviewer", "origin")},
        initial_role="reviewer",
        max_turns=max_turns,
        max_wall_time_seconds=600,
    )
    return initialize_run(
        team=team,
        request_path=request,
        protocol_path=protocol,
    )


def test_pure_origin_run_completes_and_releases_owner(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(workspace, request_protocol)

    started = start_run(run_dir)
    assert started["status"] == "RUNNING"
    assert started["tmux"]["session"] is None
    assert read_owner(workspace)["run_id"] == run_dir.name

    claim = wait_origin(run_dir, timeout=0)
    assert claim["code"] == "ORIGIN_KICKOFF"
    assert claim["role_id"] == "reviewer"
    payload = run_dir / "turns" / claim["turn_id"] / "completion.md"
    payload.write_text(
        "# Completion\n\nThe requested work is complete and verified.\n",
        encoding="utf-8",
    )
    payload.chmod(0o644)

    completed = origin_action(
        run_dir,
        action="complete",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )
    assert completed["code"] == "TEAM_COMPLETED"
    assert stat.S_IMODE(payload.stat().st_mode) == 0o600
    assert scan_journal(run_dir).status == "COMPLETED"
    assert read_owner(workspace) is not None

    delivered = wait_origin(run_dir, timeout=0, claim=claim["claim"])
    assert delivered["code"] == "TEAM_COMPLETED"
    assert read_owner(workspace) is None

    observation = derive_observation(run_dir)
    assert observation["run_status"] == "COMPLETED"
    assert observation["workspace_owner"] == "released"
    assert observation["recommended_action"] == "READ_COMPLETION"

    repeated_start = start_run(run_dir)
    assert repeated_start["status"] == "COMPLETED"
    assert repeated_start["kickoff_event"] is None
    assert read_owner(workspace) is None


def test_origin_handoff_on_final_business_turn_becomes_nonresumable_limit_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-final-turn-handoff",
        max_turns=1,
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    payload = run_dir / "turns" / claim["turn_id"] / "handoff.md"
    payload.write_text("# Handoff\n\nContinue review.\n", encoding="utf-8")

    blocked = origin_action(
        run_dir,
        action="handoff",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
        to_role="reviewer",
        wait_timeout=0,
    )

    assert blocked["code"] == "TEAM_BLOCKED"
    assert blocked["event"]["block_reason"] == "limit"
    assert blocked["event"]["limit_reason"] == "max_turns"
    assert scan_journal(run_dir).status == "BLOCKED"
    business = [
        runtime
        for runtime in iter_runtimes(run_dir)
        if runtime["business_turn_seq"] is not None
    ]
    assert len(business) == 1
    assert business[0]["outcome"] == "stalled"

    manager = wait_origin(run_dir, timeout=0, claim=claim["claim"])
    instruction = run_dir / "turns" / manager["turn_id"] / "resume.md"
    instruction.write_text("Continue in the same run.\n", encoding="utf-8")
    with pytest.raises(AgentTeamError) as rejected:
        origin_resume(
            run_dir,
            claim=manager["claim"],
            to_role="reviewer",
            source_file=instruction,
            wait_timeout=0,
        )
    assert rejected.value.code == "NEW_RUN_REQUIRED"


def test_origin_claim_is_exclusive(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-claim",
    )
    start_run(run_dir)
    first = wait_origin(run_dir, timeout=0)

    try:
        wait_origin(run_dir, timeout=0)
    except AgentTeamError as exc:
        assert exc.code == "ORIGIN_TURN_ALREADY_CLAIMED"
    else:
        raise AssertionError("second Origin session unexpectedly claimed the turn")

    repeated = wait_origin(run_dir, timeout=0, claim=first["claim"])
    assert repeated["turn_id"] == first["turn_id"]
    assert repeated["claim"] == first["claim"]


def test_deadline_after_kickoff_before_origin_claim_creates_limit_turn(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-deadline-before-claim",
    )
    with monkeypatch.context() as patched:
        patched.setattr(
            "agent_team.journal.rfc3339",
            lambda value=None: "2020-01-01T00:00:00.000Z",
        )
        start_run(run_dir)

    blocked = wait_origin(run_dir, timeout=0)

    assert blocked["code"] == "TEAM_BLOCKED"
    assert blocked["event"]["block_reason"] == "limit"
    assert blocked["event"]["limit_reason"] == "deadline"
    runtimes = iter_runtimes(run_dir)
    business = [item for item in runtimes if item["business_turn_seq"] is not None]
    assert len(business) == 1
    assert business[0]["phase"] == "finalized"
    assert business[0]["outcome"] == "cancelled"
    assert business[0]["workspace_facts_before_sha256"] is None
    assert business[0]["origin_claim_id"] is None
    assert blocked["claim"]


def test_blocked_origin_state_ignores_historical_finalized_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    projection = SimpleNamespace(
        status="BLOCKED",
        team=SimpleNamespace(roles={"reviewer": Role("reviewer", "origin")}),
    )
    monkeypatch.setattr(
        observation,
        "active_runtime",
        lambda run_dir, *, team: None,
    )

    assert observation._origin_state(tmp_path, projection) == "unclaimed"

    monkeypatch.setattr(
        observation,
        "active_runtime",
        lambda run_dir, *, team: {
            "executor": "origin",
            "phase": "exited",
        },
    )
    assert observation._origin_state(tmp_path, projection) == "exited"


def test_deadline_before_claim_recovers_after_runtime_commit_crash(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-deadline-claim-crash",
    )
    with monkeypatch.context() as patched:
        patched.setattr(
            "agent_team.journal.rfc3339",
            lambda value=None: "2020-01-01T00:00:00.000Z",
        )
        start_run(run_dir)

    with monkeypatch.context() as patched:
        patched.setattr(
            "agent_team.turns.finalize_deadline_before_claim_locked",
            lambda run_dir, runtime: (_ for _ in ()).throw(
                RuntimeError("injected crash")
            ),
        )
        with pytest.raises(RuntimeError, match="injected crash"):
            wait_origin(run_dir, timeout=0)

    pending = iter_runtimes(run_dir)
    assert len(pending) == 1
    assert pending[0]["phase"] == "starting"
    assert pending[0]["workspace_facts_before_sha256"] is None
    assert pending[0]["terminal_event_id"] is None

    blocked = wait_origin(run_dir, timeout=0)
    assert blocked["code"] == "TEAM_BLOCKED"
    assert blocked["event"]["limit_reason"] == "deadline"
    finalized = iter_runtimes(run_dir)
    assert finalized[0]["phase"] == "finalized"
    assert finalized[0]["terminal_event_id"] == blocked["event"]["event_id"]


def test_cancel_blocked_source_origin_does_not_reterminalize_its_turn(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-cancel-blocked-origin-source",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    payload = run_dir / "turns" / claim["turn_id"] / "block.md"
    payload.write_text("# Block\n\nNeed user input.\n", encoding="utf-8")
    blocked = origin_action(
        run_dir,
        action="block",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )
    source = iter_runtimes(run_dir)[0]
    assert source["phase"] == "exited"
    assert source["terminal_event_id"] == blocked["event"]["event_id"]

    cancelled = cancel_run(run_dir)

    assert cancelled["event_type"] == "cancel"
    assert cancelled["turn_id"] is None
    after = iter_runtimes(run_dir)[0]
    assert after["phase"] == "exited"
    assert after["terminal_event_id"] == blocked["event"]["event_id"]


def test_origin_block_can_resume_only_through_management_claim(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-resume",
    )
    start_run(run_dir)
    first = wait_origin(run_dir, timeout=0)
    block_payload = run_dir / "turns" / first["turn_id"] / "block.md"
    block_payload.write_text("# Block\n\nNeed direction.\n", encoding="utf-8")
    origin_action(
        run_dir,
        action="block",
        turn_id=first["turn_id"],
        claim=first["claim"],
        from_role="reviewer",
        source_file=block_payload,
    )

    manager = wait_origin(run_dir, timeout=0, claim=first["claim"])
    assert manager["code"] == "TEAM_BLOCKED"
    assert manager["role_id"] is None
    instruction_bytes = b"Continue with the evidence already collected.\\n"
    instruction = run_dir / "turns" / manager["turn_id"] / "resume.md"
    instruction.write_bytes(instruction_bytes)
    instruction.chmod(0o644)

    resumed = origin_resume(
        run_dir,
        claim=manager["claim"],
        to_role="reviewer",
        source_file=instruction,
        wait_timeout=0,
    )

    assert resumed["code"] == "RESUME_TO_ORIGIN_ROLE"
    assert stat.S_IMODE(instruction.stat().st_mode) == 0o600
    assert resumed["role_id"] == "reviewer"
    resume_event = scan_journal(run_dir).events[-1]
    assert resume_event["event_type"] == "resume"
    frozen = (run_dir / "turns" / resumed["turn_id"] / "input.md").read_bytes()
    assert frozen.endswith(instruction_bytes)
    assert resume_event["payload_sha256"] == resumed["event"]["payload_sha256"]


def test_committed_resume_reconciles_after_runtime_save_crash(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-resume-runtime-crash",
    )
    start_run(run_dir)
    first = wait_origin(run_dir, timeout=0)
    block_payload = run_dir / "turns" / first["turn_id"] / "block.md"
    block_payload.write_text("# Block\n\nNeed direction.\n", encoding="utf-8")
    origin_action(
        run_dir,
        action="block",
        turn_id=first["turn_id"],
        claim=first["claim"],
        from_role="reviewer",
        source_file=block_payload,
    )
    manager = wait_origin(run_dir, timeout=0, claim=first["claim"])
    instruction = run_dir / "turns" / manager["turn_id"] / "resume.md"
    instruction.write_text("Continue with the existing evidence.\n", encoding="utf-8")

    with monkeypatch.context() as patched:
        patched.setattr(
            "agent_team.origin.save_runtime",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("injected Runtime save crash")
            ),
        )
        with pytest.raises(OSError, match="Runtime save crash"):
            origin_resume(
                run_dir,
                claim=manager["claim"],
                to_role="reviewer",
                source_file=instruction,
                wait_timeout=0,
            )

    resumed = wait_origin(
        run_dir,
        timeout=0,
        claim=manager["claim"],
    )

    assert resumed["code"] == "RESUME_TO_ORIGIN_ROLE"
    management_runtime = next(
        item
        for item in iter_runtimes(run_dir)
        if item["turn_id"] == manager["turn_id"]
    )
    assert management_runtime["phase"] == "finalized"
    assert management_runtime["terminal_event_id"].startswith("resume-")


def test_origin_after_facts_without_runtime_hash_recovers_as_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-after-facts-crash",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    turn_dir = run_dir / "turns" / claim["turn_id"]
    frozen_after = capture_workspace_facts(
        workspace,
        turn_id=claim["turn_id"],
        boundary="after",
    )
    write_workspace_facts(
        turn_dir / "workspace-facts-after.json",
        frozen_after,
    )
    interrupted = derive_observation(run_dir)
    assert interrupted["run_status"] == "RUNNING"
    assert interrupted["health"] != "corrupted"
    payload = turn_dir / "completion.md"
    payload.write_text("# Completion\n\nDone.\n", encoding="utf-8")

    result = origin_action(
        run_dir,
        action="complete",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )

    assert result["code"] == "TEAM_BLOCKED"
    assert result["event"]["block_reason"] == "recovery"
    runtime = iter_runtimes(run_dir)[0]
    assert runtime["workspace_facts_after_sha256"] is not None
    assert runtime["phase"] == "exited"
    assert runtime["outcome"] == "failed"
    assert derive_observation(run_dir)["run_status"] == "BLOCKED"


def test_origin_after_facts_capture_failure_becomes_recovery_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-after-capture-failure",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    turn_dir = run_dir / "turns" / claim["turn_id"]
    payload = turn_dir / "completion.md"
    payload.write_text("# Completion\n\nDone.\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_team.origin.capture_workspace_facts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IntegrityError("injected After Facts failure")
        ),
    )

    result = origin_action(
        run_dir,
        action="complete",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )

    assert result["code"] == "TEAM_BLOCKED"
    assert result["event"]["block_reason"] == "recovery"
    runtime = iter_runtimes(run_dir)[0]
    assert runtime["phase"] == "exited"
    assert runtime["outcome"] == "failed"


def test_before_facts_failure_before_runtime_is_persistently_corrupted(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-before-capture-failure",
    )
    start_run(run_dir)
    monkeypatch.setattr(
        "agent_team.turns.capture_workspace_facts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IntegrityError("injected Before Facts failure")
        ),
    )

    with pytest.raises(
        IntegrityError,
        match="Before Facts failed before Turn Runtime commit",
    ):
        wait_origin(run_dir, timeout=0)

    turn_dir = run_dir / "turns" / "turn-0001"
    assert turn_dir.is_dir()
    assert not (turn_dir / "runtime.json").exists()
    with pytest.raises(IntegrityError, match="turn runtime is missing"):
        scan_journal(run_dir)


def test_corrupt_origin_facts_on_unique_turn_becomes_recovery_block(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-corrupt-facts",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    turn_dir = run_dir / "turns" / claim["turn_id"]
    (turn_dir / "workspace-facts-before.json").write_text(
        '{"schema_version": 1}\n',
        encoding="utf-8",
    )
    payload = turn_dir / "completion.md"
    payload.write_text("# Completion\n\nDone.\n", encoding="utf-8")

    result = origin_action(
        run_dir,
        action="complete",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )

    assert result["code"] == "TEAM_BLOCKED"
    assert result["event"]["block_reason"] == "recovery"
    runtime = iter_runtimes(run_dir)[0]
    assert runtime["phase"] == "exited"
    assert runtime["outcome"] == "failed"
    observation = derive_observation(run_dir)
    assert observation["run_status"] == "BLOCKED"
    assert observation["health"] == "attention"
    assert observation["recommended_action"] == "FINALIZE_ORIGIN_EXIT"


def test_wait_origin_reconciles_event_committed_before_runtime_update(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-event-runtime-crash",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    payload = run_dir / "turns" / claim["turn_id"] / "completion.md"
    payload.write_text("# Completion\n\nDone.\n", encoding="utf-8")

    from agent_team import origin

    real_save_runtime = origin.save_runtime
    calls = 0

    def fail_terminal_runtime_save(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected crash after Event commit")
        return real_save_runtime(*args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(
            "agent_team.origin.save_runtime",
            fail_terminal_runtime_save,
        )
        with pytest.raises(OSError, match="injected crash"):
            origin_action(
                run_dir,
                action="complete",
                turn_id=claim["turn_id"],
                claim=claim["claim"],
                from_role="reviewer",
                source_file=payload,
            )

    interrupted = iter_runtimes(run_dir)[0]
    assert scan_journal(run_dir).status == "COMPLETED"
    assert interrupted["phase"] == "running"
    assert interrupted["terminal_event_id"] is None

    delivered = wait_origin(
        run_dir,
        timeout=0,
        claim=claim["claim"],
    )

    assert delivered["code"] == "TEAM_COMPLETED"
    finalized = iter_runtimes(run_dir)[0]
    assert finalized["phase"] == "finalized"
    assert finalized["outcome"] == "success"
    assert finalized["terminal_event_id"] == delivered["event"]["event_id"]
    assert read_owner(workspace) is None


def test_historical_completion_does_not_disturb_new_workspace_owner(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    old_run = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-historical-completion",
    )
    start_run(old_run)
    old_claim = wait_origin(old_run, timeout=0)
    payload = old_run / "turns" / old_claim["turn_id"] / "completion.md"
    payload.write_text("# Completion\n\nDone.\n", encoding="utf-8")
    origin_action(
        old_run,
        action="complete",
        turn_id=old_claim["turn_id"],
        claim=old_claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )
    wait_origin(old_run, timeout=0, claim=old_claim["claim"])

    new_run = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-new-workspace-owner",
    )
    start_run(new_run)
    assert read_owner(workspace)["run_id"] == new_run.name

    historical = wait_origin(
        old_run,
        timeout=0,
        claim=old_claim["claim"],
    )

    assert historical["code"] == "TEAM_COMPLETED"
    assert read_owner(workspace)["run_id"] == new_run.name


def test_terminal_run_with_unfinalized_origin_rejects_new_owner(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    old_run = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-unsafe-old-owner-release",
    )
    start_run(old_run)
    claim = wait_origin(old_run, timeout=0)
    payload = old_run / "turns" / claim["turn_id"] / "completion.md"
    payload.write_text("# Completion\n\nDone.\n", encoding="utf-8")
    origin_action(
        old_run,
        action="complete",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )
    assert iter_runtimes(old_run)[0]["phase"] == "exited"

    assert release_owner(workspace, old_run.name)
    new_run = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-owner-after-unsafe-release",
    )
    start_run(new_run)

    with pytest.raises(IntegrityError, match="before safe release"):
        derive_observation(old_run)


def test_unlock_pure_origin_run_does_not_require_tmux(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = make_origin_run(
        workspace,
        request_protocol,
        run_id="at-test-origin-unlock-without-tmux",
    )
    start_run(run_dir)
    wait_origin(run_dir, timeout=0)
    monkeypatch.setattr(
        "agent_team.management.has_session",
        lambda *_args, **_kwargs: pytest.fail(
            "pure Origin Unlock must not probe tmux"
        ),
    )

    result = unlock_workspace(
        workspace,
        expect_run=run_dir.name,
        confirm_origin_stopped=True,
    )

    assert result["code"] == "WORKSPACE_UNLOCKED"
    assert read_owner(workspace) is None

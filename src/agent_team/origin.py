from __future__ import annotations

import datetime as dt
import time
from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .errors import (
    AgentTeamError,
    IntegrityError,
    InvalidArgument,
    RecoverableTurnArtifactError,
)
from .gitfacts import (
    capture_workspace_facts,
    load_workspace_facts,
    same_workspace_state,
    write_workspace_facts,
)
from .journal import (
    can_create_business_turn,
    commit_event,
    next_event_identity,
    scan_journal,
)
from .ownership import release_terminal_owner_locked
from .state import locked_run, read_owner
from .tmux_runtime import ensure_workers, signal_change
from .turns import (
    active_runtime,
    commit_technical_block_locked,
    create_business_turn_locked,
    create_management_turn_locked,
    finalize_deadline_before_claim_locked,
    is_deadline_before_claim_pending,
    iter_runtimes,
    load_runtime,
    runtime_for_input,
    save_runtime,
    session_generation_for_route,
    validate_payload_contract,
)
from .util import (
    parse_rfc3339,
    path_entry_exists,
    read_private_regular,
    read_regular,
    resolve_run_path,
    rfc3339,
    safe_relative,
    sha256_bytes,
)


def _runtime_context(
    run_dir: Path, runtime: dict[str, Any], code: str
) -> dict[str, Any]:
    projection = scan_journal(run_dir)
    event = next(
        item
        for item in projection.events
        if item["event_id"] == runtime["input_event_id"]
    )
    source_facts: dict[str, Any] | None = None
    if event.get("turn_id"):
        source = run_dir / "turns" / event["turn_id"]
        if source.exists():
            source_runtime = load_runtime(source, team=projection.team)
            source_facts = {
                "turn_id": event["turn_id"],
                "before_path": (
                    f"turns/{event['turn_id']}/workspace-facts-before.json"
                    if source_runtime["workspace_facts_before_sha256"]
                    else None
                ),
                "before_sha256": source_runtime["workspace_facts_before_sha256"],
                "after_path": (
                    f"turns/{event['turn_id']}/workspace-facts-after.json"
                    if source_runtime["workspace_facts_after_sha256"]
                    else None
                ),
                "after_sha256": source_runtime["workspace_facts_after_sha256"],
            }
    return {
        "code": code,
        "run_id": projection.team.run_id,
        "run_status": projection.status,
        "role_id": runtime["role_id"],
        "turn_id": runtime["turn_id"],
        "claim": runtime["origin_claim_id"],
        "event": event,
        "input_path": f"turns/{runtime['turn_id']}/input.md",
        "before_facts_path": (
            f"turns/{runtime['turn_id']}/workspace-facts-before.json"
            if runtime["workspace_facts_before_sha256"]
            else None
        ),
        "before_facts_sha256": runtime["workspace_facts_before_sha256"],
        "source_facts": source_facts,
    }


def _find_claim(
    run_dir: Path,
    claim: str,
) -> dict[str, Any] | None:
    team = scan_journal(run_dir).team
    matches = [
        runtime
        for runtime in iter_runtimes(run_dir, team=team)
        if runtime["origin_claim_id"] == claim
    ]
    if len(matches) > 1:
        raise IntegrityError("Origin claim appears in multiple runtimes")
    return matches[0] if matches else None


def _origin_terminal_outcome(event: dict[str, Any]) -> str:
    if event["event_type"] in {"handoff", "complete", "resume"}:
        return "success"
    if event["event_type"] == "cancel":
        return "cancelled"
    if event.get("block_reason") == "agent":
        return "success"
    if event.get("block_reason") == "limit":
        return "cancelled" if event.get("limit_reason") == "deadline" else "stalled"
    if event.get("block_reason") == "no_action":
        return "stalled"
    return "failed"


def _reconcile_origin_terminal_locked(
    run_dir: Path,
    runtime: dict[str, Any],
    projection: Any,
) -> bool:
    terminal = projection.terminal_for_turn(runtime["turn_id"])
    if terminal is None:
        return False
    if runtime["phase"] not in {"running", "exited", "finalized"}:
        raise IntegrityError("Origin terminal Event has an invalid Runtime phase")
    if runtime["phase"] != "finalized":
        runtime.update(
            {
                "phase": "finalized",
                "outcome": _origin_terminal_outcome(terminal),
                "terminal_event_id": terminal["event_id"],
            }
        )
        save_runtime(
            run_dir / "turns" / runtime["turn_id"],
            runtime,
            team=projection.team,
        )
    return True


def wait_origin(
    run_dir: Path,
    *,
    timeout: float,
    claim: str | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        with locked_run(run_dir, exclusive=True):
            projection = scan_journal(run_dir)
            team = projection.team
            owner = read_owner(team.workspace)
            if projection.status in {"RUNNING", "BLOCKED"} and (
                owner is None or owner["run_id"] != team.run_id
            ):
                raise IntegrityError(
                    "Origin wait/claim requires exact workspace ownership"
                )
            if claim:
                claimed = _find_claim(run_dir, claim)
                if claimed is None:
                    raise AgentTeamError(
                        "INVALID_ORIGIN_CLAIM", "Origin claim is unknown"
                    )
                if _reconcile_origin_terminal_locked(
                    run_dir,
                    claimed,
                    projection,
                ):
                    claim = None
                    projection = scan_journal(run_dir)
                    release_terminal_owner_locked(run_dir)
                elif claimed["phase"] == "running":
                    code = (
                        "TEAM_BLOCKED"
                        if claimed["role_id"] is None
                        else (
                            "ORIGIN_KICKOFF"
                            if projection.tail
                            and projection.tail["event_type"] == "kickoff"
                            else (
                                "RESUME_TO_ORIGIN_ROLE"
                                if projection.tail
                                and projection.tail["event_type"] == "resume"
                                else "HANDOFF_TO_ORIGIN_ROLE"
                            )
                        )
                    )
                    return _runtime_context(run_dir, claimed, code)
            if projection.status == "COMPLETED":
                release_terminal_owner_locked(run_dir)
                return {
                    "code": "TEAM_COMPLETED",
                    "run_id": team.run_id,
                    "event": projection.tail,
                    "completion_path": projection.tail["payload_path"],
                }
            if projection.status == "CANCELLED":
                release_terminal_owner_locked(run_dir)
                return {
                    "code": "TEAM_CANCELLED",
                    "run_id": team.run_id,
                    "event": projection.tail,
                }
            if projection.status == "BLOCKED":
                existing = active_runtime(run_dir, team=team)
                if existing is not None:
                    if is_deadline_before_claim_pending(existing):
                        finalize_deadline_before_claim_locked(run_dir, existing)
                        continue
                    if existing["executor"] == "origin" and existing["role_id"] is None:
                        raise AgentTeamError(
                            "ORIGIN_TURN_ALREADY_CLAIMED",
                            "Block management turn already has an Origin claim",
                        )
                    if (
                        existing["executor"] == "worker"
                        and projection.tail["turn_id"] == existing["turn_id"]
                    ):
                        # The Block is already authoritative, but the External
                        # runtime may still be stopping or waiting for explicit
                        # recovery. Return it to the user without inventing a
                        # management Claim; a later wait can Claim after the
                        # process-safety gate is closed.
                        return {
                            "code": "TEAM_BLOCKED",
                            "run_id": team.run_id,
                            "event": projection.tail,
                            "claim": None,
                            "recovery_required": (
                                existing["phase"] == "recovery_required"
                                or existing["group_quiescent"] is not True
                            ),
                        }
                    if existing["executor"] == "origin":
                        raise AgentTeamError(
                            "ORIGIN_TURN_ALREADY_CLAIMED",
                            "the Block's source Origin turn has not been finalized",
                        )
                    raise IntegrityError("Blocked run has an unexpected active runtime")
                runtime = create_management_turn_locked(
                    run_dir,
                    block_event=projection.tail,
                )
                return _runtime_context(run_dir, runtime, "TEAM_BLOCKED")
            if (
                projection.status == "RUNNING"
                and projection.current_role is not None
                and team.roles[projection.current_role].binding == "origin"
            ):
                existing = runtime_for_input(
                    run_dir,
                    projection.tail["event_id"],
                    team=team,
                )
                if existing is not None:
                    if is_deadline_before_claim_pending(existing):
                        finalize_deadline_before_claim_locked(run_dir, existing)
                        continue
                    raise AgentTeamError(
                        "ORIGIN_TURN_ALREADY_CLAIMED",
                        "Origin business turn is already claimed",
                    )
                runtime, continuity_error = create_business_turn_locked(
                    run_dir,
                    role_id=projection.current_role,
                    executor="origin",
                )
                if runtime is None:
                    continue
                if continuity_error:
                    event = commit_technical_block_locked(
                        run_dir,
                        runtime=runtime,
                        reason="recovery",
                        message=continuity_error,
                    )
                    runtime.update(
                        {
                            # The host never received this Claim, so there is no
                            # Origin sampling turn to acknowledge later.
                            "phase": "finalized",
                            "outcome": "failed",
                            "terminal_event_id": event["event_id"],
                        }
                    )
                    save_runtime(
                        run_dir / "turns" / runtime["turn_id"],
                        runtime,
                        team=team,
                    )
                    return {
                        "code": "TEAM_BLOCKED",
                        "run_id": team.run_id,
                        "event": event,
                    }
                if runtime["phase"] == "finalized":
                    continue
                runtime["phase"] = "running"
                save_runtime(
                    run_dir / "turns" / runtime["turn_id"],
                    runtime,
                    team=team,
                )
                code = {
                    "kickoff": "ORIGIN_KICKOFF",
                    "handoff": "HANDOFF_TO_ORIGIN_ROLE",
                    "resume": "RESUME_TO_ORIGIN_ROLE",
                }[projection.tail["event_type"]]
                return _runtime_context(run_dir, runtime, code)
        if time.monotonic() >= deadline:
            return {
                "code": "TIMEOUT_TOKEN_NOT_OWNED" if claim else "TIMEOUT",
                "run_id": team.run_id,
            }
        time.sleep(0.25)


def origin_context(
    run_dir: Path,
    *,
    event_id: str,
    claim: str | None,
) -> dict[str, Any]:
    """Read an Origin event without claiming, finalizing, or otherwise mutating it."""
    with locked_run(run_dir, exclusive=False):
        projection = scan_journal(run_dir)
        owner = read_owner(projection.team.workspace)
        if projection.status in {"RUNNING", "BLOCKED"} and (
            owner is None or owner["run_id"] != projection.team.run_id
        ):
            raise IntegrityError("Origin context requires exact workspace ownership")
        tail = projection.tail
        if tail is None or tail["event_id"] != event_id:
            raise AgentTeamError(
                "EVENT_NOT_CURRENT",
                f"event {event_id!r} is not the current Origin event",
            )
        if projection.status == "COMPLETED":
            return {
                "code": "TEAM_COMPLETED",
                "run_id": projection.team.run_id,
                "event": tail,
                "completion_path": tail["payload_path"],
            }
        if projection.status == "CANCELLED":
            return {
                "code": "TEAM_CANCELLED",
                "run_id": projection.team.run_id,
                "event": tail,
            }
        runtime = runtime_for_input(
            run_dir,
            event_id,
            team=projection.team,
        )
        if runtime is None or runtime["executor"] != "origin":
            raise AgentTeamError(
                "ORIGIN_EVENT_NOT_CLAIMED",
                "the current event has no claimed Origin runtime",
            )
        if not claim or runtime["origin_claim_id"] != claim:
            raise AgentTeamError(
                "INVALID_ORIGIN_CLAIM",
                "a matching Origin claim is required for the active event",
            )
        if runtime["phase"] not in {"running", "exited"}:
            raise AgentTeamError(
                "ORIGIN_EVENT_NOT_ACTIVE",
                "the referenced Origin event is no longer active",
            )
        if runtime["role_id"] is None:
            code = "TEAM_BLOCKED"
        elif tail["event_type"] == "kickoff":
            code = "ORIGIN_KICKOFF"
        elif tail["event_type"] == "resume":
            code = "RESUME_TO_ORIGIN_ROLE"
        else:
            code = "HANDOFF_TO_ORIGIN_ROLE"
        return _runtime_context(run_dir, runtime, code)


def _validate_business_claim_locked(
    run_dir: Path,
    *,
    turn_id: str,
    claim: str,
    from_role: str,
) -> tuple[dict[str, Any], Any]:
    projection = scan_journal(run_dir)
    owner = read_owner(projection.team.workspace)
    if owner is None or owner["run_id"] != projection.team.run_id:
        raise IntegrityError("Origin action requires exact workspace ownership")
    runtime = load_runtime(
        run_dir / "turns" / turn_id,
        team=projection.team,
    )
    if (
        runtime["executor"] != "origin"
        or runtime["origin_claim_id"] != claim
        or runtime["role_id"] != from_role
        or runtime["phase"] != "running"
        or projection.status != "RUNNING"
        or projection.current_role != from_role
    ):
        raise AgentTeamError("INVALID_ORIGIN_CLAIM", "claim cannot perform this action")
    input_bytes = read_regular(run_dir / "turns" / turn_id / "input.md")
    if sha256_bytes(input_bytes) != runtime["input_payload_sha256"]:
        raise IntegrityError("frozen Turn input changed before Origin action")
    return runtime, projection


def _freeze_origin_after(
    run_dir: Path,
    runtime: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    team = scan_journal(run_dir).team
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    path = turn_dir / "workspace-facts-after.json"
    recovery_message = None
    if path_entry_exists(path):
        try:
            after = load_workspace_facts(
                path,
                expected_turn_id=runtime["turn_id"],
                expected_boundary="after",
            )
            after_hash = sha256_bytes(read_regular(path))
        except (IntegrityError, OSError) as exc:
            raise RecoverableTurnArtifactError(
                "workspace_facts",
                f"Origin After Facts are damaged: {runtime['turn_id']}: {exc}",
                f"turns/{runtime['turn_id']}/workspace-facts-after.json",
            ) from exc
        if runtime["workspace_facts_after_sha256"] is None:
            runtime["workspace_facts_after_sha256"] = after_hash
            save_runtime(turn_dir, runtime, team=team)
            recovery_message = (
                "Origin After Facts existed without its Runtime hash after a crash. "
                "The frozen snapshot was preserved, but the requested action was "
                "not committed."
            )
        elif after_hash != runtime["workspace_facts_after_sha256"]:
            raise RecoverableTurnArtifactError(
                "workspace_facts",
                "Origin After Facts hash mismatch",
                f"turns/{runtime['turn_id']}/workspace-facts-after.json",
            )
        try:
            current = capture_workspace_facts(
                team.workspace,
                turn_id=runtime["turn_id"],
                boundary="after",
            )
        except (AgentTeamError, OSError) as exc:
            raise RecoverableTurnArtifactError(
                "workspace_facts",
                f"Origin After Facts could not be revalidated: "
                f"{runtime['turn_id']}: {exc}",
                f"turns/{runtime['turn_id']}/workspace-facts-after.json",
            ) from exc
        if not same_workspace_state(after, current):
            recovery_message = (
                "Workspace changed after the frozen Origin After Facts; the original "
                "action cannot be committed against a different workspace state."
            )
    else:
        try:
            after = capture_workspace_facts(
                team.workspace,
                turn_id=runtime["turn_id"],
                boundary="after",
            )
            runtime["workspace_facts_after_sha256"] = write_workspace_facts(
                path,
                after,
            )
            save_runtime(turn_dir, runtime, team=team)
        except (AgentTeamError, OSError) as exc:
            raise RecoverableTurnArtifactError(
                "workspace_facts",
                f"Origin After Facts could not be frozen: "
                f"{runtime['turn_id']}: {exc}",
                f"turns/{runtime['turn_id']}/workspace-facts-after.json",
            ) from exc
    before_path = turn_dir / "workspace-facts-before.json"
    try:
        before = load_workspace_facts(
            before_path,
            expected_turn_id=runtime["turn_id"],
            expected_boundary="before",
        )
        if (
            runtime["workspace_facts_before_sha256"] is None
            or sha256_bytes(read_regular(before_path))
            != runtime["workspace_facts_before_sha256"]
        ):
            raise IntegrityError("Origin Before Facts hash mismatch")
    except (IntegrityError, OSError) as exc:
        raise RecoverableTurnArtifactError(
            "workspace_facts",
            f"Origin Before Facts are damaged: {runtime['turn_id']}: {exc}",
            f"turns/{runtime['turn_id']}/workspace-facts-before.json",
        ) from exc
    return before, after, recovery_message


def _origin_facts(
    runtime: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
) -> bytes:
    return (
        "\n---\n\n## Agent-Team System Facts\n\n"
        f"- Turn ID: {runtime['turn_id']}\n"
        f"- From role: {runtime['role_id']}\n"
        "- Harness: origin\n"
        f"- Git HEAD before: {before['git_head'] or 'unborn'}\n"
        f"- Git HEAD after: {after['git_head'] or 'unborn'}\n"
        f"- Git-visible workspace state SHA-256 before: {before['workspace_state_sha256']}\n"
        f"- Git-visible workspace state SHA-256 after: {after['workspace_state_sha256']}\n"
    ).encode("utf-8")


def origin_action(
    run_dir: Path,
    *,
    action: str,
    turn_id: str,
    claim: str,
    from_role: str,
    source_file: Path,
    to_role: str | None = None,
    wait_timeout: float = 90,
) -> dict[str, Any]:
    with locked_run(run_dir, exclusive=True):
        runtime, projection = _validate_business_claim_locked(
            run_dir,
            turn_id=turn_id,
            claim=claim,
            from_role=from_role,
        )
        team = projection.team
        if action == "handoff" and to_role not in team.roles:
            raise AgentTeamError(
                "ROLE_NOT_FOUND",
                f"target role {to_role!r} does not exist",
            )
        if action not in {"handoff", "complete", "block"}:
            raise InvalidArgument(f"invalid Origin action: {action}")
        if action != "handoff" and to_role is not None:
            raise InvalidArgument(f"{action} does not accept a target role")

        def guard_action() -> tuple[dict[str, Any] | None, str | None]:
            current = scan_journal(run_dir)
            kickoff = current.kickoff
            if kickoff is None:
                raise IntegrityError("Origin action cannot precede kickoff")
            deadline = parse_rfc3339(kickoff["created_at"]) + dt.timedelta(
                seconds=team.max_wall_time_seconds
            )
            now = dt.datetime.now(dt.timezone.utc)
            if now >= deadline:
                return (
                    commit_technical_block_locked(
                        run_dir,
                        runtime=runtime,
                        reason="limit",
                        limit_reason="deadline",
                        message="Wall-time deadline expired before the Origin action.",
                    ),
                    None,
                )
            if action == "handoff":
                allowed, reason = can_create_business_turn(
                    run_dir,
                    current,
                    now=now,
                )
                if not allowed:
                    if reason != "max_turns":
                        raise IntegrityError(
                            f"unexpected Origin Handoff guard result: {reason}"
                        )
                    return (
                        commit_technical_block_locked(
                            run_dir,
                            runtime=runtime,
                            reason="limit",
                            limit_reason="max_turns",
                            message=(
                                "The current Origin turn is the final allowed "
                                "business turn."
                            ),
                        ),
                        None,
                    )
                target = team.roles[to_role or ""]
                if target.binding == "external":
                    try:
                        adapter = get_adapter(target.adapter or "")
                        adapter.assert_profile(
                            target.launch_profile or "",
                            target.session_policy or "",
                            target.launch_profile_sha256 or "",
                            target.launch_mode or "headless",
                        )
                        adapter.prepare_run_state(
                            run_dir=run_dir,
                            role_id=target.role_id,
                            launch_mode=target.launch_mode or "headless",
                            session_generation=session_generation_for_route(
                                run_dir,
                                target,
                                source_runtime=runtime,
                            ),
                        )
                    except AgentTeamError as exc:
                        after_probe = dt.datetime.now(dt.timezone.utc)
                        if after_probe >= deadline:
                            return (
                                commit_technical_block_locked(
                                    run_dir,
                                    runtime=runtime,
                                    reason="limit",
                                    limit_reason="deadline",
                                    message=(
                                        "Wall-time deadline expired while validating "
                                        "the target launch profile."
                                    ),
                                    created_at=rfc3339(after_probe),
                                ),
                                None,
                            )
                        return (
                            commit_technical_block_locked(
                                run_dir,
                                runtime=runtime,
                                reason="profile_changed",
                                message=exc.message,
                                created_at=rfc3339(after_probe),
                            ),
                            None,
                        )
            decision = dt.datetime.now(dt.timezone.utc)
            if decision >= deadline:
                return (
                    commit_technical_block_locked(
                        run_dir,
                        runtime=runtime,
                        reason="limit",
                        limit_reason="deadline",
                        message="Wall-time deadline expired before the Origin action.",
                    ),
                    None,
                )
            return None, rfc3339(decision)

        event, _ = guard_action()
        if event is not None:
            runtime.update(
                {
                    "phase": "exited",
                    "outcome": (
                        "cancelled"
                        if event.get("limit_reason") == "deadline"
                        else (
                            "stalled"
                            if event.get("limit_reason") == "max_turns"
                            else "failed"
                        )
                    ),
                    "terminal_event_id": event["event_id"],
                }
            )
            save_runtime(run_dir / "turns" / turn_id, runtime, team=team)
            return {"code": "TEAM_BLOCKED", "event": event}
        relative_source = safe_relative(source_file, run_dir)
        payload = read_private_regular(resolve_run_path(run_dir, relative_source))
        validate_payload_contract(
            payload,
            required_sections=team.observability.required_payload_sections,
            action=action,
        )
        try:
            before, after, recovery_message = _freeze_origin_after(run_dir, runtime)
        except RecoverableTurnArtifactError as exc:
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=(
                    "Workspace boundary evidence for this uniquely identified "
                    f"Origin Turn is damaged and cannot be regenerated: {exc.message}"
                ),
            )
            runtime.update(
                {
                    "phase": "exited",
                    "outcome": "failed",
                    "terminal_event_id": event["event_id"],
                }
            )
            save_runtime(run_dir / "turns" / turn_id, runtime, team=team)
            return {"code": "TEAM_BLOCKED", "event": event}
        if recovery_message is not None:
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=recovery_message,
            )
            runtime.update(
                {
                    "phase": "exited",
                    "outcome": "failed",
                    "terminal_event_id": event["event_id"],
                }
            )
            save_runtime(run_dir / "turns" / turn_id, runtime, team=team)
            return {"code": "TEAM_BLOCKED", "event": event}
        payload += _origin_facts(runtime, before, after)
        event, decision_at = guard_action()
        if event is not None:
            runtime.update(
                {
                    "phase": "exited",
                    "outcome": (
                        "cancelled"
                        if event.get("limit_reason") == "deadline"
                        else (
                            "stalled"
                            if event.get("limit_reason") == "max_turns"
                            else "failed"
                        )
                    ),
                    "terminal_event_id": event["event_id"],
                }
            )
            save_runtime(run_dir / "turns" / turn_id, runtime, team=team)
            return {"code": "TEAM_BLOCKED", "event": event}
        assert decision_at is not None
        seq, _ = next_event_identity(projection, action)
        if action == "handoff":
            relative = f"handoffs/{seq:04d}-{from_role}-to-{to_role}.md"
            extra: dict[str, Any] = {}
        elif action == "complete":
            relative = f"completion/{seq:04d}-{from_role}.md"
            extra = {}
        else:
            relative = f"handoffs/{seq:04d}-{from_role}-agent-block.md"
            extra = {"block_reason": "agent", "limit_reason": None}
        event = commit_event(
            run_dir,
            event_type=action,
            payload_relative=relative,
            payload_bytes=payload,
            from_role=from_role,
            to_role=to_role if action == "handoff" else None,
            turn_id=turn_id,
            created_at=decision_at,
            extra=extra,
        )
        runtime.update(
            {
                "phase": "finalized" if action == "handoff" else "exited",
                "outcome": "success",
                "terminal_event_id": event["event_id"],
            }
        )
        save_runtime(run_dir / "turns" / turn_id, runtime, team=team)
    if event.get("to_role") and team.roles[event["to_role"]].binding == "external":
        ensure_workers(run_dir, team, role_ids=(event["to_role"],))
        signal_change(team.run_id, event["to_role"])
    if action == "handoff":
        return wait_origin(run_dir, timeout=wait_timeout)
    return {
        "code": "TEAM_COMPLETED" if action == "complete" else "TEAM_BLOCKED",
        "event": event,
    }


def origin_resume(
    run_dir: Path,
    *,
    claim: str,
    to_role: str,
    source_file: Path,
    wait_timeout: float,
) -> dict[str, Any]:
    with locked_run(run_dir, exclusive=True):
        projection = scan_journal(run_dir)
        if projection.status != "BLOCKED" or projection.tail is None:
            raise AgentTeamError("RUN_NOT_BLOCKED", "run is not blocked")
        owner = read_owner(projection.team.workspace)
        if owner is None or owner["run_id"] != projection.team.run_id:
            raise IntegrityError("Origin Resume requires exact workspace ownership")
        block = projection.tail
        if block.get("block_reason") in {"limit", "profile_changed"}:
            raise AgentTeamError(
                "NEW_RUN_REQUIRED",
                "this block cannot be resumed in the same run",
            )
        if any(
            item["phase"] == "recovery_required"
            for item in iter_runtimes(run_dir, team=projection.team)
        ):
            raise AgentTeamError(
                "RECOVERY_REQUIRED",
                "run recover before attempting to resume this Block",
            )
        runtime = _find_claim(run_dir, claim)
        if (
            runtime is None
            or runtime["role_id"] is not None
            or runtime["phase"] != "running"
            or runtime["input_event_id"] != block["event_id"]
        ):
            raise AgentTeamError(
                "INVALID_ORIGIN_CLAIM", "claim is not the Block manager"
            )
        if to_role not in projection.team.roles:
            raise AgentTeamError(
                "ROLE_NOT_FOUND", f"target role {to_role!r} does not exist"
            )
        allowed, reason = can_create_business_turn(run_dir, projection)
        if not allowed:
            raise AgentTeamError(
                "LIMIT_REACHED",
                f"run cannot create another business turn: {reason}",
            )
        target = projection.team.roles[to_role]
        if target.binding == "external":
            adapter = get_adapter(target.adapter or "")
            adapter.assert_profile(
                target.launch_profile or "",
                target.session_policy or "",
                target.launch_profile_sha256 or "",
                target.launch_mode or "headless",
            )
            adapter.prepare_run_state(
                run_dir=run_dir,
                role_id=target.role_id,
                launch_mode=target.launch_mode or "headless",
                session_generation=session_generation_for_route(
                    run_dir,
                    target,
                    source_runtime=runtime,
                ),
            )
        allowed, reason = can_create_business_turn(run_dir, projection)
        if not allowed:
            raise AgentTeamError(
                "LIMIT_REACHED",
                f"run cannot create another business turn: {reason}",
            )
        source_relative = safe_relative(source_file, run_dir)
        instruction = read_private_regular(
            resolve_run_path(run_dir, source_relative)
        )
        if not instruction.strip():
            raise InvalidArgument("Resume user instruction must not be empty")
        decision = dt.datetime.now(dt.timezone.utc)
        allowed, reason = can_create_business_turn(
            run_dir,
            projection,
            now=decision,
        )
        if not allowed:
            raise AgentTeamError(
                "LIMIT_REACHED",
                f"run cannot create another business turn: {reason}",
            )
        decision_at = rfc3339(decision)
        payload = (
            "# Resume\n\n"
            f"- Block event: {block['event_id']}\n"
            f"- Block reason: {block['block_reason']}\n"
            f"- Block payload: {block['payload_path']}\n"
            f"- Block payload SHA-256: {block['payload_sha256']}\n"
            f"- To role: {to_role}\n"
            "- Scope: continue_same_run\n\n"
            "## User instruction\n\n"
        ).encode("utf-8") + instruction
        seq, _ = next_event_identity(projection, "resume")
        event = commit_event(
            run_dir,
            event_type="resume",
            payload_relative=f"resumes/{seq:04d}-block-to-{to_role}.md",
            payload_bytes=payload,
            from_role=None,
            to_role=to_role,
            turn_id=runtime["turn_id"],
            created_at=decision_at,
        )
        runtime.update(
            {
                "phase": "finalized",
                "outcome": "success",
                "terminal_event_id": event["event_id"],
            }
        )
        save_runtime(
            run_dir / "turns" / runtime["turn_id"],
            runtime,
            team=projection.team,
        )
    if target.binding == "external":
        ensure_workers(
            run_dir,
            projection.team,
            role_ids=(to_role,),
        )
        signal_change(projection.team.run_id, to_role)
    return wait_origin(run_dir, timeout=wait_timeout)

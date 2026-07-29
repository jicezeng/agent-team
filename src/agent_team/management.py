from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import ROLE_ID_RE, parse_team
from .errors import AgentTeamError, IntegrityError
from .gitfacts import load_workspace_facts
from .journal import (
    EVENT_FILE_RE,
    TURN_ID_RE,
    commit_event,
    next_event_identity,
    scan_journal,
)
from .ownership import release_terminal_owner_locked
from .processes import (
    process_group_exists,
    process_identity_state,
    terminate_verified_group,
    terminate_verified_process,
)
from .state import (
    get_run_dir,
    locked_run,
    read_owner,
    release_owner,
    workspace_lock,
)
from .supervisor import (
    validate_authorization,
    validate_runner,
    validate_supervisor,
)
from .tmux_runtime import ensure_workers, has_session, signal_change
from .turns import (
    active_runtime,
    commit_technical_block_locked,
    iter_runtimes,
    iter_turn_directories,
    load_runtime,
    load_session,
    replace_damaged_runtime,
    save_runtime,
    session_launch_state,
    validate_runtime,
)
from .util import (
    committed_directory_entries,
    parse_rfc3339,
    path_entry_exists,
    random_token,
    read_json,
    read_regular,
    rfc3339,
    sha256_bytes,
)
from .worker import (
    _load_launch_spec_for_runtime,
    _validate_external_process_chain,
    finalize_external_turn_locked,
    validate_role_snapshot,
)


def cancel_run(run_dir: Path) -> dict[str, Any]:
    with locked_run(run_dir, exclusive=True):
        projection = scan_journal(run_dir)
        if projection.status == "CANCELLED":
            return projection.tail
        if projection.status not in {"RUNNING", "BLOCKED"}:
            raise AgentTeamError(
                "RUN_NOT_CANCELLABLE",
                f"run in {projection.status} cannot be cancelled",
            )
        owner = read_owner(projection.team.workspace)
        if owner is None or owner["run_id"] != projection.team.run_id:
            raise IntegrityError("cannot cancel a started run without exact ownership")
        runtime = active_runtime(run_dir, team=projection.team)
        cancel_runtime = runtime
        if projection.status == "BLOCKED":
            cancel_runtime = (
                runtime
                if runtime
                and runtime["executor"] == "origin"
                and runtime["role_id"] is None
                and runtime["input_event_id"] == projection.tail["event_id"]
                else None
            )
        seq, _ = next_event_identity(projection, "cancel")
        payload = (
            "# Agent-Team Cancellation\n\n"
            "The run was cancelled through an explicit management command.\n"
        ).encode("utf-8")
        event = commit_event(
            run_dir,
            event_type="cancel",
            payload_relative=f"handoffs/{seq:04d}-cancel.md",
            payload_bytes=payload,
            from_role=projection.current_role,
            to_role=None,
            turn_id=cancel_runtime["turn_id"] if cancel_runtime else None,
            extra={"request_id": random_token(12), "cancel_reason": "user"},
        )
        if cancel_runtime and cancel_runtime["executor"] == "origin":
            cancel_runtime.update(
                {
                    "phase": "exited",
                    "outcome": "cancelled",
                    "terminal_event_id": event["event_id"],
                }
            )
            save_runtime(
                run_dir / "turns" / cancel_runtime["turn_id"],
                cancel_runtime,
                team=projection.team,
            )
    for role in projection.team.roles.values():
        if role.binding == "external":
            signal_change(projection.team.run_id, role.role_id)
    return event


def _cleanup_corrupted_processes_locked(
    run_dir: Path,
) -> tuple[list[str], list[str]]:
    """Stop only managed identities that remain unambiguous without Run semantics."""
    actions: list[str] = []
    unresolved: list[str] = []
    group_results: dict[tuple[int, int, str], bool] = {}
    seen_processes: set[tuple[int, str]] = set()

    def stop_group(
        *,
        turn_id: str,
        runner_pid: int,
        runner_pgid: int,
        runner_start_id: str,
    ) -> bool:
        identity = (runner_pid, runner_pgid, runner_start_id)
        if identity in group_results:
            return group_results[identity]
        if not process_group_exists(runner_pgid):
            group_results[identity] = True
            return True
        state = process_identity_state(
            runner_pid,
            runner_start_id,
            pgid=runner_pgid,
        )
        if state != "match":
            unresolved.append(f"runner:{turn_id}:{state}")
            group_results[identity] = False
            return False
        if terminate_verified_group(
            runner_pid=runner_pid,
            runner_pgid=runner_pgid,
            runner_start_id=runner_start_id,
        ):
            actions.append(f"runner-group-terminated:{turn_id}")
            group_results[identity] = True
            return True
        else:
            unresolved.append(f"runner:{turn_id}:not-quiescent")
            group_results[identity] = False
            return False

    def stop_process(*, subject: str, pid: int, start_id: str) -> None:
        identity = (pid, start_id)
        if identity in seen_processes:
            return
        seen_processes.add(identity)
        state = process_identity_state(pid, start_id)
        if state in {"gone", "reused"}:
            return
        if state != "match":
            unresolved.append(f"{subject}:{state}")
            return
        if pid == os.getpid():
            unresolved.append(f"{subject}:current-recover-process")
            return
        if terminate_verified_process(pid=pid, start_id=start_id):
            actions.append(f"process-terminated:{subject}")
        else:
            unresolved.append(f"{subject}:not-stopped")

    turns_dir = run_dir / "turns"
    try:
        turn_entries = sorted(
            committed_directory_entries(turns_dir),
            key=lambda item: item.name,
        )
    except (IntegrityError, OSError) as exc:
        unresolved.append(f"turns:{type(exc).__name__}")
        turn_entries = []
    for turn_dir in turn_entries:
        if (
            not TURN_ID_RE.fullmatch(turn_dir.name)
            or turn_dir.is_symlink()
            or not turn_dir.is_dir()
        ):
            unresolved.append(f"turn-entry:{turn_dir.name}:invalid")
            continue
        process_dir = turn_dir / "process"
        if not path_entry_exists(process_dir):
            continue
        if process_dir.is_symlink() or not process_dir.is_dir():
            unresolved.append(f"process-dir:{turn_dir.name}:invalid")
            continue

        supervisor: dict[str, Any] | None = None
        supervisor_path = process_dir / "supervisor.json"
        if path_entry_exists(supervisor_path):
            try:
                supervisor = validate_supervisor(read_json(supervisor_path))
                if supervisor["turn_id"] != turn_dir.name:
                    raise IntegrityError(
                        "Supervisor turn does not match its directory"
                    )
            except (IntegrityError, OSError) as exc:
                unresolved.append(
                    f"supervisor:{turn_dir.name}:invalid:{type(exc).__name__}"
                )
                supervisor = None

        runner: dict[str, Any] | None = None
        runner_path = process_dir / "runner.json"
        if path_entry_exists(runner_path):
            try:
                runner_value = read_json(runner_path)
                nonce = runner_value.get("launch_nonce")
                if not isinstance(nonce, str) or not nonce:
                    raise IntegrityError("Runner launch nonce is invalid")
                runner = validate_runner(
                    runner_value,
                    turn_id=turn_dir.name,
                    nonce=nonce,
                )
            except (IntegrityError, OSError) as exc:
                unresolved.append(
                    f"runner:{turn_dir.name}:invalid:{type(exc).__name__}"
                )
                runner = None

        candidate: tuple[int, int, str] | None = None
        if supervisor is not None and supervisor["runner_pid"] is not None:
            candidate = (
                supervisor["runner_pid"],
                supervisor["runner_pgid"],
                supervisor["runner_start_id"],
            )
            if runner is not None and candidate != (
                runner["runner_pid"],
                runner["runner_pgid"],
                runner["runner_start_id"],
            ):
                unresolved.append(f"runner:{turn_dir.name}:identity-conflict")
                candidate = None
        elif runner is not None:
            if supervisor is not None:
                unresolved.append(f"runner:{turn_dir.name}:orphaned")
            else:
                candidate = (
                    runner["runner_pid"],
                    runner["runner_pgid"],
                    runner["runner_start_id"],
                )

        authorization_path = process_dir / "launch-authorized.json"
        if path_entry_exists(authorization_path):
            try:
                authorization_value = read_json(authorization_path)
                nonce = authorization_value.get("launch_nonce")
                if not isinstance(nonce, str) or not nonce:
                    raise IntegrityError("authorization launch nonce is invalid")
                authorization = validate_authorization(
                    authorization_value,
                    turn_id=turn_dir.name,
                    nonce=nonce,
                )
                if candidate is None or candidate != (
                    authorization["runner_pid"],
                    authorization["runner_pgid"],
                    authorization["runner_start_id"],
                ):
                    raise IntegrityError(
                        "authorization Runner identity is ambiguous"
                    )
                if supervisor is None or (
                    authorization["supervisor_pid"],
                    authorization["supervisor_start_id"],
                ) != (
                    supervisor["supervisor_pid"],
                    supervisor["supervisor_start_id"],
                ):
                    raise IntegrityError(
                        "authorization Supervisor identity is ambiguous"
                    )
            except (IntegrityError, OSError) as exc:
                unresolved.append(
                    f"authorization:{turn_dir.name}:invalid:{type(exc).__name__}"
                )

        runner_evidence_present = runner is not None or (
            supervisor is not None and supervisor["runner_pid"] is not None
        )
        group_safely_stopped = (
            candidate is None and not runner_evidence_present
        )
        if candidate is not None:
            group_safely_stopped = stop_group(
                turn_id=turn_dir.name,
                runner_pid=candidate[0],
                runner_pgid=candidate[1],
                runner_start_id=candidate[2],
            )
        if supervisor is not None and group_safely_stopped:
            stop_process(
                subject=f"supervisor:{turn_dir.name}",
                pid=supervisor["supervisor_pid"],
                start_id=supervisor["supervisor_start_id"],
            )
        elif supervisor is not None:
            unresolved.append(
                f"supervisor:{turn_dir.name}:retained-for-runner-cleanup"
            )

    roles_dir = run_dir / "roles"
    try:
        role_entries = sorted(
            committed_directory_entries(roles_dir),
            key=lambda item: item.name,
        )
    except (IntegrityError, OSError) as exc:
        unresolved.append(f"roles:{type(exc).__name__}")
        role_entries = []
    for role_path in role_entries:
        role_id = role_path.stem
        if (
            role_path.suffix != ".json"
            or not ROLE_ID_RE.fullmatch(role_id)
            or role_path.is_symlink()
            or not role_path.is_file()
        ):
            unresolved.append(f"role-entry:{role_path.name}:invalid")
            continue
        try:
            worker = validate_role_snapshot(read_json(role_path), role_id)
        except (IntegrityError, OSError) as exc:
            unresolved.append(
                f"worker:{role_id}:invalid:{type(exc).__name__}"
            )
            continue
        stop_process(
            subject=f"worker:{role_id}",
            pid=worker["worker_pid"],
            start_id=worker["worker_start_id"],
        )
    return actions, unresolved


def _recover_unique_damaged_runtime_locked(
    run_dir: Path,
) -> dict[str, Any] | None:
    """Commit a Recovery Block when one active Runtime alone is damaged."""
    team = parse_team(read_json(run_dir / "team.json"))
    turn_dirs = iter_turn_directories(run_dir)
    valid_runtimes: list[dict[str, Any]] = []
    damaged: list[tuple[Path, Exception]] = []
    for turn_dir in turn_dirs:
        try:
            valid_runtimes.append(load_runtime(turn_dir, team=team))
        except (IntegrityError, OSError) as exc:
            damaged.append((turn_dir, exc))
    if len(damaged) != 1:
        return None
    turn_dir, damage = damaged[0]
    if not turn_dirs or turn_dir != turn_dirs[-1]:
        return None
    turn_id = turn_dir.name

    indexed_events: list[tuple[int, Path]] = []
    for event_path in committed_directory_entries(run_dir / "events"):
        match = EVENT_FILE_RE.fullmatch(event_path.name)
        if (
            match is None
            or event_path.is_symlink()
            or not event_path.is_file()
        ):
            raise IntegrityError(
                f"invalid event entry during Runtime recovery: {event_path.name}"
            )
        indexed_events.append((int(match.group(1)), event_path))
    event_paths = [
        path
        for _, path in sorted(
            indexed_events,
            key=lambda item: (item[0], item[1].name),
        )
    ]
    if not event_paths:
        return None
    raw_events = [read_json(path) for path in event_paths]
    raw_tail = raw_events[-1]
    existing_recovery = bool(
        raw_tail.get("event_type") == "block"
        and raw_tail.get("block_reason") == "recovery"
        and raw_tail.get("turn_id") == turn_id
    )
    input_event_id = (
        raw_tail.get("prev_event_id")
        if existing_recovery
        else raw_tail.get("event_id")
    )
    input_event = next(
        (
            event
            for event in raw_events
            if event.get("event_id") == input_event_id
        ),
        None,
    )
    if (
        input_event is None
        or input_event.get("event_type") not in {"kickoff", "handoff", "resume"}
        or not isinstance(input_event.get("to_role"), str)
    ):
        return None
    role_id = input_event["to_role"]
    role = team.roles.get(role_id)
    if role is None or role.binding != "external":
        # A host Origin sampling turn has no independently verifiable process
        # boundary. Do not pretend that recover proved the old host turn stopped.
        return None

    try:
        raw_runtime = read_json(turn_dir / "runtime.json")
    except (IntegrityError, OSError):
        raw_runtime = {}
    for field, expected in {
        "turn_id": turn_id,
        "input_event_id": input_event_id,
        "role_id": role_id,
        "executor": "worker",
    }.items():
        if field in raw_runtime and raw_runtime[field] != expected:
            return None

    input_bytes = read_regular(turn_dir / "input.md")
    if (
        sha256_bytes(input_bytes) != input_event.get("payload_sha256")
        or input_event.get("payload_path") is None
    ):
        return None
    before_path = turn_dir / "workspace-facts-before.json"
    before = load_workspace_facts(
        before_path,
        expected_turn_id=turn_id,
        expected_boundary="before",
    )
    if before["workspace_realpath"] != str(team.workspace):
        return None
    before_hash = sha256_bytes(read_regular(before_path))

    current_session = load_session(run_dir, role)
    raw_generation = raw_runtime.get("session_generation")
    if (
        isinstance(raw_generation, int)
        and not isinstance(raw_generation, bool)
        and raw_generation >= 1
    ):
        generation = raw_generation
    elif current_session and current_session["updated_turn_id"] == turn_id:
        generation = current_session["generation"]
    else:
        generation, _ = session_launch_state(run_dir, role)

    try:
        created_at = raw_runtime.get("created_at")
        parse_rfc3339(created_at)
    except (IntegrityError, TypeError):
        created_at = input_event["created_at"]
        parse_rfc3339(created_at)

    business_sequence = (
        sum(
            runtime["business_turn_seq"] is not None
            for runtime in valid_runtimes
        )
        + 1
    )
    initial: dict[str, Any] = {
        "schema_version": 1,
        "turn_id": turn_id,
        "business_turn_seq": business_sequence,
        "input_event_id": input_event_id,
        "input_payload_sha256": input_event["payload_sha256"],
        "role_id": role_id,
        "executor": "worker",
        "phase": "starting",
        "outcome": None,
        "session_generation": generation,
        "launch_profile": role.launch_profile,
        "launch_profile_sha256": role.launch_profile_sha256,
        "launch_nonce": None,
        "supervisor_pid": None,
        "supervisor_start_id": None,
        "runner_pid": None,
        "runner_pgid": None,
        "runner_start_id": None,
        "agent_execution_started": False,
        "group_quiescent": None,
        "workspace_facts_before_sha256": before_hash,
        "workspace_facts_after_sha256": None,
        "process_exit_code": None,
        "adapter_completed": False,
        "permission_required": False,
        "observed_session_ref": None,
        "termination_kind": None,
        "terminal_event_id": None,
        "origin_claim_id": None,
        "created_at": created_at,
        "updated_at": rfc3339(),
    }

    worker_path = run_dir / "roles" / f"{role_id}.json"
    if not path_entry_exists(worker_path):
        return None
    worker = validate_role_snapshot(read_json(worker_path), role_id)
    worker_state = process_identity_state(
        worker["worker_pid"],
        worker["worker_start_id"],
    )
    worker_stopped = worker_state in {"gone", "reused"}
    if worker_state == "match" and worker["worker_pid"] != os.getpid():
        worker_stopped = terminate_verified_process(
            pid=worker["worker_pid"],
            start_id=worker["worker_start_id"],
        )
    if not worker_stopped:
        return None

    process_dir = turn_dir / "process"
    entries = (
        {path.name for path in committed_directory_entries(process_dir)}
        if path_entry_exists(process_dir)
        else set()
    )
    if not entries:
        raw_process_evidence = (
            raw_runtime.get("launch_nonce") is not None
            or any(
                raw_runtime.get(field) is not None
                for field in {
                    "supervisor_pid",
                    "supervisor_start_id",
                    "runner_pid",
                    "runner_pgid",
                    "runner_start_id",
                    "observed_session_ref",
                    "process_exit_code",
                    "termination_kind",
                }
            )
            or any(
                raw_runtime.get(field) is True
                for field in {
                    "agent_execution_started",
                    "adapter_completed",
                    "permission_required",
                }
            )
            or raw_runtime.get("group_quiescent") is False
            or raw_runtime.get("phase")
            in {"running", "exited", "recovery_required"}
        )
        if raw_process_evidence:
            # Missing process artifacts cannot erase already-persisted evidence
            # that a Supervisor or Runner may have existed.
            return None
    final_process: dict[str, Any] = {
        "supervisor_pid": None,
        "supervisor_start_id": None,
        "runner_pid": None,
        "runner_pgid": None,
        "runner_start_id": None,
        "agent_execution_started": False,
        "adapter_completed": False,
        "permission_required": False,
        "observed_session_ref": None,
        "process_exit_code": None,
        "termination_kind": "unknown",
        "group_quiescent": True,
    }
    process_safely_stopped = True
    if entries:
        supervisor_path = process_dir / "supervisor.json"
        if not path_entry_exists(supervisor_path):
            return None
        supervisor = validate_supervisor(read_json(supervisor_path))
        if supervisor["turn_id"] != turn_id:
            return None
        nonce = supervisor["launch_nonce"]
        raw_nonce = raw_runtime.get("launch_nonce")
        if raw_nonce is not None and raw_nonce != nonce:
            return None
        initial.update(
            {
                "launch_nonce": nonce,
                "supervisor_pid": supervisor["supervisor_pid"],
                "supervisor_start_id": supervisor["supervisor_start_id"],
                "runner_pid": supervisor["runner_pid"],
                "runner_pgid": supervisor["runner_pgid"],
                "runner_start_id": supervisor["runner_start_id"],
            }
        )
        validate_runtime(initial, team=team)
        _load_launch_spec_for_runtime(
            run_dir,
            runtime=initial,
            role=role,
        )
        runner, _authorization = _validate_external_process_chain(
            run_dir,
            runtime=initial,
            role=role,
            supervisor=supervisor,
        )
        group_quiescent = runner is None or not process_group_exists(
            runner["runner_pgid"]
        )
        if not group_quiescent:
            runner_state = process_identity_state(
                runner["runner_pid"],
                runner["runner_start_id"],
                pgid=runner["runner_pgid"],
            )
            if runner_state == "match":
                group_quiescent = terminate_verified_group(
                    runner_pid=runner["runner_pid"],
                    runner_pgid=runner["runner_pgid"],
                    runner_start_id=runner["runner_start_id"],
                )
        supervisor_state = process_identity_state(
            supervisor["supervisor_pid"],
            supervisor["supervisor_start_id"],
        )
        supervisor_stopped = supervisor_state in {"gone", "reused"}
        if (
            supervisor_state == "match"
            and supervisor["supervisor_pid"] != os.getpid()
            and group_quiescent
        ):
            supervisor_stopped = terminate_verified_process(
                pid=supervisor["supervisor_pid"],
                start_id=supervisor["supervisor_start_id"],
            )
        process_safely_stopped = group_quiescent and supervisor_stopped
        final_process = {
            field: supervisor[field]
            for field in {
                "supervisor_pid",
                "supervisor_start_id",
                "runner_pid",
                "runner_pgid",
                "runner_start_id",
                "agent_execution_started",
                "adapter_completed",
                "permission_required",
                "observed_session_ref",
                "process_exit_code",
            }
        }
        final_process["termination_kind"] = (
            supervisor["termination_kind"] or "unknown"
        )
        final_process["group_quiescent"] = group_quiescent

    after_path = turn_dir / "workspace-facts-after.json"
    if path_entry_exists(after_path):
        try:
            after = load_workspace_facts(
                after_path,
                expected_turn_id=turn_id,
                expected_boundary="after",
            )
            if after["workspace_realpath"] != str(team.workspace):
                raise IntegrityError("After Facts workspace mismatch")
            initial["workspace_facts_after_sha256"] = sha256_bytes(
                read_regular(after_path)
            )
        except (IntegrityError, OSError):
            # The Recovery Event explicitly acknowledges Turn-local Facts damage.
            initial["workspace_facts_after_sha256"] = None

    validate_runtime(initial, team=team)
    runtime_values = [*valid_runtimes, initial]
    projection = scan_journal(run_dir, _runtime_values=runtime_values)
    if projection.status == "RUNNING":
        if (
            projection.current_role != role_id
            or projection.tail is None
            or projection.tail["event_id"] != input_event_id
        ):
            return None
    elif not (
        projection.status == "BLOCKED"
        and projection.tail is not None
        and projection.tail["event_type"] == "block"
        and projection.tail.get("block_reason") == "recovery"
        and projection.tail["turn_id"] == turn_id
    ):
        return None
    owner = read_owner(team.workspace)
    if owner is None or owner["run_id"] != team.run_id:
        return None

    event = commit_technical_block_locked(
        run_dir,
        runtime=initial,
        reason="recovery",
        message=(
            "The uniquely identified active Turn Runtime was damaged. Journal, "
            "frozen input, Before Facts, role binding, and process snapshots were "
            f"used only to stop execution safely and preserve a Recovery Block. "
            f"Original validation failure: {damage}"
        ),
        runtime_values=runtime_values,
    )
    final = {
        **initial,
        **final_process,
        "phase": (
            "finalized" if process_safely_stopped else "recovery_required"
        ),
        "outcome": "failed",
        "terminal_event_id": event["event_id"],
        "updated_at": rfc3339(),
    }
    validate_runtime(final, team=team)
    replace_damaged_runtime(turn_dir, final, team=team)
    projection = scan_journal(run_dir)
    actions = [f"runtime-recovery-block:{turn_id}:{event['event_id']}"]
    tmux = (
        ensure_workers(run_dir, team)
        if final["phase"] == "finalized"
        else None
    )
    return {
        "run_id": team.run_id,
        "status": projection.status,
        "actions": actions,
        "owner_released": False,
        "tmux": tmux,
    }


def _recover_run_strict(run_dir: Path) -> dict[str, Any]:
    with locked_run(run_dir, exclusive=True):
        projection = scan_journal(run_dir)
        if projection.status == "UNSTARTED":
            raise AgentTeamError("RUN_UNSTARTED", "use start, not recover")
        owner = read_owner(projection.team.workspace)
        if projection.status in {"RUNNING", "BLOCKED"} and (
            owner is None or owner["run_id"] != projection.team.run_id
        ):
            raise IntegrityError("cannot recover a started run without exact ownership")
        actions: list[str] = []
        for runtime in iter_runtimes(run_dir, team=projection.team):
            if runtime["executor"] != "worker" or runtime["phase"] == "finalized":
                continue
            event = finalize_external_turn_locked(run_dir, runtime)
            if event:
                actions.append(f"finalized:{runtime['turn_id']}:{event['event_id']}")
            elif runtime["phase"] == "starting":
                process_dir = run_dir / "turns" / runtime["turn_id"] / "process"
                role_path = run_dir / "roles" / f"{runtime['role_id']}.json"
                if not path_entry_exists(role_path):
                    raise IntegrityError(
                        "claimed External Turn has no Worker identity snapshot"
                    )
                worker = validate_role_snapshot(
                    read_json(role_path),
                    runtime["role_id"],
                )
                worker_state = process_identity_state(
                    worker["worker_pid"],
                    worker["worker_start_id"],
                )
                has_process_identity = any(
                    path_entry_exists(process_dir / name)
                    for name in {
                        "supervisor.json",
                        "runner.json",
                        "launch-authorized.json",
                    }
                )
                if worker_state in {"gone", "reused"} and not has_process_identity:
                    event = commit_technical_block_locked(
                        run_dir,
                        runtime=runtime,
                        reason="start_failure",
                        message="Worker stopped before Supervisor/Runner launch.",
                    )
                    runtime.update(
                        {
                            "phase": "finalized",
                            "outcome": "failed",
                            "group_quiescent": True,
                            "termination_kind": "unknown",
                            "terminal_event_id": event["event_id"],
                        }
                    )
                    save_runtime(
                        run_dir / "turns" / runtime["turn_id"],
                        runtime,
                        team=projection.team,
                    )
                    actions.append(f"start-failure:{runtime['turn_id']}")
        projection = scan_journal(run_dir)
        if projection.status in {"COMPLETED", "CANCELLED"}:
            owner = read_owner(projection.team.workspace)
            released = False
            if owner is not None and owner["run_id"] == projection.team.run_id:
                released = release_terminal_owner_locked(run_dir)
            return {
                "run_id": projection.team.run_id,
                "status": projection.status,
                "actions": actions,
                "owner_released": released,
                "tmux": None,
            }
        tmux = ensure_workers(run_dir, projection.team)
    return {
        "run_id": projection.team.run_id,
        "status": projection.status,
        "actions": actions,
        "owner_released": False,
        "tmux": tmux,
    }


def recover_run(run_dir: Path) -> dict[str, Any]:
    try:
        return _recover_run_strict(run_dir)
    except (IntegrityError, OSError) as original_error:
        exc = (
            original_error
            if isinstance(original_error, IntegrityError)
            else IntegrityError(
                f"recovery could not read durable state: {original_error}"
            )
        )
        repair_error: IntegrityError | None = None
        try:
            with locked_run(run_dir, exclusive=True):
                repaired = _recover_unique_damaged_runtime_locked(run_dir)
                if repaired is not None:
                    return repaired
        except (IntegrityError, OSError) as candidate_error:
            repair_error = (
                candidate_error
                if isinstance(candidate_error, IntegrityError)
                else IntegrityError(
                    "Runtime repair could not read durable state: "
                    f"{candidate_error}"
                )
            )
        with locked_run(
            run_dir,
            exclusive=True,
            verify_team_context=False,
        ):
            actions, unresolved = _cleanup_corrupted_processes_locked(run_dir)
        cleanup = (
            f" verified cleanup actions={actions or ['none']};"
            f" unresolved={unresolved or ['none']}"
        )
        raise IntegrityError(
            f"{(repair_error or exc).message};{cleanup}",
            *((repair_error or exc).evidence_paths),
        ) from original_error


def _unlock_supervisor_identity(
    value: dict[str, Any],
    *,
    subject: str,
    require_fields: bool,
) -> tuple[int, str] | None:
    fields = ("supervisor_pid", "supervisor_start_id")
    present = tuple(field in value for field in fields)
    if not any(present):
        if require_fields:
            raise IntegrityError(f"{subject} lacks Supervisor identity fields")
        return None
    if not all(present):
        raise IntegrityError(f"{subject} has partial Supervisor identity fields")
    pid, start_id = (value[field] for field in fields)
    if pid is None and start_id is None:
        return None
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or not isinstance(start_id, str)
        or not start_id
    ):
        raise IntegrityError(f"{subject} has an invalid Supervisor identity")
    return pid, start_id


def _unlock_runner_identity(
    value: dict[str, Any],
    *,
    subject: str,
    require_fields: bool,
) -> tuple[int, int, str] | None:
    fields = ("runner_pid", "runner_pgid", "runner_start_id")
    present = tuple(field in value for field in fields)
    if not any(present):
        if require_fields:
            raise IntegrityError(f"{subject} lacks Runner identity fields")
        return None
    if not all(present):
        raise IntegrityError(f"{subject} has partial Runner identity fields")
    pid, pgid, start_id = (value[field] for field in fields)
    if pid is None and pgid is None and start_id is None:
        return None
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or isinstance(pgid, bool)
        or not isinstance(pgid, int)
        or pgid != pid
        or not isinstance(start_id, str)
        or not start_id
    ):
        raise IntegrityError(f"{subject} has an invalid Runner identity")
    return pid, pgid, start_id


def _assert_unlock_identity_stopped(
    *,
    subject: str,
    identity: tuple[int, str],
) -> None:
    state = process_identity_state(*identity)
    if state == "match":
        raise AgentTeamError("LIVE_PROCESS", f"{subject} is still alive")
    if state not in {"gone", "reused"}:
        raise AgentTeamError(
            "PROCESS_IDENTITY_UNKNOWN",
            f"{subject} identity is {state}",
        )


def _assert_unlock_group_stopped(
    *,
    turn_id: str,
    identity: tuple[int, int, str],
) -> None:
    runner_pid, runner_pgid, runner_start_id = identity
    group_exists = process_group_exists(runner_pgid)
    runner_state = process_identity_state(
        runner_pid,
        runner_start_id,
        pgid=runner_pgid,
    )
    if group_exists:
        if runner_state == "match":
            raise AgentTeamError(
                "LIVE_PROCESS_GROUP",
                f"runner PGID is still alive for {turn_id}",
            )
        raise AgentTeamError(
            "PROCESS_IDENTITY_UNKNOWN",
            f"runner group exists but leader identity is {runner_state} "
            f"for {turn_id}",
        )
    if runner_state not in {"gone", "reused"}:
        raise AgentTeamError(
            "PROCESS_IDENTITY_UNKNOWN",
            f"runner identity is {runner_state} after its group disappeared "
            f"for {turn_id}",
        )


def _assert_unlock_turn_stopped(
    turn_dir: Path,
    *,
    team: Any,
    confirm_origin_stopped: bool,
) -> None:
    turn_id = turn_dir.name
    runtime_path = turn_dir / "runtime.json"
    raw_runtime: dict[str, Any] | None = None
    runtime_valid = False
    runtime_read_error: Exception | None = None
    if path_entry_exists(runtime_path):
        try:
            raw_runtime = read_json(runtime_path)
            validate_runtime(raw_runtime, team=team)
            if raw_runtime["turn_id"] != turn_id:
                raise IntegrityError("Runtime turn does not match its directory")
            runtime_valid = True
        except (IntegrityError, OSError) as exc:
            runtime_read_error = exc

    process_dir = turn_dir / "process"
    entries: set[str] = set()
    if path_entry_exists(process_dir):
        entries = {
            path.name for path in committed_directory_entries(process_dir)
        }

    supervisor_identities: set[tuple[int, str]] = set()
    runner_identities: set[tuple[int, int, str]] = set()
    runtime_process_fields_known = False
    if raw_runtime is not None:
        process_fields = {
            "supervisor_pid",
            "supervisor_start_id",
            "runner_pid",
            "runner_pgid",
            "runner_start_id",
        }
        runtime_process_fields_known = process_fields <= set(raw_runtime)
        if runtime_process_fields_known:
            supervisor_identity = _unlock_supervisor_identity(
                raw_runtime,
                subject=f"Runtime {turn_id}",
                require_fields=True,
            )
            runner_identity = _unlock_runner_identity(
                raw_runtime,
                subject=f"Runtime {turn_id}",
                require_fields=True,
            )
            if supervisor_identity is not None:
                supervisor_identities.add(supervisor_identity)
            if runner_identity is not None:
                runner_identities.add(runner_identity)
        elif process_fields & set(raw_runtime):
            raise IntegrityError(
                f"Runtime {turn_id} has incomplete process identity fields"
            )

    if "supervisor.json" in entries:
        supervisor = validate_supervisor(
            read_json(process_dir / "supervisor.json")
        )
        if supervisor["turn_id"] != turn_id:
            raise IntegrityError("Supervisor turn does not match its directory")
        supervisor_identities.add(
            (
                supervisor["supervisor_pid"],
                supervisor["supervisor_start_id"],
            )
        )
        runner_identity = _unlock_runner_identity(
            supervisor,
            subject=f"Supervisor {turn_id}",
            require_fields=True,
        )
        if runner_identity is not None:
            runner_identities.add(runner_identity)

    if "runner.json" in entries:
        runner_value = read_json(process_dir / "runner.json")
        nonce = runner_value.get("launch_nonce")
        if not isinstance(nonce, str) or not nonce:
            raise IntegrityError(f"Runner {turn_id} has an invalid launch nonce")
        runner = validate_runner(
            runner_value,
            turn_id=turn_id,
            nonce=nonce,
        )
        runner_identity = _unlock_runner_identity(
            runner,
            subject=f"Runner {turn_id}",
            require_fields=True,
        )
        assert runner_identity is not None
        runner_identities.add(runner_identity)

    if "launch-authorized.json" in entries:
        authorization_value = read_json(
            process_dir / "launch-authorized.json"
        )
        nonce = authorization_value.get("launch_nonce")
        if not isinstance(nonce, str) or not nonce:
            raise IntegrityError(
                f"authorization {turn_id} has an invalid launch nonce"
            )
        authorization = validate_authorization(
            authorization_value,
            turn_id=turn_id,
            nonce=nonce,
        )
        supervisor_identity = _unlock_supervisor_identity(
            authorization,
            subject=f"authorization {turn_id}",
            require_fields=True,
        )
        runner_identity = _unlock_runner_identity(
            authorization,
            subject=f"authorization {turn_id}",
            require_fields=True,
        )
        assert supervisor_identity is not None
        assert runner_identity is not None
        supervisor_identities.add(supervisor_identity)
        runner_identities.add(runner_identity)

    external_evidence = bool(
        supervisor_identities
        or runner_identities
        or (
            raw_runtime is not None
            and raw_runtime.get("executor") == "worker"
            and runtime_process_fields_known
        )
    )
    if not runtime_valid:
        if not external_evidence:
            origin_evidence = bool(
                raw_runtime is not None
                and raw_runtime.get("executor") == "origin"
            )
            if (
                not origin_evidence
                and any(
                    role.binding == "external"
                    for role in team.roles.values()
                )
            ):
                detail = (
                    f": {runtime_read_error}"
                    if runtime_read_error is not None
                    else ""
                )
                raise AgentTeamError(
                    "PROCESS_IDENTITY_UNKNOWN",
                    f"cannot determine managed process identities for {turn_id}{detail}",
                )
            if not confirm_origin_stopped:
                raise AgentTeamError(
                    "ORIGIN_STOP_CONFIRMATION_REQUIRED",
                    "damaged Origin turn requires --confirm-origin-stopped",
                )
        elif (
            raw_runtime is not None
            and (
                not isinstance(raw_runtime.get("executor"), str)
                or raw_runtime.get("executor") not in {"worker", "origin"}
            )
            and not supervisor_identities
            and not confirm_origin_stopped
        ):
            raise AgentTeamError(
                "ORIGIN_STOP_CONFIRMATION_REQUIRED",
                "ambiguous damaged turn requires --confirm-origin-stopped",
            )
    elif (
        raw_runtime is not None
        and raw_runtime["executor"] == "origin"
        and raw_runtime["phase"] != "finalized"
        and not confirm_origin_stopped
    ):
        raise AgentTeamError(
            "ORIGIN_STOP_CONFIRMATION_REQUIRED",
            "unfinalized Origin turn requires --confirm-origin-stopped",
        )

    if runner_identities and not supervisor_identities:
        raise AgentTeamError(
            "PROCESS_IDENTITY_UNKNOWN",
            f"Runner identities for {turn_id} lack any Supervisor identity",
        )
    for index, identity in enumerate(sorted(supervisor_identities), start=1):
        _assert_unlock_identity_stopped(
            subject=f"supervisor {turn_id} identity {index}",
            identity=identity,
        )
    for identity in sorted(runner_identities):
        _assert_unlock_group_stopped(
            turn_id=turn_id,
            identity=identity,
        )


def unlock_workspace(
    workspace: Path,
    *,
    expect_run: str,
    confirm_origin_stopped: bool,
) -> dict[str, Any]:
    with workspace_lock(workspace, exclusive=True):
        owner = read_owner(workspace)
        if owner is None:
            raise AgentTeamError("OWNER_NOT_FOUND", "workspace has no owner")
        if owner["run_id"] != expect_run:
            raise AgentTeamError(
                "OWNER_MISMATCH",
                f"workspace is owned by {owner['run_id']}, not {expect_run}",
            )
        run_dir = get_run_dir(workspace, expect_run)
        # Lock order is workspace then run; acquire only the already-held run lock here.
        from .state import file_lock

        with file_lock(run_dir / "journal.lock", exclusive=True):
            team = parse_team(read_json(run_dir / "team.json"))
            if team.run_id != expect_run or team.workspace.resolve(
                strict=True
            ) != workspace.resolve(strict=True):
                raise IntegrityError("unlock target does not match team.json")
            external_roles = [
                role
                for role in team.roles.values()
                if role.binding == "external"
            ]
            role_entries = committed_directory_entries(run_dir / "roles")
            for role_path in role_entries:
                role_id = role_path.stem
                if (
                    role_path.suffix != ".json"
                    or not ROLE_ID_RE.fullmatch(role_id)
                    or role_path.is_symlink()
                    or not role_path.is_file()
                ):
                    raise IntegrityError(
                        f"invalid Worker role snapshot entry: {role_path.name}"
                    )
                worker = validate_role_snapshot(
                    read_json(role_path),
                    role_id,
                )
                _assert_unlock_identity_stopped(
                    subject=f"worker for role {role_id}",
                    identity=(
                        worker["worker_pid"],
                        worker["worker_start_id"],
                    ),
                )
            if (external_roles or role_entries) and has_session(expect_run):
                raise AgentTeamError(
                    "LIVE_TMUX_RUNTIME",
                    "the run's tmux session still exists",
                )
            for turn_dir in iter_turn_directories(run_dir):
                _assert_unlock_turn_stopped(
                    turn_dir,
                    team=team,
                    confirm_origin_stopped=confirm_origin_stopped,
                )
            release_owner(workspace, expect_run)
    return {
        "code": "WORKSPACE_UNLOCKED",
        "workspace": str(workspace),
        "run_id": expect_run,
        "recoverable": False,
    }

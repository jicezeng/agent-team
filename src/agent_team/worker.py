from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .adapters.base import (
    AdapterEvidenceSnapshot,
    LaunchSpec,
    ProcessResult,
    TurnLaunchContext,
)
from .assets import effective_agent_team_cli
from .config import Role, load_team
from .errors import (
    AgentTeamError,
    IntegrityError,
    RecoverableTurnArtifactError,
)
from .journal import can_create_business_turn, scan_journal
from .ownership import release_terminal_owner_locked
from .processes import (
    current_identity,
    identity_matches,
    process_group_exists,
    process_identity_state,
    terminate_verified_group,
)
from .runtime_log import WorkerLogger
from .state import locked_run, read_owner
from .supervisor import (
    _save_snapshot,
    validate_authorization,
    validate_exec_error,
    validate_runner,
    validate_supervisor,
)
from .tmux_runtime import ensure_workers, session_name, signal_change
from .trace import finalize_turn_trace, validate_trace_manifest
from .turns import (
    commit_session,
    commit_technical_block_locked,
    create_business_turn_locked,
    deliver_outbox_locked,
    finalize_deadline_before_claim_locked,
    is_deadline_before_claim_pending,
    iter_runtimes,
    load_runtime,
    load_session,
    record_candidate_activation_failure_session,
    render_turn_prompt,
    runtime_for_input,
    save_runtime,
    session_launch_state,
)
from .util import (
    atomic_json,
    atomic_write,
    committed_directory_entries,
    path_entry_exists,
    random_token,
    read_json,
    read_regular,
    require_schema_version,
    rfc3339,
    set_private_umask,
)

ROLE_REQUIRED = {
    "schema_version",
    "role_id",
    "worker_pid",
    "worker_start_id",
    "tmux_session",
    "tmux_pane_id",
    "updated_at",
}


def _candidate_activation_return_role(
    run_dir: Path,
    *,
    runtime: dict[str, Any],
) -> str | None:
    projection = scan_journal(run_dir)
    inputs = [
        event
        for event in projection.events
        if event["event_id"] == runtime["input_event_id"]
    ]
    if len(inputs) != 1:
        raise IntegrityError("candidate activation Turn input is not unique")
    source = inputs[0]
    if (
        source["event_type"] != "handoff"
        or source["to_role"] != runtime["role_id"]
        or source["from_role"] not in projection.team.roles
        or source["from_role"] == runtime["role_id"]
    ):
        return None
    return source["from_role"]


def _cli_path() -> str:
    return str(effective_agent_team_cli())


def _write_role_snapshot(run_dir: Path, role_id: str) -> dict[str, Any]:
    identity = current_identity()
    value = {
        "schema_version": 1,
        "role_id": role_id,
        "worker_pid": identity.pid,
        "worker_start_id": identity.start_id,
        "tmux_session": session_name(load_team(run_dir).run_id),
        "tmux_pane_id": os.environ.get("TMUX_PANE"),
        "updated_at": rfc3339(),
    }
    validate_role_snapshot(value, role_id)
    path = run_dir / "roles" / f"{role_id}.json"
    if path_entry_exists(path):
        previous = validate_role_snapshot(read_json(path), role_id)
        previous_state = process_identity_state(
            previous["worker_pid"],
            previous["worker_start_id"],
        )
        if previous_state == "match" and (
            previous["worker_pid"] != identity.pid
            or previous["worker_start_id"] != identity.start_id
        ):
            raise AgentTeamError(
                "LIVE_WORKER_ALREADY_EXISTS",
                f"role {role_id} already has a live Worker",
            )
        if previous_state == "match" and (
            previous["tmux_session"] != value["tmux_session"]
            or previous["tmux_pane_id"] != value["tmux_pane_id"]
        ):
            raise IntegrityError(
                f"parent-observed tmux identity changed for role {role_id}"
            )
        if previous_state not in {"match", "gone", "reused"}:
            raise AgentTeamError(
                "PROCESS_IDENTITY_UNKNOWN",
                f"existing Worker identity is {previous_state} for role {role_id}",
            )
    atomic_json(path, value)
    return value


def validate_role_snapshot(value: dict[str, Any], role_id: str) -> dict[str, Any]:
    if set(value) != ROLE_REQUIRED:
        raise IntegrityError("role worker snapshot has invalid fields")
    require_schema_version(value, 1, subject="role worker snapshot")
    if (
        value["role_id"] != role_id
        or isinstance(value["worker_pid"], bool)
        or not isinstance(value["worker_pid"], int)
        or value["worker_pid"] <= 0
        or not isinstance(value["worker_start_id"], str)
        or not value["worker_start_id"]
    ):
        raise IntegrityError("role worker identity is invalid")
    if not isinstance(value["tmux_session"], str) or not value["tmux_session"]:
        raise IntegrityError("role worker tmux session is invalid")
    if not isinstance(value["tmux_pane_id"], str) or not value["tmux_pane_id"]:
        raise IntegrityError("role worker pane id is invalid")
    from .util import parse_rfc3339

    parse_rfc3339(value["updated_at"])
    return value


def _terminal_outcome(terminal: dict[str, Any]) -> str:
    event_type = terminal["event_type"]
    if event_type in {"handoff", "complete"}:
        return "success"
    if event_type == "cancel":
        return "cancelled"
    if terminal.get("block_reason") == "limit":
        return "cancelled" if terminal.get("limit_reason") == "deadline" else "stalled"
    if terminal.get("block_reason") == "agent":
        return "success"
    if terminal.get("block_reason") == "no_action":
        return "stalled"
    return "failed"


def _anchor_turn_trace(
    run_dir: Path,
    runtime: dict[str, Any],
) -> dict[str, Any] | None:
    if (
        runtime["executor"] != "worker"
        or runtime["launch_nonce"] is None
        or runtime["group_quiescent"] is not True
    ):
        return None
    team = load_team(run_dir)
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    role = team.roles[runtime["role_id"]]
    expected = runtime["trace_manifest_sha256"]
    if expected is not None:
        return validate_trace_manifest(
            turn_dir,
            expected_sha256=expected,
            expected_run_id=team.run_id,
            expected_role_id=runtime["role_id"],
            expected_adapter_id=role.adapter or "",
            expected_policy=team.observability,
        )
    manifest, manifest_hash = finalize_turn_trace(
        run_id=team.run_id,
        turn_dir=turn_dir,
        role_id=runtime["role_id"],
        adapter_id=role.adapter or "",
        policy=team.observability,
    )
    runtime["trace_manifest_sha256"] = manifest_hash
    return manifest


def _finalize_existing_terminal(
    run_dir: Path,
    runtime: dict[str, Any],
    terminal: dict[str, Any],
) -> None:
    team = load_team(run_dir)
    _anchor_turn_trace(run_dir, runtime)
    runtime["terminal_event_id"] = terminal["event_id"]
    runtime["outcome"] = _terminal_outcome(terminal)
    if runtime["group_quiescent"] is False:
        runtime["phase"] = "recovery_required"
    else:
        runtime["phase"] = "finalized"
    save_runtime(
        run_dir / "turns" / runtime["turn_id"],
        runtime,
        team=team,
    )


def _copy_supervisor_result(
    runtime: dict[str, Any],
    supervisor: dict[str, Any],
) -> None:
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
        "termination_kind",
        "group_quiescent",
    }:
        runtime[field] = supervisor[field]
    runtime["phase"] = "exited"


def _load_launch_spec_for_runtime(
    run_dir: Path,
    *,
    runtime: dict[str, Any],
    role: Role,
) -> LaunchSpec:
    turn_id = runtime["turn_id"]
    nonce = runtime["launch_nonce"]
    if not isinstance(nonce, str) or not nonce:
        raise IntegrityError("process artifacts require a Runtime launch nonce")
    launch_path = run_dir / "turns" / turn_id / "process" / "launch.json"
    try:
        launch = LaunchSpec.from_json(read_json(launch_path))
    except FileNotFoundError as exc:
        raise IntegrityError(
            "Runtime launch nonce references a missing LaunchSpec",
            f"turns/{turn_id}/process/launch.json",
        ) from exc
    team = load_team(run_dir)
    expected_env = {
        "AGENT_TEAM_RUN_ID": run_dir.name,
        "AGENT_TEAM_ROLE_ID": role.role_id,
        "AGENT_TEAM_TURN_ID": turn_id,
        "AGENT_TEAM_RUN_DIR": str(run_dir),
        "AGENT_TEAM_TURN_DIR": str(run_dir / "turns" / turn_id),
    }
    if (
        launch.adapter_id != role.adapter
        or launch.cwd != str(team.workspace)
        or launch.launch_mode != (role.launch_mode or "headless")
        or launch.launch_profile != runtime["launch_profile"]
        or launch.launch_profile_sha256 != runtime["launch_profile_sha256"]
        or any(launch.env.get(key) != item for key, item in expected_env.items())
        or not launch.env.get("AGENT_TEAM_CLI")
        or not Path(launch.argv[0]).is_absolute()
        or not Path(launch.env["AGENT_TEAM_CLI"]).is_absolute()
    ):
        raise IntegrityError(
            "LaunchSpec does not match the immutable Runtime context",
            f"turns/{turn_id}/process/launch.json",
        )
    if launch.launch_mode == "interactive":
        expected_prompt = (
            run_dir
            / "turns"
            / turn_id
            / "process"
            / "prompt.md"
        )
        if (
            launch.prompt_file != str(expected_prompt)
            or read_regular(expected_prompt) != launch.stdin.encode("utf-8")
        ):
            raise IntegrityError(
                "interactive prompt does not match the immutable LaunchSpec",
                f"turns/{turn_id}/process/prompt.md",
            )
    return launch


def _validate_external_process_chain(
    run_dir: Path,
    *,
    runtime: dict[str, Any],
    role: Role,
    supervisor: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    turn_id = runtime["turn_id"]
    nonce = runtime["launch_nonce"]
    if supervisor["turn_id"] != turn_id or supervisor["launch_nonce"] != nonce:
        raise IntegrityError(
            "Supervisor context does not match the Runtime",
            f"turns/{turn_id}/process/supervisor.json",
        )
    _load_launch_spec_for_runtime(run_dir, runtime=runtime, role=role)
    process_dir = run_dir / "turns" / turn_id / "process"
    runner_path = process_dir / "runner.json"
    authorization_path = process_dir / "launch-authorized.json"
    runner = None
    if path_entry_exists(runner_path):
        runner = validate_runner(
            read_json(runner_path),
            turn_id=turn_id,
            nonce=nonce,
        )
    if supervisor["runner_pid"] is not None:
        if runner is None:
            raise IntegrityError(
                "Supervisor-referenced Runner identity is missing",
                f"turns/{turn_id}/process/runner.json",
            )
        if (
            supervisor["runner_pid"] != runner["runner_pid"]
            or supervisor["runner_pgid"] != runner["runner_pgid"]
            or supervisor["runner_start_id"] != runner["runner_start_id"]
        ):
            raise IntegrityError(
                "Runner identity conflicts with the Supervisor snapshot"
            )
    elif supervisor["state"] == "finished" and runner is not None:
        raise IntegrityError(
            "finished Supervisor reports Runner creation failure but Runner exists"
        )
    if runtime["supervisor_pid"] is not None and (
        runtime["supervisor_pid"] != supervisor["supervisor_pid"]
        or runtime["supervisor_start_id"] != supervisor["supervisor_start_id"]
    ):
        raise IntegrityError("Runtime/Supervisor identity mismatch")
    if runtime["runner_pid"] is not None:
        if runner is None or (
            runtime["runner_pid"] != runner["runner_pid"]
            or runtime["runner_pgid"] != runner["runner_pgid"]
            or runtime["runner_start_id"] != runner["runner_start_id"]
        ):
            raise IntegrityError("Runtime/Runner identity mismatch")
    authorization = None
    if path_entry_exists(authorization_path):
        if runner is None or supervisor["runner_pid"] is None:
            raise IntegrityError(
                "launch authorization exists without complete process identities"
            )
        authorization = validate_authorization(
            read_json(authorization_path),
            turn_id=turn_id,
            nonce=nonce,
        )
        if (
            authorization["supervisor_pid"] != supervisor["supervisor_pid"]
            or authorization["supervisor_start_id"] != supervisor["supervisor_start_id"]
            or authorization["runner_pid"] != runner["runner_pid"]
            or authorization["runner_pgid"] != runner["runner_pgid"]
            or authorization["runner_start_id"] != runner["runner_start_id"]
            or authorization["launch_profile"] != runtime["launch_profile"]
            or authorization["launch_profile_sha256"]
            != runtime["launch_profile_sha256"]
            or runtime["supervisor_pid"] != supervisor["supervisor_pid"]
            or runtime["runner_pid"] != runner["runner_pid"]
        ):
            raise IntegrityError(
                "launch authorization conflicts with Runtime process identities"
            )
    elif (
        runtime["phase"] == "running"
        or runtime["agent_execution_started"]
        or runtime["permission_required"]
        or runtime["observed_session_ref"] is not None
        or supervisor["agent_execution_started"]
        or supervisor["adapter_completed"]
        or supervisor["permission_required"]
        or supervisor["observed_session_ref"] is not None
    ):
        raise IntegrityError(
            "execution evidence exists without the unique launch authorization",
            f"turns/{turn_id}/process/launch-authorized.json",
        )
    return runner, authorization


def _session_was_made_unavailable_by_turn(
    run_dir: Path,
    *,
    role: Role,
    runtime: dict[str, Any],
) -> bool:
    session = load_session(run_dir, role)
    return bool(
        session
        and session["status"] == "unavailable"
        and session["generation"] == runtime["session_generation"]
        and session["updated_turn_id"] == runtime["turn_id"]
    )


def _finalize_adapter_run_state(
    run_dir: Path,
    *,
    role: Role,
) -> Any:
    adapter = get_adapter(role.adapter or "")
    adapter.finalize_run_state(
        run_dir=run_dir,
        role_id=role.role_id,
        launch_mode=role.launch_mode or "headless",
    )
    return adapter


def finalize_external_turn_locked(
    run_dir: Path,
    runtime: dict[str, Any],
    *,
    allow_after_capture: bool = False,
) -> dict[str, Any] | None:
    team = load_team(run_dir)
    role = team.roles[runtime["role_id"]]
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    if is_deadline_before_claim_pending(runtime):
        return finalize_deadline_before_claim_locked(run_dir, runtime)
    supervisor_path = turn_dir / "process" / "supervisor.json"
    if not path_entry_exists(supervisor_path):
        projection = scan_journal(run_dir)
        terminal = projection.terminal_for_turn(runtime["turn_id"])
        process_dir = turn_dir / "process"
        if runtime["launch_nonce"] is not None:
            launch_path = process_dir / "launch.json"
            if path_entry_exists(launch_path):
                _load_launch_spec_for_runtime(
                    run_dir,
                    runtime=runtime,
                    role=role,
                )
            elif path_entry_exists(process_dir) and committed_directory_entries(
                process_dir
            ):
                raise IntegrityError(
                    "Runtime launch nonce references a missing LaunchSpec "
                    "alongside other process artifacts"
                )
        elif path_entry_exists(process_dir) and committed_directory_entries(
            process_dir
        ):
            raise IntegrityError("process artifacts exist before a launch nonce")
        runner_or_auth = any(
            path_entry_exists(process_dir / name)
            for name in {"runner.json", "launch-authorized.json"}
        )
        if runner_or_auth or runtime["supervisor_pid"] is not None:
            raise IntegrityError(
                "process identity artifacts exist without a Supervisor snapshot"
            )
        if terminal is not None and not runner_or_auth:
            runtime.update(
                {
                    "agent_execution_started": False,
                    "adapter_completed": False,
                    "permission_required": False,
                    "group_quiescent": True,
                    "termination_kind": (
                        "cancelled"
                        if terminal["event_type"] == "cancel"
                        else (
                            "deadline"
                            if terminal.get("limit_reason") == "deadline"
                            else "unknown"
                        )
                    ),
                }
            )
            _finalize_existing_terminal(run_dir, runtime, terminal)
            return terminal
        return None
    supervisor = validate_supervisor(read_json(supervisor_path))
    runner, authorization = _validate_external_process_chain(
        run_dir,
        runtime=runtime,
        role=role,
        supervisor=supervisor,
    )
    supervisor_identity = process_identity_state(
        supervisor["supervisor_pid"],
        supervisor["supervisor_start_id"],
    )
    if supervisor["state"] == "finished":
        if supervisor_identity == "match":
            # The final snapshot precedes raw-stream fsync/close and Supervisor
            # process exit. Only the process exit makes it safe to deliver.
            return None
        if supervisor_identity not in {"gone", "reused"}:
            _copy_supervisor_result(runtime, supervisor)
            runtime["phase"] = "recovery_required"
            runtime["outcome"] = "failed"
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=(
                    "Supervisor committed a final snapshot, but its process "
                    f"identity is {supervisor_identity}; process exit cannot be "
                    "proven."
                ),
            )
            runtime["terminal_event_id"] = event["event_id"]
            save_runtime(turn_dir, runtime, team=team)
            return event
    if supervisor["state"] == "stopping" and supervisor_identity == "match":
        # A live Supervisor owns termination of the Runner group. Recovery may
        # observe this snapshot between its durable write and group shutdown;
        # wait instead of touching adapter state that the Runner may still use.
        return None
    if supervisor["state"] == "stopping" and supervisor_identity in {
        "gone",
        "reused",
    }:
        runner_pgid = supervisor["runner_pgid"]
        group_quiescent = runner_pgid is not None and not process_group_exists(
            runner_pgid
        )
        if (
            not group_quiescent
            and supervisor["runner_pid"] is not None
            and identity_matches(
                supervisor["runner_pid"],
                supervisor["runner_start_id"],
                pgid=runner_pgid,
            )
        ):
            group_quiescent = terminate_verified_group(
                runner_pid=supervisor["runner_pid"],
                runner_pgid=runner_pgid,
                runner_start_id=supervisor["runner_start_id"],
            )
        if (
            group_quiescent
            and supervisor["process_exit_code"] is not None
            and supervisor["termination_kind"] is not None
        ):
            supervisor["state"] = "finished"
            supervisor["group_quiescent"] = True
            _save_snapshot(run_dir, runtime["turn_id"], supervisor)

    if supervisor["state"] not in {"finished", "stopping"}:
        if supervisor_identity == "match":
            return None
        group_quiescent = runner is None or not process_group_exists(
            runner["runner_pgid"]
        )
        if (
            not group_quiescent
            and process_identity_state(
                runner["runner_pid"],
                runner["runner_start_id"],
                pgid=runner["runner_pgid"],
            )
            == "match"
        ):
            group_quiescent = terminate_verified_group(
                runner_pid=runner["runner_pid"],
                runner_pgid=runner["runner_pgid"],
                runner_start_id=runner["runner_start_id"],
            )
        safely_stopped = supervisor_identity in {"gone", "reused"} and group_quiescent
        if safely_stopped:
            _finalize_adapter_run_state(run_dir, role=role)
        session_unavailable = _session_was_made_unavailable_by_turn(
            run_dir,
            role=role,
            runtime=runtime,
        )
        if session_unavailable and supervisor["agent_execution_started"]:
            raise IntegrityError(
                "session-unavailable evidence conflicts with execution evidence"
            )
        runtime.update(
            {
                "supervisor_pid": supervisor["supervisor_pid"],
                "supervisor_start_id": supervisor["supervisor_start_id"],
                "runner_pid": runner["runner_pid"] if runner else None,
                "runner_pgid": runner["runner_pgid"] if runner else None,
                "runner_start_id": runner["runner_start_id"] if runner else None,
                "agent_execution_started": supervisor["agent_execution_started"],
                "adapter_completed": supervisor["adapter_completed"],
                "permission_required": supervisor["permission_required"],
                "observed_session_ref": supervisor["observed_session_ref"],
                "process_exit_code": supervisor["process_exit_code"],
                "termination_kind": supervisor["termination_kind"] or "unknown",
                "group_quiescent": True if safely_stopped else False,
            }
        )
        projection = scan_journal(run_dir)
        terminal = projection.terminal_for_turn(runtime["turn_id"])
        if terminal is not None:
            _finalize_existing_terminal(run_dir, runtime, terminal)
            return terminal
        runtime["phase"] = "finalized" if safely_stopped else "recovery_required"
        runtime["outcome"] = "failed"
        event = commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason=(
                "start_failure"
                if safely_stopped and (authorization is None or session_unavailable)
                else "recovery"
            ),
            message=(
                (
                    "Harness structurally reported that the requested Session was "
                    "unavailable before model execution began."
                )
                if session_unavailable and safely_stopped
                else (
                    "Supervisor disappeared before a trustworthy final process "
                    "snapshot was committed."
                )
            ),
        )
        runtime["terminal_event_id"] = event["event_id"]
        save_runtime(turn_dir, runtime, team=team)
        return event
    _copy_supervisor_result(runtime, supervisor)
    trace_manifest = _anchor_turn_trace(run_dir, runtime)
    session_unavailable = _session_was_made_unavailable_by_turn(
        run_dir,
        role=role,
        runtime=runtime,
    )
    if session_unavailable and runtime["agent_execution_started"]:
        raise IntegrityError(
            "session-unavailable evidence conflicts with execution evidence"
        )
    result = ProcessResult(
        process_exit_code=runtime["process_exit_code"],
        termination_kind=runtime["termination_kind"] or "unknown",
        group_quiescent=bool(runtime["group_quiescent"]),
        launch_mode=role.launch_mode or "headless",
    )
    evidence = AdapterEvidenceSnapshot(
        agent_execution_started=bool(runtime["agent_execution_started"]),
        adapter_completed=bool(runtime["adapter_completed"]),
        permission_required=bool(runtime["permission_required"]),
        observed_session_ref=runtime["observed_session_ref"],
    )
    classification_adapter = get_adapter(role.adapter or "")
    candidate_classifier = getattr(
        classification_adapter,
        "candidate_activation_failure",
        None,
    )
    candidate_activation_failure = (
        None
        if session_unavailable or candidate_classifier is None
        else candidate_classifier(
            run_dir=run_dir,
            role_id=role.role_id,
            session_generation=runtime["session_generation"],
            result=result,
            evidence=evidence,
        )
    )
    if candidate_activation_failure is not None:
        record_candidate_activation_failure_session(
            run_dir,
            role=role,
            runtime=runtime,
        )
    elif runtime["observed_session_ref"] and not session_unavailable:
        commit_session(
            run_dir,
            role=role,
            runtime=runtime,
            session_ref=runtime["observed_session_ref"],
        )
    projection = scan_journal(run_dir)
    terminal = projection.terminal_for_turn(runtime["turn_id"])
    if not runtime["group_quiescent"]:
        if terminal is not None:
            _finalize_existing_terminal(run_dir, runtime, terminal)
            return terminal
        runtime["phase"] = "recovery_required"
        runtime["outcome"] = "failed"
        event = commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="recovery",
            message="Runner process group could not be proven quiescent.",
        )
        runtime["terminal_event_id"] = event["event_id"]
        save_runtime(turn_dir, runtime, team=team)
        return event
    adapter = _finalize_adapter_run_state(run_dir, role=role)
    if terminal is not None:
        _finalize_existing_terminal(run_dir, runtime, terminal)
        return terminal
    if session_unavailable:
        event = commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="start_failure",
            message=(
                "Harness structurally reported that the requested Session was "
                "unavailable before model execution began. A fresh Session generation "
                "may be created only after an explicit user-authorized Resume."
            ),
        )
        runtime["outcome"] = "failed"
        runtime["phase"] = "finalized"
        runtime["terminal_event_id"] = event["event_id"]
        save_runtime(turn_dir, runtime, team=team)
        return event
    if runtime["permission_required"]:
        event = commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="permission",
            message="Harness emitted structured permission-required evidence.",
        )
        runtime["outcome"] = "failed"
        runtime["phase"] = "finalized"
        runtime["terminal_event_id"] = event["event_id"]
        save_runtime(turn_dir, runtime, team=team)
        return event
    if (
        team.observability.audit_mode == "full"
        and trace_manifest is not None
        and (
            trace_manifest["capture"].get("truncated") is True
            or trace_manifest["capture"].get("normalized_trace_truncated") is True
        )
    ):
        event = commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="recovery",
            message=(
                "Full audit mode requires a complete Turn trace, but the configured "
                "trace size limit truncated Harness or normalized output."
            ),
        )
        runtime["outcome"] = "failed"
        runtime["phase"] = "finalized"
        runtime["terminal_event_id"] = event["event_id"]
        save_runtime(turn_dir, runtime, team=team)
        return event
    exit_info = adapter.classify_result(result, evidence)
    recoverable_kind = adapter.recoverable_termination_kind(result, evidence)
    if (
        runtime["termination_kind"] == "output_limit"
        and recoverable_kind == "output_limit"
    ):
        try:
            event = deliver_outbox_locked(
                run_dir,
                runtime=runtime,
                allow_after_capture=allow_after_capture,
                automatic_continuation_reason="output_limit",
            )
        except RecoverableTurnArtifactError as exc:
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=(
                    f"Deferred {exc.artifact} evidence for this uniquely identified "
                    "Turn is damaged and cannot be trusted or regenerated: "
                    f"{exc.message}"
                ),
            )
            runtime["outcome"] = "failed"
        except IntegrityError:
            raise
        except (AgentTeamError, OSError) as exc:
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=f"Automatic continuation delivery failed: {exc}",
            )
            runtime["outcome"] = "failed"
        else:
            runtime["outcome"] = _terminal_outcome(event)
    elif exit_info.is_normal_completion:
        try:
            event = deliver_outbox_locked(
                run_dir,
                runtime=runtime,
                allow_after_capture=allow_after_capture,
            )
        except RecoverableTurnArtifactError as exc:
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=(
                    f"Deferred {exc.artifact} evidence for this uniquely identified "
                    f"Turn is damaged and cannot be trusted or regenerated: {exc.message}"
                ),
            )
            runtime["outcome"] = "failed"
        except IntegrityError:
            raise
        except (AgentTeamError, OSError) as exc:
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=f"Deferred delivery failed: {exc}",
            )
            runtime["outcome"] = "failed"
        else:
            runtime["outcome"] = _terminal_outcome(event)
    elif candidate_activation_failure is not None:
        return_role = _candidate_activation_return_role(
            run_dir,
            runtime=runtime,
        )
        if return_role is None:
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="start_failure",
                message=(
                    f"{candidate_activation_failure} The input Event has no "
                    "distinct sending role that can receive the finding."
                ),
            )
            runtime["outcome"] = "failed"
        else:
            try:
                event = deliver_outbox_locked(
                    run_dir,
                    runtime=runtime,
                    allow_after_capture=allow_after_capture,
                    candidate_activation_failure=candidate_activation_failure,
                    candidate_activation_return_role=return_role,
                )
            except RecoverableTurnArtifactError as exc:
                event = commit_technical_block_locked(
                    run_dir,
                    runtime=runtime,
                    reason="recovery",
                    message=(
                        f"Candidate activation {exc.artifact} evidence is damaged "
                        f"and cannot be routed safely: {exc.message}"
                    ),
                )
                runtime["outcome"] = "failed"
            except IntegrityError:
                raise
            except (AgentTeamError, OSError) as exc:
                event = commit_technical_block_locked(
                    run_dir,
                    runtime=runtime,
                    reason="recovery",
                    message=f"Candidate activation finding delivery failed: {exc}",
                )
                runtime["outcome"] = "failed"
            else:
                runtime["outcome"] = _terminal_outcome(event)
    else:
        exec_error = turn_dir / "process" / "exec-error.json"
        has_exec_error = path_entry_exists(exec_error)
        if has_exec_error:
            validate_exec_error(read_json(exec_error))
        reason = (
            "start_failure"
            if not runtime["agent_execution_started"]
            and (authorization is None or has_exec_error)
            else "recovery"
        )
        event = commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason=reason,
            message=(
                "Harness did not satisfy the adapter's normal completion contract "
                f"(exit={runtime['process_exit_code']}, "
                f"termination={runtime['termination_kind']}, "
                f"started={runtime['agent_execution_started']}, "
                f"completed={runtime['adapter_completed']})."
            ),
        )
        runtime["outcome"] = "failed"
    runtime["phase"] = "finalized"
    runtime["terminal_event_id"] = event["event_id"]
    save_runtime(turn_dir, runtime, team=team)
    return event


def _authorize_launch_locked(
    run_dir: Path,
    runtime: dict[str, Any],
    supervisor: dict[str, Any],
    *,
    expected_launch: LaunchSpec,
) -> bool:
    team = load_team(run_dir)
    projection = scan_journal(run_dir)
    role = team.roles[runtime["role_id"]]
    owner = read_owner(team.workspace)
    if owner is None or owner["run_id"] != team.run_id:
        raise IntegrityError("launch authorization requires exact ownership")
    if projection.terminal_for_turn(runtime["turn_id"]) is not None:
        return False
    if projection.current_role != runtime["role_id"]:
        return False
    active = [
        item
        for item in iter_runtimes(run_dir, team=team)
        if item["phase"] in {"starting", "running", "exited", "recovery_required"}
    ]
    if (
        len(active) != 1
        or active[0]["turn_id"] != runtime["turn_id"]
        or active[0]["phase"] == "recovery_required"
    ):
        raise IntegrityError(
            "launch authorization is blocked by the process-safety recovery gate"
        )
    persisted_launch = _load_launch_spec_for_runtime(
        run_dir,
        runtime=runtime,
        role=role,
    )
    if persisted_launch != expected_launch:
        raise IntegrityError("LaunchSpec changed before launch authorization")
    allowed, reason = can_create_business_turn(run_dir, projection)
    if not allowed and reason == "deadline":
        commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="limit",
            limit_reason="deadline",
            message="Wall-time deadline expired before launch authorization.",
        )
        return False
    adapter = get_adapter(role.adapter or "")
    try:
        adapter.assert_profile(
            role.launch_profile or "",
            role.session_policy or "",
            role.launch_profile_sha256 or "",
            role.launch_mode or "headless",
        )
    except AgentTeamError as exc:
        commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="profile_changed",
            message=exc.message,
        )
        return False
    if not identity_matches(
        supervisor["supervisor_pid"], supervisor["supervisor_start_id"]
    ):
        raise IntegrityError("supervisor identity cannot be verified")
    runner_path = run_dir / "turns" / runtime["turn_id"] / "process" / "runner.json"
    runner = validate_runner(
        read_json(runner_path),
        turn_id=runtime["turn_id"],
        nonce=runtime["launch_nonce"],
    )
    if (
        runner["runner_pid"] != supervisor["runner_pid"]
        or runner["runner_pgid"] != supervisor["runner_pgid"]
        or runner["runner_start_id"] != supervisor["runner_start_id"]
        or not identity_matches(
            runner["runner_pid"],
            runner["runner_start_id"],
            pgid=runner["runner_pgid"],
        )
    ):
        raise IntegrityError("runner identity cannot be verified")
    runtime.update(
        {
            "supervisor_pid": supervisor["supervisor_pid"],
            "supervisor_start_id": supervisor["supervisor_start_id"],
            "runner_pid": runner["runner_pid"],
            "runner_pgid": runner["runner_pgid"],
            "runner_start_id": runner["runner_start_id"],
        }
    )
    save_runtime(
        run_dir / "turns" / runtime["turn_id"],
        runtime,
        team=team,
    )
    authorization = {
        "schema_version": 1,
        "turn_id": runtime["turn_id"],
        "launch_nonce": runtime["launch_nonce"],
        "supervisor_pid": supervisor["supervisor_pid"],
        "supervisor_start_id": supervisor["supervisor_start_id"],
        "runner_pid": runner["runner_pid"],
        "runner_pgid": runner["runner_pgid"],
        "runner_start_id": runner["runner_start_id"],
        "launch_profile": runtime["launch_profile"],
        "launch_profile_sha256": runtime["launch_profile_sha256"],
        "authorized_at": rfc3339(),
    }
    atomic_json(
        run_dir / "turns" / runtime["turn_id"] / "process" / "launch-authorized.json",
        authorization,
        immutable=True,
    )
    runtime["phase"] = "running"
    save_runtime(
        run_dir / "turns" / runtime["turn_id"],
        runtime,
        team=team,
    )
    return True


def _launch_turn(
    run_dir: Path,
    runtime: dict[str, Any],
    logger: WorkerLogger,
) -> dict[str, Any] | None:
    team = load_team(run_dir)
    role = team.roles[runtime["role_id"]]
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            run_dir / "turns" / runtime["turn_id"],
            team=team,
        )
        projection = scan_journal(run_dir)
        terminal = projection.terminal_for_turn(runtime["turn_id"])
        if terminal is not None:
            return finalize_external_turn_locked(run_dir, current)
        owner = read_owner(team.workspace)
        if owner is None or owner["run_id"] != team.run_id:
            raise IntegrityError("launch preparation requires exact ownership")
        if current["phase"] != "starting" or current["launch_nonce"] is not None:
            raise IntegrityError(
                "External Turn has already consumed its launch attempt"
            )
    adapter = get_adapter(role.adapter or "")
    generation, session_ref = session_launch_state(run_dir, role)
    if generation != runtime["session_generation"]:
        raise IntegrityError("session generation changed before launch")
    cli_path = _cli_path()
    current_session = load_session(run_dir, role)
    unavailable_fallback = bool(
        role.session_policy == "resume"
        and current_session
        and current_session["status"] == "unavailable"
        and generation == current_session["generation"] + 1
        and session_ref is None
    )
    recovered_as_fresh = bool(
        unavailable_fallback and runtime["input_event_id"].startswith("resume-")
    )
    if unavailable_fallback and not recovered_as_fresh:
        raise IntegrityError(
            "an unavailable Session may fall back to Fresh only after a Resume event"
        )
    if recovered_as_fresh:
        logger.write(
            "warning",
            "SESSION_RECOVERED_AS_FRESH",
            (
                "explicit Resume is starting a fresh Harness Session generation "
                f"{generation}; prior session reason="
                f"{current_session['unavailable_reason']}"
            ),
            turn_id=runtime["turn_id"],
            event_id=runtime["input_event_id"],
        )
    prompt = render_turn_prompt(
        run_dir,
        runtime,
        cli_path=cli_path,
        session_ref=session_ref,
        session_recovered_as_fresh=recovered_as_fresh,
    )
    context = TurnLaunchContext(
        run_id=team.run_id,
        role_id=role.role_id,
        turn_id=runtime["turn_id"],
        workspace=str(team.workspace),
        turn_dir=str(run_dir / "turns" / runtime["turn_id"]),
        prompt=prompt,
        session_policy=role.session_policy or "",
        session_ref=session_ref,
        session_generation=generation,
        launch_profile=role.launch_profile or "",
        launch_profile_sha256=role.launch_profile_sha256 or "",
        agent_team_cli=cli_path,
        model=role.model,
        reasoning_effort=role.reasoning_effort,
        fast_mode=role.fast_mode,
        launch_mode=role.launch_mode or "headless",
        model_provider=role.model_provider,
        model_provider_config=role.model_provider_config,
    )
    try:
        launch = adapter.prepare_launch(context)
    except AgentTeamError as exc:
        with locked_run(run_dir, exclusive=True):
            current = load_runtime(
                run_dir / "turns" / runtime["turn_id"],
                team=team,
            )
            reason = (
                "profile_changed"
                if exc.code == "PROFILE_CHANGED_NEW_RUN_REQUIRED"
                else "start_failure"
            )
            event = commit_technical_block_locked(
                run_dir,
                runtime=current,
                reason=reason,
                message=exc.message,
            )
            current.update(
                {
                    "phase": "finalized",
                    "outcome": _terminal_outcome(event),
                    "group_quiescent": True,
                    "termination_kind": (
                        "cancelled"
                        if event["event_type"] == "cancel"
                        else (
                            "deadline"
                            if event.get("limit_reason") == "deadline"
                            else "unknown"
                        )
                    ),
                    "terminal_event_id": event["event_id"],
                }
            )
            save_runtime(
                run_dir / "turns" / runtime["turn_id"],
                current,
                team=team,
            )
        return event
    nonce = random_token()
    process_dir = run_dir / "turns" / runtime["turn_id"] / "process"
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            run_dir / "turns" / runtime["turn_id"],
            team=team,
        )
        projection = scan_journal(run_dir)
        terminal = projection.terminal_for_turn(runtime["turn_id"])
        if terminal is not None:
            return finalize_external_turn_locked(run_dir, current)
        if current["phase"] != "starting" or current["launch_nonce"] is not None:
            raise IntegrityError("External Turn launch attempt is no longer available")
        current["launch_nonce"] = nonce
        save_runtime(
            run_dir / "turns" / runtime["turn_id"],
            current,
            team=team,
        )
        process_dir.mkdir(mode=0o700)
        if launch.launch_mode == "interactive":
            if launch.prompt_file != str(process_dir / "prompt.md"):
                raise IntegrityError("interactive LaunchSpec prompt path is invalid")
            atomic_write(
                process_dir / "prompt.md",
                launch.stdin.encode("utf-8"),
                immutable=True,
            )
        atomic_json(process_dir / "launch.json", launch.to_json(), immutable=True)
    logger.write(
        "info",
        "SUPERVISOR_STARTING",
        "starting turn supervisor",
        turn_id=runtime["turn_id"],
        event_id=runtime["input_event_id"],
    )
    try:
        supervisor_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agent_team",
                "_turn-supervisor",
                "--run-dir",
                str(run_dir),
                "--turn",
                runtime["turn_id"],
                f"--nonce={nonce}",
                "--launch-sha256",
                launch.content_sha256(),
            ],
            stdin=(
                None
                if (role.launch_mode or "headless") == "interactive"
                else subprocess.DEVNULL
            ),
            close_fds=True,
        )
    except OSError as exc:
        with locked_run(run_dir, exclusive=True):
            current = load_runtime(
                run_dir / "turns" / runtime["turn_id"],
                team=team,
            )
            event = commit_technical_block_locked(
                run_dir,
                runtime=current,
                reason="start_failure",
                message=f"Supervisor process creation failed: {exc}",
            )
            current.update(
                {
                    "phase": "finalized",
                    "outcome": _terminal_outcome(event),
                    "group_quiescent": True,
                    "termination_kind": (
                        "cancelled"
                        if event["event_type"] == "cancel"
                        else (
                            "deadline"
                            if event.get("limit_reason") == "deadline"
                            else "unknown"
                        )
                    ),
                    "terminal_event_id": event["event_id"],
                }
            )
            save_runtime(
                run_dir / "turns" / runtime["turn_id"],
                current,
                team=team,
            )
        return event
    snapshot_path = process_dir / "supervisor.json"
    deadline = time.monotonic() + 20.0
    authorized = False
    while time.monotonic() < deadline:
        if path_entry_exists(snapshot_path):
            with locked_run(run_dir, exclusive=True):
                snapshot = validate_supervisor(read_json(snapshot_path))
                if snapshot["state"] in {
                    "waiting_authorization",
                    "running",
                    "stopping",
                    "finished",
                }:
                    current = load_runtime(
                        run_dir / "turns" / runtime["turn_id"],
                        team=team,
                    )
                    if current["launch_nonce"] != nonce:
                        raise IntegrityError("runtime launch nonce changed")
                    if snapshot["state"] == "waiting_authorization":
                        authorized = _authorize_launch_locked(
                            run_dir,
                            current,
                            snapshot,
                            expected_launch=launch,
                        )
                    break
        if supervisor_process.poll() is not None:
            break
        time.sleep(0.05)
    if supervisor_process.poll() is None:
        with locked_run(run_dir, exclusive=True):
            if not path_entry_exists(snapshot_path):
                # The Supervisor must persist its own identity before creating
                # a Runner. Absence under the Run lock proves no managed child
                # process may have been launched by this Supervisor.
                supervisor_process.terminate()
                try:
                    supervisor_process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    supervisor_process.kill()
                    supervisor_process.wait()
    supervisor_exit_code = supervisor_process.wait()
    with locked_run(run_dir, exclusive=True):
        current = load_runtime(
            run_dir / "turns" / runtime["turn_id"],
            team=team,
        )
        if not path_entry_exists(snapshot_path):
            projection = scan_journal(run_dir)
            terminal = projection.terminal_for_turn(runtime["turn_id"])
            if terminal is not None:
                return finalize_external_turn_locked(run_dir, current)
            entries = (
                {
                    path.name
                    for path in committed_directory_entries(process_dir)
                }
                if path_entry_exists(process_dir)
                else set()
            )
            expected_entries = {"launch.json"}
            if launch.launch_mode == "interactive":
                expected_entries.add("prompt.md")
            if (
                entries != expected_entries
                or current["phase"] != "starting"
                or current["supervisor_pid"] is not None
                or current["runner_pid"] is not None
            ):
                raise IntegrityError(
                    "Supervisor snapshot is missing with ambiguous process artifacts"
                )
            _load_launch_spec_for_runtime(
                run_dir,
                runtime=current,
                role=role,
            )
            event = commit_technical_block_locked(
                run_dir,
                runtime=current,
                reason="start_failure",
                message=(
                    "Supervisor exited before persisting its identity snapshot "
                    f"(exit={supervisor_exit_code})."
                ),
            )
            current.update(
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
                current,
                team=team,
            )
            return event
        event = finalize_external_turn_locked(
            run_dir,
            current,
            allow_after_capture=True,
        )
    logger.write(
        "info",
        "TURN_FINALIZED",
        f"turn finalized (authorized={authorized})",
        turn_id=runtime["turn_id"],
        event_id=event["event_id"] if event else None,
    )
    return event


def run_worker(run_dir: Path, role_id: str) -> int:
    set_private_umask()
    team = load_team(run_dir)
    role = team.roles.get(role_id)
    if role is None or role.binding != "external":
        raise AgentTeamError(
            "ROLE_NOT_EXTERNAL", f"{role_id!r} is not an External role"
        )
    logger = WorkerLogger(
        run_dir / "logs" / f"{role_id}.jsonl",
        run_id=team.run_id,
        role_id=role_id,
    )
    try:
        with locked_run(run_dir, exclusive=True):
            owner = read_owner(team.workspace)
            if owner is None or owner["run_id"] != team.run_id:
                raise IntegrityError(
                    "worker cannot start without exact workspace ownership"
                )
            _write_role_snapshot(run_dir, role_id)
        logger.write("info", "WORKER_STARTED", "worker entered event loop")
        while True:
            event_to_signal: dict[str, Any] | None = None
            runtime_to_launch: dict[str, Any] | None = None
            try:
                with locked_run(run_dir, exclusive=True):
                    projection = scan_journal(run_dir)
                    if projection.status in {"COMPLETED", "CANCELLED"}:
                        release_terminal_owner_locked(run_dir)
                        logger.write(
                            "info",
                            "WORKER_STOPPED",
                            f"run reached {projection.status}",
                        )
                        return 0
                    owner = read_owner(team.workspace)
                    if owner is None or owner["run_id"] != team.run_id:
                        raise IntegrityError("worker lost exact workspace ownership")
                    if projection.status == "BLOCKED":
                        logger.write(
                            "info",
                            "WORKER_RETIRED",
                            "run is blocked; inactive Worker retired",
                        )
                        return 0
                    elif (
                        projection.status == "RUNNING"
                        and projection.current_role != role_id
                    ):
                        logger.write(
                            "info",
                            "WORKER_RETIRED",
                            "execution token moved to another role",
                        )
                        return 0
                    elif (
                        projection.status == "RUNNING"
                        and projection.current_role == role_id
                        and projection.tail is not None
                    ):
                        existing = runtime_for_input(
                            run_dir,
                            projection.tail["event_id"],
                            team=team,
                        )
                        if existing is not None:
                            event_to_signal = finalize_external_turn_locked(
                                run_dir,
                                existing,
                            )
                        else:
                            runtime, continuity_error = create_business_turn_locked(
                                run_dir,
                                role_id=role_id,
                                executor="worker",
                            )
                            if runtime and continuity_error:
                                event_to_signal = commit_technical_block_locked(
                                    run_dir,
                                    runtime=runtime,
                                    reason="recovery",
                                    message=continuity_error,
                                )
                                runtime.update(
                                    {
                                        "phase": "finalized",
                                        "outcome": "failed",
                                        "group_quiescent": True,
                                        "termination_kind": "unknown",
                                        "terminal_event_id": event_to_signal[
                                            "event_id"
                                        ],
                                    }
                                )
                                save_runtime(
                                    run_dir / "turns" / runtime["turn_id"],
                                    runtime,
                                    team=team,
                                )
                            elif runtime and runtime["phase"] in {
                                "starting",
                                "running",
                            }:
                                runtime_to_launch = runtime
            except IntegrityError as exc:
                logger.write("error", "TEAM_CORRUPTED", str(exc))
                return 1
            if event_to_signal and event_to_signal.get("to_role"):
                target = team.roles[event_to_signal["to_role"]]
                if target.binding == "external":
                    if target.role_id != role_id:
                        ensure_workers(
                            run_dir,
                            team,
                            role_ids=(target.role_id,),
                        )
                    signal_change(team.run_id, target.role_id)
                    if target.role_id == role_id:
                        continue
                return 0
            if event_to_signal:
                return 0
            if runtime_to_launch is not None:
                event = _launch_turn(run_dir, runtime_to_launch, logger)
                if event and event.get("to_role"):
                    target = team.roles[event["to_role"]]
                    if target.binding == "external":
                        if target.role_id != role_id:
                            ensure_workers(
                                run_dir,
                                team,
                                role_ids=(target.role_id,),
                            )
                        signal_change(team.run_id, target.role_id)
                        if target.role_id == role_id:
                            continue
                if event is not None:
                    return 0
                continue
            time.sleep(0.5)
    finally:
        logger.close()

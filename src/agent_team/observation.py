from __future__ import annotations

import datetime as dt
import math
import stat
from pathlib import Path
from typing import Any

from .config import ROLE_ID_RE
from .errors import AgentTeamError, IntegrityError, RecoverableTurnArtifactError
from .gitfacts import load_workspace_facts, validate_runtime_git_boundaries
from .journal import can_create_business_turn, scan_journal
from .processes import process_identity_state
from .state import locked_run, read_owner
from .supervisor import (
    validate_runner,
    validate_supervisor,
)
from .tmux_runtime import capture_pane, list_windows, session_name
from .trace import validate_trace_manifest
from .turns import (
    active_runtime,
    iter_runtimes,
    load_outbox,
    load_session,
    runtime_for_input,
)
from .util import (
    committed_directory_entries,
    parse_rfc3339,
    path_entry_exists,
    read_json,
    read_regular,
    rfc3339,
    sha256_bytes,
)
from .worker import (
    _load_launch_spec_for_runtime,
    _validate_external_process_chain,
    validate_role_snapshot,
)


RECOMMENDED_ACTIONS = {
    "START",
    "WAIT",
    "CLAIM_ORIGIN_EVENT",
    "FINALIZE_ORIGIN_EXIT",
    "RETURN_BLOCK_TO_USER",
    "RUN_RECOVER",
    "READ_COMPLETION",
    "MANUAL_DIAGNOSIS",
    "NONE",
}


def _validate_role_and_session_snapshots(
    run_dir: Path,
    team: Any,
    runtimes: list[dict[str, Any]],
) -> list[str]:
    evidence: list[str] = []
    runtime_by_id = {runtime["turn_id"]: runtime for runtime in runtimes}
    for role_path in committed_directory_entries(run_dir / "roles"):
        role_id = role_path.stem
        if (
            role_path.suffix != ".json"
            or not ROLE_ID_RE.fullmatch(role_id)
            or role_path.is_symlink()
            or not role_path.is_file()
        ):
            raise IntegrityError(f"invalid Worker snapshot entry: {role_path.name}")
        role = team.roles.get(role_id)
        if role is None or role.binding != "external":
            raise IntegrityError(
                f"Worker snapshot does not belong to an External role: {role_id}"
            )
        worker = validate_role_snapshot(read_json(role_path), role_id)
        if worker["tmux_session"] != session_name(team.run_id):
            raise IntegrityError(f"Worker tmux session mismatch: {role_id}")
        evidence.append(f"roles/{role_path.name}")

    for session_path in committed_directory_entries(run_dir / "sessions"):
        role_id = session_path.stem
        if (
            session_path.suffix != ".json"
            or not ROLE_ID_RE.fullmatch(role_id)
            or session_path.is_symlink()
            or not session_path.is_file()
        ):
            raise IntegrityError(
                f"invalid Session snapshot entry: {session_path.name}"
            )
        role = team.roles.get(role_id)
        if role is None or role.binding != "external":
            raise IntegrityError(
                f"Session snapshot does not belong to an External role: {role_id}"
            )
        session = load_session(run_dir, role)
        assert session is not None
        try:
            created_runtime = runtime_by_id[session["created_turn_id"]]
            updated_runtime = runtime_by_id[session["updated_turn_id"]]
        except KeyError as exc:
            raise IntegrityError(
                f"Session snapshot references an unknown Turn: {role_id}"
            ) from exc
        for subject, referenced in {
            "created": created_runtime,
            "updated": updated_runtime,
        }.items():
            if (
                referenced["executor"] != "worker"
                or referenced["role_id"] != role_id
                or referenced["session_generation"] != session["generation"]
            ):
                raise IntegrityError(
                    f"Session {subject} Turn lineage is invalid: {role_id}"
                )
        if (
            created_runtime["business_turn_seq"]
            > updated_runtime["business_turn_seq"]
        ):
            raise IntegrityError(f"Session Turn lineage moves backward: {role_id}")
        if (
            session["status"] == "available"
            and updated_runtime["observed_session_ref"] is not None
            and updated_runtime["observed_session_ref"] != session["session_ref"]
        ):
            raise IntegrityError(
                f"Session Ref conflicts with its updated Turn: {role_id}"
            )
        evidence.append(f"sessions/{session_path.name}")
    return evidence


def _validate_authoritative_snapshots(
    run_dir: Path,
    team: Any,
    projection: Any,
    runtimes: list[dict[str, Any]],
) -> tuple[list[str], set[str]]:
    evidence = _validate_role_and_session_snapshots(run_dir, team, runtimes)
    incomplete_recovery_artifacts: set[str] = set()

    def append_regular(relative: str) -> None:
        path = run_dir / relative
        try:
            info = path.lstat()
        except OSError:
            return
        if stat.S_ISREG(info.st_mode) and not path.is_symlink():
            evidence.append(relative)

    for runtime in runtimes:
        turn_id = runtime["turn_id"]
        turn_dir = run_dir / "turns" / turn_id
        terminal = projection.terminal_for_turn(turn_id)
        acknowledged_turn_damage = bool(
            terminal is not None
            and terminal["event_type"] == "block"
            and terminal.get("block_reason") == "recovery"
            and runtime["terminal_event_id"] == terminal["event_id"]
            and runtime["phase"] in {"exited", "finalized", "recovery_required"}
        )
        evidence.append(f"turns/{turn_id}/runtime.json")
        input_path = turn_dir / "input.md"
        if sha256_bytes(read_regular(input_path)) != runtime["input_payload_sha256"]:
            raise IntegrityError(
                f"Turn input hash mismatch: {turn_id}",
                f"turns/{turn_id}/input.md",
            )
        evidence.append(f"turns/{turn_id}/input.md")
        for boundary, field in {
            "before": "workspace_facts_before_sha256",
            "after": "workspace_facts_after_sha256",
        }.items():
            path = turn_dir / f"workspace-facts-{boundary}.json"
            expected_hash = runtime[field]
            try:
                if expected_hash is None:
                    if path_entry_exists(path):
                        if boundary != "after" or runtime["phase"] not in {
                            "starting",
                            "running",
                            "exited",
                            "recovery_required",
                        }:
                            raise IntegrityError(
                                f"unreferenced {boundary.title()} Facts: {turn_id}",
                                f"turns/{turn_id}/workspace-facts-{boundary}.json",
                            )
                        facts = load_workspace_facts(
                            path,
                            expected_turn_id=turn_id,
                            expected_boundary=boundary,
                        )
                        if facts["workspace_realpath"] != str(team.workspace):
                            raise IntegrityError(
                                f"{boundary.title()} Facts workspace mismatch: {turn_id}"
                            )
                        evidence.append(
                            f"turns/{turn_id}/workspace-facts-{boundary}.json"
                        )
                        incomplete_recovery_artifacts.add(turn_id)
                    continue
                facts = load_workspace_facts(
                    path,
                    expected_turn_id=turn_id,
                    expected_boundary=boundary,
                )
                if facts["workspace_realpath"] != str(team.workspace):
                    raise IntegrityError(
                        f"{boundary.title()} Facts workspace mismatch: {turn_id}"
                    )
                if sha256_bytes(read_regular(path)) != expected_hash:
                    raise IntegrityError(
                        f"{boundary.title()} Facts hash mismatch: {turn_id}",
                        f"turns/{turn_id}/workspace-facts-{boundary}.json",
                    )
                evidence.append(
                    f"turns/{turn_id}/workspace-facts-{boundary}.json"
                )
            except (IntegrityError, OSError):
                if not acknowledged_turn_damage:
                    raise
                append_regular(
                    f"turns/{turn_id}/workspace-facts-{boundary}.json"
                )
        try:
            outbox = load_outbox(turn_dir)
            if outbox is not None:
                evidence.extend(
                    [
                        f"turns/{turn_id}/outbox.json",
                        outbox["payload_path"],
                    ]
                )
            else:
                pending_payload = turn_dir / "outbox-payload.md"
                if path_entry_exists(pending_payload):
                    read_regular(pending_payload)
                    evidence.append(f"turns/{turn_id}/outbox-payload.md")
                    incomplete_recovery_artifacts.add(turn_id)
        except (RecoverableTurnArtifactError, OSError):
            if not acknowledged_turn_damage:
                raise
            append_regular(f"turns/{turn_id}/outbox.json")
            append_regular(f"turns/{turn_id}/outbox-payload.md")
        process_dir = turn_dir / "process"
        if runtime["executor"] == "origin":
            if path_entry_exists(process_dir) and committed_directory_entries(
                process_dir
            ):
                raise IntegrityError(
                    f"Origin runtime contains process artifacts: {turn_id}"
                )
            continue
        role = team.roles[runtime["role_id"]]
        nonce = runtime["launch_nonce"]
        supervisor_path = process_dir / "supervisor.json"
        runner_path = process_dir / "runner.json"
        authorization_path = process_dir / "launch-authorized.json"
        if nonce is not None:
            launch_path = process_dir / "launch.json"
            if path_entry_exists(launch_path):
                _load_launch_spec_for_runtime(
                    run_dir,
                    runtime=runtime,
                    role=role,
                )
                evidence.append(f"turns/{turn_id}/process/launch.json")
            elif (
                terminal is not None
                and terminal["event_type"] == "block"
                and terminal.get("block_reason") == "start_failure"
                and runtime["phase"] == "finalized"
                and runtime["supervisor_pid"] is None
                and runtime["runner_pid"] is None
                and runtime["agent_execution_started"] is False
                and runtime["group_quiescent"] is True
                and (
                    not path_entry_exists(process_dir)
                    or not committed_directory_entries(process_dir)
                )
            ):
                pass
            elif (
                runtime["phase"] == "starting"
                and runtime["supervisor_pid"] is None
                and runtime["runner_pid"] is None
                and (
                    not path_entry_exists(process_dir)
                    or not committed_directory_entries(process_dir)
                )
            ):
                incomplete_recovery_artifacts.add(turn_id)
            else:
                raise IntegrityError(
                    f"Runtime launch nonce references a missing LaunchSpec: {turn_id}"
                )
        elif path_entry_exists(process_dir) and committed_directory_entries(
            process_dir
        ):
            raise IntegrityError(
                f"process artifacts exist before launch nonce: {turn_id}"
            )
        supervisor = None
        if path_entry_exists(supervisor_path):
            supervisor = validate_supervisor(read_json(supervisor_path))
            if supervisor["turn_id"] != turn_id or supervisor["launch_nonce"] != nonce:
                raise IntegrityError(f"Supervisor context mismatch: {turn_id}")
            evidence.append(f"turns/{turn_id}/process/supervisor.json")
        runner = None
        if path_entry_exists(runner_path):
            if nonce is None:
                raise IntegrityError(f"Runner exists without nonce: {turn_id}")
            runner = validate_runner(
                read_json(runner_path),
                turn_id=turn_id,
                nonce=nonce,
            )
            evidence.append(f"turns/{turn_id}/process/runner.json")
        authorization = None
        if supervisor is not None:
            chain_runner, authorization = _validate_external_process_chain(
                run_dir,
                runtime=runtime,
                role=role,
                supervisor=supervisor,
            )
            if chain_runner != runner:
                raise IntegrityError(
                    f"Runner snapshot changed while reading: {turn_id}"
                )
        elif (
            runner is not None
            or path_entry_exists(authorization_path)
            or runtime["supervisor_pid"] is not None
        ):
            raise IntegrityError(
                f"process identity artifacts lack Supervisor snapshot: {turn_id}"
            )
        if authorization is not None:
            evidence.append(f"turns/{turn_id}/process/launch-authorized.json")
        elif supervisor is None and (
            runtime["phase"] == "running"
            or runtime["agent_execution_started"]
            or runtime["permission_required"]
            or runtime["observed_session_ref"] is not None
        ):
            raise IntegrityError(f"consumed launch authorization is missing: {turn_id}")
        if runtime["runner_pid"] is not None and runner is None:
            raise IntegrityError(f"referenced Runner identity is missing: {turn_id}")
        if runtime["supervisor_pid"] is not None and supervisor is None:
            raise IntegrityError(
                f"referenced Supervisor snapshot is missing: {turn_id}"
            )
        if supervisor and supervisor["runner_pid"] is not None and runner is None:
            raise IntegrityError(
                f"Supervisor-referenced Runner identity is missing: {turn_id}",
                f"turns/{turn_id}/process/runner.json",
            )
        if (
            runner
            and supervisor
            and supervisor["runner_pid"] is not None
            and (
                runner["runner_pid"] != supervisor["runner_pid"]
                or runner["runner_pgid"] != supervisor["runner_pgid"]
                or runner["runner_start_id"] != supervisor["runner_start_id"]
            )
        ):
            raise IntegrityError(f"Runner/Supervisor identity mismatch: {turn_id}")
        if (
            supervisor
            and runtime["supervisor_pid"] is not None
            and (
                runtime["supervisor_pid"] != supervisor["supervisor_pid"]
                or runtime["supervisor_start_id"] != supervisor["supervisor_start_id"]
            )
        ):
            raise IntegrityError(
                f"Runtime/Supervisor identity mismatch: {turn_id}",
                f"turns/{turn_id}/runtime.json",
                f"turns/{turn_id}/process/supervisor.json",
            )
        if (
            runner
            and runtime["runner_pid"] is not None
            and (
                runtime["runner_pid"] != runner["runner_pid"]
                or runtime["runner_pgid"] != runner["runner_pgid"]
                or runtime["runner_start_id"] != runner["runner_start_id"]
            )
        ):
            raise IntegrityError(
                f"Runtime/Runner identity mismatch: {turn_id}",
                f"turns/{turn_id}/runtime.json",
                f"turns/{turn_id}/process/runner.json",
            )
        if (
            supervisor
            and supervisor["state"] == "finished"
            and runtime["phase"] in {"exited", "finalized", "recovery_required"}
        ):
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
                if runtime[field] != supervisor[field]:
                    raise IntegrityError(
                        f"Runtime/Supervisor final {field} mismatch: {turn_id}",
                        f"turns/{turn_id}/runtime.json",
                        f"turns/{turn_id}/process/supervisor.json",
                    )
        trace_hash = runtime["trace_manifest_sha256"]
        if trace_hash is not None:
            manifest = validate_trace_manifest(
                turn_dir,
                expected_sha256=trace_hash,
                expected_run_id=team.run_id,
                expected_role_id=runtime["role_id"],
                expected_adapter_id=team.roles[runtime["role_id"]].adapter,
                expected_policy=team.observability,
            )
            evidence.append(f"turns/{turn_id}/trace-manifest.json")
            evidence.extend(
                f"turns/{turn_id}/{artifact['path']}"
                for artifact in manifest["artifacts"]
            )
        elif (
            team.config_schema_version >= 2
            and runtime["phase"] == "finalized"
            and runtime["agent_execution_started"] is True
            and runtime["group_quiescent"] is True
        ):
            raise IntegrityError(
                f"finalized External Turn lacks an anchored trace: {turn_id}",
                f"turns/{turn_id}/trace-manifest.json",
            )
    return evidence, incomplete_recovery_artifacts


def _journal_tail(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        key: event.get(key)
        for key in {
            "event_id",
            "event_seq",
            "event_type",
            "from_role",
            "to_role",
            "turn_id",
            "payload_path",
            "created_at",
        }
    }


def _origin_state(run_dir: Path, projection: Any) -> str:
    current = active_runtime(run_dir, team=projection.team)
    if current is not None and current["executor"] == "origin":
        if current["phase"] == "running":
            return "claimed"
        if current["phase"] == "exited":
            return "exited"
    if projection.status == "BLOCKED":
        return "unclaimed"
    if (
        projection.status == "RUNNING"
        and projection.current_role is not None
        and projection.team.roles[projection.current_role].binding == "origin"
        and projection.tail is not None
        and runtime_for_input(
            run_dir,
            projection.tail["event_id"],
            team=projection.team,
        )
        is None
    ):
        return "unclaimed"
    if any(role.binding == "origin" for role in projection.team.roles.values()):
        return "finalized"
    return "not_applicable"


def _managed_process_state(
    run_dir: Path,
    runtime: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    if runtime["executor"] == "origin":
        return "not_applicable", None
    path = run_dir / "turns" / runtime["turn_id"] / "process" / "supervisor.json"
    if not path_entry_exists(path):
        return "not_started", None
    supervisor = validate_supervisor(read_json(path))
    mapping = {
        "starting": "starting",
        "waiting_authorization": "waiting_authorization",
        "running": "running",
        "stopping": "stopping",
    }
    identity_state = process_identity_state(
        supervisor["supervisor_pid"],
        supervisor["supervisor_start_id"],
    )
    if supervisor["state"] == "finished":
        if identity_state in {"gone", "reused"}:
            return "exited", supervisor
        if identity_state == "match":
            return "stopping", supervisor
        return "identity_unknown", supervisor
    if identity_state != "match":
        return "identity_unknown", supervisor
    return mapping[supervisor["state"]], supervisor


def _terminal_execution_was_releasable(
    runtimes: list[dict[str, Any]],
) -> bool:
    for runtime in runtimes:
        if runtime["phase"] != "finalized":
            return False
        if runtime["executor"] != "worker":
            continue
        if runtime["group_quiescent"] is not True:
            return False
        if runtime["supervisor_pid"] is not None and process_identity_state(
            runtime["supervisor_pid"],
            runtime["supervisor_start_id"],
        ) not in {"gone", "reused"}:
            return False
        if runtime["runner_pid"] is not None and process_identity_state(
            runtime["runner_pid"],
            runtime["runner_start_id"],
        ) not in {"gone", "reused"}:
            return False
    return True


def _active_turn(
    run_dir: Path,
    runtime: dict[str, Any] | None,
    observed: dt.datetime,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if runtime is None:
        return None, None
    managed, supervisor = _managed_process_state(run_dir, runtime)
    source = supervisor or runtime
    age = max(
        0,
        math.floor((observed - parse_rfc3339(runtime["created_at"])).total_seconds()),
    )
    origin = runtime["executor"] == "origin"
    result = {
        "turn_id": runtime["turn_id"],
        "business_turn_seq": runtime["business_turn_seq"],
        "executor": runtime["executor"],
        "role_id": runtime["role_id"],
        "phase": runtime["phase"],
        "outcome": runtime["outcome"],
        "input_event_id": runtime["input_event_id"],
        "input_path": f"turns/{runtime['turn_id']}/input.md",
        "age_seconds": age,
        "managed_process_state": managed,
        "process_exit_code": None if origin else source["process_exit_code"],
        "termination_kind": None if origin else source["termination_kind"],
        "agent_execution_started": None
        if origin
        else source["agent_execution_started"],
        "adapter_completed": None if origin else source["adapter_completed"],
        "permission_required": None if origin else source["permission_required"],
        "observed_session_ref": None if origin else source["observed_session_ref"],
        "group_quiescent": None if origin else source["group_quiescent"],
    }
    return result, supervisor


def derive_observation(run_dir: Path) -> dict[str, Any]:
    observed = dt.datetime.now(dt.timezone.utc)
    observed_at = rfc3339(observed)
    with locked_run(run_dir, exclusive=False):
        projection = scan_journal(run_dir)
        team = projection.team
        validate_runtime_git_boundaries(team.workspace)
        owner = read_owner(team.workspace)
        runtimes = iter_runtimes(run_dir, team=team)
        try:
            snapshot_evidence, incomplete_recovery_artifacts = (
                _validate_authoritative_snapshots(
                    run_dir,
                    team,
                    projection,
                    runtimes,
                )
            )
        except FileNotFoundError as exc:
            missing = Path(exc.filename) if exc.filename else None
            evidence_path = None
            if missing is not None:
                try:
                    evidence_path = missing.relative_to(run_dir).as_posix()
                except ValueError:
                    evidence_path = str(missing)
            raise IntegrityError(
                "referenced authoritative snapshot is missing",
                *([evidence_path] if evidence_path else []),
            ) from exc
        active = active_runtime(run_dir, team=team)
        active_value, supervisor = _active_turn(run_dir, active, observed)
        terminal_releasable = _terminal_execution_was_releasable(runtimes)
        if owner is None:
            if projection.status == "UNSTARTED":
                owner_state = "not_acquired"
            elif (
                projection.status in {"COMPLETED", "CANCELLED"}
                and terminal_releasable
            ):
                owner_state = "released"
            else:
                raise IntegrityError("started run is missing its workspace owner")
        elif owner["run_id"] == team.run_id:
            owner_state = "this_run"
        else:
            owner_state = "other_run"
            if projection.status not in {"UNSTARTED", "COMPLETED", "CANCELLED"}:
                raise IntegrityError("started run has mismatched workspace ownership")
            if (
                projection.status in {"COMPLETED", "CANCELLED"}
                and not terminal_releasable
            ):
                raise IntegrityError(
                    "terminal run changed ownership before safe release"
                )
        if any(role.binding == "external" for role in team.roles.values()):
            try:
                windows = list_windows(team.run_id)
            except IntegrityError:
                raise
            except (AgentTeamError, OSError):
                windows = {}
        else:
            windows = {}
        expected_windows = {
            role.role_id
            for role in team.roles.values()
            if role.binding == "external"
        }
        unexpected_windows = sorted(set(windows) - expected_windows)
        if unexpected_windows:
            raise IntegrityError(
                f"tmux session contains unexpected role windows: "
                f"{unexpected_windows}"
            )
        role_items: list[dict[str, Any]] = []
        worker_missing = False
        for role in sorted(team.roles.values(), key=lambda item: item.role_id):
            if role.binding == "origin":
                if (
                    active
                    and active["executor"] == "origin"
                    and active["role_id"] == role.role_id
                ):
                    origin_role_state = "running"
                elif projection.status in {"COMPLETED", "CANCELLED"}:
                    origin_role_state = "stopped"
                elif projection.status == "UNSTARTED":
                    origin_role_state = "not_started"
                else:
                    origin_role_state = "idle"
                role_items.append(
                    {
                        "role_id": role.role_id,
                        "binding": "origin",
                        "adapter": None,
                        "session_policy": None,
                        "launch_mode": None,
                        "launch_profile": None,
                        "launch_profile_sha256": None,
                        "model": None,
                        "reasoning_effort": None,
                        "fast_mode": None,
                        "state": origin_role_state,
                        "worker_pid": None,
                        "worker_start_id": None,
                        "tmux_session": None,
                        "tmux_pane_id": None,
                        "session_status": "not_applicable",
                        "session_generation": None,
                        "session_ref": None,
                        "session_unavailable_reason": None,
                    }
                )
                continue
            role_path = run_dir / "roles" / f"{role.role_id}.json"
            worker: dict[str, Any] | None = None
            if path_entry_exists(role_path):
                worker = validate_role_snapshot(read_json(role_path), role.role_id)
                if worker["tmux_session"] != session_name(team.run_id):
                    raise IntegrityError(
                        f"Worker tmux session mismatch: {role.role_id}",
                        f"roles/{role.role_id}.json",
                    )
            worker_identity = (
                process_identity_state(
                    worker["worker_pid"],
                    worker["worker_start_id"],
                )
                if worker
                else "gone"
            )
            worker_alive = worker_identity == "match"
            worker_identity_unknown = worker_identity in {
                "unknown",
                "invalid",
                "mismatch",
            }
            window = windows.get(role.role_id)
            if (
                worker
                and window
                and (
                    worker["tmux_pane_id"] != window["tmux_pane_id"]
                    or worker["worker_pid"] != window["pane_pid"]
                )
            ):
                raise IntegrityError(
                    f"Worker/tmux identity mismatch: {role.role_id}",
                    f"roles/{role.role_id}.json",
                )
            if projection.status in {"RUNNING", "BLOCKED"} and (
                not worker_alive or window is None
            ):
                worker_missing = True
            if (
                active
                and active["role_id"] == role.role_id
                and active["phase"]
                in {"starting", "running", "exited", "recovery_required"}
            ):
                role_state = (
                    "identity_unknown"
                    if active_value
                    and active_value["managed_process_state"] == "identity_unknown"
                    else "running"
                )
            elif projection.status in {"COMPLETED", "CANCELLED"}:
                role_state = "stopped"
            elif worker_identity_unknown:
                role_state = "identity_unknown"
            elif worker_alive:
                role_state = "idle"
            else:
                role_state = "not_started"
            session = load_session(run_dir, role)
            role_items.append(
                {
                    "role_id": role.role_id,
                    "binding": "external",
                    "adapter": role.adapter,
                    "session_policy": role.session_policy,
                    "launch_mode": role.launch_mode,
                    "launch_profile": role.launch_profile,
                    "launch_profile_sha256": role.launch_profile_sha256,
                    "model": role.model,
                    "reasoning_effort": role.reasoning_effort,
                    "fast_mode": role.fast_mode,
                    "state": role_state,
                    "worker_pid": worker["worker_pid"] if worker else None,
                    "worker_start_id": worker["worker_start_id"] if worker else None,
                    "tmux_session": worker["tmux_session"] if worker else None,
                    "tmux_pane_id": (window["tmux_pane_id"] if window else None),
                    "session_status": session["status"] if session else "not_created",
                    "session_generation": session["generation"] if session else None,
                    "session_ref": session["session_ref"] if session else None,
                    "session_unavailable_reason": (
                        session["unavailable_reason"] if session else None
                    ),
                }
            )
        kickoff = projection.kickoff
        if kickoff:
            started = parse_rfc3339(kickoff["created_at"])
            deadline = started + dt.timedelta(seconds=team.max_wall_time_seconds)
            stop = (
                parse_rfc3339(projection.tail["created_at"])
                if projection.status in {"COMPLETED", "CANCELLED"}
                else observed
            )
            elapsed = max(0, math.floor((stop - started).total_seconds()))
            remaining = max(0, math.ceil((deadline - observed).total_seconds()))
            deadline_at = rfc3339(deadline)
        else:
            elapsed = remaining = deadline_at = None
        origin_state = _origin_state(run_dir, projection)
        recovery_required = (
            any(runtime["phase"] == "recovery_required" for runtime in runtimes)
            or any(
                runtime["turn_id"] in incomplete_recovery_artifacts
                and runtime["executor"] == "worker"
                for runtime in runtimes
            )
            or bool(
                active_value
                and active_value["executor"] == "worker"
                and active_value["managed_process_state"] == "identity_unknown"
            )
        )
        block = None
        if projection.status == "BLOCKED":
            tail = projection.tail
            allowed, _ = can_create_business_turn(run_dir, projection)
            new_run = (
                tail.get("block_reason") in {"limit", "profile_changed"} or not allowed
            )
            block = {
                "event_id": tail["event_id"],
                "block_reason": tail.get("block_reason"),
                "limit_reason": tail.get("limit_reason"),
                "payload_path": tail["payload_path"],
                "resume_policy": (
                    "new_run_required" if new_run else "after_user_instruction"
                ),
            }
        terminal_cleanup_running = bool(
            projection.status in {"COMPLETED", "CANCELLED"}
            and active_value
            and active_value["executor"] == "worker"
            and active_value["managed_process_state"]
            in {"starting", "waiting_authorization", "running", "stopping"}
        )
        if recovery_required:
            health, recommended = "recovery_required", "RUN_RECOVER"
        elif origin_state == "exited":
            health, recommended = "attention", "FINALIZE_ORIGIN_EXIT"
        elif terminal_cleanup_running:
            health, recommended = "attention", "WAIT"
        elif (
            projection.status in {"COMPLETED", "CANCELLED"}
            and owner_state == "this_run"
        ):
            health, recommended = "attention", "RUN_RECOVER"
        elif origin_state == "unclaimed":
            health, recommended = (
                ("attention" if projection.status == "BLOCKED" else "ok"),
                "CLAIM_ORIGIN_EVENT",
            )
        elif worker_missing:
            health, recommended = "attention", "RUN_RECOVER"
        elif projection.status == "BLOCKED":
            health, recommended = "attention", "RETURN_BLOCK_TO_USER"
        elif projection.status == "UNSTARTED":
            health, recommended = (
                ("attention", "WAIT") if owner_state == "other_run" else ("ok", "START")
            )
        elif projection.status == "COMPLETED":
            health, recommended = "ok", "READ_COMPLETION"
        elif projection.status == "CANCELLED":
            health, recommended = "ok", "NONE"
        else:
            health, recommended = "ok", "WAIT"
        evidence_paths = [
            f"events/{event['event_seq']:04d}-{event['event_id']}.json"
            for event in projection.events
        ]
        evidence_paths.append("team.json")
        if projection.kickoff is not None:
            evidence_paths.extend(["REQUEST.md", "PROTOCOL.md"])
        evidence_paths.extend(snapshot_evidence)
        if projection.tail is not None:
            evidence_paths.append(projection.tail["payload_path"])
        for role in role_items:
            if role["binding"] == "external":
                role_path = f"roles/{role['role_id']}.json"
                if path_entry_exists(run_dir / role_path):
                    evidence_paths.append(role_path)
                session_path = f"sessions/{role['role_id']}.json"
                if path_entry_exists(run_dir / session_path):
                    evidence_paths.append(session_path)
        if active:
            evidence_paths.extend(
                [
                    f"turns/{active['turn_id']}/input.md",
                    f"turns/{active['turn_id']}/runtime.json",
                ]
            )
            process_root = run_dir / "turns" / active["turn_id"] / "process"
            for name in {"runner.json", "supervisor.json", "launch-authorized.json"}:
                if path_entry_exists(process_root / name):
                    evidence_paths.append(f"turns/{active['turn_id']}/process/{name}")
        details = {
            "supervisor_pid": supervisor["supervisor_pid"] if supervisor else None,
            "supervisor_start_id": (
                supervisor["supervisor_start_id"] if supervisor else None
            ),
            "runner_pid": supervisor["runner_pid"] if supervisor else None,
            "runner_pgid": supervisor["runner_pgid"] if supervisor else None,
            "runner_start_id": supervisor["runner_start_id"] if supervisor else None,
            "owner_run_id": owner["run_id"] if owner else None,
        }
        return {
            "run_id": team.run_id,
            "run_status": projection.status,
            "health": health,
            "journal_tail": _journal_tail(projection.tail),
            "current_role": projection.current_role,
            "active_turn": active_value,
            "roles": role_items,
            "workspace_owner": owner_state,
            "origin": {"state": origin_state},
            "limits": {
                "turns_used": sum(
                    1
                    for runtime in runtimes
                    if runtime["business_turn_seq"] is not None
                ),
                "max_turns": team.max_turns,
                "elapsed_seconds": elapsed,
                "deadline_at": deadline_at,
                "remaining_seconds": remaining,
            },
            "block": block,
            "recovery_required": recovery_required,
            "recommended_action": recommended,
            "details": details,
            "evidence_paths": sorted(set(evidence_paths)),
            "_observed_at": observed_at,
        }


def corrupted_observation(
    run_id: str,
    message: str,
    *,
    evidence_paths: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "run_status": "CORRUPTED",
        "health": "corrupted",
        "journal_tail": None,
        "current_role": None,
        "active_turn": None,
        "roles": [],
        "workspace_owner": "invalid",
        "origin": {"state": "not_applicable"},
        "limits": {
            "turns_used": None,
            "max_turns": None,
            "elapsed_seconds": None,
            "deadline_at": None,
            "remaining_seconds": None,
        },
        "block": None,
        "recovery_required": None,
        "recommended_action": "MANUAL_DIAGNOSIS",
        "details": {
            "supervisor_pid": None,
            "supervisor_start_id": None,
            "runner_pid": None,
            "runner_pgid": None,
            "runner_start_id": None,
            "owner_run_id": None,
            "corruption": message,
        },
        "evidence_paths": sorted(set(evidence_paths)),
        "_observed_at": rfc3339(),
    }


def status_text(data: dict[str, Any]) -> str:
    limits = data["limits"]
    lines = [
        f"Run: {data['run_id']}",
        f"Status: {data['run_status']}",
        f"Health: {data['health']}",
        f"Current role: {data['current_role'] or '-'}",
        f"Workspace owner: {data['workspace_owner']}",
        f"Origin state: {data['origin']['state']}",
        f"Turn: {limits['turns_used']} / {limits['max_turns']}",
        f"Recommended action: {data['recommended_action']}",
    ]
    if data["active_turn"]:
        turn = data["active_turn"]
        lines.append(
            f"Active turn: {turn['turn_id']} phase={turn['phase']} "
            f"age={turn['age_seconds']}s"
        )
        lines.append(f"Managed process: {turn['managed_process_state']}")
    lines.append("")
    lines.append("Roles:")
    for role in data["roles"]:
        harness = role["adapter"] or ""
        if role.get("launch_mode"):
            harness = f"{harness}/{role['launch_mode']}"
        lines.append(
            f"  {role['role_id']:<16} {role['binding']:<8} "
            f"{harness:<24} {role['state']}"
        )
    return "\n".join(lines)


DIAGNOSTIC_ORDER = [
    "workspace_lock",
    "state_root",
    "run_lock",
    "config",
    "journal",
    "owner",
    "active_turn",
    "worker",
    "supervisor",
    "runner",
    "runner_group",
    "launch_authorization",
    "session",
    "workspace_facts",
    "recovery_gate",
    "tmux_runtime",
]


def diagnose(
    run_dir: Path,
    *,
    role_id: str | None = None,
    preexisting_failure: str | None = None,
) -> dict[str, Any]:
    if preexisting_failure is not None:
        observation = corrupted_observation(run_dir.name, preexisting_failure)
        failure = preexisting_failure
        failure_paths: list[str] = []
    else:
        try:
            observation = derive_observation(run_dir)
            failure = None
            failure_paths = []
        except IntegrityError as exc:
            observation = corrupted_observation(
                run_dir.name,
                str(exc),
                evidence_paths=exc.evidence_paths,
            )
            failure = str(exc)
            failure_paths = list(exc.evidence_paths)
    if (
        role_id is not None
        and observation["roles"]
        and role_id not in {role["role_id"] for role in observation["roles"]}
    ):
        from .errors import InvalidArgument

        raise InvalidArgument(f"unknown role for diagnosis: {role_id}")

    def related_paths(name: str, subject_role_id: str | None) -> list[str]:
        if name == "worker" and subject_role_id:
            prefixes = (
                f"roles/{subject_role_id}.json",
                f"logs/{subject_role_id}.jsonl",
            )
        elif name == "session" and subject_role_id:
            prefixes = (f"sessions/{subject_role_id}.json",)
        else:
            prefixes = {
                "config": ("REQUEST.md", "PROTOCOL.md", "team.json"),
                "journal": ("events/", "handoffs/", "resumes/", "completion/"),
                "active_turn": ("turns/",),
                "worker": ("roles/", "logs/"),
                "supervisor": ("turns/",),
                "runner": ("turns/",),
                "runner_group": ("turns/",),
                "launch_authorization": ("turns/",),
                "session": ("sessions/",),
                "workspace_facts": ("turns/",),
            }.get(name, ())
        return sorted(
            path
            for path in observation["evidence_paths"]
            if any(path == prefix or path.startswith(prefix) for prefix in prefixes)
        )

    failure_check = None
    if failure:
        lowered = failure.lower()
        keyword_checks = (
            ("workspace operation lock", "workspace_lock"),
            ("state root", "state_root"),
            ("root.json", "state_root"),
            ("journal.lock", "run_lock"),
            ("team.json", "config"),
            ("immutable run input", "config"),
            ("journal", "journal"),
            ("event", "journal"),
            ("owner", "owner"),
            ("authorization", "launch_authorization"),
            ("session", "session"),
            ("supervisor", "supervisor"),
            ("runner group", "runner_group"),
            ("runner", "runner"),
            ("facts", "workspace_facts"),
            ("worker", "worker"),
            ("turn", "active_turn"),
            ("runtime", "active_turn"),
        )
        failure_check = next(
            (name for keyword, name in keyword_checks if keyword in lowered),
            "active_turn",
        )
    code_map = {
        "workspace_lock": "WORKSPACE_LOCK_INVALID",
        "state_root": "STATE_ROOT_INVALID",
        "run_lock": "RUN_LOCK_INVALID",
        "config": "CONFIG_INTEGRITY_FAILED",
        "journal": "JOURNAL_INTEGRITY_FAILED",
        "owner": "OWNER_INVALID",
        "active_turn": "TURN_SNAPSHOT_INVALID",
        "worker": "PROCESS_IDENTITY_UNKNOWN",
        "supervisor": "PROCESS_IDENTITY_UNKNOWN",
        "runner": "PROCESS_IDENTITY_UNKNOWN",
        "runner_group": "RUNNER_GROUP_NOT_QUIESCENT",
        "launch_authorization": "LAUNCH_AUTHORIZATION_INVALID",
        "session": "SESSION_SNAPSHOT_INVALID",
        "workspace_facts": "WORKSPACE_FACTS_INVALID",
        "recovery_gate": "RECOVERY_REQUIRED",
        "tmux_runtime": "TMUX_RUNTIME_MISSING",
    }
    roles_by_id = {item["role_id"]: item for item in observation["roles"]}
    external_roles = [
        item
        for item in observation["roles"]
        if item["binding"] == "external"
        and (role_id is None or item["role_id"] == role_id)
    ]
    active = observation["active_turn"]
    active_external = (
        active
        if active
        and active["executor"] == "worker"
        and (role_id is None or active["role_id"] == role_id)
        else None
    )

    def subject_path(name: str) -> str | None:
        if name == "run_lock":
            return "journal.lock"
        if name == "config":
            return "team.json"
        if name == "journal":
            return "events/"
        if name == "active_turn" and active:
            return f"turns/{active['turn_id']}/runtime.json"
        if name == "recovery_gate" and active_external:
            return f"turns/{active_external['turn_id']}/runtime.json"
        if name == "workspace_facts" and active:
            return (
                f"turns/{active['turn_id']}/workspace-facts-before.json"
                if active["business_turn_seq"] is not None
                else None
            )
        if not active_external:
            return None
        process_root = f"turns/{active_external['turn_id']}/process"
        return {
            "supervisor": f"{process_root}/supervisor.json",
            "runner": f"{process_root}/runner.json",
            "runner_group": f"{process_root}/runner.json",
            "launch_authorization": (f"{process_root}/launch-authorized.json"),
        }.get(name)

    def subjects(name: str) -> list[tuple[str | None, str | None]]:
        if name in {"worker", "session", "tmux_runtime"}:
            if external_roles:
                return [
                    (
                        item["role_id"],
                        (
                            f"roles/{item['role_id']}.json"
                            if name == "worker"
                            else (
                                f"sessions/{item['role_id']}.json"
                                if name == "session"
                                else None
                            )
                        ),
                    )
                    for item in external_roles
                ]
            return [(role_id, None)]
        if name in {
            "supervisor",
            "runner",
            "runner_group",
            "launch_authorization",
        }:
            return [
                (
                    active_external["role_id"] if active_external else role_id,
                    subject_path(name),
                )
            ]
        return [(None, subject_path(name))]

    checks: list[dict[str, Any]] = []
    for name in DIAGNOSTIC_ORDER:
        for subject_role_id, target_path in subjects(name):
            role = (
                roles_by_id.get(subject_role_id)
                if subject_role_id is not None
                else None
            )
            role_bound = name in {"worker", "session", "tmux_runtime"}
            process_bound = name in {
                "supervisor",
                "runner",
                "runner_group",
                "launch_authorization",
            }
            applicable = not (
                (role_bound and (role is None or role["binding"] != "external"))
                or (process_bound and active_external is None)
            )
            if not applicable:
                status, code, summary = (
                    "not_applicable",
                    "NOT_APPLICABLE",
                    "not applicable",
                )
            elif failure:
                if name == failure_check:
                    code = code_map[name]
                    if name == "owner":
                        if "missing" in failure.lower():
                            code = "OWNER_MISSING"
                        elif "mismatch" in failure.lower():
                            code = "OWNER_MISMATCH"
                    status, summary = "fail", failure
                else:
                    status, code, summary = (
                        "unknown",
                        code_map[name],
                        "not provable after an earlier integrity failure",
                    )
            else:
                status, code, summary = "pass", "OK", "check passed"
                if name == "active_turn" and active is None:
                    status, code, summary = (
                        "not_applicable",
                        "NOT_APPLICABLE",
                        "no active turn",
                    )
                elif name == "worker" and role:
                    if observation["run_status"] in {"RUNNING", "BLOCKED"} and role[
                        "state"
                    ] in {"not_started", "identity_unknown"}:
                        unknown = role["state"] == "identity_unknown"
                        status = "unknown" if unknown else "fail"
                        code = (
                            "PROCESS_IDENTITY_UNKNOWN"
                            if unknown
                            else "PROCESS_RUNTIME_MISSING"
                        )
                        summary = f"External Worker {role['role_id']} is unavailable"
                elif process_bound and active_external:
                    managed = active_external["managed_process_state"]
                    if managed == "identity_unknown":
                        status, code, summary = (
                            "unknown",
                            "PROCESS_IDENTITY_UNKNOWN",
                            "active process identity cannot be verified",
                        )
                    elif (
                        (name == "supervisor" and managed == "not_started")
                        or (
                            name in {"runner", "runner_group"}
                            and managed in {"not_started", "starting"}
                        )
                        or (
                            name == "launch_authorization"
                            and managed
                            in {
                                "not_started",
                                "starting",
                                "waiting_authorization",
                            }
                        )
                    ):
                        status, code, summary = (
                            "not_applicable",
                            "NOT_APPLICABLE",
                            "this process stage has not been reached",
                        )
                    elif name == "runner_group" and observation["recovery_required"]:
                        status, code, summary = (
                            "fail",
                            "RUNNER_GROUP_NOT_QUIESCENT",
                            "Runner process group has not been proven quiescent",
                        )
                elif name == "recovery_gate" and observation["recovery_required"]:
                    status, code, summary = (
                        "fail",
                        "RECOVERY_REQUIRED",
                        "an unresolved process-safety recovery gate is active",
                    )
                elif name == "tmux_runtime" and role:
                    if observation["run_status"] in {
                        "UNSTARTED",
                        "COMPLETED",
                        "CANCELLED",
                    }:
                        status, code, summary = (
                            "not_applicable",
                            "NOT_APPLICABLE",
                            "tmux runtime is not required in this Run state",
                        )
                    elif role["tmux_session"] is None or role["tmux_pane_id"] is None:
                        status, code, summary = (
                            "fail",
                            "TMUX_RUNTIME_MISSING",
                            f"External role {role['role_id']} has no tmux runtime",
                        )
            checks.append(
                {
                    "check": name,
                    "subject_role_id": subject_role_id,
                    "subject_path": target_path,
                    "status": status,
                    "code": code,
                    "summary": summary,
                    "evidence_paths": (
                        failure_paths
                        if failure and name == failure_check
                        else related_paths(name, subject_role_id)
                    ),
                    "recommended_action": (
                        observation["recommended_action"]
                        if status not in {"pass", "not_applicable"}
                        else None
                    ),
                }
            )
    if preexisting_failure is not None:
        observed_at = observation.pop("_observed_at", rfc3339())
        return {
            "observation": observation,
            "checks": checks,
            "attachments": {"paths": [], "pane_excerpt": None},
            "_observed_at": observed_at,
        }
    paths: list[str] = []
    for role in observation["roles"]:
        if role_id and role["role_id"] != role_id:
            continue
        log = run_dir / "logs" / f"{role['role_id']}.jsonl"
        if log.exists() and not log.is_symlink() and stat.S_ISREG(log.lstat().st_mode):
            paths.append(f"logs/{role['role_id']}.jsonl")
    turns_root = run_dir / "turns"
    if turns_root.exists() and turns_root.is_dir() and not turns_root.is_symlink():
        for turn_dir in sorted(turns_root.iterdir(), key=lambda path: path.name):
            try:
                runtime = read_json(turn_dir / "runtime.json")
            except (FileNotFoundError, IntegrityError):
                continue
            if role_id and runtime.get("role_id") != role_id:
                continue
            stream = turn_dir / "process" / "stream.jsonl"
            if (
                stream.exists()
                and not stream.is_symlink()
                and stat.S_ISREG(stream.lstat().st_mode)
            ):
                paths.append(f"turns/{turn_dir.name}/process/stream.jsonl")
            trace = turn_dir / "trace.jsonl"
            if (
                trace.exists()
                and not trace.is_symlink()
                and stat.S_ISREG(trace.lstat().st_mode)
            ):
                paths.append(f"turns/{turn_dir.name}/trace.jsonl")
    pane = None
    if role_id:
        try:
            excerpt = capture_pane(run_dir.name, role_id)
        except (AgentTeamError, OSError):
            excerpt = None
        if excerpt is not None:
            pane = {
                "role_id": role_id,
                "captured_at": rfc3339(),
                "truncated": len(excerpt.encode("utf-8")) >= 65536,
                "text": excerpt,
            }
    observed_at = observation.pop("_observed_at", rfc3339())
    return {
        "observation": observation,
        "checks": checks,
        "attachments": {"paths": sorted(paths), "pane_excerpt": pane},
        "_observed_at": observed_at,
    }

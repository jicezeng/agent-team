from __future__ import annotations

import datetime as dt
import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .config import ROLE_ID_RE, Role, Team, load_team
from .errors import (
    AgentTeamError,
    IntegrityError,
    InvalidArgument,
    RecoverableTurnArtifactError,
    RoutePreflightError,
)
from .gitfacts import (
    capture_workspace_facts,
    load_workspace_facts,
    same_workspace_state,
    write_workspace_facts,
)
from .journal import (
    business_turn_count,
    can_create_business_turn,
    commit_event,
    next_event_identity,
    scan_journal,
)
from .state import read_owner
from .util import (
    atomic_json,
    atomic_write,
    committed_directory_entries,
    fsync_dir,
    parse_rfc3339,
    path_entry_exists,
    random_token,
    read_json,
    read_private_regular,
    read_regular,
    require_keys,
    require_schema_version,
    resolve_run_path,
    rfc3339,
    safe_relative,
    sha256_bytes,
)

RUNTIME_REQUIRED = {
    "schema_version",
    "turn_id",
    "business_turn_seq",
    "input_event_id",
    "input_payload_sha256",
    "role_id",
    "executor",
    "phase",
    "outcome",
    "session_generation",
    "launch_profile",
    "launch_profile_sha256",
    "launch_nonce",
    "supervisor_pid",
    "supervisor_start_id",
    "runner_pid",
    "runner_pgid",
    "runner_start_id",
    "agent_execution_started",
    "group_quiescent",
    "workspace_facts_before_sha256",
    "workspace_facts_after_sha256",
    "process_exit_code",
    "adapter_completed",
    "permission_required",
    "observed_session_ref",
    "termination_kind",
    "terminal_event_id",
    "origin_claim_id",
    "trace_manifest_sha256",
    "created_at",
    "updated_at",
}
OUTBOX_REQUIRED = {
    "schema_version",
    "turn_id",
    "action",
    "to_role",
    "block_reason",
    "payload_path",
    "payload_sha256",
    "created_at",
}
SESSION_REQUIRED = {
    "schema_version",
    "role_id",
    "adapter",
    "generation",
    "status",
    "session_ref",
    "effective_launch_profile",
    "effective_launch_profile_sha256",
    "created_turn_id",
    "updated_turn_id",
    "unavailable_reason",
    "updated_at",
}
PHASE_ORDER = {
    "starting": 0,
    "running": 1,
    "exited": 2,
    "finalized": 3,
    "recovery_required": 3,
}
OUTCOMES = {None, "success", "failed", "cancelled", "stalled"}
TERMINATION_KINDS = {
    None,
    "normal",
    "cancelled",
    "deadline",
    "signal",
    "crash",
    "action",
    "output_limit",
    "unknown",
}
AUTOMATIC_CONTINUATION_REASONS = frozenset({"output_limit"})
TURN_ID_RE = re.compile(r"^turn-\d{4,}$")
INPUT_EVENT_ID_RE = re.compile(r"^(kickoff|handoff|resume|block)-\d{4,}$")
TERMINAL_EVENT_ID_RE = re.compile(r"^(handoff|complete|block|resume|cancel)-\d{4,}$")


def _is_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def validate_payload_contract(
    payload: bytes,
    *,
    required_sections: tuple[str, ...],
) -> None:
    if not required_sections:
        return
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentTeamError(
            "PAYLOAD_CONTRACT_VIOLATION",
            "audited formal payloads must be UTF-8 Markdown",
        ) from exc
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(text.splitlines()):
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((index, match.group(1).strip()))
    lines = text.splitlines()
    missing: list[str] = []
    empty: list[str] = []
    for required in required_sections:
        matches = [
            (position, line_index)
            for position, (line_index, title) in enumerate(headings)
            if title.casefold() == required.casefold()
        ]
        if not matches:
            missing.append(required)
            continue
        has_content = False
        for position, line_index in matches:
            next_heading = (
                headings[position + 1][0]
                if position + 1 < len(headings)
                else len(lines)
            )
            if any(line.strip() for line in lines[line_index + 1 : next_heading]):
                has_content = True
                break
        if not has_content:
            empty.append(required)
    if missing or empty:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if empty:
            details.append("empty: " + ", ".join(empty))
        raise AgentTeamError(
            "PAYLOAD_CONTRACT_VIOLATION",
            "formal payload does not satisfy the audited Markdown contract ("
            + "; ".join(details)
            + ")",
        )


def is_deadline_before_claim_pending(value: dict[str, Any]) -> bool:
    return bool(
        value.get("business_turn_seq") is not None
        and value.get("role_id") is not None
        and value.get("workspace_facts_before_sha256") is None
        and value.get("phase") == "starting"
        and value.get("outcome") is None
        and value.get("terminal_event_id") is None
        and value.get("launch_nonce") is None
        and value.get("origin_claim_id") is None
    )


def validate_runtime(
    value: dict[str, Any],
    *,
    team: Team | None = None,
) -> dict[str, Any]:
    require_schema_version(value, 1, subject="turn runtime")
    if (
        "trace_manifest_sha256" not in value
        and team is not None
        and team.config_schema_version == 1
    ):
        # v0.1 Runs predate anchored Turn traces. Normalize them in memory so
        # observation and recovery can remain backward-compatible.
        value = {**value, "trace_manifest_sha256": None}
    require_keys(value, required=RUNTIME_REQUIRED, subject="turn runtime")
    if not isinstance(value["turn_id"], str) or not TURN_ID_RE.fullmatch(
        value["turn_id"]
    ):
        raise IntegrityError("turn runtime has invalid turn_id")
    if not isinstance(value["executor"], str) or value["executor"] not in {
        "worker",
        "origin",
    }:
        raise IntegrityError("turn runtime has invalid executor")
    if not isinstance(value["phase"], str) or value["phase"] not in PHASE_ORDER:
        raise IntegrityError("turn runtime has invalid phase")
    if value["outcome"] is not None and (
        not isinstance(value["outcome"], str) or value["outcome"] not in OUTCOMES
    ):
        raise IntegrityError("turn runtime has invalid outcome")
    if value["termination_kind"] is not None and (
        not isinstance(value["termination_kind"], str)
        or value["termination_kind"] not in TERMINATION_KINDS
    ):
        raise IntegrityError("turn runtime has invalid termination_kind")
    seq = value["business_turn_seq"]
    if seq is not None and (
        isinstance(seq, bool) or not isinstance(seq, int) or seq < 1
    ):
        raise IntegrityError("turn runtime has invalid business_turn_seq")
    if not isinstance(value["input_event_id"], str) or not INPUT_EVENT_ID_RE.fullmatch(
        value["input_event_id"]
    ):
        raise IntegrityError("turn runtime has invalid input_event_id")
    if not _is_hash(value["input_payload_sha256"]):
        raise IntegrityError("turn runtime has invalid input payload hash")
    if value["trace_manifest_sha256"] is not None and not _is_hash(
        value["trace_manifest_sha256"]
    ):
        raise IntegrityError("turn runtime trace manifest hash is invalid")
    if value["executor"] == "worker":
        if not isinstance(value["role_id"], str) or seq is None:
            raise IntegrityError("worker runtime requires role and business sequence")
        if (
            isinstance(value["session_generation"], bool)
            or not isinstance(value["session_generation"], int)
            or value["session_generation"] < 1
            or not isinstance(value["launch_profile"], str)
            or not value["launch_profile"]
            or not _is_hash(value["launch_profile_sha256"])
            or value["origin_claim_id"] is not None
        ):
            raise IntegrityError("worker runtime has invalid external fields")
        if value["launch_nonce"] is not None and (
            not isinstance(value["launch_nonce"], str) or not value["launch_nonce"]
        ):
            raise IntegrityError("worker runtime has invalid launch nonce")
        if not isinstance(value["agent_execution_started"], bool):
            raise IntegrityError("worker runtime has invalid execution evidence")
        if not isinstance(value["adapter_completed"], bool):
            raise IntegrityError("worker runtime has invalid completion evidence")
        if not isinstance(value["permission_required"], bool):
            raise IntegrityError("worker runtime has invalid permission evidence")
        if value["adapter_completed"] and (
            not value["agent_execution_started"] or value["permission_required"]
        ):
            raise IntegrityError("worker runtime has conflicting adapter evidence")
        if value["group_quiescent"] is not None and not isinstance(
            value["group_quiescent"], bool
        ):
            raise IntegrityError("worker runtime has invalid group quiescence")
        if value["observed_session_ref"] is not None and (
            not isinstance(value["observed_session_ref"], str)
            or not value["observed_session_ref"]
        ):
            raise IntegrityError("worker runtime has invalid observed session ref")
        if value["process_exit_code"] is not None and (
            isinstance(value["process_exit_code"], bool)
            or not isinstance(value["process_exit_code"], int)
        ):
            raise IntegrityError("worker runtime has invalid process exit code")
        supervisor_pair = (
            value["supervisor_pid"],
            value["supervisor_start_id"],
        )
        supervisor_empty = all(item is None for item in supervisor_pair)
        supervisor_full = (
            isinstance(supervisor_pair[0], int)
            and not isinstance(supervisor_pair[0], bool)
            and supervisor_pair[0] > 0
            and isinstance(supervisor_pair[1], str)
            and bool(supervisor_pair[1])
        )
        runner_triple = (
            value["runner_pid"],
            value["runner_pgid"],
            value["runner_start_id"],
        )
        runner_empty = all(item is None for item in runner_triple)
        runner_full = (
            isinstance(runner_triple[0], int)
            and not isinstance(runner_triple[0], bool)
            and runner_triple[0] > 0
            and isinstance(runner_triple[1], int)
            and not isinstance(runner_triple[1], bool)
            and runner_triple[1] == runner_triple[0]
            and isinstance(runner_triple[2], str)
            and bool(runner_triple[2])
        )
        if not (supervisor_empty or supervisor_full):
            raise IntegrityError("worker runtime has partial supervisor identity")
        if not (runner_empty or runner_full):
            raise IntegrityError("worker runtime has partial runner identity")
        if runner_full and not supervisor_full:
            raise IntegrityError("runner identity requires supervisor identity")
        if value["phase"] == "running" and (
            value["launch_nonce"] is None
            or not supervisor_full
            or not runner_full
            or value["group_quiescent"] is not None
        ):
            raise IntegrityError(
                "running worker runtime has incomplete launch identity"
            )
        if value["phase"] in {"starting", "running"} and (
            value["group_quiescent"] is not None
            or value["process_exit_code"] is not None
            or value["termination_kind"] is not None
            or value["agent_execution_started"]
            or value["adapter_completed"]
            or value["permission_required"]
            or value["observed_session_ref"] is not None
        ):
            raise IntegrityError("active worker runtime has final process evidence")
        if value["phase"] == "finalized" and value["group_quiescent"] is not True:
            raise IntegrityError("finalized worker runtime must be group-quiescent")
        if value["phase"] == "recovery_required" and value["group_quiescent"] is None:
            raise IntegrityError(
                "recovery-required runtime needs a process-safety conclusion"
            )
    else:
        if any(
            value[field] is not None
            for field in {
                "session_generation",
                "launch_profile",
                "launch_profile_sha256",
                "launch_nonce",
                "supervisor_pid",
                "supervisor_start_id",
                "runner_pid",
                "runner_pgid",
                "runner_start_id",
                "agent_execution_started",
                "group_quiescent",
                "process_exit_code",
                "adapter_completed",
                "permission_required",
                "observed_session_ref",
                "termination_kind",
                "trace_manifest_sha256",
            }
        ):
            raise IntegrityError("origin runtime contains external process fields")
        deadline_before_claim = (
            seq is not None
            and value["role_id"] is not None
            and value["workspace_facts_before_sha256"] is None
            and (
                (value["phase"] == "finalized" and value["outcome"] == "cancelled")
                or is_deadline_before_claim_pending(value)
            )
        )
        if deadline_before_claim:
            if value["origin_claim_id"] is not None:
                raise IntegrityError(
                    "deadline-before-claim Origin runtime cannot contain a claim"
                )
        elif (
            not isinstance(value["origin_claim_id"], str)
            or not value["origin_claim_id"]
        ):
            raise IntegrityError("origin runtime requires a non-empty claim")
        if value["phase"] == "recovery_required":
            raise IntegrityError("Origin runtime cannot require process recovery")
        if value["role_id"] is None:
            if seq is not None:
                raise IntegrityError("management runtime cannot be a business turn")
            if (
                value["workspace_facts_before_sha256"] is not None
                or value["workspace_facts_after_sha256"] is not None
            ):
                raise IntegrityError(
                    "management runtime cannot contain workspace facts"
                )
        else:
            if seq is None or not isinstance(value["role_id"], str):
                raise IntegrityError(
                    "origin business runtime requires role and sequence"
                )
    if seq is not None:
        before_hash = value["workspace_facts_before_sha256"]
        if before_hash is not None and not _is_hash(before_hash):
            raise IntegrityError("runtime before facts hash is invalid")
        if before_hash is None and not (
            is_deadline_before_claim_pending(value)
            or (
                value["phase"] == "finalized"
                and value["outcome"] == "cancelled"
                and (
                    value["executor"] == "origin"
                    or value["termination_kind"] == "deadline"
                )
            )
        ):
            raise IntegrityError(
                "business runtime lacks Before Facts outside deadline-before-claim"
            )
    if value["workspace_facts_after_sha256"] is not None and not _is_hash(
        value["workspace_facts_after_sha256"]
    ):
        raise IntegrityError("runtime after facts hash is invalid")
    if value["phase"] in {"exited", "finalized", "recovery_required"} and (
        value["outcome"] is None or value["terminal_event_id"] is None
    ):
        raise IntegrityError("terminated runtime requires outcome and terminal event")
    if value["phase"] in {"starting", "running"} and value["outcome"] is not None:
        raise IntegrityError("active runtime cannot have an outcome")
    if (
        value["phase"] in {"starting", "running"}
        and value["terminal_event_id"] is not None
    ):
        raise IntegrityError("active runtime cannot have a terminal event")
    if value["terminal_event_id"] is not None and (
        not isinstance(value["terminal_event_id"], str)
        or not TERMINAL_EVENT_ID_RE.fullmatch(value["terminal_event_id"])
    ):
        raise IntegrityError("runtime terminal event id is invalid")
    if team is not None and value["role_id"] is not None:
        role = team.roles.get(value["role_id"])
        if role is None:
            raise IntegrityError("turn runtime references an unknown role")
        if value["executor"] == "worker" and role.binding != "external":
            raise IntegrityError("worker runtime references an Origin role")
        if value["executor"] == "origin" and role.binding != "origin":
            raise IntegrityError("origin runtime references an External role")
        if value["executor"] == "worker" and (
            value["launch_profile"] != role.launch_profile
            or value["launch_profile_sha256"] != role.launch_profile_sha256
        ):
            raise IntegrityError("worker runtime launch profile does not match role")
    created_at = parse_rfc3339(value["created_at"])
    updated_at = parse_rfc3339(value["updated_at"])
    if updated_at < created_at:
        raise IntegrityError("runtime updated_at precedes created_at")
    return value


def _validate_turn_input(turn_dir: Path, runtime: dict[str, Any]) -> None:
    input_path = turn_dir / "input.md"
    try:
        payload = read_regular(input_path)
    except FileNotFoundError as exc:
        raise IntegrityError(
            f"turn input is missing: {runtime['turn_id']}",
            f"turns/{runtime['turn_id']}/input.md",
        ) from exc
    if sha256_bytes(payload) != runtime["input_payload_sha256"]:
        raise IntegrityError(
            f"turn input hash mismatch: {runtime['turn_id']}",
            f"turns/{runtime['turn_id']}/input.md",
        )


def load_runtime(turn_dir: Path, *, team: Team | None = None) -> dict[str, Any]:
    value = read_json(turn_dir / "runtime.json")
    if value.get("turn_id") != turn_dir.name:
        raise IntegrityError(
            f"runtime turn id does not match directory: {turn_dir.name}"
        )
    runtime = validate_runtime(value, team=team)
    _validate_turn_input(turn_dir, runtime)
    return runtime


def save_runtime(
    turn_dir: Path,
    value: dict[str, Any],
    *,
    team: Team,
    initial: bool = False,
) -> None:
    path = turn_dir / "runtime.json"
    value["updated_at"] = rfc3339()
    validate_runtime(value, team=team)
    _validate_turn_input(turn_dir, value)
    if not initial and path_entry_exists(path):
        previous = validate_runtime(read_json(path), team=team)
        immutable_fields = {
            "schema_version",
            "turn_id",
            "business_turn_seq",
            "input_event_id",
            "input_payload_sha256",
            "role_id",
            "executor",
            "session_generation",
            "launch_profile",
            "launch_profile_sha256",
            "workspace_facts_before_sha256",
            "origin_claim_id",
            "created_at",
        }
        for field in immutable_fields:
            if previous[field] != value[field]:
                raise IntegrityError(f"runtime field is immutable: {field}")
        if previous["created_at"] != value["created_at"]:
            raise IntegrityError("runtime created_at is immutable")
        allowed_phases = {
            "starting": {
                "starting",
                "running",
                "exited",
                "finalized",
                "recovery_required",
            },
            "running": {"running", "exited", "finalized", "recovery_required"},
            "exited": {"exited", "finalized", "recovery_required"},
            "recovery_required": {"recovery_required", "finalized"},
            "finalized": {"finalized"},
        }
        if value["phase"] not in allowed_phases[previous["phase"]]:
            raise IntegrityError("runtime phase cannot move backward")
        for field in {
            "launch_nonce",
            "supervisor_pid",
            "supervisor_start_id",
            "runner_pid",
            "runner_pgid",
            "runner_start_id",
            "workspace_facts_after_sha256",
            "process_exit_code",
            "termination_kind",
            "observed_session_ref",
            "outcome",
            "trace_manifest_sha256",
        }:
            if previous[field] is not None and value[field] != previous[field]:
                raise IntegrityError(f"runtime field cannot change after set: {field}")
        for field in {
            "agent_execution_started",
            "adapter_completed",
            "permission_required",
        }:
            if previous[field] is True and value[field] is not True:
                raise IntegrityError(f"runtime evidence cannot regress: {field}")
        if previous["terminal_event_id"] is not None and (
            value["terminal_event_id"] != previous["terminal_event_id"]
        ):
            raise IntegrityError("runtime terminal event is immutable")
        if previous["group_quiescent"] is True and value["group_quiescent"] is not True:
            raise IntegrityError("runtime group quiescence cannot regress")
        if parse_rfc3339(value["updated_at"]) < parse_rfc3339(previous["updated_at"]):
            raise IntegrityError("runtime updated_at cannot move backward")
    atomic_json(path, value, immutable=initial)


def iter_turn_directories(run_dir: Path) -> list[Path]:
    root = run_dir / "turns"
    entries: list[tuple[int, Path]] = []
    for path in committed_directory_entries(root):
        if path.is_symlink() or not path.is_dir():
            raise IntegrityError(f"invalid turn directory: {path.name}")
        if not TURN_ID_RE.fullmatch(path.name):
            raise IntegrityError(f"invalid turn directory name: {path.name}")
        entries.append((int(path.name.removeprefix("turn-")), path))
    entries.sort(key=lambda item: item[0])
    for expected, (_, path) in enumerate(entries, start=1):
        if path.name != f"turn-{expected:04d}":
            raise IntegrityError("turn directory sequence has a gap or duplicate")
    return [path for _, path in entries]


def replace_damaged_runtime(
    turn_dir: Path,
    value: dict[str, Any],
    *,
    team: Team,
) -> None:
    """Replace a damaged Runtime only after a Recovery Event is authoritative."""
    validate_runtime(value, team=team)
    _validate_turn_input(turn_dir, value)
    atomic_json(turn_dir / "runtime.json", value)


def iter_runtimes(run_dir: Path, *, team: Team | None = None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in iter_turn_directories(run_dir):
        result.append(load_runtime(path, team=team))
    business = [
        item["business_turn_seq"] for item in result if item["business_turn_seq"]
    ]
    if business != list(range(1, len(business) + 1)):
        raise IntegrityError("business turn sequences are not contiguous")
    return result


def runtime_for_input(
    run_dir: Path,
    event_id: str,
    *,
    team: Team,
) -> dict[str, Any] | None:
    matches = [
        runtime
        for runtime in iter_runtimes(run_dir, team=team)
        if runtime["input_event_id"] == event_id
    ]
    if len(matches) > 1:
        raise IntegrityError(f"input event {event_id} has multiple turns")
    return matches[0] if matches else None


def active_runtime(
    run_dir: Path,
    *,
    team: Team,
) -> dict[str, Any] | None:
    active = [
        item
        for item in iter_runtimes(run_dir, team=team)
        if item["phase"] in {"starting", "running", "exited", "recovery_required"}
    ]
    if len(active) > 1:
        raise IntegrityError("run has multiple active turns")
    return active[0] if active else None


def _next_turn_id(run_dir: Path, *, team: Team) -> str:
    return f"turn-{len(iter_runtimes(run_dir, team=team)) + 1:04d}"


def _new_turn_staging_directory(run_dir: Path, turn_id: str) -> Path:
    path = run_dir / "turns" / (
        f".{turn_id}.tmp-{os.getpid()}-{secrets.token_hex(8)}"
    )
    path.mkdir(mode=0o700)
    return path


def _commit_turn_directory(staging: Path, target: Path) -> None:
    if path_entry_exists(target):
        raise IntegrityError(f"turn directory already exists: {target.name}")
    os.rename(staging, target)
    fsync_dir(target.parent)


def _base_runtime(
    *,
    turn_id: str,
    business_turn_seq: int | None,
    input_event: dict[str, Any],
    role_id: str | None,
    executor: str,
    session_generation: int | None,
    launch_profile: str | None,
    launch_profile_sha256: str | None,
    before_hash: str | None,
    claim: str | None,
) -> dict[str, Any]:
    now = rfc3339()
    return {
        "schema_version": 1,
        "turn_id": turn_id,
        "business_turn_seq": business_turn_seq,
        "input_event_id": input_event["event_id"],
        "input_payload_sha256": input_event["payload_sha256"],
        "role_id": role_id,
        "executor": executor,
        "phase": "starting",
        "outcome": None,
        "session_generation": session_generation,
        "launch_profile": launch_profile,
        "launch_profile_sha256": launch_profile_sha256,
        "launch_nonce": None,
        "supervisor_pid": None,
        "supervisor_start_id": None,
        "runner_pid": None,
        "runner_pgid": None,
        "runner_start_id": None,
        "agent_execution_started": False if executor == "worker" else None,
        "group_quiescent": None,
        "workspace_facts_before_sha256": before_hash,
        "workspace_facts_after_sha256": None,
        "process_exit_code": None,
        "adapter_completed": False if executor == "worker" else None,
        "permission_required": False if executor == "worker" else None,
        "observed_session_ref": None,
        "termination_kind": None,
        "terminal_event_id": None,
        "origin_claim_id": claim,
        "trace_manifest_sha256": None,
        "created_at": now,
        "updated_at": now,
    }


def validate_session(value: dict[str, Any], *, role: Role) -> dict[str, Any]:
    require_keys(value, required=SESSION_REQUIRED, subject="session snapshot")
    require_schema_version(value, 1, subject="session snapshot")
    if value["role_id"] != role.role_id:
        raise IntegrityError("session identity is invalid")
    if value["adapter"] != role.adapter:
        raise IntegrityError("session adapter mismatch")
    if (
        isinstance(value["generation"], bool)
        or not isinstance(value["generation"], int)
        or value["generation"] < 1
    ):
        raise IntegrityError("session generation is invalid")
    if not isinstance(value["status"], str) or value["status"] not in {
        "available",
        "unavailable",
    }:
        raise IntegrityError("session status is invalid")
    for field in {"created_turn_id", "updated_turn_id"}:
        if not isinstance(value[field], str) or not TURN_ID_RE.fullmatch(value[field]):
            raise IntegrityError(f"session {field} is invalid")
    if (
        value["effective_launch_profile"] != role.launch_profile
        or value["effective_launch_profile_sha256"] != role.launch_profile_sha256
    ):
        raise IntegrityError("session launch profile mismatch")
    if value["status"] == "available":
        if not isinstance(value["session_ref"], str) or not value["session_ref"]:
            raise IntegrityError("available session has no ref")
        if value["unavailable_reason"] is not None:
            raise IntegrityError("available session has unavailable reason")
    else:
        if value["session_ref"] is not None or not isinstance(
            value["unavailable_reason"], str
        ):
            raise IntegrityError("unavailable session fields are invalid")
        if not value["unavailable_reason"]:
            raise IntegrityError("unavailable session reason is empty")
    parse_rfc3339(value["updated_at"])
    return value


def load_session(run_dir: Path, role: Role) -> dict[str, Any] | None:
    path = run_dir / "sessions" / f"{role.role_id}.json"
    if not path_entry_exists(path):
        return None
    return validate_session(read_json(path), role=role)


def session_launch_state(
    run_dir: Path,
    role: Role,
) -> tuple[int, str | None]:
    current = load_session(run_dir, role)
    if role.session_policy == "fresh":
        return (current["generation"] + 1 if current else 1), None
    if current and current["status"] == "available":
        return current["generation"], current["session_ref"]
    return (current["generation"] + 1 if current else 1), None


def session_generation_for_route(
    run_dir: Path,
    role: Role,
    *,
    source_runtime: dict[str, Any] | None = None,
) -> int:
    """Return the generation whose private state a route must prepare.

    A Fresh role routed to itself stages its Handoff before its current
    Session snapshot is committed. Account for that in-flight generation so
    preflight prepares the next immutable artifact rather than rechecking the
    current one.
    """

    generation, _ = session_launch_state(run_dir, role)
    if (
        source_runtime is not None
        and source_runtime["role_id"] == role.role_id
        and role.session_policy == "fresh"
    ):
        generation = max(generation, source_runtime["session_generation"] + 1)
    return generation


def commit_session(
    run_dir: Path,
    *,
    role: Role,
    runtime: dict[str, Any],
    session_ref: str,
) -> dict[str, Any]:
    current = load_session(run_dir, role)
    generation = runtime["session_generation"]
    if current and current["generation"] > generation:
        raise IntegrityError("older turn cannot replace newer session generation")
    if current is None and generation != 1:
        raise IntegrityError("first session snapshot must use generation 1")
    if current and generation not in {
        current["generation"],
        current["generation"] + 1,
    }:
        raise IntegrityError("session generation skipped")
    if (
        current
        and current["status"] == "unavailable"
        and generation == current["generation"]
    ):
        raise IntegrityError(
            "an unavailable session cannot be revived within its generation"
        )
    if (
        current
        and current["generation"] == generation
        and current["status"] == "available"
        and current["session_ref"] != session_ref
    ):
        raise IntegrityError("session ref changed within a generation")
    created_turn = (
        current["created_turn_id"]
        if current and current["generation"] == generation
        else runtime["turn_id"]
    )
    value = {
        "schema_version": 1,
        "role_id": role.role_id,
        "adapter": role.adapter,
        "generation": generation,
        "status": "available",
        "session_ref": session_ref,
        "effective_launch_profile": role.launch_profile,
        "effective_launch_profile_sha256": role.launch_profile_sha256,
        "created_turn_id": created_turn,
        "updated_turn_id": runtime["turn_id"],
        "unavailable_reason": None,
        "updated_at": rfc3339(),
    }
    validate_session(value, role=role)
    atomic_json(run_dir / "sessions" / f"{role.role_id}.json", value)
    return value


def mark_session_unavailable(
    run_dir: Path,
    *,
    role: Role,
    runtime: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    if not reason:
        raise IntegrityError("session unavailable reason is empty")
    current = load_session(run_dir, role)
    if current is None:
        raise IntegrityError(
            "session-unavailable evidence requires a validated Session snapshot"
        )
    generation = runtime["session_generation"]
    if current["generation"] != generation or runtime["role_id"] != role.role_id:
        raise IntegrityError(
            "session-unavailable evidence does not match the launched generation"
        )
    if current["status"] == "unavailable":
        if (
            current["updated_turn_id"] == runtime["turn_id"]
            and current["unavailable_reason"] == reason
        ):
            return current
        raise IntegrityError("session was made unavailable by a different turn")
    value = {
        **current,
        "status": "unavailable",
        "session_ref": None,
        "updated_turn_id": runtime["turn_id"],
        "unavailable_reason": reason,
        "updated_at": rfc3339(),
    }
    validate_session(value, role=role)
    atomic_json(run_dir / "sessions" / f"{role.role_id}.json", value)
    return value


def record_candidate_activation_failure_session(
    run_dir: Path,
    *,
    role: Role,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    """Consume a Fresh Session generation that failed during candidate activation."""

    if role.binding != "external" or role.session_policy != "fresh":
        raise IntegrityError(
            "candidate activation failure requires a Fresh External role"
        )
    current = load_session(run_dir, role)
    generation = runtime["session_generation"]
    if current and current["generation"] > generation:
        raise IntegrityError("older turn cannot consume a newer session generation")
    if current is None and generation != 1:
        raise IntegrityError("first failed session generation must be generation 1")
    if current and current["generation"] == generation:
        if (
            current["status"] == "unavailable"
            and current["updated_turn_id"] == runtime["turn_id"]
            and current["unavailable_reason"] == "candidate_activation_failed"
        ):
            return current
        raise IntegrityError("session generation was already committed")
    if current and generation != current["generation"] + 1:
        raise IntegrityError("failed session generation skipped")
    value = {
        "schema_version": 1,
        "role_id": role.role_id,
        "adapter": role.adapter,
        "generation": generation,
        "status": "unavailable",
        "session_ref": None,
        "effective_launch_profile": role.launch_profile,
        "effective_launch_profile_sha256": role.launch_profile_sha256,
        "created_turn_id": runtime["turn_id"],
        "updated_turn_id": runtime["turn_id"],
        "unavailable_reason": "candidate_activation_failed",
        "updated_at": rfc3339(),
    }
    validate_session(value, role=role)
    atomic_json(run_dir / "sessions" / f"{role.role_id}.json", value)
    return value


def _copy_event_input(
    run_dir: Path,
    turn_dir: Path,
    event: dict[str, Any],
) -> None:
    source = resolve_run_path(run_dir, event["payload_path"])
    payload = read_regular(source)
    if sha256_bytes(payload) != event["payload_sha256"]:
        raise IntegrityError("input event payload changed before turn claim")
    atomic_write(turn_dir / "input.md", payload, immutable=True)


def create_business_turn_locked(
    run_dir: Path,
    *,
    role_id: str,
    executor: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """Claim the journal tail. Returns (runtime, continuity_error)."""
    projection = scan_journal(run_dir)
    team = projection.team
    event = projection.tail
    if (
        projection.status != "RUNNING"
        or projection.current_role != role_id
        or event is None
    ):
        return None, None
    existing = runtime_for_input(run_dir, event["event_id"], team=team)
    if existing is not None:
        return None, None
    if active_runtime(run_dir, team=team) is not None:
        return None, None
    role = team.roles[role_id]
    if (executor == "worker") != (role.binding == "external"):
        raise IntegrityError("turn executor does not match role binding")
    allowed, reason = can_create_business_turn(run_dir, projection)
    turn_id = _next_turn_id(run_dir, team=team)
    sequence = business_turn_count(run_dir) + 1
    turn_dir = run_dir / "turns" / turn_id
    staging = _new_turn_staging_directory(run_dir, turn_id)
    before: dict[str, Any] | None = None
    try:
        _copy_event_input(run_dir, staging, event)
        if not allowed:
            if reason != "deadline":
                raise IntegrityError(
                    "journal points to a turn that cannot be created"
                )
            deadline_generation = None
            if executor == "worker":
                deadline_generation, _ = session_launch_state(run_dir, role)
            runtime = _base_runtime(
                turn_id=turn_id,
                business_turn_seq=sequence,
                input_event=event,
                role_id=role_id,
                executor=executor,
                session_generation=deadline_generation,
                launch_profile=(
                    role.launch_profile if executor == "worker" else None
                ),
                launch_profile_sha256=(
                    role.launch_profile_sha256 if executor == "worker" else None
                ),
                before_hash=None,
                claim=None,
            )
        else:
            try:
                before = capture_workspace_facts(
                    team.workspace,
                    turn_id=turn_id,
                    boundary="before",
                )
                before_hash = write_workspace_facts(
                    staging / "workspace-facts-before.json",
                    before,
                )
            except (AgentTeamError, OSError) as exc:
                # The Journal already transferred the token, but no Runtime
                # exists that could legally author a technical Block. Commit
                # the incomplete Turn directory so every later reader derives
                # CORRUPTED instead of silently retrying a different snapshot.
                _commit_turn_directory(staging, turn_dir)
                raise IntegrityError(
                    f"Before Facts failed before Turn Runtime commit: {turn_id}"
                ) from exc
            if executor == "worker":
                generation, _ = session_launch_state(run_dir, role)
            else:
                generation = None
            runtime = _base_runtime(
                turn_id=turn_id,
                business_turn_seq=sequence,
                input_event=event,
                role_id=role_id,
                executor=executor,
                session_generation=generation,
                launch_profile=(
                    role.launch_profile if executor == "worker" else None
                ),
                launch_profile_sha256=(
                    role.launch_profile_sha256 if executor == "worker" else None
                ),
                before_hash=before_hash,
                claim=random_token() if executor == "origin" else None,
            )
        save_runtime(staging, runtime, team=team, initial=True)
        _commit_turn_directory(staging, turn_dir)
    finally:
        if path_entry_exists(staging):
            shutil.rmtree(staging)
    if not allowed:
        finalize_deadline_before_claim_locked(run_dir, runtime)
        return runtime, None
    assert before is not None
    continuity_error: str | None = None
    if event["event_type"] == "handoff" and event["turn_id"]:
        source_turn = run_dir / "turns" / event["turn_id"]
        source_runtime = load_runtime(source_turn, team=team)
        after_hash = source_runtime["workspace_facts_after_sha256"]
        if after_hash is None:
            continuity_error = "source turn has no trusted After Facts"
        else:
            after_path = source_turn / "workspace-facts-after.json"
            try:
                after = load_workspace_facts(
                    after_path,
                    expected_turn_id=event["turn_id"],
                    expected_boundary="after",
                )
                if sha256_bytes(read_regular(after_path)) != after_hash:
                    raise IntegrityError("source After Facts hash mismatch")
            except (IntegrityError, OSError) as exc:
                continuity_error = (
                    "source turn trusted After Facts are damaged; "
                    f"workspace continuity is unknown: {exc}"
                )
            if (
                continuity_error is None
                and not same_workspace_state(after, before)
            ):
                continuity_error = (
                    "workspace changed after the source handoff and before target claim"
                )
    return runtime, continuity_error


def finalize_deadline_before_claim_locked(
    run_dir: Path,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    if not is_deadline_before_claim_pending(runtime):
        raise IntegrityError("runtime is not a pending deadline-before-claim turn")
    projection = scan_journal(run_dir)
    kickoff = projection.kickoff
    if kickoff is None:
        raise IntegrityError("deadline-before-claim turn has no kickoff")
    deadline = parse_rfc3339(kickoff["created_at"]) + dt.timedelta(
        seconds=projection.team.max_wall_time_seconds
    )
    if dt.datetime.now(dt.timezone.utc) < deadline:
        raise IntegrityError("deadline-before-claim turn exists before the deadline")
    existing = projection.terminal_for_turn(runtime["turn_id"])
    if existing is not None and (
        existing["event_type"] != "block"
        or existing.get("block_reason") != "limit"
        or existing.get("limit_reason") != "deadline"
    ):
        raise IntegrityError(
            "deadline-before-claim turn has a conflicting terminal event"
        )
    event = existing or commit_technical_block_locked(
        run_dir,
        runtime=runtime,
        reason="limit",
        limit_reason="deadline",
        message="Wall-time deadline expired after token transfer and before claim.",
    )
    runtime["phase"] = "finalized"
    runtime["outcome"] = "cancelled"
    runtime["terminal_event_id"] = event["event_id"]
    if runtime["executor"] == "worker":
        runtime["group_quiescent"] = True
        runtime["termination_kind"] = "deadline"
    save_runtime(
        run_dir / "turns" / runtime["turn_id"],
        runtime,
        team=projection.team,
    )
    return event


def create_management_turn_locked(
    run_dir: Path,
    *,
    block_event: dict[str, Any],
) -> dict[str, Any]:
    team = load_team(run_dir)
    existing = runtime_for_input(run_dir, block_event["event_id"], team=team)
    if existing is not None:
        return existing
    if active_runtime(run_dir, team=team) is not None:
        raise AgentTeamError(
            "ORIGIN_TURN_ALREADY_CLAIMED",
            "an Origin turn is already active",
        )
    turn_id = _next_turn_id(run_dir, team=team)
    turn_dir = run_dir / "turns" / turn_id
    staging = _new_turn_staging_directory(run_dir, turn_id)
    try:
        _copy_event_input(run_dir, staging, block_event)
        runtime = _base_runtime(
            turn_id=turn_id,
            business_turn_seq=None,
            input_event=block_event,
            role_id=None,
            executor="origin",
            session_generation=None,
            launch_profile=None,
            launch_profile_sha256=None,
            before_hash=None,
            claim=random_token(),
        )
        runtime["phase"] = "running"
        save_runtime(staging, runtime, team=team, initial=True)
        _commit_turn_directory(staging, turn_dir)
    finally:
        if path_entry_exists(staging):
            shutil.rmtree(staging)
    return runtime


def render_turn_prompt(
    run_dir: Path,
    runtime: dict[str, Any],
    *,
    cli_path: str,
    session_ref: str | None,
    session_recovered_as_fresh: bool = False,
) -> str:
    team = load_team(run_dir)
    projection = scan_journal(run_dir)
    event = next(
        item
        for item in projection.events
        if item["event_id"] == runtime["input_event_id"]
    )
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    input_note = ""
    if event["event_type"] == "resume":
        input_note = (
            "\nThis turn's direct input is a Resume payload. Within the scope of the "
            "referenced Block, its user instruction outranks PROTOCOL.md and older "
            "handoffs, but it cannot change REQUEST.md or immutable run configuration.\n"
        )
    elif event.get("system_handoff_reason") == "candidate_activation_failed":
        input_note = (
            "\nThis turn's direct input is a structured Agent-Team system Handoff, "
            "not an action selected by the candidate-bound role. Inspect its frozen "
            "evidence and choose the next protocol-valid action; do not infer a "
            "product verdict from the system classification alone.\n"
        )
    recovery_note = ""
    if session_recovered_as_fresh:
        handoff_history = [
            item
            for item in projection.events
            if item["event_type"] == "handoff"
            and runtime["role_id"] in {item["from_role"], item["to_role"]}
        ]
        if handoff_history:
            history_lines = "\n".join(
                "- "
                f"`{item['event_id']}`: `{item['from_role']}` → "
                f"`{item['to_role']}`; payload "
                f"`{resolve_run_path(run_dir, item['payload_path'])}`"
                for item in handoff_history
            )
        else:
            history_lines = "- No prior Handoff involves this role."
        recovery_note = f"""
The previous Harness Session was structurally reported unavailable. This explicitly
authorized Resume is using a fresh Session generation; prior conversational context is
not assumed equivalent. Reconstruct only from the authoritative request, protocol,
current input, live worktree, and this role's Handoff index:

{history_lines}
"""
    payload_contract_note = ""
    if team.observability.required_payload_sections:
        headings = "\n".join(
            f"- `## {section}` with concrete, non-empty content"
            for section in team.observability.required_payload_sections
        )
        payload_contract_note = f"""
This Run enforces an audited formal-payload contract. Every Handoff, Completion,
or Agent Block payload must contain:

{headings}

Record only explicit rationale and evidence. Do not claim or reconstruct private
hidden chain-of-thought.
"""
    return f"""# Agent-Team role turn

You are the dynamic role `{runtime["role_id"]}` in Agent-Team run `{team.run_id}`.
Work only as that role. Read the authoritative inputs before acting:

- Original request: `{run_dir / "REQUEST.md"}`
- Team protocol: `{run_dir / "PROTOCOL.md"}`
- Current input event: `{event["event_type"]}` / `{event["event_id"]}`
- Frozen current input: `{turn_dir / "input.md"}`
- Before workspace facts: `{turn_dir / "workspace-facts-before.json"}`
- Turn directory: `{turn_dir}`
{input_note}
{recovery_note}
{payload_contract_note}
The live Git worktree is `{team.workspace}`. Verify it directly; a sender's claims are
not facts. Obey host system/developer/safety instructions and repository instructions
before Agent-Team material. Do not edit REQUEST.md, PROTOCOL.md, team.json, events,
runtime snapshots, or any file managed by Agent-Team. Do not add `.agent-team/` to Git.
Do not launch a daemon that escapes this Runner process group.

Finish this turn with exactly one formal action. The Agent-Team Skill is
documentation, not an action interface: it has no `--complete`, `--summary`, or
other terminal arguments. Do not invoke the Skill again to transition state.
First write a Markdown payload inside the turn directory, using the Handoff
sections in PROTOCOL.md where applicable. Then run exactly one of these CLI
commands:

- handoff: `{cli_path} handoff --to <role-id> --file <payload>`
- complete: `{cli_path} complete --file <payload>`
- block: `{cli_path} block --file <payload>`

After the command succeeds, stop business work and end the turn. Ordinary prose does
not transfer execution. Never call `cancel` unless the user explicitly requested it.
Current external session ref: {session_ref or "new session"}.
"""


def validate_outbox(value: dict[str, Any], *, turn_id: str) -> dict[str, Any]:
    require_keys(value, required=OUTBOX_REQUIRED, subject="turn outbox")
    require_schema_version(value, 1, subject="turn outbox")
    if value["turn_id"] != turn_id:
        raise IntegrityError("outbox identity is invalid")
    if not isinstance(value["action"], str) or value["action"] not in {
        "handoff",
        "complete",
        "block",
    }:
        raise IntegrityError("outbox action is invalid")
    if value["action"] == "handoff":
        if (
            not isinstance(value["to_role"], str)
            or not ROLE_ID_RE.fullmatch(value["to_role"])
            or value["block_reason"] is not None
        ):
            raise IntegrityError("handoff outbox fields are invalid")
    elif value["action"] == "block":
        if value["to_role"] is not None or value["block_reason"] != "agent":
            raise IntegrityError("block outbox fields are invalid")
    elif value["to_role"] is not None or value["block_reason"] is not None:
        raise IntegrityError("complete outbox fields are invalid")
    if not _is_hash(value["payload_sha256"]):
        raise IntegrityError("outbox payload hash is invalid")
    if value["payload_path"] != f"turns/{turn_id}/outbox-payload.md":
        raise IntegrityError("outbox payload path is invalid")
    parse_rfc3339(value["created_at"])
    return value


def load_outbox(turn_dir: Path) -> dict[str, Any] | None:
    path = turn_dir / "outbox.json"
    if not path_entry_exists(path):
        return None
    try:
        value = validate_outbox(read_json(path), turn_id=turn_dir.name)
        payload = resolve_run_path(turn_dir.parent.parent, value["payload_path"])
        if sha256_bytes(read_regular(payload)) != value["payload_sha256"]:
            raise IntegrityError("outbox payload hash mismatch")
    except (IntegrityError, OSError) as exc:
        if isinstance(exc, RecoverableTurnArtifactError):
            raise
        raise RecoverableTurnArtifactError(
            "outbox",
            f"Turn Outbox is damaged: {turn_dir.name}: {exc}",
            f"turns/{turn_dir.name}/outbox.json",
        ) from exc
    return value


def _technical_payload(
    runtime: dict[str, Any],
    *,
    reason: str,
    limit_reason: str | None,
    message: str,
) -> bytes:
    lines = [
        "# Agent-Team Technical Block",
        "",
        f"- Turn ID: {runtime['turn_id']}",
        f"- Role: {runtime['role_id']}",
        f"- Block reason: {reason}",
    ]
    if limit_reason:
        lines.append(f"- Limit reason: {limit_reason}")
    lines.extend(["", "## Details", "", message, ""])
    return "\n".join(lines).encode("utf-8")


def commit_technical_block_locked(
    run_dir: Path,
    *,
    runtime: dict[str, Any],
    reason: str,
    message: str,
    limit_reason: str | None = None,
    created_at: str | None = None,
    runtime_values: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    projection = scan_journal(run_dir, _runtime_values=runtime_values)
    existing = projection.terminal_for_turn(runtime["turn_id"])
    if existing is not None:
        return existing
    seq, _ = next_event_identity(projection, "block")
    relative = f"handoffs/{seq:04d}-{runtime['role_id'] or 'origin'}-technical-block.md"
    return commit_event(
        run_dir,
        event_type="block",
        payload_relative=relative,
        payload_bytes=_technical_payload(
            runtime,
            reason=reason,
            limit_reason=limit_reason,
            message=message,
        ),
        from_role=runtime["role_id"],
        to_role=None,
        turn_id=runtime["turn_id"],
        created_at=created_at,
        extra={"block_reason": reason, "limit_reason": limit_reason},
        _runtime_values=runtime_values,
    )


def stage_external_action_locked(
    run_dir: Path,
    *,
    runtime: dict[str, Any],
    action: str,
    source_file: Path,
    to_role: str | None,
) -> dict[str, Any]:
    team = load_team(run_dir)
    projection = scan_journal(run_dir)
    owner = read_owner(team.workspace)
    if owner is None or owner["run_id"] != team.run_id:
        raise IntegrityError("External action requires exact workspace ownership")
    if (
        projection.status != "RUNNING"
        or projection.current_role != runtime["role_id"]
        or runtime["executor"] != "worker"
    ):
        existing = projection.terminal_for_turn(runtime["turn_id"])
        if existing:
            return {"code": "TURN_ALREADY_FINALIZED", "event": existing}
        raise AgentTeamError("TOKEN_NOT_OWNED", "current turn does not own the token")
    if runtime["phase"] not in {"starting", "running"}:
        raise AgentTeamError("TURN_NOT_ACTIVE", "turn is not active")
    if action not in {"handoff", "complete", "block"}:
        raise InvalidArgument(f"unsupported action: {action}")
    if action == "handoff":
        if to_role not in team.roles:
            raise AgentTeamError(
                "ROLE_NOT_FOUND", f"target role {to_role!r} does not exist"
            )
    elif to_role is not None:
        raise InvalidArgument(f"{action} does not accept a target role")
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    frozen_input = read_regular(turn_dir / "input.md")
    if sha256_bytes(frozen_input) != runtime["input_payload_sha256"]:
        raise IntegrityError("frozen Turn input changed before External action")
    source_relative = safe_relative(source_file, run_dir)
    source_bytes = read_private_regular(
        resolve_run_path(run_dir, source_relative)
    )
    validate_payload_contract(
        source_bytes,
        required_sections=team.observability.required_payload_sections,
    )
    payload_hash = sha256_bytes(source_bytes)
    requested = {
        "action": action,
        "to_role": to_role if action == "handoff" else None,
        "block_reason": "agent" if action == "block" else None,
        "payload_sha256": payload_hash,
    }
    existing = load_outbox(turn_dir)
    if existing:
        observed = {key: existing[key] for key in requested}
        if observed == requested:
            return {"code": "ACTION_ALREADY_ACCEPTED", "outbox": existing}
        raise AgentTeamError(
            "TURN_ACTION_CONFLICT",
            "this turn already staged a different terminal action",
        )
    kickoff = projection.kickoff
    assert kickoff is not None
    deadline = parse_rfc3339(kickoff["created_at"]) + dt.timedelta(
        seconds=team.max_wall_time_seconds
    )
    if dt.datetime.now(dt.timezone.utc) >= deadline:
        event = commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="limit",
            limit_reason="deadline",
            message="The run wall-time deadline expired before the action was accepted.",
        )
        return {"code": "TEAM_BLOCKED", "event": event}
    if action == "handoff":
        if runtime["business_turn_seq"] >= team.max_turns:
            event = commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="limit",
                limit_reason="max_turns",
                message="The current turn is the final allowed business turn.",
            )
            return {"code": "TEAM_BLOCKED", "event": event}
        target = team.roles[to_role or ""]
        if target.binding == "external":
            adapter = get_adapter(target.adapter or "")
            try:
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
                    event = commit_technical_block_locked(
                        run_dir,
                        runtime=runtime,
                        reason="limit",
                        limit_reason="deadline",
                        message=(
                            "The run wall-time deadline expired while validating "
                            "the target launch profile."
                        ),
                        created_at=rfc3339(after_probe),
                    )
                    return {"code": "TEAM_BLOCKED", "event": event}
                if isinstance(exc, RoutePreflightError):
                    raise AgentTeamError(
                        "ROUTE_PREFLIGHT_REJECTED",
                        f"target role {target.role_id!r} cannot be activated: "
                        f"{exc.code}: {exc.message}. No Outbox or Handoff Event "
                        "was staged; the current Turn still owns the token and "
                        "may select another Protocol-valid route.",
                    ) from exc
                event = commit_technical_block_locked(
                    run_dir,
                    runtime=runtime,
                    reason="profile_changed",
                    message=exc.message,
                    created_at=rfc3339(after_probe),
                )
                return {"code": "TEAM_BLOCKED", "event": event}
    if dt.datetime.now(dt.timezone.utc) >= deadline:
        event = commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="limit",
            limit_reason="deadline",
            message="The run wall-time deadline expired before Outbox staging.",
        )
        return {"code": "TEAM_BLOCKED", "event": event}
    payload_relative = f"turns/{runtime['turn_id']}/outbox-payload.md"
    atomic_write(turn_dir / "outbox-payload.md", source_bytes, immutable=True)
    outbox = {
        "schema_version": 1,
        "turn_id": runtime["turn_id"],
        "action": action,
        "to_role": to_role if action == "handoff" else None,
        "block_reason": "agent" if action == "block" else None,
        "payload_path": payload_relative,
        "payload_sha256": payload_hash,
        "created_at": rfc3339(),
    }
    validate_outbox(outbox, turn_id=runtime["turn_id"])
    atomic_json(turn_dir / "outbox.json", outbox, immutable=True)
    return {"code": "ACTION_ACCEPTED", "outbox": outbox}


def _system_facts_markdown(
    run_dir: Path,
    runtime: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
) -> bytes:
    team = load_team(run_dir)
    role = team.roles[runtime["role_id"]]
    lines = [
        "",
        "---",
        "",
        "## Agent-Team System Facts",
        "",
        f"- Run ID: {team.run_id}",
        f"- Turn ID: {runtime['turn_id']}",
        f"- From role: {runtime['role_id']}",
        f"- Harness: {role.adapter or 'origin'}",
        f"- Harness session: {runtime['observed_session_ref'] or 'unavailable'}",
        f"- Session generation: {runtime['session_generation']}",
        f"- Effective launch profile: {runtime['launch_profile']}",
        f"- Effective launch profile SHA-256: {runtime['launch_profile_sha256']}",
        f"- Turn started at: {runtime['created_at']}",
        f"- Turn ended at: {after['captured_at']}",
        f"- Process exit code: {runtime['process_exit_code']}",
        f"- Adapter completed: {str(runtime['adapter_completed']).lower()}",
        f"- Termination kind: {runtime['termination_kind']}",
        f"- Recorded runner PGID quiescent: {str(runtime['group_quiescent']).lower()}",
        f"- Git HEAD before: {before['git_head'] or 'unborn'}",
        f"- Git HEAD after: {after['git_head'] or 'unborn'}",
        f"- Git-visible workspace state SHA-256 before: {before['workspace_state_sha256']}",
        f"- Git-visible workspace state SHA-256 after: {after['workspace_state_sha256']}",
        f"- Git diff stat: {after['diff_stat'] or 'clean'}",
        f"- Full log: turns/{runtime['turn_id']}/process/stream.jsonl",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _automatic_continuation_payload(
    runtime: dict[str, Any],
    *,
    reason: str,
    session_policy: str,
    next_session_generation: int,
) -> bytes:
    if session_policy == "resume":
        continuity = (
            "The next Turn reuses the exact available Harness Session and its "
            "durable conversational context."
        )
    elif session_policy == "fresh":
        continuity = (
            "The role's Fresh policy creates a new Session generation. Reconstruct "
            "unfinished work only from the authoritative Request, Protocol, current "
            "input, preserved trace, and live worktree; do not assume hidden context "
            "from the exhausted Session."
        )
    else:
        raise IntegrityError("automatic continuation has invalid Session policy")
    return (
        "# Agent-Team Automatic Continuation\n\n"
        f"- From: {runtime['role_id']}\n"
        f"- To: {runtime['role_id']}\n"
        f"- Reason: {reason}\n\n"
        "## Requested next action\n\n"
        "The preceding Harness invocation reached its explicit output budget "
        "before it could submit a formal action. Inspect the live worktree and "
        "the preserved Session, continue the same role responsibility without "
        "redoing completed work, and finish this new Turn with exactly one "
        "formal handoff, complete, or block action.\n\n"
        "## Decision rationale\n\n"
        "Agent-Team proved a dedicated recoverable Harness termination, a "
        "quiescent process group, and a durably initialized Session. Continuing "
        "the same role consumes another configured business Turn and grants no "
        f"new authority. {continuity}\n\n"
        "## Evidence\n\n"
        f"- Previous Turn: {runtime['turn_id']}\n"
        f"- Termination kind: {runtime['termination_kind']}\n"
        f"- Process exit code: {runtime['process_exit_code']}\n"
        f"- Exhausted Session generation: {runtime['session_generation']}\n"
        f"- Session ref recorded: {runtime['observed_session_ref']}\n"
        f"- Session policy: {session_policy}\n"
        f"- Next Session generation: {next_session_generation}\n"
    ).encode()


def _candidate_activation_payload(
    runtime: dict[str, Any],
    *,
    to_role: str,
    failure: str,
) -> bytes:
    return (
        "# Agent-Team Candidate Activation Finding\n\n"
        "- Generated by: Agent-Team\n"
        f"- From: {runtime['role_id']}\n"
        f"- To: {to_role}\n"
        "- Reason: candidate_activation_failed\n\n"
        "## Requested next action\n\n"
        "The candidate-bound Harness exited before the assigned role obtained "
        "a usable Session. Inspect the preserved candidate and Turn diagnostics. "
        "Choose the next Protocol-valid action from that evidence. If it proves "
        "that no valid route can make progress, submit a Block; otherwise hand "
        "off the concrete finding to a capable role. Do not claim that validation "
        "passed.\n\n"
        "## Decision rationale\n\n"
        "Agent-Team proved that the Runner group is quiescent, a frozen candidate "
        "generation was bound to this role, and the Harness failed before its "
        "Session was durably initialized. Candidate semantics remain the team's "
        "responsibility; Agent-Team did not parse terminal prose or duplicate the "
        "Harness's plugin rules.\n\n"
        "## Evidence\n\n"
        f"- Structural classification: {failure}\n"
        f"- Failed Turn: {runtime['turn_id']}\n"
        f"- Candidate Session generation: {runtime['session_generation']}\n"
        f"- Process exit code: {runtime['process_exit_code']}\n"
        f"- Termination kind: {runtime['termination_kind']}\n"
        f"- Full trace: turns/{runtime['turn_id']}/process/stream.jsonl\n"
    ).encode()


def deliver_outbox_locked(
    run_dir: Path,
    *,
    runtime: dict[str, Any],
    allow_after_capture: bool = False,
    automatic_continuation_reason: str | None = None,
    candidate_activation_failure: str | None = None,
    candidate_activation_return_role: str | None = None,
) -> dict[str, Any]:
    team = load_team(run_dir)
    turn_dir = run_dir / "turns" / runtime["turn_id"]
    outbox = load_outbox(turn_dir)
    if (
        automatic_continuation_reason is not None
        and automatic_continuation_reason not in AUTOMATIC_CONTINUATION_REASONS
    ):
        raise IntegrityError("unsupported automatic continuation reason")
    automatic_continuation = automatic_continuation_reason is not None
    candidate_activation_handoff = candidate_activation_failure is not None
    if candidate_activation_handoff != (
        candidate_activation_return_role is not None
    ):
        raise IntegrityError("candidate activation Handoff fields are incomplete")
    if automatic_continuation and candidate_activation_handoff:
        raise IntegrityError("automatic Handoff modes conflict")
    if candidate_activation_handoff and (
        candidate_activation_return_role not in team.roles
        or candidate_activation_return_role == runtime["role_id"]
    ):
        raise IntegrityError("candidate activation Handoff target is invalid")
    if (
        outbox is not None
        and outbox["action"] == "handoff"
        and outbox["to_role"] not in team.roles
    ):
        raise RecoverableTurnArtifactError(
            "outbox",
            "outbox references an unknown Handoff target",
            f"turns/{runtime['turn_id']}/outbox.json",
        )
    pending_payload = turn_dir / "outbox-payload.md"
    has_orphaned_payload = outbox is None and path_entry_exists(pending_payload)
    if has_orphaned_payload:
        try:
            read_regular(pending_payload)
        except OSError as exc:
            raise RecoverableTurnArtifactError(
                "outbox",
                f"orphaned Outbox payload is unreadable: {exc}",
                f"turns/{runtime['turn_id']}/outbox-payload.md",
            ) from exc
    if automatic_continuation and outbox is not None:
        return commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="recovery",
            message=(
                "Harness reported a recoverable output limit after a formal "
                "Outbox was already staged. Agent-Team cannot guess whether to "
                "deliver the action or continue the interrupted role."
            ),
        )
    if candidate_activation_handoff and outbox is not None:
        return commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="recovery",
            message=(
                "Candidate activation failed after a formal Outbox was already "
                "staged. Agent-Team cannot replace the role's chosen action with "
                "a system Handoff."
            ),
        )
    if automatic_continuation:
        role = team.roles[runtime["role_id"]]
        session = load_session(run_dir, role)
        if (
            role.binding != "external"
            or session is None
            or session["status"] != "available"
            or session["generation"] != runtime["session_generation"]
            or session["session_ref"] != runtime["observed_session_ref"]
        ):
            return commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=(
                    "Harness reported a recoverable output limit, but the exact "
                    "exhausted role Session is not durably available."
                ),
            )

    def delivery_guard() -> tuple[dict[str, Any] | None, str | None]:
        projection = scan_journal(run_dir)
        existing = projection.terminal_for_turn(runtime["turn_id"])
        if existing is not None:
            return existing, None
        kickoff = projection.kickoff
        if kickoff is None:
            raise IntegrityError("cannot deliver an outbox before kickoff")
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
                    message="The run wall-time deadline expired before Outbox delivery.",
                ),
                None,
            )
        if automatic_continuation or candidate_activation_handoff or (
            outbox is not None and outbox["action"] == "handoff"
        ):
            allowed, reason = can_create_business_turn(
                run_dir,
                projection,
                now=now,
            )
            if not allowed:
                if reason != "max_turns":
                    raise IntegrityError(
                        f"unexpected Handoff delivery guard result: {reason}"
                    )
                return (
                    commit_technical_block_locked(
                        run_dir,
                        runtime=runtime,
                        reason="limit",
                        limit_reason="max_turns",
                        message="The current turn is the final allowed business turn.",
                    ),
                    None,
                )
            target_role_id = (
                runtime["role_id"]
                if automatic_continuation
                else (
                    candidate_activation_return_role
                    if candidate_activation_handoff
                    else outbox["to_role"]
                )
            )
            target = team.roles[target_role_id]
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
                                    "The run wall-time deadline expired while "
                                    "validating the target launch profile."
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
        # A potentially slow profile probe ran above. Make the commit decision
        # against a fresh wall clock and freeze that exact timestamp.
        decision = dt.datetime.now(dt.timezone.utc)
        if decision >= deadline:
            return (
                commit_technical_block_locked(
                    run_dir,
                    runtime=runtime,
                    reason="limit",
                    limit_reason="deadline",
                    message="The run wall-time deadline expired before Outbox delivery.",
                ),
                None,
            )
        return None, rfc3339(decision)

    guarded, _ = delivery_guard()
    if guarded is not None:
        return guarded
    after_path = turn_dir / "workspace-facts-after.json"
    persisted_runtime = load_runtime(turn_dir, team=team)
    recovered_uncommitted_after = False
    if path_entry_exists(after_path):
        try:
            after = load_workspace_facts(
                after_path,
                expected_turn_id=runtime["turn_id"],
                expected_boundary="after",
            )
            after_hash = sha256_bytes(read_regular(after_path))
        except (IntegrityError, OSError) as exc:
            raise RecoverableTurnArtifactError(
                "workspace_facts",
                f"After Facts are damaged: {runtime['turn_id']}: {exc}",
                f"turns/{runtime['turn_id']}/workspace-facts-after.json",
            ) from exc
        expected_after_hash = persisted_runtime["workspace_facts_after_sha256"]
        if expected_after_hash is None:
            persisted_runtime["workspace_facts_after_sha256"] = after_hash
            save_runtime(turn_dir, persisted_runtime, team=team)
            recovered_uncommitted_after = True
        elif expected_after_hash != after_hash:
            raise RecoverableTurnArtifactError(
                "workspace_facts",
                "After Facts hash mismatch during delivery",
                f"turns/{runtime['turn_id']}/workspace-facts-after.json",
            )
    else:
        if persisted_runtime["workspace_facts_after_sha256"] is not None:
            raise RecoverableTurnArtifactError(
                "workspace_facts",
                "Runtime references missing After Facts",
                f"turns/{runtime['turn_id']}/workspace-facts-after.json",
            )
        if not allow_after_capture:
            guarded, decision_at = delivery_guard()
            if guarded is not None:
                return guarded
            assert decision_at is not None
            return commit_technical_block_locked(
                run_dir,
                runtime=runtime,
                reason="recovery",
                message=(
                    "Harness completion was recovered without frozen After Facts. "
                    "The current Workspace cannot be substituted for the missing "
                    "historical boundary."
                ),
                created_at=decision_at,
            )
        after = capture_workspace_facts(
            team.workspace,
            turn_id=runtime["turn_id"],
            boundary="after",
        )
        after_hash = write_workspace_facts(after_path, after)
        persisted_runtime["workspace_facts_after_sha256"] = after_hash
        save_runtime(turn_dir, persisted_runtime, team=team)
    runtime["workspace_facts_after_sha256"] = after_hash
    before_path = turn_dir / "workspace-facts-before.json"
    try:
        before = load_workspace_facts(
            before_path,
            expected_turn_id=runtime["turn_id"],
            expected_boundary="before",
        )
        if (
            sha256_bytes(read_regular(before_path))
            != runtime["workspace_facts_before_sha256"]
        ):
            raise IntegrityError("Before Facts hash mismatch during delivery")
    except (IntegrityError, OSError) as exc:
        raise RecoverableTurnArtifactError(
            "workspace_facts",
            f"Before Facts are damaged: {runtime['turn_id']}: {exc}",
            f"turns/{runtime['turn_id']}/workspace-facts-before.json",
        ) from exc
    guarded, decision_at = delivery_guard()
    if guarded is not None:
        return guarded
    assert decision_at is not None
    if recovered_uncommitted_after:
        return commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="recovery",
            message=(
                "After Facts existed without its Runtime hash after a crash. "
                "The frozen snapshot was preserved, but the Outbox was not delivered."
            ),
            created_at=decision_at,
        )
    if has_orphaned_payload:
        return commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="recovery",
            message=(
                "An Outbox payload was frozen, but its action metadata was "
                "not atomically committed before the producer stopped. The "
                "payload is preserved for audit and cannot be routed safely."
            ),
            created_at=decision_at,
        )
    if candidate_activation_handoff:
        assert candidate_activation_failure is not None
        assert candidate_activation_return_role is not None
        payload = _candidate_activation_payload(
            runtime,
            to_role=candidate_activation_return_role,
            failure=candidate_activation_failure,
        )
        payload += _system_facts_markdown(run_dir, runtime, before, after)
        guarded, decision_at = delivery_guard()
        if guarded is not None:
            return guarded
        assert decision_at is not None
        projection = scan_journal(run_dir)
        seq, _ = next_event_identity(projection, "handoff")
        relative = (
            f"handoffs/{seq:04d}-{runtime['role_id']}-candidate-activation-"
            f"to-{candidate_activation_return_role}.md"
        )
        return commit_event(
            run_dir,
            event_type="handoff",
            payload_relative=relative,
            payload_bytes=payload,
            from_role=runtime["role_id"],
            to_role=candidate_activation_return_role,
            turn_id=runtime["turn_id"],
            created_at=decision_at,
            extra={"system_handoff_reason": "candidate_activation_failed"},
        )
    if outbox is None and not automatic_continuation:
        return commit_technical_block_locked(
            run_dir,
            runtime=runtime,
            reason="no_action",
            message="Harness exited normally without handoff, complete, or block.",
            created_at=decision_at,
        )
    if automatic_continuation:
        role = team.roles[runtime["role_id"]]
        next_generation = session_generation_for_route(
            run_dir,
            role,
            source_runtime=runtime,
        )
        payload = _automatic_continuation_payload(
            runtime,
            reason=automatic_continuation_reason or "",
            session_policy=role.session_policy or "",
            next_session_generation=next_generation,
        )
        payload += _system_facts_markdown(run_dir, runtime, before, after)
        guarded, decision_at = delivery_guard()
        if guarded is not None:
            return guarded
        assert decision_at is not None
        projection = scan_journal(run_dir)
        seq, _ = next_event_identity(projection, "handoff")
        relative = (
            f"handoffs/{seq:04d}-{runtime['role_id']}-automatic-continuation.md"
        )
        return commit_event(
            run_dir,
            event_type="handoff",
            payload_relative=relative,
            payload_bytes=payload,
            from_role=runtime["role_id"],
            to_role=runtime["role_id"],
            turn_id=runtime["turn_id"],
            created_at=decision_at,
            extra={"continuation_reason": automatic_continuation_reason},
        )
    try:
        payload = read_regular(resolve_run_path(run_dir, outbox["payload_path"]))
        if sha256_bytes(payload) != outbox["payload_sha256"]:
            raise IntegrityError("Outbox payload changed during delivery")
    except (IntegrityError, OSError) as exc:
        raise RecoverableTurnArtifactError(
            "outbox",
            f"Outbox payload is damaged: {runtime['turn_id']}: {exc}",
            outbox["payload_path"],
        ) from exc
    payload += _system_facts_markdown(run_dir, runtime, before, after)
    guarded, decision_at = delivery_guard()
    if guarded is not None:
        return guarded
    assert decision_at is not None
    action = outbox["action"]
    projection = scan_journal(run_dir)
    seq, _ = next_event_identity(projection, action if action != "block" else "block")
    if action == "handoff":
        relative = f"handoffs/{seq:04d}-{runtime['role_id']}-to-{outbox['to_role']}.md"
        extra: dict[str, Any] = {}
        to_role = outbox["to_role"]
    elif action == "complete":
        relative = f"completion/{seq:04d}-{runtime['role_id']}.md"
        extra = {}
        to_role = None
    else:
        relative = f"handoffs/{seq:04d}-{runtime['role_id']}-agent-block.md"
        extra = {"block_reason": "agent", "limit_reason": None}
        to_role = None
    return commit_event(
        run_dir,
        event_type=action,
        payload_relative=relative,
        payload_bytes=payload,
        from_role=runtime["role_id"],
        to_role=to_role,
        turn_id=runtime["turn_id"],
        created_at=decision_at,
        extra=extra,
    )

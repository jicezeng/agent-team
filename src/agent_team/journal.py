from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Team, load_team, parse_team
from .errors import IntegrityError
from .state import validate_state_root
from .util import (
    atomic_json,
    atomic_write,
    parse_rfc3339,
    read_json,
    read_regular,
    require_keys,
    resolve_run_path,
    rfc3339,
    sha256_bytes,
    is_uncommitted_atomic_temporary,
)


EVENT_TYPES = {"kickoff", "handoff", "complete", "block", "resume", "cancel"}
BLOCK_REASONS = {
    "agent",
    "limit",
    "profile_changed",
    "recovery",
    "start_failure",
    "no_action",
    "permission",
}
EVENT_COMMON = {
    "schema_version",
    "event_id",
    "event_seq",
    "prev_event_id",
    "event_type",
    "from_role",
    "to_role",
    "turn_id",
    "payload_path",
    "payload_sha256",
    "created_at",
}
EVENT_OPTIONAL = {
    "request_sha256",
    "protocol_sha256",
    "team_sha256",
    "block_reason",
    "limit_reason",
    "request_id",
    "cancel_reason",
}
EVENT_FILE_RE = re.compile(r"^(\d+)-([a-z]+-\d+)\.json$")
TURN_ID_RE = re.compile(r"^turn-\d{4,}$")


@dataclass(frozen=True, slots=True)
class JournalProjection:
    team: Team
    events: tuple[dict[str, Any], ...]
    status: str
    current_role: str | None

    @property
    def tail(self) -> dict[str, Any] | None:
        return self.events[-1] if self.events else None

    @property
    def kickoff(self) -> dict[str, Any] | None:
        return self.events[0] if self.events else None

    def terminal_for_turn(self, turn_id: str) -> dict[str, Any] | None:
        for event in self.events:
            if event.get("turn_id") == turn_id and event["event_type"] in {
                "handoff",
                "complete",
                "block",
                "resume",
                "cancel",
            }:
                return event
        return None


def _event_path(run_dir: Path, event: dict[str, Any]) -> Path:
    return run_dir / "events" / (
        f"{event['event_seq']:04d}-{event['event_id']}.json"
    )


def _validate_hash(value: Any, subject: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(char not in "0123456789abcdef" for char in value)
    ):
        raise IntegrityError(f"{subject} is not a SHA-256")
    return value


def _validate_event_schema(event: dict[str, Any], path: Path) -> None:
    require_keys(
        event,
        required=EVENT_COMMON,
        optional=EVENT_OPTIONAL,
        subject=f"event {path.name}",
    )
    if event["schema_version"] != 1:
        raise IntegrityError(f"unsupported event schema: {path.name}")
    if (
        not isinstance(event["event_type"], str)
        or event["event_type"] not in EVENT_TYPES
    ):
        raise IntegrityError(f"invalid event type: {path.name}")
    type_fields = set(event) - EVENT_COMMON
    expected_fields: dict[str, set[str]] = {
        "kickoff": {"request_sha256", "protocol_sha256", "team_sha256"},
        "handoff": set(),
        "complete": set(),
        "block": {"block_reason", "limit_reason"},
        "resume": set(),
        "cancel": {"request_id", "cancel_reason"},
    }
    if type_fields != expected_fields[event["event_type"]]:
        raise IntegrityError(
            f"invalid type-specific fields in {path.name}: "
            f"{sorted(type_fields)}"
        )
    seq = event["event_seq"]
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise IntegrityError(f"invalid event sequence: {path.name}")
    expected_id = f"{event['event_type']}-{seq:04d}"
    if event["event_id"] != expected_id:
        raise IntegrityError(f"invalid event id in {path.name}")
    if path.name != f"{seq:04d}-{expected_id}.json":
        raise IntegrityError(f"event filename does not match content: {path.name}")
    if event["prev_event_id"] is not None and not isinstance(
        event["prev_event_id"], str
    ):
        raise IntegrityError(f"invalid prev_event_id in {path.name}")
    if event["from_role"] is not None and not isinstance(event["from_role"], str):
        raise IntegrityError(f"invalid from_role in {path.name}")
    if event["to_role"] is not None and not isinstance(event["to_role"], str):
        raise IntegrityError(f"invalid to_role in {path.name}")
    if event["turn_id"] is not None and (
        not isinstance(event["turn_id"], str)
        or not TURN_ID_RE.fullmatch(event["turn_id"])
    ):
        raise IntegrityError(f"invalid turn_id in {path.name}")
    if not isinstance(event["payload_path"], str) or not event["payload_path"]:
        raise IntegrityError(f"invalid payload path in {path.name}")
    _validate_hash(event["payload_sha256"], f"{path.name} payload hash")
    parse_rfc3339(event["created_at"])


def _validate_payload(run_dir: Path, event: dict[str, Any]) -> None:
    payload = resolve_run_path(run_dir, event["payload_path"])
    try:
        raw = read_regular(payload)
    except FileNotFoundError as exc:
        raise IntegrityError(
            f"event payload is missing: {event['payload_path']}",
            event["payload_path"],
        ) from exc
    if sha256_bytes(raw) != event["payload_sha256"]:
        raise IntegrityError(
            f"event payload hash mismatch: {event['payload_path']}",
            event["payload_path"],
        )


def _turn_runtime(run_dir: Path, turn_id: str) -> dict[str, Any]:
    path = run_dir / "turns" / turn_id / "runtime.json"
    try:
        # Local import avoids a module cycle while ensuring Journal validation
        # never trusts a partially parsed Runtime.
        from .turns import load_runtime

        value = load_runtime(path.parent, team=load_team(run_dir))
    except FileNotFoundError as exc:
        raise IntegrityError(f"event references missing turn: {turn_id}") from exc
    if value.get("turn_id") != turn_id:
        raise IntegrityError(f"turn runtime id mismatch: {turn_id}")
    return value


def business_turn_count(run_dir: Path) -> int:
    from .turns import iter_runtimes

    team = load_team(run_dir)
    runtimes = iter_runtimes(run_dir, team=team)
    return sum(item["business_turn_seq"] is not None for item in runtimes)


def _validate_transition(
    run_dir: Path,
    team: Team,
    event: dict[str, Any],
    *,
    state: str,
    owner: str | None,
    previous: dict[str, Any] | None,
    kickoff: dict[str, Any] | None,
    terminal_turns: set[str],
    business_turns_before: int,
    runtime_by_turn: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str | None]:
    def referenced_runtime(referenced_turn_id: str) -> dict[str, Any]:
        if runtime_by_turn is None:
            return _turn_runtime(run_dir, referenced_turn_id)
        try:
            return runtime_by_turn[referenced_turn_id]
        except KeyError as exc:
            raise IntegrityError(
                f"event references missing turn: {referenced_turn_id}"
            ) from exc

    event_type = event["event_type"]
    from_role = event["from_role"]
    to_role = event["to_role"]
    turn_id = event["turn_id"]
    if from_role is not None and from_role not in team.roles:
        raise IntegrityError(f"event references unknown from_role: {from_role}")
    if to_role is not None and to_role not in team.roles:
        raise IntegrityError(f"event references unknown to_role: {to_role}")
    if event_type == "kickoff":
        if state != "UNSTARTED" or previous is not None:
            raise IntegrityError("kickoff is only valid for an unstarted run")
        if (
            from_role is not None
            or to_role != team.initial_role
            or turn_id is not None
        ):
            raise IntegrityError("kickoff routing fields are invalid")
        for field in {"request_sha256", "protocol_sha256", "team_sha256"}:
            _validate_hash(event.get(field), f"kickoff {field}")
        return "RUNNING", to_role
    if previous is None or kickoff is None:
        raise IntegrityError("non-kickoff event has no kickoff")
    if event["prev_event_id"] != previous["event_id"]:
        raise IntegrityError("event prev_event_id does not match journal tail")
    deadline = parse_rfc3339(kickoff["created_at"]) + dt.timedelta(
        seconds=team.max_wall_time_seconds
    )
    created = parse_rfc3339(event["created_at"])
    if created < parse_rfc3339(previous["created_at"]):
        raise IntegrityError("event timestamp moves backward")
    if event_type == "cancel":
        if state not in {"RUNNING", "BLOCKED"}:
            raise IntegrityError("cancel is only valid for running or blocked runs")
        if runtime_by_turn is None:
            from .turns import iter_runtimes

            transition_runtimes = iter_runtimes(run_dir, team=team)
        else:
            transition_runtimes = list(runtime_by_turn.values())
        current_claims = [
            runtime
            for runtime in transition_runtimes
            if runtime["input_event_id"] == previous["event_id"]
        ]
        if len(current_claims) > 1:
            raise IntegrityError("cancel input event has multiple active turns")
        expected_cancel_turn = (
            current_claims[0]["turn_id"] if current_claims else None
        )
        if (
            from_role != (owner if state == "RUNNING" else None)
            or to_role is not None
            or turn_id != expected_cancel_turn
            or event.get("cancel_reason") != "user"
            or not isinstance(event.get("request_id"), str)
            or not event["request_id"]
        ):
            raise IntegrityError("cancel fields are invalid")
        if turn_id is not None:
            runtime = referenced_runtime(turn_id)
            if state == "RUNNING" and runtime.get("role_id") != owner:
                raise IntegrityError("cancel turn does not belong to token owner")
            if state == "BLOCKED" and (
                runtime.get("executor") != "origin"
                or runtime.get("role_id") is not None
                or runtime.get("input_event_id") != previous["event_id"]
            ):
                raise IntegrityError(
                    "blocked cancel must reference the active management turn"
                )
            if runtime.get("terminal_event_id") not in {None, event["event_id"]}:
                raise IntegrityError("cancel runtime points to another terminal event")
        return "CANCELLED", None
    if event_type == "resume":
        if state != "BLOCKED" or previous["event_type"] != "block":
            raise IntegrityError("resume is only valid from blocked state")
        if previous.get("block_reason") in {"limit", "profile_changed"}:
            raise IntegrityError("limit/profile_changed block cannot resume")
        if runtime_by_turn is None:
            from .turns import iter_runtimes

            recovery_candidates = iter_runtimes(run_dir, team=team)
        else:
            recovery_candidates = runtime_by_turn.values()
        recovery_gate = any(
            item["phase"] == "recovery_required"
            and int(item["input_event_id"].rsplit("-", 1)[1])
            < event["event_seq"]
            for item in recovery_candidates
        )
        if recovery_gate:
            raise IntegrityError("resume was committed while recovery was required")
        if (
            from_role is not None
            or to_role is None
            or turn_id is None
            or created >= deadline
        ):
            raise IntegrityError("resume fields or deadline are invalid")
        runtime = referenced_runtime(turn_id)
        if (
            runtime.get("executor") != "origin"
            or runtime.get("role_id") is not None
            or runtime.get("business_turn_seq") is not None
            or runtime.get("input_event_id") != previous["event_id"]
            or runtime.get("terminal_event_id") not in {None, event["event_id"]}
        ):
            raise IntegrityError("resume must reference an Origin management turn")
        if business_turns_before >= team.max_turns:
            raise IntegrityError("resume was committed after max_turns")
        return "RUNNING", to_role
    if state != "RUNNING" or owner is None:
        raise IntegrityError(f"{event_type} requires a running token owner")
    if turn_id is None or turn_id in terminal_turns:
        raise IntegrityError("turn has no id or already has a terminal event")
    runtime = referenced_runtime(turn_id)
    if runtime.get("role_id") != owner or from_role != owner:
        raise IntegrityError("event sender does not own the referenced turn")
    if (
        runtime.get("input_event_id") != previous["event_id"]
        or runtime.get("terminal_event_id") not in {None, event["event_id"]}
    ):
        raise IntegrityError("event does not terminate the current input turn")
    if event_type == "handoff":
        if to_role is None or created >= deadline:
            raise IntegrityError("handoff target or deadline is invalid")
        seq = runtime.get("business_turn_seq")
        if not isinstance(seq, int) or seq >= team.max_turns:
            raise IntegrityError("handoff was committed after max_turns")
        return "RUNNING", to_role
    if event_type == "complete":
        if to_role is not None or created >= deadline:
            raise IntegrityError("complete fields or deadline are invalid")
        return "COMPLETED", None
    if event_type == "block":
        reason = event.get("block_reason")
        if (
            to_role is not None
            or not isinstance(reason, str)
            or reason not in BLOCK_REASONS
        ):
            raise IntegrityError("block fields are invalid")
        if reason == "limit":
            limit_reason = event.get("limit_reason")
            if not isinstance(limit_reason, str) or limit_reason not in {
                "deadline",
                "max_turns",
            }:
                raise IntegrityError("limit block has invalid limit_reason")
            if limit_reason == "deadline" and created < deadline:
                raise IntegrityError("deadline block was committed before the deadline")
            if limit_reason == "max_turns":
                if created >= deadline:
                    raise IntegrityError(
                        "max-turn block cannot override an expired deadline"
                    )
                if runtime.get("business_turn_seq") != team.max_turns:
                    raise IntegrityError(
                        "max-turn block does not terminate the final allowed turn"
                    )
        elif event.get("limit_reason") is not None:
            raise IntegrityError("non-limit block has limit_reason")
        if created >= deadline and not (
            reason == "limit" and event.get("limit_reason") == "deadline"
        ):
            raise IntegrityError("non-deadline event committed after deadline")
        return "BLOCKED", None
    raise IntegrityError(f"unsupported event transition: {event_type}")


def scan_journal(
    run_dir: Path,
    *,
    verify_config: bool = True,
    _runtime_values: list[dict[str, Any]] | None = None,
) -> JournalProjection:
    team = parse_team(read_json(run_dir / "team.json"))
    if (
        team.run_id != run_dir.name
        or run_dir.parent != team.workspace / ".agent-team" / "runs"
    ):
        raise IntegrityError(
            "team.json does not match the canonical Run directory",
            "team.json",
        )
    events_dir = run_dir / "events"
    if not events_dir.is_dir() or events_dir.is_symlink():
        raise IntegrityError("events directory is invalid")
    indexed_files: list[tuple[int, Path]] = []
    for path in events_dir.iterdir():
        if is_uncommitted_atomic_temporary(path):
            continue
        if path.is_symlink() or not path.is_file():
            raise IntegrityError(f"invalid event entry: {path.name}")
        match = EVENT_FILE_RE.fullmatch(path.name)
        if not match:
            raise IntegrityError(f"invalid event filename: {path.name}")
        indexed_files.append((int(match.group(1)), path))
    files = [
        path
        for _, path in sorted(
            indexed_files,
            key=lambda item: (item[0], item[1].name),
        )
    ]
    events: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for expected_seq, path in enumerate(files, start=1):
        event = read_json(path)
        _validate_event_schema(event, path)
        if event["event_seq"] != expected_seq:
            raise IntegrityError("journal event sequence has a gap or duplicate")
        expected_prev = previous["event_id"] if previous else None
        if event["prev_event_id"] != expected_prev:
            raise IntegrityError("journal prev_event_id chain is invalid")
        _validate_payload(run_dir, event)
        events.append(event)
        previous = event

    if verify_config and events:
        kickoff_event = events[0]
        if kickoff_event["event_type"] != "kickoff":
            raise IntegrityError("journal does not begin with kickoff")
        inputs = {
            "REQUEST.md": kickoff_event["request_sha256"],
            "PROTOCOL.md": kickoff_event["protocol_sha256"],
            "team.json": kickoff_event["team_sha256"],
        }
        for name, expected in inputs.items():
            try:
                actual = sha256_bytes(read_regular(run_dir / name))
            except FileNotFoundError as exc:
                raise IntegrityError(f"immutable run input is missing: {name}") from exc
            if actual != expected:
                raise IntegrityError(f"immutable run input changed: {name}", name)

    validate_state_root(team.workspace)

    # The immutable Journal is validated before any mutable Turn Runtime is
    # consulted, so Journal corruption has one stable priority everywhere.
    if _runtime_values is None:
        try:
            from .turns import iter_runtimes

            runtimes = iter_runtimes(run_dir, team=team)
        except FileNotFoundError as exc:
            raise IntegrityError("turn runtime is missing") from exc
    else:
        runtimes = _runtime_values
    runtime_by_turn = {runtime["turn_id"]: runtime for runtime in runtimes}
    if len(runtime_by_turn) != len(runtimes):
        raise IntegrityError("multiple runtimes use the same turn id")
    runtime_input_ids = {runtime["input_event_id"] for runtime in runtimes}
    if len(runtime_input_ids) != len(runtimes):
        raise IntegrityError("multiple turns claim the same input event")

    state = "UNSTARTED"
    owner: str | None = None
    previous = None
    kickoff: dict[str, Any] | None = None
    terminal_turns: set[str] = set()
    prior_event_ids: set[str] = set()
    for event in events:
        state, owner = _validate_transition(
            run_dir,
            team,
            event,
            state=state,
            owner=owner,
            previous=previous,
            kickoff=kickoff,
            terminal_turns=terminal_turns,
            business_turns_before=sum(
                runtime["business_turn_seq"] is not None
                and runtime["input_event_id"] in prior_event_ids
                for runtime in runtimes
            ),
            runtime_by_turn=runtime_by_turn,
        )
        if event["event_type"] == "kickoff":
            kickoff = event
        if (
            event["turn_id"] is not None
            and event["event_type"] in {"handoff", "complete", "block", "cancel"}
        ):
            terminal_turns.add(event["turn_id"])
        prior_event_ids.add(event["event_id"])
        previous = event
    event_by_id = {event["event_id"]: event for event in events}
    for runtime in runtimes:
        input_event = event_by_id.get(runtime["input_event_id"])
        if input_event is None:
            raise IntegrityError(
                f"turn references unknown input event: {runtime['turn_id']}"
            )
        if runtime["business_turn_seq"] is None:
            if (
                runtime["executor"] != "origin"
                or runtime["role_id"] is not None
                or input_event["event_type"] != "block"
            ):
                raise IntegrityError(
                    f"invalid management turn input: {runtime['turn_id']}"
                )
        elif (
            input_event["event_type"] not in {"kickoff", "handoff", "resume"}
            or input_event["to_role"] != runtime["role_id"]
        ):
            raise IntegrityError(
                f"business turn input routing mismatch: {runtime['turn_id']}"
            )
        terminal_id = runtime["terminal_event_id"]
        if terminal_id is None:
            continue
        terminal = event_by_id.get(terminal_id)
        if terminal is None:
            raise IntegrityError(
                f"runtime references unknown terminal event: {runtime['turn_id']}"
            )
        if terminal["turn_id"] != runtime["turn_id"]:
            raise IntegrityError(
                f"runtime terminal event references another turn: "
                f"{runtime['turn_id']}"
            )
        expected_types = (
            {"resume", "cancel"}
            if runtime["business_turn_seq"] is None
            else {"handoff", "complete", "block", "cancel"}
        )
        if terminal["event_type"] not in expected_types:
            raise IntegrityError(
                f"runtime has invalid terminal event type: {runtime['turn_id']}"
            )
        if terminal["event_type"] in {"handoff", "complete", "resume"}:
            expected_outcome = "success"
        elif terminal["event_type"] == "cancel":
            expected_outcome = "cancelled"
        elif terminal.get("block_reason") == "agent":
            expected_outcome = "success"
        elif terminal.get("block_reason") == "limit":
            expected_outcome = (
                "cancelled"
                if terminal.get("limit_reason") == "deadline"
                else "stalled"
            )
        elif terminal.get("block_reason") == "no_action":
            expected_outcome = "stalled"
        else:
            expected_outcome = "failed"
        if runtime["outcome"] != expected_outcome:
            raise IntegrityError(
                f"runtime outcome conflicts with terminal event: "
                f"{runtime['turn_id']}"
            )
    projection = JournalProjection(team, tuple(events), state, owner)
    # Existing Worker and Session snapshots are never repairable by guessing,
    # so every command shares this unconditional integrity gate. Turn-local
    # Facts/Outbox/process damage remains available to the deterministic
    # finalization paths that can author a Recovery Block.
    from .observation import _validate_role_and_session_snapshots

    _validate_role_and_session_snapshots(run_dir, team, runtimes)
    return projection


def next_event_identity(projection: JournalProjection, event_type: str) -> tuple[int, str]:
    if event_type not in EVENT_TYPES:
        raise ValueError(event_type)
    seq = len(projection.events) + 1
    return seq, f"{event_type}-{seq:04d}"


def commit_event(
    run_dir: Path,
    *,
    event_type: str,
    payload_relative: str,
    payload_bytes: bytes,
    from_role: str | None,
    to_role: str | None,
    turn_id: str | None,
    created_at: str | None = None,
    extra: dict[str, Any] | None = None,
    _runtime_values: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Commit an event while the caller holds the run's exclusive lock."""
    projection = scan_journal(run_dir, _runtime_values=_runtime_values)
    if projection.kickoff is not None:
        from .gitfacts import validate_runtime_git_boundaries

        validate_runtime_git_boundaries(projection.team.workspace)
    seq, event_id = next_event_identity(projection, event_type)
    payload_path = resolve_run_path(run_dir, payload_relative)
    atomic_write(payload_path, payload_bytes, immutable=True)
    event: dict[str, Any] = {
        "schema_version": 1,
        "event_id": event_id,
        "event_seq": seq,
        "prev_event_id": projection.tail["event_id"] if projection.tail else None,
        "event_type": event_type,
        "from_role": from_role,
        "to_role": to_role,
        "turn_id": turn_id,
        "payload_path": payload_relative,
        "payload_sha256": sha256_bytes(payload_bytes),
        "created_at": created_at or rfc3339(),
    }
    if extra:
        event.update(extra)
    path = _event_path(run_dir, event)
    _validate_event_schema(event, path)
    # Validate the prospective transition before making the event visible.
    terminal_turns = {
        item["turn_id"]
        for item in projection.events
        if item["turn_id"] is not None
        and item["event_type"] in {"handoff", "complete", "block", "cancel"}
    }
    _validate_transition(
        run_dir,
        projection.team,
        event,
        state=projection.status,
        owner=projection.current_role,
        previous=projection.tail,
        kickoff=projection.kickoff,
        terminal_turns=terminal_turns,
        business_turns_before=(
            sum(
                runtime["business_turn_seq"] is not None
                for runtime in _runtime_values
            )
            if _runtime_values is not None
            else business_turn_count(run_dir)
        ),
        runtime_by_turn=(
            {
                runtime["turn_id"]: runtime
                for runtime in _runtime_values
            }
            if _runtime_values is not None
            else None
        ),
    )
    atomic_json(path, event, immutable=True)
    return event


def can_create_business_turn(
    run_dir: Path,
    projection: JournalProjection | None = None,
    *,
    now: dt.datetime | None = None,
) -> tuple[bool, str | None]:
    current = projection or scan_journal(run_dir)
    if current.kickoff is None:
        return False, "unstarted"
    when = now or dt.datetime.now(dt.timezone.utc)
    deadline = parse_rfc3339(current.kickoff["created_at"]) + dt.timedelta(
        seconds=current.team.max_wall_time_seconds
    )
    if when >= deadline:
        return False, "deadline"
    if business_turn_count(run_dir) >= current.team.max_turns:
        return False, "max_turns"
    return True, None


def find_event(
    projection: JournalProjection,
    event_id: str,
) -> dict[str, Any] | None:
    return next((event for event in projection.events if event["event_id"] == event_id), None)

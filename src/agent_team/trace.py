from __future__ import annotations

import base64
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .adapters import get_adapter
from .adapters.base import StreamRecord
from .config import ObservabilityPolicy
from .errors import IntegrityError, InvalidArgument
from .util import (
    atomic_write,
    canonical_json_bytes,
    fsync_dir,
    path_entry_exists,
    read_json,
    read_regular,
    require_keys,
    resolve_run_path,
    rfc3339,
    sha256_bytes,
)


TRACE_MANIFEST_REQUIRED = {
    "schema_version",
    "run_id",
    "turn_id",
    "role_id",
    "adapter_id",
    "created_at",
    "policy",
    "capture",
    "summary",
    "artifacts",
}
TRACE_EVENT_TYPES = {
    "agent_message",
    "diagnostic",
    "error",
    "file_change",
    "harness_event",
    "reasoning_summary",
    "session",
    "tool_call",
    "tool_result",
    "turn",
    "usage",
}
COMMON_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(
        r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|authorization|"
        r"client[_-]?secret|credential|password|private[_-]?key|secret)"
        r"[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+"
    ),
)
SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "client_secret",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
}


class Redactor:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.count = 0

    def text(self, value: str) -> str:
        if not self.enabled:
            return value
        result = value
        for pattern in COMMON_SECRET_PATTERNS:
            if pattern.groups:
                result, replacements = pattern.subn(r"\1[REDACTED]", result)
            else:
                result, replacements = pattern.subn("[REDACTED]", result)
            self.count += replacements
        return result

    def value(self, value: Any, *, key: str | None = None) -> Any:
        if not self.enabled:
            return value
        normalized_key = key.lower().replace("-", "_") if key else None
        if normalized_key and (
            normalized_key in SENSITIVE_KEYS
            or normalized_key.endswith(("_api_key", "_access_token", "_password"))
        ):
            self.count += 1
            return "[REDACTED]"
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {
                item_key: self.value(item, key=str(item_key))
                for item_key, item in value.items()
            }
        return value

    def stream_text(self, value: str) -> str:
        if not self.enabled:
            return value
        try:
            structured = json.loads(value)
        except ValueError:
            return self.text(value)
        redacted = self.value(structured)
        suffix = "\n" if value.endswith("\n") else ""
        return json.dumps(
            redacted,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + suffix


def _decode_outer_data(value: dict[str, Any]) -> bytes:
    encoding = value.get("encoding")
    data = value.get("data")
    if not isinstance(data, str):
        raise IntegrityError("stream record data must be a string")
    if encoding == "utf-8":
        return data.encode("utf-8")
    if encoding == "base64":
        try:
            return base64.b64decode(data, validate=True)
        except ValueError as exc:
            raise IntegrityError("stream record has invalid base64 data") from exc
    raise IntegrityError(f"stream record has invalid encoding: {encoding!r}")


def iter_stream_records(stream_path: Path) -> list[StreamRecord]:
    if not path_entry_exists(stream_path):
        return []
    raw = read_regular(stream_path)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    first_seq: dict[str, int | None] = {"stdout": None, "stderr": None}
    observed_at: dict[str, str | None] = {"stdout": None, "stderr": None}
    records: list[StreamRecord] = []
    last_seq = 0
    for raw_line in raw.splitlines():
        try:
            outer = json.loads(raw_line)
        except (UnicodeDecodeError, ValueError) as exc:
            raise IntegrityError(f"invalid stream JSONL: {stream_path}") from exc
        if not isinstance(outer, dict):
            raise IntegrityError("stream JSONL entry must be an object")
        schema_version = outer.get("schema_version")
        source = outer.get("source")
        seq = outer.get("seq")
        if (
            source not in {"stdout", "stderr"}
            or isinstance(seq, bool)
            or not isinstance(seq, int)
            or seq <= last_seq
            or not isinstance(outer.get("observed_at"), str)
        ):
            raise IntegrityError("stream JSONL entry has invalid envelope")
        last_seq = seq
        if schema_version == 2:
            first = outer.get("original_first_seq")
            last = outer.get("original_last_seq")
            if (
                isinstance(first, bool)
                or not isinstance(first, int)
                or isinstance(last, bool)
                or not isinstance(last, int)
                or first < 1
                or last < first
            ):
                raise IntegrityError("archived stream record has invalid raw reference")
            data = _decode_outer_data(outer)
            try:
                decoded = data.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                decoded = base64.b64encode(data).decode("ascii")
                encoding = "base64"
            records.append(
                StreamRecord(
                    source=source,
                    first_seq=first,
                    last_seq=last,
                    observed_at=outer["observed_at"],
                    encoding=encoding,
                    data=decoded,
                )
            )
            continue
        if schema_version != 1:
            raise IntegrityError("unsupported stream JSONL schema")
        data = _decode_outer_data(outer)
        if first_seq[source] is None:
            first_seq[source] = seq
        observed_at[source] = outer["observed_at"]
        buffers[source].extend(data)
        while True:
            newline = buffers[source].find(b"\n")
            if newline < 0:
                break
            record_bytes = bytes(buffers[source][: newline + 1])
            del buffers[source][: newline + 1]
            try:
                decoded = record_bytes.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                decoded = base64.b64encode(record_bytes).decode("ascii")
                encoding = "base64"
            records.append(
                StreamRecord(
                    source=source,
                    first_seq=first_seq[source] or seq,
                    last_seq=seq,
                    observed_at=outer["observed_at"],
                    encoding=encoding,
                    data=decoded,
                )
            )
            first_seq[source] = seq if buffers[source] else None
    for source in ("stdout", "stderr"):
        if not buffers[source]:
            continue
        data = bytes(buffers[source])
        try:
            decoded = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            decoded = base64.b64encode(data).decode("ascii")
            encoding = "base64"
        records.append(
            StreamRecord(
                source=source,
                first_seq=first_seq[source] or last_seq or 1,
                last_seq=last_seq or 1,
                observed_at=observed_at[source] or rfc3339(),
                encoding=encoding,
                data=decoded,
            )
        )
    records.sort(key=lambda item: (item.last_seq, item.first_seq, item.source))
    return records


def _normalized_events(
    *,
    run_id: str,
    turn_id: str,
    role_id: str,
    adapter_id: str,
    records: Iterable[StreamRecord],
    redaction: str,
) -> tuple[list[dict[str, Any]], int]:
    adapter = get_adapter(adapter_id)
    redactor = Redactor(redaction == "standard")
    events: list[dict[str, Any]] = []
    trace_seq = 0
    for record in records:
        for normalized in adapter.normalize_stream_record(record):
            trace_seq += 1
            events.append(
                {
                    "schema_version": 1,
                    "trace_seq": trace_seq,
                    "observed_at": record.observed_at,
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "role_id": role_id,
                    "adapter_id": adapter_id,
                    "event_type": normalized.event_type,
                    "raw_ref": {
                        "source": record.source,
                        "first_seq": record.first_seq,
                        "last_seq": record.last_seq,
                    },
                    "data": redactor.value(normalized.data),
                }
            )
    return events, redactor.count


def _usage_summary(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    event_counts: Counter[str] = Counter()
    tool_calls = 0
    tool_results = 0
    usage: dict[str, Any] = {}
    event_count = 0
    for event in events:
        event_count += 1
        event_type = event["event_type"]
        event_counts[event_type] += 1
        tool_calls += int(event_type == "tool_call")
        tool_results += int(event_type == "tool_result")
        if event_type != "usage":
            continue
        data = event["data"]
        supplied_usage = data.get("usage")
        if isinstance(supplied_usage, dict):
            usage.update(supplied_usage)
        for key in {
            "total_cost_usd",
            "duration_ms",
            "duration_api_ms",
            "num_turns",
        }:
            if data.get(key) is not None:
                usage[key] = data[key]
    return {
        "event_count": event_count,
        "event_counts": dict(sorted(event_counts.items())),
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "usage": usage,
    }


def _serialize_trace(
    events: list[dict[str, Any]],
    *,
    max_bytes: int,
) -> tuple[bytes, list[dict[str, Any]], bool, int]:
    lines: list[bytes] = []
    stored_events: list[dict[str, Any]] = []
    used = 0
    for index, event in enumerate(events):
        line = canonical_json_bytes(event)
        if used + len(line) > max_bytes:
            omitted = len(events) - index
            return b"".join(lines), stored_events, True, omitted
        lines.append(line)
        stored_events.append(event)
        used += len(line)
    return b"".join(lines), stored_events, False, 0


def _redacted_stream(
    records: Iterable[StreamRecord],
    *,
    redaction: str,
) -> tuple[bytes, bytes, int]:
    redactor = Redactor(redaction == "standard")
    outer_lines: list[bytes] = []
    stderr = bytearray()
    for seq, record in enumerate(records, start=1):
        if record.encoding == "base64":
            if redactor.enabled:
                data = "[REDACTED_BINARY]"
                redactor.count += 1
            else:
                data = record.data
            encoding = "utf-8" if redactor.enabled else "base64"
        else:
            data = redactor.stream_text(record.data)
            encoding = "utf-8"
        outer = {
            "schema_version": 2,
            "seq": seq,
            "observed_at": record.observed_at,
            "source": record.source,
            "encoding": encoding,
            "data": data,
            "original_first_seq": record.first_seq,
            "original_last_seq": record.last_seq,
            "redacted": redactor.enabled,
        }
        outer_lines.append(canonical_json_bytes(outer))
        if record.source == "stderr":
            stderr.extend(data.encode("utf-8"))
    return b"".join(outer_lines), bytes(stderr), redactor.count


def _artifact(path: Path, turn_dir: Path, kind: str) -> dict[str, Any]:
    raw = read_regular(path)
    return {
        "path": path.relative_to(turn_dir).as_posix(),
        "kind": kind,
        "size_bytes": len(raw),
        "sha256": sha256_bytes(raw),
    }


def _record_size(record: StreamRecord) -> int:
    if record.encoding == "utf-8":
        return len(record.data.encode("utf-8"))
    try:
        return len(base64.b64decode(record.data, validate=True))
    except ValueError as exc:
        raise IntegrityError("normalized stream record has invalid base64 data") from exc


def finalize_turn_trace(
    *,
    run_id: str,
    turn_dir: Path,
    role_id: str,
    adapter_id: str,
    policy: ObservabilityPolicy,
) -> tuple[dict[str, Any], str]:
    manifest_path = turn_dir / "trace-manifest.json"
    if path_entry_exists(manifest_path):
        manifest = validate_trace_manifest(
            turn_dir,
            expected_run_id=run_id,
            expected_role_id=role_id,
            expected_adapter_id=adapter_id,
            expected_policy=policy,
        )
        return manifest, sha256_bytes(read_regular(manifest_path))
    process_dir = turn_dir / "process"
    stream_path = process_dir / "stream.jsonl"
    records = iter_stream_records(stream_path)
    events, trace_redactions = _normalized_events(
        run_id=run_id,
        turn_id=turn_dir.name,
        role_id=role_id,
        adapter_id=adapter_id,
        records=records,
        redaction=policy.redaction,
    )
    (
        trace_bytes,
        stored_events,
        normalized_truncated,
        omitted_events,
    ) = _serialize_trace(
        events,
        max_bytes=policy.max_trace_bytes,
    )
    trace_path = turn_dir / "trace.jsonl"
    atomic_write(trace_path, trace_bytes, immutable=True)

    stream_redactions = 0
    if policy.raw_retention == "redacted" and path_entry_exists(stream_path):
        archived, stderr, stream_redactions = _redacted_stream(
            records,
            redaction=policy.redaction,
        )
        atomic_write(stream_path, archived)
        stderr_path = process_dir / "stderr.log"
        if path_entry_exists(stderr_path):
            atomic_write(stderr_path, stderr)
    elif policy.raw_retention == "delete":
        for path in (stream_path, process_dir / "stderr.log"):
            if path_entry_exists(path):
                path.unlink()
        fsync_dir(process_dir)

    capture_path = process_dir / "capture.json"
    if path_entry_exists(capture_path):
        capture = read_json(capture_path)
    else:
        retained_size = sum(_record_size(record) for record in records)
        capture = {
            "schema_version": 1,
            "source_bytes": retained_size,
            "stored_source_bytes": retained_size,
            "dropped_source_bytes": 0,
            "chunks_observed": len(records),
            "chunks_stored": len(records),
            "truncated": False,
            "closed_at": rfc3339(),
        }
    capture = {
        **capture,
        "normalized_trace_truncated": normalized_truncated,
        "normalized_events_omitted": omitted_events,
        "records_observed": len(records),
        "normalized_events_observed": len(events),
        "normalized_events_stored": len(stored_events),
        "trace_redactions": trace_redactions,
        "stream_redactions": stream_redactions,
    }
    artifact_specs = [
        (turn_dir / "input.md", "input"),
        (process_dir / "launch.json", "launch"),
        (capture_path, "capture"),
        (stream_path, "harness_stream"),
        (process_dir / "stderr.log", "stderr"),
        (turn_dir / "outbox.json", "formal_action"),
        (turn_dir / "outbox-payload.md", "formal_output"),
        (turn_dir / "output.md", "final_message"),
        (trace_path, "normalized_trace"),
    ]
    artifacts = [
        _artifact(path, turn_dir, kind)
        for path, kind in artifact_specs
        if path_entry_exists(path)
    ]
    created_at = capture.get("closed_at")
    if not isinstance(created_at, str) or not created_at:
        created_at = rfc3339()
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "turn_id": turn_dir.name,
        "role_id": role_id,
        "adapter_id": adapter_id,
        "created_at": created_at,
        "policy": policy.to_json(),
        "capture": capture,
        "summary": _usage_summary(stored_events),
        "artifacts": artifacts,
    }
    atomic_write(manifest_path, canonical_json_bytes(manifest), immutable=True)
    return manifest, sha256_bytes(read_regular(manifest_path))


def validate_trace_manifest(
    turn_dir: Path,
    *,
    expected_sha256: str | None = None,
    expected_run_id: str | None = None,
    expected_role_id: str | None = None,
    expected_adapter_id: str | None = None,
    expected_policy: ObservabilityPolicy | None = None,
) -> dict[str, Any]:
    path = turn_dir / "trace-manifest.json"
    raw = read_regular(path)
    if expected_sha256 is not None and sha256_bytes(raw) != expected_sha256:
        raise IntegrityError(
            f"trace manifest hash mismatch: {turn_dir.name}",
            f"turns/{turn_dir.name}/trace-manifest.json",
        )
    manifest = read_json(path)
    require_keys(
        manifest,
        required=TRACE_MANIFEST_REQUIRED,
        subject="trace manifest",
    )
    if (
        manifest["schema_version"] != 1
        or manifest["turn_id"] != turn_dir.name
        or not isinstance(manifest["run_id"], str)
        or not manifest["run_id"]
        or not isinstance(manifest["role_id"], str)
        or not manifest["role_id"]
        or not isinstance(manifest["adapter_id"], str)
        or not manifest["adapter_id"]
        or not isinstance(manifest["created_at"], str)
        or not manifest["created_at"]
        or not isinstance(manifest["policy"], dict)
        or not isinstance(manifest["capture"], dict)
        or not isinstance(manifest["summary"], dict)
        or not isinstance(manifest["artifacts"], list)
    ):
        raise IntegrityError("trace manifest identity is invalid")
    if expected_run_id is not None and manifest["run_id"] != expected_run_id:
        raise IntegrityError("trace manifest run identity is invalid")
    if expected_role_id is not None and manifest["role_id"] != expected_role_id:
        raise IntegrityError("trace manifest role identity is invalid")
    if expected_adapter_id is not None and (
        manifest["adapter_id"] != expected_adapter_id
    ):
        raise IntegrityError("trace manifest adapter identity is invalid")
    if expected_policy is not None and manifest["policy"] != expected_policy.to_json():
        raise IntegrityError("trace manifest policy does not match the Run")
    seen: set[str] = set()
    kinds: dict[str, str] = {}
    for artifact in manifest["artifacts"]:
        if not isinstance(artifact, dict):
            raise IntegrityError("trace manifest artifact must be an object")
        require_keys(
            artifact,
            required={"path", "kind", "size_bytes", "sha256"},
            subject="trace manifest artifact",
        )
        relative = artifact["path"]
        kind = artifact["kind"]
        size = artifact["size_bytes"]
        digest = artifact["sha256"]
        if (
            not isinstance(relative, str)
            or not relative
            or relative in seen
            or not isinstance(kind, str)
            or not kind
            or kind in kinds
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise IntegrityError("trace manifest artifact path is invalid")
        seen.add(relative)
        kinds[kind] = relative
        artifact_path = resolve_run_path(turn_dir, relative)
        raw_artifact = read_regular(artifact_path)
        if (
            size != len(raw_artifact)
            or digest != sha256_bytes(raw_artifact)
        ):
            raise IntegrityError(
                f"trace artifact hash mismatch: {relative}",
                f"turns/{turn_dir.name}/{relative}",
            )
    if kinds.get("normalized_trace") != "trace.jsonl":
        raise IntegrityError("trace manifest does not anchor trace.jsonl")
    events = read_trace_events(turn_dir)
    if manifest["summary"] != _usage_summary(events):
        raise IntegrityError("trace manifest summary does not match trace.jsonl")
    capture = manifest["capture"]
    for key in {
        "source_bytes",
        "stored_source_bytes",
        "dropped_source_bytes",
        "chunks_observed",
        "chunks_stored",
        "records_observed",
        "normalized_events_observed",
        "normalized_events_stored",
        "normalized_events_omitted",
        "trace_redactions",
        "stream_redactions",
    }:
        value = capture.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise IntegrityError(f"trace manifest capture {key} is invalid")
    if (
        capture.get("schema_version") != 1
        or not isinstance(capture.get("closed_at"), str)
        or not capture["closed_at"]
        or not isinstance(capture.get("truncated"), bool)
        or not isinstance(capture.get("normalized_trace_truncated"), bool)
        or capture["source_bytes"]
        != capture["stored_source_bytes"] + capture["dropped_source_bytes"]
        or capture["chunks_stored"] > capture["chunks_observed"]
        or capture["normalized_events_stored"] != len(events)
        or capture["normalized_events_observed"]
        != capture["normalized_events_stored"]
        + capture["normalized_events_omitted"]
        or capture["normalized_trace_truncated"]
        != bool(capture["normalized_events_omitted"])
    ):
        raise IntegrityError("trace manifest capture summary is inconsistent")
    return manifest


def read_trace_events(turn_dir: Path) -> list[dict[str, Any]]:
    path = turn_dir / "trace.jsonl"
    if not path_entry_exists(path):
        return []
    events: list[dict[str, Any]] = []
    for expected, raw_line in enumerate(read_regular(path).splitlines(), start=1):
        try:
            event = json.loads(raw_line)
        except (UnicodeDecodeError, ValueError) as exc:
            raise IntegrityError(f"invalid normalized trace: {turn_dir.name}") from exc
        if (
            not isinstance(event, dict)
            or event.get("schema_version") != 1
            or event.get("trace_seq") != expected
            or event.get("turn_id") != turn_dir.name
            or event.get("event_type") not in TRACE_EVENT_TYPES
            or not isinstance(event.get("data"), dict)
        ):
            raise IntegrityError(f"normalized trace event is invalid: {turn_dir.name}")
        raw_ref = event.get("raw_ref")
        if (
            not isinstance(event.get("run_id"), str)
            or not event["run_id"]
            or not isinstance(event.get("role_id"), str)
            or not event["role_id"]
            or not isinstance(event.get("adapter_id"), str)
            or not event["adapter_id"]
            or not isinstance(event.get("observed_at"), str)
            or not event["observed_at"]
            or not isinstance(raw_ref, dict)
            or set(raw_ref) != {"source", "first_seq", "last_seq"}
            or raw_ref["source"] not in {"stdout", "stderr"}
            or isinstance(raw_ref["first_seq"], bool)
            or not isinstance(raw_ref["first_seq"], int)
            or isinstance(raw_ref["last_seq"], bool)
            or not isinstance(raw_ref["last_seq"], int)
            or raw_ref["first_seq"] < 1
            or raw_ref["last_seq"] < raw_ref["first_seq"]
        ):
            raise IntegrityError(
                f"normalized trace raw reference is invalid: {turn_dir.name}"
            )
        events.append(event)
    return events


def live_trace_events(
    *,
    run_id: str,
    turn_dir: Path,
    role_id: str,
    adapter_id: str,
    redaction: str = "standard",
) -> list[dict[str, Any]]:
    if path_entry_exists(turn_dir / "trace.jsonl"):
        return read_trace_events(turn_dir)
    records = iter_stream_records(turn_dir / "process" / "stream.jsonl")
    events, _ = _normalized_events(
        run_id=run_id,
        turn_id=turn_dir.name,
        role_id=role_id,
        adapter_id=adapter_id,
        records=records,
        redaction=redaction,
    )
    return events


def _decode_text(path: Path, redactor: Redactor) -> str:
    if not path_entry_exists(path):
        return ""
    raw = read_regular(path)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "[BINARY CONTENT]"
    return redactor.text(text)


def _aggregate_summaries(summaries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    event_counts: Counter[str] = Counter()
    tool_calls = 0
    tool_results = 0
    usage: dict[str, float | int] = {}
    for summary in summaries:
        supplied_counts = summary.get("event_counts")
        if isinstance(supplied_counts, dict):
            for key, value in supplied_counts.items():
                if isinstance(key, str) and isinstance(value, int):
                    event_counts[key] += value
        if isinstance(summary.get("tool_calls"), int):
            tool_calls += summary["tool_calls"]
        if isinstance(summary.get("tool_results"), int):
            tool_results += summary["tool_results"]
        supplied_usage = summary.get("usage")
        if isinstance(supplied_usage, dict):
            for key, value in supplied_usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
    return {
        "event_counts": dict(sorted(event_counts.items())),
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "usage": usage,
    }


def build_transcript(
    run_dir: Path,
    *,
    role_id: str | None = None,
    turn_id: str | None = None,
) -> dict[str, Any]:
    from .config import load_team
    from .turns import iter_runtimes, load_outbox

    team = load_team(run_dir)
    if role_id is not None and role_id not in team.roles:
        raise InvalidArgument(f"unknown transcript role: {role_id}")
    runtimes = iter_runtimes(run_dir, team=team)
    selected = [
        runtime
        for runtime in runtimes
        if (role_id is None or runtime["role_id"] == role_id)
        and (turn_id is None or runtime["turn_id"] == turn_id)
    ]
    if turn_id is not None and not selected:
        raise InvalidArgument(f"unknown transcript turn: {turn_id}")
    turns: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for runtime in selected:
        current_turn_dir = run_dir / "turns" / runtime["turn_id"]
        redactor = Redactor(team.observability.redaction == "standard")
        role = team.roles.get(runtime["role_id"]) if runtime["role_id"] else None
        adapter_id = role.adapter if role and role.binding == "external" else "origin"
        events = (
            live_trace_events(
                run_id=team.run_id,
                turn_dir=current_turn_dir,
                role_id=runtime["role_id"] or "origin-management",
                adapter_id=adapter_id or "",
                redaction=team.observability.redaction,
            )
            if runtime["executor"] == "worker"
            else []
        )
        manifest = None
        if runtime["trace_manifest_sha256"] is not None:
            manifest = validate_trace_manifest(
                current_turn_dir,
                expected_sha256=runtime["trace_manifest_sha256"],
                expected_run_id=team.run_id,
                expected_role_id=runtime["role_id"],
                expected_adapter_id=adapter_id,
                expected_policy=team.observability,
            )
        summary = manifest["summary"] if manifest else _usage_summary(events)
        summaries.append(summary)
        launch_path = current_turn_dir / "process" / "launch.json"
        prompt = ""
        if path_entry_exists(launch_path):
            launch = read_json(launch_path)
            supplied_prompt = launch.get("stdin")
            if isinstance(supplied_prompt, str):
                prompt = redactor.text(supplied_prompt)
        outbox = load_outbox(current_turn_dir)
        formal_output = None
        if outbox is not None:
            output_path = resolve_run_path(run_dir, outbox["payload_path"])
            formal_output = {
                "action": outbox["action"],
                "to_role": outbox["to_role"],
                "path": outbox["payload_path"],
                "sha256": outbox["payload_sha256"],
                "content": _decode_text(output_path, redactor),
            }
        turns.append(
            {
                "turn_id": runtime["turn_id"],
                "business_turn_seq": runtime["business_turn_seq"],
                "role_id": runtime["role_id"],
                "executor": runtime["executor"],
                "adapter_id": adapter_id,
                "phase": runtime["phase"],
                "outcome": runtime["outcome"],
                "input": {
                    "path": f"turns/{runtime['turn_id']}/input.md",
                    "sha256": runtime["input_payload_sha256"],
                    "content": _decode_text(current_turn_dir / "input.md", redactor),
                },
                "prompt": prompt,
                "events": events,
                "formal_output": formal_output,
                "trace_manifest_sha256": runtime["trace_manifest_sha256"],
                "summary": summary,
                "origin_trace_coverage": (
                    "not_applicable"
                    if runtime["executor"] == "worker"
                    else "formal_boundaries_only"
                ),
            }
        )
    return {
        "run_id": team.run_id,
        "audit_mode": team.observability.audit_mode,
        "redaction": team.observability.redaction,
        "turn_count": len(turns),
        "summary": _aggregate_summaries(summaries),
        "turns": turns,
    }


def flattened_trace_events(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for turn in transcript["turns"]:
        for event in turn["events"]:
            result.append(event)
    return result


def render_trace_event(event: dict[str, Any]) -> str:
    prefix = (
        f"{event['observed_at']} {event['turn_id']} "
        f"{event['role_id']} {event['event_type']}"
    )
    data = event["data"]
    event_type = event["event_type"]
    if event_type in {"agent_message", "reasoning_summary"}:
        detail = data.get("text", "")
    elif event_type == "tool_call":
        detail = data.get("command") or (
            f"{data.get('tool')}: "
            + json.dumps(data.get("input"), ensure_ascii=False, sort_keys=True)
        )
    elif event_type == "tool_result":
        detail = data.get("output")
        if detail is None:
            detail = data.get("content")
        if not isinstance(detail, str):
            detail = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    else:
        detail = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return f"{prefix} {detail}".rstrip()


def render_transcript(transcript: dict[str, Any]) -> str:
    lines = [
        f"Run: {transcript['run_id']}",
        f"Audit mode: {transcript['audit_mode']}",
        f"Turns: {transcript['turn_count']}",
        "Summary: "
        + json.dumps(transcript["summary"], ensure_ascii=False, sort_keys=True),
    ]
    for turn in transcript["turns"]:
        lines.extend(
            [
                "",
                (
                    f"## {turn['turn_id']} role={turn['role_id']} "
                    f"executor={turn['executor']} outcome={turn['outcome']}"
                ),
                f"Input: {turn['input']['path']} sha256={turn['input']['sha256']}",
                turn["input"]["content"].rstrip(),
            ]
        )
        if turn["prompt"]:
            lines.extend(["", "### Harness prompt", turn["prompt"].rstrip()])
        if turn["executor"] == "origin":
            lines.append(
                "Trace coverage: formal input/output and workspace boundaries only"
            )
        for event in turn["events"]:
            lines.append(render_trace_event(event))
        if turn["formal_output"] is not None:
            lines.extend(
                [
                    "",
                    (
                        "### Formal output: "
                        f"{turn['formal_output']['action']} "
                        f"{turn['formal_output']['path']}"
                    ),
                    turn["formal_output"]["content"].rstrip(),
                ]
            )
    return "\n".join(lines)

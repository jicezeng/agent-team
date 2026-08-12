from __future__ import annotations

import base64
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .adapters import get_adapter
from .adapters.base import LaunchSpec, StreamRecord
from .config import MAX_LIMIT_VALUE, ObservabilityPolicy
from .errors import IntegrityError, InvalidArgument
from .util import (
    atomic_write,
    canonical_json_bytes,
    fsync_dir,
    parse_rfc3339,
    path_entry_exists,
    read_json,
    read_regular,
    require_keys,
    require_schema_version,
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
RAW_STREAM_V1_REQUIRED = {
    "schema_version",
    "seq",
    "observed_at",
    "source",
    "encoding",
    "data",
}
RAW_STREAM_V2_REQUIRED = RAW_STREAM_V1_REQUIRED | {
    "original_first_seq",
    "original_last_seq",
    "redacted",
}
TRACE_EVENT_REQUIRED = {
    "schema_version",
    "trace_seq",
    "observed_at",
    "run_id",
    "turn_id",
    "role_id",
    "adapter_id",
    "event_type",
    "raw_ref",
    "data",
}
CAPTURE_FILE_REQUIRED = {
    "schema_version",
    "source_bytes",
    "stored_source_bytes",
    "dropped_source_bytes",
    "chunks_observed",
    "chunks_stored",
    "truncated",
    "closed_at",
}
TRACE_CAPTURE_REQUIRED = CAPTURE_FILE_REQUIRED | {
    "normalized_trace_truncated",
    "normalized_events_omitted",
    "records_observed",
    "normalized_events_observed",
    "normalized_events_stored",
    "trace_redactions",
    "stream_redactions",
}
OBSERVABILITY_POLICY_REQUIRED = {
    "audit_mode",
    "redaction",
    "max_trace_bytes",
    "raw_retention",
    "required_payload_sections",
}
TRACE_ARTIFACT_PATHS = {
    "input": "input.md",
    "launch": "process/launch.json",
    "capture": "process/capture.json",
    "harness_stream": "process/stream.jsonl",
    "stderr": "process/stderr.log",
    "formal_action": "outbox.json",
    "formal_output": "outbox-payload.md",
    "final_message": "output.md",
    "normalized_trace": "trace.jsonl",
}
TRACE_PREPARATION_REQUIRED = {
    "schema_version",
    "source_stream_sha256",
    "source_stderr_sha256",
    "manifest",
}
TRACE_PREPARATION_PATH = "process/trace-finalization.json"
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
STREAM_SOURCES = ("stdout", "stderr", "terminal")
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


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key!r}")
        value[key] = item
    return value


def _json_object_line(raw_line: bytes, *, subject: str) -> dict[str, Any]:
    try:
        value = json.loads(raw_line, object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, ValueError) as exc:
        raise IntegrityError(f"invalid {subject}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"{subject} entry must be an object")
    return value


def _stream_envelopes(stream_path: Path) -> list[dict[str, Any]]:
    if not path_entry_exists(stream_path):
        return []
    # Only LF-terminated records are committed. A Supervisor crash may leave one
    # incomplete tail, which is diagnostic truncation rather than a JSON record.
    lines = read_regular(stream_path).split(b"\n")[:-1]
    envelopes: list[dict[str, Any]] = []
    stream_schema: int | None = None
    for expected_seq, raw_line in enumerate(lines, start=1):
        outer = _json_object_line(raw_line, subject=f"stream JSONL: {stream_path}")
        schema_version = outer.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version not in {1, 2}
        ):
            raise IntegrityError("unsupported stream JSONL schema")
        required = (
            RAW_STREAM_V1_REQUIRED
            if schema_version == 1
            else RAW_STREAM_V2_REQUIRED
        )
        require_keys(outer, required=required, subject="stream JSONL entry")
        if stream_schema is None:
            stream_schema = schema_version
        elif schema_version != stream_schema:
            raise IntegrityError("stream JSONL mixes schema versions")
        source = outer["source"]
        seq = outer["seq"]
        if (
            not isinstance(source, str)
            or source not in STREAM_SOURCES
            or isinstance(seq, bool)
            or not isinstance(seq, int)
            or seq != expected_seq
        ):
            raise IntegrityError("stream JSONL entry has invalid envelope")
        parse_rfc3339(outer["observed_at"])
        _decode_outer_data(outer)
        if schema_version == 2:
            first = outer["original_first_seq"]
            last = outer["original_last_seq"]
            if (
                isinstance(first, bool)
                or not isinstance(first, int)
                or isinstance(last, bool)
                or not isinstance(last, int)
                or first < 1
                or last < first
                or not isinstance(outer["redacted"], bool)
            ):
                raise IntegrityError(
                    "archived stream record has invalid raw reference"
                )
        envelopes.append(outer)
    return envelopes


def _validate_capture_file(value: dict[str, Any]) -> dict[str, Any]:
    require_keys(value, required=CAPTURE_FILE_REQUIRED, subject="turn capture")
    require_schema_version(value, 1, subject="turn capture")
    for key in {
        "source_bytes",
        "stored_source_bytes",
        "dropped_source_bytes",
        "chunks_observed",
        "chunks_stored",
    }:
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise IntegrityError(f"turn capture {key} is invalid")
    if (
        not isinstance(value["truncated"], bool)
        or value["source_bytes"]
        != value["stored_source_bytes"] + value["dropped_source_bytes"]
        or value["chunks_stored"] > value["chunks_observed"]
        or value["truncated"] != bool(value["dropped_source_bytes"])
    ):
        raise IntegrityError("turn capture summary is inconsistent")
    parse_rfc3339(value["closed_at"])
    return value


def _validate_capture_against_stream(
    capture: dict[str, Any],
    envelopes: list[dict[str, Any]],
) -> None:
    if envelopes and envelopes[0]["schema_version"] != 1:
        # Schema 2 is a post-validation redacted archive used by an idempotent
        # retry. Its entries are framed records, not the original byte chunks.
        return
    stored_bytes = sum(len(_decode_outer_data(item)) for item in envelopes)
    if (
        capture["chunks_stored"] != len(envelopes)
        or capture["stored_source_bytes"] != stored_bytes
    ):
        raise IntegrityError("turn capture does not match the retained raw stream")


def _policy_from_json(value: dict[str, Any]) -> ObservabilityPolicy:
    require_keys(
        value,
        required=OBSERVABILITY_POLICY_REQUIRED,
        subject="trace manifest policy",
    )
    audit_mode = value["audit_mode"]
    redaction = value["redaction"]
    max_trace_bytes = value["max_trace_bytes"]
    raw_retention = value["raw_retention"]
    sections = value["required_payload_sections"]
    if not isinstance(audit_mode, str) or audit_mode not in {"standard", "full"}:
        raise IntegrityError("trace manifest audit mode is invalid")
    if not isinstance(redaction, str) or redaction not in {"standard", "none"}:
        raise IntegrityError("trace manifest redaction policy is invalid")
    if (
        isinstance(max_trace_bytes, bool)
        or not isinstance(max_trace_bytes, int)
        or max_trace_bytes < 1024
        or max_trace_bytes > MAX_LIMIT_VALUE
    ):
        raise IntegrityError("trace manifest byte limit is invalid")
    if not isinstance(raw_retention, str) or raw_retention not in {
        "redacted",
        "keep",
        "delete",
    }:
        raise IntegrityError("trace manifest raw retention is invalid")
    if raw_retention == "redacted" and redaction != "standard":
        raise IntegrityError("trace manifest redacted retention is inconsistent")
    if (
        not isinstance(sections, list)
        or not all(isinstance(section, str) and section.strip() for section in sections)
        or len({section.casefold() for section in sections}) != len(sections)
    ):
        raise IntegrityError("trace manifest payload sections are invalid")
    if audit_mode == "full":
        folded = {section.casefold() for section in sections}
        if raw_retention == "delete" or not {
            "decision rationale",
            "evidence",
        }.issubset(folded):
            raise IntegrityError("trace manifest full audit policy is inconsistent")
    return ObservabilityPolicy(
        audit_mode=audit_mode,
        redaction=redaction,
        max_trace_bytes=max_trace_bytes,
        raw_retention=raw_retention,
        required_payload_sections=tuple(sections),
    )


def iter_stream_records(stream_path: Path) -> list[StreamRecord]:
    envelopes = _stream_envelopes(stream_path)
    buffers = {source: bytearray() for source in STREAM_SOURCES}
    first_seq: dict[str, int | None] = {
        source: None for source in STREAM_SOURCES
    }
    observed_at: dict[str, str | None] = {
        source: None for source in STREAM_SOURCES
    }
    records: list[StreamRecord] = []
    last_seq = 0
    for outer in envelopes:
        schema_version = outer["schema_version"]
        source = outer["source"]
        seq = outer["seq"]
        last_seq = seq
        if schema_version == 2:
            first = outer["original_first_seq"]
            last = outer["original_last_seq"]
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
        data = _decode_outer_data(outer)
        if source == "terminal":
            try:
                decoded = data.decode("utf-8")
                encoding = "utf-8"
            except UnicodeDecodeError:
                decoded = base64.b64encode(data).decode("ascii")
                encoding = "base64"
            records.append(
                StreamRecord(
                    source=source,
                    first_seq=seq,
                    last_seq=seq,
                    observed_at=outer["observed_at"],
                    encoding=encoding,
                    data=decoded,
                )
            )
            continue
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
    for source in STREAM_SOURCES:
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


def _artifact_bytes(relative: str, kind: str, raw: bytes) -> dict[str, Any]:
    return {
        "path": relative,
        "kind": kind,
        "size_bytes": len(raw),
        "sha256": sha256_bytes(raw),
    }


def _artifact_specs(turn_dir: Path) -> list[tuple[Path, str, str]]:
    return [
        (turn_dir / relative, kind, relative)
        for kind, relative in TRACE_ARTIFACT_PATHS.items()
    ]


def _planned_artifacts(
    turn_dir: Path,
    *,
    overrides: dict[str, bytes | None],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path, kind, relative in _artifact_specs(turn_dir):
        if relative in overrides:
            raw = overrides[relative]
            if raw is None:
                continue
        elif path_entry_exists(path):
            raw = read_regular(path)
        else:
            continue
        artifacts.append(_artifact_bytes(relative, kind, raw))
    return artifacts


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _parse_manifest_header(
    manifest: dict[str, Any],
    turn_dir: Path,
    *,
    expected_run_id: str | None = None,
    expected_role_id: str | None = None,
    expected_adapter_id: str | None = None,
    expected_policy: ObservabilityPolicy | None = None,
) -> ObservabilityPolicy:
    require_keys(
        manifest,
        required=TRACE_MANIFEST_REQUIRED,
        subject="trace manifest",
    )
    if (
        isinstance(manifest["schema_version"], bool)
        or not isinstance(manifest["schema_version"], int)
        or manifest["schema_version"] != 1
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
    parse_rfc3339(manifest["created_at"])
    parsed_policy = _policy_from_json(manifest["policy"])
    if expected_policy is not None and parsed_policy != expected_policy:
        raise IntegrityError("trace manifest policy does not match the Run")
    return parsed_policy


def _parse_artifact_descriptors(
    manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    seen: set[str] = set()
    artifacts: dict[str, dict[str, Any]] = {}
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
            or kind not in TRACE_ARTIFACT_PATHS
            or relative != TRACE_ARTIFACT_PATHS[kind]
            or kind in artifacts
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or not _is_sha256(digest)
        ):
            raise IntegrityError("trace manifest artifact path is invalid")
        seen.add(relative)
        artifacts[kind] = artifact
    if "normalized_trace" not in artifacts:
        raise IntegrityError("trace manifest does not anchor trace.jsonl")
    if "capture" not in artifacts:
        raise IntegrityError("trace manifest does not anchor process/capture.json")
    return artifacts


def _validate_artifact_files(
    turn_dir: Path,
    artifacts: dict[str, dict[str, Any]],
) -> None:
    actual = {
        kind
        for path, kind, _relative in _artifact_specs(turn_dir)
        if path_entry_exists(path)
    }
    if set(artifacts) != actual:
        raise IntegrityError(
            "trace manifest artifact set does not match retained files"
        )
    for kind, artifact in artifacts.items():
        relative = artifact["path"]
        artifact_path = resolve_run_path(turn_dir, relative)
        raw_artifact = read_regular(artifact_path)
        if (
            artifact["size_bytes"] != len(raw_artifact)
            or artifact["sha256"] != sha256_bytes(raw_artifact)
        ):
            raise IntegrityError(
                f"trace artifact hash mismatch: {relative}",
                f"turns/{turn_dir.name}/{relative}",
            )


def _validate_manifest_trace_and_capture(
    turn_dir: Path,
    manifest: dict[str, Any],
    parsed_policy: ObservabilityPolicy,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events = read_trace_events(turn_dir)
    for event in events:
        if (
            event["run_id"] != manifest["run_id"]
            or event["turn_id"] != manifest["turn_id"]
            or event["role_id"] != manifest["role_id"]
            or event["adapter_id"] != manifest["adapter_id"]
        ):
            raise IntegrityError("normalized trace identity does not match manifest")
    if canonical_json_bytes(manifest["summary"]) != canonical_json_bytes(
        _usage_summary(events)
    ):
        raise IntegrityError("trace manifest summary does not match trace.jsonl")
    capture = manifest["capture"]
    require_keys(
        capture,
        required=TRACE_CAPTURE_REQUIRED,
        subject="trace manifest capture",
    )
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
    capture_file = _validate_capture_file(
        {key: capture[key] for key in CAPTURE_FILE_REQUIRED}
    )
    persisted_capture = _validate_capture_file(
        read_json(turn_dir / "process/capture.json")
    )
    if capture_file != persisted_capture:
        raise IntegrityError("trace manifest capture does not match capture.json")
    if (
        capture["closed_at"] != manifest["created_at"]
        or not isinstance(capture["normalized_trace_truncated"], bool)
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
    if capture["stored_source_bytes"] > parsed_policy.max_trace_bytes:
        raise IntegrityError("trace manifest capture exceeds its byte limit")
    return events, capture_file


def _validate_retention_state(
    turn_dir: Path,
    *,
    policy: ObservabilityPolicy,
    artifacts: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
    capture: dict[str, Any],
    capture_file: dict[str, Any],
) -> None:
    stream_path = turn_dir / "process/stream.jsonl"
    stderr_path = turn_dir / "process/stderr.log"
    if policy.raw_retention == "delete":
        if (
            "harness_stream" in artifacts
            or "stderr" in artifacts
            or path_entry_exists(stream_path)
            or path_entry_exists(stderr_path)
        ):
            raise IntegrityError("trace retention delete left raw process output")
        return
    if "harness_stream" not in artifacts:
        raise IntegrityError("trace manifest does not anchor the retained raw stream")
    if "stderr" not in artifacts:
        raise IntegrityError("trace manifest does not anchor the retained stderr")
    envelopes = _stream_envelopes(stream_path)
    if envelopes:
        schema_version = envelopes[0]["schema_version"]
        if policy.raw_retention == "keep" and schema_version != 1:
            raise IntegrityError("trace keep retention requires a Schema 1 raw stream")
        if policy.raw_retention == "redacted" and (
            schema_version != 2
            or any(envelope["redacted"] is not True for envelope in envelopes)
        ):
            raise IntegrityError(
                "trace redacted retention requires a redacted Schema 2 stream"
            )
    _validate_capture_against_stream(capture_file, envelopes)
    records = iter_stream_records(stream_path)
    if read_regular(stderr_path) != _archived_stderr(stream_path):
        raise IntegrityError("retained stderr does not match the raw stream")
    if capture["records_observed"] != len(records):
        raise IntegrityError("trace manifest record count does not match raw stream")
    raw_refs = {
        (record.source, record.first_seq, record.last_seq) for record in records
    }
    if any(
        (
            event["raw_ref"]["source"],
            event["raw_ref"]["first_seq"],
            event["raw_ref"]["last_seq"],
        )
        not in raw_refs
        for event in events
    ):
        raise IntegrityError("normalized trace contains an unknown raw reference")


def _validate_trace_manifest_value(
    turn_dir: Path,
    manifest: dict[str, Any],
    *,
    expected_run_id: str | None = None,
    expected_role_id: str | None = None,
    expected_adapter_id: str | None = None,
    expected_policy: ObservabilityPolicy | None = None,
) -> dict[str, Any]:
    parsed_policy = _parse_manifest_header(
        manifest,
        turn_dir,
        expected_run_id=expected_run_id,
        expected_role_id=expected_role_id,
        expected_adapter_id=expected_adapter_id,
        expected_policy=expected_policy,
    )
    artifacts = _parse_artifact_descriptors(manifest)
    _validate_artifact_files(turn_dir, artifacts)
    events, capture_file = _validate_manifest_trace_and_capture(
        turn_dir,
        manifest,
        parsed_policy,
    )
    _validate_retention_state(
        turn_dir,
        policy=parsed_policy,
        artifacts=artifacts,
        events=events,
        capture=manifest["capture"],
        capture_file=capture_file,
    )
    return manifest


def _prepare_trace_finalization(
    *,
    run_id: str,
    turn_dir: Path,
    role_id: str,
    adapter_id: str,
    policy: ObservabilityPolicy,
) -> tuple[dict[str, Any], dict[str, bytes | None]]:
    process_dir = turn_dir / "process"
    stream_path = process_dir / "stream.jsonl"
    stderr_path = process_dir / "stderr.log"
    capture_path = process_dir / "capture.json"
    if not path_entry_exists(capture_path):
        raise IntegrityError(
            f"turn capture is missing: {turn_dir.name}",
            f"turns/{turn_dir.name}/process/capture.json",
        )
    if not path_entry_exists(stream_path):
        raise IntegrityError(
            f"turn raw stream is missing: {turn_dir.name}",
            f"turns/{turn_dir.name}/process/stream.jsonl",
        )
    if not path_entry_exists(stderr_path):
        raise IntegrityError(
            f"turn stderr capture is missing: {turn_dir.name}",
            f"turns/{turn_dir.name}/process/stderr.log",
        )
    capture_file = _validate_capture_file(read_json(capture_path))
    if capture_file["stored_source_bytes"] > policy.max_trace_bytes:
        raise IntegrityError("turn capture exceeds the frozen trace byte limit")
    source_stream = read_regular(stream_path)
    source_stderr = read_regular(stderr_path)
    envelopes = _stream_envelopes(stream_path)
    if envelopes and envelopes[0]["schema_version"] != 1:
        raise IntegrityError("turn raw stream was archived before trace preparation")
    _validate_capture_against_stream(capture_file, envelopes)
    records = iter_stream_records(stream_path)
    if source_stderr != _archived_stderr(stream_path):
        raise IntegrityError("turn stderr capture does not match the raw stream")
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
    ) = _serialize_trace(events, max_bytes=policy.max_trace_bytes)
    stream_redactions = 0
    retained_stream: bytes | None = source_stream
    retained_stderr: bytes | None = source_stderr
    if policy.raw_retention == "redacted":
        retained_stream, archived_stderr, stream_redactions = _redacted_stream(
            records,
            redaction=policy.redaction,
        )
        retained_stderr = archived_stderr
    elif policy.raw_retention == "delete":
        retained_stream = None
        retained_stderr = None
    capture = {
        **capture_file,
        "normalized_trace_truncated": normalized_truncated,
        "normalized_events_omitted": omitted_events,
        "records_observed": len(records),
        "normalized_events_observed": len(events),
        "normalized_events_stored": len(stored_events),
        "trace_redactions": trace_redactions,
        "stream_redactions": stream_redactions,
    }
    retained = {
        "trace.jsonl": trace_bytes,
        "process/stream.jsonl": retained_stream,
        "process/stderr.log": retained_stderr,
    }
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "turn_id": turn_dir.name,
        "role_id": role_id,
        "adapter_id": adapter_id,
        "created_at": capture["closed_at"],
        "policy": policy.to_json(),
        "capture": capture,
        "summary": _usage_summary(stored_events),
        "artifacts": _planned_artifacts(turn_dir, overrides=retained),
    }
    preparation = {
        "schema_version": 1,
        "source_stream_sha256": sha256_bytes(source_stream),
        "source_stderr_sha256": sha256_bytes(source_stderr),
        "manifest": manifest,
    }
    return preparation, retained


def _parse_trace_preparation(
    value: dict[str, Any],
    turn_dir: Path,
    *,
    run_id: str,
    role_id: str,
    adapter_id: str,
    policy: ObservabilityPolicy,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    require_keys(
        value,
        required=TRACE_PREPARATION_REQUIRED,
        subject="trace finalization receipt",
    )
    if (
        isinstance(value["schema_version"], bool)
        or not isinstance(value["schema_version"], int)
        or value["schema_version"] != 1
        or not _is_sha256(value["source_stream_sha256"])
        or not _is_sha256(value["source_stderr_sha256"])
        or not isinstance(value["manifest"], dict)
    ):
        raise IntegrityError("trace finalization receipt is invalid")
    manifest = value["manifest"]
    parsed_policy = _parse_manifest_header(
        manifest,
        turn_dir,
        expected_run_id=run_id,
        expected_role_id=role_id,
        expected_adapter_id=adapter_id,
        expected_policy=policy,
    )
    artifacts = _parse_artifact_descriptors(manifest)
    if parsed_policy.raw_retention == "delete":
        if "harness_stream" in artifacts or "stderr" in artifacts:
            raise IntegrityError("trace deletion receipt retains raw process output")
    elif "harness_stream" not in artifacts:
        raise IntegrityError("trace receipt omits the retained raw stream")
    elif "stderr" not in artifacts:
        raise IntegrityError("trace receipt omits the retained stderr")
    return manifest, artifacts


def _assert_prepared_payload(
    artifact: dict[str, Any],
    data: bytes,
) -> None:
    if (
        artifact["size_bytes"] != len(data)
        or artifact["sha256"] != sha256_bytes(data)
    ):
        raise IntegrityError("trace finalization receipt payload is inconsistent")


def _verify_prepared_file(
    turn_dir: Path,
    artifact: dict[str, Any],
) -> None:
    raw = read_regular(resolve_run_path(turn_dir, artifact["path"]))
    _assert_prepared_payload(artifact, raw)


def _archived_stderr(stream_path: Path) -> bytes:
    stderr = bytearray()
    for record in iter_stream_records(stream_path):
        if record.source != "stderr":
            continue
        if record.encoding == "utf-8":
            stderr.extend(record.data.encode("utf-8"))
        else:
            try:
                stderr.extend(base64.b64decode(record.data, validate=True))
            except ValueError as exc:
                raise IntegrityError("archived stderr record is invalid") from exc
    return bytes(stderr)


def _apply_trace_preparation(
    turn_dir: Path,
    preparation: dict[str, Any],
    *,
    run_id: str,
    role_id: str,
    adapter_id: str,
    policy: ObservabilityPolicy,
    retained: dict[str, bytes | None] | None,
) -> dict[str, Any]:
    manifest, artifacts = _parse_trace_preparation(
        preparation,
        turn_dir,
        run_id=run_id,
        role_id=role_id,
        adapter_id=adapter_id,
        policy=policy,
    )
    trace_path = turn_dir / "trace.jsonl"
    trace_artifact = artifacts["normalized_trace"]
    if retained is not None:
        trace_bytes = retained["trace.jsonl"]
        assert trace_bytes is not None
        _assert_prepared_payload(trace_artifact, trace_bytes)
        atomic_write(trace_path, trace_bytes, immutable=True)
    else:
        _verify_prepared_file(turn_dir, trace_artifact)

    # Validate every non-retention semantic field before changing or deleting
    # the source stream. The prepared receipt is the durable recovery source
    # only after the normalized Trace has also been proven intact.
    parsed_policy = _policy_from_json(manifest["policy"])
    _validate_manifest_trace_and_capture(turn_dir, manifest, parsed_policy)

    process_dir = turn_dir / "process"
    stream_path = process_dir / "stream.jsonl"
    stderr_path = process_dir / "stderr.log"
    if policy.raw_retention == "redacted":
        stream_artifact = artifacts["harness_stream"]
        if retained is not None:
            stream_bytes = retained["process/stream.jsonl"]
            assert stream_bytes is not None
            _assert_prepared_payload(stream_artifact, stream_bytes)
            atomic_write(stream_path, stream_bytes)
        else:
            _verify_prepared_file(turn_dir, stream_artifact)
        stderr_bytes = (
            retained["process/stderr.log"]
            if retained is not None
            else _archived_stderr(stream_path)
        )
        assert stderr_bytes is not None
        _assert_prepared_payload(artifacts["stderr"], stderr_bytes)
        atomic_write(stderr_path, stderr_bytes)
    elif policy.raw_retention == "delete":
        for path in (stream_path, stderr_path):
            if path_entry_exists(path):
                path.unlink()
        fsync_dir(process_dir)

    _validate_trace_manifest_value(
        turn_dir,
        manifest,
        expected_run_id=run_id,
        expected_role_id=role_id,
        expected_adapter_id=adapter_id,
        expected_policy=policy,
    )
    return manifest


def _remove_trace_preparation(path: Path) -> None:
    if not path_entry_exists(path):
        return
    path.unlink()
    fsync_dir(path.parent)


def finalize_turn_trace(
    *,
    run_id: str,
    turn_dir: Path,
    role_id: str,
    adapter_id: str,
    policy: ObservabilityPolicy,
) -> tuple[dict[str, Any], str]:
    manifest_path = turn_dir / "trace-manifest.json"
    preparation_path = turn_dir / TRACE_PREPARATION_PATH
    if path_entry_exists(manifest_path):
        manifest = validate_trace_manifest(
            turn_dir,
            expected_run_id=run_id,
            expected_role_id=role_id,
            expected_adapter_id=adapter_id,
            expected_policy=policy,
        )
        _remove_trace_preparation(preparation_path)
        return manifest, sha256_bytes(read_regular(manifest_path))
    retained: dict[str, bytes | None] | None = None
    if path_entry_exists(preparation_path):
        preparation = read_json(preparation_path)
        _parse_trace_preparation(
            preparation,
            turn_dir,
            run_id=run_id,
            role_id=role_id,
            adapter_id=adapter_id,
            policy=policy,
        )
        stream_path = turn_dir / "process/stream.jsonl"
        if path_entry_exists(stream_path):
            envelopes = _stream_envelopes(stream_path)
            if not envelopes or envelopes[0]["schema_version"] == 1:
                recomputed, retained = _prepare_trace_finalization(
                    run_id=run_id,
                    turn_dir=turn_dir,
                    role_id=role_id,
                    adapter_id=adapter_id,
                    policy=policy,
                )
                if canonical_json_bytes(recomputed) != canonical_json_bytes(preparation):
                    raise IntegrityError(
                        "trace finalization receipt no longer matches source artifacts"
                    )
            elif policy.raw_retention != "redacted":
                raise IntegrityError("unexpected archived stream during trace retry")
        elif policy.raw_retention != "delete":
            raise IntegrityError("retained stream disappeared during trace finalization")
    else:
        preparation, retained = _prepare_trace_finalization(
            run_id=run_id,
            turn_dir=turn_dir,
            role_id=role_id,
            adapter_id=adapter_id,
            policy=policy,
        )
        atomic_write(
            preparation_path,
            canonical_json_bytes(preparation),
            immutable=True,
        )
    manifest = _apply_trace_preparation(
        turn_dir,
        preparation,
        run_id=run_id,
        role_id=role_id,
        adapter_id=adapter_id,
        policy=policy,
        retained=retained,
    )
    atomic_write(manifest_path, canonical_json_bytes(manifest), immutable=True)
    digest = sha256_bytes(read_regular(manifest_path))
    validate_trace_manifest(
        turn_dir,
        expected_sha256=digest,
        expected_run_id=run_id,
        expected_role_id=role_id,
        expected_adapter_id=adapter_id,
        expected_policy=policy,
    )
    _remove_trace_preparation(preparation_path)
    return manifest, digest


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
    return _validate_trace_manifest_value(
        turn_dir,
        manifest,
        expected_run_id=expected_run_id,
        expected_role_id=expected_role_id,
        expected_adapter_id=expected_adapter_id,
        expected_policy=expected_policy,
    )


def read_trace_events(turn_dir: Path) -> list[dict[str, Any]]:
    path = turn_dir / "trace.jsonl"
    if not path_entry_exists(path):
        return []
    raw = read_regular(path)
    if raw and not raw.endswith(b"\n"):
        raise IntegrityError(
            f"normalized trace has an incomplete tail: {turn_dir.name}"
        )
    events: list[dict[str, Any]] = []
    for expected, raw_line in enumerate(raw.split(b"\n")[:-1], start=1):
        event = _json_object_line(
            raw_line,
            subject=f"normalized trace: {turn_dir.name}",
        )
        require_keys(
            event,
            required=TRACE_EVENT_REQUIRED,
            subject="normalized trace event",
        )
        event_type = event["event_type"]
        require_schema_version(event, 1, subject="normalized trace event")
        if (
            isinstance(event["trace_seq"], bool)
            or not isinstance(event["trace_seq"], int)
            or event["trace_seq"] != expected
            or event["turn_id"] != turn_dir.name
            or not isinstance(event_type, str)
            or event_type not in TRACE_EVENT_TYPES
            or not isinstance(event["data"], dict)
        ):
            raise IntegrityError(f"normalized trace event is invalid: {turn_dir.name}")
        raw_ref = event["raw_ref"]
        if (
            not isinstance(event["run_id"], str)
            or not event["run_id"]
            or not isinstance(event["role_id"], str)
            or not event["role_id"]
            or not isinstance(event["adapter_id"], str)
            or not event["adapter_id"]
            or not isinstance(raw_ref, dict)
            or set(raw_ref) != {"source", "first_seq", "last_seq"}
            or not isinstance(raw_ref.get("source"), str)
            or raw_ref.get("source") not in STREAM_SOURCES
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
        parse_rfc3339(event["observed_at"])
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
            launch = LaunchSpec.from_json(read_json(launch_path))
            prompt = redactor.text(launch.stdin)
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

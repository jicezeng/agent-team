from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import agent_team.trace as trace_module
from agent_team.config import ObservabilityPolicy
from agent_team.errors import IntegrityError
from agent_team.supervisor import StreamRecorder, _base_snapshot
from agent_team.trace import (
    finalize_turn_trace,
    iter_stream_records,
    live_trace_events,
    read_trace_events,
    validate_trace_manifest,
)
from agent_team.util import (
    atomic_json,
    atomic_write,
    canonical_json_bytes,
    sha256_bytes,
)


class _NoEvidenceAdapter:
    def parse_stream_record(self, _record):
        return None


def _capture(
    run_dir: Path,
    records: list[tuple[str, dict]],
    *,
    adapter_id: str = "codex",
    max_bytes: int = 64 * 1024 * 1024,
) -> None:
    recorder = StreamRecorder(
        run_dir=run_dir,
        turn_id="turn-0001",
        adapter_id=adapter_id,
        snapshot=_base_snapshot("turn-0001", "nonce"),
        max_bytes=max_bytes,
    )
    recorder.adapter = _NoEvidenceAdapter()

    async def exercise() -> None:
        for source, value in records:
            await recorder.record(
                source,
                (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8"),
            )
        await recorder.close()

    asyncio.run(exercise())


def _capture_bytes(
    run_dir: Path,
    chunks: list[tuple[str, bytes]],
    *,
    adapter_id: str = "codex",
) -> None:
    recorder = StreamRecorder(
        run_dir=run_dir,
        turn_id="turn-0001",
        adapter_id=adapter_id,
        snapshot=_base_snapshot("turn-0001", "nonce"),
    )
    recorder.adapter = _NoEvidenceAdapter()

    async def exercise() -> None:
        for source, data in chunks:
            await recorder.record(source, data)
        await recorder.close()

    asyncio.run(exercise())


def _turn(run_dir: Path) -> Path:
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    atomic_write(turn_dir / "input.md", b"# Input\n\nInspect the implementation.\n")
    return turn_dir


def _raw_chunk(
    *,
    seq: int = 1,
    observed_at: str = "2026-08-12T00:00:00.000Z",
    data: str = "record\n",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "seq": seq,
        "observed_at": observed_at,
        "source": "stdout",
        "encoding": "utf-8",
        "data": data,
    }


def _trace_event(turn_dir: Path) -> dict[str, object]:
    return {
        "schema_version": 1,
        "trace_seq": 1,
        "observed_at": "2026-08-12T00:00:00.000Z",
        "run_id": "at-trace-schema",
        "turn_id": turn_dir.name,
        "role_id": "developer",
        "adapter_id": "codex",
        "event_type": "diagnostic",
        "raw_ref": {"source": "stdout", "first_seq": 1, "last_seq": 1},
        "data": {"text": "record"},
    }


def test_trace_manifest_normalizes_redacts_hashes_and_detects_tampering(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-trace-normalized"
    turn_dir = _turn(run_dir)
    secret = "sk-abcdefghijklmnopqrstuvwx"
    _capture(
        run_dir,
        [
            ("stdout", {"type": "thread.started", "thread_id": "thread-1"}),
            (
                "stdout",
                {
                    "type": "item.completed",
                    "item": {
                        "id": "message-1",
                        "type": "agent_message",
                        "text": f"Token {secret}",
                    },
                },
            ),
            (
                "stdout",
                {
                    "type": "item.started",
                    "item": {
                        "id": "command-1",
                        "type": "command_execution",
                        "command": "tool --password=supersecret",
                    },
                },
            ),
            (
                "stdout",
                {
                    "type": "item.completed",
                    "item": {
                        "id": "command-1",
                        "type": "command_execution",
                        "aggregated_output": '{"api_key":"another-secret"}',
                        "exit_code": 0,
                        "status": "completed",
                    },
                },
            ),
            (
                "stdout",
                {
                    "type": "item.completed",
                    "item": {
                        "id": "change-1",
                        "type": "file_change",
                        "changes": [{"path": "src/app.py", "kind": "update"}],
                    },
                },
            ),
            (
                "stdout",
                {
                    "type": "item.completed",
                    "item": {
                        "id": "reasoning-1",
                        "type": "reasoning",
                        "text": "Checked the failing boundary.",
                    },
                },
            ),
            (
                "stdout",
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 40,
                        "output_tokens": 12,
                        "reasoning_output_tokens": 3,
                    },
                },
            ),
        ],
    )
    atomic_json(
        turn_dir / "process" / "launch.json",
        {"stdin": "authoritative harness prompt"},
    )
    atomic_json(turn_dir / "outbox.json", {"action": "complete"})
    atomic_write(
        turn_dir / "outbox-payload.md",
        b"# Complete\n\n## Decision rationale\n\nDone.\n\n## Evidence\n\nTests pass.\n",
    )
    policy = ObservabilityPolicy()

    manifest, digest = finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="reviewer",
        adapter_id="codex",
        policy=policy,
    )

    events = read_trace_events(turn_dir)
    assert [event["event_type"] for event in events] == [
        "session",
        "agent_message",
        "tool_call",
        "tool_result",
        "file_change",
        "reasoning_summary",
        "usage",
    ]
    assert [event["trace_seq"] for event in events] == list(range(1, 8))
    assert events[1]["raw_ref"] == {
        "source": "stdout",
        "first_seq": 2,
        "last_seq": 2,
    }
    assert events[-1]["data"]["usage"]["input_tokens"] == 100
    assert manifest["summary"]["event_count"] == 7
    assert manifest["summary"]["tool_calls"] == 1
    assert manifest["summary"]["tool_results"] == 1
    assert manifest["summary"]["usage"]["output_tokens"] == 12
    assert manifest["capture"]["records_observed"] == 7
    assert manifest["capture"]["normalized_events_stored"] == 7
    assert {
        artifact["kind"] for artifact in manifest["artifacts"]
    } >= {
        "input",
        "launch",
        "capture",
        "harness_stream",
        "formal_action",
        "formal_output",
        "normalized_trace",
    }
    assert secret not in (turn_dir / "trace.jsonl").read_text(encoding="utf-8")
    archived = (turn_dir / "process" / "stream.jsonl").read_text(encoding="utf-8")
    assert secret not in archived
    assert "another-secret" not in archived
    assert "supersecret" not in archived
    assert "[REDACTED]" in archived
    assert validate_trace_manifest(
        turn_dir,
        expected_sha256=digest,
        expected_run_id=run_dir.name,
        expected_role_id="reviewer",
        expected_adapter_id="codex",
        expected_policy=policy,
    ) == manifest

    trace_path = turn_dir / "trace.jsonl"
    trace_path.write_bytes(trace_path.read_bytes() + b"{}\n")
    with pytest.raises(IntegrityError, match="trace artifact hash mismatch"):
        validate_trace_manifest(turn_dir, expected_sha256=digest)


def test_trace_retention_delete_removes_raw_stream_but_keeps_normalized_trace(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-trace-delete"
    turn_dir = _turn(run_dir)
    _capture(
        run_dir,
        [
            (
                "stderr",
                {
                    "type": "error",
                    "error": {"message": "password=do-not-retain"},
                },
            ),
            ("stdout", {"type": "thread.started", "thread_id": "thread-1"}),
        ],
    )
    policy = ObservabilityPolicy(raw_retention="delete")

    manifest, digest = finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=policy,
    )

    assert not (turn_dir / "process" / "stream.jsonl").exists()
    assert not (turn_dir / "process" / "stderr.log").exists()
    assert (turn_dir / "trace.jsonl").exists()
    assert {
        artifact["kind"] for artifact in manifest["artifacts"]
    }.isdisjoint({"harness_stream", "stderr"})
    validate_trace_manifest(
        turn_dir,
        expected_sha256=digest,
        expected_policy=policy,
    )


@pytest.mark.parametrize(
    "failure_path",
    ["trace.jsonl", "process/stream.jsonl", "process/stderr.log", "trace-manifest.json"],
)
def test_redacted_trace_finalization_resumes_at_every_commit_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_path: str,
) -> None:
    run_dir = tmp_path / f"at-trace-redacted-retry-{Path(failure_path).name}"
    turn_dir = _turn(run_dir)
    secret = b"sk-abcdefghijklmnopqrstuvwx"
    _capture_bytes(
        run_dir,
        [
            (
                "stdout",
                b'{"type":"item.completed","item":{"id":"message-1",'
                b'"type":"agent_message","text":"' + secret + b'"}}\n',
            ),
            ("stderr", b"\xff\xfe\x00private-binary\n"),
        ],
    )
    policy = ObservabilityPolicy()
    original_atomic_write = trace_module.atomic_write
    failed = False

    def fail_once(path: Path, data: bytes, **kwargs: object) -> None:
        nonlocal failed
        relative = path.relative_to(turn_dir).as_posix()
        if relative == failure_path and not failed:
            failed = True
            raise RuntimeError(f"simulated crash before {relative}")
        original_atomic_write(path, data, **kwargs)

    monkeypatch.setattr(trace_module, "atomic_write", fail_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id="developer",
            adapter_id="codex",
            policy=policy,
        )

    receipt_path = turn_dir / "process" / "trace-finalization.json"
    prepared_manifest = json.loads(receipt_path.read_text(encoding="utf-8"))["manifest"]
    monkeypatch.setattr(trace_module, "atomic_write", original_atomic_write)
    manifest, digest = finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=policy,
    )

    assert manifest == prepared_manifest
    assert manifest["capture"]["stream_redactions"] == 2
    assert not receipt_path.exists()
    assert b"private-binary" not in (turn_dir / "process/stream.jsonl").read_bytes()
    assert validate_trace_manifest(
        turn_dir,
        expected_sha256=digest,
        expected_policy=policy,
    ) == manifest
    assert finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=policy,
    ) == (manifest, digest)


def test_trace_finalization_receipt_failure_leaves_source_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "at-trace-receipt-failure"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stderr", {"type": "error", "message": "failure"})])
    process_dir = turn_dir / "process"
    stream_path = process_dir / "stream.jsonl"
    stderr_path = process_dir / "stderr.log"
    source_stream = stream_path.read_bytes()
    source_stderr = stderr_path.read_bytes()
    original_atomic_write = trace_module.atomic_write

    def fail_receipt(path: Path, data: bytes, **kwargs: object) -> None:
        if path.name == "trace-finalization.json":
            raise RuntimeError("simulated crash before receipt")
        original_atomic_write(path, data, **kwargs)

    monkeypatch.setattr(trace_module, "atomic_write", fail_receipt)
    with pytest.raises(RuntimeError, match="simulated crash"):
        finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id="developer",
            adapter_id="codex",
            policy=ObservabilityPolicy(raw_retention="delete"),
        )

    assert stream_path.read_bytes() == source_stream
    assert stderr_path.read_bytes() == source_stderr
    assert not (turn_dir / "trace.jsonl").exists()
    assert not (process_dir / "trace-finalization.json").exists()

    monkeypatch.setattr(trace_module, "atomic_write", original_atomic_write)
    manifest, digest = finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=ObservabilityPolicy(raw_retention="delete"),
    )
    assert validate_trace_manifest(
        turn_dir,
        expected_sha256=digest,
        expected_policy=ObservabilityPolicy(raw_retention="delete"),
    ) == manifest


def test_delete_trace_finalization_resumes_after_raw_deletion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "at-trace-delete-retry"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stdout", {"type": "turn.started"})])
    policy = ObservabilityPolicy(raw_retention="delete")
    original_atomic_write = trace_module.atomic_write

    def fail_manifest(path: Path, data: bytes, **kwargs: object) -> None:
        if path.name == "trace-manifest.json":
            raise RuntimeError("simulated crash before manifest")
        original_atomic_write(path, data, **kwargs)

    monkeypatch.setattr(trace_module, "atomic_write", fail_manifest)
    with pytest.raises(RuntimeError, match="simulated crash"):
        finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id="developer",
            adapter_id="codex",
            policy=policy,
        )
    assert not (turn_dir / "process" / "stream.jsonl").exists()
    assert not (turn_dir / "process" / "stderr.log").exists()

    monkeypatch.setattr(trace_module, "atomic_write", original_atomic_write)
    manifest, digest = finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=policy,
    )
    assert validate_trace_manifest(
        turn_dir,
        expected_sha256=digest,
        expected_policy=policy,
    ) == manifest


def test_delete_trace_finalization_resumes_between_raw_deletions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "at-trace-delete-between-files"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stderr", {"type": "error", "message": "failure"})])
    process_dir = turn_dir / "process"
    stream_path = process_dir / "stream.jsonl"
    stderr_path = process_dir / "stderr.log"
    original_unlink = Path.unlink
    failed = False

    def fail_stderr_once(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal failed
        if path == stderr_path and not failed:
            failed = True
            raise RuntimeError("simulated crash between raw deletions")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_stderr_once)
    with pytest.raises(RuntimeError, match="simulated crash"):
        finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id="developer",
            adapter_id="codex",
            policy=ObservabilityPolicy(raw_retention="delete"),
        )
    assert not stream_path.exists()
    assert stderr_path.exists()
    assert (process_dir / "trace-finalization.json").exists()

    monkeypatch.setattr(Path, "unlink", original_unlink)
    policy = ObservabilityPolicy(raw_retention="delete")
    manifest, digest = finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=policy,
    )
    assert not stderr_path.exists()
    assert validate_trace_manifest(
        turn_dir,
        expected_sha256=digest,
        expected_policy=policy,
    ) == manifest


def test_trace_manifest_rejects_omitted_retained_artifact(tmp_path: Path) -> None:
    run_dir = tmp_path / "at-trace-omitted-artifact"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stdout", {"type": "turn.started"})])
    finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=ObservabilityPolicy(),
    )
    manifest_path = turn_dir / "trace-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = [
        artifact for artifact in manifest["artifacts"] if artifact["kind"] != "input"
    ]
    atomic_write(manifest_path, canonical_json_bytes(manifest))

    with pytest.raises(IntegrityError, match="artifact set"):
        validate_trace_manifest(turn_dir)


def test_trace_manifest_rejects_raw_artifact_recreated_after_delete(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-trace-delete-residual"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stdout", {"type": "turn.started"})])
    policy = ObservabilityPolicy(raw_retention="delete")
    finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=policy,
    )
    atomic_write(turn_dir / "process" / "stream.jsonl", b"")

    with pytest.raises(IntegrityError, match="artifact set|left raw"):
        validate_trace_manifest(turn_dir, expected_policy=policy)


def test_trace_manifest_rejects_unredacted_stream_under_redacted_policy(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-trace-wrong-retention-schema"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stdout", {"type": "turn.started"})])
    keep = ObservabilityPolicy(raw_retention="keep")
    finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=keep,
    )
    manifest_path = turn_dir / "trace-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["policy"]["raw_retention"] = "redacted"
    atomic_write(manifest_path, canonical_json_bytes(manifest))

    with pytest.raises(IntegrityError, match="redacted Schema 2"):
        validate_trace_manifest(turn_dir)


def test_trace_manifest_rejects_stderr_that_does_not_match_stream(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-trace-stderr-mismatch"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stderr", {"type": "error", "message": "failure"})])
    policy = ObservabilityPolicy(raw_retention="keep")
    finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=policy,
    )
    stderr_path = turn_dir / "process" / "stderr.log"
    stderr = b"different retained stderr\n"
    atomic_write(stderr_path, stderr)
    manifest_path = turn_dir / "trace-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(
        item for item in manifest["artifacts"] if item["kind"] == "stderr"
    )
    artifact["size_bytes"] = len(stderr)
    artifact["sha256"] = sha256_bytes(stderr)
    atomic_write(manifest_path, canonical_json_bytes(manifest))

    with pytest.raises(IntegrityError, match="stderr does not match"):
        validate_trace_manifest(turn_dir, expected_policy=policy)


def test_trace_manifest_summary_does_not_treat_boolean_as_integer(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-trace-summary-type"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stdout", {"type": "turn.started"})])
    finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=ObservabilityPolicy(),
    )
    manifest_path = turn_dir / "trace-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["summary"]["tool_calls"] == 0
    manifest["summary"]["tool_calls"] = False
    atomic_write(manifest_path, canonical_json_bytes(manifest))

    with pytest.raises(IntegrityError, match="summary does not match"):
        validate_trace_manifest(turn_dir)


def test_trace_finalization_checks_stderr_before_destructive_retention(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-trace-stderr-before-delete"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stderr", {"type": "error", "message": "failure"})])
    stderr_path = turn_dir / "process" / "stderr.log"
    atomic_write(stderr_path, b"different stderr\n")

    with pytest.raises(IntegrityError, match="stderr capture does not match"):
        finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id="developer",
            adapter_id="codex",
            policy=ObservabilityPolicy(raw_retention="delete"),
        )
    assert (turn_dir / "process" / "stream.jsonl").exists()
    assert stderr_path.exists()
    assert not (turn_dir / "process" / "trace-finalization.json").exists()


def test_normalized_trace_limit_stops_at_a_complete_event_boundary(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "at-trace-limit"
    turn_dir = _turn(run_dir)
    _capture(
        run_dir,
        [
            ("stdout", {"type": "thread.started", "thread_id": "thread-1"}),
            (
                "stdout",
                {
                    "type": "item.completed",
                    "item": {
                        "id": "message-1",
                        "type": "agent_message",
                        "text": "x" * 4096,
                    },
                },
            ),
            (
                "stdout",
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                },
            ),
        ],
    )
    # The raw JSONL fits under this limit, while the normalized envelopes do
    # not, so this exercises only normalized-trace truncation.
    policy = ObservabilityPolicy(max_trace_bytes=4400)

    manifest, _ = finalize_turn_trace(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
        policy=policy,
    )

    events = read_trace_events(turn_dir)
    assert [event["event_type"] for event in events] == ["session"]
    assert manifest["capture"]["normalized_trace_truncated"] is True
    assert manifest["capture"]["normalized_events_observed"] == 3
    assert manifest["capture"]["normalized_events_stored"] == 1
    assert manifest["capture"]["normalized_events_omitted"] == 2
    assert live_trace_events(
        run_id=run_dir.name,
        turn_dir=turn_dir,
        role_id="developer",
        adapter_id="codex",
    ) == events


def test_trace_finalization_requires_supervisor_capture(tmp_path: Path) -> None:
    run_dir = tmp_path / "at-trace-missing-capture"
    turn_dir = _turn(run_dir)
    process_dir = turn_dir / "process"
    process_dir.mkdir()
    atomic_write(process_dir / "stream.jsonl", b"")

    with pytest.raises(IntegrityError, match="capture is missing"):
        finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id="developer",
            adapter_id="codex",
            policy=ObservabilityPolicy(
                audit_mode="full",
                required_payload_sections=("Decision rationale", "Evidence"),
            ),
        )


def test_trace_finalization_rejects_raw_sequence_gap(tmp_path: Path) -> None:
    run_dir = tmp_path / "at-trace-sequence-gap"
    turn_dir = _turn(run_dir)
    process_dir = turn_dir / "process"
    process_dir.mkdir()
    chunks = [_raw_chunk(seq=1), _raw_chunk(seq=3)]
    atomic_write(
        process_dir / "stream.jsonl",
        b"".join(
            (json.dumps(chunk, separators=(",", ":")) + "\n").encode()
            for chunk in chunks
        ),
    )
    atomic_write(process_dir / "stderr.log", b"")
    atomic_json(
        process_dir / "capture.json",
        {
            "schema_version": 1,
            "source_bytes": 14,
            "stored_source_bytes": 14,
            "dropped_source_bytes": 0,
            "chunks_observed": 2,
            "chunks_stored": 2,
            "truncated": False,
            "closed_at": "2026-08-12T00:00:01.000Z",
        },
    )

    with pytest.raises(IntegrityError, match="invalid envelope"):
        finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id="developer",
            adapter_id="codex",
            policy=ObservabilityPolicy(raw_retention="keep"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("chunks_stored", 8),
        ("stored_source_bytes", 8),
    ],
)
def test_trace_finalization_reconciles_capture_with_raw_stream(
    tmp_path: Path,
    field: str,
    value: int,
) -> None:
    run_dir = tmp_path / f"at-trace-capture-{field.replace('_', '-')}"
    turn_dir = _turn(run_dir)
    _capture(run_dir, [("stdout", {"type": "turn.started"})])
    capture_path = turn_dir / "process" / "capture.json"
    capture = json.loads(capture_path.read_text(encoding="utf-8"))
    capture[field] = value
    if field == "chunks_stored":
        capture["chunks_observed"] = value
    else:
        capture["source_bytes"] = value
    atomic_json(capture_path, capture)

    with pytest.raises(IntegrityError, match="does not match"):
        finalize_turn_trace(
            run_id=run_dir.name,
            turn_dir=turn_dir,
            role_id="developer",
            adapter_id="codex",
            policy=ObservabilityPolicy(raw_retention="keep"),
        )


def test_raw_stream_ignores_only_the_unterminated_tail(tmp_path: Path) -> None:
    stream_path = tmp_path / "stream.jsonl"
    first = (json.dumps(_raw_chunk(), separators=(",", ":")) + "\n").encode()
    atomic_write(stream_path, first + b'{"schema_version":1,"seq":2')

    records = iter_stream_records(stream_path)

    assert len(records) == 1
    assert records[0].first_seq == 1
    assert records[0].data == "record\n"


@pytest.mark.parametrize("mutation", ["unknown", "timestamp", "duplicate"])
def test_raw_stream_rejects_non_closed_complete_records(
    tmp_path: Path,
    mutation: str,
) -> None:
    stream_path = tmp_path / "stream.jsonl"
    chunk = _raw_chunk()
    if mutation == "unknown":
        chunk["unexpected"] = True
        line = json.dumps(chunk, separators=(",", ":"))
    elif mutation == "timestamp":
        chunk["observed_at"] = "not-a-time"
        line = json.dumps(chunk, separators=(",", ":"))
    else:
        line = json.dumps(chunk, separators=(",", ":"))[:-1] + ',"seq":1}'
    atomic_write(stream_path, (line + "\n").encode())

    with pytest.raises(IntegrityError):
        iter_stream_records(stream_path)


@pytest.mark.parametrize("mutation", ["unknown", "timestamp", "duplicate"])
def test_normalized_trace_rejects_non_closed_events(
    tmp_path: Path,
    mutation: str,
) -> None:
    turn_dir = _turn(tmp_path / "at-trace-event-schema")
    event = _trace_event(turn_dir)
    if mutation == "unknown":
        event["unexpected"] = True
        line = json.dumps(event, separators=(",", ":"))
    elif mutation == "timestamp":
        event["observed_at"] = "not-a-time"
        line = json.dumps(event, separators=(",", ":"))
    else:
        line = json.dumps(event, separators=(",", ":"))[:-1] + ',"trace_seq":1}'
    atomic_write(turn_dir / "trace.jsonl", (line + "\n").encode())

    with pytest.raises(IntegrityError):
        read_trace_events(turn_dir)

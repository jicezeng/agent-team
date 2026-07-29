from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_team.config import ObservabilityPolicy
from agent_team.errors import IntegrityError
from agent_team.supervisor import StreamRecorder, _base_snapshot
from agent_team.trace import (
    finalize_turn_trace,
    live_trace_events,
    read_trace_events,
    validate_trace_manifest,
)
from agent_team.util import atomic_json, atomic_write


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


def _turn(run_dir: Path) -> Path:
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    atomic_write(turn_dir / "input.md", b"# Input\n\nInspect the implementation.\n")
    return turn_dir


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
    policy = ObservabilityPolicy(max_trace_bytes=1024)

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

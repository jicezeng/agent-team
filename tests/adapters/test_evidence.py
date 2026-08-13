from __future__ import annotations

import json

import pytest

from agent_team.adapters.base import (
    AdapterEvidence,
    AdapterEvidenceSnapshot,
    ProcessResult,
    StreamRecord,
)
from agent_team.adapters.claude_code import ClaudeCodeAdapter
from agent_team.adapters.codex import CodexAdapter
from agent_team.errors import IntegrityError

from ._support import record


def test_codex_structured_evidence() -> None:
    adapter = CodexAdapter()
    snapshot = AdapterEvidenceSnapshot()
    snapshot.merge(
        adapter.parse_stream_record(
            record({"type": "thread.started", "thread_id": "thread-1"})
        )
    )
    snapshot.merge(adapter.parse_stream_record(record({"type": "turn.completed"})))

    assert snapshot.agent_execution_started
    assert snapshot.adapter_completed
    assert snapshot.observed_session_ref == "thread-1"


def test_codex_normalizes_mcp_tool_lifecycle() -> None:
    adapter = CodexAdapter()
    started = adapter.normalize_stream_record(
        record(
            {
                "type": "item.started",
                "item": {
                    "id": "mcp-1",
                    "type": "mcp_tool_call",
                    "arguments": {"query": "status"},
                    "status": "in_progress",
                },
            }
        )
    )
    completed = adapter.normalize_stream_record(
        record(
            {
                "type": "item.completed",
                "item": {
                    "id": "mcp-1",
                    "type": "mcp_tool_call",
                    "result": {"ok": True},
                    "status": "completed",
                },
            }
        )
    )

    assert started[0].event_type == "tool_call"
    assert started[0].data["tool"] == "mcp_tool_call"
    assert completed[0].event_type == "tool_result"
    assert completed[0].data["output"] == {"ok": True}


def test_claude_structured_evidence() -> None:
    adapter = ClaudeCodeAdapter()
    snapshot = AdapterEvidenceSnapshot()
    snapshot.merge(
        adapter.parse_stream_record(
            record({"type": "system", "subtype": "init", "session_id": "session-1"})
        )
    )
    snapshot.merge(
        adapter.parse_stream_record(
            record({"type": "assistant", "session_id": "session-1"})
        )
    )
    snapshot.merge(
        adapter.parse_stream_record(
            record(
                {
                    "type": "result",
                    "subtype": "success",
                    "session_id": "session-1",
                    "is_error": False,
                }
            )
        )
    )

    assert snapshot.agent_execution_started
    assert snapshot.adapter_completed
    assert snapshot.observed_session_ref == "session-1"


def test_claude_normalizes_messages_tools_reasoning_and_usage() -> None:
    adapter = ClaudeCodeAdapter()
    values = [
        {
            "type": "assistant",
            "session_id": "session-1",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "Checked the boundary."},
                    {"type": "text", "text": "I found one issue."},
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "Read",
                        "input": {"file_path": "src/app.py"},
                    },
                ]
            },
        },
        {
            "type": "user",
            "session_id": "session-1",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "file contents",
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "session_id": "session-1",
            "is_error": False,
            "usage": {"input_tokens": 10, "output_tokens": 4},
            "total_cost_usd": 0.01,
            "duration_ms": 1200,
            "num_turns": 2,
        },
    ]

    events = [
        event
        for value in values
        for event in adapter.normalize_stream_record(record(value))
    ]

    assert [event.event_type for event in events] == [
        "diagnostic",
        "agent_message",
        "tool_call",
        "tool_result",
        "usage",
    ]
    assert events[0].data["redacted_private_reasoning"] is True
    assert "Checked the boundary" not in json.dumps([event.data for event in events])
    assert events[2].data["tool"] == "Read"
    assert events[3].data["tool_call_id"] == "tool-1"
    assert events[4].data["total_cost_usd"] == 0.01


def test_claude_exposed_reasoning_summary_is_retained() -> None:
    adapter = ClaudeCodeAdapter()
    events = adapter.normalize_stream_record(
        record(
            {
                "type": "assistant",
                "session_id": "session-1",
                "message": {
                    "content": [
                        {
                            "type": "reasoning_summary",
                            "summary": "Exposed summary text.",
                        },
                    ]
                },
            }
        )
    )
    assert len(events) == 1
    assert events[0].event_type == "reasoning_summary"
    assert events[0].data["text"] == "Exposed summary text."
    assert "Checked the boundary" not in json.dumps(events[0].data)


def test_claude_private_thinking_text_is_never_in_trace() -> None:
    adapter = ClaudeCodeAdapter()
    secret_reasoning = "SECRET_PRIVATE_REASONING_DO_NOT_LEAK_XYZ"
    events = adapter.normalize_stream_record(
        record(
            {
                "type": "assistant",
                "session_id": "session-1",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": secret_reasoning},
                        {"type": "text", "text": "Public response text."},
                    ]
                },
            }
        )
    )
    event_types = [event.event_type for event in events]
    assert event_types == ["diagnostic", "agent_message"]
    serialized = json.dumps([event.data for event in events])
    assert secret_reasoning not in serialized
    assert events[0].data["redacted_private_reasoning"] is True
    assert events[1].data["text"] == "Public response text."


def test_claude_generic_reasoning_text_is_never_in_trace() -> None:
    adapter = ClaudeCodeAdapter()
    secret_reasoning = "SECRET_GENERIC_REASONING_DO_NOT_LEAK_ABC"
    events = adapter.normalize_stream_record(
        record(
            {
                "type": "assistant",
                "session_id": "session-1",
                "message": {
                    "content": [
                        {"type": "reasoning", "text": secret_reasoning},
                        {"type": "text", "text": "Public response text."},
                    ]
                },
            }
        )
    )
    event_types = [event.event_type for event in events]
    assert event_types == ["diagnostic", "agent_message"]
    serialized = json.dumps([event.data for event in events])
    assert secret_reasoning not in serialized
    assert events[0].data["redacted_private_reasoning"] is True
    assert events[0].data["block_type"] == "reasoning"
    assert events[1].data["text"] == "Public response text."


def test_claude_structured_missing_session_is_normalized_and_sticky() -> None:
    adapter = ClaudeCodeAdapter()
    snapshot = AdapterEvidenceSnapshot()
    unavailable = adapter.parse_stream_record(
        record(
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "num_turns": 0,
                "session_id": "do-not-accept-this-candidate",
                "errors": [
                    "No conversation found with session ID: expired-secret-session"
                ],
            }
        )
    )

    assert unavailable == AdapterEvidence(
        session_unavailable_reason="session_not_found"
    )
    assert snapshot.merge(unavailable)
    assert snapshot.session_unavailable_reason == "session_not_found"
    assert snapshot.observed_session_ref is None

    # Claude can emit init for a new, unrelated candidate after the rejection.
    # It must not silently turn the failed resume into a fresh session.
    assert not snapshot.merge(
        adapter.parse_stream_record(
            record(
                {
                    "type": "system",
                    "subtype": "init",
                    "session_id": "unapproved-fresh-session",
                }
            )
        )
    )
    assert snapshot.observed_session_ref is None
    with pytest.raises(IntegrityError):
        snapshot.merge(AdapterEvidence(agent_execution_started=True))


@pytest.mark.parametrize(
    "value",
    [
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "num_turns": 1,
            "errors": ["No conversation found with session ID: old"],
        },
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "num_turns": 0,
            "errors": ["network failure"],
        },
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "num_turns": 0,
            "errors": "No conversation found with session ID: old",
        },
    ],
)
def test_claude_does_not_guess_session_unavailable(value: dict) -> None:
    assert ClaudeCodeAdapter().parse_stream_record(record(value)) is None


def test_non_json_text_is_not_workflow_evidence() -> None:
    adapter = CodexAdapter()
    result = adapter.parse_stream_record(
        StreamRecord(
            source="stdout",
            first_seq=1,
            last_seq=1,
            observed_at="2026-07-28T00:00:00.000Z",
            encoding="utf-8",
            data="Review passed, complete!\n",
        )
    )
    assert result is None


@pytest.mark.parametrize(
    "value",
    [
        {"type": []},
        {"type": "error", "error": {"code": []}},
    ],
)
def test_codex_ignores_unhashable_structured_discriminators(value: dict) -> None:
    assert CodexAdapter().parse_stream_record(record(value)) is None


def test_normal_completion_requires_observed_session_ref() -> None:
    adapter = CodexAdapter()
    result = ProcessResult(
        process_exit_code=0,
        termination_kind="normal",
        group_quiescent=True,
    )
    evidence = AdapterEvidenceSnapshot(
        agent_execution_started=True,
        adapter_completed=True,
    )

    assert not adapter.classify_result(result, evidence).is_normal_completion

    evidence.observed_session_ref = "thread-1"
    assert adapter.classify_result(result, evidence).is_normal_completion


def test_interactive_completion_preserves_signal_exit_as_action() -> None:
    adapter = CodexAdapter()
    result = ProcessResult(
        process_exit_code=-15,
        termination_kind="action",
        group_quiescent=True,
        launch_mode="interactive",
    )
    evidence = AdapterEvidenceSnapshot(
        agent_execution_started=True,
        adapter_completed=True,
        observed_session_ref="thread-1",
    )

    assert adapter.classify_result(result, evidence).is_normal_completion

    result = ProcessResult(
        process_exit_code=-15,
        termination_kind="signal",
        group_quiescent=True,
        launch_mode="interactive",
    )
    assert not adapter.classify_result(result, evidence).is_normal_completion

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team.adapters.base import (
    AdapterEvidence,
    AdapterEvidenceSnapshot,
    LaunchSpec,
    ProcessResult,
    StreamRecord,
    TurnLaunchContext,
)
from agent_team.adapters.claude_code import ClaudeCodeAdapter
from agent_team.adapters.codex import CodexAdapter
from agent_team.errors import AgentTeamError, IntegrityError


def record(value: dict) -> StreamRecord:
    return StreamRecord(
        source="stdout",
        first_seq=1,
        last_seq=1,
        observed_at="2026-07-28T00:00:00.000Z",
        encoding="utf-8",
        data=json.dumps(value) + "\n",
    )


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


def test_codex_start_and_resume_freeze_equivalent_permissions(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "fixed state"
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: state_dir,
    )
    adapter = CodexAdapter()

    mappings = adapter.profile_mappings()["default"]

    assert mappings["start"] == mappings["resume"]
    rendered = " ".join(mappings["start"])
    assert "--ignore-user-config" in mappings["start"]
    assert "--ignore-rules" in mappings["start"]
    assert 'sandbox_mode="workspace-write"' in rendered
    assert 'approval_policy="never"' in rendered
    assert "sandbox_workspace_write.network_access=false" in rendered
    roots_option = next(
        item
        for item in mappings["start"]
        if item.startswith("sandbox_workspace_write.writable_roots=")
    )
    roots = json.loads(roots_option.split("=", 1)[1])
    assert roots == [
        str(state_dir / "workspace-locks"),
        str(state_dir / "workspaces"),
    ]


def test_resume_profile_rejects_non_equivalent_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/codex"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "test")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        adapter,
        "profile_mappings",
        lambda: {
            "default": {
                "start": ["--permission", "write"],
                "resume": ["--permission", "read"],
            }
        },
    )

    with pytest.raises(AgentTeamError) as rejected:
        adapter.profile_fingerprint("default", "resume")

    assert rejected.value.code == "RESUME_PERMISSION_MISMATCH"


def test_fresh_profile_rejects_missing_start_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/codex"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "test")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        adapter,
        "profile_mappings",
        lambda: {"default": {"resume": ["--permission", "write"]}},
    )

    with pytest.raises(AgentTeamError) as rejected:
        adapter.profile_fingerprint("default", "fresh")

    assert rejected.value.code == "START_PROFILE_UNSUPPORTED"


def launch_context(
    *,
    adapter,
    session_policy: str,
    session_ref: str | None,
) -> TurnLaunchContext:
    profile_hash = adapter.profile_fingerprint("default", session_policy)
    return TurnLaunchContext(
        run_id="at-adapter-test",
        role_id="developer",
        turn_id="turn-0001",
        workspace="/tmp/workspace",
        turn_dir="/tmp/workspace/.agent-team/runs/at-adapter-test/turns/turn-0001",
        prompt="perform the turn",
        session_policy=session_policy,
        session_ref=session_ref,
        session_generation=1,
        launch_profile="default",
        launch_profile_sha256=profile_hash,
        agent_team_cli="/usr/local/bin/agent-team",
    )


def test_claude_launch_reads_text_prompt_from_stdin(monkeypatch) -> None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: __import__("pathlib").Path("/bin/claude"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "2.1.25")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)

    start = adapter.prepare_launch(
        launch_context(adapter=adapter, session_policy="resume", session_ref=None)
    )
    resumed = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref="550e8400-e29b-41d4-a716-446655440000",
        )
    )

    assert "--input-format" in start.argv
    assert start.argv[start.argv.index("--input-format") + 1] == "text"
    assert start.argv[-1] != "-"
    assert resumed.argv[-1] != "-"
    assert start.stdin == "perform the turn"
    assert resumed.stdin == "perform the turn"
    expected = adapter.profile_mappings()["default"]["start"]
    assert expected == adapter.profile_mappings()["default"]["resume"]
    assert "--setting-sources" in expected
    assert expected[expected.index("--setting-sources") + 1] == ""
    assert "--strict-mcp-config" in expected
    assert "--tools" in expected
    assert "--plugin-dir" in expected
    assert start.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert resumed.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert LaunchSpec.from_json(start.to_json()) == start
    assert LaunchSpec.from_json(resumed.to_json()) == resumed


def test_codex_launch_uses_frozen_permissions_for_start_and_resume(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: state_dir,
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/codex"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "0.145.0")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)

    start = adapter.prepare_launch(
        launch_context(adapter=adapter, session_policy="resume", session_ref=None)
    )
    resumed = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref="019fa804-8bc9-7bc3-a8e9-baf8cee27430",
        )
    )

    expected = adapter.profile_mappings()["default"]["start"]
    assert all(item in start.argv for item in expected)
    assert all(item in resumed.argv for item in expected)
    assert "--skip-git-repo-check" not in start.argv
    assert "--skip-git-repo-check" not in resumed.argv

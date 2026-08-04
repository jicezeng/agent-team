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
from agent_team.bootstrap import parse_role_spec
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
                        {"type": "reasoning_summary", "summary": "Exposed summary text."},
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


def test_codex_exposes_explicit_elevated_profiles(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "fixed state"
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: state_dir,
    )
    adapter = CodexAdapter()

    mappings = adapter.profile_mappings()

    assert set(mappings) == {"default", "trusted-workspace", "full-access"}
    assert all(
        mapping["start"] == mapping["resume"] for mapping in mappings.values()
    )
    assert all(
        "--ignore-user-config" in mapping["start"]
        and "--ignore-rules" in mapping["start"]
        for mapping in mappings.values()
    )
    default = " ".join(mappings["default"]["start"])
    trusted = " ".join(mappings["trusted-workspace"]["start"])
    full = " ".join(mappings["full-access"]["start"])
    assert 'sandbox_mode="workspace-write"' in default
    assert "sandbox_workspace_write.network_access=false" in default
    assert 'sandbox_mode="workspace-write"' in trusted
    assert "sandbox_workspace_write.network_access=true" in trusted
    assert 'sandbox_mode="danger-full-access"' in full
    assert 'approval_policy="never"' in full
    assert "sandbox_workspace_write." not in full


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
    profile: str = "default",
) -> TurnLaunchContext:
    profile_hash = adapter.profile_fingerprint(profile, session_policy)
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
        launch_profile=profile,
        launch_profile_sha256=profile_hash,
        agent_team_cli="/usr/local/bin/agent-team",
    )


def test_claude_launch_reads_text_prompt_from_stdin(monkeypatch) -> None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: __import__("pathlib").Path("/bin/claude"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "2.1.25")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.claude_internal_tmpdir",
        lambda: Path("/tmp/claude-501"),
    )

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
    assert "--settings" in expected
    settings = json.loads(expected[expected.index("--settings") + 1])
    assert settings == {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "excludedCommands": [
                "/opt/agent-team/bin/agent-team handoff *",
                "/opt/agent-team/bin/agent-team complete *",
                "/opt/agent-team/bin/agent-team block *",
            ],
            "filesystem": {
                "allowWrite": ["/tmp/claude-501"],
            },
        }
    }
    allowed_index = expected.index("--allowedTools")
    denied_index = expected.index("--disallowedTools")
    assert expected[allowed_index + 1 : denied_index] == [
        "Bash(/opt/agent-team/bin/agent-team handoff *)",
        "Bash(/opt/agent-team/bin/agent-team complete *)",
        "Bash(/opt/agent-team/bin/agent-team block *)",
    ]
    assert "Bash(/opt/agent-team/bin/agent-team cancel *)" in expected
    assert "Bash(/opt/agent-team/bin/agent-team origin-*)" in expected
    assert start.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert resumed.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"
    assert LaunchSpec.from_json(start.to_json()) == start
    assert LaunchSpec.from_json(resumed.to_json()) == resumed


def test_claude_exposes_explicit_elevated_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.effective_claude_plugin",
        lambda: Path("/opt/agent-team/claude-plugin"),
    )
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.claude_internal_tmpdir",
        lambda: Path("/tmp/claude-501"),
    )
    mappings = ClaudeCodeAdapter().profile_mappings()

    assert set(mappings) == {"default", "trusted-workspace", "full-access"}
    assert all(
        mapping["start"] == mapping["resume"] for mapping in mappings.values()
    )
    for mapping in mappings.values():
        argv = mapping["start"]
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in argv
        assert "Bash(/opt/agent-team/bin/agent-team cancel *)" in argv
        assert "Bash(/opt/agent-team/bin/agent-team origin-*)" in argv

    def permission_mode(profile: str) -> str:
        argv = mappings[profile]["start"]
        return argv[argv.index("--permission-mode") + 1]

    def settings(profile: str) -> dict:
        argv = mappings[profile]["start"]
        return json.loads(argv[argv.index("--settings") + 1])

    assert permission_mode("default") == "acceptEdits"
    assert permission_mode("trusted-workspace") == "acceptEdits"
    assert permission_mode("full-access") == "bypassPermissions"
    assert "bypassPermissions" not in mappings["trusted-workspace"]["start"]
    assert settings("default")["sandbox"]["enabled"] is True
    assert settings("default")["sandbox"]["allowUnsandboxedCommands"] is False
    assert settings("trusted-workspace") == settings("default")
    assert settings("full-access") == {"sandbox": {"enabled": False}}


@pytest.mark.parametrize(
    ("adapter_id", "profile"),
    [
        ("codex", "trusted-workspace"),
        ("codex", "full-access"),
        ("claude-code", "trusted-workspace"),
        ("claude-code", "full-access"),
    ],
)
def test_role_spec_accepts_explicit_elevated_profiles(
    monkeypatch: pytest.MonkeyPatch,
    adapter_id: str,
    profile: str,
) -> None:
    expected_hash = "a" * 64

    class StubAdapter:
        def profile_fingerprint(
            self,
            selected_profile: str,
            session_policy: str,
        ) -> str:
            assert selected_profile == profile
            assert session_policy == "resume"
            return expected_hash

    monkeypatch.setattr(
        "agent_team.bootstrap.get_adapter",
        lambda selected_adapter: StubAdapter(),
    )

    role_id, role = parse_role_spec(f"qa={adapter_id}:resume:{profile}")

    assert role_id == "qa"
    assert role.adapter == adapter_id
    assert role.session_policy == "resume"
    assert role.launch_profile == profile
    assert role.launch_profile_sha256 == expected_hash


@pytest.mark.parametrize("profile", ["trusted-workspace", "full-access"])
def test_claude_elevated_profiles_apply_to_start_and_resume(
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/claude"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "2.1.25")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.claude_internal_tmpdir",
        lambda: Path("/tmp/claude-501"),
    )
    start = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=None,
            profile=profile,
        )
    )
    resumed = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref="550e8400-e29b-41d4-a716-446655440000",
            profile=profile,
        )
    )

    expected = adapter.profile_mappings()[profile]["start"]
    assert all(item in start.argv for item in expected)
    assert all(item in resumed.argv for item in expected)
    assert start.launch_profile == profile
    assert resumed.launch_profile == profile


def test_claude_profile_rejects_relative_internal_tmpdir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", "relative-tmp")
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )

    with pytest.raises(AgentTeamError) as rejected:
        ClaudeCodeAdapter().profile_mappings()

    assert rejected.value.code == "CLAUDE_CODE_TMPDIR_INVALID"


def test_claude_version_probe_uses_an_isolated_config_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ClaudeCodeAdapter()
    observed_config_dir: Path | None = None

    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/claude"))

    def run(command, **kwargs):
        nonlocal observed_config_dir
        assert command == ["/bin/claude", "--version"]
        observed_config_dir = Path(kwargs["env"]["CLAUDE_CONFIG_DIR"])
        assert observed_config_dir.is_dir()
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 10
        return __import__("subprocess").CompletedProcess(
            command,
            0,
            stdout="2.1.25 (Claude Code)\n",
            stderr="",
        )

    monkeypatch.setattr("agent_team.adapters.claude_code.subprocess.run", run)

    assert adapter.executable_version() == "2.1.25 (Claude Code)"
    assert observed_config_dir is not None
    assert not observed_config_dir.exists()


@pytest.mark.parametrize("profile", ["default", "trusted-workspace", "full-access"])
def test_codex_launch_uses_frozen_permissions_for_start_and_resume(
    monkeypatch,
    tmp_path: Path,
    profile: str,
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
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=None,
            profile=profile,
        )
    )
    resumed = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref="019fa804-8bc9-7bc3-a8e9-baf8cee27430",
            profile=profile,
        )
    )

    expected = adapter.profile_mappings()[profile]["start"]
    assert all(item in start.argv for item in expected)
    assert all(item in resumed.argv for item in expected)
    assert "--skip-git-repo-check" not in start.argv
    assert "--skip-git-repo-check" not in resumed.argv

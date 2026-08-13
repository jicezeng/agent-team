from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from agent_team.adapters.base import (
    AdapterEvidence,
    AdapterEvidenceSnapshot,
    HarnessAdapter,
    HarnessLaunchOptions,
    LaunchSpec,
    ProcessResult,
    StreamRecord,
    TurnLaunchContext,
)
from agent_team.adapters.claude_code import ClaudeCodeAdapter
from agent_team.adapters.codex import CodexAdapter
from agent_team.bootstrap import parse_role_spec
from agent_team.errors import AgentTeamError, IntegrityError, InvalidArgument


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


def test_codex_start_and_resume_freeze_equivalent_permissions() -> None:
    adapter = CodexAdapter()

    mappings = adapter.profile_mappings()["default"]

    assert mappings["start"] == mappings["resume"]
    rendered = " ".join(mappings["start"])
    assert "--ignore-user-config" in mappings["start"]
    assert "--ignore-rules" in mappings["start"]
    assert 'sandbox_mode="workspace-write"' in rendered
    assert 'approval_policy="never"' in rendered
    assert "features.hooks=false" in mappings["start"]
    assert "sandbox_workspace_write.network_access=false" in rendered
    assert "sandbox_workspace_write.writable_roots=[]" in mappings["start"]


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
    assert all(
        "features.hooks=false" in mapping["start"]
        for mapping in mappings.values()
    )
    default = " ".join(mappings["default"]["start"])
    trusted = " ".join(mappings["trusted-workspace"]["start"])
    full = " ".join(mappings["full-access"]["start"])
    assert 'sandbox_mode="workspace-write"' in default
    assert "sandbox_workspace_write.network_access=false" in default
    assert 'sandbox_mode="workspace-write"' in trusted
    assert "sandbox_workspace_write.network_access=true" in trusted
    assert (
        "sandbox_workspace_write.writable_roots=[]"
        in mappings["trusted-workspace"]["start"]
    )
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
        lambda _launch_mode="headless": {
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
        lambda _launch_mode="headless": {
            "default": {"resume": ["--permission", "write"]}
        },
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
    model: str | None = None,
    reasoning_effort: str | None = None,
    fast_mode: bool | None = None,
    launch_mode: str = "headless",
    workspace: str = "/tmp/workspace",
    turn_dir: str = (
        "/tmp/workspace/.agent-team/runs/at-adapter-test/turns/turn-0001"
    ),
) -> TurnLaunchContext:
    profile_hash = adapter.profile_fingerprint(
        profile,
        session_policy,
        launch_mode,
    )
    return TurnLaunchContext(
        run_id="at-adapter-test",
        role_id="developer",
        turn_id="turn-0001",
        workspace=workspace,
        turn_dir=turn_dir,
        prompt="perform the turn",
        session_policy=session_policy,
        session_ref=session_ref,
        session_generation=1,
        launch_profile=profile,
        launch_profile_sha256=profile_hash,
        agent_team_cli="/usr/local/bin/agent-team",
        model=model,
        reasoning_effort=reasoning_effort,
        fast_mode=fast_mode,
        launch_mode=launch_mode,
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


def test_codex_launch_applies_model_effort_and_fast_to_start_and_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: tmp_path / "state",
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/codex"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "0.146.0")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    contexts = [
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=session_ref,
            model="gpt-5.6-sol",
            reasoning_effort="max",
            fast_mode=True,
        )
        for session_ref in (None, "019fa804-8bc9-7bc3-a8e9-baf8cee27430")
    ]

    for launch in (adapter.prepare_launch(context) for context in contexts):
        assert launch.argv[launch.argv.index("--model") + 1] == "gpt-5.6-sol"
        assert 'model_reasoning_effort="max"' in launch.argv
        assert 'service_tier="fast"' in launch.argv
        fast_index = launch.argv.index("--enable")
        assert launch.argv[fast_index + 1] == "fast_mode"


def test_claude_launch_applies_model_and_effort_to_start_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/claude"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "2.1.111")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.claude_internal_tmpdir",
        lambda: Path("/tmp/claude-501"),
    )
    contexts = [
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=session_ref,
            model="opus",
            reasoning_effort="xhigh",
        )
        for session_ref in (None, "550e8400-e29b-41d4-a716-446655440000")
    ]

    for launch in (adapter.prepare_launch(context) for context in contexts):
        assert launch.argv[launch.argv.index("--model") + 1] == "opus"
        assert launch.env["CLAUDE_CODE_EFFORT_LEVEL"] == "xhigh"


def test_codex_interactive_launch_uses_isolated_native_tui_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    source_home = tmp_path / "user-codex-home"
    source_home.mkdir()
    (source_home / "config.toml").write_text(
        "[mcp_servers.must_not_load]\ncommand = 'unsafe'\n",
        encoding="utf-8",
    )
    source_auth = source_home / "auth.json"
    source_auth.write_text('{"token":"test-only"}\n', encoding="utf-8")
    source_auth.chmod(0o600)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: state_dir,
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/codex"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "0.146.0")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        adapter,
        "_assert_interactive_authentication",
        lambda _home: None,
    )

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )
    context = launch_context(
        adapter=adapter,
        session_policy="resume",
        session_ref=None,
        model="gpt-5.6-sol",
        reasoning_effort="max",
        fast_mode=True,
        launch_mode="interactive",
        workspace=str(workspace),
        turn_dir=str(turn_dir),
    )
    launch = adapter.prepare_launch(context)
    isolated_home = Path(launch.env["CODEX_HOME"])

    assert isolated_home != source_home
    assert tomllib.loads(
        (isolated_home / "config.toml").read_text(encoding="utf-8")
    ) == {
        "projects": {str(workspace): {"trust_level": "trusted"}}
    }
    assert (isolated_home / "auth.json").read_bytes() == source_auth.read_bytes()
    assert (isolated_home / "auth.json").stat().st_ino != source_auth.stat().st_ino
    assert launch.launch_mode == "interactive"
    assert launch.argv[0] == "/bin/codex"
    assert "exec" not in launch.argv
    assert "--ignore-user-config" not in launch.argv
    assert "--ignore-rules" not in launch.argv
    assert "features.hooks=false" in launch.argv
    assert "sandbox_workspace_write.writable_roots=[]" in launch.argv
    assert "--no-alt-screen" in launch.argv
    assert launch.prompt_file == str(turn_dir / "process" / "prompt.md")
    assert launch.expected_session_ref is None

    resumed = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref="019fa804-8bc9-7bc3-a8e9-baf8cee27430",
            launch_mode="interactive",
            workspace=str(workspace),
            turn_dir=str(turn_dir),
        )
    )
    assert resumed.argv[1] == "resume"
    assert "features.hooks=false" in resumed.argv
    assert "sandbox_workspace_write.writable_roots=[]" in resumed.argv
    assert resumed.expected_session_ref == "019fa804-8bc9-7bc3-a8e9-baf8cee27430"

    generated = isolated_home / "plugins" / "cache"
    generated.mkdir(parents=True)
    generated.chmod(0o755)
    cache_file = generated / "metadata.json"
    cache_file.write_text("{}\n", encoding="utf-8")
    cache_file.chmod(0o644)
    session_file = isolated_home / "sessions" / "kept.jsonl"
    session_file.parent.mkdir()
    session_file.write_text("{}\n", encoding="utf-8")
    session_file.chmod(0o644)
    temporary = isolated_home / "tmp" / "arg0"
    temporary.mkdir(parents=True)
    (temporary / "wrapper").symlink_to("/bin/codex")

    adapter.finalize_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )

    assert not (isolated_home / "tmp").exists()
    assert session_file.read_text(encoding="utf-8") == "{}\n"
    for path in (
        state_dir / "harness-homes",
        state_dir / "harness-homes" / "codex",
        isolated_home,
        *isolated_home.rglob("*"),
    ):
        assert not path.is_symlink()
        assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    ("legacy_config", "should_migrate"),
    [
        (b"", True),
        (b"[mcp_servers.must_not_replace]\ncommand = 'unsafe'\n", False),
    ],
)
def test_codex_interactive_run_state_only_migrates_owned_legacy_empty_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    legacy_config: bytes,
    should_migrate: bool,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-legacy"
    run_dir.mkdir(parents=True)
    source_home = tmp_path / "user-codex-home"
    source_home.mkdir()
    source_auth = source_home / "auth.json"
    source_auth.write_text('{"token":"test-only"}\n', encoding="utf-8")
    source_auth.chmod(0o600)
    state_dir = tmp_path / "state"
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: state_dir,
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(
        adapter,
        "_assert_interactive_authentication",
        lambda _home: None,
    )
    home = adapter._interactive_home(run_dir, "developer")
    home.mkdir(parents=True)
    marker_path = home / "agent-team-home.json"
    marker_path.write_text(
        json.dumps(adapter._interactive_marker(run_dir, "developer")),
        encoding="utf-8",
    )
    marker_path.chmod(0o600)
    config_path = home / "config.toml"
    config_path.write_bytes(legacy_config)
    config_path.chmod(0o600)

    if should_migrate:
        adapter.prepare_run_state(
            run_dir=run_dir,
            role_id="developer",
            launch_mode="interactive",
        )
        assert config_path.read_bytes() == adapter._interactive_config(run_dir)
    else:
        with pytest.raises(IntegrityError, match="unexpected content"):
            adapter.prepare_run_state(
                run_dir=run_dir,
                role_id="developer",
                launch_mode="interactive",
            )
        assert config_path.read_bytes() == legacy_config


def test_codex_interactive_session_refs_are_scoped_to_home_and_workspace(
    tmp_path: Path,
) -> None:
    home = tmp_path / "isolated-home"
    session_dir = home / "sessions" / "2026" / "08" / "05"
    session_dir.mkdir(parents=True)
    matching = {
        "type": "session_meta",
        "payload": {"id": "matching-session", "cwd": "/worktree"},
    }
    other = {
        "type": "session_meta",
        "payload": {"id": "other-session", "cwd": "/other"},
    }
    (session_dir / "matching.jsonl").write_text(
        json.dumps(matching) + "\n{}\n",
        encoding="utf-8",
    )
    (session_dir / "other.jsonl").write_text(
        json.dumps(other) + "\n{}\n",
        encoding="utf-8",
    )
    launch = LaunchSpec(
        adapter_id="codex",
        argv=("/bin/codex",),
        cwd="/worktree",
        env={"CODEX_HOME": str(home)},
        stdin="prompt",
        launch_profile="default",
        launch_profile_sha256="0" * 64,
        starts_new_session=True,
        launch_mode="interactive",
        prompt_file="/run/turn/process/prompt.md",
    )

    assert CodexAdapter().interactive_session_refs(launch) == {
        "matching-session"
    }


def test_claude_interactive_launch_uses_native_tui_and_known_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/claude"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "2.1.111")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.claude_internal_tmpdir",
        lambda: Path("/tmp/claude-501"),
    )
    monkeypatch.setattr(
        adapter,
        "_assert_interactive_workspace_trusted",
        lambda _workspace: None,
    )

    launch = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=None,
            model="opus",
            reasoning_effort="high",
            launch_mode="interactive",
        )
    )

    assert launch.argv[0] == "/bin/claude"
    assert "-p" not in launch.argv
    assert "--output-format" not in launch.argv
    assert "--session-id" in launch.argv
    assert launch.expected_session_ref == launch.argv[
        launch.argv.index("--session-id") + 1
    ]
    assert launch.prompt_file is not None
    assert launch.env["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    assert "--setting-sources" in launch.argv
    assert launch.argv[launch.argv.index("--setting-sources") + 1] == ""


def test_claude_interactive_run_state_requires_pretrusted_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    run_dir.mkdir(parents=True)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    adapter = ClaudeCodeAdapter()

    with pytest.raises(AgentTeamError) as rejected:
        adapter.prepare_run_state(
            run_dir=run_dir,
            role_id="developer",
            launch_mode="interactive",
        )

    assert rejected.value.code == "HARNESS_WORKSPACE_TRUST_REQUIRED"
    assert f"cd {workspace} && claude" in rejected.value.message

    (config_dir / ".claude.json").write_text(
        json.dumps(
            {
                "projects": {
                    str(tmp_path): {"hasTrustDialogAccepted": True},
                }
            }
        ),
        encoding="utf-8",
    )

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )


@pytest.mark.parametrize(
    ("current_trusted", "legacy_trusted"),
    [(True, False), (False, True)],
)
def test_claude_default_state_prefers_current_config_then_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    current_trusted: bool,
    legacy_trusted: bool,
) -> None:
    home = tmp_path / "home"
    current_dir = home / ".claude"
    current_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    current_path = current_dir / ".config.json"
    legacy_path = home / ".claude.json"
    current_path.write_text(
        json.dumps(
            {
                "projects": {
                    str(workspace): {
                        "hasTrustDialogAccepted": current_trusted,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    legacy_path.write_text(
        json.dumps(
            {
                "projects": {
                    str(workspace): {
                        "hasTrustDialogAccepted": legacy_trusted,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.Path.home",
        lambda: home,
    )

    assert ClaudeCodeAdapter._user_state_path() == current_path
    assert ClaudeCodeAdapter._workspace_is_trusted(workspace) is current_trusted

    current_path.unlink()
    assert ClaudeCodeAdapter._user_state_path() == legacy_path
    assert ClaudeCodeAdapter._workspace_is_trusted(workspace) is legacy_trusted


def test_claude_interactive_trust_rejects_invalid_user_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / ".config.json").write_text(
        '{"projects": []}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))

    with pytest.raises(AgentTeamError) as rejected:
        ClaudeCodeAdapter._assert_interactive_workspace_trusted(workspace)

    assert rejected.value.code == "HARNESS_USER_CONFIG_INVALID"


def test_launch_spec_reads_legacy_headless_schema() -> None:
    legacy = {
        "adapter_id": "codex",
        "argv": ["/bin/codex", "exec", "-"],
        "cwd": "/worktree",
        "env": {},
        "stdin": "prompt",
        "launch_profile": "default",
        "launch_profile_sha256": "0" * 64,
        "starts_new_session": True,
    }

    parsed = LaunchSpec.from_json(legacy)

    assert parsed.schema_version == 1
    assert parsed.launch_mode == "headless"
    assert parsed.to_json() == legacy


@pytest.mark.parametrize("adapter", [CodexAdapter(), ClaudeCodeAdapter()])
def test_interactive_terminal_json_is_only_diagnostic(
    adapter: HarnessAdapter,
) -> None:
    record = StreamRecord(
        source="terminal",
        first_seq=1,
        last_seq=1,
        observed_at="2026-08-05T00:00:00Z",
        encoding="utf-8",
        data='{"type":"turn.completed","session_id":"must-not-count"}',
    )

    normalized = adapter.normalize_stream_record(record)

    assert len(normalized) == 1
    assert normalized[0].event_type == "diagnostic"
    assert normalized[0].data["source"] == "terminal"


def test_codex_resolves_isolated_user_model_effort_and_fast_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        (
            'model = "gpt-user-default"\n'
            'model_reasoning_effort = "high"\n'
            'service_tier = "fast"\n'
            "[features]\n"
            "fast_mode = true\n"
            "[mcp_servers.ignored]\n"
            'command = "must-not-be-loaded"\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(config_home))

    options = CodexAdapter().resolve_launch_options(
        model=None,
        reasoning_effort=None,
        fast_mode=None,
    )
    overridden = CodexAdapter().resolve_launch_options(
        model="gpt-explicit",
        reasoning_effort=None,
        fast_mode=None,
    )

    assert options == HarnessLaunchOptions(
        model="gpt-user-default",
        reasoning_effort="high",
        fast_mode=True,
    )
    assert overridden == HarnessLaunchOptions(
        model="gpt-explicit",
        reasoning_effort="high",
        fast_mode=True,
    )


def test_explicit_codex_field_does_not_load_that_user_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        'model = 42\nmodel_reasoning_effort = "medium"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(config_home))

    options = CodexAdapter().resolve_launch_options(
        model="gpt-explicit",
        reasoning_effort=None,
        fast_mode=False,
    )

    assert options == HarnessLaunchOptions(
        model="gpt-explicit",
        reasoning_effort="medium",
        fast_mode=False,
    )


def test_claude_resolves_environment_over_user_model_and_effort_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "claude-home"
    config_home.mkdir()
    (config_home / "settings.json").write_text(
        json.dumps(
            {
                "model": "sonnet",
                "effortLevel": "medium",
                "permissions": {"allow": ["Bash(*)"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_home))
    monkeypatch.setenv("ANTHROPIC_MODEL", "opus")
    monkeypatch.setenv("CLAUDE_CODE_EFFORT_LEVEL", "max")

    options = ClaudeCodeAdapter().resolve_launch_options(
        model=None,
        reasoning_effort=None,
        fast_mode=None,
    )
    overridden = ClaudeCodeAdapter().resolve_launch_options(
        model="haiku",
        reasoning_effort="low",
        fast_mode=None,
    )

    assert options == HarnessLaunchOptions(
        model="opus",
        reasoning_effort="max",
    )
    assert overridden == HarnessLaunchOptions(
        model="haiku",
        reasoning_effort="low",
    )


def test_claude_rejects_codex_fast_mode() -> None:
    with pytest.raises(InvalidArgument, match="only supported by the codex"):
        ClaudeCodeAdapter().resolve_launch_options(
            model="opus",
            reasoning_effort="high",
            fast_mode=True,
        )


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
    assert settings("full-access") == {
        "sandbox": {"enabled": False},
        "skipDangerousModePermissionPrompt": True,
    }
    assert "skipDangerousModePermissionPrompt" not in settings("default")


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
        @staticmethod
        def assert_launch_mode(selected_mode: str) -> None:
            assert selected_mode == "interactive"

        def resolve_launch_options(self, **_kwargs) -> HarnessLaunchOptions:
            return HarnessLaunchOptions(
                fast_mode=False if adapter_id == "codex" else None
            )

        def profile_fingerprint(
            self,
            selected_profile: str,
            session_policy: str,
            launch_mode: str,
        ) -> str:
            assert selected_profile == profile
            assert session_policy == "resume"
            assert launch_mode == "interactive"
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
    assert role.launch_mode == "interactive"


def test_role_spec_freezes_explicit_harness_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StubAdapter:
        @staticmethod
        def assert_launch_mode(selected_mode: str) -> None:
            assert selected_mode == "interactive"

        def resolve_launch_options(self, **values) -> HarnessLaunchOptions:
            assert values == {
                "model": "gpt-explicit",
                "reasoning_effort": "high",
                "fast_mode": True,
            }
            return HarnessLaunchOptions(
                model=values["model"],
                reasoning_effort=values["reasoning_effort"],
                fast_mode=values["fast_mode"],
            )

        def profile_fingerprint(
            self,
            profile: str,
            _policy: str,
            launch_mode: str,
        ) -> str:
            assert profile == "default"
            assert launch_mode == "interactive"
            return "a" * 64

    monkeypatch.setattr(
        "agent_team.bootstrap.get_adapter",
        lambda _adapter_id: StubAdapter(),
    )

    role_id, role = parse_role_spec(
        "reviewer=codex:resume:default",
        model="gpt-explicit",
        reasoning_effort="high",
        fast_mode=True,
    )

    assert role_id == "reviewer"
    assert role.launch_profile == "default"
    assert role.model == "gpt-explicit"
    assert role.reasoning_effort == "high"
    assert role.fast_mode is True


@pytest.mark.parametrize("adapter_id", ["codex", "claude-code"])
def test_role_spec_defaults_external_roles_to_full_access(
    monkeypatch: pytest.MonkeyPatch,
    adapter_id: str,
) -> None:
    class StubAdapter:
        @staticmethod
        def assert_launch_mode(selected_mode: str) -> None:
            assert selected_mode == "interactive"

        @staticmethod
        def resolve_launch_options(**_values) -> HarnessLaunchOptions:
            return HarnessLaunchOptions(
                fast_mode=False if adapter_id == "codex" else None
            )

        @staticmethod
        def profile_fingerprint(
            profile: str,
            policy: str,
            launch_mode: str,
        ) -> str:
            assert profile == "full-access"
            assert policy == "resume"
            assert launch_mode == "interactive"
            return "f" * 64

    monkeypatch.setattr(
        "agent_team.bootstrap.get_adapter",
        lambda selected: StubAdapter(),
    )

    role_id, role = parse_role_spec(f"developer={adapter_id}:resume")

    assert role_id == "developer"
    assert role.launch_profile == "full-access"
    assert role.launch_profile_sha256 == "f" * 64


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

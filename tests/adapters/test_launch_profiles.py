from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team.adapters.base import (
    HarnessLaunchOptions,
    LaunchSpec,
)
from agent_team.adapters.claude_code import ClaudeCodeAdapter
from agent_team.adapters.codex import CodexAdapter
from agent_team.errors import AgentTeamError

from ._support import launch_context


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
    assert all(mapping["start"] == mapping["resume"] for mapping in mappings.values())
    assert all(
        "--ignore-user-config" in mapping["start"]
        and "--ignore-rules" in mapping["start"]
        for mapping in mappings.values()
    )
    assert all(
        "features.hooks=false" in mapping["start"] for mapping in mappings.values()
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


def test_claude_launch_reads_text_prompt_from_stdin(monkeypatch) -> None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(
        adapter, "executable", lambda: __import__("pathlib").Path("/bin/claude")
    )
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
            model_provider="openai",
        )
        for session_ref in (None, "019fa804-8bc9-7bc3-a8e9-baf8cee27430")
    ]

    for launch in (adapter.prepare_launch(context) for context in contexts):
        assert launch.argv[launch.argv.index("--model") + 1] == "gpt-5.6-sol"
        assert 'model_provider="openai"' in launch.argv
        assert 'model_reasoning_effort="max"' in launch.argv
        assert 'service_tier="fast"' in launch.argv
        fast_index = launch.argv.index("--enable")
        assert launch.argv[fast_index + 1] == "fast_mode"


def test_codex_launch_freezes_custom_provider_for_start_and_resume(
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
    monkeypatch.setattr(adapter, "authentication_status", lambda: False)
    provider_config = {
        "name": "Company Proxy",
        "base_url": "https://proxy.example.test/v1",
        "env_key": "COMPANY_PROXY_API_KEY",
        "env_http_headers": {"X-Tenant": "COMPANY_TENANT"},
        "wire_api": "responses",
    }
    contexts = [
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=session_ref,
            model="proxy-model",
            reasoning_effort="high",
            model_provider="company_proxy",
            model_provider_config=provider_config,
        )
        for session_ref in (None, "019fa804-8bc9-7bc3-a8e9-baf8cee27430")
    ]

    for launch in (adapter.prepare_launch(context) for context in contexts):
        rendered = "\n".join(launch.argv)
        assert 'model_provider="company_proxy"' in launch.argv
        assert (
            'model_providers.company_proxy.base_url="https://proxy.example.test/v1"'
            in launch.argv
        )
        assert (
            'model_providers.company_proxy.env_key="COMPANY_PROXY_API_KEY"'
            in launch.argv
        )
        assert (
            "model_providers.company_proxy.env_http_headers="
            '{ "X-Tenant" = "COMPANY_TENANT" }' in launch.argv
        )
        assert 'model_providers.company_proxy.wire_api="responses"' in launch.argv
        assert launch.argv[launch.argv.index("--model") + 1] == "proxy-model"
        assert "provider-secret" not in rendered
        assert "provider-secret" not in json.dumps(launch.to_json())


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


def test_claude_launch_freezes_gateway_route_for_start_and_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/claude"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "2.1.111")
    monkeypatch.setattr(adapter, "authentication_status", lambda: False)
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.claude_internal_tmpdir",
        lambda: Path("/tmp/claude-501"),
    )
    provider_config = {
        "settings": {
            "base_url": "https://gateway.example.test/anthropic",
        },
        "credential_environment_names": ["ANTHROPIC_AUTH_TOKEN"],
    }
    contexts = [
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=session_ref,
            model="gateway-model",
            reasoning_effort="high",
            model_provider="gateway",
            model_provider_config=provider_config,
        )
        for session_ref in (None, "550e8400-e29b-41d4-a716-446655440000")
    ]

    for launch in (adapter.prepare_launch(context) for context in contexts):
        assert launch.env["ANTHROPIC_BASE_URL"] == (
            "https://gateway.example.test/anthropic"
        )
        assert launch.env["CLAUDE_CODE_USE_BEDROCK"] == "0"
        assert launch.env["CLAUDE_CODE_USE_VERTEX"] == "0"
        assert launch.env["CLAUDE_CODE_USE_FOUNDRY"] == "0"
        assert "ANTHROPIC_AUTH_TOKEN" not in launch.env
        assert "must-not-be-persisted" not in json.dumps(launch.to_json())
    options = HarnessLaunchOptions(
        model="gateway-model",
        reasoning_effort="high",
        model_provider="gateway",
        model_provider_config=provider_config,
    )
    assert adapter.worker_environment_names(
        run_dir=Path("/unused"),
        role_id="developer",
        options=options,
    ) == ("ANTHROPIC_AUTH_TOKEN",)

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_team.adapters.base import (
    HarnessAdapter,
    HarnessLaunchOptions,
    LaunchSpec,
    StreamRecord,
)
from agent_team.adapters.claude_code import ClaudeCodeAdapter
from agent_team.adapters.codex import CodexAdapter
from agent_team.adapters.deepseek_harness import DeepSeekHarnessAdapter
from agent_team.adapters.opencode import OpenCodeAdapter
from agent_team.bootstrap import parse_role_spec
from agent_team.errors import AgentTeamError, InvalidArgument

from ._support import launch_context


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


@pytest.mark.parametrize(
    "adapter",
    [
        CodexAdapter(),
        ClaudeCodeAdapter(),
        OpenCodeAdapter(),
        DeepSeekHarnessAdapter(),
    ],
)
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
        model_provider="openai",
    )
    assert overridden == HarnessLaunchOptions(
        model="gpt-explicit",
        reasoning_effort="high",
        fast_mode=True,
        model_provider="openai",
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
        model_provider="openai",
    )


def test_fully_explicit_builtin_codex_route_does_not_read_user_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text("invalid = [\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(config_home))

    options = CodexAdapter().resolve_launch_options(
        model="gpt-explicit",
        reasoning_effort="medium",
        fast_mode=False,
        model_provider="openai",
    )

    assert options == HarnessLaunchOptions(
        model="gpt-explicit",
        reasoning_effort="medium",
        fast_mode=False,
        model_provider="openai",
    )


def test_codex_snapshots_selected_custom_provider_without_credential_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        (
            'model = "proxy-model"\n'
            'model_provider = "company_proxy"\n'
            "[model_providers.company_proxy]\n"
            'name = "Company Proxy"\n'
            'base_url = "https://proxy.example.test/v1"\n'
            'env_key = "COMPANY_PROXY_API_KEY"\n'
            'env_key_instructions = "set this outside Agent-Team"\n'
            'wire_api = "responses"\n'
            'env_http_headers = { "X-Tenant" = "COMPANY_TENANT" }\n'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(config_home))
    monkeypatch.setenv("COMPANY_PROXY_API_KEY", "must-never-enter-run-state")
    monkeypatch.setenv("COMPANY_TENANT", "tenant-secret-value")

    options = CodexAdapter().resolve_launch_options(
        model=None,
        reasoning_effort=None,
        fast_mode=None,
    )

    assert options == HarnessLaunchOptions(
        model="proxy-model",
        reasoning_effort=None,
        fast_mode=False,
        model_provider="company_proxy",
        model_provider_config={
            "name": "Company Proxy",
            "base_url": "https://proxy.example.test/v1",
            "env_key": "COMPANY_PROXY_API_KEY",
            "wire_api": "responses",
            "env_http_headers": {"X-Tenant": "COMPANY_TENANT"},
        },
    )
    rendered = json.dumps(options.model_provider_config, sort_keys=True)
    assert "must-never-enter-run-state" not in rendered
    assert "tenant-secret-value" not in rendered
    assert "env_key_instructions" not in rendered


@pytest.mark.parametrize(
    "unsafe_config",
    [
        'experimental_bearer_token = "literal-secret"\n',
        'http_headers = { Authorization = "literal-secret" }\n',
        'query_params = { api_key = "literal-secret" }\n',
        '[model_providers.company_proxy.auth]\ncommand = "/bin/token-helper"\n',
    ],
)
def test_codex_rejects_secret_bearing_or_executable_provider_fields(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unsafe_config: str,
) -> None:
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        (
            "[model_providers.company_proxy]\n"
            'base_url = "https://proxy.example.test/v1"\n'
            + unsafe_config
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(config_home))

    with pytest.raises(AgentTeamError) as rejected:
        CodexAdapter().resolve_launch_options(
            model="proxy-model",
            reasoning_effort="high",
            fast_mode=False,
            model_provider="company_proxy",
        )

    assert rejected.value.code == "HARNESS_PROVIDER_CONFIG_UNSUPPORTED"
    assert "literal-secret" not in rejected.value.message


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///tmp/provider",
        "https://user:secret@proxy.example.test/v1",
        "https://proxy.example.test/v1?api_key=secret",
        "https://proxy.example.test/v1#secret",
    ],
)
def test_codex_rejects_unsafe_custom_provider_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base_url: str,
) -> None:
    config_home = tmp_path / "codex-home"
    config_home.mkdir()
    (config_home / "config.toml").write_text(
        (
            "[model_providers.company_proxy]\n"
            f"base_url = {json.dumps(base_url)}\n"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(config_home))

    with pytest.raises(AgentTeamError) as rejected:
        CodexAdapter().resolve_launch_options(
            model="proxy-model",
            reasoning_effort="high",
            fast_mode=False,
            model_provider="company_proxy",
        )

    assert rejected.value.code == "HARNESS_USER_CONFIG_INVALID"
    assert "secret" not in rejected.value.message


def test_codex_custom_provider_prerequisites_use_only_referenced_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = HarnessLaunchOptions(
        model="proxy-model",
        reasoning_effort="high",
        fast_mode=False,
        model_provider="company_proxy",
        model_provider_config={
            "base_url": "https://proxy.example.test/v1",
            "env_key": "COMPANY_PROXY_API_KEY",
            "wire_api": "responses",
        },
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "authentication_status", lambda: False)
    monkeypatch.setenv("COMPANY_PROXY_API_KEY", "provider-secret")

    adapter.assert_launch_prerequisites(options)
    monkeypatch.delenv("COMPANY_PROXY_API_KEY")

    with pytest.raises(AgentTeamError) as rejected:
        adapter.assert_launch_prerequisites(options)

    assert rejected.value.code == "HARNESS_ENVIRONMENT_UNAVAILABLE"


def test_codex_custom_provider_can_require_codex_account_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    options = HarnessLaunchOptions(
        model="proxy-model",
        reasoning_effort="high",
        fast_mode=False,
        model_provider="company_proxy",
        model_provider_config={
            "base_url": "https://proxy.example.test/v1",
            "requires_openai_auth": True,
            "wire_api": "responses",
        },
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "authentication_status", lambda: False)

    with pytest.raises(AgentTeamError) as rejected:
        adapter.assert_launch_prerequisites(options)

    assert rejected.value.code == "HARNESS_NOT_AUTHENTICATED"


def test_model_provider_option_is_codex_only() -> None:
    with pytest.raises(InvalidArgument, match="only supported by the codex"):
        ClaudeCodeAdapter().resolve_launch_options(
            model="opus",
            reasoning_effort="high",
            fast_mode=None,
            model_provider="company_proxy",
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
    assert all(mapping["start"] == mapping["resume"] for mapping in mappings.values())
    for mapping in mappings.values():
        argv = mapping["start"]
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in argv
        assert "Bash(/opt/agent-team/bin/agent-team cancel *)" in argv
        assert "Bash(/opt/agent-team/bin/agent-team origin-*)" in argv

    def permission_mode(profile: str) -> str | None:
        argv = mappings[profile]["start"]
        if "--permission-mode" not in argv:
            return None
        return argv[argv.index("--permission-mode") + 1]

    def settings(profile: str) -> dict:
        argv = mappings[profile]["start"]
        return json.loads(argv[argv.index("--settings") + 1])

    assert permission_mode("default") == "acceptEdits"
    assert permission_mode("trusted-workspace") == "acceptEdits"
    assert permission_mode("full-access") is None
    assert "--dangerously-skip-permissions" in mappings["full-access"]["start"]
    assert "--dangerously-skip-permissions" not in mappings["default"]["start"]
    assert (
        "--dangerously-skip-permissions"
        not in mappings["trusted-workspace"]["start"]
    )
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
        ("opencode", "trusted-workspace"),
        ("opencode", "full-access"),
        ("deepseek-harness", "trusted-workspace"),
        ("deepseek-harness", "full-access"),
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
                "model_provider": "company_proxy",
            }
            return HarnessLaunchOptions(
                model=values["model"],
                reasoning_effort=values["reasoning_effort"],
                fast_mode=values["fast_mode"],
                model_provider=values["model_provider"],
                model_provider_config={
                    "base_url": "https://proxy.example.test/v1",
                    "wire_api": "responses",
                },
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
        model_provider="company_proxy",
    )

    assert role_id == "reviewer"
    assert role.launch_profile == "default"
    assert role.model == "gpt-explicit"
    assert role.reasoning_effort == "high"
    assert role.fast_mode is True
    assert role.model_provider == "company_proxy"
    assert role.model_provider_config == {
        "base_url": "https://proxy.example.test/v1",
        "wire_api": "responses",
    }


def test_role_spec_freezes_workspace_local_dsh_plugin_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    plugin = workspace / "packages" / "candidate"
    plugin.mkdir(parents=True)

    class StubAdapter:
        @staticmethod
        def assert_launch_mode(selected_mode: str) -> None:
            assert selected_mode == "interactive"

        @staticmethod
        def resolve_launch_options(**_values) -> HarnessLaunchOptions:
            return HarnessLaunchOptions(
                model="deepseek-official/test",
                reasoning_effort="high",
            )

        @staticmethod
        def profile_fingerprint(
            _profile: str,
            _policy: str,
            _launch_mode: str,
        ) -> str:
            return "a" * 64

    monkeypatch.setattr(
        "agent_team.bootstrap.get_adapter",
        lambda _adapter_id: StubAdapter(),
    )

    _, role = parse_role_spec(
        "validator=deepseek-harness:fresh:full-access",
        dsh_plugin=str(plugin),
        workspace=workspace,
    )

    assert role.dsh_plugin == "packages/candidate"

    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(InvalidArgument, match="inside"):
        parse_role_spec(
            "validator=deepseek-harness:fresh:full-access",
            dsh_plugin=str(outside),
            workspace=workspace,
        )

    with pytest.raises(InvalidArgument, match="deepseek-harness"):
        parse_role_spec(
            "reviewer=codex:fresh:full-access",
            dsh_plugin=str(plugin),
            workspace=workspace,
        )


@pytest.mark.parametrize(
    "adapter_id",
    ["codex", "claude-code", "opencode", "deepseek-harness"],
)
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

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from agent_team.adapters.base import (
    LaunchSpec,
)
from agent_team.adapters.claude_code import ClaudeCodeAdapter
from agent_team.adapters.codex import CodexAdapter
from agent_team.config import Role, make_team
from agent_team.errors import AgentTeamError, IntegrityError

from ._support import launch_context


def _write_codex_team(
    run_dir: Path,
    workspace: Path,
    *,
    model: str | None = "gpt-5.6-sol",
    model_provider: str | None = None,
    model_provider_config: dict[str, object] | None = None,
) -> None:
    team = make_team(
        run_id=run_dir.name,
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "full-access",
                "0" * 64,
                model,
                "max",
                True,
                "interactive",
                None,
                model_provider,
                model_provider_config,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=60,
    )
    (run_dir / "team.json").write_bytes(team.canonical_bytes())


def _write_claude_team(
    run_dir: Path,
    workspace: Path,
    *,
    model_provider: str | None = None,
    model_provider_config: dict[str, object] | None = None,
) -> None:
    team = make_team(
        run_id=run_dir.name,
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "claude-code",
                "resume",
                "full-access",
                "0" * 64,
                "opus",
                "high",
                None,
                "interactive",
                None,
                model_provider,
                model_provider_config,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=60,
    )
    (run_dir / "team.json").write_bytes(team.canonical_bytes())


def test_codex_interactive_custom_provider_does_not_copy_openai_auth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-custom-provider"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    source_home = tmp_path / "user-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text(
        '{"token":"must-not-be-copied"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setenv("COMPANY_PROXY_API_KEY", "provider-secret")
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: tmp_path / "state",
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/codex"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "0.146.0")
    monkeypatch.setattr(adapter, "authentication_status", lambda: False)
    provider_config = {
        "base_url": "https://proxy.example.test/v1",
        "env_key": "COMPANY_PROXY_API_KEY",
        "wire_api": "responses",
    }
    _write_codex_team(
        run_dir,
        workspace,
        model="proxy-model",
        model_provider="company_proxy",
        model_provider_config=provider_config,
    )

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )
    isolated_home = adapter._interactive_home(run_dir, "developer")

    assert not (isolated_home / "auth.json").exists()
    launch = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=None,
            model="proxy-model",
            reasoning_effort="max",
            fast_mode=True,
            model_provider="company_proxy",
            model_provider_config=provider_config,
            launch_mode="interactive",
            workspace=str(workspace),
            turn_dir=str(turn_dir),
        )
    )
    assert launch.env["CODEX_HOME"] == str(isolated_home)
    assert 'model_provider="company_proxy"' in launch.argv
    assert "provider-secret" not in json.dumps(launch.to_json())


def test_codex_interactive_launch_uses_isolated_native_tui_and_mcp_state(
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
        "[mcp_servers.copied]\ncommand = 'test-mcp'\n",
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
    _write_codex_team(run_dir, workspace)

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
        "mcp_servers": {"copied": {"command": "test-mcp"}},
        "projects": {str(workspace): {"trust_level": "trusted"}},
        "tui": {"model_availability_nux": {"gpt-5.6-sol": 4}},
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
    "partial_config",
    [
        b"",
        b"[mcp_servers.must_not_replace]\ncommand = 'unsafe'\n",
    ],
)
def test_codex_capability_snapshot_rejects_a_preexisting_partial_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    partial_config: bytes,
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
    _write_codex_team(run_dir, workspace)
    home = adapter._interactive_home(run_dir, "developer")
    home.mkdir(parents=True)
    marker_path = home / "agent-team-home.json"
    marker_path.write_text(
        json.dumps(adapter._interactive_marker(run_dir, "developer")),
        encoding="utf-8",
    )
    marker_path.chmod(0o600)
    config_path = home / "config.toml"
    config_path.write_bytes(partial_config)
    config_path.chmod(0o600)

    with pytest.raises(IntegrityError, match="immutable file already exists"):
        adapter.prepare_run_state(
            run_dir=run_dir,
            role_id="developer",
            launch_mode="interactive",
        )
    assert config_path.read_bytes() == partial_config


@pytest.mark.parametrize(
    "unexpected",
    [
        (
            '[projects."{workspace}"]\ntrust_level = "trusted"\n\n'
            '[tui.model_availability_nux]\n"gpt-5.6-sol" = 3\n'
        ),
        (
            '[projects."{workspace}"]\ntrust_level = "trusted"\n\n'
            '[tui.model_availability_nux]\n"other-model" = 4\n'
        ),
        "[mcp_servers.unexpected]\ncommand = 'unsafe'\n",
    ],
)
def test_codex_interactive_config_rejects_any_post_prepare_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unexpected: str,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-codex-config-drift"
    run_dir.mkdir(parents=True)
    source_home = tmp_path / "user-codex-home"
    source_home.mkdir()
    source_auth = source_home / "auth.json"
    source_auth.write_text('{"token":"test-only"}\n', encoding="utf-8")
    source_auth.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: tmp_path / "state",
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(
        adapter,
        "_assert_interactive_authentication",
        lambda _home: None,
    )
    _write_codex_team(run_dir, workspace)

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )
    config_path = adapter._interactive_home(run_dir, "developer") / "config.toml"
    unexpected = unexpected.format(workspace=workspace)
    config_path.write_text(unexpected, encoding="utf-8")

    with pytest.raises(IntegrityError, match="private config changed"):
        adapter.prepare_run_state(
            run_dir=run_dir,
            role_id="developer",
            launch_mode="interactive",
        )

    assert config_path.read_text(encoding="utf-8") == unexpected


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

    assert CodexAdapter().interactive_session_refs(launch) == {"matching-session"}


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
    monkeypatch.setattr(
        adapter,
        "_assert_runtime_home",
        lambda **_kwargs: Path("/private/claude"),
    )
    monkeypatch.setattr(
        adapter,
        "_capability_launch_args",
        lambda **_kwargs: (),
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
    assert (
        launch.expected_session_ref
        == launch.argv[launch.argv.index("--session-id") + 1]
    )
    assert launch.prompt_file is not None
    assert launch.env["CLAUDE_CODE_EFFORT_LEVEL"] == "high"
    assert launch.env["CLAUDE_CONFIG_DIR"] == "/private/claude"
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
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.fixed_state_dir",
        lambda: tmp_path / "state",
    )
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
    _write_claude_team(run_dir, workspace)
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )
    home = adapter._runtime_home(run_dir, "developer")
    state = json.loads((home / ".config.json").read_text(encoding="utf-8"))
    assert state["bypassPermissionsModeAccepted"] is True
    assert state["projects"] == {
        str(workspace): {"hasTrustDialogAccepted": True}
    }
    assert json.loads(
        (home / "agent-team-home.json").read_text(encoding="utf-8")
    ) == adapter._runtime_marker(run_dir, "developer")

    debug_dir = home / "debug"
    debug_dir.mkdir()
    debug_dir.chmod(0o755)
    debug_log = debug_dir / "session.log"
    debug_log.write_text("session\n", encoding="utf-8")
    debug_log.chmod(0o644)
    latest = debug_dir / "latest"
    latest.symlink_to(debug_log.name)

    adapter.finalize_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )

    assert latest.is_symlink()
    assert latest.read_text(encoding="utf-8") == "session\n"
    assert debug_dir.stat().st_mode & 0o077 == 0
    assert debug_log.stat().st_mode & 0o077 == 0


def test_claude_external_provider_does_not_copy_claude_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-claude-gateway"
    run_dir.mkdir(parents=True)
    config_dir = tmp_path / "claude-config"
    config_dir.mkdir()
    (config_dir / ".config.json").write_text(
        json.dumps(
            {
                "projects": {
                    str(workspace): {"hasTrustDialogAccepted": True},
                }
            }
        ),
        encoding="utf-8",
    )
    credentials = config_dir / ".credentials.json"
    credentials.write_text('{"token":"must-not-be-copied"}\n', encoding="utf-8")
    credentials.chmod(0o600)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "gateway-secret")
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.fixed_state_dir",
        lambda: tmp_path / "state",
    )
    provider_config = {
        "settings": {
            "base_url": "https://gateway.example.test/anthropic",
        },
        "credential_environment_names": ["ANTHROPIC_AUTH_TOKEN"],
    }
    _write_claude_team(
        run_dir,
        workspace,
        model_provider="gateway",
        model_provider_config=provider_config,
    )
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "authentication_status", lambda: False)

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )

    home = adapter._runtime_home(run_dir, "developer")
    assert not (home / ".credentials.json").exists()
    assert adapter.worker_environment_names(
        run_dir=run_dir,
        role_id="developer",
    ) == ("ANTHROPIC_AUTH_TOKEN",)


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


def test_claude_restricted_private_state_drops_full_access_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        ClaudeCodeAdapter,
        "_read_user_state",
        classmethod(
            lambda _cls: {
                "bypassPermissionsModeAccepted": True,
                "customApiKeyResponses": {"approved": ["fingerprint"]},
                "projects": {"/other": {"hasTrustDialogAccepted": True}},
            }
        ),
    )

    state = ClaudeCodeAdapter._private_runtime_state(
        workspace=workspace,
        profile="trusted-workspace",
    )

    assert "bypassPermissionsModeAccepted" not in state
    assert state["projects"] == {
        str(workspace): {"hasTrustDialogAccepted": True}
    }
    assert state["customApiKeyResponses"] == {"approved": ["fingerprint"]}


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

from __future__ import annotations

import json
import stat
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_team.adapters.claude_code import ClaudeCodeAdapter
from agent_team.adapters.codex import CodexAdapter
from agent_team.adapters.deepseek_harness import DeepSeekHarnessAdapter
from agent_team.adapters.opencode import OpenCodeAdapter
from agent_team.config import Role, make_team

from ._support import launch_context


def _write_team(
    *,
    run_dir: Path,
    workspace: Path,
    adapter_id: str,
    launch_mode: str,
    profile_hash: str,
    model: str | None,
) -> None:
    team = make_team(
        run_id=run_dir.name,
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                role_id="developer",
                binding="external",
                adapter=adapter_id,
                session_policy="resume",
                launch_profile="default",
                launch_profile_sha256=profile_hash,
                model=model,
                reasoning_effort=None,
                fast_mode=None,
                launch_mode=launch_mode,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=60,
    )
    (run_dir / "team.json").write_bytes(team.canonical_bytes())


def test_codex_copies_enabled_plugins_and_mcp_into_both_launch_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    source_home = tmp_path / "codex-source"
    plugin = source_home / "plugins" / "cache" / "market" / "chrome" / "1.0.0"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text(
        '{"name":"chrome","version":"1.0.0"}\n',
        encoding="utf-8",
    )
    executable = plugin / "server"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)
    (source_home / "config.toml").write_text(
        '[plugins."chrome@market"]\n'
        "enabled = true\n\n"
        "[mcp_servers.chrome]\n"
        f'command = "{source_home}/plugins/cache/market/chrome/latest/server"\n'
        'env_vars = ["CHROME_TOKEN"]\n',
        encoding="utf-8",
    )
    auth = source_home / "auth.json"
    auth.write_text('{"token":"private"}\n', encoding="utf-8")
    auth.chmod(0o600)
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setenv("CHROME_TOKEN", "ephemeral")
    monkeypatch.setattr(
        "agent_team.adapters.codex.fixed_state_dir",
        lambda: tmp_path / "state",
    )
    adapter = CodexAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/codex"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "0.149.1")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        adapter,
        "_assert_interactive_authentication",
        lambda _home: None,
    )
    profile_hash = adapter.profile_fingerprint("default", "resume", "headless")
    _write_team(
        run_dir=run_dir,
        workspace=workspace,
        adapter_id="codex",
        launch_mode="headless",
        profile_hash=profile_hash,
        model=None,
    )

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="headless",
    )
    home = adapter._interactive_home(run_dir, "developer")
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))

    assert config["plugins"] == {"chrome@market": {"enabled": True}}
    assert config["mcp_servers"]["chrome"]["command"].startswith(str(home))
    copied = home / "plugins" / "cache" / "market" / "chrome" / "1.0.0"
    assert (copied / ".codex-plugin" / "plugin.json").is_file()
    assert (copied / "server").stat().st_mode & stat.S_IXUSR
    assert adapter.worker_environment_names(
        run_dir=run_dir,
        role_id="developer",
    ) == ("CHROME_TOKEN",)
    launch = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=None,
            workspace=str(workspace),
            turn_dir=str(turn_dir),
        )
    )
    assert launch.env["CODEX_HOME"] == str(home)
    assert "--ignore-user-config" not in launch.argv


def test_claude_copies_enabled_plugin_and_merged_mcp_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    source_home = tmp_path / "claude-source"
    plugin = source_home / "plugins" / "cache" / "market" / "demo" / "1.0.0"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        '{"name":"demo"}\n',
        encoding="utf-8",
    )
    source_home.mkdir(exist_ok=True)
    (source_home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"demo@market": True}}),
        encoding="utf-8",
    )
    (source_home / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    "demo@market": [
                        {"scope": "user", "installPath": str(plugin)}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (source_home / ".config.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "user": {
                        "command": "/bin/echo",
                        "env": {"TOKEN": "${CLAUDE_MCP_TOKEN}"},
                    }
                },
                "projects": {},
            }
        ),
        encoding="utf-8",
    )
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "project": {"command": "/bin/echo", "args": ["project"]}
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(source_home))
    monkeypatch.setenv("CLAUDE_MCP_TOKEN", "ephemeral")
    monkeypatch.setattr(
        "agent_team.adapters.claude_code.fixed_state_dir",
        lambda: tmp_path / "state",
    )
    adapter = ClaudeCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/claude"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "2.1.25")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    profile_hash = adapter.profile_fingerprint("default", "resume", "headless")
    _write_team(
        run_dir=run_dir,
        workspace=workspace,
        adapter_id="claude-code",
        launch_mode="headless",
        profile_hash=profile_hash,
        model=None,
    )

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="headless",
    )
    home = adapter._runtime_home(run_dir, "developer")
    mcp = json.loads((home / "agent-team-mcp.json").read_text(encoding="utf-8"))
    launch = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=None,
            workspace=str(workspace),
            turn_dir=str(turn_dir),
        )
    )

    assert set(mcp["mcpServers"]) == {"user", "project"}
    assert launch.env["CLAUDE_CONFIG_DIR"] == str(home)
    assert launch.argv[launch.argv.index("--mcp-config") + 1] == str(
        home / "agent-team-mcp.json"
    )
    assert any("agent-team-plugins" in item for item in launch.argv)
    assert adapter.worker_environment_names(
        run_dir=run_dir,
        role_id="developer",
    ) == ("CLAUDE_MCP_TOKEN",)


def test_opencode_private_config_loads_plugins_and_mcp_without_persisting_it_in_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    local_plugin = tmp_path / "plugin.js"
    local_plugin.write_text("export const Demo = async () => ({})\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_team.adapters.opencode.fixed_state_dir",
        lambda: tmp_path / "state",
    )
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "1.18.18")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setenv("OPENCODE_MCP_TOKEN", "ephemeral-secret-value")
    resolved = {
        "model": "deepseek/deepseek-v4-pro",
        "provider": {},
        "plugin": [local_plugin.as_uri(), "remote-plugin@1.2.3"],
        "mcp": {
            "demo": {
                "type": "local",
                "command": ["/bin/echo", "Bearer ephemeral-secret-value"],
            }
        },
    }
    monkeypatch.setattr(adapter, "_resolved_user_config", lambda _workspace: resolved)
    profile_hash = adapter.profile_fingerprint("default", "resume", "headless")
    _write_team(
        run_dir=run_dir,
        workspace=workspace,
        adapter_id="opencode",
        launch_mode="headless",
        profile_hash=profile_hash,
        model="deepseek/deepseek-v4-pro",
    )

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="headless",
    )
    home = adapter._config_home(run_dir, "developer")
    config = json.loads(
        (home / "opencode" / "opencode.json").read_text(encoding="utf-8")
    )
    launch = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=None,
            model="deepseek/deepseek-v4-pro",
            workspace=str(workspace),
            turn_dir=str(turn_dir),
        )
    )

    assert config["plugin"][0].startswith((home / "agent-team-plugins").as_uri())
    assert config["plugin"][1] == "remote-plugin@1.2.3"
    assert config["mcp"]["demo"]["command"] == [
        "/bin/echo",
        "Bearer {env:OPENCODE_MCP_TOKEN}",
    ]
    assert "ephemeral-secret-value" not in json.dumps(config)
    assert "--pure" not in launch.argv
    assert "ephemeral-secret-value" not in json.dumps(launch.env)
    assert "OPENCODE_MCP_TOKEN" not in launch.env["OPENCODE_CONFIG_CONTENT"]
    assert adapter.worker_environment_names(
        run_dir=run_dir,
        role_id="developer",
    ) == ("OPENCODE_MCP_TOKEN",)


def test_deepseek_harness_copies_profile_bundle_and_mcp_patch_to_generations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    run_dir.mkdir(parents=True)
    source_home = tmp_path / "dsh-source"
    source_profile = source_home / "profiles" / "headless"
    plugin = source_profile / "node_modules" / "demo-plugin"
    plugin.mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "version": "1.0.0",
                "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
            }
        ),
        encoding="utf-8",
    )
    (plugin / "cordis.patch.yml").write_text("[]\n", encoding="utf-8")
    (source_profile / "package.json").write_text(
        json.dumps(
            {
                "dependencies": {"demo-plugin": "1.0.0"},
                "dsh": {
                    "profile": {
                        "bundles": [
                            "@deepseek-ai/dsh-base",
                            "@deepseek-ai/dsh-headless",
                            "demo-plugin",
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (source_profile / "cordis.patch.yml").write_text(
        "- id: mcp-demo\n"
        "  name: '@deepseek-ai/dsh-mcp-client'\n"
        "  config:\n"
        "    serverName: demo\n"
        "    url: !!js 'process.env.DSH_MCP_URL'\n",
        encoding="utf-8",
    )
    tui = tmp_path / "dsh-tui"
    tui.mkdir()
    (tui / "package.json").write_text(
        json.dumps({"name": "@agent-team/dsh-tui", "version": "0.1.0"}),
        encoding="utf-8",
    )
    runtime = tmp_path / "runtime"
    (runtime / "node_modules").mkdir(parents=True)
    monkeypatch.setenv("DSH_HOME", str(source_home))
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.fixed_state_dir",
        lambda: tmp_path / "state",
    )
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.managed_dsh_runtime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.managed_dsh_runtime_report",
        dict,
    )
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.dsh_tui_source",
        lambda: tui,
    )
    adapter = DeepSeekHarnessAdapter()
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.load_team",
        lambda _run_dir: SimpleNamespace(
            workspace=workspace,
            roles={"developer": SimpleNamespace(dsh_plugin=None)},
        ),
    )
    _write_team(
        run_dir=run_dir,
        workspace=workspace,
        adapter_id="deepseek-harness",
        launch_mode="interactive",
        profile_hash="0" * 64,
        model="deepseek/deepseek-chat",
    )

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
        session_generation=1,
    )
    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
        session_generation=2,
    )

    for generation in (1, 2):
        home = adapter._home(run_dir, "developer", generation)
        profile = home / "profiles" / "agent-team"
        manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
        assert "demo-plugin" in manifest["dsh"]["profile"]["bundles"]
        assert (profile / "node_modules" / "demo-plugin" / "package.json").is_file()
        assert "mcp-demo" in (profile / "cordis.patch.yml").read_text(
            encoding="utf-8"
        )
    assert adapter.worker_environment_names(
        run_dir=run_dir,
        role_id="developer",
    ) == ("DSH_MCP_URL",)

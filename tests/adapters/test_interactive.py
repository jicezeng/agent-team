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
from agent_team.errors import AgentTeamError, IntegrityError

from ._support import launch_context


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
    ) == {"projects": {str(workspace): {"trust_level": "trusted"}}}
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

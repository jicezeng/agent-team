from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from agent_team.adapters.base import HarnessLaunchOptions
from agent_team.adapters.deepseek_harness import DeepSeekHarnessAdapter
from agent_team.assets import dsh_tui_source
from agent_team.errors import AgentTeamError, InvalidArgument

from ._support import launch_context


def _stub_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    adapter: DeepSeekHarnessAdapter,
) -> tuple[Path, Path]:
    state = tmp_path / "state"
    runtime = state / "installed" / "deepseek-harness-runtime"
    executable = runtime / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.fixed_state_dir",
        lambda: state,
    )
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.managed_dsh_runtime",
        lambda: runtime,
    )
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.managed_dsh_runtime_report",
        lambda: {
            "root": str(runtime),
            "executable": str(executable),
            "package": "@deepseek-ai/dsh",
            "version": "0.1.0-rc.6",
            "integrity": "test",
            "version_output": "0.1.0-rc.6",
        },
    )
    monkeypatch.setattr(adapter, "executable", lambda: executable)
    monkeypatch.setattr(adapter, "executable_version", lambda: "0.1.0-rc.6")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    return state, runtime


def test_dsh_adapter_is_interactive_only_and_has_frozen_defaults() -> None:
    adapter = DeepSeekHarnessAdapter()

    options = adapter.resolve_launch_options(
        model=None,
        reasoning_effort=None,
        fast_mode=None,
    )

    assert options == HarnessLaunchOptions(
        model="deepseek-official/deepseek-v4-flash",
        reasoning_effort="high",
    )
    mappings = adapter.profile_mappings("interactive")
    assert set(mappings) == {"default", "trusted-workspace", "full-access"}
    assert all(mapping["start"] == mapping["resume"] for mapping in mappings.values())
    assert "sandbox=workspace-write" in mappings["default"]["start"]
    assert "sandbox=danger-full-access" in mappings["full-access"]["start"]
    with pytest.raises(AgentTeamError, match="interactive TUI"):
        adapter.profile_mappings("headless")
    with pytest.raises(InvalidArgument, match="provider/model"):
        adapter.assert_launch_options(
            HarnessLaunchOptions(model="unqualified", reasoning_effort="high")
        )
    with pytest.raises(InvalidArgument, match="off, high, max"):
        adapter.assert_launch_options(
            HarnessLaunchOptions(
                model="deepseek-official/deepseek-v4-flash",
                reasoning_effort="medium",
            )
        )


def test_dsh_interactive_launch_prepares_private_tui_and_resumes_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    adapter = DeepSeekHarnessAdapter()
    state, _runtime = _stub_runtime(monkeypatch, tmp_path, adapter)

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )
    context = launch_context(
        adapter=adapter,
        session_policy="resume",
        session_ref=None,
        model="deepseek-official/deepseek-v4-flash",
        reasoning_effort="max",
        launch_mode="interactive",
        workspace=str(workspace),
        turn_dir=str(turn_dir),
    )
    launch = adapter.prepare_launch(context)
    home = Path(launch.env["DSH_HOME"])

    assert home.is_relative_to(state / "harness-homes" / "deepseek-harness")
    assert launch.launch_mode == "interactive"
    assert launch.argv[:3] == (
        str(adapter.executable()),
        "--profile",
        "agent-team",
    )
    assert "--session-id" in launch.argv
    assert launch.expected_session_ref is not None
    assert launch.expected_session_ref.startswith("agent-team-")
    assert launch.env["DSH_PERMISSION_MODE"] == "workspace-write"
    assert launch.env["DSH_TELEMETRY_DISABLED"] == "1"
    assert launch.env["DSH_TOOLS_MODE"] == "native"
    assert launch.prompt_file == str(turn_dir / "process" / "prompt.md")
    profile = home / "profiles" / "agent-team"
    manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    assert manifest["dsh"]["profile"]["bundles"] == [
        "@deepseek-ai/dsh-base",
        "@agent-team/dsh-tui",
    ]
    installed_plugin = profile / "node_modules" / "@agent-team" / "dsh-tui"
    assert (installed_plugin / "lib" / "index.js").read_bytes() == (
        dsh_tui_source() / "lib" / "index.js"
    ).read_bytes()

    session_ref = launch.expected_session_ref
    session_log = home / "sessions" / "--workspace--" / session_ref / "session.jsonl"
    session_log.parent.mkdir(parents=True)
    session_log.write_text(
        json.dumps(
            {
                "type": "session",
                "version": 0,
                "id": session_ref,
                "createdAt": 1,
                "cwd": str(workspace),
                "delegationDepth": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    resumed = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=session_ref,
            model="deepseek-official/deepseek-v4-flash",
            reasoning_effort="max",
            launch_mode="interactive",
            workspace=str(workspace),
            turn_dir=str(turn_dir),
        )
    )

    assert "--resume" in resumed.argv
    assert "--session-id" not in resumed.argv
    assert resumed.expected_session_ref == session_ref
    assert resumed.starts_new_session is False
    assert adapter.interactive_session_refs(resumed) == {session_ref}

    generated = home / "settings.yaml"
    generated.write_text("generated: true\n", encoding="utf-8")
    generated.chmod(0o644)
    adapter.finalize_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )
    assert stat.S_IMODE(generated.stat().st_mode) == 0o600
    assert stat.S_IMODE(session_log.stat().st_mode) == 0o600


def test_dsh_resume_rejects_missing_private_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    adapter = DeepSeekHarnessAdapter()
    _stub_runtime(monkeypatch, tmp_path, adapter)
    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )

    with pytest.raises(AgentTeamError) as rejected:
        adapter.prepare_launch(
            launch_context(
                adapter=adapter,
                session_policy="resume",
                session_ref="agent-team-missing",
                model="deepseek-official/deepseek-v4-flash",
                reasoning_effort="high",
                launch_mode="interactive",
                workspace=str(workspace),
                turn_dir=str(turn_dir),
            )
        )

    assert rejected.value.code == "HARNESS_SESSION_UNAVAILABLE"


def test_bundled_dsh_tui_never_renders_private_reasoning() -> None:
    source = (dsh_tui_source() / "lib" / "index.js").read_text(encoding="utf-8")
    patch = (dsh_tui_source() / "cordis.patch.yml").read_text(encoding="utf-8")

    assert "agents.resume" in source
    assert "agents.create" in source
    assert "reasoning-delta" in source
    assert "chunk.text" not in source.split("reasoning-delta", 1)[1].split(
        "text-delta", 1
    )[0]
    assert "[thinking]" in source
    assert "session-persistence-jsonl" in patch
    assert "compression: none" in patch
    assert "tool-subagent" in patch
    assert "disabled: true" in patch

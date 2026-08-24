from __future__ import annotations

import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_team.adapters.base import HarnessLaunchOptions
from agent_team.adapters.deepseek_harness import DeepSeekHarnessAdapter
from agent_team.assets import dsh_tui_source
from agent_team.errors import AgentTeamError, IntegrityError, InvalidArgument

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
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.load_team",
        lambda _run_dir: SimpleNamespace(
            workspace=_run_dir.parent.parent.parent,
            roles={"developer": SimpleNamespace(dsh_plugin=None)},
        ),
    )
    return state, runtime


def test_dsh_adapter_is_interactive_only_and_defers_omitted_defaults() -> None:
    adapter = DeepSeekHarnessAdapter()

    options = adapter.resolve_launch_options(
        model=None,
        reasoning_effort=None,
        fast_mode=None,
    )

    assert options == HarnessLaunchOptions()
    adapter.assert_launch_options(options)
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


def test_dsh_runtime_is_provisioned_only_when_launch_dependencies_are_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = DeepSeekHarnessAdapter()
    installs: list[str] = []
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.install_managed_dsh_runtime",
        lambda: installs.append("managed-runtime"),
    )
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)

    adapter.ensure_launch_dependencies(
        HarnessLaunchOptions(
            model="deepseek-official/deepseek-v4-flash",
            reasoning_effort="high",
        )
    )

    assert installs == ["managed-runtime"]


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


def test_dsh_launch_omits_model_flags_for_native_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-native-default-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    adapter = DeepSeekHarnessAdapter()
    _stub_runtime(monkeypatch, tmp_path, adapter)
    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )

    launch = adapter.prepare_launch(
        launch_context(
            adapter=adapter,
            session_policy="fresh",
            session_ref=None,
            model=None,
            reasoning_effort=None,
            launch_mode="interactive",
            workspace=str(workspace),
            turn_dir=str(turn_dir),
        )
    )

    assert "--provider" not in launch.argv
    assert "--model" not in launch.argv
    assert "--reasoning-effort" not in launch.argv


def test_dsh_role_installs_and_freezes_workspace_bundle_on_activation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    source = workspace / "packages" / "candidate"
    (source / "lib").mkdir(parents=True)
    (source / "package.json").write_text(
        json.dumps(
            {
                "name": "@example/candidate",
                "version": "1.2.3",
                "type": "module",
                "main": "lib/index.js",
                "dsh": {"bundle": {"patch": "./cordis.patch.yml"}},
            }
        ),
        encoding="utf-8",
    )
    (source / "cordis.patch.yml").write_text("[]\n", encoding="utf-8")
    (source / "lib" / "index.js").write_text(
        "export const name = 'candidate'\n",
        encoding="utf-8",
    )
    (source / "node_modules").mkdir()
    (source / "node_modules" / "workspace-dependency").symlink_to(source / "lib")
    run_dir = workspace / ".agent-team" / "runs" / "at-plugin-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    adapter = DeepSeekHarnessAdapter()
    state, _runtime = _stub_runtime(monkeypatch, tmp_path, adapter)
    monkeypatch.setattr(
        "agent_team.adapters.deepseek_harness.load_team",
        lambda _run_dir: SimpleNamespace(
            workspace=workspace,
            roles={
                "developer": SimpleNamespace(dsh_plugin="packages/candidate")
            },
        ),
    )

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )
    context = launch_context(
        adapter=adapter,
        session_policy="fresh",
        session_ref=None,
        model="deepseek-official/deepseek-v4-flash",
        reasoning_effort="high",
        launch_mode="interactive",
        workspace=str(workspace),
        turn_dir=str(turn_dir),
    )
    launch = adapter.prepare_launch(context)
    home = Path(launch.env["DSH_HOME"])
    profile = home / "profiles" / "agent-team"
    manifest = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    installed = profile / "node_modules" / "@example" / "candidate"

    assert manifest["dsh"]["profile"]["bundles"] == [
        "@deepseek-ai/dsh-base",
        "@example/candidate",
        "@agent-team/dsh-tui",
    ]
    assert manifest["dependencies"] == {"@example/candidate": "1.2.3"}
    assert (installed / "lib" / "index.js").read_text(encoding="utf-8") == (
        "export const name = 'candidate'\n"
    )
    assert not (installed / "node_modules").exists()
    assert len(launch.env["AGENT_TEAM_DSH_PLUGIN_SHA256"]) == 64
    assert home.is_relative_to(state / "harness-homes" / "deepseek-harness")

    (source / "lib" / "index.js").write_text(
        "export const name = 'changed-after-activation'\n",
        encoding="utf-8",
    )
    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="interactive",
    )
    assert (installed / "lib" / "index.js").read_text(encoding="utf-8") == (
        "export const name = 'candidate'\n"
    )

    snapshot_path = home / "agent-team-dsh-plugin.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["content_sha256"] = "0" * 64
    snapshot_path.chmod(0o600)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    with pytest.raises(IntegrityError, match="snapshot"):
        adapter.prepare_launch(context)


def test_dsh_role_rejects_candidate_that_shadows_managed_bundles(
    tmp_path: Path,
) -> None:
    source = tmp_path / "candidate"
    source.mkdir()
    (source / "package.json").write_text(
        json.dumps(
            {
                "name": "@agent-team/dsh-tui",
                "version": "1.0.0",
                "dsh": {"bundle": {"patch": "cordis.patch.yml"}},
            }
        ),
        encoding="utf-8",
    )
    (source / "cordis.patch.yml").write_text("[]\n", encoding="utf-8")

    with pytest.raises(AgentTeamError) as rejected:
        DeepSeekHarnessAdapter._candidate_contract(
            source,
            source_relative="candidate",
        )

    assert rejected.value.code == "DSH_PLUGIN_INVALID"


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
    assert "const initialTurnReason = new Promise" in source
    assert "resolveInitialTurn?.(reason)" in source
    assert "assertInitialTurnCompleted(await renderer.initialTurnReason)" in source
    assert "initial agent turn did not complete" in source
    assert "'agentDefaultModel'" in source
    assert "defaultModel.currentSelection()" in source
    assert "--provider and --model must be supplied together" in source
    assert "session-persistence-jsonl" in patch
    assert "compression: none" in patch
    assert "tool-subagent" in patch
    assert "disabled: true" in patch

from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest

from agent_team.adapters.base import (
    AdapterEvidenceSnapshot,
    HarnessLaunchOptions,
    LaunchSpec,
    StreamRecord,
)
from agent_team.adapters.opencode import OpenCodeAdapter
from agent_team.config import Role, make_team
from agent_team.errors import AgentTeamError, InvalidArgument

from ._support import launch_context, record


def _prepare_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[OpenCodeAdapter, Path, Path]:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        "agent_team.adapters.opencode.fixed_state_dir",
        lambda: state_dir,
    )
    monkeypatch.setattr(
        "agent_team.adapters.opencode.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "1.18.18")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        "agent_team.adapters.opencode.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"model": "deepseek/deepseek-v4-pro", "provider": {}}),
            stderr="",
        ),
    )
    team = make_team(
        run_id="at-adapter-test",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "opencode",
                "resume",
                "default",
                adapter.profile_fingerprint("default", "resume"),
                "deepseek/deepseek-v4-pro",
                None,
                None,
                "headless",
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=60,
    )
    (run_dir / "team.json").write_bytes(team.canonical_bytes())
    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="headless",
    )
    return adapter, workspace, turn_dir


def test_opencode_profiles_are_equivalent_and_do_not_claim_a_shell_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "agent_team.adapters.opencode.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    adapter = OpenCodeAdapter()

    mappings = adapter.profile_mappings()
    default = adapter._runtime_config("default")["permission"]
    trusted = adapter._runtime_config("trusted-workspace")["permission"]
    full = adapter._runtime_config("full-access")["permission"]

    assert set(mappings) == {"default", "trusted-workspace", "full-access"}
    assert all(item["start"] == item["resume"] for item in mappings.values())
    assert all("--pure" not in item["start"] for item in mappings.values())
    assert all("--auto" in item["start"] for item in mappings.values())
    assert default["bash"]["*"] == "deny"
    assert trusted["bash"]["*"] == "deny"
    assert "webfetch" not in default
    assert trusted["webfetch"] == "allow"
    assert default["external_directory"] == "deny"
    assert default["bash"]["/opt/agent-team/bin/agent-team handoff *"] == "allow"
    assert full["*"] == "allow"
    assert full["bash"]["/opt/agent-team/bin/agent-team cancel *"] == "deny"


def test_opencode_profile_hash_includes_inline_permission_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "1.18.18")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    monkeypatch.setattr(
        "agent_team.adapters.opencode.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    original = adapter.profile_fingerprint("default", "resume")
    original_config = adapter._runtime_config

    def changed_config(profile: str, **kwargs):
        value = original_config(profile, **kwargs)
        value["permission"]["webfetch"] = "allow"
        return value

    monkeypatch.setattr(adapter, "_runtime_config", changed_config)

    assert adapter.profile_fingerprint("default", "resume") != original


def test_opencode_resolves_qualified_user_model_and_opaque_variant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))

    def run(command, **kwargs):
        assert kwargs["cwd"] == tmp_path
        assert kwargs["env"]["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
        assert kwargs["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
        if command == ["/bin/opencode", "models", "deepseek"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="deepseek/deepseek-v4-pro\n",
                stderr="",
            )
        assert command == ["/bin/opencode", "debug", "config", "--pure"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"model": "deepseek/deepseek-v4-pro"}),
            stderr="",
        )

    monkeypatch.setattr("agent_team.adapters.opencode.subprocess.run", run)

    assert adapter.resolve_launch_options(
        model=None,
        reasoning_effort="provider-deep",
        fast_mode=None,
        workspace=tmp_path,
    ) == HarnessLaunchOptions(
        model="deepseek/deepseek-v4-pro",
        reasoning_effort="provider-deep",
    )


def test_opencode_qualifies_a_unique_unqualified_user_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))

    def run(command, **kwargs):
        if command == ["/bin/opencode", "debug", "config", "--pure"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "model": "doubao-seed-2.0-pro",
                        "provider": {
                            "anthropic": {"models": {"doubao-seed-2.0-pro": {}}},
                            "other": {"models": {"different-model": {}}},
                        },
                    }
                ),
                stderr="",
            )
        assert command == ["/bin/opencode", "models", "anthropic"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="anthropic/doubao-seed-2.0-pro\n",
            stderr="",
        )

    monkeypatch.setattr("agent_team.adapters.opencode.subprocess.run", run)

    options = adapter.resolve_launch_options(
        model=None,
        reasoning_effort=None,
        fast_mode=None,
        workspace=tmp_path,
    )

    assert options.model == "anthropic/doubao-seed-2.0-pro"


def test_opencode_rejects_a_model_missing_from_the_effective_catalog(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))
    monkeypatch.setattr(
        "agent_team.adapters.opencode.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="Provider not found: missing",
        ),
    )

    with pytest.raises(InvalidArgument, match="not available"):
        adapter.resolve_launch_options(
            model="missing/example-model",
            reasoning_effort=None,
            fast_mode=None,
            workspace=tmp_path,
        )


@pytest.mark.parametrize("model", [None, "unqualified", "provider/", "/model"])
def test_opencode_rejects_missing_or_unqualified_models(model: str | None) -> None:
    with pytest.raises(InvalidArgument, match="provider/model"):
        OpenCodeAdapter().assert_launch_options(HarnessLaunchOptions(model=model))


def test_opencode_rejects_codex_fast_mode() -> None:
    with pytest.raises(InvalidArgument, match="only supported by the codex"):
        OpenCodeAdapter().assert_launch_options(
            HarnessLaunchOptions(
                model="deepseek/deepseek-v4-pro",
                fast_mode=True,
            )
        )


def test_opencode_headless_start_and_resume_use_stdin_and_frozen_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, workspace, turn_dir = _prepare_adapter(tmp_path, monkeypatch)
    launches = []
    for session_ref in (None, "ses_003ac0a84ffe623SFrcdLywRW1"):
        context = launch_context(
            adapter=adapter,
            session_policy="resume",
            session_ref=session_ref,
            profile="trusted-workspace",
            model="deepseek/deepseek-v4-pro",
            reasoning_effort="high",
            workspace=str(workspace),
            turn_dir=str(turn_dir),
        )
        launches.append(adapter.prepare_launch(context))

    start, resumed = launches
    for launch in launches:
        assert launch.argv[:2] == ("/bin/opencode", "run")
        assert "--format" in launch.argv
        assert launch.argv[launch.argv.index("--format") + 1] == "json"
        assert launch.argv[launch.argv.index("--model") + 1] == (
            "deepseek/deepseek-v4-pro"
        )
        assert launch.argv[launch.argv.index("--variant") + 1] == "high"
        assert launch.stdin == "perform the turn"
        assert launch.env["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
        assert launch.env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
        config = json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])
        assert config["share"] == "disabled"
        assert config["model"] == "deepseek/deepseek-v4-pro"
        assert config["small_model"] == "deepseek/deepseek-v4-pro"
        assert config["agent"]["agent-team-runtime"]["model"] == (
            "deepseek/deepseek-v4-pro"
        )
        assert config["agent"]["agent-team-runtime"]["variant"] == "high"
        assert config["permission"]["bash"]["*"] == "deny"
        assert LaunchSpec.from_json(launch.to_json()) == launch
    assert start.starts_new_session is True
    assert "--session" not in start.argv
    assert resumed.starts_new_session is False
    assert resumed.argv[resumed.argv.index("--session") + 1] == (
        "ses_003ac0a84ffe623SFrcdLywRW1"
    )


def test_opencode_freezes_selected_custom_provider_without_copying_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    run_dir = workspace / ".agent-team" / "runs" / "at-adapter-test"
    turn_dir = run_dir / "turns" / "turn-0001"
    turn_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        "agent_team.adapters.opencode.fixed_state_dir",
        lambda: state_dir,
    )
    monkeypatch.setattr(
        "agent_team.adapters.opencode.effective_agent_team_cli",
        lambda: Path("/opt/agent-team/bin/agent-team"),
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-provider-secret-value")
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))
    monkeypatch.setattr(adapter, "executable_version", lambda: "1.18.18")
    monkeypatch.setattr(adapter, "authentication_status", lambda: True)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "model": "anthropic/doubao-seed-2.0-pro",
                    "provider": {
                        "anthropic": {
                            "options": {
                                "baseURL": "https://example.invalid/v1",
                                "apiKey": "test-provider-secret-value",
                                "headers": {
                                    "Authorization": (
                                        "Bearer test-provider-secret-value"
                                    )
                                },
                            },
                            "models": {"doubao-seed-2.0-pro": {"name": "Doubao"}},
                        }
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("agent_team.adapters.opencode.subprocess.run", run)
    profile_hash = adapter.profile_fingerprint("full-access", "resume")
    team = make_team(
        run_id="at-adapter-test",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "opencode",
                "resume",
                "full-access",
                profile_hash,
                "anthropic/doubao-seed-2.0-pro",
                None,
                None,
                "headless",
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=60,
    )
    (run_dir / "team.json").write_bytes(team.canonical_bytes())

    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="headless",
    )

    assert len(calls) == 1
    assert calls[0][1]["env"]["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    home = adapter._config_home(run_dir, "developer")
    snapshot_text = (home / "agent-team-provider.json").read_text(encoding="utf-8")
    assert "test-provider-secret-value" not in snapshot_text
    snapshot = json.loads(snapshot_text)
    provider = snapshot["provider"]
    assert provider["options"]["apiKey"] == "{env:ANTHROPIC_API_KEY}"
    assert provider["options"]["headers"]["Authorization"] == (
        "Bearer {env:ANTHROPIC_API_KEY}"
    )
    assert adapter.worker_environment_names(
        run_dir=run_dir,
        role_id="developer",
    ) == ("ANTHROPIC_API_KEY",)

    context = launch_context(
        adapter=adapter,
        session_policy="resume",
        session_ref=None,
        profile="full-access",
        model="anthropic/doubao-seed-2.0-pro",
        workspace=str(workspace),
        turn_dir=str(turn_dir),
    )
    launch = adapter.prepare_launch(context)
    assert "test-provider-secret-value" not in launch.env["OPENCODE_CONFIG_CONTENT"]
    inline = json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])
    assert inline["provider"]["anthropic"] == provider
    assert inline["model"] == "anthropic/doubao-seed-2.0-pro"
    assert inline["small_model"] == "anthropic/doubao-seed-2.0-pro"
    assert "test-provider-secret-value" not in json.dumps(launch.to_json())

    monkeypatch.setattr(
        "agent_team.adapters.opencode.subprocess.run",
        lambda *args, **kwargs: pytest.fail("frozen provider was re-resolved"),
    )
    adapter.prepare_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="headless",
    )


def test_opencode_rejects_literal_provider_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_TEAM_UNSAFE_LITERAL", raising=False)
    with pytest.raises(AgentTeamError) as raised:
        OpenCodeAdapter._sanitize_provider_value(
            {"options": {"apiKey": "not-backed-by-an-environment-variable"}}
        )
    assert raised.value.code == "HARNESS_PROVIDER_CREDENTIAL_UNSAFE"


def test_opencode_does_not_treat_model_tokenizer_as_a_credential() -> None:
    value = {"models": {"example": {"tokenizer": "provider-default"}}}
    assert OpenCodeAdapter._sanitize_provider_value(value) == value


def test_opencode_interactive_launch_uses_direct_mode_and_resume_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, workspace, turn_dir = _prepare_adapter(tmp_path, monkeypatch)
    context = launch_context(
        adapter=adapter,
        session_policy="resume",
        session_ref="ses_003ac0a84ffe623SFrcdLywRW1",
        profile="default",
        model="deepseek/deepseek-v4-pro",
        reasoning_effort="high",
        launch_mode="interactive",
        workspace=str(workspace),
        turn_dir=str(turn_dir),
    )

    launch = adapter.prepare_launch(context)

    assert launch.argv[:3] == ("/bin/opencode", "run", "--interactive")
    assert launch.argv[launch.argv.index("--dir") + 1] == str(workspace)
    assert launch.argv[launch.argv.index("--variant") + 1] == "high"
    assert "--prompt" not in launch.argv
    assert launch.expected_session_ref == "ses_003ac0a84ffe623SFrcdLywRW1"
    assert launch.prompt_file == str(turn_dir / "process" / "prompt.md")
    config = json.loads(launch.env["OPENCODE_CONFIG_CONTENT"])
    assert config["model"] == "deepseek/deepseek-v4-pro"
    assert config["small_model"] == "deepseek/deepseek-v4-pro"
    assert config["agent"]["agent-team-runtime"]["variant"] == "high"


def test_opencode_run_state_is_private_and_preserves_internal_symlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, _workspace, turn_dir = _prepare_adapter(tmp_path, monkeypatch)
    run_dir = turn_dir.parent.parent
    home = adapter._config_home(run_dir, "developer")
    package = home / "opencode" / "node_modules" / "package"
    package.mkdir(parents=True)
    executable = package / "tool"
    executable.write_text("tool", encoding="utf-8")
    executable.chmod(0o755)
    link = home / "opencode" / "node_modules" / "tool"
    link.symlink_to(Path("package") / "tool")

    adapter.finalize_run_state(
        run_dir=run_dir,
        role_id="developer",
        launch_mode="headless",
    )

    assert link.is_symlink()
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert stat.S_IMODE(executable.stat().st_mode) == 0o700


def test_opencode_interactive_session_refs_are_scoped_to_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    other = tmp_path / "other"
    workspace.mkdir()
    other.mkdir()
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))

    def run(command, **kwargs):
        assert command[-4:] == ["--format", "json", "--max-count", "1000"]
        assert kwargs["cwd"] == str(workspace)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    {"id": "matching", "directory": str(workspace)},
                    {"id": "other", "directory": str(other)},
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr("agent_team.adapters.opencode.subprocess.run", run)
    launch = LaunchSpec(
        adapter_id="opencode",
        argv=("/bin/opencode", str(workspace)),
        cwd=str(workspace),
        env={"XDG_CONFIG_HOME": str(tmp_path / "config")},
        stdin="prompt",
        launch_profile="default",
        launch_profile_sha256="0" * 64,
        starts_new_session=True,
        launch_mode="interactive",
        prompt_file=str(tmp_path / "prompt.md"),
    )

    assert adapter.interactive_session_refs(launch) == {"matching"}


def test_opencode_interactive_session_refs_accept_empty_cli_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))
    monkeypatch.setattr(
        "agent_team.adapters.opencode.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="",
            stderr="",
        ),
    )
    launch = LaunchSpec(
        adapter_id="opencode",
        argv=("/bin/opencode", str(workspace)),
        cwd=str(workspace),
        env={"XDG_CONFIG_HOME": str(tmp_path / "config")},
        stdin="prompt",
        launch_profile="default",
        launch_profile_sha256="0" * 64,
        starts_new_session=True,
        launch_mode="interactive",
        prompt_file=str(tmp_path / "prompt.md"),
    )

    assert adapter.interactive_session_refs(launch) == set()


def test_opencode_structured_evidence_and_tool_normalization() -> None:
    adapter = OpenCodeAdapter()
    session = "ses_003abe4d1ffejBDVLh06iNsaU2"
    snapshot = AdapterEvidenceSnapshot()
    snapshot.merge(
        adapter.parse_stream_record(
            record(
                {
                    "type": "step_start",
                    "sessionID": session,
                    "part": {"type": "step-start"},
                }
            )
        )
    )
    tool = record(
        {
            "type": "tool_use",
            "sessionID": session,
            "part": {
                "type": "tool",
                "tool": "bash",
                "callID": "call-1",
                "state": {
                    "status": "completed",
                    "input": {"command": "pwd"},
                    "output": "/workspace\n",
                    "metadata": {"exit": 0},
                },
            },
        }
    )
    snapshot.merge(adapter.parse_stream_record(tool))
    snapshot.merge(
        adapter.parse_stream_record(
            record(
                {
                    "type": "step_finish",
                    "sessionID": session,
                    "part": {
                        "type": "step-finish",
                        "reason": "tool-calls",
                    },
                }
            )
        )
    )
    assert snapshot.adapter_completed is False
    snapshot.merge(
        adapter.parse_stream_record(
            record(
                {
                    "type": "step_finish",
                    "sessionID": session,
                    "part": {
                        "type": "step-finish",
                        "reason": "stop",
                        "tokens": {"input": 3, "output": 2},
                    },
                }
            )
        )
    )

    assert snapshot.agent_execution_started is True
    assert snapshot.adapter_completed is True
    assert snapshot.observed_session_ref == session
    normalized = adapter.normalize_stream_record(tool)
    assert [event.event_type for event in normalized] == [
        "tool_call",
        "tool_result",
    ]
    assert normalized[0].data["input"] == {"command": "pwd"}
    assert normalized[1].data["output"] == "/workspace\n"


def test_opencode_permission_error_is_structured_evidence() -> None:
    adapter = OpenCodeAdapter()
    evidence = adapter.parse_stream_record(
        record(
            {
                "type": "tool_use",
                "sessionID": "ses_permission",
                "part": {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "error",
                        "error": "Permission denied for this command",
                    },
                },
            }
        )
    )

    assert evidence is not None
    assert evidence.agent_execution_started is True
    assert evidence.permission_required is True


def test_opencode_reports_exact_cli_missing_session_error() -> None:
    adapter = OpenCodeAdapter()
    missing = StreamRecord(
        source="stderr",
        first_seq=1,
        last_seq=1,
        observed_at="2026-08-14T00:00:00Z",
        encoding="utf-8",
        data="\x1b[91m\x1b[1mError: \x1b[0mSession not found\n",
    )

    evidence = adapter.parse_stream_record(missing)

    assert evidence is not None
    assert evidence.session_unavailable_reason == "session_not_found"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("0 credentials\n0 environment variables\n", False),
        ("1 credential\n0 environment variables\n", True),
        ("0 credentials\n2 environment variables\n", True),
    ],
)
def test_opencode_authentication_probe_uses_credential_counts(
    output: str,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OpenCodeAdapter()
    monkeypatch.setattr(adapter, "executable", lambda: Path("/bin/opencode"))
    monkeypatch.setattr(
        "agent_team.adapters.opencode.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=output,
            stderr="",
        ),
    )

    assert adapter.authentication_status() is expected

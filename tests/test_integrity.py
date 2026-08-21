from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_team.bootstrap import initialize_run, start_run
from agent_team.config import (
    REQUIRED_AUDIT_PAYLOAD_SECTIONS,
    ObservabilityPolicy,
    Role,
    Team,
    make_team,
    parse_team,
)
from agent_team.errors import AgentTeamError, IntegrityError, InvalidArgument
from agent_team.journal import scan_journal
from agent_team.observation import derive_observation
from agent_team.origin import origin_action, wait_origin
from agent_team.state import (
    ensure_workspace_lock,
    file_lock,
    read_owner,
    state_paths,
    workspace_lock,
)
from agent_team.turns import validate_outbox, validate_payload_contract
from agent_team.util import (
    is_uncommitted_atomic_temporary,
    read_private_regular,
    rfc3339,
)


def _run(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    run_id: str,
) -> Path:
    request, protocol = request_protocol
    team = make_team(
        run_id=run_id,
        workspace=workspace,
        origin_harness="codex",
        roles={"reviewer": Role("reviewer", "origin")},
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    )
    return initialize_run(team=team, request_path=request, protocol_path=protocol)


def test_private_run_source_rejects_multiple_hard_links(tmp_path: Path) -> None:
    source = tmp_path / "payload.md"
    source.write_text("private payload\n", encoding="utf-8")
    os.link(source, tmp_path / "second-name.md")

    with pytest.raises(IntegrityError, match="multiple hard links"):
        read_private_regular(source)


def test_full_access_requires_one_confirmation_before_first_kickoff(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_team import bootstrap

    request, protocol = request_protocol
    team = make_team(
        run_id="at-test-full-access-confirmation",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "full-access",
                "f" * 64,
                fast_mode=False,
                launch_mode="interactive",
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    )
    monkeypatch.setattr(bootstrap, "_assert_external_capability", lambda _role: None)
    run_dir = initialize_run(
        team=team,
        request_path=request,
        protocol_path=protocol,
    )
    preflight_called = False

    def unexpected_preflight(_run_dir: Path) -> Team:
        nonlocal preflight_called
        preflight_called = True
        return team

    monkeypatch.setattr(bootstrap, "_preflight_start", unexpected_preflight)

    with pytest.raises(AgentTeamError) as rejected:
        start_run(run_dir)

    assert rejected.value.code == "FULL_ACCESS_CONFIRMATION_REQUIRED"
    assert rejected.value.exit_code == 2
    assert scan_journal(run_dir).status == "UNSTARTED"
    assert read_owner(workspace) is None
    assert list((run_dir / "events").iterdir()) == []
    assert preflight_called is False

    monkeypatch.setattr(bootstrap, "_preflight_start", lambda _run_dir: team)
    monkeypatch.setattr(
        bootstrap,
        "ensure_workers",
        lambda _run_dir, _team, **_kwargs: {
            "session": "test-session",
            "created": [],
            "existing": [],
        },
    )
    monkeypatch.setattr(bootstrap, "signal_change", lambda *_args: None)

    started = start_run(run_dir, confirm_full_access=True)

    assert started["status"] == "RUNNING"
    kickoff = started["kickoff_event"]
    payload = (run_dir / kickoff["payload_path"]).read_text(encoding="utf-8")
    assert "Full-access confirmation" in payload
    assert "`developer`" in payload

    from agent_team import management

    monkeypatch.setattr(
        management,
        "recover_run",
        lambda _run_dir: {
            "status": "RUNNING",
            "tmux": None,
            "actions": [],
            "owner_released": False,
        },
    )
    repeated = start_run(run_dir)
    assert repeated["status"] == "RUNNING"
    assert repeated["kickoff_event"] is None


@pytest.mark.parametrize("filename", ["REQUEST.md", "PROTOCOL.md", "team.json"])
def test_kickoff_inputs_are_immutable(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    filename: str,
) -> None:
    run_dir = _run(
        workspace, request_protocol, f"at-test-{filename.split('.')[0].lower()}"
    )
    start_run(run_dir)
    path = run_dir / filename
    path.write_bytes(path.read_bytes() + b"\nchanged")

    with pytest.raises(IntegrityError):
        scan_journal(run_dir)


def test_journal_rejects_unhashable_event_type_as_corruption(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(workspace, request_protocol, "at-test-event-type")
    start_run(run_dir)
    event_path = run_dir / "events" / "0001-kickoff-0001.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["event_type"] = []
    event_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(IntegrityError, match="invalid event type"):
        scan_journal(run_dir)


def test_journal_rejects_unhashable_block_reason_as_corruption(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(workspace, request_protocol, "at-test-block-reason")
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    payload = run_dir / "turns" / claim["turn_id"] / "block.md"
    payload.write_text("# Block\n\nNeed input.\n", encoding="utf-8")
    origin_action(
        run_dir,
        action="block",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )
    event_path = run_dir / "events" / "0002-block-0002.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["block_reason"] = []
    event_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(IntegrityError, match="block fields"):
        scan_journal(run_dir)


@pytest.mark.parametrize("field", ["adapter", "session_policy"])
def test_team_rejects_unhashable_external_discriminators(
    workspace: Path,
    field: str,
) -> None:
    team = make_team(
        run_id=f"at-test-team-{field.replace('_', '-')}",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    team["roles"]["developer"][field] = []

    with pytest.raises(IntegrityError):
        parse_team(team)


@pytest.mark.parametrize("schema_version", [True, 4.0, "4", None])
def test_team_schema_version_requires_an_exact_integer(
    workspace: Path,
    schema_version: object,
) -> None:
    team = make_team(
        run_id="at-test-team-schema-type",
        workspace=workspace,
        origin_harness="codex",
        roles={"reviewer": Role("reviewer", "origin")},
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    team["schema_version"] = schema_version

    with pytest.raises(IntegrityError, match="unsupported team.json schema"):
        parse_team(team)


@pytest.mark.parametrize("schema_version", [True, 1.0])
def test_journal_schema_version_requires_an_exact_integer(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    schema_version: object,
) -> None:
    run_dir = _run(workspace, request_protocol, "at-test-event-schema-type")
    start_run(run_dir)
    event_path = run_dir / "events" / "0001-kickoff-0001.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["schema_version"] = schema_version
    event_path.write_text(json.dumps(event), encoding="utf-8")

    with pytest.raises(IntegrityError, match="unsupported event"):
        scan_journal(run_dir)


def test_team_schema_preserves_frozen_harness_options(workspace: Path) -> None:
    team = make_team(
        run_id="at-test-harness-options",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "default",
                "0" * 64,
                "gpt-5.6-sol",
                "max",
                True,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    )

    assert team.config_schema_version == 7
    assert team.to_json()["roles"]["developer"]["harness_options"] == {
        "model": "gpt-5.6-sol",
        "reasoning_effort": "max",
        "fast_mode": True,
        "model_provider": "openai",
        "model_provider_config": None,
    }
    assert team.roles["developer"].model == "gpt-5.6-sol"
    assert team.roles["developer"].reasoning_effort == "max"
    assert team.roles["developer"].fast_mode is True
    assert team.roles["developer"].model_provider == "openai"
    assert team.roles["developer"].launch_mode == "interactive"


def test_team_schema_preserves_frozen_codex_model_provider(
    workspace: Path,
) -> None:
    provider_config = {
        "name": "Company Proxy",
        "base_url": "https://proxy.example.test/v1",
        "env_key": "COMPANY_PROXY_API_KEY",
        "env_http_headers": {"X-Tenant": "COMPANY_TENANT"},
        "wire_api": "responses",
    }
    team = make_team(
        run_id="at-test-model-provider",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "default",
                "0" * 64,
                "proxy-model",
                "high",
                False,
                "interactive",
                None,
                "company_proxy",
                provider_config,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    )

    parsed = parse_team(team.to_json())

    assert parsed.roles["developer"].model_provider == "company_proxy"
    assert parsed.roles["developer"].model_provider_config == provider_config


def test_team_schema_v5_remains_readable_without_model_provider(
    workspace: Path,
) -> None:
    value = make_team(
        run_id="at-test-schema-v5",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    value["schema_version"] = 5
    options = value["roles"]["developer"]["harness_options"]
    options.pop("model_provider")
    options.pop("model_provider_config")

    parsed = parse_team(value)

    assert parsed.config_schema_version == 5
    assert parsed.roles["developer"].model_provider is None
    assert parsed.roles["developer"].model_provider_config is None


@pytest.mark.parametrize(
    ("provider", "provider_config", "message"),
    [
        (None, None, "model provider is required"),
        (
            "openai",
            {"base_url": "https://proxy.example.test/v1"},
            "built-in model provider cannot be overridden",
        ),
        ("company_proxy", None, "custom model provider has no definition"),
    ],
)
def test_team_rejects_incomplete_codex_model_provider_contract(
    workspace: Path,
    provider: str | None,
    provider_config: dict[str, object] | None,
    message: str,
) -> None:
    value = make_team(
        run_id="at-test-invalid-model-provider",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    options = value["roles"]["developer"]["harness_options"]
    options["model_provider"] = provider
    options["model_provider_config"] = provider_config

    with pytest.raises(IntegrityError, match=message):
        parse_team(value)


def test_team_schema_preserves_frozen_claude_model_provider(
    workspace: Path,
) -> None:
    provider_config = {
        "settings": {
            "base_url": "https://gateway.example.test/anthropic",
        },
        "credential_environment_names": ["ANTHROPIC_AUTH_TOKEN"],
    }
    team = make_team(
        run_id="at-test-claude-model-provider",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "reviewer": Role(
                "reviewer",
                "external",
                "claude-code",
                "resume",
                "default",
                "0" * 64,
                "gateway-model",
                "high",
                None,
                "interactive",
                None,
                "gateway",
                provider_config,
            )
        },
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    )

    parsed = parse_team(team.to_json())

    assert parsed.roles["reviewer"].model_provider == "gateway"
    assert parsed.roles["reviewer"].model_provider_config == provider_config


@pytest.mark.parametrize(
    ("provider_config", "message"),
    [
        (
            {
                "settings": {
                    "base_url": "https://token@gateway.example.test/anthropic",
                },
                "credential_environment_names": ["ANTHROPIC_AUTH_TOKEN"],
            },
            "without credentials",
        ),
        (
            {
                "settings": {
                    "base_url": "https://gateway.example.test/anthropic",
                    "api_key": "plaintext-secret",
                },
                "credential_environment_names": [],
            },
            "unsupported fields",
        ),
        (
            {
                "settings": {
                    "base_url": "https://gateway.example.test/anthropic",
                },
                "credential_environment_names": ["UNSAFE_CUSTOM_TOKEN"],
            },
            "unsupported names",
        ),
    ],
)
def test_team_rejects_unsafe_claude_model_provider_config(
    workspace: Path,
    provider_config: dict[str, object],
    message: str,
) -> None:
    value = make_team(
        run_id="at-test-unsafe-claude-provider",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "reviewer": Role(
                "reviewer",
                "external",
                "claude-code",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    options = value["roles"]["reviewer"]["harness_options"]
    options["model_provider"] = "gateway"
    options["model_provider_config"] = provider_config

    with pytest.raises(IntegrityError, match=message):
        parse_team(value)


def test_team_schema_v6_claude_route_remains_direct_anthropic(
    workspace: Path,
) -> None:
    value = make_team(
        run_id="at-test-schema-v6-claude",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "reviewer": Role(
                "reviewer",
                "external",
                "claude-code",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    value["schema_version"] = 6
    options = value["roles"]["reviewer"]["harness_options"]
    options["model_provider"] = None
    options["model_provider_config"] = None

    parsed = parse_team(value)

    assert parsed.config_schema_version == 6
    assert parsed.roles["reviewer"].model_provider is None
    assert parsed.roles["reviewer"].model_provider_config is None


def test_team_rejects_separate_model_provider_for_opencode_role(
    workspace: Path,
) -> None:
    value = make_team(
        run_id="at-test-opencode-model-provider",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "reviewer": Role(
                "reviewer",
                "external",
                "opencode",
                "resume",
                "default",
                "0" * 64,
                "openai/gpt-5",
            )
        },
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    options = value["roles"]["reviewer"]["harness_options"]
    options["model_provider"] = "company_proxy"
    options["model_provider_config"] = {
        "base_url": "https://proxy.example.test/v1"
    }

    with pytest.raises(IntegrityError, match="not a separate option"):
        parse_team(value)


def test_legacy_team_schema_has_no_frozen_harness_options(
    workspace: Path,
) -> None:
    value = make_team(
        run_id="at-test-legacy-harness-options",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    value["schema_version"] = 2
    value["roles"]["developer"].pop("harness_options")
    value["roles"]["developer"].pop("launch_mode")
    value["roles"]["developer"].pop("dsh_plugin")

    parsed = parse_team(value)

    assert parsed.config_schema_version == 2
    assert parsed.roles["developer"].model is None
    assert parsed.roles["developer"].reasoning_effort is None
    assert parsed.roles["developer"].fast_mode is None
    assert parsed.roles["developer"].launch_mode == "headless"


def test_team_rejects_fast_mode_for_claude_code(workspace: Path) -> None:
    value = make_team(
        run_id="at-test-claude-fast",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "claude-code",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    value["roles"]["developer"]["harness_options"]["fast_mode"] = True

    with pytest.raises(IntegrityError, match="fast mode is not supported"):
        parse_team(value)


def test_team_accepts_frozen_opencode_model_and_variant(workspace: Path) -> None:
    team = make_team(
        run_id="at-test-opencode-options",
        workspace=workspace,
        origin_harness="opencode",
        roles={
            "developer": Role(
                "developer",
                "external",
                "opencode",
                "resume",
                "default",
                "0" * 64,
                "deepseek/deepseek-v4-pro",
                "provider-deep",
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    )

    assert team.roles["developer"].adapter == "opencode"
    assert team.roles["developer"].model == "deepseek/deepseek-v4-pro"
    assert team.roles["developer"].reasoning_effort == "provider-deep"


def test_team_accepts_frozen_deepseek_harness_route_and_effort(
    workspace: Path,
) -> None:
    team = make_team(
        run_id="at-test-dsh-options",
        workspace=workspace,
        origin_harness="deepseek-harness",
        roles={
            "developer": Role(
                "developer",
                "external",
                "deepseek-harness",
                "resume",
                "default",
                "0" * 64,
                "deepseek-official/deepseek-v4-flash",
                "max",
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    )

    assert team.roles["developer"].adapter == "deepseek-harness"
    assert team.roles["developer"].reasoning_effort == "max"


@pytest.mark.parametrize("effort", [None, "medium", "xhigh"])
def test_team_rejects_invalid_deepseek_harness_effort(
    workspace: Path,
    effort: str | None,
) -> None:
    value = make_team(
        run_id="at-test-dsh-invalid-effort",
        workspace=workspace,
        origin_harness="deepseek-harness",
        roles={
            "developer": Role(
                "developer",
                "external",
                "deepseek-harness",
                "resume",
                "default",
                "0" * 64,
                "deepseek-official/deepseek-v4-flash",
                "high",
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    value["roles"]["developer"]["harness_options"]["reasoning_effort"] = effort

    with pytest.raises(IntegrityError, match="invalid reasoning effort"):
        parse_team(value)


@pytest.mark.parametrize("model", [None, "unqualified", "/model", "provider/"])
def test_team_rejects_unqualified_opencode_model(
    workspace: Path,
    model: str | None,
) -> None:
    value = make_team(
        run_id="at-test-opencode-model",
        workspace=workspace,
        origin_harness="opencode",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "resume",
                "default",
                "0" * 64,
            )
        },
        initial_role="developer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    value["roles"]["developer"]["adapter"] = "opencode"
    value["roles"]["developer"]["harness_options"]["model"] = model
    value["roles"]["developer"]["harness_options"]["fast_mode"] = None

    with pytest.raises(IntegrityError, match="provider/model"):
        parse_team(value)


@pytest.mark.parametrize(
    ("max_turns", "max_wall_time_seconds"),
    [
        (1 << 31, 300),
        (2, 1 << 31),
    ],
)
def test_team_rejects_unrepresentable_safety_limits(
    workspace: Path,
    max_turns: int,
    max_wall_time_seconds: int,
) -> None:
    with pytest.raises(InvalidArgument, match="limits must be integers"):
        make_team(
            run_id="at-test-limit-range",
            workspace=workspace,
            origin_harness="codex",
            roles={"reviewer": Role("reviewer", "origin")},
            initial_role="reviewer",
            max_turns=max_turns,
            max_wall_time_seconds=max_wall_time_seconds,
        )


def test_legacy_team_schema_preserves_pre_observability_semantics(
    workspace: Path,
) -> None:
    value = make_team(
        run_id="at-test-legacy-team",
        workspace=workspace,
        origin_harness="codex",
        roles={"reviewer": Role("reviewer", "origin")},
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    ).to_json()
    value["schema_version"] = 1
    value.pop("observability")

    parsed = parse_team(value)

    assert parsed.config_schema_version == 1
    assert parsed.observability == ObservabilityPolicy(
        redaction="none",
        raw_retention="keep",
    )


def test_full_audit_mode_requires_all_business_roles_to_be_external(
    workspace: Path,
) -> None:
    with pytest.raises(InvalidArgument, match="every business role"):
        make_team(
            run_id="at-test-full-audit-origin",
            workspace=workspace,
            origin_harness="codex",
            roles={"reviewer": Role("reviewer", "origin")},
            initial_role="reviewer",
            max_turns=2,
            max_wall_time_seconds=300,
            observability=ObservabilityPolicy(
                audit_mode="full",
                required_payload_sections=REQUIRED_AUDIT_PAYLOAD_SECTIONS,
            ),
        )


def test_full_audit_mode_accepts_external_roles_and_required_sections(
    workspace: Path,
) -> None:
    team = make_team(
        run_id="at-test-full-audit-external",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "reviewer": Role(
                "reviewer",
                "external",
                "codex",
                "fresh",
                "default",
                "0" * 64,
            )
        },
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
        observability=ObservabilityPolicy(
            audit_mode="full",
            required_payload_sections=REQUIRED_AUDIT_PAYLOAD_SECTIONS,
        ),
    )

    assert team.observability.audit_mode == "full"
    assert team.roles["reviewer"].binding == "external"


def test_full_audit_required_sections_are_case_insensitive(
    workspace: Path,
) -> None:
    team = make_team(
        run_id="at-test-full-audit-section-case",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "reviewer": Role(
                "reviewer",
                "external",
                "codex",
                "fresh",
                "default",
                "0" * 64,
            )
        },
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
        observability=ObservabilityPolicy(
            audit_mode="full",
            required_payload_sections=("decision RATIONALE", "EVIDENCE"),
        ),
    )

    assert team.observability.required_payload_sections == (
        "decision RATIONALE",
        "EVIDENCE",
    )


def test_full_audit_mode_rejects_raw_trace_deletion(
    workspace: Path,
) -> None:
    with pytest.raises(InvalidArgument, match="cannot delete"):
        make_team(
            run_id="at-test-full-audit-delete",
            workspace=workspace,
            origin_harness="codex",
            roles={
                "reviewer": Role(
                    "reviewer",
                    "external",
                    "codex",
                    "fresh",
                    "default",
                    "0" * 64,
                )
            },
            initial_role="reviewer",
            max_turns=2,
            max_wall_time_seconds=300,
            observability=ObservabilityPolicy(
                audit_mode="full",
                raw_retention="delete",
                required_payload_sections=REQUIRED_AUDIT_PAYLOAD_SECTIONS,
            ),
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"# Complete\n\nDone.\n", "missing"),
        (
            b"# Complete\n\n## Decision rationale\n\n## Evidence\n\nTests pass.\n",
            "empty",
        ),
    ],
)
def test_audited_payload_contract_rejects_missing_or_empty_sections(
    payload: bytes,
    message: str,
) -> None:
    with pytest.raises(AgentTeamError, match=message) as rejected:
        validate_payload_contract(
            payload,
            required_sections=REQUIRED_AUDIT_PAYLOAD_SECTIONS,
        )
    assert getattr(rejected.value, "code", None) == "PAYLOAD_CONTRACT_VIOLATION"


def test_audited_payload_contract_accepts_explicit_rationale_and_evidence() -> None:
    validate_payload_contract(
        (
            b"# Completion\n\n"
            b"## Decision rationale\n\n"
            b"The implementation meets the declared invariants.\n\n"
            b"## Evidence\n\n"
            b"`uv run pytest` passed.\n"
        ),
        required_sections=REQUIRED_AUDIT_PAYLOAD_SECTIONS,
    )


def test_unstarted_run_has_no_owner(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(workspace, request_protocol, "at-test-unstarted")
    observation = derive_observation(run_dir)
    assert observation["run_status"] == "UNSTARTED"
    assert observation["workspace_owner"] == "not_acquired"
    assert observation["recommended_action"] == "START"


def test_workspace_lock_creation_accepts_concurrent_winner(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_team import state

    _, lock_path, _ = state_paths(workspace)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_bytes(b"")
    real_exists = state.path_entry_exists
    monkeypatch.setattr(
        state,
        "path_entry_exists",
        lambda path: False if path == lock_path else real_exists(path),
    )

    assert ensure_workspace_lock(workspace) == lock_path


def test_workspace_lock_opens_existing_state_lock_read_only(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_team import state

    lock_path = ensure_workspace_lock(workspace)
    real_open = state.os.open
    observed_access_modes: list[int] = []

    def tracked_open(path, flags, *args):
        if Path(path) == lock_path:
            observed_access_modes.append(flags & os.O_ACCMODE)
        return real_open(path, flags, *args)

    monkeypatch.setattr(state.os, "open", tracked_open)

    with workspace_lock(workspace, exclusive=True):
        pass

    assert observed_access_modes == [os.O_RDONLY]


def test_run_staging_directory_uses_atomic_temporary_name(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, protocol = request_protocol
    team = make_team(
        run_id="at-test-run-staging-name",
        workspace=workspace,
        origin_harness="codex",
        roles={"reviewer": Role("reviewer", "origin")},
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    )
    from agent_team import bootstrap

    observed: list[bool] = []

    def fail_before_commit(source: Path, _target: Path) -> None:
        observed.append(is_uncommitted_atomic_temporary(Path(source)))
        raise OSError("injected directory commit failure")

    monkeypatch.setattr(bootstrap.os, "rename", fail_before_commit)

    with pytest.raises(OSError, match="directory commit failure"):
        initialize_run(
            team=team,
            request_path=request,
            protocol_path=protocol,
        )

    assert observed == [True]
    assert not (workspace / ".agent-team" / "runs" / team.run_id).exists()


def test_start_retains_owner_if_kickoff_commit_becomes_visible_then_errors(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = _run(
        workspace,
        request_protocol,
        "at-test-kickoff-visible-error",
    )
    from agent_team import bootstrap

    real_commit = bootstrap.commit_event

    def commit_then_fail(*args, **kwargs):
        real_commit(*args, **kwargs)
        raise OSError("injected post-rename failure")

    monkeypatch.setattr(bootstrap, "commit_event", commit_then_fail)

    with pytest.raises(OSError, match="post-rename failure"):
        start_run(run_dir)

    assert scan_journal(run_dir).status == "RUNNING"
    assert read_owner(workspace)["run_id"] == run_dir.name


def test_dangling_owner_symlink_is_corruption_not_missing_owner(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(
        workspace,
        request_protocol,
        "at-test-dangling-owner",
    )
    start_run(run_dir)
    _, _, owner_path = state_paths(workspace)
    owner_path.unlink()
    owner_path.symlink_to(owner_path.with_name("missing-owner.json"))

    with pytest.raises(IntegrityError, match="non-regular"):
        read_owner(workspace)

    with pytest.raises(IntegrityError, match="non-regular"):
        derive_observation(run_dir)


def test_uncommitted_event_atomic_temporary_is_not_a_journal_event(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(
        workspace,
        request_protocol,
        "at-test-event-atomic-temporary",
    )
    start_run(run_dir)
    temporary = (
        run_dir
        / "events"
        / ".0002-handoff-0002.json.tmp-999-0123456789abcdef"
    )
    temporary.write_text("partial", encoding="utf-8")

    projection = scan_journal(run_dir)

    assert projection.status == "RUNNING"
    assert len(projection.events) == 1


def test_uncommitted_turn_staging_directory_is_not_a_claim(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(
        workspace,
        request_protocol,
        "at-test-turn-atomic-temporary",
    )
    start_run(run_dir)
    temporary = (
        run_dir / "turns" / ".turn-0001.tmp-999-0123456789abcdef"
    )
    temporary.mkdir()
    (temporary / "input.md").write_text("partial", encoding="utf-8")

    claim = wait_origin(run_dir, timeout=0)

    assert claim["turn_id"] == "turn-0001"
    assert (run_dir / "turns" / "turn-0001" / "runtime.json").exists()
    assert temporary.exists()
    assert scan_journal(run_dir).status == "RUNNING"


def test_uncommitted_root_marker_temporary_does_not_bind_state_root(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    root = workspace / ".agent-team"
    root.mkdir()
    (root / ".root.json.tmp-999-0123456789abcdef").write_text(
        "partial",
        encoding="utf-8",
    )

    run_dir = _run(
        workspace,
        request_protocol,
        "at-test-root-atomic-temporary",
    )

    assert (workspace / ".agent-team" / "root.json").exists()
    assert run_dir.exists()


def test_init_rejects_symbolic_link_request(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    request, protocol = request_protocol
    linked = request.parent / "linked-request.md"
    linked.symlink_to(request)
    team = make_team(
        run_id="at-test-symlink-request",
        workspace=workspace,
        origin_harness="codex",
        roles={"reviewer": Role("reviewer", "origin")},
        initial_role="reviewer",
        max_turns=2,
        max_wall_time_seconds=300,
    )

    with pytest.raises(InvalidArgument, match="non-symlink"):
        initialize_run(
            team=team,
            request_path=linked,
            protocol_path=protocol,
        )


def test_init_rejects_nonempty_state_root_without_marker(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    stale = workspace / ".agent-team" / "runs" / "at-stale"
    stale.mkdir(parents=True)

    with pytest.raises(InvalidArgument, match="root.json is missing"):
        _run(workspace, request_protocol, "at-test-missing-root-marker")


def test_runtime_terminal_event_must_cross_link_to_journal(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(
        workspace,
        request_protocol,
        "at-test-runtime-terminal-cross-link",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    payload = run_dir / "turns" / claim["turn_id"] / "completion.md"
    payload.write_text("# Completion\n\nDone.\n", encoding="utf-8")
    origin_action(
        run_dir,
        action="complete",
        turn_id=claim["turn_id"],
        claim=claim["claim"],
        from_role="reviewer",
        source_file=payload,
    )
    runtime_path = run_dir / "turns" / claim["turn_id"] / "runtime.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["terminal_event_id"] = "cancel-9999"
    runtime_path.write_text(
        json.dumps(runtime, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(IntegrityError, match="terminat"):
        scan_journal(run_dir)


def test_journal_corruption_has_priority_over_mutable_runtime_damage(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(
        workspace,
        request_protocol,
        "at-test-journal-priority",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    (run_dir / "turns" / claim["turn_id"] / "runtime.json").write_text(
        "{broken runtime",
        encoding="utf-8",
    )
    event_path = run_dir / "events" / "0001-kickoff-0001.json"
    event_path.write_text("{broken event", encoding="utf-8")

    with pytest.raises(IntegrityError) as captured:
        scan_journal(run_dir)

    assert str(event_path) in captured.value.message


def test_missing_referenced_turn_input_is_corruption_not_io_error(
    workspace: Path,
    request_protocol: tuple[Path, Path],
) -> None:
    run_dir = _run(
        workspace,
        request_protocol,
        "at-test-missing-turn-input",
    )
    start_run(run_dir)
    claim = wait_origin(run_dir, timeout=0)
    (run_dir / "turns" / claim["turn_id"] / "input.md").unlink()

    with pytest.raises(IntegrityError, match="turn input is missing"):
        derive_observation(run_dir)


def test_lock_file_with_additional_hard_link_is_rejected(tmp_path: Path) -> None:
    lock = tmp_path / "journal.lock"
    lock.write_bytes(b"")
    os.link(lock, tmp_path / "second-name.lock")

    with (
        pytest.raises(IntegrityError, match="not a regular file"),
        file_lock(lock, exclusive=False),
    ):
        raise AssertionError("invalid lock was acquired")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("to_role", "../reviewer"),
        ("payload_path", "handoffs/0001-kickoff.md"),
        ("created_at", "not-a-time"),
        ("action", []),
    ],
)
def test_outbox_requires_canonical_identity_fields(field: str, value: object) -> None:
    outbox = {
        "schema_version": 1,
        "turn_id": "turn-0001",
        "action": "handoff",
        "to_role": "reviewer",
        "block_reason": None,
        "payload_path": "turns/turn-0001/outbox-payload.md",
        "payload_sha256": "0" * 64,
        "created_at": rfc3339(),
    }
    outbox[field] = value

    with pytest.raises(IntegrityError, match="outbox|RFC 3339"):
        validate_outbox(outbox, turn_id="turn-0001")

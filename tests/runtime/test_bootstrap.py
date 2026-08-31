from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_team.bootstrap import _assert_external_capability, initialize_run, start_run
from agent_team.config import (
    REQUIRED_AUDIT_PAYLOAD_SECTIONS,
    ObservabilityPolicy,
    Role,
    WorkflowPolicy,
    make_team,
)
from agent_team.errors import AgentTeamError
from agent_team.turns import (
    render_turn_prompt,
)

from ._support import (
    PROFILE,
    PROFILE_HASH,
    _BootstrapAdapter,
    _external_run,
)


def test_external_turn_prompt_disambiguates_skill_from_formal_cli(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-prompt-formal-cli",
    )

    prompt = render_turn_prompt(
        run_dir,
        runtime,
        cli_path="/opt/agent-team/bin/agent-team",
        session_ref=None,
    )

    assert "documentation, not an action interface" in prompt
    assert "has no `--complete`, `--summary`" in prompt
    assert "Do not invoke the Skill again to transition state" in prompt
    assert (
        "`/opt/agent-team/bin/agent-team handoff --to <role-id> --file <payload>`"
    ) in prompt


def test_external_turn_prompt_explains_strengthened_payload_contract(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-prompt-coverage-contract",
        observability=ObservabilityPolicy(
            required_payload_sections=REQUIRED_AUDIT_PAYLOAD_SECTIONS,
        ),
    )

    prompt = render_turn_prompt(
        run_dir,
        runtime,
        cli_path="/opt/agent-team/bin/agent-team",
        session_ref=None,
    )

    assert "`## Acceptance coverage`" in prompt
    assert "`## Open findings`" in prompt
    assert "map every material Request and Protocol condition" in prompt
    assert "its only content must be `None`" in prompt
    assert "the CLI rejects any other content" in prompt


def test_external_turn_prompt_surfaces_frozen_workflow_policy(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-prompt-workflow-policy",
        include_external_reviewer=True,
        workflow=WorkflowPolicy(
            allowed_handoffs={"developer": ("reviewer",), "reviewer": ()},
            read_only_roles=("developer",),
        ),
    )

    prompt = render_turn_prompt(
        run_dir,
        runtime,
        cli_path="/opt/agent-team/bin/agent-team",
        session_ref=None,
    )

    assert "Role-selected Handoff targets: `reviewer`" in prompt
    assert "Workspace access: read-only" in prompt
    assert "Any Git-visible workspace change" in prompt


def test_external_turn_prompt_indexes_prior_formal_inputs(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir, runtime = _external_run(
        workspace,
        request_protocol,
        monkeypatch,
        run_id="at-worker-prompt-history-index",
    )
    prior_path = run_dir / "handoffs" / "0001-validator-to-developer.md"
    prior_path.write_text("# Finding\n", encoding="utf-8")
    current_event_id = runtime["input_event_id"]
    monkeypatch.setattr(
        "agent_team.turns.scan_journal",
        lambda _run_dir: SimpleNamespace(
            events=[
                {
                    "event_id": "handoff-0001",
                    "event_seq": 1,
                    "event_type": "handoff",
                    "from_role": "validator",
                    "to_role": "developer",
                    "payload_path": "handoffs/0001-validator-to-developer.md",
                },
                {
                    "event_id": current_event_id,
                    "event_seq": 2,
                    "event_type": "handoff",
                    "from_role": "developer",
                    "to_role": "reviewer",
                    "payload_path": "handoffs/0002-developer-to-reviewer.md",
                },
            ]
        ),
    )

    prompt = render_turn_prompt(
        run_dir,
        runtime,
        cli_path="/opt/agent-team/bin/agent-team",
        session_ref=None,
    )

    assert "Prior formal input index, earliest first" in prompt
    assert str(prior_path) in prompt
    assert "latest Handoff does not erase" in prompt


def test_init_rejects_launcher_that_detaches_from_managed_group(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, protocol = request_protocol
    adapter = _BootstrapAdapter()
    monkeypatch.setattr(
        adapter,
        "probe",
        lambda: SimpleNamespace(
            authenticated=True,
            launcher_stays_in_process_group=False,
        ),
    )
    monkeypatch.setattr("agent_team.bootstrap.get_adapter", lambda _adapter: adapter)
    team = make_team(
        run_id="at-worker-detaching-launcher",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "fresh",
                PROFILE,
                PROFILE_HASH,
                launch_mode="headless",
            )
        },
        initial_role="developer",
        max_turns=4,
        max_wall_time_seconds=300,
    )

    with pytest.raises(AgentTeamError) as rejected:
        initialize_run(
            team=team,
            request_path=request,
            protocol_path=protocol,
        )

    assert rejected.value.code == "HARNESS_PROCESS_MODEL_UNSUPPORTED"
    assert not (workspace / ".agent-team" / "runs" / team.run_id).exists()


def test_init_rejects_unauthenticated_external_harness(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, protocol = request_protocol
    adapter = _BootstrapAdapter()
    monkeypatch.setattr(
        adapter,
        "probe",
        lambda: SimpleNamespace(
            authenticated=False,
            launcher_stays_in_process_group=True,
        ),
    )
    monkeypatch.setattr("agent_team.bootstrap.get_adapter", lambda _adapter: adapter)
    team = make_team(
        run_id="at-worker-unauthenticated",
        workspace=workspace,
        origin_harness="codex",
        roles={
            "developer": Role(
                "developer",
                "external",
                "codex",
                "fresh",
                PROFILE,
                PROFILE_HASH,
            )
        },
        initial_role="developer",
        max_turns=4,
        max_wall_time_seconds=300,
    )

    with pytest.raises(AgentTeamError) as rejected:
        initialize_run(
            team=team,
            request_path=request,
            protocol_path=protocol,
        )

    assert rejected.value.code == "HARNESS_NOT_AUTHENTICATED"
    assert not (workspace / ".agent-team").exists()


def test_capability_allows_provider_route_without_harness_account_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _BootstrapAdapter()
    monkeypatch.setattr(
        adapter,
        "probe",
        lambda: SimpleNamespace(
            authenticated=False,
            launcher_stays_in_process_group=True,
        ),
    )
    monkeypatch.setattr(adapter, "authentication_required", lambda _options: False)
    monkeypatch.setattr("agent_team.bootstrap.get_adapter", lambda _adapter: adapter)
    role = Role(
        "developer",
        "external",
        "codex",
        "fresh",
        PROFILE,
        PROFILE_HASH,
        launch_mode="headless",
    )

    _assert_external_capability(role)


def test_start_freezes_capabilities_for_every_external_role_before_kickoff(
    workspace: Path,
    request_protocol: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, protocol = request_protocol
    adapter = _BootstrapAdapter()
    prepared: list[str] = []

    def prepare_capabilities(*, run_dir: Path, role_id: str, **_kwargs: object) -> None:
        assert not tuple((run_dir / "events").glob("*.json"))
        prepared.append(role_id)

    monkeypatch.setattr(adapter, "prepare_capability_state", prepare_capabilities)
    monkeypatch.setattr("agent_team.bootstrap.get_adapter", lambda _adapter: adapter)
    monkeypatch.setattr("agent_team.ownership.get_adapter", lambda _adapter: adapter)
    monkeypatch.setattr("agent_team.bootstrap.tmux_executable", lambda: "/bin/true")
    monkeypatch.setattr(
        "agent_team.bootstrap.ensure_workers",
        lambda _run_dir, _team, **_kwargs: {
            "session": "test-session",
            "created": [],
            "existing": ["developer"],
        },
    )
    monkeypatch.setattr("agent_team.bootstrap.signal_change", lambda *_args: False)
    team = make_team(
        run_id="at-worker-freeze-all-capabilities",
        workspace=workspace,
        origin_harness="codex",
        roles={
            role_id: Role(
                role_id,
                "external",
                "codex",
                "fresh",
                PROFILE,
                PROFILE_HASH,
                launch_mode="headless",
            )
            for role_id in ("developer", "reviewer")
        },
        initial_role="developer",
        max_turns=4,
        max_wall_time_seconds=300,
    )
    run_dir = initialize_run(
        team=team,
        request_path=request,
        protocol_path=protocol,
    )

    started = start_run(run_dir)

    assert prepared == ["developer", "reviewer"]
    assert started["kickoff_event"]["event_type"] == "kickoff"

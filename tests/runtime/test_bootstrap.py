from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_team.bootstrap import _assert_external_capability, initialize_run
from agent_team.config import (
    Role,
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

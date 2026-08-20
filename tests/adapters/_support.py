from __future__ import annotations

import json

from agent_team.adapters.base import StreamRecord, TurnLaunchContext


def record(value: dict) -> StreamRecord:
    return StreamRecord(
        source="stdout",
        first_seq=1,
        last_seq=1,
        observed_at="2026-07-28T00:00:00.000Z",
        encoding="utf-8",
        data=json.dumps(value) + "\n",
    )


def launch_context(
    *,
    adapter,
    session_policy: str,
    session_ref: str | None,
    profile: str = "default",
    model: str | None = None,
    reasoning_effort: str | None = None,
    fast_mode: bool | None = None,
    model_provider: str | None = None,
    model_provider_config: dict[str, object] | None = None,
    launch_mode: str = "headless",
    workspace: str = "/tmp/workspace",
    turn_dir: str = ("/tmp/workspace/.agent-team/runs/at-adapter-test/turns/turn-0001"),
) -> TurnLaunchContext:
    profile_hash = adapter.profile_fingerprint(
        profile,
        session_policy,
        launch_mode,
    )
    return TurnLaunchContext(
        run_id="at-adapter-test",
        role_id="developer",
        turn_id="turn-0001",
        workspace=workspace,
        turn_dir=turn_dir,
        prompt="perform the turn",
        session_policy=session_policy,
        session_ref=session_ref,
        session_generation=1,
        launch_profile=profile,
        launch_profile_sha256=profile_hash,
        agent_team_cli="/usr/local/bin/agent-team",
        model=model,
        reasoning_effort=reasoning_effort,
        fast_mode=fast_mode,
        launch_mode=launch_mode,
        model_provider=model_provider,
        model_provider_config=model_provider_config,
    )

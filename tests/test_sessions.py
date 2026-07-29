from __future__ import annotations

from pathlib import Path

import pytest

from agent_team.config import Role
from agent_team.errors import IntegrityError
from agent_team.turns import (
    commit_session,
    load_session,
    mark_session_unavailable,
    session_launch_state,
)


def _role() -> Role:
    return Role(
        "developer",
        "external",
        "claude-code",
        "resume",
        "default",
        "a" * 64,
    )


def test_valid_session_unavailable_advances_only_on_next_generation(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "sessions").mkdir(parents=True)
    role = _role()
    first_runtime = {
        "turn_id": "turn-0001",
        "role_id": role.role_id,
        "session_generation": 1,
    }
    commit_session(
        run_dir,
        role=role,
        runtime=first_runtime,
        session_ref="session-one",
    )
    resume_runtime = {
        "turn_id": "turn-0002",
        "role_id": role.role_id,
        "session_generation": 1,
    }

    unavailable = mark_session_unavailable(
        run_dir,
        role=role,
        runtime=resume_runtime,
        reason="session_not_found",
    )

    assert unavailable["status"] == "unavailable"
    assert unavailable["session_ref"] is None
    assert unavailable["created_turn_id"] == "turn-0001"
    assert unavailable["updated_turn_id"] == "turn-0002"
    assert unavailable["unavailable_reason"] == "session_not_found"
    assert mark_session_unavailable(
        run_dir,
        role=role,
        runtime=resume_runtime,
        reason="session_not_found",
    ) == unavailable
    assert session_launch_state(run_dir, role) == (2, None)

    with pytest.raises(IntegrityError, match="cannot be revived"):
        commit_session(
            run_dir,
            role=role,
            runtime=resume_runtime,
            session_ref="silently-revived",
        )

    fresh_runtime = {
        "turn_id": "turn-0003",
        "role_id": role.role_id,
        "session_generation": 2,
    }
    available = commit_session(
        run_dir,
        role=role,
        runtime=fresh_runtime,
        session_ref="session-two",
    )
    assert available["status"] == "available"
    assert available["generation"] == 2
    assert available["created_turn_id"] == "turn-0003"
    assert load_session(run_dir, role) == available


def test_unavailable_requires_existing_valid_session(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "sessions").mkdir(parents=True)

    with pytest.raises(IntegrityError, match="validated Session snapshot"):
        mark_session_unavailable(
            run_dir,
            role=_role(),
            runtime={
                "turn_id": "turn-0001",
                "role_id": "developer",
                "session_generation": 1,
            },
            reason="session_not_found",
        )


def test_resume_session_remains_stable_across_five_later_turns(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "sessions").mkdir(parents=True)
    role = _role()
    session_ref = "session-stable-across-six-turns"

    for turn_number in range(1, 7):
        generation, launch_ref = session_launch_state(run_dir, role)
        assert generation == 1
        assert launch_ref == (None if turn_number == 1 else session_ref)
        runtime = {
            "turn_id": f"turn-{turn_number:04d}",
            "role_id": role.role_id,
            "session_generation": generation,
        }
        committed = commit_session(
            run_dir,
            role=role,
            runtime=runtime,
            session_ref=session_ref,
        )
        assert committed["generation"] == 1
        assert committed["session_ref"] == session_ref
        assert committed["created_turn_id"] == "turn-0001"
        assert committed["updated_turn_id"] == runtime["turn_id"]

    assert session_launch_state(run_dir, role) == (1, session_ref)

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_team.turns import RUNTIME_REQUIRED

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLAIM_GUIDES = (
    "README.md",
    "agent-team_technical_design_v0.1.md",
    "skills/codex/agent-team/SKILL.md",
    "plugins/claude-code/agent-team/skills/agent-team/SKILL.md",
)


@pytest.mark.parametrize("relative_path", CLAIM_GUIDES)
def test_claim_guidance_never_splits_option_from_opaque_value(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert re.search(r"--claim(?=\s)", content) is None


def test_technical_design_runtime_example_matches_closed_schema() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )
    section = design.split("## 13.3 Worker 与 Turn Runtime", 1)[1]
    json_block = section.split("```json", 1)[1].split("```", 1)[0]
    runtime = json.loads(json_block)

    assert set(runtime) == RUNTIME_REQUIRED


def test_technical_design_status_role_examples_match_closed_schema() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )
    section = design.split("## 27.1 结构化输出合同", 1)[1]
    json_block = section.split("```json", 1)[1].split("```", 1)[0]
    roles = json.loads(json_block)["data"]["roles"]
    expected = {
        "role_id",
        "binding",
        "adapter",
        "session_policy",
        "launch_mode",
        "launch_profile",
        "launch_profile_sha256",
        "model",
        "reasoning_effort",
        "fast_mode",
        "state",
        "worker_pid",
        "worker_start_id",
        "tmux_session",
        "tmux_pane_id",
        "session_status",
        "session_generation",
        "session_ref",
        "session_unavailable_reason",
    }

    assert roles
    assert all(set(role) == expected for role in roles)

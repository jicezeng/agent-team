from __future__ import annotations

import re
from pathlib import Path

import pytest

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

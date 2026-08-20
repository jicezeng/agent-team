from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import pytest

from agent_team.cli import build_parser
from agent_team.turns import RUNTIME_REQUIRED

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CLAIM_GUIDES = (
    "README.md",
    "docs/user-guide.md",
    "agent-team_technical_design_v0.1.md",
    "skills/codex/agent-team/SKILL.md",
    "plugins/claude-code/agent-team/skills/agent-team/SKILL.md",
    "skills/opencode/agent-team/SKILL.md",
)
PROFILE_GUIDES = (
    "skills/codex/agent-team/SKILL.md",
    "skills/codex/agent-team/references/protocol-template.md",
    "plugins/claude-code/agent-team/skills/agent-team/SKILL.md",
    "plugins/claude-code/agent-team/skills/agent-team/references/protocol-template.md",
    "skills/opencode/agent-team/SKILL.md",
    "skills/opencode/agent-team/references/protocol-template.md",
)
ORIGIN_SKILLS = (
    "skills/codex/agent-team/SKILL.md",
    "plugins/claude-code/agent-team/skills/agent-team/SKILL.md",
    "skills/opencode/agent-team/SKILL.md",
)


def _strict_json(value: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise AssertionError(f"documented JSON contains duplicate key: {key}")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=reject_duplicate_keys)


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
    runtime = _strict_json(json_block)

    assert set(runtime) == RUNTIME_REQUIRED


def test_technical_design_status_role_examples_match_closed_schema() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )
    section = design.split("## 27.1 结构化输出合同", 1)[1]
    json_block = section.split("```json", 1)[1].split("```", 1)[0]
    roles = _strict_json(json_block)["data"]["roles"]
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


def test_technical_design_json_examples_are_strict_json() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )
    blocks = re.findall(r"```json\s*\n(.*?)```", design, flags=re.DOTALL)

    assert blocks
    for block in blocks:
        _strict_json(block)


def test_current_product_docs_acknowledge_interactive_claude_acceptance() -> None:
    prd = (REPOSITORY_ROOT / "agent-team_prd_v0.1.md").read_text(encoding="utf-8")
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )

    assert "235 项回归验证" not in prd
    assert "尚未保存真实 Interactive Claude Code 闭环报告" not in prd
    assert "Claude Code → Codex → 同一 Claude Session 恢复" in prd
    assert "混合 Interactive Claude Code/Codex" in design


def test_technical_design_does_not_present_an_internal_claim_command() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )

    assert "`claim`" not in design
    assert "agent-team claim" not in design


def test_technical_design_mentions_every_public_cli_command_and_option() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )
    parser = build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    assert "agent-team --version" in design
    for command, command_parser in subparsers.choices.items():
        if command.startswith("_"):
            continue
        assert f"agent-team {command}" in design
        for action in command_parser._actions:
            for option in action.option_strings:
                if option not in {"-h", "--help"}:
                    assert option in design, f"{command} option missing: {option}"


def test_technical_design_lists_mode_specific_process_artifacts() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )
    shared_directory = design.split("## 22. 共享目录设计", 1)[1].split(
        "### 22.1 Git 处理",
        1,
    )[0]
    technical_snapshots = design.split("### 22.3 技术快照", 1)[1].split(
        "### 22.4 Workspace Ownership",
        1,
    )[0]

    assert "prompt.md" in shared_directory
    assert "exec-error.json" in shared_directory
    assert "process/launch.json" in technical_snapshots
    assert "process/prompt.md" in technical_snapshots


def test_technical_design_treats_missing_committed_turn_input_as_corruption() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )

    assert "可从仍通过 Hash 校验的 Event Payload 完成首次创建" not in design
    assert "最终 Turn" in design
    assert "不得从 Event Payload 重新生成" in design


def test_technical_design_documents_audit_integrity_error_exit() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )
    observation_contract = design.split("## 27.1 结构化输出合同", 1)[1].split(
        "## 27.2 `agent-team status`",
        1,
    )[0]

    assert "`transcript` / `tail`" in observation_contract
    assert "`TEAM_CORRUPTED`" in observation_contract
    assert "`1` 只表示" in observation_contract


def test_technical_design_does_not_overstate_adapter_probe() -> None:
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )

    assert "Probe 不启动真实模型" in design
    assert "不动态证明 CLI 已接受参数" in design
    assert "该版本 Probe 证明可用" not in design


@pytest.mark.parametrize(
    "relative_path",
    ("docs/user-guide.md", "agent-team_technical_design_v0.1.md"),
)
def test_operator_docs_list_the_complete_external_turn_environment(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
    expected = {
        "AGENT_TEAM_RUN_ID",
        "AGENT_TEAM_ROLE_ID",
        "AGENT_TEAM_TURN_ID",
        "AGENT_TEAM_RUN_DIR",
        "AGENT_TEAM_TURN_DIR",
        "AGENT_TEAM_CLI",
    }

    assert all(name in content for name in expected)


@pytest.mark.parametrize(
    "relative_path",
    (
        "README.md",
        "agent-team_prd_v0.1.md",
        "agent-team_technical_design_v0.1.md",
    ),
)
def test_product_docs_disclose_managed_harness_policy_boundary(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert "Managed" in content
    assert "features.hooks=false" in content
    assert "launch_profile_sha256" in content
    assert "doctor" in content


@pytest.mark.parametrize(
    "relative_path",
    (
        "README.md",
        "agent-team_prd_v0.1.md",
        "agent-team_technical_design_v0.1.md",
    ),
)
def test_product_docs_define_full_access_default_and_start_confirmation(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert "full-access" in content
    assert "--confirm-full-access" in content
    assert "一次" in content or "once" in content


def test_readme_stays_focused_and_links_to_the_user_guide() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert len(readme.splitlines()) <= 200
    assert "docs/user-guide.md" in readme


def test_product_docs_define_bidirectional_deepseek_harness_integration() -> None:
    prd = (REPOSITORY_ROOT / "agent-team_prd_v0.1.md").read_text(encoding="utf-8")
    design = (REPOSITORY_ROOT / "agent-team_technical_design_v0.1.md").read_text(
        encoding="utf-8"
    )
    guide = (REPOSITORY_ROOT / "docs/user-guide.md").read_text(encoding="utf-8")

    for content in (prd, design, guide):
        assert "DeepSeek Harness" in content
        assert "Origin" in content
        assert "External" in content
        assert "interactive" in content.lower()
    assert "不是 External Adapter" not in prd
    assert "不新增 External Adapter" not in design
    assert "DSH is an Origin integration only" not in guide
    assert "`deepseek-harness` 四类 External Binding" in design
    assert "DSH External roles support only `interactive`" in guide
    assert "integration:deepseek_harness_skill" in design


def test_deepseek_design_freezes_managed_interactive_contract() -> None:
    content = (
        REPOSITORY_ROOT / "docs/deepseek-harness-integration-design.md"
    ).read_text(encoding="utf-8")

    assert "@deepseek-ai/dsh@0.1.0-rc.6" in content
    assert "agents.create" in content
    assert "agents.resume" in content
    assert "interactive-only" in content.lower()
    assert "Python SDK Bridge" in content
    assert "private reasoning text" in content
    assert "agent_team_cli" in content


def test_shared_codex_dsh_skill_selects_explicit_origin_metadata() -> None:
    content = (REPOSITORY_ROOT / ORIGIN_SKILLS[0]).read_text(encoding="utf-8")

    assert 'if [ "${DSH_SHELL:-}" = "1" ]' in content
    assert "origin_harness=deepseek-harness" in content
    assert "origin_harness=codex" in content
    assert '--origin-harness "$origin_harness"' in content
    assert "This branch records Origin metadata only and grants no permission." in content


@pytest.mark.parametrize(
    ("relative_path", "origin_harness"),
    (
        (
            "plugins/claude-code/agent-team/skills/agent-team/SKILL.md",
            "claude-code",
        ),
        ("skills/opencode/agent-team/SKILL.md", "opencode"),
    ),
)
def test_harness_specific_skills_pass_explicit_origin_metadata(
    relative_path: str,
    origin_harness: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert f"--origin-harness {origin_harness}" in content


@pytest.mark.parametrize("relative_path", ORIGIN_SKILLS)
def test_origin_skills_reuse_one_absolute_cli_path(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert "canonical absolute executable path" in content
    assert "do not re-resolve it" in content
    assert '"<absolute-agent-team-cli>" init' in content
    assert '"<absolute-agent-team-cli>" start' in content
    assert '"<absolute-agent-team-cli>" wait-origin' in content


@pytest.mark.parametrize("relative_path", ORIGIN_SKILLS)
def test_origin_skills_keep_full_access_consent_before_init(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
    consent = content.index("obtain one explicit")
    initialization = content.index('"<absolute-agent-team-cli>" init')

    assert consent < initialization
    assert "If the user declines, do not create or start the Run." in content
    assert "Omit `--confirm-full-access`" in content


@pytest.mark.parametrize("relative_path", PROFILE_GUIDES)
def test_profile_guides_require_one_confirmation_for_default_full_access(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert "defaults to `full-access`" in content or re.search(
        r"By\s+default",
        content,
    )
    assert "--confirm-full-access" in content or "one-time confirmation" in content


@pytest.mark.parametrize("relative_path", PROFILE_GUIDES)
def test_profile_guides_disclose_administrator_policy_boundary(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert "administrator" in content
    assert "Doctor" in content
    assert "Profile" in content

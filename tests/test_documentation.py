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
    "agent-team_technical_design_v0.1.md",
    "skills/codex/agent-team/SKILL.md",
    "plugins/claude-code/agent-team/skills/agent-team/SKILL.md",
)
PROFILE_GUIDES = (
    "skills/codex/agent-team/SKILL.md",
    "skills/codex/agent-team/references/protocol-template.md",
    "plugins/claude-code/agent-team/skills/agent-team/SKILL.md",
    "plugins/claude-code/agent-team/skills/agent-team/references/protocol-template.md",
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
    ("README.md", "agent-team_technical_design_v0.1.md"),
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


@pytest.mark.parametrize("relative_path", PROFILE_GUIDES)
def test_profile_guides_disclose_administrator_policy_boundary(
    relative_path: str,
) -> None:
    content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

    assert "administrator" in content
    assert "Doctor" in content
    assert "Profile" in content

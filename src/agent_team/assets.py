from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .errors import AgentTeamError
from .state import account_home, fixed_state_dir


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def bundled_asset(relative: str) -> Path:
    candidates = (
        _repository_root() / relative,
        Path(__file__).resolve().parent / "bundled" / relative,
    )
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir() and not candidate.is_symlink():
            return candidate.resolve(strict=True)
    raise AgentTeamError(
        "INTEGRATION_ASSET_MISSING",
        f"bundled integration asset is unavailable: {relative}",
    )


def codex_skill_source() -> Path:
    return bundled_asset("skills/codex/agent-team")


def claude_plugin_source() -> Path:
    return bundled_asset("plugins/claude-code/agent-team")


def opencode_skill_source() -> Path:
    return bundled_asset("skills/opencode/agent-team")


def installed_claude_plugin() -> Path:
    return fixed_state_dir() / "installed" / "claude-code-plugin"


def installed_codex_skill() -> Path:
    return account_home() / ".codex" / "skills" / "agent-team"


def installed_opencode_skill() -> Path:
    return account_home() / ".config" / "opencode" / "skills" / "agent-team"


def effective_claude_plugin() -> Path:
    installed = installed_claude_plugin()
    if installed.exists() and installed.is_dir() and not installed.is_symlink():
        return installed.resolve(strict=True)
    return claude_plugin_source()


def effective_agent_team_cli() -> Path:
    # Worker processes are launched with the exact interpreter that started
    # the Run. Prefer its sibling console script so a tmux server's inherited
    # PATH cannot change the absolute command embedded in launch profiles.
    candidate = Path(sys.executable).parent / "agent-team"
    if candidate.exists() and candidate.is_file():
        return candidate.resolve(strict=True)
    located = shutil.which("agent-team")
    if located:
        return Path(located).resolve(strict=True)
    raise AgentTeamError(
        "AGENT_TEAM_CLI_NOT_FOUND",
        "cannot locate the agent-team console script",
    )

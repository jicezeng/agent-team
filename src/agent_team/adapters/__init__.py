from __future__ import annotations

from .base import HarnessAdapter
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .opencode import OpenCodeAdapter


def get_adapter(adapter_id: str) -> HarnessAdapter:
    if adapter_id == "codex":
        return CodexAdapter()
    if adapter_id == "claude-code":
        return ClaudeCodeAdapter()
    if adapter_id == "opencode":
        return OpenCodeAdapter()
    raise ValueError(f"unsupported adapter: {adapter_id}")


__all__ = [
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "HarnessAdapter",
    "OpenCodeAdapter",
    "get_adapter",
]

from __future__ import annotations

from .base import HarnessAdapter
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter


def get_adapter(adapter_id: str) -> HarnessAdapter:
    if adapter_id == "codex":
        return CodexAdapter()
    if adapter_id == "claude-code":
        return ClaudeCodeAdapter()
    raise ValueError(f"unsupported adapter: {adapter_id}")


__all__ = [
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "HarnessAdapter",
    "get_adapter",
]

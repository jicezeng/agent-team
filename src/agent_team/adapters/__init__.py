from __future__ import annotations

from .base import HarnessAdapter
from .claude_code import ClaudeCodeAdapter
from .codex import CodexAdapter
from .deepseek_harness import DeepSeekHarnessAdapter
from .opencode import OpenCodeAdapter


def get_adapter(adapter_id: str) -> HarnessAdapter:
    if adapter_id == "codex":
        return CodexAdapter()
    if adapter_id == "claude-code":
        return ClaudeCodeAdapter()
    if adapter_id == "opencode":
        return OpenCodeAdapter()
    if adapter_id == "deepseek-harness":
        return DeepSeekHarnessAdapter()
    raise ValueError(f"unsupported adapter: {adapter_id}")


__all__ = [
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "DeepSeekHarnessAdapter",
    "HarnessAdapter",
    "OpenCodeAdapter",
    "get_adapter",
]

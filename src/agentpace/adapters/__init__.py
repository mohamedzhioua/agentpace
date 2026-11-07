"""Registered harness adapters."""

from __future__ import annotations

from agentpace.adapters.base import Adapter
from agentpace.adapters.claude_code import ClaudeCodeAdapter
from agentpace.adapters.codex import CodexAdapter

ADAPTERS: dict[str, Adapter] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter(),
    CodexAdapter.name: CodexAdapter(),
}


def get_adapter(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown source {name!r}; available: {sorted(ADAPTERS)}") from None

"""Registered harness adapters."""

from __future__ import annotations

from agentpace.adapters.base import Adapter
from agentpace.adapters.claude_code import ClaudeCodeAdapter
from agentpace.adapters.cline import ClineAdapter
from agentpace.adapters.codex import CodexAdapter
from agentpace.adapters.gemini_cli import GeminiCliAdapter
from agentpace.adapters.opencode import OpenCodeAdapter

ADAPTERS: dict[str, Adapter] = {
    ClaudeCodeAdapter.name: ClaudeCodeAdapter(),
    CodexAdapter.name: CodexAdapter(),
    GeminiCliAdapter.name: GeminiCliAdapter(),
    OpenCodeAdapter.name: OpenCodeAdapter(),
    ClineAdapter.name: ClineAdapter(),
}


def get_adapter(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown source {name!r}; available: {sorted(ADAPTERS)}") from None

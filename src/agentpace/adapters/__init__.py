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

# Built from each tool's source and shipped without a check against real sessions, by the owner's
# decision for 0.8.0. `agentpace ingest` says so on stderr when one of these is the source.
EXPERIMENTAL: frozenset[str] = frozenset(
    {GeminiCliAdapter.name, OpenCodeAdapter.name, ClineAdapter.name}
)


def get_adapter(name: str) -> Adapter:
    try:
        return ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown source {name!r}; available: {sorted(ADAPTERS)}") from None

"""The adapter interface: how a harness's history becomes `RawRun` records.

An adapter finds runs and extracts the allowed metrics. Everything after that, such as
normalizing models, classifying and pseudonymizing, happens once for every harness. `RawRun`
has no slot for content, so content has no way out of an adapter.

To add a harness, write one module with a class that satisfies `Adapter`, register it in
`agentpace.adapters`, and add its name to the `harness` enum in the schema.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Source:
    """A location holding one run. `session` and `project` are hash inputs, never stored."""

    path: str = field(repr=False)
    session: str = field(repr=False)
    project: str = field(repr=False)


@dataclass(frozen=True)
class RawRun:
    """The only facts an adapter may report. Identifying fields stay out of `repr`."""

    harness: str
    model: str
    start: float
    end: float
    spans: tuple[tuple[float, float], ...]
    active_s: int
    tool_calls: int
    edit_calls: int
    tokens: int | None
    errors: int
    outcome: str
    agent_type: str | None = field(repr=False, default=None)
    session: str = field(repr=False, default="")
    project: str = field(repr=False, default="")


@dataclass
class ParseStats:
    """What an adapter could not use. Reported to the user, so nothing is lost silently."""

    unreadable_files: int = 0
    empty_runs: int = 0
    unparsed_lines: int = 0


class Adapter(Protocol):
    name: str

    def default_path(self) -> Path: ...

    def discover(self, path: Path) -> Iterator[Source]: ...

    def parse(self, source: Source, idle_cap_s: int, stats: ParseStats) -> RawRun | None: ...

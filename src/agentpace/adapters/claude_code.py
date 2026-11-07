"""Claude Code subagent transcripts.

Layout: `<root>/<project>/<session>/subagents/**/agent-*.jsonl`, with a sibling `.meta.json`.
The format has no published schema, so unknown record types are skipped, never fatal.

Privacy boundary: JSON has to be decoded a line at a time, so content passes through memory
there. `_project` copies the few allowed values out of each parsed line, and nothing else in
this module ever sees that line. Parse errors are counted and their messages discarded, because
a decoder message can quote the text it failed on.
"""

from __future__ import annotations

import itertools
import json
import os
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agentpace import schema
from agentpace.adapters.base import ParseStats, RawRun, Source

_EFFORTS = frozenset(schema.enum("effort"))
_EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "NotebookEdit", "Write"})
_TERMINAL_TOOLS = frozenset({"StructuredOutput", "SubagentHandback"})
_CLEAN_STOPS = frozenset({"end_turn", "stop_sequence"})
_SYNTHETIC_MODEL = "<synthetic>"


@dataclass(frozen=True)
class _Line:
    """The allowed view of one transcript line."""

    role: str
    time: float | None
    model: str | None
    message_id: str | None
    tokens: int
    stop_reason: str | None
    tools: tuple[str, ...]
    results: int
    last_result_errored: bool
    errors: int


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _parse_time(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        # Python 3.10 rejects a trailing Z.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _project(record: Any) -> _Line | None:
    """Copy allowed values out of a parsed line. The only code that touches a parsed line."""
    if not isinstance(record, dict):
        return None
    role = record.get("type")
    if role not in ("assistant", "user"):
        return None
    message = record.get("message")
    time = _parse_time(record.get("timestamp"))
    if not isinstance(message, dict):
        return _Line(role, time, None, None, 0, None, (), 0, False, 0)
    usage = message.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    tools: list[str] = []
    results = errors = 0
    last_errored = False
    content = message.get("content")
    for block in content if isinstance(content, list) else ():
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        name = block.get("name")
        if kind == "tool_use" and isinstance(name, str):
            tools.append(name)
        elif kind == "tool_result":
            results += 1
            last_errored = block.get("is_error") is True
            errors += last_errored
    model = message.get("model")
    message_id = message.get("id")
    stop_reason = message.get("stop_reason")
    return _Line(
        role=role,
        time=time,
        model=model if role == "assistant" and isinstance(model, str) else None,
        message_id=message_id if isinstance(message_id, str) else None,
        tokens=_as_int(usage.get("input_tokens")) + _as_int(usage.get("output_tokens")),
        stop_reason=stop_reason if isinstance(stop_reason, str) else None,
        tools=tuple(tools),
        results=results,
        last_result_errored=last_errored,
        errors=errors,
    )


def _meta_path(transcript: str) -> str:
    return transcript[: -len(".jsonl")] + ".meta.json"


def _read_meta(path: str) -> tuple[str | None, bool, str | None]:
    """Return only `agentType`, `finished` and `effort`. `description` and the rest stay unread.

    `effort` survives only as one of the schema's values. Any other string is dropped here, so it
    never reaches a row, the cache or a message.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, ValueError):
        return None, False, None
    if not isinstance(meta, dict):
        return None, False, None
    agent_type = meta.get("agentType")
    effort = meta.get("effort")
    return (
        agent_type if isinstance(agent_type, str) else None,
        meta.get("finished") is True,
        effort if isinstance(effort, str) and effort in _EFFORTS else None,
    )


def _outcome(
    stop_reason: str | None, finished: bool, last_tool: str | None, last_errored: bool
) -> str:
    if stop_reason == "refusal":
        return "failed"
    if finished or last_tool in _TERMINAL_TOOLS or stop_reason in _CLEAN_STOPS:
        return "completed"
    return "failed" if last_errored else "unknown"


def _active(times: list[float], idle_cap_s: int) -> tuple[int, tuple[tuple[float, float], ...]]:
    """Clip idle gaps, and split the run into spans wherever a gap exceeded the cap."""
    active = 0.0
    spans: list[tuple[float, float]] = []
    span_start = times[0]
    for before, after in itertools.pairwise(times):
        gap = after - before
        active += min(gap, idle_cap_s)
        if gap > idle_cap_s:
            spans.append((span_start, before))
            span_start = after
    spans.append((span_start, times[-1]))
    return round(active), tuple(spans)


def _extended(path: Path) -> str:
    """Use the extended prefix on Windows, where transcripts exceed the 260-character limit."""
    absolute = os.path.abspath(path)
    if os.name == "nt" and not absolute.startswith("\\\\?\\"):
        return "\\\\?\\" + absolute
    return absolute


class ClaudeCodeAdapter:
    name = "claude-code"
    # Bump whenever `parse` would return something different for the same file, so cached
    # runs from the old logic are discarded.
    version = 1

    def default_path(self) -> Path:
        return Path.home() / ".claude" / "projects"

    def discover(self, path: Path) -> Iterator[Source]:
        root = _extended(path)
        listing: Iterable[tuple[str, list[str], list[str]]]
        if os.path.isfile(root):
            listing = [(os.path.dirname(root), [], [os.path.basename(root)])]
        else:
            listing = os.walk(root)
        for directory, _, files in listing:
            parts = directory.replace("\\", "/").split("/")
            if "subagents" not in parts:
                continue
            anchor = parts.index("subagents")
            if anchor < 2:
                continue
            for file in sorted(files):
                if file.startswith("agent-") and file.endswith(".jsonl"):
                    transcript = os.path.join(directory, file)
                    yield Source(
                        path=transcript,
                        session=parts[anchor - 1],
                        project=parts[anchor - 2],
                        companions=(_meta_path(transcript),),
                    )

    def parse(self, source: Source, idle_cap_s: int, stats: ParseStats) -> RawRun | None:
        times: list[float] = []
        models: Counter[str] = Counter()
        tokens_by_message: dict[str, int] = {}
        tool_calls = edit_calls = errors = 0
        last_tool: str | None = None
        last_stop: str | None = None
        last_errored = False
        try:
            with open(source.path, encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    # Cheap skip of the large request-log lines, which are never needed.
                    if '"assistant"' not in raw and '"user"' not in raw:
                        continue
                    try:
                        line = _project(json.loads(raw))
                    except ValueError:
                        stats.unparsed_lines += 1
                        continue
                    if line is None:
                        continue
                    if line.time is not None:
                        times.append(line.time)
                    if line.model and line.model != _SYNTHETIC_MODEL:
                        models[line.model] += 1
                    if line.role == "assistant" and line.message_id:
                        # One message repeats its usage on every content block.
                        seen = tokens_by_message.get(line.message_id, 0)
                        tokens_by_message[line.message_id] = max(seen, line.tokens)
                    if line.stop_reason:
                        last_stop = line.stop_reason
                    tool_calls += len(line.tools)
                    edit_calls += sum(tool in _EDIT_TOOLS for tool in line.tools)
                    if line.tools:
                        last_tool = line.tools[-1]
                    if line.results:
                        last_errored = line.last_result_errored
                    errors += line.errors
        except OSError:
            stats.unreadable_files += 1
            return None
        if len(times) < 2 or not models:
            stats.empty_runs += 1
            return None
        times.sort()
        active_s, spans = _active(times, idle_cap_s)
        agent_type, finished, effort = _read_meta(_meta_path(source.path))
        return RawRun(
            harness=self.name,
            model=models.most_common(1)[0][0],
            start=times[0],
            end=times[-1],
            spans=spans,
            active_s=active_s,
            tool_calls=tool_calls,
            edit_calls=edit_calls,
            tokens=sum(tokens_by_message.values()) or None,
            errors=errors,
            outcome=_outcome(last_stop, finished, last_tool, last_errored),
            effort=effort,
            agent_type=agent_type,
            session=source.session,
            project=source.project,
        )

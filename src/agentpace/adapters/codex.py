"""The Codex adapter: one agent turn is one run, and one rollout file is one batch.

Layout: `~/.codex/sessions/<year>/<month>/<day>/rollout-*.jsonl`. Every line is
`{timestamp, type, payload}`. A turn opens with an `event_msg` of type `task_started` and closes
with `task_complete` or `turn_aborted` carrying the same `turn_id`.

Privacy: `_project` is the only code that touches a parsed line, and it copies an allowlist of
keys. Session metadata yields the session id, the working folder (a hash input only, which sits in
memory and in the local cache as Claude Code folder names do, never in a row), whether the file is
a fork, and the session source reduced to a fixed word. Turn context yields the turn id, model and
an effort from the schema enum. Events yield turn ids, epoch times, an error flag, an abort reason
from a fixed set, and the type and completed/failed status of finished items. Response items yield
their type only, and token records two counters. Prompts, messages, reasoning, commands, patches,
tool arguments and outputs, instructions, git data and agent names are never copied.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentpace import schema
from agentpace.adapters.base import ParseStats, RawRun, Source
from agentpace.adapters.claude_code import _active, _as_int, _extended, _parse_time

_EFFORTS = frozenset(schema.enum("effort"))
_SOURCES = frozenset(
    {"cli", "vscode", "exec", "mcp", "custom", "review", "compact", "memory_consolidation"}
)
_SKIPPED_SOURCES = frozenset({"compact", "memory_consolidation"})  # upkeep, not agent work
_ABORT_REASONS = frozenset({"interrupted", "replaced", "review_ended", "budget_limited"})
_TOOL_ITEMS = frozenset({"function_call", "custom_tool_call", "local_shell_call"})
_STATUS_ITEMS = frozenset({"CommandExecution", "FileChange", "McpToolCall", "Extension"})
_REVIEW_MODEL = "codex-auto-review"


@dataclass(frozen=True)
class _Line:
    kind: str
    time: float | None
    turn_id: str | None = None
    epoch: float | None = None  # started_at or completed_at, when the line has one
    model: str | None = None
    effort: str | None = None
    failed: bool = False  # a task_complete error, or an item whose status is "failed"
    reason: str | None = None
    item: str | None = None
    tokens: int = 0
    session_id: str | None = None
    folder: str | None = None
    forked: bool = False
    source: str | None = None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _epoch(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _reduce_source(value: Any) -> str | None:
    """A fixed word for where the session came from. Only key names are read from a dict."""
    if isinstance(value, str):
        return value if value in _SOURCES else None
    if isinstance(value, dict) and set(value) == {"subagent"}:
        inner = value["subagent"]
        if isinstance(inner, dict) and "thread_spawn" in inner:
            return "thread_spawn"
        return "other"
    return None


def _project(record: Any) -> _Line | None:
    """Copy allowed values out of one parsed line. The only code that touches a parsed line."""
    if not isinstance(record, dict):
        return None
    kind = record.get("type")
    payload = record.get("payload")
    if not isinstance(kind, str) or not isinstance(payload, dict):
        return None
    time = _parse_time(record.get("timestamp"))
    if kind == "session_meta":
        return _Line(
            "session_meta",
            time,
            session_id=_text(payload.get("id")),
            folder=_text(payload.get("cwd")),
            forked=payload.get("forked_from_id") is not None,
            source=_reduce_source(payload.get("source")),
        )
    if kind == "turn_context":
        effort = payload.get("effort")
        return _Line(
            "turn_context",
            time,
            turn_id=_text(payload.get("turn_id")),
            model=_text(payload.get("model")),
            effort=effort if isinstance(effort, str) and effort in _EFFORTS else None,
        )
    if kind == "token_usage_record":
        usage = payload.get("turn_token_usage")
        usage = usage if isinstance(usage, dict) else {}
        return _Line(
            "tokens",
            time,
            turn_id=_text(payload.get("turn_id")),
            tokens=_as_int(usage.get("input_tokens")) + _as_int(usage.get("output_tokens")),
        )
    if kind == "response_item":
        return _Line("tool" if payload.get("type") in _TOOL_ITEMS else "line", time)
    if kind != "event_msg":
        return _Line("line", time)
    event = payload.get("type")
    if event in ("task_started", "turn_started"):
        return _Line(
            "start",
            time,
            turn_id=_text(payload.get("turn_id")),
            epoch=_epoch(payload.get("started_at")),
        )
    if event in ("task_complete", "turn_complete"):
        return _Line(
            "complete",
            time,
            turn_id=_text(payload.get("turn_id")),
            epoch=_epoch(payload.get("completed_at")),
            failed=payload.get("error") is not None,
        )
    if event == "turn_aborted":
        reason = payload.get("reason")
        return _Line(
            "abort",
            time,
            turn_id=_text(payload.get("turn_id")),
            epoch=_epoch(payload.get("completed_at")),
            reason=reason if isinstance(reason, str) and reason in _ABORT_REASONS else None,
        )
    if event == "item_completed":
        item = payload.get("item")
        item = item if isinstance(item, dict) else {}
        item_type = item.get("type")
        status = item.get("status")
        return _Line(
            "item",
            time,
            item=item_type if item_type in _STATUS_ITEMS else None,
            failed=item_type in _STATUS_ITEMS and status == "failed",
        )
    return _Line("line", time)


@dataclass
class _Turn:
    turn_id: str | None
    start: float | None
    times: list[float] = field(default_factory=list)
    tool_calls: int = 0
    edit_calls: int = 0
    errors: int = 0
    tokens: int = 0
    end: float | None = None
    outcome: str = "unknown"


def _turns(lines: Iterable[_Line]) -> Iterator[_Turn]:
    """Group lines into turns by position, closing each on its own complete or abort."""
    turn: _Turn | None = None
    for line in lines:
        if line.kind == "start":
            if turn is not None:
                yield turn  # a new turn began before this one closed: outcome stays unknown
            turn = _Turn(line.turn_id, line.epoch if line.epoch is not None else line.time)
        if turn is None:
            continue
        if line.time is not None:
            turn.times.append(line.time)
        if line.kind == "tool":
            turn.tool_calls += 1
        elif line.kind == "item":
            turn.edit_calls += line.item == "FileChange"
            turn.errors += line.failed
        elif line.kind == "tokens" and line.turn_id is None:
            turn.tokens = max(turn.tokens, line.tokens)
        elif line.kind in ("complete", "abort"):
            if line.turn_id is not None and line.turn_id != turn.turn_id:
                continue  # it closes some other turn, never the open one
            turn.end = line.epoch if line.epoch is not None else line.time
            if line.kind == "complete":
                turn.outcome = "failed" if line.failed else "completed"
            yield turn
            turn = None
    if turn is not None:
        yield turn


class CodexAdapter:
    name = "codex"
    # Bump whenever `parse` would return something different for the same file, so cached
    # runs from the old logic are discarded.
    version = 1

    def default_path(self) -> Path:
        return Path.home() / ".codex" / "sessions"

    def discover(self, path: Path) -> Iterator[Source]:
        root = _extended(path)
        if os.path.isfile(root):
            listing: Iterable[tuple[str, list[str], list[str]]] = [
                (os.path.dirname(root), [], [os.path.basename(root)])
            ]
        else:
            listing = os.walk(root)
        for directory, _, files in listing:
            for file in sorted(files):
                if file.startswith("rollout-") and file.endswith(".jsonl"):
                    # Session and project come from the file's own metadata, in `parse`.
                    yield Source(path=os.path.join(directory, file), session="", project="")

    def parse(self, source: Source, idle_cap_s: int, stats: ParseStats) -> list[RawRun]:
        lines: list[_Line] = []
        try:
            with open(source.path, encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    try:
                        line = _project(json.loads(raw))
                    except ValueError:
                        stats.unparsed_lines += 1
                        continue
                    if line is not None:
                        lines.append(line)
        except OSError:
            stats.unreadable_files += 1
            return []
        metas = [line for line in lines if line.kind == "session_meta"]
        if not metas or metas[0].session_id is None:
            stats.empty_runs += 1
            return []
        first = metas[0]
        # Copied history would count the same turns twice, so a fork, or a file whose metadata
        # names more than one session, is skipped whole. Upkeep sessions are not agent work.
        forked = any(meta.forked for meta in metas)
        mixed = len({meta.session_id for meta in metas}) != 1
        if forked or mixed or first.source in _SKIPPED_SOURCES:
            stats.skipped_files += 1
            return []
        contexts = {line.turn_id: line for line in lines if line.kind == "turn_context"}
        last_model = next((line.model for line in reversed(lines) if line.model), None)
        tokens_by_turn: dict[str, int] = {}
        for line in lines:
            if line.kind == "tokens" and line.turn_id is not None:
                tokens_by_turn[line.turn_id] = max(tokens_by_turn.get(line.turn_id, 0), line.tokens)
        runs: list[RawRun] = []
        for turn in _turns(lines):
            context = contexts.get(turn.turn_id)
            model = (context.model if context else None) or last_model
            times = sorted(t for t in (turn.start, *turn.times, turn.end) if t is not None)
            if model is None or len(times) < 2:
                stats.empty_runs += 1
                continue
            active_s, spans = _active(times, idle_cap_s)
            tokens = max(turn.tokens, tokens_by_turn.get(turn.turn_id or "", 0))
            review = first.source == "review" or model == _REVIEW_MODEL
            runs.append(
                RawRun(
                    harness=self.name,
                    model=model,
                    start=times[0],
                    end=times[-1],
                    spans=spans,
                    active_s=active_s,
                    tool_calls=turn.tool_calls,
                    edit_calls=turn.edit_calls,
                    tokens=tokens or None,
                    errors=turn.errors,
                    outcome=turn.outcome,
                    effort=context.effort if context else None,
                    agent_type="reviewer" if review else None,
                    session=first.session_id or "",
                    project=first.folder or "",
                )
            )
        return runs

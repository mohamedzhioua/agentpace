"""The OpenCode adapter: one user message and the agent's replies to it is one run, and one
session is one batch.

EXPERIMENTAL. Built from the upstream source of `opencode-ai` 1.19.5, tag `v1.19.5` of
`anomalyco/opencode` at commit 810c0eba1a7348036f8bdf61e6632d0c36c6876c, and shipped unverified
against real session history. Citations below are `file:line` at that commit.

Layout. OpenCode keeps history in one SQLite database, `opencode.db`, or `opencode-<channel>.db`
for a preview channel (`packages/core/src/database/database.ts:43-55`). It sits in
`$XDG_DATA_HOME/opencode`, else `~/.local/share/opencode`, on every OS, Windows included,
because `xdg-basedir` 5.1.0 has no Windows branch (`packages/core/src/global.ts:3-11`). The tables
read here are `session`, `message` and `part` (`packages/core/src/session/sql.ts:22-98`). A message
row's `data` is the v1 message without its ids (`packages/core/src/session/projector.ts:77-82`),
and a part row's `data` is the v1 part without its ids (`projector.ts:84-87`). The shapes are in
`packages/schema/src/v1/session.ts`.

A run. For each user message the agent loop writes one assistant message per model step, every
one with `parentID` set to that user message (`packages/opencode/src/session/prompt.ts:1186-1201`),
and stops once a step finishes with a reason other than `tool-calls` or `unknown`
(`prompt.ts:1111-1130`). So the assistant messages sharing a `parentID` are one run. The human
wait sits before the user message, outside the run. A task subagent runs in a child session whose
`parent_id` names the parent (`packages/opencode/src/tool/task.ts:156-172`). Child sessions are
runs in their own batch, as Codex subagent threads are, and never summed into the parent turn.

Privacy: `_project_message` and `_project_part` are the only code that touches a decoded record,
and they copy an allowlist. From an assistant message: `parentID`, the epoch times, the model id,
the agent name, a variant from the schema's effort enum, a summary flag, an aborted flag, an error
flag, whether the finish reason is `stop`, and three token counters. From a part: its type, and
for a tool part whether its name is an edit tool, whether it failed (not merely interrupted), and
its start and end times; for a step-finish part three token counters. From a session row: its id,
whether it has a parent, its creation time and its folder (a hash input only, which sits in
memory and in the local cache as Codex working folders do, never in a row). Prompts, text,
reasoning, tool names beyond that comparison, tool input, output and error text, error messages,
titles, paths, summaries and system prompts are never copied. SQLite messages are discarded,
because they can quote what they failed on.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from agentpace import schema
from agentpace.adapters.base import ParseStats, RawRun, Source
from agentpace.adapters.claude_code import _active

_EFFORTS = frozenset(schema.enum("effort"))
# `packages/schema/src/v1/session.ts:357-370`. Any other part type is counted as unknown.
_PART_TYPES = frozenset(
    {
        "text",
        "subtask",
        "reasoning",
        "file",
        "tool",
        "step-start",
        "step-finish",
        "snapshot",
        "patch",
        "agent",
        "retry",
        "compaction",
    }
)
# `packages/opencode/src/tool/edit.ts:59`, `write.ts:28`, `apply_patch.ts:23`.
_EDIT_TOOLS = frozenset({"edit", "write", "apply_patch"})
_TOOL_STATUSES = frozenset({"pending", "running", "completed", "error"})
# `packages/schema/src/v1/session.ts:43`, set on a user interrupt (`prompt.ts:1203-1211`).
_ABORTED = "MessageAbortedError"
# The hidden agent that writes compaction summaries
# (`packages/opencode/src/session/compaction.ts:393-401`).
_COMPACTION_AGENT = "compaction"
# A cheap superset of tool and step-finish parts, so most text parts are never loaded. Python
# still decides each row by its own `type`.
_PART_FILTER = "SELECT message_id, time_created, data FROM part WHERE data LIKE ? OR data LIKE ?"
_PART_PATTERNS = ('%"tool"%', '%"step-finish"%')


@dataclass(frozen=True)
class _Message:
    role: str  # "assistant" or "user". A user message is never read past its role.
    parent: str | None = None
    created: float | None = None
    completed: float | None = None
    model: str | None = None
    agent: str | None = None
    effort: str | None = None
    summary: bool = False
    aborted: bool = False
    failed: bool = False
    clean: bool = False  # the finish reason is `stop`
    tokens: int = 0


@dataclass(frozen=True)
class _Part:
    kind: str  # "tool", "step" or "other"
    edit: bool = False
    failed: bool = False
    times: tuple[float, ...] = ()
    tokens: int = 0


@dataclass(frozen=True)
class _Session:
    child: bool
    created: float | None
    folder: str


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _ms(value: Any) -> float | None:
    number = _number(value)
    return number / 1000 if number is not None else None


def _tokens(value: Any) -> int:
    """Input plus output plus reasoning. OpenCode stores input without cache reads and writes,
    and output without reasoning (`packages/opencode/src/session/session.ts:361-377`), so this is
    the provider's input plus output, as the Claude Code and Codex rules count."""
    if not isinstance(value, dict):
        return 0
    return sum(int(_number(value.get(key)) or 0) for key in ("input", "output", "reasoning"))


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _project_message(record: Any) -> _Message | None:
    """Copy allowed values out of one decoded message. None means malformed or unknown."""
    if not isinstance(record, dict):
        return None
    role = record.get("role")
    if role == "user":
        return _Message("user")
    if role != "assistant":
        return None
    time = record.get("time")
    time = time if isinstance(time, dict) else {}
    parent = _text(record.get("parentID"))
    created = _ms(time.get("created"))
    if parent is None or created is None:
        return None
    error = record.get("error")
    aborted = isinstance(error, dict) and error.get("name") == _ABORTED
    variant = record.get("variant")
    return _Message(
        "assistant",
        parent=parent,
        created=created,
        completed=_ms(time.get("completed")),
        model=_text(record.get("modelID")),
        agent=_text(record.get("agent")),
        effort=variant if isinstance(variant, str) and variant in _EFFORTS else None,
        summary=record.get("summary") is True or record.get("agent") == _COMPACTION_AGENT,
        aborted=aborted,
        failed=error is not None and not aborted,
        clean=record.get("finish") == "stop",
        tokens=_tokens(record.get("tokens")),
    )


def _project_part(record: Any) -> _Part | None:
    """Copy allowed values out of one decoded part. None means malformed or unknown."""
    if not isinstance(record, dict):
        return None
    kind = record.get("type")
    if kind not in _PART_TYPES:
        return None
    if kind == "step-finish":
        return _Part("step", tokens=_tokens(record.get("tokens")))
    if kind != "tool":
        return _Part("other")
    state = record.get("state")
    if not isinstance(state, dict) or state.get("status") not in _TOOL_STATUSES:
        return None
    time = state.get("time")
    time = time if isinstance(time, dict) else {}
    metadata = state.get("metadata")
    # Tools still running when the user interrupts are closed as errors marked `interrupted`
    # (`packages/opencode/src/session/processor.ts:591-607`). They are not failures.
    interrupted = isinstance(metadata, dict) and metadata.get("interrupted") is True
    times = tuple(t for t in (_ms(time.get("start")), _ms(time.get("end"))) if t is not None)
    return _Part(
        "tool",
        edit=record.get("tool") in _EDIT_TOOLS,
        failed=state.get("status") == "error" and not interrupted,
        times=times,
    )


def _read_only_uri(path: str) -> str:
    return Path(os.path.abspath(path)).as_uri() + "?mode=ro"


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


@dataclass
class _Tables:
    sessions: dict[str, _Session]
    messages: list[tuple[str, str, Any]]  # message id, session id, data
    parts: list[tuple[Any, Any, Any]]  # message id, time created, data


def _read(path: str) -> _Tables:
    """Read the three tables, read-only. Raises `sqlite3.Error`, whose message is never shown."""
    with closing(sqlite3.connect(_read_only_uri(path), uri=True)) as connection:
        connection.text_factory = _decode
        sessions: dict[str, _Session] = {}
        for sid, parent, folder, created in connection.execute(
            "SELECT id, parent_id, directory, time_created FROM session"
        ):
            if isinstance(sid, str):
                sessions[sid] = _Session(
                    child=parent is not None,
                    created=_ms(created),
                    folder=folder if isinstance(folder, str) else "",
                )
        messages = [
            (mid, sid, data)
            for mid, sid, data in connection.execute(
                "SELECT id, session_id, data FROM message ORDER BY time_created, id"
            )
            if isinstance(mid, str) and isinstance(sid, str)
        ]
        parts = list(connection.execute(_PART_FILTER, _PART_PATTERNS))
    return _Tables(sessions, messages, parts)


def _decoded(data: Any) -> Any:
    """The decoded value, or None when the text is not JSON. Decoder messages are discarded."""
    if not isinstance(data, str):
        return None
    try:
        return json.loads(data)
    except ValueError:
        return None


def _outcome(last: _Message) -> str:
    """A user interrupt is unknown, as a Codex abort is. Any other error failed. A step that ended
    on `stop` completed. Anything else, such as `length` or no end at all, is unknown."""
    if last.aborted:
        return "unknown"
    if last.failed:
        return "failed"
    return "completed" if last.clean and last.completed is not None else "unknown"


class OpenCodeAdapter:
    name = "opencode"
    # Bump whenever `parse` would return something different for the same file, so cached
    # runs from the old logic are discarded.
    version = 1

    def default_path(self) -> Path:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        return Path(base) / "opencode"

    def discover(self, path: Path) -> Iterator[Source]:
        root = os.path.abspath(path)
        if os.path.isfile(root):
            names: Iterable[str] = [os.path.basename(root)]
            root = os.path.dirname(root)
        else:
            # Top level only: the data folder also holds snapshot repositories and logs.
            try:
                names = sorted(os.listdir(root))
            except OSError:
                return
        for name in names:
            if name == "opencode.db" or (name.startswith("opencode-") and name.endswith(".db")):
                database = os.path.join(root, name)
                # Recent writes sit in the write-ahead log (`database.ts:27`), so a change to it
                # makes the cached runs stale. Session and project are set per run, in `parse`.
                yield Source(path=database, session="", project="", companions=(database + "-wal",))

    def parse(self, source: Source, idle_cap_s: int, stats: ParseStats) -> list[RawRun]:
        try:
            tables = _read(source.path)
        except sqlite3.Error:
            stats.unreadable_files += 1
            return []
        # Assistant messages grouped by session and the user message they answer, in row order.
        groups: dict[tuple[str, str], list[tuple[_Message, str]]] = defaultdict(list)
        for mid, sid, data in tables.messages:
            message = _project_message(_decoded(data))
            if message is None:
                stats.unparsed_lines += 1
                continue
            if message.role == "assistant" and message.parent is not None:
                groups[(sid, message.parent)].append((message, mid))
        parts: dict[str, list[_Part]] = defaultdict(list)
        for mid, stamp, data in tables.parts:
            part = _project_part(_decoded(data))
            if part is None:
                stats.unparsed_lines += 1
                continue
            # The row's own creation time is when the part was first written (`projector.ts:319`).
            created = _ms(stamp)
            if part.kind != "other" and isinstance(mid, str):
                extra = (created,) if created is not None else ()
                parts[mid].append(replace(part, times=(*part.times, *extra)))
        runs: list[RawRun] = []
        for key, members in groups.items():
            run = self._run(key, members, tables.sessions, parts, idle_cap_s)
            if isinstance(run, RawRun):
                runs.append(run)
            elif run == "skipped":
                stats.skipped_runs += 1
            else:
                stats.empty_runs += 1
        runs.sort(key=lambda run: (run.start, run.end))
        return runs

    def _run(
        self,
        key: tuple[str, str],
        pairs: list[tuple[_Message, str]],
        sessions: dict[str, _Session],
        parts: dict[str, list[_Part]],
        idle_cap_s: int,
    ) -> RawRun | Literal["skipped", "empty"]:
        session = sessions.get(key[0])
        if session is None:
            return "empty"
        # A fork copies earlier messages, with their original times, into a new session
        # (`packages/opencode/src/session/session.ts:691-731`, created at :529). A message older
        # than its own session is such a copy, and counting it would count the run twice.
        if session.created is not None:
            pairs = [(m, i) for m, i in pairs if m.created is None or m.created >= session.created]
            if not pairs:
                return "skipped"
        # Compaction summaries are upkeep, not agent work, as Codex compact sessions are.
        pairs = [(m, i) for m, i in pairs if not m.summary]
        if not pairs:
            return "skipped"
        models = Counter(m.model for m, _ in pairs if m.model)
        if not models:
            return "empty"
        times: list[float] = []
        tool_calls = edit_calls = errors = tokens = 0
        for message, mid in pairs:
            times += [t for t in (message.created, message.completed) if t is not None]
            steps = 0
            for part in parts.get(mid, ()):
                times += part.times
                if part.kind == "tool":
                    tool_calls += 1
                    edit_calls += part.edit
                    errors += part.failed
                else:
                    steps += part.tokens
            # Each step-finish part carries one step's usage, and the message keeps only the
            # last step's (`packages/opencode/src/session/processor.ts:452-469`).
            tokens += steps or message.tokens
        times.sort()
        if len(times) < 2:
            return "empty"
        active_s, spans = _active(times, idle_cap_s)
        last = pairs[-1][0]  # rows come in creation order
        agents = Counter(m.agent for m, _ in pairs if m.agent)
        return RawRun(
            harness=self.name,
            model=models.most_common(1)[0][0],
            start=times[0],
            end=times[-1],
            spans=spans,
            active_s=active_s,
            tool_calls=tool_calls,
            edit_calls=edit_calls,
            tokens=tokens or None,
            errors=errors,
            outcome=_outcome(last),
            effort=last.effort,
            # A task subagent's agent name is a lookup key for classification, as Claude Code's
            # agentType is. A top-level run has none, as a Codex turn has none.
            agent_type=agents.most_common(1)[0][0] if session.child and agents else None,
            session=key[0],
            project=session.folder,
        )

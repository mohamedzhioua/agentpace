"""SYNTHETIC OpenCode databases. Nothing here came from a real session.

The format is derived from the upstream source, never from real history: `anomalyco/opencode`,
tag `v1.19.5` (npm `opencode-ai` 1.19.5), commit 810c0eba1a7348036f8bdf61e6632d0c36c6876c.

- Tables: `packages/core/src/database/migration/20260127222353_familiar_lady_ursula.ts:23-73`,
  the columns this adapter reads, with later columns left out.
- Message `data` is the v1 message without `id` and `sessionID`, and part `data` the v1 part
  without its ids: `packages/core/src/session/projector.ts:77-87`. Shapes:
  `packages/schema/src/v1/session.ts:240-354` (parts, user) and `:453-485` (assistant).
- A message row's `time_created` is the message's own creation time, and a part row's is when
  the part was first written: `projector.ts:262-268` and `:319`.

Every field that can carry content holds a canary, so the tests can prove none leaves the adapter.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
FOLDER = "/work/CANARY_CWD"
CANARIES = (
    "CANARY_PROMPT",
    "CANARY_RESPONSE",
    "CANARY_REASONING",
    "CANARY_TOOL_INPUT",
    "CANARY_TOOL_OUTPUT",
    "CANARY_TOOL_ERROR",
    "CANARY_TOOL_TITLE",
    "CANARY_TOOL_META",
    "CANARY_TOOL_NAME",
    "CANARY_ERROR_MESSAGE",
    "CANARY_ROOT",
    "CANARY_TITLE",
    "CANARY_SLUG",
    "CANARY_SUMMARY",
    "CANARY_SYSTEM",
    "CANARY_PROVIDER",
    "CANARY_VARIANT",
    "CANARY_FINISH",
    "CANARY_PRIMARY_AGENT",
    "CANARY_SNAPSHOT",
    "CANARY_PATCH",
    "CANARY_MALFORMED",
    "CANARY_UNKNOWN",
)

_DDL = """
CREATE TABLE `session` (
  `id` text PRIMARY KEY,
  `project_id` text NOT NULL,
  `parent_id` text,
  `slug` text NOT NULL,
  `directory` text NOT NULL,
  `title` text NOT NULL,
  `version` text NOT NULL,
  `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL
);
CREATE TABLE `message` (
  `id` text PRIMARY KEY,
  `session_id` text NOT NULL,
  `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL,
  `data` text NOT NULL
);
CREATE TABLE `part` (
  `id` text PRIMARY KEY,
  `message_id` text NOT NULL,
  `session_id` text NOT NULL,
  `time_created` integer NOT NULL,
  `time_updated` integer NOT NULL,
  `data` text NOT NULL
);
"""


def ms(seconds: float) -> int:
    return int((BASE + timedelta(seconds=seconds)).timestamp() * 1000)


def epoch(seconds: float) -> float:
    return (BASE + timedelta(seconds=seconds)).timestamp()


def user(model="claude-sonnet-4-5"):
    """A user message. Content sits in its parts and summary, never read."""
    return {
        "role": "user",
        "time": {"created": 0},
        "summary": {"title": "CANARY_SUMMARY", "body": "CANARY_SUMMARY", "diffs": []},
        "agent": "CANARY_PRIMARY_AGENT",
        "model": {"providerID": "CANARY_PROVIDER", "modelID": model},
        "system": "CANARY_SYSTEM",
    }


def assistant(
    parent,
    created,
    completed,
    *,
    model="claude-sonnet-4-5",
    agent="CANARY_PRIMARY_AGENT",
    finish="stop",
    error=None,
    variant="high",
    summary=None,
    tokens=(10, 5, 2),
):
    """One assistant message: one model step answering the user message `parent`."""
    message = {
        "role": "assistant",
        "time": {"created": ms(created)},
        "parentID": parent,
        "modelID": model,
        "providerID": "CANARY_PROVIDER",
        "mode": agent,
        "agent": agent,
        "path": {"cwd": FOLDER, "root": "/CANARY_ROOT"},
        "cost": 0.01,
        "tokens": {
            "input": tokens[0],
            "output": tokens[1],
            "reasoning": tokens[2],
            "cache": {"read": 999, "write": 99},
        },
        "variant": variant,
    }
    if completed is not None:
        message["time"]["completed"] = ms(completed)
    if finish is not None:
        message["finish"] = finish
    if error is not None:
        message["error"] = {"name": error, "data": {"message": "CANARY_ERROR_MESSAGE"}}
    if summary is not None:
        message["summary"] = summary
    return message


def tool(start, end, *, name="read", status="completed", interrupted=False):
    state = {
        "status": status,
        "input": {"filePath": "/CANARY_TOOL_INPUT"},
        "metadata": {"note": "CANARY_TOOL_META"},
        "time": {"start": ms(start), "end": ms(end)},
    }
    if status == "completed":
        state.update(output="CANARY_TOOL_OUTPUT", title="CANARY_TOOL_TITLE")
    elif status == "error":
        state["error"] = "CANARY_TOOL_ERROR"
        if interrupted:
            state["metadata"]["interrupted"] = True
    return {"type": "tool", "callID": "call-1", "tool": name, "state": state}


def step_finish(tokens=(10, 5, 2)):
    return {
        "type": "step-finish",
        "reason": "stop",
        "snapshot": "CANARY_SNAPSHOT",
        "cost": 0.01,
        "tokens": {
            "input": tokens[0],
            "output": tokens[1],
            "reasoning": tokens[2],
            "cache": {"read": 999, "write": 99},
        },
    }


def text(body="CANARY_RESPONSE"):
    return {"type": "text", "text": body, "time": {"start": 0}}


class Database:
    """Builds one synthetic `opencode.db`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.ids = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(_DDL)

    def _next(self, prefix):
        self.ids += 1
        return f"{prefix}_{self.ids:04d}"

    def _write(self, sql, values):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(sql, values)

    def session(self, sid="ses_1", *, parent=None, created=-60, folder=FOLDER):
        self._write(
            "INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, "prj", parent, "CANARY_SLUG", folder, "CANARY_TITLE", "1.19.5", ms(created), 0),
        )
        return sid

    def message(self, sid, data, *, created=None, mid=None):
        """Insert one message row. `data` may be a dict or raw text, to plant malformed rows."""
        mid = mid or self._next("msg")
        if isinstance(data, dict) and data.get("role") == "user" and created is not None:
            data = {**data, "time": {"created": ms(created)}}
        stamp = data["time"]["created"] if isinstance(data, dict) else ms(created or 0)
        raw = data if isinstance(data, str) else json.dumps(data)
        self._write("INSERT INTO message VALUES (?, ?, ?, ?, ?)", (mid, sid, stamp, stamp, raw))
        return mid

    def part(self, sid, mid, data, *, at=0):
        raw = data if isinstance(data, str) else json.dumps(data)
        self._write(
            "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
            (self._next("prt"), mid, sid, ms(at), ms(at), raw),
        )

    def turn(self, sid, start, end, *, tools=1, edits=0, steps=2, **kwargs):
        """A user message and the agent's run: `steps` assistant messages, the last ending at
        `end`, the first holding the tool calls."""
        parent = self.message(sid, user(), created=start)
        self.part(sid, parent, text("CANARY_PROMPT"), at=start)
        span = (end - start) / steps
        carried = {k: v for k, v in kwargs.items() if k in ("model", "agent", "variant")}
        for step in range(steps):
            first, final = step == 0, step == steps - 1
            created = start + step * span
            completed = start + (step + 1) * span
            options = kwargs if final else {**carried, "finish": "tool-calls"}
            mid = self.message(sid, assistant(parent, created, completed, **options))
            self.part(sid, mid, {"type": "reasoning", "text": "CANARY_REASONING", "time": {}})
            if first:
                for index in range(tools):
                    name = "edit" if index < edits else "read"
                    self.part(sid, mid, tool(created + 1, created + 2, name=name), at=created + 1)
            self.part(sid, mid, text(), at=completed)
            self.part(sid, mid, step_finish(), at=completed)
        return parent

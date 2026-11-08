"""The Gemini CLI adapter: one agent turn is one run, and one session file is one batch.

EXPERIMENTAL. Built from the Gemini CLI source at commit
google-gemini/gemini-cli@62bab5b232c15b925d31e655ce844ab939c882b7, cited below as `file:line`
under `packages/core/src/`, and shipped unverified against real session files.

Layout: `~/.gemini/tmp/<project>/chats/session-<time>-<id>.jsonl` for a main session, and
`chats/<parent session id>/<session id>.jsonl` for a subagent
(services/chatRecordingService.ts:797-840, config/storage.ts:54-60, 195-197, 230-234).
`GEMINI_CLI_HOME` replaces the home folder (utils/paths.ts:22-28). Each line is one JSON record
(chatRecordingService.ts:959-971): a metadata record `{sessionId, projectHash, startTime, ...}`
first (:873-882), then message records `{id, timestamp, type, content, ...}`
(services/chatRecordingTypes.ts:44-87) and `$set`, `$patch` and `$rewindTo` updates
(chatRecordingTypes.ts:123-154). A message is appended again, whole, each time its tokens or tool
calls change (chatRecordingService.ts:1068-1087, 1186, 1259), so one id can appear many times and
its last copy wins (:463). Legacy `.json` files are not read: resuming one copies its whole
history into a new `.jsonl` (:748-772), so reading both would count it twice.

What one run is: Gemini CLI writes no turn-start or turn-end event, so the turn is inferred.

- A user message opens a turn when it is not a tool response and the next user message or model
  response after it is a model response. Tool results are recorded as user messages too
  (core/geminiChat.ts:600-609), and context the CLI injects as user turns (the environment,
  IDE context, a compression summary) is never answered by a model call of its own before the next
  user message (geminiChat.ts:868-888, 1418-1437; utils/environmentContext.ts:84-111).
- A model response is a `gemini` message carrying a model, a token count or tool calls
  (chatRecordingService.ts:1127-1133, 1221-1227). Copies the CLI writes when it rewrites history
  carry none of them (:1579-1591).
- The turn runs until the next opening prompt. Its times are the prompt, each response and each
  tool call (a tool call's time is when it finished, geminiChat.ts:1892). Info, warning and error
  messages, injected context and placeholder replies add no time, because they can be written
  after the human has been away.
- `completed` when the last response asked for no tool call. Otherwise `failed` when the turn holds
  an `error` message, else `unknown`: an interrupted or failed stream is rolled back with a
  `$rewindTo` or `$patch` (geminiChat.ts:836-851), which this adapter does not replay, so the work
  done before the rollback still counts.

Privacy: `_project` is the only code that touches a parsed line, and it copies an allowlist. The
metadata yields the session id and `projectHash`, a SHA-256 of the project folder written by the
CLI itself (chatRecordingService.ts:707, utils/paths.ts:365-367), used only as the input of the
`project` pseudonym. A message yields its id, timestamp, a type from a fixed set, whether a user
message is a tool response (only the presence of a `functionResponse` part is tested), the model,
three token counters, and per tool call its id, whether its name is an edit tool, whether its
status is `error`, and its timestamp. Prompts,
replies, thoughts, tool names, arguments, results, descriptions, summaries, directories and the
memory scratchpad are never copied.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentpace.adapters.base import ParseStats, RawRun, Source
from agentpace.adapters.claude_code import _active, _as_int, _extended, _parse_time

# chatRecordingTypes.ts:72-82.
_MESSAGE_TYPES = frozenset({"user", "info", "error", "warning", "gemini"})
# tools/definitions/base-declarations.ts:61 and :65.
_EDIT_TOOLS = frozenset({"write_file", "replace"})


@dataclass(frozen=True)
class _Call:
    call_id: str
    edit: bool
    failed: bool  # status "error" (scheduler/types.ts:26-34, 206-209)
    time: float | None


@dataclass(frozen=True)
class _Record:
    kind: str  # "meta", "message" or "update"
    session_id: str | None = None
    project: str | None = None
    message_id: str | None = None
    time: float | None = None
    type: str | None = None
    tool_response: bool = False
    model: str | None = None
    tokens: int | None = None
    calls: tuple[_Call, ...] = ()


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _is_tool_response(content: Any) -> bool:
    """True when any part holds a `functionResponse` (utils/messageInspectors.ts:9-15)."""
    if not isinstance(content, list):
        return False
    return any(isinstance(part, dict) and bool(part.get("functionResponse")) for part in content)


def _tokens(value: Any) -> int | None:
    """Input plus output, without cache reads. `input` is the prompt count, which includes the
    cached part, so `cached` is taken off (chatRecordingTypes.ts:19-26). Thought and tool-prompt
    tokens are left out, as reasoning tokens are for Codex."""
    if not isinstance(value, dict):
        return None
    prompt = _as_int(value.get("input"))
    return max(prompt - _as_int(value.get("cached")), 0) + _as_int(value.get("output"))


def _calls(value: Any) -> tuple[_Call, ...]:
    """Each tool call's id, edit flag, error flag and time (chatRecordingTypes.ts:54-67)."""
    calls: list[_Call] = []
    for call in value if isinstance(value, list) else ():
        if not isinstance(call, dict) or not isinstance(call.get("id"), str):
            continue
        calls.append(
            _Call(
                call_id=call["id"],
                edit=call.get("name") in _EDIT_TOOLS,
                failed=call.get("status") == "error",
                time=_parse_time(call.get("timestamp")),
            )
        )
    return tuple(calls)


def _project(record: Any) -> _Record | None:
    """Copy allowed values out of one parsed line. The only code that touches a parsed line.

    Shapes are told apart in the order the CLI's own loader uses (chatRecordingService.ts:319-563).
    None means the line matches no known shape, or names an unknown message type."""
    if not isinstance(record, dict):
        return None
    if isinstance(record.get("$rewindTo"), str) or isinstance(record.get("$patch"), dict):
        return _Record("update")
    if isinstance(record.get("id"), str):
        kind = record.get("type")
        if kind not in _MESSAGE_TYPES:
            return None
        time = _parse_time(record.get("timestamp"))
        if kind == "user":
            return _Record(
                "message",
                message_id=record["id"],
                time=time,
                type="user",
                tool_response=_is_tool_response(record.get("content")),
            )
        if kind == "gemini":
            return _Record(
                "message",
                message_id=record["id"],
                time=time,
                type="gemini",
                model=_text(record.get("model")),
                tokens=_tokens(record.get("tokens")),
                calls=_calls(record.get("toolCalls")),
            )
        return _Record("message", message_id=record["id"], time=time, type=kind)
    update = record.get("$set")
    if isinstance(update, dict):
        session_id = _text(update.get("sessionId"))
        return _Record("meta", session_id=session_id) if session_id else _Record("update")
    if isinstance(record.get("sessionId"), str) and isinstance(record.get("projectHash"), str):
        return _Record("meta", session_id=record["sessionId"], project=record["projectHash"])
    return None


@dataclass
class _Message:
    """One message id, merged across every copy of it in the file."""

    type: str
    time: float | None
    tool_response: bool
    model: str | None
    tokens: int | None
    calls: dict[str, _Call] = field(default_factory=dict)

    @property
    def response(self) -> bool:
        return self.type == "gemini" and (
            self.model is not None or self.tokens is not None or bool(self.calls)
        )


def _merge(records: Iterable[_Record]) -> list[_Message]:
    """One entry per message id, at its first position. Later copies update tokens, the model
    and tool calls, as the CLI's own loader keeps the last copy."""
    merged: dict[str, _Message] = {}
    for record in records:
        if record.kind != "message" or record.message_id is None or record.type is None:
            continue
        message = merged.get(record.message_id)
        if message is None:
            merged[record.message_id] = _Message(
                record.type,
                record.time,
                record.tool_response,
                record.model,
                record.tokens,
                {call.call_id: call for call in record.calls},
            )
            continue
        if message.time is None:
            message.time = record.time
        message.tool_response = record.tool_response
        message.model = record.model or message.model
        message.tokens = record.tokens if record.tokens is not None else message.tokens
        message.calls.update({call.call_id: call for call in record.calls})
    return list(merged.values())


def _opens(messages: Sequence[_Message], index: int) -> bool:
    message = messages[index]
    if message.type != "user" or message.tool_response:
        return False
    for later in messages[index + 1 :]:
        if later.response:
            return True
        if later.type == "user":
            return False
    return False


def _turns(messages: Sequence[_Message]) -> Iterator[tuple[_Message, Sequence[_Message]]]:
    """Each opening prompt with every message up to the next one."""
    openers = [index for index in range(len(messages)) if _opens(messages, index)]
    for position, start in enumerate(openers):
        stop = openers[position + 1] if position + 1 < len(openers) else len(messages)
        yield messages[start], messages[start + 1 : stop]


class GeminiCliAdapter:
    name = "gemini-cli"
    # Bump whenever `parse` would return something different for the same file, so cached
    # runs from the old logic are discarded.
    version = 1

    def default_path(self) -> Path:
        home = os.environ.get("GEMINI_CLI_HOME")
        return (Path(home) if home else Path.home()) / ".gemini" / "tmp"

    def discover(self, path: Path) -> Iterator[Source]:
        root = _extended(path)
        if os.path.isfile(root):
            if root.endswith(".jsonl"):
                yield Source(path=root, session="", project="")
            return
        for directory, _, files in os.walk(root):
            parts = directory.replace("\\", "/").split("/")
            # A main session sits in `chats`, a subagent one level below it.
            if "chats" not in parts[-2:]:
                continue
            for file in sorted(files):
                if file.endswith(".jsonl"):
                    # Session and project come from the file's own metadata, in `parse`.
                    yield Source(path=os.path.join(directory, file), session="", project="")

    def parse(self, source: Source, idle_cap_s: int, stats: ParseStats) -> list[RawRun]:
        records: list[_Record] = []
        try:
            with open(source.path, encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    if not raw.strip():
                        continue
                    try:
                        record = _project(json.loads(raw))
                    except ValueError:
                        record = None
                    if record is None:
                        stats.unparsed_lines += 1
                        continue
                    records.append(record)
        except OSError:
            stats.unreadable_files += 1
            return []
        sessions = [r.session_id for r in records if r.kind == "meta" and r.session_id]
        if not sessions:
            stats.empty_runs += 1
            return []
        if len(set(sessions)) != 1:
            # Metadata naming two sessions would mix their turns, so the file is skipped whole.
            stats.skipped_files += 1
            return []
        project = next((r.project for r in records if r.kind == "meta" and r.project), None)
        messages = _merge(records)
        last_model = next((m.model for m in reversed(messages) if m.model), None)
        turns = list(_turns(messages))
        if not turns:
            stats.empty_runs += 1  # as a cached file with no runs is counted
            return []
        runs: list[RawRun] = []
        for prompt, body in turns:
            responses = [message for message in body if message.response]
            calls: dict[str, _Call] = {}
            for response in responses:
                calls.update(response.calls)
            models = Counter(response.model for response in responses if response.model)
            model = models.most_common(1)[0][0] if models else last_model
            stamps = [prompt.time, *(r.time for r in responses), *(c.time for c in calls.values())]
            times = sorted(t for t in stamps if t is not None)
            if model is None or len(times) < 2:
                stats.empty_runs += 1
                continue
            if not responses[-1].calls:
                outcome = "completed"
            elif any(message.type == "error" for message in body):
                outcome = "failed"
            else:
                outcome = "unknown"
            active_s, spans = _active(times, idle_cap_s)
            tokens = sum(response.tokens or 0 for response in responses)
            runs.append(
                RawRun(
                    harness=self.name,
                    model=model,
                    start=times[0],
                    end=times[-1],
                    spans=spans,
                    active_s=active_s,
                    tool_calls=len(calls),
                    edit_calls=sum(call.edit for call in calls.values()),
                    tokens=tokens or None,
                    errors=sum(call.failed for call in calls.values()),
                    outcome=outcome,
                    session=sessions[0],
                    project=project or "",
                )
            )
        return runs

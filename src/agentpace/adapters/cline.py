"""The Cline adapter: one agent turn is one run, and one task is one batch. EXPERIMENTAL.

Built from the upstream source, not from real history, and shipped unverified as experimental.
Pinned commits of `cline/cline`: `94eecbbe1a2f899c66b118d13656651b0a884e1f` (main) and
`7761370240ffce9e3890cb56f6c51655f3f5b0a7` (the `legacy-extension` branch, whose writer the
stable VSIX still runs for most users, per `apps/vscode-rollout/README.md:6` and
`CHANGELOG.md:492` at main).

Layout (legacy writer, `apps/vscode/src/core/storage/disk.ts:129-133,328-339@7761370`):
`<storage>/tasks/<task id>/ui_messages.json`, one JSON array of messages, rewritten whole on every
change. `<storage>/state/taskHistory.json` (`disk.ts:454`) indexes the tasks. In VS Code,
`<storage>` is the extension's global storage folder (`extension.ts:645`),
`.../Code/User/globalStorage/saoudrizwan.claude-dev`. Tasks made by the SDK bundle ("Next") live
in `~/.cline/data/sessions` instead (`sdk/packages/shared/src/storage/paths.ts:194-200@94eecbb`)
and are not read here.

A message (`apps/vscode/src/shared/ExtensionMessage.ts:123-140@7761370`) is
`{ts, type: "ask"|"say", ask?, say?, text?, modelInfo?, ...}`. `ts` is epoch milliseconds, set
once when the message is created and never changed afterwards (`core/task/index.ts:837-903,
859-863,1066-1078,3300`). An `ask` waits on the human (`index.ts:931-937`).

A run is one agent turn. It opens on the first message after the start of the task or after the
previous turn closed. It closes on the ask that hands the task back to the human: a
`completion_result`, `followup` or `plan_mode_respond` ask closes it as `completed`. A
`resume_task` or `resume_completed_task` ask means the task was left and reopened, so it closes
the open turn at the message before it, as `failed` when the turn's last request failed and
`unknown` otherwise. The same goes for a turn still open at the end of the file.

Approval waits are not agent time. Every other ask, apart from `command_output`, waits on a
human decision (a tool, command or MCP approval, a failed request to retry). The gap from that
ask to the next message is dropped whole, and the idle cap clips what remains.

Privacy: `_project` is the only code that touches a parsed message, and it copies an allowlist:
`ts`, `type`, the ask or say name from the fixed upstream set, `modelInfo.modelId`, two token
counters and a cancel reason from a fixed set out of an `api_req_started` text, and whether a
`tool` text names a file edit. Prompts, responses, reasoning, commands, tool arguments and
output, paths, images, files and the request text are never copied. `_read_index` copies only
each task's id and starting folder, a hash input that sits in memory and in the local cache as
Codex working folders do, never in a row. `_read_models` copies only model ids and times.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentpace.adapters.base import ParseStats, RawRun, Source
from agentpace.adapters.claude_code import _active, _as_int, _extended

# `ExtensionMessage.ts:142-160@7761370`.
_ASKS = frozenset(
    {
        "followup",
        "plan_mode_respond",
        "act_mode_respond",
        "command",
        "command_output",
        "completion_result",
        "tool",
        "api_req_failed",
        "resume_task",
        "resume_completed_task",
        "mistake_limit_reached",
        "browser_action_launch",
        "use_mcp_server",
        "new_task",
        "condense",
        "summarize_task",
        "report_bug",
        "use_subagents",
    }
)
# `ExtensionMessage.ts:162-200@7761370`.
_SAYS = frozenset(
    {
        "task",
        "error",
        "error_retry",
        "api_req_started",
        "api_req_finished",
        "text",
        "reasoning",
        "completion_result",
        "user_feedback",
        "user_feedback_diff",
        "api_req_retried",
        "command",
        "command_output",
        "tool",
        "shell_integration_warning",
        "shell_integration_warning_with_suggestion",
        "browser_action_launch",
        "browser_action",
        "browser_action_result",
        "mcp_server_request_started",
        "mcp_server_response",
        "mcp_notification",
        "use_mcp_server",
        "diff_error",
        "deleted_api_reqs",
        "clineignore_error",
        "command_permission_denied",
        "checkpoint_created",
        "load_mcp_documentation",
        "generate_explanation",
        "info",
        "task_progress",
        "hook_status",
        "hook_output_stream",
        "subagent",
        "use_subagents",
        "subagent_usage",
        "conditional_rules_applied",
    }
)
_COMPLETING_ASKS = frozenset({"completion_result", "followup", "plan_mode_respond"})
_RESUME_ASKS = frozenset({"resume_task", "resume_completed_task"})
# A command still running, not a human decision (`index.ts:907-912`).
_NOT_WAITS = frozenset({"command_output"})
# Each tool use leaves one message, a `say` when auto-approved and an `ask` otherwise
# (`WriteToFileToolHandler.ts:205-247`, `ExecuteCommandToolHandler.ts:83,231`,
# `UseMcpToolHandler.ts:42,96@7761370`).
_TOOL_NAMES = frozenset({"tool", "command", "use_mcp_server", "browser_action_launch"})
_EDIT_TOOLS = frozenset({"editedExistingFile", "newFileCreated", "fileDeleted"})
_ERROR_SAYS = frozenset({"error", "diff_error", "clineignore_error", "command_permission_denied"})
_ERROR_ASKS = frozenset({"api_req_failed", "mistake_limit_reached"})
# `ExtensionMessage.ts:376@7761370`. `user_cancelled` is an interruption, not a failure.
_CANCEL_REASONS = frozenset({"streaming_failed", "user_cancelled", "retries_exhausted"})
_FAILED_CANCELS = frozenset({"streaming_failed", "retries_exhausted"})
_MESSAGES = "ui_messages.json"
_METADATA = "task_metadata.json"


@dataclass(frozen=True)
class _Message:
    time: float
    kind: str  # "ask" or "say"
    name: str  # one of `_ASKS` or `_SAYS`
    model: str | None = None
    tokens: int = 0
    cancel: str | None = None
    edit: bool = False

    @property
    def waits(self) -> bool:
        """The gap after this message is a human decision, never agent time."""
        return (
            self.kind == "ask"
            and self.name not in _NOT_WAITS
            and self.name not in _COMPLETING_ASKS
            and self.name not in _RESUME_ASKS
        )


def _seconds(value: Any) -> float | None:
    """Epoch milliseconds, as Cline writes them, in seconds. None for anything else."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return value / 1000
    return None


def _text_json(value: Any) -> dict[str, Any]:
    """Decode a message's `text` when it holds a JSON object. Decoder messages are discarded,
    because they can quote the text they failed on."""
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (ValueError, RecursionError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _project(record: Any) -> _Message | None:
    """Copy allowed values out of one parsed message. The only code that touches a message.

    None means malformed or unknown: the caller counts it and skips it.
    """
    if not isinstance(record, dict):
        return None
    time = _seconds(record.get("ts"))
    if time is None:
        return None
    kind = record.get("type")
    if kind == "ask":
        name = record.get("ask")
        known = _ASKS
    elif kind == "say":
        name = record.get("say")
        known = _SAYS
    else:
        return None
    if not isinstance(name, str) or name not in known:
        return None
    info = record.get("modelInfo")
    model = info.get("modelId") if isinstance(info, dict) else None
    tokens = 0
    cancel = None
    edit = False
    if kind == "say" and name == "api_req_started":
        # `ClineApiReqInfo`, `ExtensionMessage.ts:350-365`, written by `core/task/utils.ts:28-49`.
        request = _text_json(record.get("text"))
        tokens = _as_int(request.get("tokensIn")) + _as_int(request.get("tokensOut"))
        reason = request.get("cancelReason")
        cancel = reason if isinstance(reason, str) and reason in _CANCEL_REASONS else None
    elif name == "tool":
        # `ClineSayTool.tool`, `ExtensionMessage.ts:202-215`. Only a fixed name survives.
        edit = _text_json(record.get("text")).get("tool") in _EDIT_TOOLS
    return _Message(
        time=time,
        kind=kind,
        name=name,
        model=model if isinstance(model, str) and model else None,
        tokens=tokens,
        cancel=cancel,
        edit=edit,
    )


@dataclass
class _Turn:
    messages: list[_Message] = field(default_factory=list)
    failing: bool = False  # the latest request failed and nothing has succeeded since
    outcome: str = "unknown"

    def add(self, message: _Message) -> None:
        self.messages.append(message)
        if message.name == "api_req_started":
            self.failing = message.cancel in _FAILED_CANCELS
        elif message.kind == "ask" and message.name in _ERROR_ASKS:
            self.failing = True

    def left_open(self) -> _Turn:
        self.outcome = "failed" if self.failing else "unknown"
        return self


def _turns(messages: Iterable[_Message]) -> Iterator[_Turn]:
    """Group messages into turns by position, closing each on the ask that hands back."""
    turn: _Turn | None = None
    for message in messages:
        resume = message.kind == "ask" and message.name in _RESUME_ASKS
        completing = message.kind == "ask" and message.name in _COMPLETING_ASKS
        if resume:
            if turn is not None:
                yield turn.left_open()  # the turn ends at the message before the reopening
                turn = None
            continue
        if turn is None:
            if completing:
                continue  # nothing open to close
            turn = _Turn()
        turn.add(message)
        if completing:
            turn.outcome = "completed"
            yield turn
            turn = None
    if turn is not None:
        yield turn.left_open()


def _timing(
    messages: list[_Message], idle_cap_s: int
) -> tuple[int, tuple[tuple[float, float], ...]]:
    """Active seconds and spans, with every approval wait dropped and other gaps capped."""
    segments: list[list[float]] = [[]]
    for message in messages:
        segments[-1].append(message.time)
        if message.waits:
            segments.append([])
    active = 0
    spans: list[tuple[float, float]] = []
    for segment in segments:
        if not segment:
            continue
        seconds, parts = _active(sorted(segment), idle_cap_s)
        active += seconds
        spans.extend(parts)
    return active, tuple(sorted(spans))


def _read_json(path: str) -> Any:
    """A whole JSON file, or None. Error messages are discarded, since they can quote content."""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return json.load(handle)
    except (OSError, ValueError, RecursionError):
        return None


def _read_index(path: str) -> dict[str, str]:
    """Task id to starting folder, from `state/taskHistory.json` (`HistoryItem.ts:2,14@7761370`).

    Each entry also holds the task's prompt and costs. Only `id` and `cwdOnTaskInitialization`
    are copied.
    """
    folders: dict[str, str] = {}
    items = _read_json(path)
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        task_id = item.get("id")
        folder = item.get("cwdOnTaskInitialization")
        if isinstance(task_id, str) and isinstance(folder, str):
            folders[task_id] = folder
    return folders


def _read_models(path: str) -> list[tuple[float, str]]:
    """`(time, model id)` pairs from `task_metadata.json` `model_usage`
    (`ContextTrackerTypes.ts:11-32`, written by `ModelContextTracker.ts:28-33@7761370`).
    File paths, environment details and provider ids in the same file are never copied."""
    meta = _read_json(path)
    usage = meta.get("model_usage") if isinstance(meta, dict) else None
    models: list[tuple[float, str]] = []
    for entry in usage if isinstance(usage, list) else ():
        if not isinstance(entry, dict):
            continue
        time = _seconds(entry.get("ts"))
        model = entry.get("model_id")
        if time is not None and isinstance(model, str) and model:
            models.append((time, model))
    return sorted(models)


def _fallback_model(models: list[tuple[float, str]], start: float) -> str | None:
    """The model in use when the turn started, else the first one recorded."""
    chosen = None
    for time, model in models:
        if time <= start or chosen is None:
            chosen = model
    return chosen


def _vscode_user_dir() -> Path:
    """VS Code's per-user folder. This is VS Code's convention, not Cline's, and is unverified."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        return (Path(appdata) if appdata else Path.home() / "AppData" / "Roaming") / "Code" / "User"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User"
    config = os.environ.get("XDG_CONFIG_HOME")
    return (Path(config) if config else Path.home() / ".config") / "Code" / "User"


class ClineAdapter:
    name = "cline"
    # Bump whenever `parse` would return something different for the same file, so cached
    # runs from the old logic are discarded.
    version = 1

    def default_path(self) -> Path:
        # `<publisher>.<name>` from `apps/vscode/package.json:2,14@94eecbb`.
        return _vscode_user_dir() / "globalStorage" / "saoudrizwan.claude-dev"

    def discover(self, path: Path) -> Iterator[Source]:
        root = _extended(path)
        listing: Iterable[tuple[str, list[str], list[str]]]
        if os.path.isfile(root):
            listing = [(os.path.dirname(root), [], [os.path.basename(root)])]
        else:
            listing = os.walk(root)
        indexes: dict[str, dict[str, str]] = {}
        for directory, _, files in listing:
            if _MESSAGES not in files:
                continue
            tasks = os.path.dirname(directory)
            if os.path.basename(tasks) != "tasks":
                continue
            storage = os.path.dirname(tasks)
            if storage not in indexes:
                indexes[storage] = _read_index(os.path.join(storage, "state", "taskHistory.json"))
            task_id = os.path.basename(directory)
            yield Source(
                path=os.path.join(directory, _MESSAGES),
                session=task_id,
                # Missing from the index means one shared project pseudonym, never a guess.
                project=indexes[storage].get(task_id, ""),
                companions=(os.path.join(directory, _METADATA),),
            )

    def parse(self, source: Source, idle_cap_s: int, stats: ParseStats) -> list[RawRun]:
        try:
            with open(source.path, encoding="utf-8", errors="replace") as handle:
                data = json.load(handle)
        except OSError:
            stats.unreadable_files += 1
            return []
        except (ValueError, RecursionError):
            stats.unparsed_lines += 1  # the whole file is one JSON array
            return []
        if not isinstance(data, list):
            stats.unparsed_lines += 1
            return []
        messages: list[_Message] = []
        seen: set[_Message] = set()
        for record in data:
            message = _project(record)
            if message is None:
                stats.unparsed_lines += 1
                continue
            if message in seen:
                continue  # a repeated message is one message
            seen.add(message)
            messages.append(message)
        last_model = next((m.model for m in reversed(messages) if m.model), None)
        recorded: list[tuple[float, str]] | None = None
        runs: list[RawRun] = []
        for turn in _turns(messages):
            times = [message.time for message in turn.messages]
            models = Counter(message.model for message in turn.messages if message.model)
            model = models.most_common(1)[0][0] if models else last_model
            if model is None:
                if recorded is None:
                    recorded = _read_models(source.companions[0]) if source.companions else []
                model = _fallback_model(recorded, min(times))
            if model is None or len(times) < 2:
                stats.empty_runs += 1
                continue
            active_s, spans = _timing(turn.messages, idle_cap_s)
            tokens = sum(message.tokens for message in turn.messages)
            runs.append(
                RawRun(
                    harness=self.name,
                    model=model,
                    start=min(times),
                    end=max(times),
                    spans=spans,
                    active_s=active_s,
                    tool_calls=sum(m.name in _TOOL_NAMES for m in turn.messages),
                    edit_calls=sum(m.edit for m in turn.messages),
                    tokens=tokens or None,
                    errors=sum(
                        (m.kind == "say" and m.name in _ERROR_SAYS)
                        or (m.kind == "ask" and m.name in _ERROR_ASKS)
                        for m in turn.messages
                    ),
                    outcome=turn.outcome,
                    session=source.session,
                    project=source.project,
                )
            )
        return runs

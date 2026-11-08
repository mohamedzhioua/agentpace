"""SYNTHETIC Cline task folders. Nothing here came from a real task.

Cline is not installed where this adapter was written, so these shapes are built from the
upstream source, not copied from real files:

- writer: cline/cline@7761370240ffce9e3890cb56f6c51655f3f5b0a7 (`legacy-extension` branch)
  - `apps/vscode/src/shared/ExtensionMessage.ts:123-140` (a message), `:350-365` (the
    `api_req_started` text), `:202-215` (a `tool` text)
  - `apps/vscode/src/core/task/index.ts:837-903,1066-1078` (asks carry no `modelInfo`, says do)
  - `apps/vscode/src/core/storage/disk.ts:328-339` (`ui_messages.json` is one JSON array)
  - `apps/vscode/src/shared/HistoryItem.ts` (`state/taskHistory.json` entries)
  - `apps/vscode/src/core/context/context-tracking/ContextTrackerTypes.ts` (`task_metadata.json`)
- reader: cline/cline@94eecbbe1a2f899c66b118d13656651b0a884e1f (main),
  `apps/vscode/src/sdk/legacy-state-reader.ts:43-64` (where the files live)

Every field that can carry content holds a canary, so the tests can prove none leaves the adapter.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
MODEL = "claude-sonnet-4-5-20250929"
FOLDER = "/work/CANARY_CWD"
TASK_ID = "CANARY_TASKID_1"
CANARIES = (
    "CANARY_PROMPT",
    "CANARY_RESPONSE",
    "CANARY_REASONING",
    "CANARY_IMAGE",
    "CANARY_PATH",
    "CANARY_REQUEST",
    "CANARY_STREAM_ERROR",
    "CANARY_TOOL_OUTPUT",
    "CANARY_DIFF",
    "CANARY_COMMAND",
    "CANARY_COMMAND_OUTPUT",
    "CANARY_MCP_ARGS",
    "CANARY_MCP_RESPONSE",
    "CANARY_RESULT",
    "CANARY_FEEDBACK",
    "CANARY_ERROR",
    "CANARY_QUESTION",
    "CANARY_PROVIDER",
    "CANARY_CHECKPOINT",
    "CANARY_TASK_NAME",
    "CANARY_HOST",
    "CANARY_TYPE",
    "CANARY_SAY",
    "CANARY_CANCEL",
    "CANARY_TOOL_NAME",
    "CANARY_BROKEN",
)


def ms(seconds):
    return int((BASE + timedelta(seconds=seconds)).timestamp() * 1000)


def epoch(seconds):
    return (BASE + timedelta(seconds=seconds)).timestamp()


def say(seconds, name, text="CANARY_RESPONSE", *, model=MODEL, **extra):
    message = {
        "ts": ms(seconds),
        "type": "say",
        "say": name,
        "text": text,
        "lastCheckpointHash": "CANARY_CHECKPOINT",
        "conversationHistoryIndex": 3,
        **extra,
    }
    if model is not None:
        message["modelInfo"] = {"modelId": model, "providerId": "CANARY_PROVIDER", "mode": "act"}
    return message


def ask(seconds, name, text="CANARY_QUESTION", **extra):
    return {"ts": ms(seconds), "type": "ask", "ask": name, "text": text, **extra}


def task(seconds, **kwargs):
    return say(
        seconds,
        "task",
        "CANARY_PROMPT",
        images=["data:image/png;base64,CANARY_IMAGE"],
        files=["/CANARY_PATH/a.py"],
        **kwargs,
    )


def feedback(seconds, **kwargs):
    return say(seconds, "user_feedback", "CANARY_FEEDBACK", **kwargs)


def request(seconds, tokens=(100, 20), cancel=None, **kwargs):
    info = {
        "request": "CANARY_REQUEST /CANARY_PATH/x.py",
        "tokensIn": tokens[0],
        "tokensOut": tokens[1],
        "cacheWrites": 999,
        "cacheReads": 999,
        "cost": 0.1,
        "streamingFailedMessage": "CANARY_STREAM_ERROR",
    }
    if cancel is not None:
        info["cancelReason"] = cancel
    return say(seconds, "api_req_started", json.dumps(info), **kwargs)


def tool(seconds, name="readFile", *, approval=False, **kwargs):
    text = json.dumps(
        {
            "tool": name,
            "path": "/CANARY_PATH/x.py",
            "content": "CANARY_TOOL_OUTPUT",
            "diff": "CANARY_DIFF",
        }
    )
    # Auto-approved tools are a `say`, tools waiting on the human an `ask`.
    return ask(seconds, "tool", text) if approval else say(seconds, "tool", text, **kwargs)


def completion(seconds, **kwargs):
    return [
        say(seconds, "completion_result", "CANARY_RESULT", **kwargs),
        ask(seconds + 1, "completion_result", ""),
    ]


def turn(start, opener="task", **kwargs):
    """One complete turn, 21 s of agent time around a 60 s approval wait.

    Two tool calls, one an edit the human approves, three requests of 120 tokens each.
    """
    open_message = task(start, **kwargs) if opener == "task" else feedback(start, **kwargs)
    return [
        open_message,
        request(start + 1, **kwargs),
        say(start + 2, "text", reasoning="CANARY_REASONING", **kwargs),
        tool(start + 5, **kwargs),
        request(start + 6, **kwargs),
        tool(start + 10, "editedExistingFile", approval=True),
        request(start + 70, **kwargs),
        *completion(start + 80, **kwargs),
    ]


def history_item(task_id, folder=FOLDER):
    return {
        "id": task_id,
        "ulid": "01CANARYULID",
        "ts": ms(0),
        "task": "CANARY_TASK_NAME",
        "tokensIn": 1,
        "tokensOut": 1,
        "totalCost": 0.1,
        "cwdOnTaskInitialization": folder,
        "shadowGitConfigWorkTree": "/CANARY_PATH/.git",
        "modelId": MODEL,
    }


def metadata(*models):
    return {
        "files_in_context": [
            {
                "path": "/CANARY_PATH/f.py",
                "record_state": "active",
                "record_source": "read_tool",
                "cline_read_date": ms(0),
                "cline_edit_date": None,
            }
        ],
        "model_usage": [
            {"ts": ms(seconds), "model_id": model, "model_provider_id": "CANARY_PROVIDER"}
            for seconds, model in models
        ],
        "environment_history": [{"ts": ms(0), "host_name": "CANARY_HOST", "os_name": "x"}],
    }


def write_task(storage, task_id, messages, meta=None):
    folder = Path(storage) / "tasks" / task_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "ui_messages.json"
    path.write_text(json.dumps(messages), encoding="utf-8")
    if meta is not None:
        (folder / "task_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def write_index(storage, items):
    folder = Path(storage) / "state"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "taskHistory.json").write_text(json.dumps(items), encoding="utf-8")

"""SYNTHETIC Gemini CLI session records. Nothing here came from a real session.

The record shapes and the order they are written in follow the Gemini CLI source at
google-gemini/gemini-cli@62bab5b232c15b925d31e655ce844ab939c882b7. Citations are `file:line`
under `packages/core/src/` at that commit. The format has not yet been checked against a real
session file, so these fixtures are only as right as that reading of the source.

Every field that can carry content holds a canary, so the tests can prove no canary leaves the
adapter.
"""

from datetime import datetime, timedelta, timezone

UPSTREAM = "google-gemini/gemini-cli@62bab5b232c15b925d31e655ce844ab939c882b7"
BASE = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
CANARIES = (
    "CANARY_PROMPT",
    "CANARY_DISPLAY",
    "CANARY_REPLY",
    "CANARY_THOUGHT",
    "CANARY_ARGS",
    "CANARY_RESULT",
    "CANARY_TOOL_NAME",
    "CANARY_DISPLAY_NAME",
    "CANARY_DESCRIPTION",
    "CANARY_ENV",
    "CANARY_SUMMARY",
    "CANARY_SCRATCHPAD",
    "CANARY_INFO",
    "CANARY_ERROR",
    "CANARY_DIRECTORY",
    "CANARY_TYPE",
    "CANARY_STATUS",
    "CANARY_GARBAGE",
)
# Names that sit in the local cache by design, never in a row, a report or `share` output: the
# project folder name in the file path, and `projectHash`, the input of the `project` pseudonym.
PROJECT_DIR = "CANARY_PROJECT_DIR"
PROJECT_HASH = "CANARY_PROJECT_HASH"


def at(seconds):
    """`new Date().toISOString()`, as every timestamp is written (chatRecordingService.ts:1103)."""
    stamp = BASE + timedelta(seconds=seconds)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def metadata(seconds=0, session="sess-1", kind=None):
    """The first line of a new session file (chatRecordingService.ts:873-882)."""
    record = {
        "sessionId": session,
        "projectHash": PROJECT_HASH,
        "startTime": at(seconds),
        "lastUpdated": at(seconds),
        "directories": ["/home/CANARY_DIRECTORY"],
    }
    if kind is not None:
        record["kind"] = kind
    return record


def touched(seconds):
    """Written after every recorded message (chatRecordingService.ts:1135, 1060-1066)."""
    return {"$set": {"lastUpdated": at(seconds)}}


def environment(seconds=0):
    """The environment context, a user turn the CLI injects at startup with a stable id
    (utils/environmentContext.ts:84-111), synced to the file without a model
    (chatRecordingService.ts:1579-1591)."""
    return {
        "id": "environment-context",
        "timestamp": at(seconds),
        "type": "user",
        "content": [{"text": "CANARY_ENV"}],
    }


def prompt(seconds, message_id):
    """A human prompt (core/geminiChat.ts:572-577)."""
    return {
        "id": message_id,
        "timestamp": at(seconds),
        "type": "user",
        "content": [{"text": "CANARY_PROMPT"}],
        "displayContent": [{"text": "CANARY_DISPLAY"}],
    }


def tool_response(seconds, message_id, call_id):
    """A tool result, recorded as a user message (core/geminiChat.ts:600-609)."""
    return {
        "id": message_id,
        "timestamp": at(seconds),
        "type": "user",
        "content": [
            {
                "functionResponse": {
                    "id": call_id,
                    "name": "CANARY_TOOL_NAME",
                    "response": {"output": "CANARY_RESULT"},
                }
            }
        ],
    }


def call(seconds, call_id, name="run_shell_command", status="success"):
    """A finished tool call (chatRecordingTypes.ts:54-67, core/geminiChat.ts:1886-1900)."""
    return {
        "id": call_id,
        "name": name,
        "args": {"command": "CANARY_ARGS", "file_path": "/home/CANARY_DIRECTORY/x"},
        "result": [{"functionResponse": {"id": call_id, "response": {"output": "CANARY_RESULT"}}}],
        "status": status,
        "timestamp": at(seconds),
        "displayName": "CANARY_DISPLAY_NAME",
        "description": "CANARY_DESCRIPTION",
        "resultDisplay": "CANARY_RESULT",
        "renderOutputAsMarkdown": False,
    }


def reply(
    seconds, message_id, *, model="gemini-2.5-pro", text="CANARY_REPLY", tokens=(100, 20, 30)
):
    """A model response with its usage (chatRecordingService.ts:1127-1133, 1168-1189). `tokens`
    is (input, output, cached); `input` is the prompt count, which includes the cached part."""
    record = {
        "id": message_id,
        "timestamp": at(seconds),
        "type": "gemini",
        "content": text,
        "thoughts": [
            {"subject": "CANARY_THOUGHT", "description": "CANARY_THOUGHT", "timestamp": at(seconds)}
        ],
        "tokens": None,
    }
    if tokens is not None:
        prompt_tokens, output, cached = tokens
        record["tokens"] = {
            "input": prompt_tokens,
            "output": output,
            "cached": cached,
            "thoughts": 7,
            "tool": 3,
            "total": prompt_tokens + output + 10,
        }
    if model is not None:
        record["model"] = model
    return record


def with_calls(record, *calls):
    """The same response appended again, whole, once its tool calls finish
    (chatRecordingService.ts:1237-1259)."""
    return {**record, "toolCalls": list(calls)}


def turn(start, end, prefix, *, model="gemini-2.5-pro", tool="replace", status="success"):
    """One completed turn in the order the CLI writes it: the prompt, a response asking for a
    tool, that response again with its finished call, the tool result, then the final reply."""
    asks = reply(start + 5, f"{prefix}-m1", model=model, text="")
    return [
        prompt(start, f"{prefix}-u"),
        touched(start),
        asks,
        touched(start + 5),
        with_calls(asks, call(start + 20, f"{prefix}-c1", name=tool, status=status)),
        tool_response(start + 21, f"{prefix}-t1", f"{prefix}-c1"),
        touched(start + 21),
        reply(end, f"{prefix}-m2", model=model),
        touched(end),
    ]


def interrupted(start, prefix, *, status="cancelled"):
    """A turn stopped during its tool call: the call is recorded with its status, and no final
    reply follows."""
    asks = reply(start + 5, f"{prefix}-m1", text="")
    return [
        prompt(start, f"{prefix}-u"),
        touched(start),
        asks,
        touched(start + 5),
        with_calls(asks, call(start + 30, f"{prefix}-c1", status=status)),
    ]


def repairs(seconds, prefix, call_id):
    """What the next prompt writes first after an interrupted turn: a synthetic tool result for
    the unanswered call and a placeholder reply, neither with a model
    (core/geminiChat.ts:868-888, 952-958)."""
    return [
        tool_response(seconds, f"{prefix}-fix-t", call_id),
        {
            "id": f"{prefix}-fix-m",
            "timestamp": at(seconds),
            "type": "gemini",
            "content": [{"text": "CANARY_REPLY"}],
            "thoughts": [],
            "tokens": None,
        },
    ]


def compression(seconds, prefix):
    """A history rewrite after compression: the summary and its acknowledgement are appended as
    new messages without a model, then the old ids are removed (core/client.ts:1305,
    chatRecordingService.ts:1579-1591, 1683-1693)."""
    return [
        {
            "id": f"{prefix}-summary",
            "timestamp": at(seconds),
            "type": "user",
            "content": [{"text": "CANARY_SUMMARY"}],
        },
        {
            "id": f"{prefix}-ack",
            "timestamp": at(seconds),
            "type": "gemini",
            "content": [{"text": "CANARY_REPLY"}],
        },
        {"$patch": {"removeIds": [f"{prefix}-old"], "orderIds": [f"{prefix}-summary"]}},
        touched(seconds),
    ]


def info(seconds, message_id, kind="info"):
    """A UI message: info, warning or error (cli/src/ui/hooks/useHistoryManager.ts:91-114)."""
    text = "CANARY_ERROR" if kind == "error" else "CANARY_INFO"
    return {"id": message_id, "timestamp": at(seconds), "type": kind, "content": text}


def rewind(message_id):
    """A rollback of the history from this message on (chatRecordingService.ts:1469, 1676)."""
    return {"$rewindTo": message_id}


def scratchpad():
    """Workflow metadata kept for memory extraction (chatRecordingTypes.ts:33-39)."""
    return {
        "$set": {
            "summary": "CANARY_SUMMARY",
            "memoryScratchpad": {
                "version": 1,
                "workflowSummary": "CANARY_SCRATCHPAD",
                "touchedPaths": ["/home/CANARY_DIRECTORY"],
            },
        }
    }


def session(*records, session_id="sess-1", kind=None):
    """A whole file: metadata, the environment context, then the given records."""
    return [metadata(session=session_id, kind=kind), environment(), *records]

"""SYNTHETIC test data. Nothing here came from a real run.

Builds fake Claude Code transcript trees and fake dataset rows. Every sensitive field holds a
canary string, so a test can prove that no canary reaches any output.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CANARIES = (
    "CANARY_PROMPT",
    "CANARY_CODE",
    "CANARY_PATH",
    "CANARY_REPO",
    "CANARY_BRANCH",
    "CANARY_DESCRIPTION",
    "CANARY_AGENT",
    "CANARY_TOOL_INPUT",
    "CANARY_RESULT",
    "CANARY_THINKING",
)

BASE = datetime(2026, 3, 2, 9, 0, 0, tzinfo=timezone.utc)


def _stamp(seconds: float) -> str:
    return (BASE + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def assistant(
    at: float,
    message_id: str,
    tool: str | None = None,
    model: str = "claude-opus-5-5",
    input_tokens: int = 100,
    output_tokens: int = 20,
    stop_reason: str | None = "tool_use",
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "thinking", "thinking": "CANARY_THINKING"}]
    if tool:
        content.append(
            {"type": "tool_use", "name": tool, "input": {"file_path": "/CANARY_PATH/x.py"}}
        )
    return {
        "type": "assistant",
        "timestamp": _stamp(at),
        "cwd": "/home/someone/CANARY_REPO",
        "gitBranch": "CANARY_BRANCH",
        "wireToolInputs": {"command": "CANARY_TOOL_INPUT"},
        "message": {
            "id": message_id,
            "model": model,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": 999_999,
            },
            "content": content,
        },
    }


def user(at: float, is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": _stamp(at),
        "cwd": "/home/someone/CANARY_REPO",
        "toolUseResult": {"stdout": "CANARY_RESULT"},
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "is_error": is_error, "content": "CANARY_CODE"},
                {"type": "text", "text": "CANARY_PROMPT"},
            ],
        },
    }


def write_run(
    root: Path,
    project: str,
    session: str,
    agent: str,
    lines: list[dict[str, Any]],
    agent_type: str | None = "implementer",
    finished: bool | None = None,
    nested: str = "",
) -> Path:
    directory = root / project / session / "subagents"
    if nested:
        directory = directory / nested
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"agent-{agent}.jsonl"
    body = "\n".join(json.dumps(line) for line in lines)
    path.write_text(body + "\n", encoding="utf-8")
    meta: dict[str, Any] = {"description": "CANARY_DESCRIPTION CANARY_PROMPT"}
    if agent_type is not None:
        meta["agentType"] = agent_type
    if finished is not None:
        meta["finished"] = finished
    (directory / f"agent-{agent}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def simple_run(start: float, duration: float, tool: str = "Read") -> list[dict[str, Any]]:
    """A run that starts at `start` and ends `duration` seconds later on a hand-back."""
    return [
        user(start),
        assistant(start + duration / 2, f"m{start}-1", tool),
        user(start + duration / 2),
        assistant(start + duration, f"m{start}-2", "SubagentHandback"),
    ]


def row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "harness": "claude-code",
        "model": "claude-opus-5-5",
        "task_kind": "feature",
        "risk": "normal",
        "size": "S",
        "duration_s": 120,
        "tool_calls": 10,
        "tokens": 1000,
        "retries": 0,
        "outcome": "completed",
        "parallel_agents": 1,
        "contributor": "0123456789abcdef",
        "week": "2026-W10",
        "batch": "aaaaaaaaaaaa",
        "project": "bbbbbbbbbbbb",
        "offset_s": 0,
    }
    base.update(overrides)
    return base


def dataset(
    seed: int = 7,
    sessions: int = 60,
    runs_per_session: int = 8,
    weeks: int = 6,
    contributors: int = 1,
    drift: float = 0.0,
) -> list[dict[str, Any]]:
    """Log-normal rows with session effects, so estimators have known structure to recover."""
    rng = random.Random(seed)
    medians = {("review", "low"): 120.0, ("feature", "normal"): 300.0, ("docs", "normal"): 60.0}
    cells = list(medians)
    rows: list[dict[str, Any]] = []
    for s in range(sessions):
        week = s % weeks + 1
        effect = rng.gauss(0, 0.4)
        offset = 0
        peak = rng.choice([1, 2, 3, 4])
        for r in range(runs_per_session):
            kind, risk = cells[r % len(cells)]
            log_median = medians[(kind, risk)]
            value = log_median * 2.718281828 ** (effect + rng.gauss(0, 0.8) + drift * week)
            duration = max(1, round(value))
            rows.append(
                row(
                    task_kind=kind,
                    risk=risk,
                    size="na" if kind == "review" else rng.choice(["S", "M", "L"]),
                    duration_s=duration,
                    tool_calls=max(1, duration // 10),
                    parallel_agents=peak,
                    contributor=f"{s % contributors:016x}",
                    week=f"2026-W{week:02d}",
                    batch=f"{s:012x}",
                    project=f"{s % 4:012x}",
                    offset_s=offset,
                )
            )
            offset += max(1, duration // peak)
    return rows

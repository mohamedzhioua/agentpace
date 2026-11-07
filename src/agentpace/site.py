"""Build the data block inside `docs/index.html`.

The page is one static file with no build step. Its data sits inline, so it works from `file://`
and makes no requests of any kind. Fetching a JSON file would add a network call.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Sequence
from html import escape
from typing import Any

from agentpace import estimate as est
from agentpace import render
from agentpace.schema import Row

START = '<script id="leaderboard-data" type="application/json">'
END = "</script>"
BINS_PER_DECADE = 10


def _summary(values: Sequence[int]) -> dict[str, int]:
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50": round(est.quantile(ordered, 0.5)),
        "p75": round(est.quantile(ordered, 0.75)),
        "p90": round(est.quantile(ordered, 0.9)),
    }


def _histogram(values: Sequence[int]) -> list[dict[str, int]]:
    """Counts on a log axis. Aggregates only: individual runs are not embedded in the page."""
    counts: dict[int, int] = defaultdict(int)
    for value in values:
        counts[math.floor(math.log10(value) * BINS_PER_DECADE)] += 1
    return [
        {
            "low": round(10 ** (index / BINS_PER_DECADE)),
            "high": round(10 ** ((index + 1) / BINS_PER_DECADE)),
            "n": counts[index],
        }
        for index in range(max(counts) + 1)
    ]


def payload(rows: Sequence[Row], backtest_report: dict[str, Any]) -> dict[str, Any]:
    completed = [row for row in rows if row["outcome"] == "completed"]
    cells: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for row in completed:
        cells[(row["model"], row["task_kind"], row["risk"])].append(row["duration_s"])
    durations = [row["duration_s"] for row in completed]
    weeks = sorted({row["week"] for row in rows})
    time_split = backtest_report["splits"]["time"]["tracks"]["pre-run"]
    winner = backtest_report["selection"]["winner"]
    plans = backtest_report["plans"]
    return {
        "runs": len(rows),
        "completed": len(completed),
        "sessions": len({row["batch"] for row in rows}),
        "contributors": len({row["contributor"] for row in rows}),
        "first_week": weeks[0],
        "last_week": weeks[-1],
        "overall": _summary(durations),
        "histogram": _histogram(durations),
        "cells": [
            {"model": key[0], "kind": key[1], "risk": key[2], **_summary(values)}
            for key, values in sorted(cells.items())
        ],
        "backtest": {
            "sessions": plans["sessions"],
            "plan_p80_coverage": round(plans["results"]["agentpace"]["coverage80"]),
            "plan_p90_coverage": round(plans["results"]["agentpace"]["coverage90"]),
            "task_p80_coverage": round(time_split[winner]["session"]["coverage80"]),
            "task_mdape": round(time_split[winner]["session"]["mdape"]),
        },
    }


def texts(data: dict[str, Any]) -> dict[str, str]:
    """Sentences the page shows before any script runs, so link previews, readers without
    JavaScript and crawlers all see the same numbers as everyone else."""
    overall, scores = data["overall"], data["backtest"]
    people = "contributor" if data["contributors"] == 1 else "contributors"
    return {
        "lede": (
            f"Measured from {data['completed']:,} completed subagent runs, not from anyone's "
            f"opinion. Half finished within {render.duration(overall['p50'])}. One in ten took "
            f"more than {render.duration(overall['p90'])}."
        ),
        "trace-note": (
            f"Every completed run from {data['first_week']} to {data['last_week']}. "
            "Time runs left to right on a log scale."
        ),
        "reading-scope": (
            f"{data['runs']:,} runs in {data['sessions']} sessions from {data['contributors']} "
            f"{people}, with heavy automated fan-out. It will not match your setup until more "
            "people contribute."
        ),
        "reading-backtest": (
            f"On {scores['sessions']} held-out sessions, the P80 for the whole session covered "
            f"{scores['plan_p80_coverage']}% and the P90 covered {scores['plan_p90_coverage']}%. "
            f"For a single task the median miss is {scores['task_mdape']}%, so read a range, "
            "never one number."
        ),
    }


def _fill(html: str, element_id: str, text: str) -> str:
    pattern = re.compile(rf'(id="{element_id}"[^>]*>)(.*?)(</(?:p|figcaption)>)', re.DOTALL)
    filled, count = pattern.subn(lambda m: m[1] + escape(text, quote=False) + m[3], html, count=1)
    if count != 1:
        raise ValueError(f"the page has no element with id {element_id}")
    return filled


def embed(html: str, data: dict[str, Any]) -> str:
    start = html.index(START) + len(START)
    end = html.index(END, start)
    block = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return f"{html[:start]}{block}{html[end:]}"


def build(html: str, data: dict[str, Any]) -> str:
    """The data block plus the sentences drawn from it."""
    page = embed(html, data)
    for element_id, text in texts(data).items():
        page = _fill(page, element_id, text)
    return page

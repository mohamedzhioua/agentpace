"""Keep published numbers tied to the data.

A number in a document sits between `<!-- claim:NAME -->` and `<!-- /claim -->`. This module works
out what each one should say. `agentpace claims --check` fails when any of them disagrees with the
rows and the backtest, so a figure in the README cannot outlive the data behind it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agentpace import estimate as est
from agentpace import render
from agentpace.report import ci
from agentpace.schema import Row

_MARKER = re.compile(r"<!-- claim:([a-z0-9_]+) -->(.*?)<!-- /claim -->", re.DOTALL)
_MIN_TABLE_ROWS = 10


def _kind_table(rows: Sequence[Row]) -> str:
    completed = [row for row in rows if row["outcome"] == "completed"]
    entries = []
    for kind in sorted({row["task_kind"] for row in completed}):
        values = sorted(row["duration_s"] for row in completed if row["task_kind"] == kind)
        if len(values) >= _MIN_TABLE_ROWS:
            entries.append((kind, len(values), *(est.quantile(values, q) for q in est.QUANTILES)))
    entries.sort(key=lambda entry: entry[2])
    lines = ["| Task kind | Runs | P50 | P80 | P90 |", "|---|---:|---:|---:|---:|"]
    lines += [
        f"| {kind} | {n:,} | {render.duration(p50)} | {render.duration(p80)} | "
        f"{render.duration(p90)} |"
        for kind, n, p50, p80, p90 in entries
    ]
    return "\n" + "\n".join(lines) + "\n"


def _example_output(rows: Sequence[Row], plan_path: Path | None) -> str:
    """The quickstart's real output, so the README cannot show a result the tool would not give."""
    if plan_path is None or not plan_path.exists():
        return ""
    tasks, cap = est.tasks_from_json(json.loads(plan_path.read_text(encoding="utf-8")))
    return "\n" + render.plan_text(est.estimate(est.fit(rows), tasks, cap)) + "\n"


def values(
    rows: Sequence[Row], report: dict[str, Any], plan_path: Path | None = None
) -> dict[str, str]:
    data = report["dataset"]
    selection = report["selection"]
    winner = selection["winner"]
    results = report["splits"]["time"]["tracks"]["pre-run"]
    ours = results[winner]["session"]
    ours_row = results[winner]["row"]
    median = results["global-median"]["session"]
    plan = report["plans"]["results"]["agentpace"]
    naive_plan = report["plans"]["results"]["sum-of-medians"]
    project = report["splits"]["project"]
    contributor = report["splits"]["contributor"]["status"]
    project_p80 = (
        f"{project['tracks']['pre-run'][winner]['session']['coverage80']:.1f}%"
        if project["status"] == "ok"
        else "not available"
    )
    completed = sorted(row["duration_s"] for row in rows if row["outcome"] == "completed")
    found = report["splits"]["time"]["intervals"]
    plan_found = report["plans"]["intervals"]
    # The README describes the pooled selection-period number, so locked weeks stay out of it.
    weeks = [w for w in report["splits"]["time"]["by_week"] if w["period"] == "selection"]
    latest = weeks[-1]

    def spread(field: str) -> str:
        values = [week[field] for week in weeks]
        return f"{min(values):.0f}% to {max(values):.0f}%"

    def gap(name: str) -> str:
        low, high = found["rival_minus_ours"][name]
        return f"{low:+.0f} to {high:+.0f}"

    return {
        "runs": f"{data['rows']:,}",
        "completed": f"{data['completed']:,}",
        "sessions": str(data["sessions"]),
        "projects": str(data["projects"]),
        "contributors": str(data["contributors"]),
        "first_week": data["weeks"][0],
        "last_week": data["weeks"][-1],
        "top_project": f"{data['top_project_share']:.0f}%",
        "top_sessions": f"{data['top_three_session_share']:.0f}%",
        "overall_p50": render.duration(est.quantile(completed, 0.5)),
        "overall_p90": render.duration(est.quantile(completed, 0.9)),
        "winner": winner,
        "plan_sessions": str(report["plans"]["sessions"]),
        "plan_p50": ci(plan["coverage50"], plan_found["coverage50"], unit="%"),
        "plan_p80": ci(plan["coverage80"], plan_found["coverage80"], unit="%"),
        "plan_p90": ci(plan["coverage90"], plan_found["coverage90"], unit="%"),
        "plan_pinball": ci(plan["pinball"], plan_found["pinball"]),
        "naive_plan_pinball": f"{naive_plan['pinball']:.0f}",
        "plan_mape": f"{plan['mape']:.0f}%",
        "plan_mdape": f"{plan['mdape']:.0f}%",
        "plan_width": f"{plan['width']:.1f}x",
        "plan_width80": f"{plan['width80']:.1f}x",
        "naive_plan_p80": f"{naive_plan['coverage80']:.0f}%",
        "naive_plan_mape": f"{naive_plan['mape']:.0f}%",
        "task_p50": f"{ours['coverage50']:.1f}%",
        "task_p80": ci(ours["coverage80"], found["coverage80"], 1, "%"),
        "task_p90": f"{ours['coverage90']:.1f}%",
        "task_p80_row": f"{ours_row['coverage80']:.1f}%",
        "task_mape": f"{ours['mape']:.0f}%",
        "task_mdape": f"{ours['mdape']:.0f}%",
        "task_width": f"{ours['width']:.1f}x",
        "task_pinball": ci(ours["pinball"], found["pinball"]),
        "median_pinball": f"{median['pinball']:.0f}",
        "median_mape": f"{median['mape']:.0f}%",
        "human_pinball": f"{results['human-guess']['session']['pinball']:.0f}",
        "rounds_pinball": f"{results['fixed-rounds']['session']['pinball']:.0f}",
        "median_pinball_gap": gap("global-median"),
        "human_pinball_gap": gap("human-guess"),
        "rounds_pinball_gap": gap("fixed-rounds"),
        "weeks_tested": str(len(weeks)),
        "weekly_p80": spread("coverage80_session"),
        "weekly_p80_row": spread("coverage80_row"),
        "latest_week": latest["week"],
        "latest_week_p80": f"{latest['coverage80_session']:.0f}%",
        "latest_week_p80_row": f"{latest['coverage80_row']:.0f}%",
        "latest_week_sessions": str(latest["sessions"]),
        "selection_last_week": report["selection"]["last_week"],
        "locked_status": report["locked"]["status"],
        "project_p80": project_p80,
        "contributor_split": contributor,
        "loss_count": str(len(report["losses"])),
        "kind_table": _kind_table(rows),
        "example_output": _example_output(rows, plan_path),
    }


def apply(text: str, known: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in known:
            raise ValueError(f"unknown claim: {name}")
        return f"<!-- claim:{name} -->{known[name]}<!-- /claim -->"

    return _MARKER.sub(replace, text)

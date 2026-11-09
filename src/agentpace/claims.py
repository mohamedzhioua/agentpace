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


def _cell_table(rows: Sequence[Row]) -> str:
    """Durations by task kind, recorded risk and model, in the order the CLI backs off.

    Each `(task_kind, risk)` group lists its pooled row first, then its models; the `all kinds`
    row is last. A row needs `_MIN_TABLE_ROWS` runs to be listed and `est.POOL_MIN` to show a range.
    """
    usable = [row for row in rows if row["outcome"] == "completed" and row["duration_s"] > 0]

    def line(kind: str, risk: str, model: str, chosen: Sequence[Row]) -> str | None:
        if len(chosen) < _MIN_TABLE_ROWS:
            return None
        if len(chosen) < est.POOL_MIN:
            cells = "too few runs | |"
        else:
            values = sorted(row["duration_s"] for row in chosen)
            cells = " | ".join(render.duration(est.quantile(values, q)) for q in est.QUANTILES)
        return f"| {kind} | {risk} | {model} | {len(chosen):,} | {cells} |"

    entries: list[str | None] = []
    for kind, risk in sorted({(row["task_kind"], row["risk"]) for row in usable}):
        group = [row for row in usable if row["task_kind"] == kind and row["risk"] == risk]
        entries.append(line(kind, risk, "all models", group))
        counts: dict[str, int] = {}
        for row in group:
            counts[row["model"]] = counts.get(row["model"], 0) + 1
        for model in sorted(counts, key=lambda name: (-counts[name], name)):
            entries.append(line(kind, risk, model, [r for r in group if r["model"] == model]))
    entries.append(line("all kinds", "all", "all models", usable))
    lines = [
        "| Task kind | Recorded risk | Model | Runs | P50 | P80 | P90 |",
        "|---|---|---|---:|---:|---:|---:|",
        *(entry for entry in entries if entry is not None),
    ]
    return "\n" + "\n".join(lines) + "\n"


def _not_completed_by_harness(rows: Sequence[Row]) -> str:
    """Recorded runs that did not complete (failed or unknown), by harness, from the loaded rows."""
    parts = []
    for harness in est.outcome_summary(rows).by_harness:
        missing = harness.failed + harness.unknown
        parts.append(
            f"{harness.harness} {missing:,} of {harness.rows:,} ({missing / harness.rows:.1%})"
        )
    return ", ".join(parts)


# The timeline the README's quote example re-answers, passed as `--quoted` exactly as given.
QUOTE_EXAMPLE = "3 months"


def _example_output(rows: Sequence[Row], plan_path: Path | None, quoted: str | None = None) -> str:
    """The quickstart's real output, so the README cannot show a result the tool would not give."""
    if plan_path is None or not plan_path.exists():
        return ""
    tasks, cap = est.tasks_from_json(json.loads(plan_path.read_text(encoding="utf-8")))
    return "\n" + render.plan_text(est.estimate(est.fit(rows), tasks, cap), quoted) + "\n"


def values(
    rows: Sequence[Row],
    report: dict[str, Any],
    plan_path: Path | None = None,
    quote_plan_path: Path | None = None,
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
    # Defined by rank, never by fold number, because ranks shift as data is added.
    largest = project.get("without_largest")
    project_top_harness = largest["harness"] if largest else "not available"
    project_top_sessions = (
        f"{largest['sessions']} of {largest['of_sessions']}" if largest else "not available"
    )
    completed = sorted(row["duration_s"] for row in rows if row["outcome"] == "completed")
    found = report["splits"]["time"]["intervals"]
    plan_found = report["plans"]["intervals"]
    # The README describes the pooled selection-period number, so locked weeks stay out of it.
    weeks = [w for w in report["splits"]["time"]["by_week"] if w["period"] == "selection"]
    latest = weeks[-1]
    # Whole sessions with projects held out. Not available when it could not run, or in a report
    # from before it existed.
    held_out = report.get("project_plans", {"status": "absent"})
    project_plans = dict.fromkeys(
        (
            "project_plan_sessions",
            "project_plan_projects",
            "project_plan_p80",
            "project_plan_p90",
            "project_plan_gap",
        ),
        "not available",
    )
    if held_out["status"] == "ok":
        held_plan = held_out["results"]["agentpace"]
        held_found = held_out["intervals"]
        held_low, held_high = held_found["rival_minus_ours"]["sum-of-medians"]
        project_plans = {
            "project_plan_sessions": str(held_out["sessions"]),
            "project_plan_projects": str(held_out["folds"]),
            "project_plan_p80": ci(held_plan["coverage80"], held_found["coverage80"], unit="%"),
            "project_plan_p90": ci(held_plan["coverage90"], held_found["coverage90"], unit="%"),
            "project_plan_gap": f"{held_low:+.0f} to {held_high:+.0f}",
        }

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
        "project_top_harness": project_top_harness,
        "project_top_sessions": project_top_sessions,
        **project_plans,
        "contributor_split": contributor,
        "loss_count": str(len(report["losses"])),
        "not_completed_by_harness": _not_completed_by_harness(rows),
        "cell_table": _cell_table(rows),
        "example_output": _example_output(rows, plan_path),
        "quote_example_output": _example_output(rows, quote_plan_path, QUOTE_EXAMPLE),
    }


def apply(text: str, known: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in known:
            raise ValueError(f"unknown claim: {name}")
        return f"<!-- claim:{name} -->{known[name]}<!-- /claim -->"

    return _MARKER.sub(replace, text)

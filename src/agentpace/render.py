"""Plain-text and JSON views of results. No math here."""

from __future__ import annotations

import dataclasses
import json

from agentpace.estimate import PlanRange


def duration(seconds: float) -> str:
    """Two significant figures: anything finer would suggest precision the data lacks."""
    minutes = seconds / 60
    if seconds < 90:
        return f"{seconds:.0f} s"
    if minutes < 90:
        return f"{minutes:.0f} min"
    hours = minutes / 60
    return f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.1f} days"


def plan_text(plan: PlanRange) -> str:
    width = max(len("task"), *(len(task.id) for task in plan.tasks))
    lines = [
        f"Total wall clock: P50 {duration(plan.p50)}, P80 {duration(plan.p80)}, "
        f"P90 {duration(plan.p90)}",
        "",
        f"{'task':<{width}}  {'P50':>9}  {'P80':>9}  {'P90':>9}  {'critical':>8}  cell (n)",
    ]
    for task in plan.tasks:
        lines.append(
            f"{task.id:<{width}}  {duration(task.p50):>9}  {duration(task.p80):>9}  "
            f"{duration(task.p90):>9}  {task.criticality:>7.0%}  {task.cell} (n={task.n})"
        )
    lines += [
        "",
        f"Critical path: {' -> '.join(plan.critical_path)} "
        f"(longest in {plan.critical_path_share:.0%} of simulations)",
    ]
    if plan.effective_parallelism is None:
        lines.append(f"Effective parallelism at cap {plan.agent_cap}: not learned yet")
    else:
        lines.append(
            f"Effective parallelism at cap {plan.agent_cap}: {plan.effective_parallelism:.2f} "
            f"(from {plan.parallelism_sessions} sessions). Throughput set the total in "
            f"{plan.throughput_bound_share:.0%} of simulations."
        )
    excluded = f", {plan.rows_excluded} not completed so excluded" if plan.rows_excluded else ""
    lines.append(
        f"Method: {plan.method}. {plan.rows_used} runs{excluded}. "
        f"{plan.draws} simulations, seed {plan.seed}."
    )
    if plan.size_used:
        lines.append("Size used: oracle track, stored size is measured after a run.")
    if plan.warnings:
        lines += ["", "Warnings:", *(f"  - {note}" for note in plan.warnings)]
    return "\n".join(lines)


def plan_json(plan: PlanRange) -> str:
    return json.dumps(dataclasses.asdict(plan), indent=2)

"""Plain-text and JSON views of results. No math here."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from agentpace.estimate import HumanPart, PlanRange

HUMAN_PART = "Human part (review, testing, decisions, waiting between agent turns)"
CLOCK_NOTE = (
    "The session clock counts agent runs and the gaps between them, each gap up to the idle cap "
    "(900 s by default), so waiting on you between runs sits inside the total up to that cap."
)


def count(n: int, noun: str, plural: str | None = None) -> str:
    """A count with its noun in agreement: `1 row`, `2 rows`. `plural` defaults to noun + s."""
    return f"{n} {noun if n == 1 else (plural or noun + 's')}"


def human_text(human: HumanPart) -> str:
    """The human block. It names the quantity and its status, and never offers a number derived
    from agent time. A measured form quotes only the user's own waits, per human turn. No
    allowance for the human part is added to the total, but waits up to the idle cap already sit
    inside the session clock, so the measured form says not to add them."""
    p50, p80, p90 = human.per_turn_p50, human.per_turn_p80, human.per_turn_p90
    if p50 is not None and p80 is not None and p90 is not None:
        return (
            f"{HUMAN_PART}: {human.status} from {human.source}. Waiting on you per human turn: "
            f"P50 {duration(p50)}, P80 {duration(p80)}, P90 {duration(p90)} "
            f"({count(human.gaps or 0, 'gap')}, {count(human.sessions or 0, 'session')}); "
            "elapsed time, not effort. "
            "No allowance for it is added to the total above, and up to the idle cap such waits "
            "already sit inside the session clock, so do not add them to it. "
            "Do not derive it from the agent time."
        )
    return (
        f"{HUMAN_PART}: {human.status}. No allowance for it is added to the total above. "
        "State it separately; do not derive it from the agent time."
    )


def review_capacity_text(human: HumanPart) -> str | None:
    """A review capacity the user stated, verbatim and labelled as theirs. Nothing is parsed or
    computed from it: a plan task is an agent run, not a pull request, so no timeline follows."""
    if human.review_capacity_stated is None:
        return None
    return (
        f'Review capacity: "{human.review_capacity_stated}" (stated by you; not measured, not '
        "applied to the total: a plan task is an agent run, not a pull request)"
    )


def duration(seconds: float) -> str:
    """Two significant figures: anything finer would suggest precision the data lacks."""
    minutes = seconds / 60
    if seconds < 90:
        return f"{seconds:.0f} s"
    if minutes < 90:
        return f"{minutes:.0f} min"
    hours = minutes / 60
    return f"{hours:.1f} h" if hours < 48 else f"{hours / 24:.1f} days"


def quote_text(quoted: str) -> str:
    """A timeline someone already gave, verbatim. It is calendar time from an unknown source, so
    it is never converted into agent time, compared with the range or turned into a ratio."""
    return f'Quoted: "{quoted}" (calendar time, as quoted; unverified, not converted)'


def outcomes_text(plan: PlanRange) -> str | None:
    """The loaded runs that did not complete, by harness, counted from every loaded row and never
    per cell. A recorded harness outcome is not task acceptance, and `unknown` is not a failure,
    so both are named apart. None when nothing was counted."""
    summary = plan.outcomes
    if not summary.by_harness:
        return None
    parts = []
    for harness in summary.by_harness:
        missing = harness.failed + harness.unknown
        share = missing / harness.rows if harness.rows else 0.0
        parts.append(
            f"{harness.harness} {missing} of {harness.rows} ({share:.1%}; "
            f"{harness.failed} failed, {harness.unknown} unknown)"
        )
    line = (
        f"Not completed, by harness: {', '.join(parts)}. These are recorded harness outcomes, "
        "not task acceptance, and they are left out of the fit."
    )
    if summary.completed_without_duration:
        without = summary.completed_without_duration
        line += (
            f" {count(without, 'completed row')} with no duration "
            f"{'is' if without == 1 else 'are'} left out too."
        )
    return line


def plan_text(plan: PlanRange, quoted: str | None = None, idle_cap: str | None = None) -> str:
    """The text form. `idle_cap` is the finished `Idle cap:` line, under the quote when there is
    one and directly above the clock note."""
    width = max(len("task"), *(len(task.id) for task in plan.tasks))
    capacity = review_capacity_text(plan.human)
    lines = [
        f"Plan total on the session clock: P50 {duration(plan.p50)}, "
        f"P80 {duration(plan.p80)}, "
        f"P90 {duration(plan.p90)}",
        *([quote_text(quoted)] if quoted is not None else []),
        *([idle_cap] if idle_cap is not None else []),
        CLOCK_NOTE,
        human_text(plan.human),
        *([capacity] if capacity is not None else []),
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
            f"(from {count(plan.parallelism_sessions, 'session')}). Throughput set the total in "
            f"{plan.throughput_bound_share:.0%} of simulations."
        )
    excluded = f", {plan.rows_excluded} not completed so excluded" if plan.rows_excluded else ""
    lines.append(
        f"Method: {plan.method}. {count(plan.rows_used, 'run')}{excluded}. "
        f"{plan.draws} simulations, seed {plan.seed}."
    )
    outcomes = outcomes_text(plan)
    if outcomes is not None:
        lines.append(outcomes)
    if plan.size_used:
        lines.append("Size used: oracle track, stored size is measured after a run.")
    if plan.warnings:
        lines += ["", "Warnings:", *(f"  - {note}" for note in plan.warnings)]
    return "\n".join(lines)


def plan_dict(plan: PlanRange) -> dict[str, Any]:
    data = dataclasses.asdict(plan)
    data["human"] = {key: value for key, value in data["human"].items() if value is not None}
    return data


def plan_json(plan: PlanRange, idle_cap: dict[str, Any] | None = None) -> str:
    """The JSON form. `idle_cap`, when given, is added as one top-level key of that name."""
    data = plan_dict(plan)
    if idle_cap is not None:
        data["idle_cap"] = idle_cap
    return json.dumps(data, indent=2)


def score_text(forecast: dict[str, Any], score: dict[str, Any], source: str) -> str:
    actual = score["actual_s"]
    hits = ", ".join(
        f"P{key[1:]} {'yes' if actual <= forecast[key] else 'no'}" for key in ("p50", "p80", "p90")
    )
    lines = [
        f"Forecast {forecast['id']}: P50 {duration(forecast['p50'])}, "
        f"P80 {duration(forecast['p80'])}, P90 {duration(forecast['p90'])}",
        f"Actual: {duration(actual)} ({source})",
        f"Within: {hits}",
    ]
    if "quoted_s" in forecast:
        ratio = forecast["quoted_s"] / actual
        closer = "closer" if score["quote_closer"] else "not closer"
        lines.append(
            f'Quoted: "{forecast["quoted"]}" (as quoted, calendar time), {ratio:.1f}x the actual, '
            f"{closer} than the P50 by ratio"
        )
    elif "quoted" in forecast:
        lines.append(f'Quoted: "{forecast["quoted"]}", not parseable, so not scored')
    lines.append(f"Recorded score for {forecast['id']}.")
    return "\n".join(lines)


def ledger_summary_text(summary: Any) -> str:
    lines = [f"Your forecasts, n = {summary.scored}, of which {summary.attested} attested by you."]
    if summary.within is None:
        lines.append(f"Not enough scored forecasts (n = {summary.scored}, need 5).")
    else:
        within = ", ".join(f"P{key[1:]} {share:.0%}" for key, share in summary.within.items())
        lines.append(f"Actual within: {within}. Mean pinball loss: {summary.pinball:.0f} s.")
    if summary.quotes:
        if summary.quote_closer is None:
            lines.append(f"Quoted timelines: not enough to rate (n = {summary.quotes}, need 5).")
        else:
            lines.append(
                f"Quoted timelines (n = {summary.quotes}, calendar time as quoted): closer than "
                f"the P50 in {summary.quote_closer:.0%}, median {summary.quote_ratio:.1f}x the "
                "actual."
            )
    lines.append(
        "Actuals are session spans on their own clock (agent runs plus the gaps between them, "
        "each gap up to the idle cap), or figures you attested, as given."
    )
    return "\n".join(lines)

"""Honest evaluation: every forecast is made from strictly earlier or separate data.

Whole sessions move between train and test, because rows inside one session are far from
independent. A random row split would leak them across the line, so it is deliberately absent.

Two tracks exist because stored `size` is measured after a run. The pre-run track is the real
forecast and the headline. The oracle-size track is an upper bound and is labeled as one.

The default method is chosen by a rule fixed before any result was seen: the lowest
session-weighted pinball loss on the time split, with ties inside the bootstrap interval going to
the simpler candidate. Every other number is reported whether it flatters the project or not.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from agentpace import estimate as est
from agentpace.schema import Row

MIN_TRAIN_ROWS = 200
MIN_PROJECT_ROWS = 30
CELL_DRAWS = 4000
PLAN_DRAWS = 1000
BOOTSTRAPS = 2000
SEED = 20261107
MINUTES_PER_ROUND = 3

# Assumptions, not measurements. They stand in for what a person might guess.
HUMAN_HOURS = {
    "bugfix": 4.0,
    "docs": 2.0,
    "feature": 8.0,
    "migration": 16.0,
    "other": 2.0,
    "refactor": 6.0,
    "review": 1.0,
    "tests": 3.0,
}
ROUNDS = {
    "bugfix": 4,
    "docs": 2,
    "feature": 8,
    "migration": 12,
    "other": 3,
    "refactor": 8,
    "review": 2,
    "tests": 4,
}

BASELINES = ("human-guess", "fixed-rounds", "rounds-oracle", "global-median")
ERROR_METRICS = ("mape", "mdape", "male", "pinball")


@dataclass(frozen=True)
class Forecast:
    p50: float
    p80: float
    p90: float
    actual: float
    session: str


@dataclass(frozen=True)
class Fold:
    name: str
    train: list[Row]
    test: list[Row]


def _completed(rows: Sequence[Row]) -> list[Row]:
    return [row for row in rows if row["outcome"] == "completed"]


def _sessions(rows: Sequence[Row]) -> dict[str, list[Row]]:
    groups: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        groups[row["batch"]].append(row)
    return groups


def session_weeks(rows: Sequence[Row]) -> dict[str, str]:
    """A session belongs to the week of its first run, so it never straddles a split."""
    return {
        batch: min(members, key=lambda r: (r["offset_s"], r["week"]))["week"]
        for batch, members in _sessions(rows).items()
    }


def time_folds(rows: Sequence[Row]) -> list[Fold]:
    """Rolling origin: train on all earlier weeks, test on one week."""
    weeks = session_weeks(rows)
    folds = []
    for week in sorted(set(weeks.values())):
        train = [row for row in rows if weeks[row["batch"]] < week]
        test = [row for row in rows if weeks[row["batch"]] == week]
        if len(_completed(train)) >= MIN_TRAIN_ROWS and _completed(test):
            folds.append(Fold(week, train, test))
    return folds


def group_folds(rows: Sequence[Row], field: str, minimum: int) -> list[Fold]:
    """Leave one group out."""
    groups = sorted({row[field] for row in rows if field in row})
    folds = []
    for group in groups:
        test = [row for row in rows if row.get(field) == group]
        train = [row for row in rows if row.get(field) != group]
        if len(test) >= minimum and len(_completed(train)) >= MIN_TRAIN_ROWS:
            folds.append(Fold(group, train, test))
    return folds


def _candidate(method: str, use_size: bool) -> Callable[[Fold, random.Random], list[Forecast]]:
    def predict(fold: Fold, rng: random.Random) -> list[Forecast]:
        model = est.fit(fold.train, method, use_size)
        cache: dict[tuple[str, ...], tuple[float, float, float]] = {}
        forecasts = []
        for row in _completed(fold.test):
            query = {name: row[name] for name in ("task_kind", "risk", "model", "size")}
            key = tuple(query.values())
            if key not in cache:
                cache[key] = est.cell_quantiles(model, query, CELL_DRAWS, rng)
            p50, p80, p90 = cache[key]
            forecasts.append(Forecast(p50, p80, p90, row["duration_s"], row["batch"]))
        return forecasts

    return predict


def _point(guess: Callable[[Row, float], float]) -> Callable[[Fold, random.Random], list[Forecast]]:
    """A single-number baseline. The same number stands in for every quantile."""

    def predict(fold: Fold, _: random.Random) -> list[Forecast]:
        ordered = sorted(row["duration_s"] for row in _completed(fold.train))
        median = est.quantile(ordered, 0.5)
        forecasts = []
        for row in _completed(fold.test):
            value = guess(row, median)
            forecasts.append(Forecast(value, value, value, row["duration_s"], row["batch"]))
        return forecasts

    return predict


def _baselines() -> dict[str, Callable[[Fold, random.Random], list[Forecast]]]:
    return {
        "human-guess": _point(lambda row, _: HUMAN_HOURS[row["task_kind"]] * 3600),
        "fixed-rounds": _point(lambda row, _: ROUNDS[row["task_kind"]] * MINUTES_PER_ROUND * 60.0),
        "rounds-oracle": _point(
            lambda row, _: max(row["tool_calls"], 1) * MINUTES_PER_ROUND * 60.0
        ),
        "global-median": _point(lambda _, median: median),
    }


def _pinball(forecast: Forecast, predicted: float, q: float) -> float:
    gap = forecast.actual - predicted
    return max(q * gap, (q - 1) * gap)


def pinball_sum(forecast: Forecast) -> float:
    return (
        _pinball(forecast, forecast.p50, 0.5)
        + _pinball(forecast, forecast.p80, 0.8)
        + _pinball(forecast, forecast.p90, 0.9)
    )


def _weighted_median(pairs: list[tuple[float, float]]) -> float:
    pairs = sorted(pairs)
    half = sum(weight for _, weight in pairs) / 2
    running = 0.0
    for value, weight in pairs:
        running += weight
        if running >= half:
            return value
    return pairs[-1][0]


def metrics(forecasts: Sequence[Forecast], weights: Sequence[float]) -> dict[str, float]:
    """Accuracy, calibration and sharpness. Width sits beside coverage, because a method can
    earn perfect coverage with absurdly wide ranges."""
    total = sum(weights)

    def mean(values: Sequence[float]) -> float:
        return sum(v * w for v, w in zip(values, weights, strict=True)) / total

    percentage = [abs(f.p50 - f.actual) / f.actual for f in forecasts]
    return {
        "mape": 100 * mean(percentage),
        "mdape": 100 * _weighted_median(list(zip(percentage, weights, strict=True))),
        "male": mean([abs(math.log(f.p50 / f.actual)) for f in forecasts]),
        "coverage50": 100 * mean([float(f.actual <= f.p50) for f in forecasts]),
        "coverage80": 100 * mean([float(f.actual <= f.p80) for f in forecasts]),
        "coverage90": 100 * mean([float(f.actual <= f.p90) for f in forecasts]),
        "pinball": mean([pinball_sum(f) for f in forecasts]),
        "width": _weighted_median(
            [(f.p90 / f.p50, w) for f, w in zip(forecasts, weights, strict=True)]
        ),
    }


def both_weightings(forecasts: Sequence[Forecast]) -> dict[str, dict[str, float]]:
    sizes: dict[str, int] = defaultdict(int)
    for forecast in forecasts:
        sizes[forecast.session] += 1
    return {
        "row": metrics(forecasts, [1.0] * len(forecasts)),
        "session": metrics(forecasts, [1.0 / sizes[f.session] for f in forecasts]),
    }


def session_losses(forecasts: Sequence[Forecast]) -> dict[str, float]:
    """The primary metric, one value per session."""
    groups: dict[str, list[float]] = defaultdict(list)
    for forecast in forecasts:
        groups[forecast.session].append(pinball_sum(forecast))
    return {session: statistics.fmean(values) for session, values in groups.items()}


def difference_interval(
    a: dict[str, float], b: dict[str, float], rng: random.Random
) -> tuple[float, float]:
    """95% cluster bootstrap interval for mean(a) - mean(b), resampling whole sessions."""
    sessions = sorted(a)
    gaps = [a[session] - b[session] for session in sessions]
    means = sorted(statistics.fmean(rng.choices(gaps, k=len(gaps))) for _ in range(BOOTSTRAPS))
    return est.quantile(means, 0.025), est.quantile(means, 0.975)


def select(forecasts: dict[str, list[Forecast]], rng: random.Random) -> dict[str, Any]:
    """Apply the pre-registered rule. `est.METHODS` is ordered simplest first."""
    losses = {method.name: session_losses(forecasts[method.name]) for method in est.METHODS}
    means = {name: statistics.fmean(values.values()) for name, values in losses.items()}
    best = min(means, key=lambda name: means[name])
    intervals = {name: difference_interval(losses[name], losses[best], rng) for name in means}
    winner = next(
        name for name in means if name == best or intervals[name][0] <= 0 <= intervals[name][1]
    )
    return {
        "primary_metric": "session-weighted pinball loss, summed over 0.5, 0.8 and 0.9",
        "loss": means,
        "difference_from_best": {name: list(intervals[name]) for name in means},
        "lowest_loss": best,
        "winner": winner,
    }


def _evaluate(
    folds: Sequence[Fold], use_size: bool, seed: int
) -> tuple[dict[str, list[Forecast]], dict[str, dict[str, dict[str, float]]]]:
    predictors = {m.name: _candidate(m.name, use_size) for m in est.METHODS}
    predictors.update(_baselines())
    forecasts: dict[str, list[Forecast]] = {}
    for name, predict in predictors.items():
        rng = random.Random(f"{seed}:{name}:{use_size}")
        forecasts[name] = [item for fold in folds for item in predict(fold, rng)]
    return forecasts, {name: both_weightings(items) for name, items in forecasts.items()}


def plan_forecasts(folds: Sequence[Fold], method: str, seed: int) -> dict[str, list[Forecast]]:
    """Treat each held-out session as a plan and predict how long it took, end to end.

    Real dependencies are not recorded, so the critical path falls back to the longest run, and
    the observed peak concurrency stands in for the cap. That peak is itself an outcome, so this
    tests the throughput and session terms, not dependency planning.
    """
    ours: list[Forecast] = []
    naive: list[Forecast] = []
    for fold in folds:
        model = est.fit(fold.train, method)
        median = est.quantile(sorted(r["duration_s"] for r in _completed(fold.train)), 0.5)
        for batch, members in sorted(_sessions(_completed(fold.test)).items()):
            if len(members) < est.MIN_SESSION_RUNS:
                continue
            tasks = [
                est.Task(str(i), row["task_kind"], row["risk"], model=row["model"])
                for i, row in enumerate(members)
            ]
            cap = max(row["parallel_agents"] for row in members)
            plan = est.estimate(model, tasks, cap, PLAN_DRAWS, seed)
            first = min(row["offset_s"] for row in members)
            actual = max(row["offset_s"] + row["duration_s"] for row in members) - first
            actual = max(actual, 1)
            ours.append(Forecast(plan.p50, plan.p80, plan.p90, actual, batch))
            guess = median * len(members) / cap
            naive.append(Forecast(guess, guess, guess, actual, batch))
    return {"agentpace": ours, "sum-of-medians": naive}


def _losses(
    scheme: str, track: str, results: dict[str, dict[str, dict[str, float]]], winner: str
) -> list[dict[str, Any]]:
    """Every place a baseline beat the shipped method. Generated, so it cannot be edited out."""
    entries = []
    ours = results[winner]
    for weighting in ("row", "session"):
        for baseline in BASELINES:
            theirs = results[baseline][weighting]
            for metric in ERROR_METRICS:
                if theirs[metric] < ours[weighting][metric]:
                    entries.append(
                        {
                            "split": scheme,
                            "track": track,
                            "weighting": weighting,
                            "metric": metric,
                            "baseline": baseline,
                            "baseline_value": theirs[metric],
                            "agentpace_value": ours[weighting][metric],
                        }
                    )
            gap_theirs = abs(theirs["coverage80"] - 80)
            gap_ours = abs(ours[weighting]["coverage80"] - 80)
            if gap_theirs < gap_ours:
                entries.append(
                    {
                        "split": scheme,
                        "track": track,
                        "weighting": weighting,
                        "metric": "coverage80",
                        "baseline": baseline,
                        "baseline_value": theirs["coverage80"],
                        "agentpace_value": ours[weighting]["coverage80"],
                    }
                )
    return entries


def dataset_summary(rows: Sequence[Row]) -> dict[str, Any]:
    completed = _completed(rows)
    sessions = _sessions(rows)
    largest = sorted((len(members) for members in sessions.values()), reverse=True)
    project_sizes: dict[str, int] = defaultdict(int)
    for row in rows:
        project_sizes[row.get("project", "")] += 1
    return {
        "rows": len(rows),
        "completed": len(completed),
        "contributors": len({row["contributor"] for row in rows}),
        "projects": len({row["project"] for row in rows if "project" in row}),
        "sessions": len(sessions),
        "weeks": sorted({row["week"] for row in rows}),
        "top_project_share": 100 * max(project_sizes.values()) / len(rows),
        "top_three_session_share": 100 * sum(largest[:3]) / len(rows),
    }


def run(rows: Sequence[Row], seed: int = SEED) -> dict[str, Any]:
    """Everything `docs/backtest.md` reports."""
    usable = [dict(row) for row in rows]
    for row in usable:
        row.setdefault("project", "")
    time = time_folds(usable)
    if not time:
        raise ValueError("not enough weeks of data for a time split")
    forecasts, pre_run = _evaluate(time, use_size=False, seed=seed)
    selection = select(forecasts, random.Random(seed))
    winner = selection["winner"]
    _, oracle = _evaluate(time, use_size=True, seed=seed)
    report: dict[str, Any] = {
        "seed": seed,
        "dataset": dataset_summary(rows),
        "selection": selection,
        "splits": {
            "time": {
                "status": "ok",
                "folds": [fold.name for fold in time],
                "rows": len(forecasts[winner]),
                "tracks": {"pre-run": pre_run, "oracle-size": oracle},
            }
        },
    }
    report_losses = _losses("time", "pre-run", pre_run, winner)
    report_losses += _losses("time", "oracle-size", oracle, winner)

    projects = group_folds(usable, "project", MIN_PROJECT_ROWS)
    projects = [fold for fold in projects if fold.name]
    if len(projects) >= 2:
        held_out, project_results = _evaluate(projects, use_size=False, seed=seed)
        report["splits"]["project"] = {
            "status": "ok",
            "folds": len(projects),
            "rows": len(held_out[winner]),
            "tracks": {"pre-run": project_results},
        }
        report_losses += _losses("project", "pre-run", project_results, winner)
    else:
        report["splits"]["project"] = {"status": "could not run (fewer than 2 usable projects)"}

    contributors = len({row["contributor"] for row in rows})
    if contributors >= 2:
        groups = group_folds(usable, "contributor", 1)
        held_out, contributor_results = _evaluate(groups, use_size=False, seed=seed)
        report["splits"]["contributor"] = {
            "status": "ok",
            "folds": len(groups),
            "rows": len(held_out[winner]),
            "tracks": {"pre-run": contributor_results},
        }
        report_losses += _losses("contributor", "pre-run", contributor_results, winner)
    else:
        report["splits"]["contributor"] = {"status": f"could not run ({contributors} contributor)"}

    plans = plan_forecasts(time, winner, seed)
    report["plans"] = {
        "label": "oracle-structure",
        "sessions": len(plans["agentpace"]),
        "results": {name: both_weightings(items)["row"] for name, items in plans.items()},
    }
    ours = report["plans"]["results"]["agentpace"]
    theirs = report["plans"]["results"]["sum-of-medians"]
    for metric in ("mape", "mdape", "male"):
        if theirs[metric] < ours[metric]:
            report_losses.append(
                {
                    "split": "time",
                    "track": "whole sessions",
                    "weighting": "session",
                    "metric": metric,
                    "baseline": "sum-of-medians",
                    "baseline_value": theirs[metric],
                    "agentpace_value": ours[metric],
                }
            )
    report["losses"] = report_losses
    return report

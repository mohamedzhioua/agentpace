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

# Frozen. Selection uses time folds up to this week and never re-runs on later ones. Later weeks
# form the locked holdout, which scores the shipped method on data it was never chosen on.
SELECTION_LAST_WEEK = "2026-W45"
LOCKED_MIN_SESSIONS = 20  # sessions with at least est.MIN_SESSION_RUNS completed runs

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


def session_means(
    forecasts: Sequence[Forecast], value: Callable[[Forecast], float]
) -> dict[str, float]:
    """One value per session, so a session-weighted metric is the plain mean of these."""
    groups: dict[str, list[float]] = defaultdict(list)
    for forecast in forecasts:
        groups[forecast.session].append(value(forecast))
    return {session: statistics.fmean(values) for session, values in groups.items()}


def session_losses(forecasts: Sequence[Forecast]) -> dict[str, float]:
    """The primary metric, one value per session."""
    return session_means(forecasts, pinball_sum)


def _covered(level: str) -> Callable[[Forecast], float]:
    return lambda f: 100.0 * (f.actual <= getattr(f, level))


def difference_interval(
    a: dict[str, float], b: dict[str, float], rng: random.Random
) -> tuple[float, float]:
    """95% cluster bootstrap interval for mean(a) - mean(b), resampling whole sessions."""
    sessions = sorted(a)
    gaps = [a[session] - b[session] for session in sessions]
    means = sorted(statistics.fmean(rng.choices(gaps, k=len(gaps))) for _ in range(BOOTSTRAPS))
    return est.quantile(means, 0.025), est.quantile(means, 0.975)


def mean_interval(values: dict[str, float], rng: random.Random) -> list[float]:
    """95% cluster bootstrap interval for the mean of one value per session."""
    return list(difference_interval(values, dict.fromkeys(values, 0.0), rng))


def intervals(
    forecasts: dict[str, list[Forecast]], winner: str, rivals: Sequence[str], rng: random.Random
) -> dict[str, Any]:
    """Session-bootstrap intervals for the shipped method's numbers, and for each rival's
    pinball loss minus ours. A gap interval above zero means the rival really is worse."""
    ours = forecasts[winner]
    losses = session_losses(ours)
    result: dict[str, Any] = {
        level: mean_interval(session_means(ours, _covered(f"p{level[-2:]}")), rng)
        for level in ("coverage50", "coverage80", "coverage90")
    }
    result["pinball"] = mean_interval(losses, rng)
    result["rival_minus_ours"] = {
        name: list(difference_interval(session_losses(forecasts[name]), losses, rng))
        for name in rivals
    }
    return result


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


def _forecast_folds(
    folds: Sequence[Fold], use_size: bool, seed: int
) -> dict[str, list[list[Forecast]]]:
    """Forecasts per method and per fold. Each method draws from one stream across the folds in
    order, so appending later folds never changes the forecasts for earlier ones."""
    predictors = {m.name: _candidate(m.name, use_size) for m in est.METHODS}
    predictors.update(_baselines())
    forecasts: dict[str, list[list[Forecast]]] = {}
    for name, predict in predictors.items():
        rng = random.Random(f"{seed}:{name}:{use_size}")
        forecasts[name] = [predict(fold, rng) for fold in folds]
    return forecasts


def _pooled(per_fold: dict[str, list[list[Forecast]]], picked: range) -> dict[str, list[Forecast]]:
    return {name: [item for i in picked for item in folds[i]] for name, folds in per_fold.items()}


def _evaluate(
    folds: Sequence[Fold], use_size: bool, seed: int
) -> tuple[dict[str, list[Forecast]], dict[str, dict[str, dict[str, float]]]]:
    forecasts = _pooled(_forecast_folds(folds, use_size, seed), range(len(folds)))
    return forecasts, {name: both_weightings(items) for name, items in forecasts.items()}


def by_week(
    folds: Sequence[Fold], per_fold: dict[str, list[list[Forecast]]], winner: str, last: str
) -> list[dict[str, Any]]:
    """The shipped method fold by fold, because a pooled coverage can hide weeks that miss in
    both directions."""
    weeks = []
    for i, fold in enumerate(folds):
        ours = both_weightings(per_fold[winner][i])
        median = both_weightings(per_fold["global-median"][i])["session"]
        weeks.append(
            {
                "week": fold.name,
                "period": "selection" if fold.name <= last else "locked",
                "sessions": len({f.session for f in per_fold[winner][i]}),
                "rows": len(per_fold[winner][i]),
                "coverage80_session": ours["session"]["coverage80"],
                "coverage80_row": ours["row"]["coverage80"],
                "width": ours["session"]["width"],
                "pinball": ours["session"]["pinball"],
                "median_pinball": median["pinball"],
            }
        )
    return weeks


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


def _locked_holdout(
    locked: Sequence[Fold],
    forecasts: dict[str, list[Forecast]],
    winner: str,
    seed: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Score the shipped method on weeks after selection. Nothing here feeds back into it."""
    plans = plan_forecasts(locked, winner, seed)
    qualifying = len(plans["agentpace"])
    base: dict[str, Any] = {
        "folds": [fold.name for fold in locked],
        "min_sessions": LOCKED_MIN_SESSIONS,
        "qualifying_sessions": qualifying,
    }
    if qualifying < LOCKED_MIN_SESSIONS:
        status = (
            f"not enough data ({qualifying} of {LOCKED_MIN_SESSIONS} sessions with "
            f"{est.MIN_SESSION_RUNS}+ runs)"
        )
        return {**base, "status": status}, []
    names = [winner, *BASELINES]
    results = {name: both_weightings(forecasts[name]) for name in names}
    rng = random.Random(f"{seed}:locked-intervals")
    locked_result = {
        **base,
        "status": "ok",
        "rows": len(forecasts[winner]),
        "tracks": {"pre-run": results},
        "intervals": intervals(forecasts, winner, BASELINES, rng),
        "plans": {name: both_weightings(items)["row"] for name, items in plans.items()},
    }
    return locked_result, _losses("locked", "pre-run", results, winner)


def _plan_losses(split: str, results: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    ours = results["agentpace"]
    theirs = results["sum-of-medians"]
    return [
        {
            "split": split,
            "track": "whole sessions",
            "weighting": "session",
            "metric": metric,
            "baseline": "sum-of-medians",
            "baseline_value": theirs[metric],
            "agentpace_value": ours[metric],
        }
        for metric in ("mape", "mdape", "male", "pinball")
        if theirs[metric] < ours[metric]
    ]


def run(
    rows: Sequence[Row], seed: int = SEED, selection_last_week: str = SELECTION_LAST_WEEK
) -> dict[str, Any]:
    """Everything `docs/backtest.md` reports."""
    usable = [dict(row) for row in rows]
    for row in usable:
        row.setdefault("project", "")
    every_week = time_folds(usable)
    time = [fold for fold in every_week if fold.name <= selection_last_week]
    locked = [fold for fold in every_week if fold.name > selection_last_week]
    if not time:
        raise ValueError("not enough weeks of data for a time split")
    per_fold = _forecast_folds([*time, *locked], use_size=False, seed=seed)
    forecasts = _pooled(per_fold, range(len(time)))
    pre_run = {name: both_weightings(items) for name, items in forecasts.items()}
    selection = select(forecasts, random.Random(seed))
    selection["last_week"] = selection_last_week
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
                "intervals": intervals(
                    forecasts, winner, BASELINES, random.Random(f"{seed}:intervals")
                ),
                "by_week": by_week([*time, *locked], per_fold, winner, selection_last_week),
            }
        },
    }
    report_losses = _losses("time", "pre-run", pre_run, winner)
    report_losses += _losses("time", "oracle-size", oracle, winner)
    if locked:
        locked_forecasts = _pooled(per_fold, range(len(time), len(time) + len(locked)))
        report["locked"], locked_losses = _locked_holdout(locked, locked_forecasts, winner, seed)
    else:
        report["locked"] = {
            "status": f"not enough data (no weeks after {selection_last_week} yet)",
            "folds": [],
            "min_sessions": LOCKED_MIN_SESSIONS,
            "qualifying_sessions": 0,
        }
        locked_losses = []

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
        "intervals": intervals(
            plans, "agentpace", ["sum-of-medians"], random.Random(f"{seed}:plan-intervals")
        ),
    }
    report_losses += _plan_losses("time", report["plans"]["results"])
    if report["locked"]["status"] == "ok":
        locked_losses += _plan_losses("locked", report["locked"]["plans"])
    report["losses"] = report_losses + locked_losses
    return report

"""A local ledger of forecasts, so a user can later see how often their own estimates held.

`estimate --record` appends one forecast record to `~/.agentpace/forecasts.jsonl`. Scores are
appended later as separate records, never edited in place. The file is local only: `share` never
reads it. It may hold free text, such as task ids and a quoted human-scale timeline, but no file
paths, no data sources and nothing derived from the local secret.

A quote such as "3 weeks" is calendar time as someone said it. It is stored as given, and turned
into seconds only when it parses exactly, by fixed units. No other conversion is ever applied.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

Record = dict[str, Any]

FORECAST, SCORE = "forecast", "score"
_UNITS = {
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86_400,
    "day": 86_400,
    "days": 86_400,
    "w": 7 * 86_400,
    "week": 7 * 86_400,
    "weeks": 7 * 86_400,
    "mo": 30 * 86_400,
    "month": 30 * 86_400,
    "months": 30 * 86_400,
}
_DURATION = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(min|minutes?|h|hours?|d|days?|w|weeks?|mo|months?)\s*$",
    re.IGNORECASE,
)


def parse_duration(text: str) -> int | None:
    """Seconds for an exact "<number> <unit>", by fixed units (1 d = 86,400 s, 1 w = 7 d,
    1 mo = 30 d). Anything else is None, never a guess."""
    match = _DURATION.match(text)
    if match is None:
        return None
    seconds = float(match.group(1)) * _UNITS[match.group(2).lower()]
    return round(seconds) if seconds >= 1 else None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical(record: Mapping[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def record_id(record: Mapping[str, Any]) -> str:
    """Derived from the content and the time it was issued, so no random draw is needed."""
    body = {key: value for key, value in record.items() if key != "id"}
    return hashlib.sha256((record["issued_at"] + _canonical(body)).encode("utf-8")).hexdigest()[:8]


def forecast_record(
    *,
    issued_at: str,
    version: str,
    plan_document: Mapping[str, Any],
    result: Mapping[str, Any],
    quoted: str | None,
) -> Record:
    """`result` is the dict `render.plan_json` prints, so the ledger holds what the user saw."""
    record: Record = {
        "kind": FORECAST,
        "issued_at": issued_at,
        "version": version,
        "method": result["method"],
        "seed": result["seed"],
        "draws": result["draws"],
        "rows_used": result["rows_used"],
        "rows_excluded": result["rows_excluded"],
        "plan": plan_document,
        "p50": result["p50"],
        "p80": result["p80"],
        "p90": result["p90"],
        "tasks": [
            {"id": t["id"], "p50": t["p50"], "p80": t["p80"], "p90": t["p90"], "cell": t["cell"]}
            for t in result["tasks"]
        ],
        "human": result["human"],
    }
    if quoted is not None:
        record["quoted"] = quoted
        seconds = parse_duration(quoted)
        if seconds is not None:
            record["quoted_s"] = seconds
    record["id"] = record_id(record)
    return record


def append(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical(record) + "\n")


@dataclass
class Ledger:
    forecasts: dict[str, Record] = field(default_factory=dict)
    scores: list[Record] = field(default_factory=list)
    skipped: int = 0


def _valid(record: Any) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("id"), str):
        return False
    if record.get("kind") == FORECAST:
        return all(isinstance(record.get(key), (int, float)) for key in ("p50", "p80", "p90"))
    if record.get("kind") == SCORE:
        return isinstance(record.get("actual_s"), int)
    return False


def read(path: Path) -> Ledger:
    """Every valid record. A damaged or unknown line is skipped and counted, never fatal."""
    ledger = Ledger()
    if not path.exists():
        return ledger
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                record = None
            if not _valid(record):
                ledger.skipped += 1
            elif record["kind"] == FORECAST:
                ledger.forecasts[record["id"]] = record
            else:
                ledger.scores.append(record)
    return ledger


def latest_scores(scores: Sequence[Record]) -> dict[str, Record]:
    """The latest score per forecast wins, so a correction never needs an edit in place."""
    latest: dict[str, Record] = {}
    for score in scores:
        latest[score["id"]] = score
    return latest


# Scoring. A score compares a forecast with what happened. With a session, the actual is agent
# active time on that session's own clock (idle gaps over the idle cap clipped), the same quantity
# the forecast predicts and the backtest scores. It is not wall clock, and the human part is
# outside it. With `--actual`, the user attests the figure themselves.

MIN_RATED = 5
QUANTILE_KEYS = (("p50", 0.5), ("p80", 0.8), ("p90", 0.9))


class ScoreRefused(ValueError):
    """A score that cannot be made honestly, with the reason as its message."""


def parse_issued(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def iso_week_of(epoch: float) -> str:
    year, week, _ = datetime.fromtimestamp(epoch, tz=timezone.utc).isocalendar()
    return f"{year}-W{week:02d}"


def session_span(rows: Sequence[Mapping[str, Any]]) -> int:
    """`max(offset + duration) - min(offset)` on the session clock, as in the backtest."""
    end = max(int(row["offset_s"]) + int(row["duration_s"]) for row in rows)
    return max(1, end - min(int(row["offset_s"]) for row in rows))


def _quote_closer(forecast: Mapping[str, Any], actual_s: int) -> bool | None:
    """Whether the quoted timeline was closer to the actual than the P50, by ratio."""
    quoted = forecast.get("quoted_s")
    if not isinstance(quoted, int) or quoted <= 0 or forecast["p50"] <= 0 or actual_s <= 0:
        return None
    return abs(math.log(quoted / actual_s)) < abs(math.log(forecast["p50"] / actual_s))


def score_record(
    forecast: Mapping[str, Any],
    *,
    actual_s: int,
    source: str,
    verified: bool,
    scored_at: str,
    session: str | None = None,
) -> Record:
    record: Record = {
        "kind": SCORE,
        "id": forecast["id"],
        "scored_at": scored_at,
        "actual_s": actual_s,
        "source": source,
        "verified": verified,
        "quote_closer": _quote_closer(forecast, actual_s),
    }
    if session is not None:
        record["session"] = session
    return record


def check_session(
    forecast: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], first_start: float | None
) -> int:
    """The actual for a session score, or a refusal. A session scores only when it provably
    started after the forecast was issued."""
    if len(rows) < 2:
        raise ScoreRefused(
            "that session has fewer than 2 completed local runs; run `agentpace ingest`"
        )
    issued = parse_issued(forecast["issued_at"])
    if min(row["week"] for row in rows) < iso_week_of(issued):
        raise ScoreRefused("that session ran before the forecast was issued, so it cannot score it")
    if first_start is None:
        raise ScoreRefused(
            "cannot verify this session started after the forecast; use --actual to attest it"
        )
    if first_start < issued:
        raise ScoreRefused("that session started before the forecast was issued")
    return session_span(rows)


def _pinball(actual: float, predicted: float, q: float) -> float:
    gap = actual - predicted
    return max(q * gap, (q - 1) * gap)


@dataclass(frozen=True)
class Summary:
    scored: int
    attested: int
    within: dict[str, float] | None
    pinball: float | None
    quotes: int
    quote_closer: float | None
    quote_ratio: float | None


def summarize(ledger: Ledger) -> Summary:
    pairs = [
        (ledger.forecasts[key], score)
        for key, score in latest_scores(ledger.scores).items()
        if key in ledger.forecasts
    ]
    attested = sum(not score["verified"] for _, score in pairs)
    within = pinball = None
    if len(pairs) >= MIN_RATED:
        within = {
            key: sum(score["actual_s"] <= forecast[key] for forecast, score in pairs) / len(pairs)
            for key, _ in QUANTILE_KEYS
        }
        pinball = sum(
            sum(_pinball(score["actual_s"], forecast[key], q) for key, q in QUANTILE_KEYS)
            for forecast, score in pairs
        ) / len(pairs)
    quoted = [(f, s) for f, s in pairs if isinstance(f.get("quoted_s"), int)]
    closer = ratio = None
    if len(quoted) >= MIN_RATED:
        closer = sum(bool(s["quote_closer"]) for _, s in quoted) / len(quoted)
        ratio = statistics.median(f["quoted_s"] / s["actual_s"] for f, s in quoted)
    return Summary(len(pairs), attested, within, pinball, len(quoted), closer, ratio)

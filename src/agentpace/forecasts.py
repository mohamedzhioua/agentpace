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
import re
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

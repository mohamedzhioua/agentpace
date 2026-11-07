"""Turn `RawRun` records into normalized rows, one code path for every harness."""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agentpace import classify, pseudonym, schema
from agentpace.adapters.base import Adapter, ParseStats, RawRun
from agentpace.schema import Row

DEFAULT_IDLE_CAP_S = 900

_FAMILIES = (
    re.compile(r"claude-(?:opus|sonnet|haiku|fable)-\d+(?:-\d{1,2})?(?!\d)"),
    re.compile(r"claude-\d(?:-\d)?-(?:opus|sonnet|haiku)"),
    re.compile(r"gpt-\d+(?:\.\d+)?(?:-[a-z]+)?"),
    re.compile(r"gemini-\d(?:\.\d)?-[a-z]+"),
    re.compile(r"(?<![a-z0-9])o\d(?:-[a-z]+)?(?![a-z0-9])"),
)


def normalize_model(raw: str) -> str:
    """Keep a public model family. ARNs and proxy names can leak account or customer names."""
    lowered = raw.lower()
    for family in _FAMILIES:
        match = family.search(lowered)
        if match:
            return match.group(0)
    return "other"


def iso_week(epoch: float) -> str:
    year, week, _ = datetime.fromtimestamp(epoch, tz=timezone.utc).isocalendar()
    return f"{year}-W{week:02d}"


@dataclass
class IngestReport:
    files: int = 0
    rows: int = 0
    zero_duration: int = 0
    parse: ParseStats = field(default_factory=ParseStats)

    def lines(self) -> list[str]:
        parse = self.parse
        return [
            f"files seen:       {self.files}",
            f"rows produced:    {self.rows}",
            f"empty runs:       {parse.empty_runs}",
            f"unreadable files: {parse.unreadable_files}",
            f"unparsed lines:   {parse.unparsed_lines}",
            f"zero-duration:    {self.zero_duration}",
        ]


class SessionClock:
    """Maps real time onto a session's own clock, with idle gaps clipped."""

    def __init__(self, runs: Sequence[RawRun], idle_cap_s: int) -> None:
        merged: list[list[float]] = []
        for start, end in sorted(span for run in runs for span in run.spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        self._starts = [start for start, _ in merged]
        self._ends = [end for _, end in merged]
        self._elapsed = [0.0]
        for i in range(1, len(merged)):
            busy = self._ends[i - 1] - self._starts[i - 1]
            gap = self._starts[i] - self._ends[i - 1]
            self._elapsed.append(self._elapsed[-1] + busy + min(gap, idle_cap_s))

    def at(self, time: float) -> int:
        index = max(bisect_right(self._starts, time) - 1, 0)
        return round(self._elapsed[index] + max(time - self._starts[index], 0.0))


def peak_overlaps(runs: Sequence[RawRun]) -> dict[int, int]:
    """Peak number of runs alive at once during each run, including itself."""
    events = sorted(
        (time, order, index)
        for index, run in enumerate(runs)
        for time, order in ((run.start, 0), (run.end, 1))
    )
    peaks = dict.fromkeys(range(len(runs)), 1)
    live: set[int] = set()
    for _, order, index in events:
        if order == 1:
            live.discard(index)
            continue
        live.add(index)
        for member in live:
            peaks[member] = max(peaks[member], len(live))
    return peaks


def load_labels(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    labels: dict[str, dict[str, str]] = json.loads(path.read_text(encoding="utf-8"))
    return labels


def apply_labels(row: Row, labels: Mapping[str, Mapping[str, str]]) -> None:
    label = labels.get(run_ref(row))
    if label:
        row["task_kind"] = label.get("kind", row["task_kind"])
        row["risk"] = label.get("risk", row["risk"])


def run_ref(row: Row) -> str:
    """A short handle for labeling one run."""
    return f"{row['batch']}:{row['offset_s']}"


def build_rows(
    runs: Sequence[RawRun],
    secret: bytes,
    idle_cap_s: int = DEFAULT_IDLE_CAP_S,
    kind_map: Mapping[str, str] | None = None,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[list[Row], int]:
    """Return schema-valid rows, plus how many runs had zero duration and were dropped."""
    sessions: dict[tuple[str, str], list[RawRun]] = defaultdict(list)
    for run in runs:
        sessions[(run.project, run.session)].append(run)
    contributor = pseudonym.contributor(secret)
    rows: list[Row] = []
    dropped = 0
    for (project, session), members in sessions.items():
        clock = SessionClock(members, idle_cap_s)
        peaks = peak_overlaps(members)
        batch = pseudonym.batch(secret, session)
        project_hash = pseudonym.project(secret, project)
        taken: set[tuple[int, int]] = set()
        order = sorted(range(len(members)), key=lambda i: (members[i].start, members[i].end))
        for index in order:
            run = members[index]
            if run.active_s < 1:
                dropped += 1
                continue
            # Agents launched together can tie on (start, duration) to the second. A one-second
            # nudge keeps both runs apart under the duplicate key, deterministically.
            offset = clock.at(run.start)
            while (offset, run.active_s) in taken:
                offset += 1
            taken.add((offset, run.active_s))
            kind = classify.classify_kind(run.agent_type, kind_map)
            row: Row = {
                "harness": run.harness,
                "model": normalize_model(run.model),
                "task_kind": kind,
                "risk": classify.classify_risk(run.agent_type, kind),
                "size": classify.size_bucket(run.edit_calls),
                "duration_s": run.active_s,
                "tool_calls": run.tool_calls,
                "retries": run.errors,
                "outcome": run.outcome,
                "parallel_agents": peaks[index],
                "contributor": contributor,
                "week": iso_week(run.start),
                "batch": batch,
                "project": project_hash,
                "offset_s": offset,
            }
            if run.tokens is not None:
                row["tokens"] = run.tokens
            if labels:
                apply_labels(row, labels)
            problems = schema.check_row(row)
            if problems:
                raise ValueError(f"adapter produced an invalid row: {problems}")
            rows.append(row)
    return rows, dropped


def ingest(
    adapter: Adapter,
    path: Path,
    secret: bytes,
    idle_cap_s: int = DEFAULT_IDLE_CAP_S,
    kind_map: Mapping[str, str] | None = None,
    labels: Mapping[str, Mapping[str, str]] | None = None,
) -> tuple[list[Row], IngestReport]:
    report = IngestReport()
    runs: list[RawRun] = []
    for source in adapter.discover(path):
        report.files += 1
        run = adapter.parse(source, idle_cap_s, report.parse)
        if run is not None:
            runs.append(run)
    rows, report.zero_duration = build_rows(runs, secret, idle_cap_s, kind_map, labels)
    report.rows = len(rows)
    return rows, report


def merge_into(path: Path, rows: Sequence[Row]) -> int:
    """Add rows to a store. Old rows survive, since harnesses delete their transcripts."""
    existing = schema.read_rows(path) if path.exists() else []
    merged = {schema.dedupe_key(row): row for row in [*existing, *rows]}
    return schema.write_rows(path, (merged[key] for key in sorted(merged)))

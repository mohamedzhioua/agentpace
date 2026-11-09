"""Waiting on the human, measured from main-session timestamps. Local only.

A main Claude Code session alternates between agent turns and human prompts. The time from the
agent's last record to the next prompt a person typed is a wait on the human: review, testing,
decisions and everything else between turns, breaks included. It is elapsed time, not effort.

Main sessions are never a source of run rows. This module reads them for this one quantity, and
copies four things out of each parsed line: the record type, its timestamp, its `turnOrigin`
(only whether it says a human typed the prompt) and whether it carries a tool result. Nothing it
writes reaches `share`, the dataset or a run row.
"""

from __future__ import annotations

import json
import os
import statistics
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentpace import estimate as est
from agentpace import ingest, paths, pseudonym, render, schema
from agentpace.adapters.claude_code import _extended, _parse_time

Turn = dict[str, Any]

HOUR_S = 3600
_ASSISTANT, _HUMAN, _OTHER = "assistant", "human", "other"


@dataclass(frozen=True)
class _Mark:
    time: float
    kind: str


@dataclass(frozen=True)
class Gap:
    time: float
    gap_s: int
    agent_turn_s: int


@dataclass
class TurnsReport:
    files: int = 0
    sessions: int = 0
    gaps: int = 0
    no_origin: int = 0
    too_short: int = 0
    unreadable: int = 0
    kept_from_earlier: int = 0

    def lines(self) -> list[str]:
        lines = [
            f"read {render.count(self.files, 'main-session transcript')}: "
            f"{render.count(self.gaps, 'gap')} in {render.count(self.sessions, 'session')}"
        ]
        if self.no_origin:
            lines.append(
                f"skipped {render.count(self.no_origin, 'session')} with no turnOrigin, so human "
                "prompts cannot be told apart from other records"
            )
        if self.too_short:
            lines.append(
                f"skipped {render.count(self.too_short, 'session')} with fewer than 2 human prompts"
            )
        if self.unreadable:
            lines.append(f"could not read {render.count(self.unreadable, 'file')}")
        if self.kept_from_earlier:
            lines.append(
                f"kept {render.count(self.kept_from_earlier, 'gap')} from sessions whose "
                "transcripts are gone"
            )
        return lines


def _schema() -> dict[str, Any]:
    return schema.load_schema_file(paths.turn_schema_file())


def check_turn(turn: Any) -> list[str]:
    return schema.check_against(_schema(), turn)


def _project(record: Any) -> tuple[str, float, bool | None] | None:
    """Copy the allowed values out of a parsed line: the only code that touches one.

    Returns the mark kind, its time, and whether the line carried a `turnOrigin` key at all
    (None for assistant records, where it does not apply)."""
    if not isinstance(record, dict):
        return None
    role = record.get("type")
    time = _parse_time(record.get("timestamp"))
    if time is None:
        return None
    if role == "assistant":
        return (_ASSISTANT, time, None)
    if role != "user":
        return None
    message = record.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    ):
        return (_OTHER, time, None)
    origin = record.get("turnOrigin")
    return (_HUMAN if origin == "human" else _OTHER, time, "turnOrigin" in record)


def gaps(marks: Sequence[_Mark]) -> list[Gap]:
    """Each human prompt after an agent record opens a gap, paired with the agent turn before
    it."""
    found: list[Gap] = []
    last_agent: float | None = None
    previous_prompt: float | None = None
    for mark in sorted(marks, key=lambda m: m.time):
        if mark.kind == _ASSISTANT:
            last_agent = mark.time
        elif mark.kind == _HUMAN:
            if last_agent is not None:
                turn = 0.0
                if previous_prompt is not None and last_agent >= previous_prompt:
                    turn = last_agent - previous_prompt
                found.append(Gap(mark.time, round(mark.time - last_agent), round(turn)))
            previous_prompt = mark.time
    return found


@dataclass
class _Session:
    marks: list[_Mark] = field(default_factory=list)
    has_origin: bool = False


def _read(path: str) -> _Session:
    session = _Session()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            projected = _project(record)
            if projected is None:
                continue
            kind, time, carries_origin = projected
            session.marks.append(_Mark(time, kind))
            session.has_origin = session.has_origin or bool(carries_origin)
    return session


def discover(root: Path) -> Iterator[tuple[str, str]]:
    """Main-session transcripts sit directly in each project folder as `<session>.jsonl`."""
    base = _extended(root)
    try:
        projects = sorted(os.scandir(base), key=lambda entry: entry.name)
    except OSError:
        return
    for project in projects:
        if not project.is_dir():
            continue
        try:
            files = sorted(os.scandir(project.path), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in files:
            if entry.is_file() and entry.name.endswith(".jsonl"):
                yield entry.path, entry.name[: -len(".jsonl")]


def collect(root: Path, secret: bytes) -> tuple[list[Turn], set[str], TurnsReport]:
    report = TurnsReport()
    turns: list[Turn] = []
    seen: set[str] = set()
    for path, session_id in discover(root):
        report.files += 1
        try:
            session = _read(path)
        except (OSError, UnicodeDecodeError):
            report.unreadable += 1
            continue
        if not session.has_origin:
            report.no_origin += 1
            continue
        if sum(mark.kind == _HUMAN for mark in session.marks) < 2:
            report.too_short += 1
            continue
        pseudonym_id = pseudonym.turn_session(secret, session_id)
        seen.add(pseudonym_id)
        found = gaps(session.marks)
        if found:
            report.sessions += 1
        for gap in found:
            turns.append(
                {
                    "session": pseudonym_id,
                    "week": ingest.iso_week(gap.time),
                    "gap_s": gap.gap_s,
                    "agent_turn_s": gap.agent_turn_s,
                }
            )
    report.gaps = len(turns)
    return turns, seen, report


def read_turns(path: Path) -> list[Turn]:
    if not path.exists():
        return []
    turns = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                turn = json.loads(line)
            except ValueError:
                turn = None
            if turn is None or check_turn(turn):
                raise ValueError(
                    f"line {number} of {path.name} is not a valid turn; run agentpace turns"
                )
            turns.append(turn)
    return turns


def write_turns(path: Path, turns: Sequence[Turn]) -> None:
    """Atomic, like the run store, so an interrupted write cannot leave half a file."""
    order = ("session", "week", "gap_s", "agent_turn_s")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for turn in turns:
            handle.write(json.dumps({key: turn[key] for key in order}, separators=(",", ":")))
            handle.write("\n")
    os.replace(temporary, path)


def update(root: Path, store: Path, secret: bytes) -> TurnsReport:
    """Replace the gaps of every session read in this pass and keep the rest, because Claude
    Code deletes old transcripts."""
    fresh, seen, report = collect(root, secret)
    kept = [turn for turn in read_turns(store) if turn["session"] not in seen]
    report.kept_from_earlier = len(kept)
    for turn in fresh:
        problems = check_turn(turn)
        if problems:
            raise ValueError(f"a turn failed its schema: {problems}")
    write_turns(store, kept + fresh)
    return report


@dataclass(frozen=True)
class Summary:
    gaps: int
    sessions: int
    p50: float
    p80: float
    p90: float
    over_hour: float
    waiting_share: float


def summarize(turns: Sequence[Turn]) -> Summary | None:
    """Uncapped quantiles of the wait per human turn, with no constant applied."""
    if not turns:
        return None
    values = sorted(float(turn["gap_s"]) for turn in turns)
    totals: dict[str, list[float]] = {}
    for turn in turns:
        waiting, working = totals.setdefault(turn["session"], [0.0, 0.0])
        totals[turn["session"]] = [waiting + turn["gap_s"], working + turn["agent_turn_s"]]
    shares = [w / (w + a) for w, a in totals.values() if w + a > 0]
    p50, p80, p90 = (est.quantile(values, q) for q in est.QUANTILES)
    return Summary(
        gaps=len(values),
        sessions=len(totals),
        p50=p50,
        p80=p80,
        p90=p90,
        over_hour=sum(value > HOUR_S for value in values) / len(values),
        waiting_share=statistics.median(shares) if shares else 0.0,
    )

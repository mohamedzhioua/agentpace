"""The idle cap a rows file was filled with, kept in a note beside it.

`--idle-cap` moves a run's `duration_s` and `offset_s`, and both are in the duplicate key, so a
run ingested under two caps would be stored twice. A rows file therefore keeps one cap, recorded
by `ingest` in `<file>.clock.json` before its first rows. The note holds the cap and nothing
else; it is local and never shared.

A rows file with rows and no note predates the note. It counts as 900 s when every row in it is
a published row, since a row equal in key to a published one is on the published clock, and as
not recorded otherwise. Nothing here guesses a cap from the rows themselves.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from agentpace import ingest, paths, schema

DEFAULT = ingest.DEFAULT_IDLE_CAP_S
VERSION = 1
SUFFIX = ".clock.json"
REPAIR = "https://github.com/mohamedzhioua/agentpace/blob/main/docs/cli.md#the-idle-cap"

EMPTY = "empty"
RECORDED = "recorded"
PUBLISHED = "published rows"
NOT_RECORDED = "not recorded"

SEED = "bundled seed"
LOCAL = "your local store"
DATA = "--data rows"

Key = tuple[str, str, int, int]


@dataclass(frozen=True)
class StoreCap:
    """What a rows file says about its idle cap. `seconds` is None when it holds no row or its
    cap is not recorded."""

    state: str
    seconds: int | None

    @property
    def note_due(self) -> bool:
        """Whether an ingest into it writes the note: no row yet, or only published rows."""
        return self.state in (EMPTY, PUBLISHED)


def note_path(rows_file: Path) -> Path:
    return rows_file.with_name(rows_file.name + SUFFIX)


def note_text(seconds: int) -> str:
    return json.dumps({"version": VERSION, "idle_cap_s": seconds}) + "\n"


def write_note(rows_file: Path, seconds: int) -> None:
    """Write the note atomically. Only `ingest` calls this, under the store lock."""
    note = note_path(rows_file)
    note.parent.mkdir(parents=True, exist_ok=True)
    temporary = note.with_name(note.name + ".tmp")
    temporary.write_bytes(note_text(seconds).encode("utf-8"))
    os.replace(temporary, note)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def read_note(rows_file: Path) -> int:
    """The cap a readable note records. Anything else is refused with I4."""
    note = note_path(rows_file)
    try:
        data = json.loads(note.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if (
        isinstance(data, dict)
        and set(data) == {"version", "idle_cap_s"}
        and _is_int(data["version"])
        and data["version"] == VERSION
        and _is_int(data["idle_cap_s"])
        and data["idle_cap_s"] >= 1
    ):
        seconds: int = data["idle_cap_s"]
        return seconds
    raise ValueError(
        f"{note.name} is not a clock record this version can read. Nothing was changed. "
        "Update agentpace, or delete that file, after which the idle cap counts as not recorded"
    )


def published_keys() -> set[Key]:
    """Every run key in the bundled seed."""
    seed = paths.seed_dir()
    if not seed.exists():
        return set()
    return {schema.dedupe_key(row) for row in schema.read_dataset([seed])}


def store_cap(
    rows_file: Path,
    rows: Sequence[schema.Row] | None = None,
    published: Callable[[], Collection[Key]] = published_keys,
) -> StoreCap:
    """The cap of a rows file. A note beside a file with no row is ignored. The bundled seed is
    read only when the file holds rows and has no note."""
    if rows is None:
        rows = schema.read_rows(rows_file) if rows_file.exists() else []
    if not rows:
        return StoreCap(EMPTY, None)
    if note_path(rows_file).exists():
        return StoreCap(RECORDED, read_note(rows_file))
    keys = published()
    if all(schema.dedupe_key(row) in keys for row in rows):
        return StoreCap(PUBLISHED, DEFAULT)
    return StoreCap(NOT_RECORDED, None)


# Ingest.


def check_cap(seconds: int) -> None:
    if seconds < 1:
        raise ValueError("--idle-cap must be 1 or more seconds")


def admit(cap: StoreCap, seconds: int, name: str) -> None:
    """Refuse an ingest at `seconds` into a rows file named `name` that cannot take it."""
    if cap.state == RECORDED and cap.seconds != seconds:
        raise ValueError(
            f"{name} was filled with a {cap.seconds} s idle cap, and this ingest asked for "
            f"{seconds} s. Nothing was ingested. A store keeps one cap; for another, pass --out "
            "with a new file"
        )
    if cap.state in (PUBLISHED, NOT_RECORDED) and seconds != DEFAULT:
        raise ValueError(
            f"{name} has no recorded idle cap, so only the default, {DEFAULT} s, can be added to "
            f"it, and this ingest asked for {seconds} s. Nothing was ingested. For another cap, "
            "pass --out with a new file"
        )


def not_default_warning(seconds: int) -> str:
    return (
        f"warning: idle cap is {seconds} s, not the default {DEFAULT} s. The published dataset "
        f"uses {DEFAULT} s, so these rows cannot be shared, and an estimate that pools them with "
        "the bundled seed mixes two clocks."
    )


def not_recorded_note(name: str) -> str:
    return (
        f"note: {name} has no recorded idle cap: it was filled before agentpace kept one. share "
        f"refuses such a store. To record the cap, rebuild it: {REPAIR}"
    )


# Share and score.


def check_shareable(cap: StoreCap, name: str) -> None:
    """Refuse to share a store that is not known to be on the published clock."""
    if cap.state == RECORDED and cap.seconds != DEFAULT:
        raise ValueError(
            f"{name} was filled with a {cap.seconds} s idle cap, and the published dataset uses "
            f"{DEFAULT} s, so its rows cannot be shared. Nothing was written. To contribute, "
            "rename it and run agentpace ingest at the default cap"
        )
    if cap.state == NOT_RECORDED:
        raise ValueError(
            f"{name} has no recorded idle cap, so share cannot tell whether its rows are on the "
            f"published {DEFAULT} s clock. Nothing was written. Rebuild the store first: {REPAIR}"
        )


def session_note(cap: StoreCap, name: str) -> str | None:
    """A note for `score --session` when the store's cap is not the recorded default."""
    if cap.state == NOT_RECORDED:
        return (
            f"note: {name} has no recorded idle cap, so this session's span may not be on the "
            "forecast's clock."
        )
    if cap.state == RECORDED and cap.seconds != DEFAULT:
        return (
            f"note: {name} was filled with a {cap.seconds} s idle cap, so this session's span is "
            "on that clock, which may not be the forecast's."
        )
    return None


# Estimate.


@dataclass(frozen=True)
class Source:
    """One rows file, or the bundled seed, that took part in an estimate, and its cap."""

    label: str
    seconds: int | None


@dataclass(frozen=True)
class Caps:
    """The caps behind an estimate. The `Idle cap:` line and the `idle_cap` JSON key are both
    built from this, and both are shown only when a source other than the bundled seed took
    part."""

    sources: tuple[Source, ...]

    @property
    def shown(self) -> bool:
        return any(source.label != SEED for source in self.sources)

    def parts(self) -> list[tuple[int | None, list[str]]]:
        """One part per distinct cap, in order of first appearance, with its labels in order."""
        parts: dict[int | None, list[str]] = {}
        for source in self.sources:
            labels = parts.setdefault(source.seconds, [])
            if source.label not in labels:
                labels.append(source.label)
        return list(parts.items())

    def line(self) -> str:
        parts = self.parts()
        text = "Idle cap: " + ", ".join(
            f"{'not recorded' if seconds is None else f'{seconds} s'} ({', '.join(labels)})"
            for seconds, labels in parts
        )
        text += "."
        recorded = [seconds for seconds, _ in parts if seconds is not None]
        if len(recorded) >= 2:
            text += " This range pools rows on different clocks."
        elif recorded and len(recorded) < len(parts):
            text += " The rows with no recorded cap may be on another clock."
        return text

    def as_dict(self) -> dict[str, Any]:
        parts = self.parts()
        return {
            "seconds": parts[0][0] if len(parts) == 1 else None,
            "sources": [
                {"source": label, "seconds": seconds}
                for seconds, labels in parts
                for label in labels
            ],
        }


def estimate_caps(data: Sequence[str]) -> Caps:
    """The caps of the rows an estimate reads: every `--data` file in order, a folder's `*.jsonl`
    by name, or else the bundled seed and the local store. A file with no row is skipped."""
    published = cache(published_keys)
    sources: list[Source] = []
    if data:
        for source in map(Path, data):
            files = sorted(source.glob("*.jsonl")) if source.is_dir() else [source]
            for file in files:
                rows = schema.read_rows(file)
                if rows:
                    sources.append(Source(DATA, store_cap(file, rows, published).seconds))
        return Caps(tuple(sources))
    if paths.seed_dir().exists():
        sources.append(Source(SEED, DEFAULT))
    store = paths.local_runs()
    rows = schema.read_rows(store) if store.exists() else []
    if rows:
        sources.append(Source(LOCAL, store_cap(store, rows, published).seconds))
    return Caps(tuple(sources))

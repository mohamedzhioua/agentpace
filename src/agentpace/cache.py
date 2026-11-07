"""Remember parsed runs, so a second ingest reads only what changed.

A cold ingest of a few thousand transcripts takes minutes, because every line has to be decoded.
The cache maps each transcript path to the size and modification time of the transcript and of
its companion files, such as Claude Code's `.meta.json`, and to the `RawRun` parsed from them. A
run is parsed again when any of those stamps changes. An unfinished run can only change by
writing to one of those files, so it needs no special case.

The cache also keeps runs whose transcript has since been deleted. Claude Code removes old
transcripts, and a session's surviving runs take their offsets from every sibling's spans, so
losing an early sibling would shift the rest and duplicate them in the store.

Privacy: the file stays in the local agentpace folder, beside the secret, and is never shared. It
holds only `RawRun` fields, with the model already normalized, plus the transcript path. Folder
names and agent types sit there as they sit in the transcripts it summarizes.
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from typing import Any

from agentpace.adapters.base import RawRun

FORMAT = 3  # 2: keys are os.path.normcase paths. 3: each entry holds a list of runs

# [size, mtime_ns] for the transcript and each companion file, or None for a missing companion.
Stamps = list[list[int] | None]


def _key(path: str) -> str:
    """One key per file. Windows paths are case-insensitive, so `c:\\` and `C:\\` must match, or a
    deleted transcript would come back twice."""
    return os.path.normcase(path)


def _to_json(run: RawRun) -> dict[str, Any]:
    return dataclasses.asdict(run)


def _from_json(data: dict[str, Any]) -> RawRun:
    spans = tuple((float(start), float(end)) for start, end in data["spans"])
    return RawRun(**{**data, "spans": spans})


def _entry_runs(entry: dict[str, Any]) -> list[RawRun]:
    return [_from_json(run) for run in entry["runs"]]


class RunCache:
    def __init__(self, path: Path, adapter: str, adapter_version: int, idle_cap_s: int) -> None:
        self.path = path
        self._header = {
            "format": FORMAT,
            "adapter": adapter,
            "adapter_version": adapter_version,
            "idle_cap_s": idle_cap_s,
        }
        self.entries: dict[str, dict[str, Any]] = {}
        self.rebuilt = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            if {key: stored.get(key) for key in self._header} != self._header:
                raise ValueError("cache was written by another version")
            entries = stored["entries"]
            for entry in entries.values():
                if not isinstance(entry["stamps"], list) or not isinstance(entry["runs"], list):
                    raise ValueError("cache entry has no stamps or runs")
                _entry_runs(entry)  # reject anything that no longer fits a RawRun
            self.entries = entries
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.entries = {}
            self.rebuilt = True

    def lookup(self, path: str, stamps: Stamps) -> tuple[bool, list[RawRun]]:
        """Return (hit, runs). A hit needs the transcript and every companion file unchanged."""
        entry = self.entries.get(_key(path))
        if entry is None or entry["stamps"] != stamps:
            return False, []
        return True, _entry_runs(entry)

    def store(self, path: str, stamps: Stamps, runs: list[RawRun]) -> None:
        self.entries[_key(path)] = {"stamps": stamps, "runs": [_to_json(run) for run in runs]}

    def vanished(self, sessions: set[tuple[str, str]], seen: set[str]) -> list[RawRun]:
        """Cached runs of these sessions whose transcript no longer exists."""
        runs = []
        seen = {_key(path) for path in seen}
        for path, entry in self.entries.items():
            if path in seen:
                continue
            found = [run for run in _entry_runs(entry) if (run.project, run.session) in sessions]
            if found and not os.path.exists(path):
                runs.extend(found)
        return runs

    def runs(self) -> list[RawRun]:
        """Every cached run, including runs whose transcript is gone."""
        return [run for entry in self.entries.values() for run in _entry_runs(entry)]

    def save(self) -> None:
        """Write atomically, so a reader never sees half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        document = {**self._header, "entries": self.entries}
        temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self.path)

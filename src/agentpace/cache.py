"""Remember parsed runs, so a second ingest reads only what changed.

A cold ingest of a few thousand transcripts takes minutes, because every line has to be decoded.
The cache maps each transcript path to its size, its modification time and the `RawRun` parsed
from it. A file is parsed again when either stamp changes, or when its run had not completed,
since an unfinished run may still be going.

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

FORMAT = 1


def _to_json(run: RawRun | None) -> dict[str, Any] | None:
    return None if run is None else dataclasses.asdict(run)


def _from_json(data: dict[str, Any] | None) -> RawRun | None:
    if data is None:
        return None
    spans = tuple((float(start), float(end)) for start, end in data["spans"])
    return RawRun(**{**data, "spans": spans})


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
                _from_json(entry["run"])  # reject anything that no longer fits a RawRun
            self.entries = entries
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.entries = {}
            self.rebuilt = True

    def lookup(self, path: str, size: int, mtime_ns: int) -> tuple[bool, RawRun | None]:
        """Return (hit, run). Only an unchanged file whose run completed, or was empty, hits."""
        entry = self.entries.get(path)
        if entry is None or entry["size"] != size or entry["mtime_ns"] != mtime_ns:
            return False, None
        run = _from_json(entry["run"])
        if run is not None and run.outcome != "completed":
            return False, None
        return True, run

    def store(self, path: str, size: int, mtime_ns: int, run: RawRun | None) -> None:
        self.entries[path] = {"size": size, "mtime_ns": mtime_ns, "run": _to_json(run)}

    def vanished(self, sessions: set[tuple[str, str]], seen: set[str]) -> list[RawRun]:
        """Cached runs of these sessions whose transcript no longer exists."""
        runs = []
        for path, entry in self.entries.items():
            run = _from_json(entry["run"])
            if run is None or path in seen or (run.project, run.session) not in sessions:
                continue
            if not os.path.exists(path):
                runs.append(run)
        return runs

    def save(self) -> None:
        """Write atomically, so a reader never sees half a file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        document = {**self._header, "entries": self.entries}
        temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, self.path)

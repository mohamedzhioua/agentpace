"""Where agentpace finds its files.

Installed wheels carry the schema and seed rows under `_bundled`. A source checkout reads the
same files from the repo root, so there is never a second copy to drift.
"""

from __future__ import annotations

import os
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE.parent.parent


def home() -> Path:
    """Directory for the local secret, labels and ingested rows."""
    override = os.environ.get("AGENTPACE_HOME")
    return Path(override) if override else Path.home() / ".agentpace"


def schema_file() -> Path:
    bundled = _PACKAGE / "_bundled" / "run.schema.json"
    return bundled if bundled.exists() else _REPO_ROOT / "schema" / "run.schema.json"


def turn_schema_file() -> Path:
    bundled = _PACKAGE / "_bundled" / "turn.schema.json"
    return bundled if bundled.exists() else _REPO_ROOT / "schema" / "turn.schema.json"


def seed_dir() -> Path:
    bundled = _PACKAGE / "_bundled" / "runs"
    return bundled if bundled.exists() else _REPO_ROOT / "data" / "runs"


def local_runs() -> Path:
    return home() / "runs.jsonl"


def turns_file() -> Path:
    """Waits on the human, from main sessions. Local only, never shared."""
    return home() / "turns.jsonl"


def labels_file() -> Path:
    return home() / "labels.json"


def cache_file(adapter: str) -> Path:
    return home() / f"cache-{adapter}.json"

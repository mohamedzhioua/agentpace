"""Row validation against `schema/run.schema.json`, without third-party packages.

This implements only the JSON Schema keywords that file uses. An unknown keyword raises instead
of being ignored, so the schema cannot quietly gain a rule that nothing enforces.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from agentpace import paths

Row = dict[str, Any]

_ANNOTATIONS = {"description", "title", "$schema", "$id"}
_KNOWN = _ANNOTATIONS | {
    "type",
    "enum",
    "pattern",
    "minimum",
    "required",
    "properties",
    "additionalProperties",
}


@lru_cache(maxsize=4)
def load_schema_file(path: Path) -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for node in [schema, *schema["properties"].values()]:
        unknown = set(node) - _KNOWN
        if unknown:
            raise ValueError(f"unsupported schema keywords: {sorted(unknown)}")
    return schema


def load_schema() -> dict[str, Any]:
    return load_schema_file(paths.schema_file())


def fields() -> tuple[str, ...]:
    """Row keys in canonical order."""
    return tuple(load_schema()["properties"])


def enum(field: str) -> tuple[str, ...]:
    return tuple(load_schema()["properties"][field]["enum"])


def _check_value(name: str, value: Any, rule: dict[str, Any]) -> str | None:
    if "enum" in rule and value not in rule["enum"]:
        return f"{name}: not an allowed value"
    expected = rule.get("type")
    if expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        return f"{name}: must be an integer"
    if expected == "string" and not isinstance(value, str):
        return f"{name}: must be a string"
    if "minimum" in rule and isinstance(value, int) and value < rule["minimum"]:
        return f"{name}: below minimum {rule['minimum']}"
    if "pattern" in rule and isinstance(value, str) and not re.fullmatch(rule["pattern"], value):
        return f"{name}: does not match the required pattern"
    return None


def check_row(row: Any) -> list[str]:
    """Return every schema violation. Messages name fields, never echo values."""
    return check_against(load_schema(), row)


def check_against(schema: dict[str, Any], row: Any) -> list[str]:
    """`check_row` for any schema this module can read."""
    if not isinstance(row, dict):
        return ["row is not a JSON object"]
    properties = schema["properties"]
    errors = [f"{name}: missing" for name in schema["required"] if name not in row]
    errors += [f"unknown field #{i}" for i, name in enumerate(row) if name not in properties]
    for name, rule in properties.items():
        if name in row:
            problem = _check_value(name, row[name], rule)
            if problem:
                errors.append(problem)
    return errors


def canonical(row: Row) -> str:
    """One row as one line of JSON, in schema field order."""
    ordered = {name: row[name] for name in fields() if name in row}
    return json.dumps(ordered, separators=(",", ":"), ensure_ascii=True)


def dedupe_key(row: Row) -> tuple[str, str, int, int]:
    """Identifies a run, not its labels, so a relabeled re-share is still a duplicate."""
    return (row["contributor"], row["batch"], row["offset_s"], row["duration_s"])


def read_rows(path: Path) -> list[Row]:
    """Every row in `path`. A bad line is named by file and line number, never by its content."""
    rows: list[Row] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                raise ValueError(f"{path.name}:{number}: not valid JSON") from None
            if not isinstance(row, dict):
                raise ValueError(f"{path.name}:{number}: not a JSON object")
            rows.append(row)
    return rows


def read_dataset(sources: Iterable[Path]) -> list[Row]:
    """Load rows from files or directories of `.jsonl`, keeping one row per run."""
    merged: dict[tuple[str, str, int, int], Row] = {}
    for source in sources:
        files = sorted(source.glob("*.jsonl")) if source.is_dir() else [source]
        for file in files:
            for row in read_rows(file):
                merged[dedupe_key(row)] = row
    return [merged[key] for key in sorted(merged)]


def write_rows(path: Path, rows: Iterable[Row]) -> int:
    """Write atomically. A hook can be killed at its timeout, and a half-written store would make
    every later read fail."""
    lines = [canonical(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(line + "\n" for line in lines)
    os.replace(temporary, path)
    return len(lines)

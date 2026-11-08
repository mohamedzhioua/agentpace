"""Checks every data file must pass, locally and in CI.

Contributed files are parsed, never executed. Findings name a file, a line and a rule, and never
quote the offending value, because a CI log is public and the value may be the very thing that
should not be published.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agentpace import ingest, schema
from agentpace.adapters import EXPERIMENTAL

MAX_DURATION_S = 86_400
MAX_CALLS_PER_SECOND = 5
MAX_FILE_BYTES = 20_000_000

_PII = {
    "email address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "url": re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE),
    "windows path": re.compile(r"[A-Za-z]:[\\/]"),
    "unix path": re.compile(r"/(?:home|Users|root|var|tmp|etc|opt|mnt|workspace)/"),
    "ip address": re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])"),
    "api key": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{8,})"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
}


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.rule}"


def scan_for_pii(text: str) -> list[str]:
    """Names of the PII shapes present. Runs over raw text, before any parsing."""
    return [name for name, pattern in _PII.items() if pattern.search(text)]


def check_values(row: schema.Row) -> list[str]:
    """Rules a schema cannot express."""
    problems: list[str] = []
    if row["harness"] in EXPERIMENTAL:
        problems.append(
            "harness adapter is experimental: rows are not published until it is verified"
        )
    if ingest.normalize_model(row["model"]) != row["model"]:
        problems.append("model is not a known public family or 'other'")
    if row["duration_s"] > MAX_DURATION_S:
        problems.append("outlier: duration over 24 hours")
    if row["tool_calls"] > row["duration_s"] * MAX_CALLS_PER_SECOND:
        problems.append("outlier: implausible tool calls per second")
    if row["outcome"] == "completed" and row["tool_calls"] == 0 and not row.get("tokens"):
        problems.append("outlier: completed with no activity")
    return problems


def _check_file(path: Path, seen: dict[tuple[str, str, int, int], str]) -> list[Finding]:
    name = path.name
    if path.suffix != ".jsonl":
        return [Finding(name, 0, "data files must end in .jsonl")]
    if path.stat().st_size > MAX_FILE_BYTES:
        return [Finding(name, 0, "file is too large")]
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return [Finding(name, 0, "file is not UTF-8")]
    findings: list[Finding] = []
    contributors: set[str] = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        for shape in scan_for_pii(line):
            findings.append(Finding(name, number, f"looks like PII: {shape}"))
        try:
            row = json.loads(line)
        except ValueError:
            findings.append(Finding(name, number, "not valid JSON"))
            continue
        problems = schema.check_row(row)
        if problems:
            findings += [Finding(name, number, problem) for problem in problems]
            continue
        findings += [Finding(name, number, problem) for problem in check_values(row)]
        key = schema.dedupe_key(row)
        if key in seen:
            findings.append(Finding(name, number, f"duplicate of a run in {seen[key]}"))
        seen[key] = name
        contributors.add(row["contributor"])
    if len(contributors) > 1:
        findings.append(Finding(name, 0, "a file must hold one contributor"))
    elif contributors and not name.startswith(next(iter(contributors))):
        findings.append(Finding(name, 0, "file name must start with its contributor"))
    return findings


def validate(sources: Sequence[Path]) -> list[Finding]:
    files: list[Path] = []
    for source in sources:
        files += sorted(p for p in source.iterdir() if p.is_file()) if source.is_dir() else [source]
    seen: dict[tuple[str, str, int, int], str] = {}
    findings: list[Finding] = []
    for path in files:
        if path.name != ".gitkeep":
            findings += _check_file(path, seen)
    return findings

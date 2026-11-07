"""Prepare rows for contribution. Local only: this module writes a file and nothing else.

There is deliberately no network code here or anywhere else in the package. Sending the file is a
git push the contributor runs themselves, after reading exactly what it holds.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from agentpace import schema
from agentpace.schema import Row

NOTICE = """\
What these rows reveal, even without any content:
  - Rows from you link to each other through `contributor`.
  - Rows from one session link through `batch`, and `project` groups a local project.
    Use --drop-project to omit it.
  - `week` and the mix of models show roughly when you worked and on what plan tier.
  - A pull request ties this pseudonym to your GitHub identity in public history.
  - Submitting a data pull request dedicates these rows to the public domain under CC0-1.0.
"""


def select(
    rows: Iterable[Row],
    since: str | None = None,
    drop_project: bool = False,
    already_published: Iterable[Row] = (),
) -> tuple[list[Row], int]:
    """Choose what to share. Returns the rows and how many were already published."""
    known = {schema.dedupe_key(row) for row in already_published}
    chosen: list[Row] = []
    skipped = 0
    for row in rows:
        if since and row["week"] < since:
            continue
        if schema.dedupe_key(row) in known:
            skipped += 1
            continue
        kept = {key: value for key, value in row.items() if key != "project" or not drop_project}
        problems = schema.check_row(kept)
        if problems:
            raise ValueError(f"refusing to share an invalid row: {problems}")
        chosen.append(kept)
    return sorted(chosen, key=schema.dedupe_key), skipped


def file_name(rows: Sequence[Row]) -> str:
    weeks = sorted(row["week"] for row in rows)
    return f"{rows[0]['contributor']}-{weeks[0]}-{weeks[-1]}.jsonl"


def git_commands(path: Path) -> list[str]:
    return [
        f'git add "{path}"',
        'git commit -m "data: add runs"',
        "git push origin HEAD",
        "Then open a pull request.",
    ]

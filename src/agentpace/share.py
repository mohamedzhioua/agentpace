"""Prepare rows for contribution. Local only: this module writes a file and nothing else.

There is deliberately no network code here or anywhere else in the package. Sending the file is a
git push the contributor runs themselves, after reading exactly what it holds.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

from agentpace import schema
from agentpace.adapters import EXPERIMENTAL
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
    drop_sessions: Iterable[str] = (),
) -> tuple[list[Row], int, dict[str, int]]:
    """Choose what to share. Returns the rows, how many were already published, and the rows
    left out per dropped session.

    A dropped session is withheld whole, before anything else is decided, so the file name and
    the published count see only the rows actually shared. Every dropped session must match a
    local row: a mistyped one would otherwise share the session it meant to withhold.
    """
    known = {schema.dedupe_key(row) for row in already_published}
    dropped = dict.fromkeys(drop_sessions, 0)
    chosen: list[Row] = []
    skipped = 0
    for row in rows:
        if row["batch"] in dropped:
            dropped[row["batch"]] += 1
            continue
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
    unmatched = sorted(batch for batch, count in dropped.items() if not count)
    if unmatched:
        raise ValueError(
            f"--drop-session names {len(unmatched)} sessions with no local run "
            f"({', '.join(unmatched)}); nothing was written. See `agentpace runs`"
        )
    return sorted(chosen, key=schema.dedupe_key), skipped, dropped


def file_name(rows: Sequence[Row]) -> str:
    weeks = sorted(row["week"] for row in rows)
    return f"{rows[0]['contributor']}-{weeks[0]}-{weeks[-1]}.jsonl"


def held_rows(path: Path, rows: Sequence[Row]) -> int | None:
    """How many rows `path` already holds, or None when it does not exist yet.

    Refuses a file that holds anyone else's rows: a contribution file belongs to one contributor.
    """
    if not path.exists():
        return None
    contributor = rows[0]["contributor"]
    old = schema.read_rows(path)
    if any(row.get("contributor") != contributor for row in old):
        raise ValueError(
            f"refusing to add rows to {path.name}: it holds another contributor's rows"
        )
    return len(old)


def write_contribution(path: Path, rows: Sequence[Row]) -> tuple[int, int]:
    """Create `path`, or extend it. Returns (rows added, rows it already held).

    An existing file keeps its bytes exactly; the new rows go after them. `select` has already
    left out every published run, so an appended row is never a duplicate.
    """
    if not rows:
        raise ValueError("nothing to write")
    existing = held_rows(path, rows) or 0
    old = path.read_bytes() if path.exists() else b""
    if old and not old.endswith(b"\n"):
        old += b"\n"
    new = "".join(schema.canonical(row) + "\n" for row in rows).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(old + new)
    os.replace(temporary, path)
    return len(rows), existing


def experimental_note(rows: Iterable[Row]) -> str | None:
    """A warning when rows from unverified adapters are in the share. They stay in it."""
    harnesses = [row["harness"] for row in rows if row["harness"] in EXPERIMENTAL]
    if not harnesses:
        return None
    names = ", ".join(sorted(set(harnesses)))
    return (
        f"{len(harnesses)} rows come from experimental adapters ({names}). validate rejects them"
        " until the adapter is verified, so a pull request with them stays red until a maintainer"
        " reconciles them by hand; see docs/verify-adapter.md."
    )


def git_commands(path: Path) -> list[str]:
    return [
        f'agentpace validate "{path.parent}"',
        f'git add "{path}"',
        'git commit -m "data: add runs"',
        "git push origin HEAD",
        "Then open a pull request.",
    ]

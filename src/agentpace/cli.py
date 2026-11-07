"""Command line interface."""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from agentpace import (
    __version__,
    backtest,
    claims,
    estimate,
    ingest,
    paths,
    pseudonym,
    render,
    report,
    schema,
    share,
    site,
    validate,
)
from agentpace.adapters import ADAPTERS, get_adapter
from agentpace.cache import RunCache


def _kind_map(pairs: Sequence[str]) -> dict[str, str]:
    allowed = schema.enum("task_kind")
    mapping: dict[str, str] = {}
    for pair in pairs:
        name, separator, kind = pair.partition("=")
        if not separator or kind not in allowed:
            raise ValueError(f"--kind-map expects name=kind, with kind one of {allowed}")
        mapping[name] = kind
    return mapping


def _ingest_into_store(
    args: argparse.Namespace, path: Path, wait_s: float
) -> tuple[ingest.IngestReport, int]:
    adapter = get_adapter(args.source)
    out = Path(args.out) if args.out else paths.local_runs()
    with ingest.store_lock(out, wait_s=wait_s):
        secret = pseudonym.load_secret(paths.home())
        labels = ingest.load_labels(paths.labels_file())
        # A non-default idle cap changes durations and keys, so it neither reads nor feeds the
        # cache.
        cache = (
            RunCache(paths.cache_file(adapter.name), adapter.name, adapter.version, args.idle_cap)
            if args.idle_cap == ingest.DEFAULT_IDLE_CAP_S
            else None
        )
        rows, report = ingest.ingest(
            adapter, path, secret, args.idle_cap, _kind_map(args.kind_map), labels, cache=cache
        )
        return report, ingest.merge_into(out, rows)


def hook_session(payload_text: str) -> Path | None:
    """The session folder a SubagentStop payload points at, or None.

    Only `agent_transcript_path` is taken from the payload. Other fields, such as the agent's
    last message, are content and are dropped unread. The whole session is ingested, not the one
    file, because a run's offset comes from every sibling run in its session.
    """
    try:
        payload = json.loads(payload_text)
    except ValueError:
        return None
    value = payload.get("agent_transcript_path") if isinstance(payload, dict) else None
    del payload
    if not isinstance(value, str) or not value:
        return None
    parts = Path(value).parts
    if "subagents" not in parts:
        return None
    anchor = parts.index("subagents")
    if anchor < 3:  # needs a root, a project and a session above it
        return None
    folder = Path(*parts[:anchor])
    return folder if folder.is_dir() else None


def _ingest_hook(args: argparse.Namespace) -> int:
    """Run from a SubagentStop hook. It must never block or fail the agent it runs after.

    So it always exits 0, prints nothing, and gives up quietly when the store is busy: the next
    ingest picks the run up, since the cache re-reads anything that changed.
    """
    with suppress(Exception):  # a hook that raised would show its error in the user's session
        folder = hook_session(sys.stdin.read())
        if folder is not None:
            _ingest_into_store(args, folder, wait_s=5.0)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    if args.hook:
        return _ingest_hook(args)
    adapter = get_adapter(args.source)
    path = Path(args.path) if args.path else adapter.default_path()
    if not path.exists():
        raise ValueError("the transcript path does not exist")
    try:
        report, total = _ingest_into_store(args, path, wait_s=30.0)
    except ingest.StoreBusy as busy:
        raise ValueError(
            f"another ingest is writing the store. Try again, or delete {busy} if none is running"
        ) from None
    for line in report.lines():
        print(line, file=sys.stderr)
    print(f"store now holds:  {total} rows", file=sys.stderr)
    return 0


def cmd_runs(args: argparse.Namespace) -> int:
    rows = schema.read_rows(paths.local_runs()) if paths.local_runs().exists() else []
    if not rows:
        raise ValueError("no local rows yet; run `agentpace ingest` first")
    for row in sorted(rows, key=lambda r: (r["week"], r["batch"], r["offset_s"]))[-args.last :]:
        print(
            f"{ingest.run_ref(row)}  {row['week']}  {row['model']:<20} {row['task_kind']:<10}"
            f"{row['risk']:<7}{row['size']:<3} {row['duration_s']:>6}s  {row['outcome']}"
        )
    return 0


def cmd_label(args: argparse.Namespace) -> int:
    if args.kind is None and args.risk is None:
        raise ValueError("give --kind, --risk or both")
    store = paths.local_runs()
    rows = schema.read_rows(store) if store.exists() else []
    if args.ref == "last" and rows:
        latest = max(rows, key=lambda r: (r["week"], r["batch"], r["offset_s"]))
        ref = ingest.run_ref(latest)
    else:
        ref = args.ref
    matches = [row for row in rows if ingest.run_ref(row) == ref]
    if not matches:
        raise ValueError("no local run has that reference; see `agentpace runs`")
    labels = ingest.load_labels(paths.labels_file())
    label = labels.setdefault(ref, {})
    if args.kind:
        label["kind"] = args.kind
    if args.risk:
        label["risk"] = args.risk
    paths.labels_file().write_text(json.dumps(labels, indent=2) + "\n", encoding="utf-8")
    for row in matches:
        ingest.apply_labels(row, labels)
    schema.write_rows(store, rows)
    print(f"labeled {ref}: {matches[0]['task_kind']}, {matches[0]['risk']} risk")
    return 0


def _dataset(sources: Sequence[str]) -> list[schema.Row]:
    """Explicit sources, or else the bundled seed rows plus your own ingested rows."""
    if sources:
        chosen = [Path(source) for source in sources]
        for path in chosen:
            if not path.exists():
                raise ValueError("a --data path does not exist")
    else:
        chosen = [path for path in (paths.seed_dir(), paths.local_runs()) if path.exists()]
    rows = schema.read_dataset(chosen)
    if not rows:
        raise ValueError("no runs found; run `agentpace ingest` or pass --data")
    return rows


def cmd_estimate(args: argparse.Namespace) -> int:
    text = sys.stdin.read() if args.plan == "-" else Path(args.plan).read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("the plan is not valid JSON") from None
    tasks, cap = estimate.tasks_from_json(document)
    model = estimate.fit(_dataset(args.data), args.method, args.use_size)
    plan = estimate.estimate(model, tasks, cap, args.draws, args.seed)
    print(render.plan_json(plan) if args.json else render.plan_text(plan))
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    rows = _dataset(args.data or [str(paths.seed_dir())])
    results = backtest.run(rows, args.seed)
    out = Path(args.out)
    if args.check:
        if report.is_current(results, out):
            print("backtest numbers match the published report")
            return 0
        print("backtest numbers differ from the published report", file=sys.stderr)
        return 1
    report.write(results, rows, out)
    selection = results["selection"]
    print(f"wrote {out / 'backtest.md'}; winner: {selection['winner']}")
    if selection["winner"] != estimate.DEFAULT_METHOD:
        print(
            f"note: the default method is {estimate.DEFAULT_METHOD}; update DEFAULT_METHOD",
            file=sys.stderr,
        )
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    store = paths.local_runs()
    rows = schema.read_rows(store) if store.exists() else []
    if not rows:
        raise ValueError("no local rows yet; run `agentpace ingest` first")
    out = Path(args.out_dir)
    published = schema.read_dataset([out]) if out.exists() else []
    chosen, skipped = share.select(rows, args.since, args.drop_project, published)
    print(share.NOTICE, file=sys.stderr)
    if skipped:
        print(f"{skipped} rows are already published and left out.", file=sys.stderr)
    if not chosen:
        print("nothing new to share.", file=sys.stderr)
        return 0
    if not args.write:
        for row in chosen:
            print(schema.canonical(row))
        print(
            f"dry run: {len(chosen)} rows shown above, nothing written. "
            "Pass --write to create the file.",
            file=sys.stderr,
        )
        return 0
    path = out / share.file_name(chosen)
    count = schema.write_rows(path, chosen)
    print(f"wrote {count} rows to {path}. Nothing has been sent. To submit:", file=sys.stderr)
    for command in share.git_commands(path):
        print(f"  {command}", file=sys.stderr)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    sources = [Path(path) for path in args.paths] or [paths.seed_dir()]
    if not all(source.exists() for source in sources):
        raise ValueError("a path does not exist")
    findings = validate.validate(sources)
    for finding in findings:
        print(finding)
    if findings:
        print(f"{len(findings)} problems", file=sys.stderr)
        return 1
    print("all rows valid")
    return 0


def cmd_site(args: argparse.Namespace) -> int:
    rows = _dataset(args.data or [str(paths.seed_dir())])
    page = Path(args.out)
    if not page.exists():
        raise ValueError("the page does not exist")
    stored = page.parent / "backtest.json"
    if not stored.exists():
        raise ValueError("run `agentpace backtest` first: the page quotes its results")
    results = json.loads(stored.read_text(encoding="utf-8"))
    current = page.read_text(encoding="utf-8")
    updated = site.build(current, site.payload(rows, results))
    if args.check:
        if updated == current:
            print("the page's data is current")
            return 0
        print("the page's data is out of date; run `agentpace site`", file=sys.stderr)
        return 1
    page.write_text(updated, encoding="utf-8", newline="\n")
    print(f"updated {page}")
    return 0


def cmd_claims(args: argparse.Namespace) -> int:
    stored = Path(args.report)
    if not stored.exists():
        raise ValueError("run `agentpace backtest` first: claims come from its report")
    rows = _dataset(args.data or [str(paths.seed_dir())])
    report_data = json.loads(stored.read_text(encoding="utf-8"))
    known = claims.values(rows, report_data, Path("examples/plan.json"))
    stale = []
    for name in args.files:
        path = Path(name)
        current = path.read_text(encoding="utf-8")
        updated = claims.apply(current, known)
        if updated == current:
            continue
        stale.append(name)
        if not args.check:
            path.write_text(updated, encoding="utf-8", newline="\n")
    if args.check and stale:
        print(f"out of date: {', '.join(stale)}. Run `agentpace claims`.", file=sys.stderr)
        return 1
    print("claims match the data" if args.check else f"updated {len(stale)} files")
    return 0


def _reader_went_away(error: OSError) -> bool:
    return isinstance(error, BrokenPipeError) or (os.name == "nt" and error.errno == errno.EINVAL)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentpace",
        description="Real run times for coding agents, as P50/P80/P90 ranges.",
    )
    parser.add_argument("--version", action="version", version=f"agentpace {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest_cmd = commands.add_parser("ingest", help="read local agent history into rows")
    ingest_cmd.add_argument("path", nargs="?", help="history folder (default: the harness's own)")
    ingest_cmd.add_argument("--source", default="claude-code", choices=sorted(ADAPTERS))
    ingest_cmd.add_argument("--out", help="rows file (default: ~/.agentpace/runs.jsonl)")
    ingest_cmd.add_argument(
        "--idle-cap",
        type=int,
        default=ingest.DEFAULT_IDLE_CAP_S,
        help="seconds an idle gap may contribute (default: %(default)s)",
    )
    ingest_cmd.add_argument(
        "--kind-map",
        action="append",
        default=[],
        metavar="NAME=KIND",
        help="map one of your agent types to a task kind (repeatable)",
    )
    ingest_cmd.add_argument(
        "--hook",
        action="store_true",
        help="read a Claude Code SubagentStop payload on stdin and ingest that session quietly",
    )
    ingest_cmd.set_defaults(handler=cmd_ingest)

    runs_cmd = commands.add_parser("runs", help="list recent local runs and their references")
    runs_cmd.add_argument("--last", type=int, default=20, help="how many (default: %(default)s)")
    runs_cmd.set_defaults(handler=cmd_runs)

    label_cmd = commands.add_parser("label", help="correct the kind or risk of one run")
    label_cmd.add_argument("ref", help="a reference from `agentpace runs`, or 'last'")
    label_cmd.add_argument("--kind", choices=schema.enum("task_kind"))
    label_cmd.add_argument("--risk", choices=schema.enum("risk"))
    label_cmd.set_defaults(handler=cmd_label)

    estimate_cmd = commands.add_parser("estimate", help="P50/P80/P90 for a plan of tasks")
    estimate_cmd.add_argument("plan", help="tasks JSON file, or - for stdin")
    estimate_cmd.add_argument("--data", action="append", default=[], help="rows file or folder")
    estimate_cmd.add_argument(
        "--method", default=estimate.DEFAULT_METHOD, choices=[m.name for m in estimate.METHODS]
    )
    estimate_cmd.add_argument(
        "--use-size",
        action="store_true",
        help="use size: an oracle track, because stored size is measured after a run",
    )
    estimate_cmd.add_argument("--draws", type=int, default=estimate.DEFAULT_DRAWS)
    estimate_cmd.add_argument("--seed", type=int, default=estimate.DEFAULT_SEED)
    estimate_cmd.add_argument("--json", action="store_true", help="machine-readable output")
    estimate_cmd.set_defaults(handler=cmd_estimate)

    backtest_cmd = commands.add_parser("backtest", help="evaluate on held-out runs")
    backtest_cmd.add_argument("--data", action="append", default=[], help="rows file or folder")
    backtest_cmd.add_argument("--out", default="docs", help="report folder (default: %(default)s)")
    backtest_cmd.add_argument("--seed", type=int, default=backtest.SEED)
    backtest_cmd.add_argument(
        "--check", action="store_true", help="fail if results differ from the published report"
    )
    backtest_cmd.set_defaults(handler=cmd_backtest)

    share_cmd = commands.add_parser(
        "share", help="preview your rows, or write a file to contribute"
    )
    mode = share_cmd.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="print exact rows (the default)")
    mode.add_argument("--write", action="store_true", help="create the contribution file")
    share_cmd.add_argument("--out-dir", default="data/runs", help="default: %(default)s")
    share_cmd.add_argument("--since", metavar="YYYY-Www", help="only this ISO week onward")
    share_cmd.add_argument("--drop-project", action="store_true", help="omit the project field")
    share_cmd.set_defaults(handler=cmd_share)

    validate_cmd = commands.add_parser("validate", help="check data files")
    validate_cmd.add_argument("paths", nargs="*", help="files or folders (default: seed data)")
    validate_cmd.set_defaults(handler=cmd_validate)

    site_cmd = commands.add_parser("site", help="refresh the data inside the static page")
    site_cmd.add_argument("--data", action="append", default=[], help="rows file or folder")
    site_cmd.add_argument("--out", default="docs/index.html", help="default: %(default)s")
    site_cmd.add_argument("--check", action="store_true", help="fail if the page is out of date")
    site_cmd.set_defaults(handler=cmd_site)

    claims_cmd = commands.add_parser("claims", help="tie numbers in documents to the data")
    claims_cmd.add_argument(
        "files", nargs="*", default=["README.md", "skills/agentpace/assets/portable-prompt.md"]
    )
    claims_cmd.add_argument("--data", action="append", default=[], help="rows file or folder")
    claims_cmd.add_argument("--report", default="docs/backtest.json")
    claims_cmd.add_argument("--check", action="store_true", help="fail if any number is stale")
    claims_cmd.set_defaults(handler=cmd_claims)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.handler(args)
        sys.stdout.flush()
    except ValueError as error:
        print(f"agentpace: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        if not _reader_went_away(error):
            raise
        # Piping into `head` is a normal way to preview rows. Stop quietly.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    return result

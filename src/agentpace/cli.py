"""Command line interface."""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from agentpace import (
    __version__,
    backtest,
    estimate,
    ingest,
    paths,
    pseudonym,
    render,
    report,
    schema,
    share,
    validate,
)
from agentpace.adapters import ADAPTERS, get_adapter


def _kind_map(pairs: Sequence[str]) -> dict[str, str]:
    allowed = schema.enum("task_kind")
    mapping: dict[str, str] = {}
    for pair in pairs:
        name, separator, kind = pair.partition("=")
        if not separator or kind not in allowed:
            raise ValueError(f"--kind-map expects name=kind, with kind one of {allowed}")
        mapping[name] = kind
    return mapping


def cmd_ingest(args: argparse.Namespace) -> int:
    adapter = get_adapter(args.source)
    path = Path(args.path) if args.path else adapter.default_path()
    if not path.exists():
        raise ValueError("the transcript path does not exist")
    secret = pseudonym.load_secret(paths.home())
    labels = ingest.load_labels(paths.labels_file())
    rows, report = ingest.ingest(
        adapter, path, secret, args.idle_cap, _kind_map(args.kind_map), labels
    )
    out = Path(args.out) if args.out else paths.local_runs()
    total = ingest.merge_into(out, rows)
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

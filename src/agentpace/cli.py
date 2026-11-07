"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from agentpace import __version__, ingest, paths, pseudonym, schema
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result: int = args.handler(args)
    except ValueError as error:
        print(f"agentpace: {error}", file=sys.stderr)
        return 2
    return result

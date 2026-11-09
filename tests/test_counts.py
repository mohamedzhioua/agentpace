"""Count messages agree in number: the singular and the plural branch of each one."""

import contextlib
import dataclasses
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agentpace import cli, ingest, paths, render, schema, share, turns
from agentpace.estimate import HumanPart, PlanRange, TaskRange
from tests.synthetic import row

NAME = "0123456789abcdef-2026-W10-2026-W10.jsonl"


def plan(**overrides):
    task = TaskRange("a", 1, 2, 3, "docs", 0, 0.0, 1.0)
    base = PlanRange(
        1, 2, 3, (task,), ("a",), 1.0, 2, 0.9, 2, 0.5, "shrunk", False, 10, 0, 500, 1, ()
    )
    return dataclasses.replace(base, **overrides)


class Helper(unittest.TestCase):
    def test_one_takes_the_noun_and_every_other_count_the_plural(self):
        self.assertEqual(render.count(1, "row"), "1 row")
        self.assertEqual(render.count(2, "row"), "2 rows")
        self.assertEqual(render.count(0, "row"), "0 rows")
        self.assertEqual(render.count(3383, "run"), "3383 runs")
        self.assertEqual(render.count(1, "child", "children"), "1 child")
        self.assertEqual(render.count(2, "child", "children"), "2 children")


class Render(unittest.TestCase):
    def human(self, gaps, sessions):
        return HumanPart(
            status="measured",
            source="your history, local",
            per_turn_p50=60.0,
            per_turn_p80=120.0,
            per_turn_p90=180.0,
            gaps=gaps,
            sessions=sessions,
        )

    def test_the_measured_human_part(self):
        self.assertIn("(1 gap, 1 session); elapsed", render.human_text(self.human(1, 1)))
        self.assertIn("(4 gaps, 2 sessions); elapsed", render.human_text(self.human(4, 2)))

    def test_the_parallelism_line(self):
        for sessions, said in ((1, "(from 1 session)."), (29, "(from 29 sessions).")):
            with self.subTest(sessions=sessions):
                text = render.plan_text(plan(parallelism_sessions=sessions))
                self.assertIn(f"Effective parallelism at cap 2: 0.90 {said} Throughput", text)

    def test_the_method_line(self):
        for used, said in ((1, "1 run."), (2, "2 runs."), (3383, "3383 runs.")):
            with self.subTest(used=used):
                lines = render.plan_text(plan(rows_used=used)).splitlines()
                self.assertIn(f"Method: shrunk. {said} 500 simulations, seed 1.", lines)
        lines = render.plan_text(plan(rows_used=1, rows_excluded=1)).splitlines()
        self.assertIn(
            "Method: shrunk. 1 run, 1 not completed so excluded. 500 simulations, seed 1.", lines
        )


class Messages(unittest.TestCase):
    def test_no_chronology(self):
        self.assertEqual(
            cli._no_chronology(1),
            "1 session has no local chronology (no cache entry, or ingested with another idle "
            "cap); it is listed by week only. Run agentpace ingest to refresh the cache.",
        )
        self.assertEqual(
            cli._no_chronology(2),
            "2 sessions have no local chronology (no cache entry, or ingested with another idle "
            "cap); they are listed by week only. Run agentpace ingest to refresh the cache.",
        )

    def test_cannot_tell_last(self):
        self.assertEqual(
            cli._cannot_tell_last(1),
            "cannot tell which run is last: 1 session has no local chronology. "
            "Run agentpace ingest, or give a reference from agentpace runs",
        )
        self.assertEqual(
            cli._cannot_tell_last(2),
            "cannot tell which run is last: 2 sessions have no local chronology. "
            "Run agentpace ingest, or give a reference from agentpace runs",
        )

    def test_experimental_rows_in_an_estimate(self):
        one = [row(harness="opencode")]
        self.assertEqual(
            cli._experimental_rows_note(one),
            "note: 1 of 1 row comes from experimental adapters (opencode); it takes part in this "
            "estimate because it is in your local store. Pass --data to choose the rows.",
        )
        self.assertEqual(
            cli._experimental_rows_note([*one, row(), row()]),
            "note: 1 of 3 rows comes from experimental adapters (opencode); it takes part in this "
            "estimate because it is in your local store. Pass --data to choose the rows.",
        )
        self.assertEqual(
            cli._experimental_rows_note([*one, row(harness="cline"), row()]),
            "note: 2 of 3 rows come from experimental adapters (cline, opencode); they take part "
            "in this estimate because they are in your local store. Pass --data to choose the "
            "rows.",
        )

    def test_experimental_rows_in_a_share(self):
        self.assertEqual(
            share.experimental_note([row(harness="opencode"), row()]),
            "1 row comes from experimental adapters (opencode). validate rejects it until the "
            "adapter is verified, so a pull request with it stays red until a maintainer "
            "reconciles it by hand; see docs/verify-adapter.md.",
        )
        self.assertEqual(
            share.experimental_note([row(harness="opencode"), row(harness="opencode")]),
            "2 rows come from experimental adapters (opencode). validate rejects them until the "
            "adapter is verified, so a pull request with them stays red until a maintainer "
            "reconciles them by hand; see docs/verify-adapter.md.",
        )

    def test_unmatched_dropped_sessions(self):
        for drop, said in ((["f" * 12], "1 session"), (["e" * 12, "f" * 12], "2 sessions")):
            with self.subTest(drop=drop):
                with self.assertRaises(ValueError) as caught:
                    share.select([row()], drop_sessions=drop)
                self.assertIn(
                    f"--drop-session names {said} with no local run (", str(caught.exception)
                )

    def test_the_turns_report(self):
        one = turns.TurnsReport(1, 1, 1, 1, 1, 1, 1)
        self.assertEqual(
            one.lines(),
            [
                "read 1 main-session transcript: 1 gap in 1 session",
                "skipped 1 session with no turnOrigin, so human prompts cannot be told apart "
                "from other records",
                "skipped 1 session with fewer than 2 human prompts",
                "could not read 1 file",
                "kept 1 gap from sessions whose transcripts are gone",
            ],
        )
        two = turns.TurnsReport(2, 2, 2, 2, 2, 2, 2)
        self.assertEqual(
            two.lines(),
            [
                "read 2 main-session transcripts: 2 gaps in 2 sessions",
                "skipped 2 sessions with no turnOrigin, so human prompts cannot be told apart "
                "from other records",
                "skipped 2 sessions with fewer than 2 human prompts",
                "could not read 2 files",
                "kept 2 gaps from sessions whose transcripts are gone",
            ],
        )


class Temp(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()


class Commands(Temp):
    def test_ingest_says_what_the_store_holds(self):
        for total, said in ((1, "1 row"), (2, "2 rows")):
            with self.subTest(total=total):
                result = (ingest.IngestReport(), total)
                with mock.patch.object(cli, "_ingest_into_store", return_value=result):
                    code, _, err = self.run_cli("ingest", str(self.root))
                self.assertEqual(code, 0)
                self.assertTrue(err.endswith(f"\nstore now holds:  {said}\n"))

    def test_turns_says_what_it_measured(self):
        for count, said in (
            (1, "1 gap between agent turns in 1 session."),
            (2, "2 gaps between agent turns in 2 sessions."),
        ):
            summary = SimpleNamespace(
                gaps=count,
                sessions=count,
                p50=60,
                p80=120,
                p90=180,
                over_hour=0.0,
                waiting_share=0.5,
            )
            with (
                self.subTest(count=count),
                mock.patch.object(turns, "update", return_value=turns.TurnsReport()),
                mock.patch.object(turns, "read_turns", return_value=[]),
                mock.patch.object(turns, "summarize", return_value=summary),
            ):
                code, out, _ = self.run_cli("turns", str(self.root))
                self.assertEqual(code, 0)
                self.assertIn(f"Waiting on you, from your history (local): {said}\n", out)

    def test_validate_counts_its_problems(self):
        for lines, said in ((1, "1 problem\n"), (2, "2 problems\n")):
            with self.subTest(lines=lines):
                path = self.root / f"0123456789abcdef-{lines}.jsonl"
                path.write_text("{broken\n" * lines, encoding="utf-8")
                code, _, err = self.run_cli("validate", str(path))
                self.assertEqual(code, 1)
                self.assertEqual(err, said)

    def test_claims_counts_the_files_it_updated(self):
        data = self.root / "rows.jsonl"
        schema.write_rows(data, [row()])
        report = self.root / "report.json"
        report.write_text("{}", encoding="utf-8")
        for count, said in ((1, "updated 1 file\n"), (2, "updated 2 files\n")):
            files = [self.root / f"doc{i}-{count}.md" for i in range(count)]
            for path in files:
                path.write_text("old", encoding="utf-8")
            with (
                self.subTest(count=count),
                mock.patch.object(cli.claims, "values", return_value={}),
                mock.patch.object(cli.claims, "apply", return_value="new"),
            ):
                code, out, _ = self.run_cli(
                    "claims", *map(str, files), "--data", str(data), "--report", str(report)
                )
                self.assertEqual(code, 0)
                self.assertEqual(out, said)

    def test_score_counts_the_damaged_ledger_lines(self):
        for skipped, said in ((1, "1 damaged line"), (2, "2 damaged lines")):
            ledger = cli.forecasts.Ledger(skipped=skipped)
            with (
                self.subTest(skipped=skipped),
                mock.patch.object(cli.forecasts, "read", return_value=ledger),
            ):
                code, _, err = self.run_cli("score")
                self.assertEqual(code, 0)
                self.assertIn(f"skipped {said} in the ledger\n", err)


class Share(Temp):
    def setUp(self):
        super().setUp()
        self.out = self.root / "runs"
        self.path = self.out / NAME

    def local(self, *rows):
        schema.write_rows(paths.local_runs(), list(rows))

    def share(self, *args):
        return self.run_cli("share", "--out-dir", str(self.out), *args)

    def test_one_row(self):
        self.local(row())
        _, _, err = self.share()
        self.assertIn("dry run: 1 row shown above, nothing written.", err)
        _, _, err = self.share("--write")
        self.assertIn(f"wrote 1 row to {self.path}. Nothing has been sent.", err)
        self.local(row(), row(offset_s=5))
        _, _, err = self.share()
        self.assertIn("1 row is already published and left out.", err)
        self.assertIn(f"--write would add it to {self.path}, which holds 1 row.", err)
        _, _, err = self.share("--write")
        self.assertIn(f"added 1 row to {self.path}, which already held 1.", err)

    def test_two_rows(self):
        self.local(row(), row(offset_s=5))
        _, _, err = self.share()
        self.assertIn("dry run: 2 rows shown above, nothing written.", err)
        _, _, err = self.share("--write")
        self.assertIn(f"wrote 2 rows to {self.path}. Nothing has been sent.", err)
        self.local(row(), row(offset_s=5), row(offset_s=6), row(offset_s=7))
        _, _, err = self.share()
        self.assertIn("2 rows are already published and left out.", err)
        self.assertIn(f"--write would add them to {self.path}, which holds 2 rows.", err)
        _, _, err = self.share("--write")
        self.assertIn(f"added 2 rows to {self.path}, which already held 2.", err)

    def test_dropped_sessions(self):
        self.local(row(), row(batch="d" * 12, offset_s=7), row(batch="c" * 12, offset_s=9))
        _, _, err = self.share("--drop-session", "d" * 12)
        self.assertIn("1 row from 1 dropped session left out.", err)
        _, _, err = self.share("--drop-session", "d" * 12, "--drop-session", "c" * 12)
        self.assertIn("2 rows from 2 dropped sessions left out.", err)

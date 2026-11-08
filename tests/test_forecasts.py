"""The local forecast ledger: `estimate --record` and `--quoted`."""

import ast
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, forecasts, paths, pseudonym, render, schema
from agentpace.adapters import get_adapter
from agentpace.adapters.base import RawRun
from agentpace.cache import RunCache
from tests.synthetic import dataset, row

REPO = Path(__file__).resolve().parent.parent
PLAN = {
    "agent_cap": 2,
    "tasks": [
        {"id": "a", "kind": "feature"},
        {"id": "b", "kind": "docs", "depends_on": ["a"]},
    ],
}


class Durations(unittest.TestCase):
    def test_exact_quotes_parse_by_fixed_units(self):
        cases = {
            "3 weeks": 3 * 7 * 86_400,
            "3w": 3 * 7 * 86_400,
            "2 Days": 2 * 86_400,
            "1.5 h": 5_400,
            "45 min": 2_700,
            "1 month": 30 * 86_400,
            " 2 mo ": 60 * 86_400,
        }
        for text, seconds in cases.items():
            with self.subTest(text=text):
                self.assertEqual(forecasts.parse_duration(text), seconds)

    def test_anything_else_is_not_a_duration(self):
        for text in ("3", "about 3 weeks", "3 weeks-ish", "2-3 weeks", "a month", "3 sprints", ""):
            with self.subTest(text=text):
                self.assertIsNone(forecasts.parse_duration(text))


class Ledger(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "forecasts.jsonl"

    def record(self, quoted=None):
        result = {
            "method": "shrunk",
            "seed": 1,
            "draws": 10,
            "rows_used": 20,
            "rows_excluded": 0,
            "p50": 1.0,
            "p80": 2.0,
            "p90": 3.0,
            "tasks": [{"id": "a", "p50": 1.0, "p80": 2.0, "p90": 3.0, "cell": "all", "n": 1}],
            "human": {"status": "not measured", "included_in_total": False},
        }
        return forecasts.forecast_record(
            issued_at="2026-11-07T10:00:00Z",
            version="0.4.0",
            plan_document=PLAN,
            result=result,
            quoted=quoted,
        )

    def test_a_record_round_trips(self):
        record = self.record("3 weeks")
        forecasts.append(self.path, record)
        ledger = forecasts.read(self.path)
        self.assertEqual(ledger.forecasts, {record["id"]: record})
        self.assertEqual(record["quoted_s"], 1_814_400)

    def test_the_id_comes_from_the_content_and_time(self):
        self.assertEqual(self.record()["id"], self.record()["id"])
        self.assertNotEqual(self.record()["id"], self.record("3 weeks")["id"])
        self.assertRegex(self.record()["id"], r"^[0-9a-f]{8}$")

    def test_damaged_and_unknown_lines_are_skipped_and_counted(self):
        forecasts.append(self.path, self.record())
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("not json\n")
            handle.write('{"kind": "mystery", "id": "x"}\n')
            handle.write('{"kind": "forecast", "id": "y"}\n')
        ledger = forecasts.read(self.path)
        self.assertEqual(len(ledger.forecasts), 1)
        self.assertEqual(ledger.skipped, 3)

    def test_a_missing_file_is_an_empty_ledger(self):
        self.assertEqual(forecasts.read(self.path).forecasts, {})

    def test_the_ledger_uses_no_randomness(self):
        tree = ast.parse((REPO / "src" / "agentpace" / "forecasts.py").read_text(encoding="utf-8"))
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        imported |= {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("random", imported)
        self.assertNotIn("secrets", imported)


class RecordCommand(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.data = self.root / "runs.jsonl"
        schema.write_rows(self.data, dataset())
        self.plan = self.root / "plan.json"
        self.plan.write_text(json.dumps(PLAN), encoding="utf-8")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def estimate(self, *extra):
        return self.run_cli(
            "estimate", str(self.plan), "--data", str(self.data), "--draws", "300", *extra
        )

    def test_recording_does_not_change_what_is_printed(self):
        _, plain, _ = self.estimate()
        code, recorded, err = self.estimate("--record")
        self.assertEqual(code, 0)
        self.assertEqual(plain, recorded)
        self.assertIn("recorded forecast", err)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_the_record_holds_what_the_user_saw(self):
        _, out, _ = self.estimate("--json", "--record")
        shown = json.loads(out)
        (record,) = forecasts.read(paths.forecasts_file()).forecasts.values()
        self.assertEqual(
            (record["p50"], record["p80"], record["p90"]),
            (shown["p50"], shown["p80"], shown["p90"]),
        )
        self.assertEqual(record["human"], shown["human"])
        self.assertEqual(record["plan"], PLAN)
        self.assertTrue(record["issued_at"].endswith("Z"))

    def test_no_path_or_source_is_recorded(self):
        self.estimate("--record")
        text = paths.forecasts_file().read_text(encoding="utf-8")
        for fragment in (str(self.root), "runs.jsonl", "plan.json", "\\\\", "/home"):
            self.assertNotIn(fragment, text)

    def test_a_parseable_quote_is_stored_with_seconds(self):
        _, _, err = self.estimate("--record", "--quoted", "3 weeks")
        (record,) = forecasts.read(paths.forecasts_file()).forecasts.values()
        self.assertEqual((record["quoted"], record["quoted_s"]), ("3 weeks", 1_814_400))
        self.assertIn("calendar time", err)

    def test_an_unparseable_quote_is_stored_as_text_only(self):
        _, _, err = self.estimate("--record", "--quoted", "about 3 weeks")
        (record,) = forecasts.read(paths.forecasts_file()).forecasts.values()
        self.assertEqual(record["quoted"], "about 3 weeks")
        self.assertNotIn("quoted_s", record)
        self.assertIn("not parseable, stored as text", err)

    def test_a_quote_alone_is_shown_and_writes_no_ledger(self):
        code, out, err = self.estimate("--quoted", "3 weeks")
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("Total agent time, on the session clock: P50 "))
        self.assertEqual(
            lines[1], 'Quoted: "3 weeks" (calendar time, as quoted; unverified, not converted)'
        )
        self.assertTrue(lines[2].startswith("Human part"))
        self.assertFalse(paths.forecasts_file().exists())
        self.assertEqual(list(self.root.rglob("forecasts.jsonl")), [])

    def test_the_quote_line_is_the_only_text_change(self):
        _, plain, _ = self.estimate()
        _, quoted, _ = self.estimate("--quoted", "about 3 weeks")
        lines = quoted.splitlines()
        self.assertEqual(lines[1], render.quote_text("about 3 weeks"))
        self.assertEqual(lines[:1] + lines[2:], plain.splitlines())
        self.assertNotIn("Quoted:", plain)

    def test_a_quote_leaves_the_json_unchanged(self):
        _, plain, _ = self.estimate("--json")
        _, quoted, err = self.estimate("--json", "--quoted", "3 weeks")
        self.assertEqual(quoted, plain)
        self.assertEqual(err, "")

    def test_a_recorded_quote_shows_the_line_and_keeps_the_record_line(self):
        _, out, err = self.estimate("--record", "--quoted", "3 weeks")
        (record,) = forecasts.read(paths.forecasts_file()).forecasts.values()
        self.assertIn(render.quote_text("3 weeks"), out.splitlines())
        self.assertEqual(
            err,
            f"recorded forecast {record['id']} in {paths.forecasts_file()}"
            "; quote stored as calendar time, as quoted\n",
        )

    def test_share_is_unchanged_by_a_ledger(self):
        schema.write_rows(paths.local_runs(), [row(), row(offset_s=5)])
        out_dir = str(self.root / "runs")
        _, before, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.estimate("--record", "--quoted", "3 weeks")
        self.assertTrue(paths.forecasts_file().exists())
        _, after, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.assertEqual(before, after)


ISSUED = "2026-11-02T09:00:00Z"  # Monday of 2026-W45
ISSUED_EPOCH = forecasts.parse_issued(ISSUED)


class ScoreCommand(RecordCommand):
    """Scores appended to the ledger, with the honesty rule for sessions."""

    def setUp(self):
        super().setUp()
        self.secret = pseudonym.load_secret(paths.home())
        self.batch = pseudonym.batch(self.secret, "session-1")

    def forecast(self, quoted=None, p50=600.0, p80=1200.0, p90=1800.0, issued_at=ISSUED):
        result = {
            "method": "shrunk",
            "seed": 1,
            "draws": 10,
            "rows_used": 20,
            "rows_excluded": 0,
            "p50": p50,
            "p80": p80,
            "p90": p90,
            "tasks": [],
            "human": {"status": "not measured", "included_in_total": False},
        }
        record = forecasts.forecast_record(
            issued_at=issued_at,
            version="0.4.0",
            plan_document=PLAN,
            result=result,
            quoted=quoted,
        )
        forecasts.append(paths.forecasts_file(), record)
        return record["id"]

    def session(self, start_offset_s=60.0, week="2026-W45", spans=((0, 300), (200, 900))):
        """Two local rows in one batch, and the matching runs in the ingest cache."""
        rows = [
            row(batch=self.batch, week=week, offset_s=offset, duration_s=length)
            for offset, length in spans
        ]
        schema.write_rows(paths.local_runs(), rows)
        adapter = get_adapter("claude-code")
        cache = RunCache(paths.cache_file(adapter.name), adapter.name, adapter.version, 900)
        for index, (offset, length) in enumerate(spans):
            start = ISSUED_EPOCH + start_offset_s + offset
            run = RawRun(
                harness="claude-code",
                model="claude-opus-5-5",
                start=start,
                end=start + length,
                spans=((start, start + length),),
                active_s=length,
                tool_calls=1,
                edit_calls=0,
                tokens=None,
                errors=0,
                outcome="completed",
                session="session-1",
            )
            cache.store(f"/x/agent-{index}.jsonl", [[1, 1]], [run])
        cache.save()

    def scores(self):
        return forecasts.read(paths.forecasts_file()).scores

    def test_a_session_started_after_the_forecast_scores_verified(self):
        key = self.forecast(quoted="3 weeks")
        self.session()
        code, out, _ = self.run_cli("score", key, "--session", f"{self.batch}:0")
        self.assertEqual(code, 0)
        (score,) = self.scores()
        self.assertEqual(
            (score["actual_s"], score["source"], score["verified"]), (1100, "session", True)
        )
        self.assertEqual(score["session"], self.batch)
        self.assertFalse(score["quote_closer"])
        self.assertIn("agent active time on that session's clock", out)
        self.assertIn("as quoted, calendar time", out)
        self.assertNotIn("wall clock", out.lower())

    def test_a_session_started_before_the_forecast_is_refused(self):
        key = self.forecast()
        self.session(start_offset_s=-60.0)
        code, _, err = self.run_cli("score", key, "--session", self.batch)
        self.assertNotEqual(code, 0)
        self.assertIn("started before the forecast", err)
        self.assertEqual(self.scores(), [])

    def test_a_session_missing_from_the_cache_is_refused(self):
        key = self.forecast()
        schema.write_rows(
            paths.local_runs(),
            [
                row(batch=self.batch, week="2026-W45"),
                row(batch=self.batch, week="2026-W45", offset_s=9),
            ],
        )
        code, _, err = self.run_cli("score", key, "--session", self.batch)
        self.assertNotEqual(code, 0)
        self.assertIn("cannot verify this session started after the forecast", err)

    def test_a_session_from_an_earlier_week_is_refused(self):
        key = self.forecast()
        self.session(week="2026-W44")
        code, _, err = self.run_cli("score", key, "--session", self.batch)
        self.assertNotEqual(code, 0)
        self.assertIn("ran before the forecast was issued", err)

    def test_an_attested_actual_is_marked_unverified(self):
        key = self.forecast()
        code, out, _ = self.run_cli("score", key, "--actual", "2h")
        self.assertEqual(code, 0)
        (score,) = self.scores()
        self.assertEqual(
            (score["actual_s"], score["source"], score["verified"]), (7200, "actual", False)
        )
        self.assertIn("attested by you", out)

    def test_the_summary_needs_five_scored_forecasts(self):
        for index in range(4):
            self.run_cli("score", self.forecast(p50=100.0 + index), "--actual", "10 min")
        _, out, _ = self.run_cli("score")
        self.assertIn("not enough scored forecasts (n = 4, need 5)", out.lower())
        self.run_cli("score", self.forecast(p50=200.0), "--actual", "10 min")
        _, out, _ = self.run_cli("score")
        self.assertIn("n = 5, of which 5 attested", out)
        self.assertIn("Actual within: P50 0%, P80 100%, P90 100%", out)

    def test_the_latest_score_per_forecast_wins(self):
        key = self.forecast()
        self.run_cli("score", key, "--actual", "1h")
        self.run_cli("score", key, "--actual", "5 min")
        latest = forecasts.latest_scores(self.scores())
        self.assertEqual(latest[key]["actual_s"], 300)
        self.assertEqual(forecasts.summarize(forecasts.read(paths.forecasts_file())).scored, 1)

    def test_an_unknown_id_is_a_clean_error(self):
        code, _, err = self.run_cli("score", "deadbeef", "--actual", "1h")
        self.assertNotEqual(code, 0)
        self.assertIn("no recorded forecast has that id", err)

    def test_the_secret_never_reaches_the_ledger(self):
        key = self.forecast()
        self.session()
        self.run_cli("score", key, "--session", self.batch)
        self.assertNotIn(self.secret.hex(), paths.forecasts_file().read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

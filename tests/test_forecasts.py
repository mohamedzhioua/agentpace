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

from agentpace import cli, forecasts, paths, schema
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

    def test_a_quote_needs_a_record(self):
        code, _, err = self.estimate("--quoted", "3 weeks")
        self.assertNotEqual(code, 0)
        self.assertIn("--record", err)

    def test_share_is_unchanged_by_a_ledger(self):
        schema.write_rows(paths.local_runs(), [row(), row(offset_s=5)])
        out_dir = str(self.root / "runs")
        _, before, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.estimate("--record", "--quoted", "3 weeks")
        self.assertTrue(paths.forecasts_file().exists())
        _, after, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

"""Outcome disclosure: the loaded runs that did not complete, by harness (0.10.0)."""

import contextlib
import dataclasses
import io
import json
import os
import random
import re
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from agentpace import cli, estimate, forecasts, paths, render, schema
from agentpace.estimate import HarnessOutcomes, OutcomeSummary, PlanRange, TaskRange
from tests.synthetic import dataset, row

REPO = Path(__file__).resolve().parent.parent
PLAN = {"agent_cap": 2, "tasks": [{"id": "a", "kind": "feature"}, {"id": "b", "kind": "docs"}]}
BANNED = ("attempt", "abandon", "acceptance rate")


def mixed():
    """Synthetic rows: claude-code with failures and unknowns, codex with a completed row with
    no duration. Nothing here came from a real run."""
    rows = dataset(sessions=20, runs_per_session=6)
    rows[0] = {**rows[0], "outcome": "failed"}
    rows[1] = {**rows[1], "outcome": "unknown"}
    rows[2] = {**rows[2], "outcome": "unknown"}
    codex = [
        row(harness="codex", batch=f"c{i:011x}", offset_s=0, outcome=outcome, duration_s=length)
        for i, (outcome, length) in enumerate(
            [("completed", 60), ("completed", 0), ("failed", 30), ("completed", 90)]
        )
    ]
    return rows + codex


def check_invariants(test, rows, summary, model=None):
    test.assertEqual(summary.rows, len(rows))
    test.assertEqual(summary.rows, summary.completed + summary.failed + summary.unknown)
    test.assertEqual(summary.rows, sum(h.rows for h in summary.by_harness))
    for name in ("completed", "failed", "unknown"):
        test.assertEqual(getattr(summary, name), sum(getattr(h, name) for h in summary.by_harness))
    for harness in summary.by_harness:
        test.assertEqual(harness.rows, harness.completed + harness.failed + harness.unknown)
    names = [h.harness for h in summary.by_harness]
    test.assertEqual(names, sorted(names))
    test.assertEqual(len(names), len(set(names)))
    if model is not None:
        test.assertEqual(model.outcomes, summary)
        test.assertEqual(model.rows_used, summary.completed - summary.completed_without_duration)
        test.assertEqual(
            model.rows_excluded,
            summary.failed + summary.unknown + summary.completed_without_duration,
        )


def plan_with(outcomes):
    task = TaskRange("a", 1, 2, 3, "docs", 0, 0.0, 1.0)
    plan = PlanRange(
        1, 2, 3, (task,), ("a",), 1.0, 2, None, 0, 0.0, "shrunk", False, 10, 0, 500, 1, ()
    )
    return dataclasses.replace(plan, outcomes=outcomes)


class Summary(unittest.TestCase):
    def test_mixed_harnesses(self):
        rows = mixed()
        summary = estimate.outcome_summary(rows)
        check_invariants(self, rows, summary, estimate.fit(rows))
        claude, codex = summary.by_harness
        self.assertEqual((claude.harness, claude.failed, claude.unknown), ("claude-code", 1, 2))
        self.assertEqual(codex, HarnessOutcomes("codex", 4, 3, 1, 0))
        self.assertEqual(summary.completed_without_duration, 1)

    def test_one_harness(self):
        rows = dataset(sessions=10, runs_per_session=5)
        rows[3] = {**rows[3], "outcome": "failed"}
        summary = estimate.outcome_summary(rows)
        check_invariants(self, rows, summary, estimate.fit(rows))
        self.assertEqual(summary.by_harness, (HarnessOutcomes("claude-code", 50, 49, 1, 0),))

    def test_all_completed(self):
        rows = dataset(sessions=10, runs_per_session=5)
        summary = estimate.outcome_summary(rows)
        check_invariants(self, rows, summary, estimate.fit(rows))
        self.assertEqual((summary.failed, summary.unknown), (0, 0))
        line = render.outcomes_text(plan_with(summary))
        self.assertIn("claude-code 0 of 50 (0.0%; 0 failed, 0 unknown)", line)

    def test_unknown_only(self):
        rows = [row(outcome="unknown", offset_s=i) for i in range(4)]
        summary = estimate.outcome_summary(rows)
        check_invariants(self, rows, summary)
        self.assertEqual(summary.by_harness, (HarnessOutcomes("claude-code", 4, 0, 0, 4),))
        self.assertEqual(summary.failed, 0)  # unknown is never counted as a failure
        line = render.outcomes_text(plan_with(summary))
        self.assertIn("claude-code 4 of 4 (100.0%; 0 failed, 4 unknown)", line)

    def test_a_completed_row_with_no_duration_is_counted_apart(self):
        rows = [*dataset(sessions=10, runs_per_session=5), row(duration_s=0, offset_s=999)]
        summary = estimate.outcome_summary(rows)
        model = estimate.fit(rows)
        check_invariants(self, rows, summary, model)
        self.assertEqual(summary.completed_without_duration, 1)
        self.assertEqual((summary.failed, summary.unknown), (0, 0))
        self.assertEqual(model.rows_excluded, 1)
        line = render.outcomes_text(plan_with(summary))
        self.assertIn("claude-code 0 of 51 (0.0%; 0 failed, 0 unknown)", line)
        self.assertTrue(line.endswith(" 1 completed row with no duration is left out too."))
        two = dataclasses.replace(summary, completed_without_duration=2)
        line = render.outcomes_text(plan_with(two))
        self.assertTrue(line.endswith(" 2 completed rows with no duration are left out too."))

    def test_an_outcome_outside_the_enum_names_the_problem_not_the_value(self):
        rows = [row(outcome="CANARY_OUTCOME")]
        with self.assertRaises(ValueError) as caught:
            estimate.outcome_summary(rows)
        self.assertNotIn("CANARY", str(caught.exception))
        self.assertIn("outcome", str(caught.exception))
        with self.assertRaises(ValueError):
            estimate.fit([*dataset(sessions=10, runs_per_session=5), *rows])

    def test_the_outcomes_are_the_schema_enum(self):
        self.assertEqual(estimate.OUTCOMES, schema.enum("outcome"))

    def test_counting_draws_nothing_and_leaves_the_range_alone(self):
        rows = mixed()
        model = estimate.fit(rows)
        state = random.getstate()
        estimate.outcome_summary(rows)
        self.assertEqual(random.getstate(), state)
        tasks, cap = estimate.tasks_from_json(PLAN)
        counted = estimate.estimate(model, tasks, cap, draws=500)
        blank = estimate.estimate(
            dataclasses.replace(model, outcomes=OutcomeSummary()), tasks, cap, draws=500
        )
        self.assertEqual(dataclasses.replace(counted, outcomes=OutcomeSummary()), blank)
        self.assertEqual(counted.outcomes, model.outcomes)


class Text(unittest.TestCase):
    def test_the_line_sits_directly_after_the_method_line(self):
        rows = mixed()
        tasks, cap = estimate.tasks_from_json(PLAN)
        plan = estimate.estimate(estimate.fit(rows), tasks, cap, draws=500)
        lines = render.plan_text(plan).splitlines()
        method = next(i for i, line in enumerate(lines) if line.startswith("Method: "))
        self.assertEqual(
            lines[method],
            f"Method: shrunk. {plan.rows_used} runs, {plan.rows_excluded} not completed so "
            "excluded. 500 simulations, seed 20261107.",
        )
        self.assertEqual(lines[method + 1], render.outcomes_text(plan))
        self.assertEqual(
            lines[method + 1],
            "Not completed, by harness: claude-code 3 of 120 (2.5%; 1 failed, 2 unknown), "
            "codex 1 of 4 (25.0%; 1 failed, 0 unknown). These are recorded harness outcomes, not "
            "task acceptance, and they are left out of the fit. 1 completed row with no duration "
            "is left out too.",
        )
        without = dataclasses.replace(plan, outcomes=OutcomeSummary())
        self.assertEqual(
            render.plan_text(without).splitlines(), lines[: method + 1] + lines[method + 2 :]
        )

    def test_no_line_when_nothing_was_counted(self):
        self.assertIsNone(render.outcomes_text(plan_with(OutcomeSummary())))
        self.assertNotIn("Not completed", render.plan_text(plan_with(OutcomeSummary())))

    def test_the_line_never_speaks_of_attempts_or_acceptance_rates(self):
        line = render.outcomes_text(plan_with(estimate.outcome_summary(mixed())))
        for word in BANNED:
            self.assertNotIn(word, line.lower())

    def test_the_seed_line_matches_an_independent_recount(self):
        """Counted from the seed rows as loaded, so the line follows the data, never constants."""
        keys = {}
        for file in sorted(paths.seed_dir().glob("*.jsonl")):
            for text in file.read_text(encoding="utf-8").splitlines():
                if text.strip():
                    item = json.loads(text)
                    key = (item["contributor"], item["batch"], item["offset_s"], item["duration_s"])
                    keys[key] = (item["harness"], item["outcome"])
        counts = Counter(keys.values())
        parts = []
        for harness in sorted({harness for harness, _ in counts}):
            total = sum(n for (h, _), n in counts.items() if h == harness)
            failed, unknown = counts[(harness, "failed")], counts[(harness, "unknown")]
            parts.append(
                f"{harness} {failed + unknown} of {total} "
                f"({(failed + unknown) / total:.1%}; {failed} failed, {unknown} unknown)"
            )
        rows = schema.read_dataset([paths.seed_dir()])
        tasks, cap = estimate.tasks_from_json(json.loads(paths.example_plan().read_text("utf-8")))
        plan = estimate.estimate(estimate.fit(rows), tasks, cap, draws=200)
        self.assertEqual(
            render.outcomes_text(plan),
            f"Not completed, by harness: {', '.join(parts)}. These are recorded harness outcomes, "
            "not task acceptance, and they are left out of the fit.",
        )
        self.assertRegex(
            render.outcomes_text(plan),
            r"^Not completed, by harness: claude-code \d+ of \d+ \(\d+\.\d%; \d+ failed, \d+ "
            r"unknown\), codex \d+ of \d+ \(\d+\.\d%; \d+ failed, \d+ unknown\)\. ",
        )


class Command(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.rows = mixed()
        self.folder = self.root / "runs"
        self.data = self.folder / "runs.jsonl"
        schema.write_rows(self.data, self.rows)
        self.plan = self.root / "plan.json"
        self.plan.write_text(json.dumps(PLAN), encoding="utf-8")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def estimate(self, *extra, data=None):
        sources = data if data is not None else ("--data", str(self.data))
        code, out, err = self.run_cli(
            "estimate", str(self.plan), *sources, "--draws", "300", *extra
        )
        self.assertEqual(code, 0, err)
        return out, err

    def test_the_json_counts_the_rows_given_with_data(self):
        out, _ = self.estimate("--json")
        shown = json.loads(out)
        summary = estimate.outcome_summary(self.rows)
        self.assertEqual(shown["outcomes"], json.loads(json.dumps(dataclasses.asdict(summary))))
        self.assertEqual(
            shown["outcomes"]["by_harness"][1],
            {"harness": "codex", "rows": 4, "completed": 3, "failed": 1, "unknown": 0},
        )
        self.assertEqual(shown["rows_used"], summary.completed - summary.completed_without_duration)
        self.assertEqual(
            shown["rows_excluded"],
            summary.failed + summary.unknown + summary.completed_without_duration,
        )
        self.assertEqual([key for key in shown if key != "idle_cap"][-1], "outcomes")

    def test_duplicate_sources_count_each_run_once(self):
        once, _ = self.estimate("--json")
        twice, _ = self.estimate(
            "--json", data=("--data", str(self.data), "--data", str(self.folder))
        )
        self.assertEqual(json.loads(twice)["outcomes"], json.loads(once)["outcomes"])
        self.assertEqual(json.loads(once)["outcomes"]["rows"], len(self.rows))
        copy = self.root / "copy.jsonl"
        schema.write_rows(copy, self.rows)
        again, _ = self.estimate("--json", data=("--data", str(self.data), "--data", str(copy)))
        self.assertEqual(json.loads(again)["outcomes"], json.loads(once)["outcomes"])

    def test_only_the_rows_given_with_data_are_counted(self):
        schema.write_rows(paths.local_runs(), [row(harness="codex", outcome="failed")])
        out, _ = self.estimate("--json")
        self.assertEqual(json.loads(out)["outcomes"]["rows"], len(self.rows))
        line = next(line for line in self.estimate()[0].splitlines() if "by harness" in line)
        self.assertIn("codex 1 of 4 (25.0%; 1 failed, 0 unknown)", line)

    def test_stderr_is_unchanged(self):
        _, err = self.estimate()
        self.assertEqual(err, "")

    def test_a_record_carries_the_outcomes_shown(self):
        out, _ = self.estimate("--json", "--record")
        (record,) = forecasts.read(paths.forecasts_file()).forecasts.values()
        self.assertEqual(record["outcomes"], json.loads(out)["outcomes"])
        self.assertEqual(forecasts.record_id(record), record["id"])

    def test_an_old_record_without_outcomes_still_reads_and_scores(self):
        self.estimate("--json", "--record")
        (record,) = forecasts.read(paths.forecasts_file()).forecasts.values()
        old = {key: value for key, value in record.items() if key not in ("outcomes", "id")}
        old["version"] = "0.9.2"
        old["id"] = forecasts.record_id(old)
        paths.forecasts_file().write_text("", encoding="utf-8")
        forecasts.append(paths.forecasts_file(), old)
        ledger = forecasts.read(paths.forecasts_file())
        self.assertEqual((list(ledger.forecasts), ledger.skipped), ([old["id"]], 0))
        code, out, err = self.run_cli("score", old["id"], "--actual", "2h")
        self.assertEqual(code, 0, err)
        self.assertIn(f"Recorded score for {old['id']}.", out)
        (score,) = forecasts.read(paths.forecasts_file()).scores
        self.assertEqual(score["actual_s"], 7200)
        code, out, _ = self.run_cli("score")
        self.assertEqual(code, 0)
        self.assertIn("n = 1", out)

    def test_a_hand_built_result_without_outcomes_records_none(self):
        result = {
            "method": "shrunk",
            "seed": 1,
            "draws": 10,
            "rows_used": 20,
            "rows_excluded": 0,
            "p50": 1.0,
            "p80": 2.0,
            "p90": 3.0,
            "tasks": [],
            "human": {"status": "not measured", "included_in_total": False},
        }
        record = forecasts.forecast_record(
            issued_at="2026-11-08T10:00:00Z",
            version="0.10.0",
            plan_document=PLAN,
            result=result,
            quoted=None,
        )
        self.assertNotIn("outcomes", record)


class Backtest(unittest.TestCase):
    def test_the_published_pages_never_print_the_counts(self):
        for name in ("backtest.py", "report.py", "share.py", "site.py"):
            with self.subTest(module=name):
                text = (REPO / "src" / "agentpace" / name).read_text(encoding="utf-8")
                self.assertNotRegex(text, re.compile(r"outcomes_text|\.outcomes\b"))


if __name__ == "__main__":
    unittest.main()

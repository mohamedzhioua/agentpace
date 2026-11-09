"""A plan's `risk` is read as the recorded category it matches against (0.12.0).

In the rows, risk is the agent's role, not a rating of the work, so a written risk no row can
record at that kind takes the kind's recorded risk and the output says so. `--use-risk` looks every
written value up as given. The rule lives only at the plan boundary, `tasks_from_json`.
"""

import ast
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, paths, render, schema
from agentpace import estimate as est
from tests.synthetic import dataset, row

REPO = Path(__file__).resolve().parent.parent
OPUS = "claude-opus-5-5"
DRAWS = "500"

NOTE = (
    "risk as written was not used for api (high -> normal), review (normal -> low): in the "
    "recorded runs, risk is the agent's role (review and research agents low, migration agents "
    "high, everything else normal), not how hard or how reversible the work is, so each took the "
    "risk recorded for its kind. Pass --use-risk to look the values up as written."
)


def research_rows():
    """`other` at recorded risk `low`, the research cell, with enough runs to be its own cell."""
    return [
        row(
            task_kind="other",
            risk="low",
            size="na",
            duration_s=200 + 17 * i,
            batch=f"{1000 + i // 4:012x}",
            offset_s=(i % 4) * 300,
        )
        for i in range(40)
    ]


class PlanRisk(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.rows = [*dataset(), *research_rows()]
        self.data = self.root / "runs.jsonl"
        schema.write_rows(self.data, self.rows)

    def run_cli(self, document, *args):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(document), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(
                ["estimate", str(plan), "--data", str(self.data), "--draws", DRAWS, *args]
            )
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue()

    def run_json(self, document, *args):
        return json.loads(self.run_cli(document, "--json", *args))

    @staticmethod
    def plan(*tasks):
        return {"agent_cap": 2, "model": OPUS, "tasks": list(tasks)}

    def test_a_feature_marked_high_is_looked_up_as_recorded(self):
        written = self.run_json(self.plan({"id": "api", "kind": "feature", "risk": "high"}))
        recorded = self.run_json(self.plan({"id": "api", "kind": "feature", "risk": "normal"}))
        self.assertEqual(written["tasks"][0]["cell"], f"feature x normal x {OPUS}")
        self.assertTrue(written["warnings"])
        self.assertEqual(recorded["warnings"], [])
        del written["warnings"], recorded["warnings"]
        self.assertEqual(written, recorded)

    def test_use_risk_reproduces_the_written_cell(self):
        document = self.plan({"id": "api", "kind": "feature", "risk": "high"})
        given = self.run_json(document, "--use-risk")
        self.assertEqual(given["tasks"][0]["cell"], "feature x high")
        self.assertFalse(any(w.startswith("risk as written") for w in given["warnings"]))
        # The same cell and numbers as a task built with the written risk, as before 0.12.0.
        direct = est.estimate(
            est.fit(self.rows), [est.Task("api", "feature", "high", model=OPUS)], 2, int(DRAWS)
        )
        given.pop("idle_cap")
        self.assertEqual(given, json.loads(render.plan_json(direct)))

    def test_a_research_task_keeps_its_cell(self):
        result = self.run_json(self.plan({"id": "probe", "kind": "other", "risk": "low"}))
        self.assertEqual(result["tasks"][0]["cell"], f"other x low x {OPUS}")
        self.assertFalse(any(w.startswith("risk as written") for w in result["warnings"]))

    def test_the_note_names_each_replaced_task_in_simulation_order(self):
        out = self.run_cli(
            self.plan(
                {"id": "review", "kind": "review", "risk": "normal", "depends_on": ["api"]},
                {"id": "api", "kind": "feature", "risk": "high"},
                {"id": "docs", "kind": "docs"},
            )
        )
        self.assertIn(f"  - {NOTE}\n", out)
        self.assertEqual(out.count("risk as written"), 1)

    def test_no_note_when_nothing_was_replaced(self):
        out = self.run_cli(
            self.plan(
                {"id": "api", "kind": "feature"},
                {"id": "review", "kind": "review", "risk": "low", "depends_on": ["api"]},
                {"id": "probe", "kind": "other", "risk": "low"},
            )
        )
        self.assertNotIn("risk as written", out)
        out = self.run_cli(
            self.plan({"id": "api", "kind": "feature", "risk": "high"}), "--use-risk"
        )
        self.assertNotIn("risk as written", out)

    def test_the_shipped_examples_name_only_recordable_risks(self):
        """So the README's example blocks do not move: every written risk is used as written."""
        for name in ("plan.json", "quote-plan.json"):
            document = json.loads((REPO / "examples" / name).read_text(encoding="utf-8"))
            with self.subTest(plan=name):
                tasks, _ = est.tasks_from_json(document)
                given, _ = est.tasks_from_json(document, use_risk=True)
                self.assertEqual(tasks, given)
                self.assertTrue(all(task.risk_given == task.risk for task in tasks))

    def test_the_example_json_is_the_same_with_and_without_use_risk(self):
        args = ("estimate", "--example", "--json", "--data", str(self.data), "--draws", DRAWS)
        outputs = []
        for extra in ((), ("--use-risk",)):
            out = io.StringIO()
            with mock.patch("sys.stdin", io.StringIO("")), contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([*args, *extra]), 0)
            outputs.append(out.getvalue())
        self.assertEqual(outputs[0], outputs[1])
        self.assertTrue(paths.example_plan().exists())


class Boundary(unittest.TestCase):
    def test_the_backtest_never_reads_a_plan_document(self):
        """The backtest's held-out queries carry the row's own risk; the plan rule must not
        reach them."""
        source = (REPO / "src" / "agentpace" / "backtest.py").read_text(encoding="utf-8")
        names = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        imported = {
            alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        self.assertNotIn("tasks_from_json", names | imported)
        self.assertNotIn("recorded_risk", names | imported)


if __name__ == "__main__":
    unittest.main()

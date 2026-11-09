"""`model-offset-v1` (experiments/model_offset/PROTOCOL.md, "Tests"), on SYNTHETIC rows."""

import ast
import contextlib
import io
import json
import math
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from experiments.model_offset import __main__ as report
from experiments.model_offset import offset

from agentpace import backtest, schema
from agentpace import estimate as est
from tests.synthetic import dataset, row

REPO = Path(__file__).resolve().parent.parent
A = "claude-opus-5-5"
B = "claude-sonnet-5-5"
LOG_BASE = {("feature", "normal"): math.log(300.0), ("docs", "normal"): math.log(60.0)}


def rows(effect_b, per_cell=300, seed=11):
    """`per_cell` runs per kind and model; model B's log duration moves by `effect_b[kind]`.
    `review` at low risk holds model A only, so B there is an unseen model under a seen cell."""
    rng = random.Random(seed)
    made = []
    cells = [(kind, risk, model) for kind, risk in LOG_BASE for model in (A, B)]
    for kind, risk, model in cells:
        shift = effect_b[kind] if model == B else 0.0
        for i in range(per_cell):
            value = LOG_BASE[(kind, risk)] + shift + rng.gauss(0.0, 0.3)
            made.append(
                row(
                    task_kind=kind,
                    risk=risk,
                    model=model,
                    duration_s=max(1, round(math.exp(value))),
                    batch=f"{kind[:2]}{model[7:9]}{i // 10:08x}",
                    offset_s=i,
                )
            )
    for i in range(per_cell):
        value = math.log(120.0) + rng.gauss(0.0, 0.3)
        made.append(
            row(
                task_kind="review",
                risk="low",
                model=A,
                size="na",
                duration_s=max(1, round(math.exp(value))),
                batch=f"rv{i // 10:010x}",
                offset_s=i,
            )
        )
    return made


def query(kind, risk, model):
    return {"task_kind": kind, "risk": risk, "model": model, "size": None}


class Fit(unittest.TestCase):
    def test_an_additive_model_effect_is_recovered(self):
        model = offset.fit(rows({"feature": 1.0, "docs": 1.0}))
        self.assertLess(abs(model.beta[B] - model.beta[A] - 1.0), 0.1)

    def test_a_pure_interaction_carries_no_offset_and_widens_the_cell(self):
        train = rows({"feature": 1.0, "docs": -1.0})
        model = offset.fit(train)
        self.assertLess(abs(model.beta[B] - model.beta[A]), 0.1)
        unseen = query("review", "low", B)
        incumbent = est.fit(train, "shrunk").locate(unseen)
        challenger = model.locate(unseen)
        self.assertEqual(incumbent.missing, "model")
        self.assertLess(abs(challenger.mu - incumbent.mu), 0.1)
        self.assertGreater(challenger.v, incumbent.v)

    def test_other_queries_get_the_incumbents_cell_exactly(self):
        train = rows({"feature": 1.0, "docs": 1.0})
        model = offset.fit(train)
        incumbent = est.fit(train, "shrunk")
        for name, kind, risk, model_name in (
            ("unaffected", "feature", "normal", A),
            ("unknown model", "feature", "normal", "gpt-unseen"),
            ("unseen kind", "migration", "high", A),
            ("unseen risk", "feature", "high", A),
        ):
            with self.subTest(name):
                found = query(kind, risk, model_name)
                self.assertEqual(model.locate(found), incumbent.locate(found))

    def test_a_model_only_in_the_test_week_has_no_offset(self):
        canary = "canary-model"
        made = dataset(sessions=60, runs_per_session=8, weeks=6)
        made += [
            row(model=canary, week="2026-W06", batch="cccccccccccc", offset_s=i * 100)
            for i in range(5)
        ]
        fold = backtest.time_folds(made)[-1]
        self.assertIn(canary, {r["model"] for r in fold.test})
        model = offset.fit(fold.train)
        self.assertNotIn(canary, model.beta)
        self.assertIn(A, model.beta)


class Boundary(unittest.TestCase):
    def test_the_package_never_names_experiments(self):
        for path in sorted((REPO / "src" / "agentpace").rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                elif isinstance(node, ast.Name):
                    names = [node.id]
                elif isinstance(node, ast.Attribute):
                    names = [node.attr]
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names = [node.value] if node.value.startswith("experiments") else []
                for name in names:
                    with self.subTest(file=path.name):
                        self.assertNotEqual(name.split(".")[0], "experiments")


class Check(unittest.TestCase):
    def run_main(self, root, out, *argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return report.main(list(argv), root=root, out=out)

    def test_check_round_trips(self):
        with tempfile.TemporaryDirectory() as folder:
            root, out = Path(folder), Path(folder) / "out"
            out.mkdir()
            schema.write_rows(root / "data" / "runs" / "rows.jsonl", dataset())
            (root / "docs").mkdir()
            status = {"locked": {"status": "not enough data (no weeks after 2026-W45 yet)"}}
            (root / "docs" / "backtest.json").write_text(json.dumps(status), encoding="utf-8")

            self.assertEqual(self.run_main(root, out, "--check"), 1)  # nothing written yet
            self.assertEqual(self.run_main(root, out), 0)
            self.assertEqual(self.run_main(root, out, "--check"), 0)
            stored = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["decision"], {"status": "open", "line": report.AWAITING})
            self.assertIn("no weeks after 2026-W45 yet", (out / "report.md").read_text("utf-8"))

            page = out / "report.md"
            page.write_text(page.read_text(encoding="utf-8") + "edited\n", encoding="utf-8")
            self.assertEqual(self.run_main(root, out, "--check"), 1)

            # A closed decision is reprinted on later data, never decided again.
            stored["decision"] = {
                "status": "closed",
                "outcome": "not shown",
                "line": "not shown, closed",
                "latest_week": "2026-W47",
                "data_sha256": "0" * 64,
                "gates": dict.fromkeys(report.GATES, "pass"),
            }
            (out / "report.json").write_text(json.dumps(stored), encoding="utf-8")
            schema.write_rows(root / "data" / "runs" / "more.jsonl", [row(batch="dddddddddddd")])
            self.assertEqual(self.run_main(root, out), 0)
            again = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(again["decision"], stored["decision"])
            self.assertNotEqual(again["data"]["sha256"], stored["data"]["sha256"])
            self.assertEqual(self.run_main(root, out, "--check"), 0)

    def test_the_locked_path_runs_end_to_end(self):
        # Model B is seen only in review/low in the first week, then given features in the first
        # locked week, so that fold has affected rows and sessions.
        made = dataset()
        made += [
            row(
                task_kind="review",
                risk="low",
                size="na",
                model=B,
                week="2026-W01",
                batch="b10000000000",
                offset_s=i * 100,
                duration_s=100 + i,
            )
            for i in range(6)
        ]
        made += [
            row(
                model=B,
                week="2026-W04",
                batch=f"b4{s:010x}",
                offset_s=i * 100,
                duration_s=200 + 10 * i + s,
            )
            for s in range(3)
            for i in range(5)
        ]
        # Fewer draws keep it fast; this checks the path, not the numbers.
        with (
            mock.patch.object(backtest, "CELL_DRAWS", 400),
            mock.patch.object(backtest, "PLAN_DRAWS", 200),
            mock.patch.object(backtest, "BOOTSTRAPS", 200),
        ):
            found = report.build(made, "ok", "0" * 64, None, last_week="2026-W03")
        locked = found["locked"]
        self.assertEqual(locked["status"], "ok")
        self.assertGreater(locked["affected"]["claude_code_sessions"], 0)
        self.assertTrue(all(i is not None for i in found["gates"]["G1"]["intervals"]))
        self.assertIn(found["decision"]["status"], ("open", "closed"))
        _, page = report.render(found)
        self.assertIn("### Paired intervals", page)

        # With G0 forced to hold, the rule decides once and closes.
        gates = json.loads(json.dumps(found["gates"]))
        gates["G0"]["result"] = "pass"
        decided = report.decide(gates, "2026-W06", "0" * 64, None)
        self.assertEqual(decided["status"], "closed")
        self.assertIn(decided["outcome"], ("win", "lose", "not shown"))


if __name__ == "__main__":
    unittest.main()

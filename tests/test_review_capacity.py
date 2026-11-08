"""`estimate --review-capacity`: the user's own figure, verbatim and never applied (ruling 0.10)."""

import ast
import contextlib
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, paths, render, schema
from agentpace.estimate import HumanPart
from tests.synthetic import dataset, row
from tests.test_skills import FRONTMATTER_SHA256
from tests.test_turns import SESSION

REPO = Path(__file__).resolve().parent.parent
MESSAGE = 'agentpace: --review-capacity needs one short line, such as "4 PRs a day"'
VALID = (
    "4 PRs a day",
    "about 6 reviews per week",
    "one afternoon a week",
    "x" * 80,
)
INVALID = ("", "   ", "4 PRs a day\nand more", "x" * 81)


def capacity_line(stated):
    return (
        f'Review capacity: "{stated}" (stated by you; not measured, not applied to the total: '
        "a plan task is an agent run, not a pull request)"
    )


class Temp(unittest.TestCase):
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
        document = {
            "agent_cap": 2,
            "tasks": [
                {"id": "a", "kind": "feature", "size": "M"},
                {"id": "b", "kind": "docs", "depends_on": ["a"]},
            ],
        }
        self.plan.write_text(json.dumps(document), encoding="utf-8")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO("")),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def estimate(self, *extra):
        return self.run_cli(
            "estimate", str(self.plan), "--data", str(self.data), "--draws", "500", *extra
        )


class StatedFigure(Temp):
    def test_valid_figures_show_verbatim_directly_under_the_human_line(self):
        for stated in VALID:
            with self.subTest(stated=stated):
                code, out, _ = self.estimate("--review-capacity", stated)
                self.assertEqual(code, 0)
                lines = out.splitlines()
                human = next(i for i, line in enumerate(lines) if line.startswith("Human part"))
                self.assertEqual(lines[human + 1], capacity_line(stated))
                self.assertEqual(sum(line.startswith("Review capacity") for line in lines), 1)

    def test_the_figure_is_stripped_and_otherwise_kept_as_given(self):
        _, out, _ = self.estimate("--review-capacity", "  4 PRs a day \t")
        self.assertIn(capacity_line("4 PRs a day"), out.splitlines())
        _, raw, _ = self.estimate("--review-capacity", "4 PRs a day", "--json")
        _, padded, _ = self.estimate("--review-capacity", "  4 PRs a day \t", "--json")
        self.assertEqual(raw, padded)

    def test_bad_figures_exit_2_with_the_message_and_no_traceback(self):
        for stated in INVALID:
            with self.subTest(stated=stated):
                code, out, err = self.estimate("--review-capacity", stated)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(err, MESSAGE + "\n")
                self.assertNotIn("Traceback", err)

    def test_a_bad_figure_is_refused_before_anything_is_recorded(self):
        code, _, _ = self.estimate("--review-capacity", "x" * 81, "--record")
        self.assertEqual(code, 2)
        self.assertFalse(paths.forecasts_file().exists())

    def test_the_line_holds_no_digit_outside_the_quotes_and_no_ratio(self):
        line = render.review_capacity_text(HumanPart(review_capacity_stated="4 PRs a day"))
        assert line is not None
        outside = line.replace('"4 PRs a day"', "")
        self.assertIsNone(re.search(r"\d", outside), outside)
        for ratio in ("/", "x the", "per ", "%"):
            self.assertNotIn(ratio, outside)

    def test_no_line_without_a_stated_figure(self):
        self.assertIsNone(render.review_capacity_text(HumanPart()))


class NothingMoves(Temp):
    def test_without_the_flag_there_is_no_line_and_no_key(self):
        _, text, _ = self.estimate()
        self.assertNotIn("Review capacity", text)
        _, out, _ = self.estimate("--json")
        self.assertNotIn("review_capacity_stated", json.loads(out)["human"])

    def test_the_key_is_present_only_with_the_flag(self):
        _, out, _ = self.estimate("--json", "--review-capacity", "4 PRs a day")
        self.assertEqual(json.loads(out)["human"]["review_capacity_stated"], "4 PRs a day")

    def test_the_range_is_the_same_with_and_without_a_stated_figure(self):
        _, without, _ = self.estimate("--json")
        _, stated, _ = self.estimate("--json", "--review-capacity", "4 PRs a day")
        before, after = json.loads(without), json.loads(stated)
        for key in ("p50", "p80", "p90", "tasks", "critical_path", "warnings"):
            self.assertEqual(before[key], after[key], key)
        del after["human"]["review_capacity_stated"]
        self.assertEqual(before, after)

    def test_the_text_differs_by_exactly_the_one_line(self):
        _, without, _ = self.estimate()
        _, stated, _ = self.estimate("--review-capacity", "4 PRs a day")
        lines = stated.splitlines()
        lines.remove(capacity_line("4 PRs a day"))
        self.assertEqual(lines, without.splitlines())

    def test_the_quote_stays_on_line_2(self):
        _, out, _ = self.estimate("--quoted", "3 weeks", "--review-capacity", "4 PRs a day")
        self.assertTrue(out.splitlines()[1].startswith('Quoted: "3 weeks"'))

    def test_stderr_is_unchanged(self):
        _, _, without = self.estimate()
        _, _, stated = self.estimate("--review-capacity", "4 PRs a day")
        self.assertEqual(without, stated)
        _, _, recorded = self.estimate("--review-capacity", "4 PRs a day", "--record")
        _, _, plain = self.estimate("--record")
        ids = r"recorded forecast \S+ in"
        self.assertEqual(
            re.sub(ids, "recorded forecast ID in", recorded),
            re.sub(ids, "recorded forecast ID in", plain),
        )


class MeasuredWaits(Temp):
    def test_measured_waits_and_the_stated_figure_print_together(self):
        projects = self.root / "projects"
        folder = projects / "CANARY-PROJECT"
        folder.mkdir(parents=True)
        lines = [r if isinstance(r, str) else json.dumps(r) for r in SESSION]
        (folder / "s.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        code, _, err = self.run_cli("turns", str(projects))
        self.assertEqual(code, 0, err)
        _, out, _ = self.estimate("--review-capacity", "4 PRs a day")
        lines = out.splitlines()
        human = next(i for i, line in enumerate(lines) if line.startswith("Human part"))
        self.assertIn("measured from your history, local", lines[human])
        self.assertEqual(lines[human + 1], capacity_line("4 PRs a day"))
        _, data, _ = self.estimate("--json", "--review-capacity", "4 PRs a day")
        measured = json.loads(data)["human"]
        self.assertEqual(measured["status"], "measured")
        self.assertEqual(measured["review_capacity_stated"], "4 PRs a day")
        self.assertFalse(measured["included_in_total"])


class Ledger(Temp):
    def records(self):
        lines = paths.forecasts_file().read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def test_record_keeps_the_figure_inside_human(self):
        code, _, err = self.estimate("--review-capacity", "4 PRs a day", "--record")
        self.assertEqual(code, 0, err)
        (record,) = self.records()
        self.assertEqual(record["human"]["review_capacity_stated"], "4 PRs a day")
        self.assertNotIn("review_capacity_stated", {k for k in record if k != "human"})

    def test_nothing_is_written_without_record(self):
        code, _, _ = self.estimate("--review-capacity", "4 PRs a day")
        self.assertEqual(code, 0)
        self.assertFalse(paths.forecasts_file().exists())

    def test_score_is_unchanged_by_a_stated_figure(self):
        self.estimate("--review-capacity", "4 PRs a day", "--record")
        self.estimate("--record")
        stated, plain = self.records()
        self.assertEqual(stated["p50"], plain["p50"])
        outputs = []
        for record in (stated, plain):
            code, out, err = self.run_cli("score", record["id"], "--actual", "2h")
            self.assertEqual(code, 0, err)
            self.assertNotIn("Review capacity", out)
            self.assertNotIn("4 PRs a day", out)
            outputs.append(out.replace(record["id"], "ID"))
        self.assertEqual(outputs[0], outputs[1])
        code, summary, _ = self.run_cli("score")
        self.assertEqual(code, 0)
        self.assertNotIn("4 PRs a day", summary)

    def test_score_never_builds_a_stated_figure(self):
        self.assertIsNone(cli._human_part(self.root / "missing.jsonl").review_capacity_stated)


class Share(Temp):
    def test_share_dry_run_is_unaffected_by_a_ledger_holding_a_figure(self):
        schema.write_rows(paths.local_runs(), [row(), row(offset_s=5)])
        out_dir = str(self.root / "out")
        _, before, before_err = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.estimate("--review-capacity", "4 PRs a day", "--record")
        self.assertIn("4 PRs a day", paths.forecasts_file().read_text(encoding="utf-8"))
        code, after, after_err = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual((before, before_err), (after, after_err))
        self.assertNotIn("4 PRs a day", after + after_err)

    def test_share_imports_nothing_named_forecasts(self):
        source = (Path(cli.__file__).parent / "share.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertFalse({name for name in imported if "forecasts" in name}, imported)


class Skills(unittest.TestCase):
    def test_frontmatters_are_byte_identical(self):
        for name, digest in FRONTMATTER_SHA256.items():
            with self.subTest(skill=name):
                text = (REPO / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
                head = "".join(text.splitlines(keepends=True)[:6])
                self.assertEqual(hashlib.sha256(head.encode("utf-8")).hexdigest(), digest)

    def test_the_skills_forward_the_figure_only_when_stated(self):
        agentpace = (REPO / "skills" / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        estimate = (REPO / "skills" / "pace-estimate" / "SKILL.md").read_text(encoding="utf-8")
        for text in (agentpace, estimate):
            self.assertIn('--review-capacity "<figure>"', text)
            self.assertIn("`Review capacity:` line as printed", text)
        self.assertIn("never turn it into a timeline", agentpace)
        self.assertIn("Only if the user stated their own review capacity", estimate)


if __name__ == "__main__":
    unittest.main()

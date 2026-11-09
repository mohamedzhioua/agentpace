import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from agentpace import claims, cli, paths, schema
from agentpace import estimate as est
from agentpace.adapters import EXPERIMENTAL
from tests import synthetic

REPO = Path(__file__).resolve().parent.parent


class Apply(unittest.TestCase):
    def test_replaces_marked_values(self):
        text = "We saw <!-- claim:runs -->1<!-- /claim --> runs."
        self.assertEqual(
            claims.apply(text, {"runs": "3,000"}),
            "We saw <!-- claim:runs -->3,000<!-- /claim --> runs.",
        )

    def test_leaves_unmarked_text_alone(self):
        self.assertEqual(claims.apply("plain 42", {"runs": "1"}), "plain 42")

    def test_is_idempotent(self):
        once = claims.apply("<!-- claim:a -->x<!-- /claim -->", {"a": "y"})
        self.assertEqual(claims.apply(once, {"a": "y"}), once)

    def test_an_unknown_claim_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "unknown claim"):
            claims.apply("<!-- claim:made_up -->1<!-- /claim -->", {})

    def test_spans_multiple_lines(self):
        text = "<!-- claim:t -->\nold\n<!-- /claim -->"
        self.assertEqual(
            claims.apply(text, {"t": "\nnew\n"}), "<!-- claim:t -->\nnew\n<!-- /claim -->"
        )


class Published(unittest.TestCase):
    """Every number in the published documents must equal what the data says."""

    def setUp(self):
        self.known = claims.values(
            schema.read_dataset([paths.seed_dir()]),
            json.loads((REPO / "docs" / "backtest.json").read_text(encoding="utf-8")),
            REPO / "examples" / "plan.json",
            REPO / "examples" / "quote-plan.json",
        )

    def test_documents_match_the_data(self):
        for name in ("README.md", "skills/agentpace/assets/portable-prompt.md"):
            text = (REPO / name).read_text(encoding="utf-8")
            self.assertEqual(claims.apply(text, self.known), text, name)

    def test_contributor_split_is_reported_honestly(self):
        self.assertIn("could not run", self.known["contributor_split"])

    def test_headline_numbers_carry_intervals(self):
        for name in ("task_p80", "plan_p50", "plan_p80", "plan_p90"):
            self.assertRegex(self.known[name], r"^[\d.]+% \(CI [\d.]+% to [\d.]+%\)$", name)
        for name in ("task_pinball", "plan_pinball"):
            self.assertRegex(self.known[name], r"^[\d.]+ \(CI [\d.]+ to [\d.]+\)$", name)

    def test_the_readme_shows_the_weekly_spread(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        for name in ("weekly_p80", "weekly_p80_row", "latest_week_p80", "locked_status"):
            self.assertIn(f"<!-- claim:{name} -->", text)

    def test_the_readme_cites_its_claims(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertGreater(text.count("<!-- claim:"), 15)

    def test_data_values_appear_only_inside_markers(self):
        # `project_top_harness` is a harness name such as "codex", which the README also uses
        # as an ordinary word, for example `--source codex`. It is not a number from the data.
        words = {"project_top_harness"}
        for document in ("README.md", "skills/agentpace/assets/portable-prompt.md"):
            text = (REPO / document).read_text(encoding="utf-8")
            unmarked = claims._MARKER.sub("", text)
            for name, value in self.known.items():
                if name in words or len(value) < 3:
                    continue
                with self.subTest(document=document, claim=name):
                    self.assertNotIn(value, unmarked)


def _table_cells(table: str) -> dict[tuple[str, str, str], list[str]]:
    """The body rows of a generated cell table, keyed by kind, risk and model."""
    cells = {}
    for line in table.strip().splitlines()[2:]:
        kind, risk, model, *rest = (part.strip() for part in line.strip("|").split("|"))
        cells[(kind, risk, model)] = rest
    return cells


class CellTable(unittest.TestCase):
    """The portable prompt's table mirrors the CLI's back-off and shows no range below 30 runs."""

    def rows(self, count, **fields):
        return [synthetic.row(**{"duration_s": 60 + i, **fields}) for i in range(count)]

    def test_two_models_in_one_kind_get_different_quantiles(self):
        rows = self.rows(40, model="claude-opus-5-5", duration_s=3000)
        rows += self.rows(40, model="claude-sonnet-5-5")
        cells = _table_cells(claims._cell_table(rows))
        opus = cells[("feature", "normal", "claude-opus-5-5")]
        sonnet = cells[("feature", "normal", "claude-sonnet-5-5")]
        self.assertEqual(opus[0], "40")
        self.assertEqual(sonnet[0], "40")
        self.assertNotEqual(opus[1:], sonnet[1:])
        self.assertEqual(cells[("feature", "normal", "all models")][0], "80")

    def test_a_completed_row_with_no_duration_is_left_out(self):
        rows = [*self.rows(30), synthetic.row(duration_s=0)]
        cells = _table_cells(claims._cell_table(rows))
        self.assertEqual(cells[("feature", "normal", "claude-opus-5-5")][0], "30")
        self.assertEqual(cells[("all kinds", "all", "all models")][0], "30")

    def test_a_cell_of_ten_to_twenty_nine_runs_shows_no_range(self):
        for count in (10, 29):
            with self.subTest(count=count):
                table = claims._cell_table(self.rows(count))
                cells = _table_cells(table)
                self.assertEqual(
                    cells[("feature", "normal", "claude-opus-5-5")],
                    [str(count), "too few runs", "", ""],
                )
                self.assertIn(f"| {count} | too few runs | | |", table)
        cells = _table_cells(claims._cell_table(self.rows(30)))
        self.assertNotIn("too few runs", cells[("feature", "normal", "claude-opus-5-5")])

    def test_a_cell_under_ten_runs_is_absent(self):
        rows = self.rows(30) + self.rows(9, model="gpt-x")
        cells = _table_cells(claims._cell_table(rows))
        self.assertNotIn(("feature", "normal", "gpt-x"), cells)
        self.assertEqual(cells[("feature", "normal", "all models")][0], "39")

    def test_all_kinds_counts_every_usable_row(self):
        rows = synthetic.dataset(seed=7, sessions=60)
        rows += [synthetic.row(outcome="failed"), synthetic.row(duration_s=0)]
        usable = [r for r in rows if r["outcome"] == "completed" and r["duration_s"] > 0]
        cells = _table_cells(claims._cell_table(rows))
        self.assertEqual(cells[("all kinds", "all", "all models")][0], f"{len(usable):,}")

    def test_pooled_rows_lead_each_group_and_models_follow_by_runs(self):
        rows = self.rows(12, model="b-model") + self.rows(12, model="a-model")
        rows += self.rows(20, model="c-model") + self.rows(40, task_kind="docs")
        keys = list(_table_cells(claims._cell_table(rows)))
        self.assertEqual(
            keys,
            [
                ("docs", "normal", "all models"),
                ("docs", "normal", "claude-opus-5-5"),
                ("feature", "normal", "all models"),
                ("feature", "normal", "c-model"),
                ("feature", "normal", "a-model"),
                ("feature", "normal", "b-model"),
                ("all kinds", "all", "all models"),
            ],
        )

    def test_the_prompt_states_no_table_value_outside_the_table(self):
        text = (REPO / "skills" / "agentpace" / "assets" / "portable-prompt.md").read_text(
            encoding="utf-8"
        )
        table = next(
            match.group(2)
            for match in claims._MARKER.finditer(text)
            if match.group(1) == "cell_table"
        )
        unmarked = claims._MARKER.sub("", text)
        durations = set(re.findall(r"\d[\d,]*(?:\.\d)? (?:s|min|h|days)", table))
        # The prose names the pooling threshold ("fewer than 30 runs") and the quantile labels
        # (P50, P80, P90). A cell whose run count equals one of those is not a number leaking
        # from the table, so a regenerated table that lands on it must not fail here.
        labels = {str(est.POOL_MIN), *(f"{q * 100:.0f}" for q in est.QUANTILES)}
        counts = {match.strip("| ") for match in re.findall(r"\| [\d,]+ \|", table)} - labels
        self.assertTrue(durations)
        self.assertTrue(counts)
        for token in durations | counts:
            with self.subTest(token=token):
                self.assertNotRegex(unmarked, rf"(?<![\d,.]){re.escape(token)}(?![\d,])")


class QuoteExample(unittest.TestCase):
    """The README re-answers a quoted timeline with the tool's real output, never a ratio."""

    @classmethod
    def setUpClass(cls):
        cls.readme = (REPO / "README.md").read_text(encoding="utf-8")
        start = cls.readme.index("### Re-answer a quoted timeline")
        cls.section = cls.readme[start : cls.readme.index("\n## ", start)]

    def test_the_block_is_claim_marked_and_quotes_the_example(self):
        self.assertIn("<!-- claim:quote_example_output -->", self.section)
        self.assertIn(f'--quoted "{claims.QUOTE_EXAMPLE}"', self.section.replace("\n", " "))
        self.assertIn("(examples/quote-plan.json)", self.section)
        block = claims._MARKER.search(self.section).group(2).splitlines()
        self.assertTrue(block[1].startswith("Plan total on the session clock: "))
        self.assertEqual(
            block[2],
            f'Quoted: "{claims.QUOTE_EXAMPLE}" (calendar time, as quoted; unverified, not '
            "converted)",
        )

    def test_the_prose_never_compares_the_two(self):
        prose = claims._MARKER.sub("", self.section)
        self.assertNotRegex(prose, r"(?i)faster|slower|ratio|\d\s*x\b|times (as|faster)")
        for phrase in ("calendar time, as quoted", "measured agent time", "human part"):
            self.assertIn(phrase, prose)

    def test_the_quote_plan_has_about_six_tasks_and_is_not_bundled(self):
        plan = json.loads((REPO / "examples" / "quote-plan.json").read_text(encoding="utf-8"))
        self.assertTrue(5 <= len(plan["tasks"]) <= 7)
        self.assertTrue(any(task.get("depends_on") for task in plan["tasks"]))
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertNotIn("quote-plan.json", pyproject)

    def test_the_quote_plan_uses_the_quickstart_model(self):
        """The README's examples share the quickstart's model, so none is picked for effect."""
        quickstart = json.loads((REPO / "examples" / "plan.json").read_text(encoding="utf-8"))
        plan = json.loads((REPO / "examples" / "quote-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["model"], quickstart["model"])
        self.assertFalse(any("model" in task for task in plan["tasks"]))

    def test_the_seed_has_no_experimental_rows(self):
        rows = schema.read_dataset([paths.seed_dir()])
        self.assertFalse([row for row in rows if row["harness"] in EXPERIMENTAL])


class Command(unittest.TestCase):
    def test_check_fails_on_a_stale_number(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "doc.md"
            path.write_text("<!-- claim:runs -->1<!-- /claim -->", encoding="utf-8")
            args = [
                "claims",
                str(path),
                "--report",
                str(REPO / "docs" / "backtest.json"),
                "--data",
                str(paths.seed_dir()),
            ]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([*args, "--check"]), 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(cli.main([*args, "--check"]), 0)
            self.assertNotIn(">1<", path.read_text(encoding="utf-8"))


class NotCompletedByHarness(unittest.TestCase):
    """The README states the not-completed runs by harness only through a generated claim."""

    @classmethod
    def setUpClass(cls):
        cls.readme = (REPO / "README.md").read_text(encoding="utf-8")
        cls.value = claims.values(
            schema.read_dataset([paths.seed_dir()]),
            json.loads((REPO / "docs" / "backtest.json").read_text(encoding="utf-8")),
        )["not_completed_by_harness"]

    def test_the_limitations_bullet_carries_the_marker(self):
        bullet = next(
            line
            for line in self.readme.splitlines()
            if line.startswith("- **Failed runs are under-counted.**")
        )
        self.assertIn("<!-- claim:not_completed_by_harness -->", bullet)
        self.assertIn("not task acceptance", bullet)

    def test_the_value_names_each_harness_with_counts_and_shares(self):
        self.assertRegex(
            self.value,
            r"^[a-z-]+ [\d,]+ of [\d,]+ \(\d+\.\d%\)(, [a-z-]+ [\d,]+ of [\d,]+ \(\d+\.\d%\))*$",
        )

    def test_its_shares_appear_only_inside_markers(self):
        unmarked = claims._MARKER.sub("", self.readme)
        shares = re.findall(r"\d+\.\d%", self.value)
        self.assertTrue(shares)
        for share in shares:
            with self.subTest(share=share):
                self.assertNotRegex(unmarked, rf"(?<![\d.]){re.escape(share)}")


PROJECT_PLAN_CLAIMS = (
    "project_plan_sessions",
    "project_plan_projects",
    "project_plan_p80",
    "project_plan_p90",
    "project_plan_gap",
)


class ProjectPlans(unittest.TestCase):
    """Whole sessions with projects held out reach the README only through five claims."""

    @classmethod
    def setUpClass(cls):
        cls.rows = schema.read_dataset([paths.seed_dir()])
        cls.report = json.loads((REPO / "docs" / "backtest.json").read_text(encoding="utf-8"))
        cls.readme = (REPO / "README.md").read_text(encoding="utf-8")

    def known(self, project_plans):
        found = {key: value for key, value in self.report.items() if key != "project_plans"}
        if project_plans is not None:
            found["project_plans"] = project_plans
        return claims.values(self.rows, found)

    def test_values_follow_the_report(self):
        # Not today's values, so a claim that stopped reading the report would fail here.
        ours = {"coverage80": 72.41, "coverage90": 88.62}
        held_out = {
            "status": "ok",
            "folds": 7,
            "sessions": 41,
            "results": {"agentpace": ours},
            "intervals": {
                "coverage80": [61.2, 83.9],
                "coverage90": [80.1, 97.0],
                "rival_minus_ours": {"sum-of-medians": [-345.6, 9876.4]},
            },
        }
        known = self.known(held_out)
        self.assertEqual(
            [known[name] for name in PROJECT_PLAN_CLAIMS],
            ["41", "7", "72% (CI 61% to 84%)", "89% (CI 80% to 97%)", "-346 to +9876"],
        )

    def test_not_available_unless_it_ran(self):
        could_not = {
            "status": "could not run (fewer than 2 projects with a qualifying session)",
            "label": "oracle-structure",
        }
        for project_plans in (could_not, None):
            known = self.known(project_plans)
            for name in PROJECT_PLAN_CLAIMS:
                with self.subTest(name=name, absent=project_plans is None):
                    self.assertEqual(known[name], "not available")

    def test_the_readme_carries_the_five_markers_in_one_retrospective_bullet(self):
        bullet = next(
            line
            for line in self.readme.splitlines()
            if "<!-- claim:project_plan_sessions -->" in line
        )
        self.assertTrue(bullet.startswith("- "))
        for name in PROJECT_PLAN_CLAIMS:
            self.assertEqual(self.readme.count(f"<!-- claim:{name} -->"), 1, name)
            self.assertIn(f"<!-- claim:{name} -->", bullet)
        self.assertIn("retrospective", bullet)
        self.assertIn("<!-- claim:contributors -->", bullet)
        self.assertNotIn("one contributor", bullet)
        self.assertIn("never chooses the method", bullet)
        self.assertIn("docs/backtest.md#whole-sessions-projects-held-out-oracle-structure", bullet)
        self.assertIn("This is the forward test of plan totals.", self.readme)
        self.assertNotIn("This is the only direct test of plan totals.", self.readme)


if __name__ == "__main__":
    unittest.main()

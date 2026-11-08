import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, forecasts, ingest, paths, render, schema
from agentpace.estimate import HumanPart, PlanRange, TaskRange
from tests.synthetic import dataset, row
from tests.test_skills import BANNED_PHRASES

MEASURED = HumanPart(
    status="measured",
    source="your history, local",
    per_turn_p50=60.0,
    per_turn_p80=120.0,
    per_turn_p90=180.0,
    gaps=4,
    sessions=2,
)


class EstimateCommand(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        # Keep the user's own measured waits out of these results.
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

    def run_cli(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_text_output_is_always_a_range(self):
        code, out, _ = self.run_cli(
            "estimate", str(self.plan), "--data", str(self.data), "--draws", "500"
        )
        self.assertEqual(code, 0)
        for marker in ("P50", "P80", "P90", "Critical path", "Effective parallelism"):
            self.assertIn(marker, out)

    def test_example_runs_the_shipped_plan(self):
        example = Path(__file__).resolve().parent.parent / "examples" / "plan.json"
        common = ("--data", str(self.data), "--draws", "500")
        code, by_flag, _ = self.run_cli("estimate", "--example", *common)
        self.assertEqual(code, 0)
        _, by_file, _ = self.run_cli("estimate", str(example), *common)
        self.assertEqual(by_flag, by_file)

    def test_example_and_a_plan_together_is_an_error(self):
        code, _, err = self.run_cli("estimate", str(self.plan), "--example")
        self.assertEqual(code, 2)
        self.assertIn("not both", err)

    def test_no_plan_says_what_to_give(self):
        code, _, err = self.run_cli("estimate")
        self.assertEqual(code, 2)
        self.assertIn("--example", err)

    def test_json_output_parses(self):
        _, out, _ = self.run_cli(
            "estimate", str(self.plan), "--data", str(self.data), "--draws", "500", "--json"
        )
        parsed = json.loads(out)
        self.assertLessEqual(parsed["p50"], parsed["p90"])
        self.assertEqual(parsed["critical_path"], ["a", "b"])
        self.assertEqual(parsed["human"], {"status": "not measured", "included_in_total": False})

    def test_text_output_states_the_human_part_without_a_number(self):
        _, out, _ = self.run_cli(
            "estimate", str(self.plan), "--data", str(self.data), "--draws", "500"
        )
        line = next(line for line in out.splitlines() if line.startswith("Human part"))
        self.assertIn("review, testing, decisions, waiting between agent turns", line)
        self.assertIn("not measured. No allowance for it is added to the total above.", line)
        self.assertIn("do not derive it from the agent time", line)
        self.assertNotRegex(line, r"\d")

    def test_plan_can_come_from_stdin(self):
        code, out, _ = self.run_cli(
            "estimate",
            "-",
            "--data",
            str(self.data),
            "--draws",
            "500",
            stdin=self.plan.read_text(encoding="utf-8"),
        )
        self.assertEqual(code, 0)
        self.assertIn("P90", out)

    def test_invalid_json_is_a_clean_error(self):
        self.plan.write_text("{not json CANARY", encoding="utf-8")
        code, _, err = self.run_cli("estimate", str(self.plan), "--data", str(self.data))
        self.assertEqual(code, 2)
        self.assertNotIn("CANARY", err)

    def test_missing_data_path_is_a_clean_error(self):
        code, _, err = self.run_cli("estimate", str(self.plan), "--data", str(self.root / "x"))
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)

    def test_missing_plan_file_is_a_clean_error(self):
        for missing in (self.root / "no-such.json", self.root):
            with self.subTest(plan=missing.name):
                code, out, err = self.run_cli("estimate", str(missing))
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(err, "agentpace: no plan file at that path\n")

    def test_no_data_at_all_explains_what_to_do(self):
        empty = self.root / "empty"
        empty.mkdir()
        code, _, err = self.run_cli("estimate", str(self.plan), "--data", str(empty))
        self.assertEqual(code, 2)
        self.assertIn("ingest", err)

    def test_a_model_that_is_not_a_string_is_a_clean_error(self):
        good = {"id": "a", "kind": "docs"}
        for bad in ({"x": 1}, ["a"], True, 3):
            for document, message in (
                ({"model": bad, "tasks": [good]}, "model must be a string"),
                (
                    {"tasks": [good, {**good, "id": "b", "model": bad}]},
                    "task #2: model must be a string",
                ),
            ):
                with self.subTest(document=document):
                    code, out, err = self.run_cli(
                        "estimate", "-", "--data", str(self.data), stdin=json.dumps(document)
                    )
                    self.assertEqual(code, 2)
                    self.assertEqual(out, "")
                    self.assertEqual(err, f"agentpace: {message}\n")

    def test_a_null_plan_model_forecasts_as_no_model(self):
        common = ("--data", str(self.data), "--draws", "500", "--json")
        document = json.loads(self.plan.read_text(encoding="utf-8"))
        _, without, _ = self.run_cli("estimate", "-", *common, stdin=json.dumps(document))
        nulled = {**document, "model": None}
        _, with_null, _ = self.run_cli("estimate", "-", *common, stdin=json.dumps(nulled))
        self.assertEqual(with_null, without)

    def test_the_first_line_names_the_plan_total_on_the_session_clock(self):
        _, out, _ = self.run_cli(
            "estimate", str(self.plan), "--data", str(self.data), "--draws", "500"
        )
        self.assertRegex(
            out.splitlines()[0],
            r"^Plan total on the session clock: P50 [^,]+, P80 [^,]+, P90 [^,]+$",
        )
        self.assertNotIn("wall clock", out.lower())

    def test_version(self):
        with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--version"])
        self.assertEqual(caught.exception.code, 0)


class Durations(unittest.TestCase):
    def test_units_follow_the_magnitude(self):
        cases = {30: "30 s", 600: "10 min", 7200: "2.0 h", 345600: "4.0 days"}
        for seconds, text in cases.items():
            self.assertEqual(render.duration(seconds), text)

    def test_warnings_and_oracle_track_are_shown(self):
        task = TaskRange("a", 1, 2, 3, "docs", 0, 0.0, 1.0)
        plan = PlanRange(
            1, 2, 3, (task,), ("a",), 1.0, 2, None, 0, 0.0, "weighted", True, 10, 1, 500, 1, ("x",)
        )
        text = render.plan_text(plan)
        self.assertIn("not learned yet", text)
        self.assertIn("oracle track", text)
        self.assertIn("Warnings", text)
        self.assertIn("not completed", text)

    def test_plan_text_never_says_wall_clock(self):
        task = TaskRange("a", 1, 2, 3, "docs", 0, 0.0, 1.0)
        measured = HumanPart(
            status="measured",
            source="your history, local",
            per_turn_p50=60.0,
            per_turn_p80=120.0,
            per_turn_p90=180.0,
            gaps=4,
            sessions=2,
        )
        for human in (HumanPart(), measured):
            for parallelism in (None, 1.5):
                plan = PlanRange(
                    p50=600,
                    p80=1200,
                    p90=1800,
                    tasks=(task,),
                    critical_path=("a",),
                    critical_path_share=1.0,
                    agent_cap=2,
                    effective_parallelism=parallelism,
                    parallelism_sessions=3,
                    throughput_bound_share=0.5,
                    method="shrunk",
                    size_used=False,
                    rows_used=10,
                    rows_excluded=0,
                    draws=500,
                    seed=1,
                    warnings=("x",),
                    human=human,
                )
                with self.subTest(human=human.status, parallelism=parallelism):
                    text = render.plan_text(plan)
                    self.assertTrue(
                        text.startswith(
                            "Plan total on the session clock: P50 10 min, P80 20 min, P90 30 min\n"
                        )
                    )
                    self.assertNotRegex(text, r"(?i)wall[ -]?clock")

    def test_a_quote_is_shown_verbatim_and_never_converted(self):
        for quoted in ("3 months", "about 3 weeks", "2-3 sprints", ""):
            with self.subTest(quoted=quoted):
                line = render.quote_text(quoted)
                self.assertTrue(line.startswith(f'Quoted: "{quoted}" ('))
                for phrase in ("calendar time", "as quoted", "unverified"):
                    self.assertIn(phrase, line)
                outside = line.replace(f'"{quoted}"', "", 1)
                self.assertNotRegex(outside, r"\d")
                self.assertNotRegex(outside, r"(?i)\bratio\b|\d\s*x\b|faster")

    def test_the_quote_sits_between_the_total_and_the_human_part(self):
        task = TaskRange("a", 1, 2, 3, "docs", 0, 0.0, 1.0)
        plan = PlanRange(
            1, 2, 3, (task,), ("a",), 1.0, 2, None, 0, 0.0, "shrunk", False, 10, 0, 500, 1, ()
        )
        plain = render.plan_text(plan).splitlines()
        quoted = render.plan_text(plan, "3 months").splitlines()
        self.assertEqual(quoted[1], render.quote_text("3 months"))
        self.assertEqual(quoted[:1] + quoted[2:], plain)
        self.assertEqual(quoted[2], render.CLOCK_NOTE)
        self.assertTrue(quoted[3].startswith("Human part"))


class ClockWording(unittest.TestCase):
    def plan(self, human=None):
        task = TaskRange("a", 1, 2, 3, "docs", 0, 0.0, 1.0)
        return PlanRange(
            p50=600,
            p80=1200,
            p90=1800,
            tasks=(task,),
            critical_path=("a",),
            critical_path_share=1.0,
            agent_cap=2,
            effective_parallelism=None,
            parallelism_sessions=0,
            throughput_bound_share=0.0,
            method="shrunk",
            size_used=False,
            rows_used=10,
            rows_excluded=0,
            draws=500,
            seed=1,
            warnings=(),
            human=human or HumanPart(),
        )

    def test_the_clock_note_is_printed_once_directly_above_the_human_line(self):
        for human in (HumanPart(), MEASURED):
            for quoted in (None, "3 months"):
                with self.subTest(human=human.status, quoted=quoted):
                    lines = render.plan_text(self.plan(human), quoted).splitlines()
                    self.assertEqual(lines.count(render.CLOCK_NOTE), 1)
                    note = lines.index(render.CLOCK_NOTE)
                    self.assertEqual(note, 1 if quoted is None else 2)
                    self.assertEqual(lines[note + 1], render.human_text(human))
                    if quoted is not None:
                        self.assertEqual(lines[1], render.quote_text(quoted))

    def test_the_clock_note_names_the_gaps_and_the_cap(self):
        self.assertEqual(
            render.CLOCK_NOTE,
            "The session clock counts agent runs and the gaps between them, each gap up to the "
            "idle cap (900 s by default), so waiting on you between runs sits inside the total up "
            "to that cap.",
        )

    def test_the_unmeasured_human_line_adds_no_allowance(self):
        self.assertEqual(
            render.human_text(HumanPart()),
            "Human part (review, testing, decisions, waiting between agent turns): not measured. "
            "No allowance for it is added to the total above. State it separately; do not derive "
            "it from the agent time.",
        )

    def test_the_measured_human_line_says_short_waits_sit_inside_the_clock(self):
        self.assertEqual(
            render.human_text(MEASURED),
            "Human part (review, testing, decisions, waiting between agent turns): measured from "
            "your history, local. Waiting on you per human turn: P50 60 s, P80 2 min, P90 3 min "
            "(4 gaps, 2 sessions); elapsed time, not effort. No allowance for it is added to the "
            "total above, and up to the idle cap such waits already sit inside the session clock, "
            "so do not add them to it. Do not derive it from the agent time.",
        )

    def test_the_ledger_summary_names_session_spans(self):
        summary = forecasts.Summary(0, 0, None, None, 0, None, None)
        last = render.ledger_summary_text(summary).splitlines()[-1]
        self.assertEqual(
            last,
            "Actuals are session spans on their own clock (agent runs plus the gaps between them, "
            "each gap up to the idle cap), or figures you attested, as given.",
        )

    def test_no_output_carries_a_banned_phrase(self):
        summary = forecasts.Summary(5, 1, {"p50": 0.4, "p80": 0.8, "p90": 1.0}, 60.0, 0, None, None)
        texts = [render.ledger_summary_text(summary)]
        for human in (HumanPart(), MEASURED):
            for quoted in (None, "3 months"):
                texts.append(render.plan_text(self.plan(human), quoted))
        for text in texts:
            for phrase in BANNED_PHRASES:
                with self.subTest(phrase=phrase):
                    self.assertNotIn(phrase.lower(), text.lower())

    def test_the_example_output_carries_no_banned_phrase(self):
        out = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as home,
            mock.patch.dict(os.environ, {"AGENTPACE_HOME": home}),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(cli.main(["estimate", "--example", "--draws", "200"]), 0)
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("Plan total on the session clock: P50 "))
        self.assertEqual(lines[1], render.CLOCK_NOTE)
        self.assertTrue(lines[2].startswith("Human part"))
        for phrase in BANNED_PHRASES:
            self.assertNotIn(phrase.lower(), out.getvalue().lower())


class CommandText(unittest.TestCase):
    def test_validate_help_names_the_seed_default_and_data_runs(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(out):
            cli.main(["validate", "--help"])
        self.assertIn(
            "files or folders (default: the seed data shipped with agentpace; contributors pass "
            "data/runs)",
            " ".join(out.getvalue().split()),
        )

    def test_backtest_note_says_selection_is_frozen(self):
        results = {"selection": {"winner": "not-the-default"}}
        err = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch.object(cli.backtest, "run", return_value=results),
            mock.patch.object(cli.report, "write"),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            data = Path(folder) / "runs.jsonl"
            schema.write_rows(data, [row()])
            code = cli.main(["backtest", "--data", str(data), "--out", folder])
        self.assertEqual(code, 0)
        note = err.getvalue()
        self.assertNotIn("update DEFAULT_METHOD", note)
        for phrase in ("frozen at 2026-W45", "pin test fails CI", "re-registration in CHANGELOG"):
            self.assertIn(phrase, note)


class ExperimentalNote(unittest.TestCase):
    """The three unverified adapters say so on stderr; nothing else changes."""

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

    def ingest(self, source):
        report = ingest.IngestReport(files=1, rows=1)
        with mock.patch.object(cli, "_ingest_into_store", return_value=(report, 1)):
            code, out, err = self.run_cli("ingest", str(self.root), "--source", source)
        self.assertEqual(code, 0, err)
        return out, err

    def test_each_experimental_source_prints_the_note_first_on_stderr(self):
        _, baseline = self.ingest("claude-code")
        for source in ("gemini-cli", "opencode", "cline"):
            with self.subTest(source=source):
                out, err = self.ingest(source)
                note, _, rest = err.partition("\n")
                self.assertEqual(
                    note,
                    f"note: the {source} adapter is experimental: built from the tool's source, "
                    "not yet verified on real sessions. Check the rows before sharing them, and "
                    "report problems at https://github.com/mohamedzhioua/agentpace/issues",
                )
                self.assertEqual(out, "")
                self.assertEqual(rest, baseline)

    def test_claude_code_and_codex_print_no_note(self):
        for source in ("claude-code", "codex"):
            with self.subTest(source=source):
                out, err = self.ingest(source)
                self.assertEqual(out, "")
                self.assertNotIn("experimental", err)
                self.assertTrue(err.startswith("files seen:"))

    def test_share_keeps_the_rows_and_warns_only_on_stderr(self):
        rows = [
            row(harness=harness, week="2026-W45", offset_s=index)
            for index, harness in enumerate(("gemini-cli", "opencode", "cline", "claude-code"))
        ]
        schema.write_rows(paths.local_runs(), rows)
        code, out, err = self.run_cli("share", "--out-dir", str(self.root / "runs"), "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertNotIn("experimental", out)
        self.assertNotIn("adapter is experimental", err)
        self.assertIn("3 rows come from experimental adapters (cline, gemini-cli, opencode).", err)
        for harness in ("gemini-cli", "opencode", "cline"):
            self.assertIn(f'"{harness}"', out)

    def estimate(self, *extra):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps({"tasks": [{"id": "a", "kind": "docs"}]}), encoding="utf-8")
        return self.run_cli("estimate", str(plan), "--draws", "200", "--json", *extra)

    def test_an_estimate_counts_experimental_store_rows_on_stderr_only(self):
        rows = [
            row(harness=harness, week="2026-W45", offset_s=index)
            for index, harness in enumerate(("opencode", "gemini-cli", "claude-code", "opencode"))
        ]
        schema.write_rows(paths.local_runs(), rows)
        total = len(schema.read_dataset([paths.seed_dir(), paths.local_runs()]))
        code, out, err = self.estimate()
        self.assertEqual(code, 0, err)
        self.assertEqual(
            err,
            f"note: 3 of {total} rows come from experimental adapters (gemini-cli, opencode); "
            "they take part in this estimate because they are in your local store. "
            "Pass --data to choose the rows.\n",
        )
        self.assertNotIn("experimental", out)
        self.assertEqual(set(json.loads(out)), set(PlanRange.__dataclass_fields__))

    def test_no_note_without_experimental_rows_or_with_chosen_data(self):
        schema.write_rows(paths.local_runs(), [row(harness="claude-code", week="2026-W45")])
        self.assertEqual(self.estimate()[2], "")
        schema.write_rows(paths.local_runs(), [row(harness="gemini-cli", week="2026-W45")])
        data = ("--data", str(paths.local_runs()), "--data", str(paths.seed_dir()))
        code, _, err = self.estimate(*data)
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")


if __name__ == "__main__":
    unittest.main()

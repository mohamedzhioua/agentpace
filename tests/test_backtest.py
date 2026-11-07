import contextlib
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import backtest, charts, claims, cli, estimate, report, schema
from agentpace.backtest import Forecast
from tests.synthetic import dataset, row

REPO = Path(__file__).resolve().parent.parent


def f(actual, p50=None, p80=None, p90=None, session="s"):
    p50 = actual if p50 is None else p50
    return Forecast(p50, p80 or p50, p90 or p50, actual, session)


class Splits(unittest.TestCase):
    def rows(self):
        return dataset(sessions=48, runs_per_session=8, weeks=6)

    def test_time_folds_only_look_backward(self):
        rows = self.rows()
        weeks = backtest.session_weeks(rows)
        for fold in backtest.time_folds(rows):
            self.assertTrue(all(weeks[r["batch"]] < fold.name for r in fold.train))
            self.assertTrue(all(weeks[r["batch"]] == fold.name for r in fold.test))

    def test_a_session_never_straddles_a_split(self):
        rows = self.rows()
        first = rows[0]["batch"]
        for r in rows:
            if r["batch"] == first and r["offset_s"] > 0:
                r["week"] = "2026-W06"  # the session runs on across a week boundary
        for fold in backtest.time_folds(rows):
            train = {r["batch"] for r in fold.train}
            test = {r["batch"] for r in fold.test}
            self.assertFalse(train & test)

    def test_group_folds_hold_each_group_out_entirely(self):
        rows = self.rows()
        folds = backtest.group_folds(rows, "project", 1)
        self.assertEqual(len(folds), 4)
        for fold in folds:
            self.assertFalse({r["project"] for r in fold.train} & {fold.name})

    def test_small_groups_are_skipped(self):
        self.assertEqual(backtest.group_folds(self.rows(), "project", 10_000), [])

    def test_folds_need_enough_history(self):
        self.assertEqual(backtest.time_folds(dataset(sessions=6, runs_per_session=4)), [])


class Metrics(unittest.TestCase):
    def test_a_perfect_forecast(self):
        m = backtest.metrics([f(100), f(200)], [1, 1])
        self.assertEqual((m["mape"], m["male"], m["pinball"]), (0, 0, 0))

    def test_mape_uses_the_p50(self):
        m = backtest.metrics([f(100, p50=150, p80=999, p90=999)], [1])
        self.assertEqual(m["mape"], 50)

    def test_coverage_counts_actuals_at_or_under(self):
        forecasts = [f(100, p50=50, p80=100, p90=200), f(100, p50=50, p80=90, p90=200)]
        m = backtest.metrics(forecasts, [1, 1])
        self.assertEqual((m["coverage50"], m["coverage80"], m["coverage90"]), (0, 50, 100))

    def test_pinball_penalizes_under_forecasts_more_at_high_quantiles(self):
        low = backtest._pinball(f(100), 50, 0.9)
        high = backtest._pinball(f(100), 150, 0.9)
        self.assertAlmostEqual(low, 45)
        self.assertAlmostEqual(high, 5)

    def test_width_is_reported_beside_coverage(self):
        m = backtest.metrics([f(100, p50=100, p80=300, p90=400)], [1])
        self.assertEqual(m["width"], 4)
        self.assertEqual(m["width80"], 3)

    def test_weights_change_the_answer(self):
        forecasts = [f(100, p50=200), f(100)]
        self.assertAlmostEqual(backtest.metrics(forecasts, [3, 1])["mape"], 75)

    def test_session_weighting_stops_one_session_dominating(self):
        big = [f(100, p50=200, session="big") for _ in range(9)]
        both = backtest.both_weightings([*big, f(100, session="small")])
        self.assertAlmostEqual(both["row"]["mape"], 90)
        self.assertAlmostEqual(both["session"]["mape"], 50)


class Selection(unittest.TestCase):
    def forecasts(self, losses):
        return {
            method.name: [
                f(100, p50=100 + losses[method.name] + (i % 3), session=f"s{i}") for i in range(40)
            ]
            for method in estimate.METHODS
        }

    def test_a_clear_winner_wins(self):
        losses = {"global": 90, "kind": 80, "backoff": 70, "shrunk": 0, "weighted": 60}
        result = backtest.select(self.forecasts(losses), random.Random(1))
        self.assertEqual(result["winner"], "shrunk")

    def test_a_tie_goes_to_the_simpler_candidate(self):
        losses = {"global": 90, "kind": 0, "backoff": 80, "shrunk": 70, "weighted": 0}
        forecasts = self.forecasts(losses)
        forecasts["weighted"] = [
            Forecast(x.p50 + (-1) ** i * 0.1, x.p80, x.p90, x.actual, x.session)
            for i, x in enumerate(forecasts["kind"])
        ]
        result = backtest.select(forecasts, random.Random(1))
        self.assertEqual(result["winner"], "kind")

    def test_interval_excludes_zero_for_a_real_gap(self):
        a = {f"s{i}": 10.0 + i % 2 for i in range(30)}
        b = {f"s{i}": 1.0 for i in range(30)}
        low, _ = backtest.difference_interval(a, b, random.Random(2))
        self.assertGreater(low, 0)


class FullRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = dataset(sessions=60, runs_per_session=8, weeks=6)
        with (
            mock.patch.object(backtest, "CELL_DRAWS", 300),
            mock.patch.object(backtest, "PLAN_DRAWS", 200),
            mock.patch.object(backtest, "BOOTSTRAPS", 100),
        ):
            cls.report = backtest.run(cls.rows)

    def test_every_candidate_and_baseline_is_scored(self):
        results = self.report["splits"]["time"]["tracks"]["pre-run"]
        expected = {m.name for m in estimate.METHODS} | set(backtest.BASELINES)
        self.assertEqual(set(results), expected)

    def test_single_contributor_says_it_could_not_run(self):
        self.assertEqual(
            self.report["splits"]["contributor"]["status"], "could not run (1 contributor)"
        )

    def test_project_split_runs_under_its_own_name(self):
        self.assertEqual(self.report["splits"]["project"]["status"], "ok")

    def test_each_project_is_shown_alone_and_adds_up(self):
        split = self.report["splits"]["project"]
        self.assertEqual(len(split["by_project"]), split["folds"])
        self.assertEqual(sum(p["rows"] for p in split["by_project"]), split["rows"])
        self.assertEqual(
            sum(p["sessions"] for p in split["by_project"]),
            split["without_largest"]["of_sessions"],
        )
        sessions = [p["sessions"] for p in split["by_project"]]
        self.assertEqual(sessions, sorted(sessions, reverse=True))
        self.assertEqual(split["without_largest"]["sessions"], sessions[0])

    def test_one_project_has_no_project_rows_and_no_claim(self):
        rows = [{**r, "project": "aaaaaaaaaaaa"} for r in self.rows]
        result = _small_run(rows)
        split = result["splits"]["project"]
        self.assertNotEqual(split["status"], "ok")
        self.assertNotIn("by_project", split)
        self.assertNotIn("without_largest", split)
        known = claims.values(rows, json.loads(report.to_json(result)), REPO / "examples/plan.json")
        self.assertEqual(known["project_top_harness"], "not available")
        self.assertEqual(known["project_top_sessions"], "not available")

    def test_single_harness_says_it_could_not_run(self):
        self.assertEqual(self.report["splits"]["harness"]["status"], "could not run (1 harness)")
        self.assertEqual(list(self.report["harness"]["dataset"]), ["claude-code"])

    def test_the_harness_row_repeats_the_pooled_numbers_for_one_harness(self):
        winner = self.report["selection"]["winner"]
        pooled = self.report["splits"]["time"]["tracks"]["pre-run"][winner]["session"]
        alone = self.report["harness"]["selection_folds"]["claude-code"]
        self.assertAlmostEqual(alone["pinball"], pooled["pinball"])
        self.assertAlmostEqual(alone["coverage80_session"], pooled["coverage80"])

    def test_markdown_says_so_too(self):
        text = report.markdown(self.report)
        self.assertIn("## By harness", text)
        self.assertIn("could not run (1 harness)", text)
        self.assertIn("Could not run (1 contributor)", text)
        self.assertIn("oracle-structure", text)
        self.assertIn("Where agentpace loses", text)

    def test_baseline_oracle_is_labeled(self):
        self.assertIn("It uses the answer", report.markdown(self.report))

    def test_results_are_reproducible(self):
        with (
            mock.patch.object(backtest, "CELL_DRAWS", 300),
            mock.patch.object(backtest, "PLAN_DRAWS", 200),
            mock.patch.object(backtest, "BOOTSTRAPS", 100),
        ):
            again = backtest.run(self.rows)
        self.assertEqual(report.to_json(self.report), report.to_json(again))

    def test_two_contributors_unlock_the_contributor_split(self):
        rows = dataset(sessions=60, runs_per_session=8, contributors=2)
        with (
            mock.patch.object(backtest, "CELL_DRAWS", 200),
            mock.patch.object(backtest, "PLAN_DRAWS", 100),
            mock.patch.object(backtest, "BOOTSTRAPS", 50),
        ):
            result = backtest.run(rows)
        self.assertEqual(result["splits"]["contributor"]["status"], "ok")

    def test_too_little_history_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "weeks"):
            backtest.run(dataset(sessions=4, runs_per_session=4))

    def test_write_and_check_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            self.assertFalse(report.is_current(self.report, out))
            report.write(self.report, self.rows, out)
            self.assertTrue(report.is_current(self.report, out))
            for name in ("backtest.md", "assets/run-ranges.svg", "assets/backtest-coverage.svg"):
                self.assertTrue((out / name).exists())


def _small_run(rows, **kwargs):
    with (
        mock.patch.object(backtest, "CELL_DRAWS", 300),
        mock.patch.object(backtest, "PLAN_DRAWS", 200),
        mock.patch.object(backtest, "BOOTSTRAPS", 100),
    ):
        return backtest.run(rows, **kwargs)


class Harnesses(unittest.TestCase):
    def test_each_harness_is_summarized_from_forecasts_already_drawn(self):
        rows = [row(), row(offset_s=5), row(harness="codex", batch="cccccccccccc")]
        forecasts = {
            "shrunk": [
                Forecast(10, 20, 30, 15, "aaaaaaaaaaaa"),
                Forecast(10, 20, 30, 25, "cccccccccccc", "codex"),
            ],
            "global-median": [
                Forecast(12, 12, 12, 15, "aaaaaaaaaaaa"),
                Forecast(12, 12, 12, 25, "cccccccccccc", "codex"),
            ],
        }
        summary = backtest.harness_summary(rows, forecasts, "shrunk")
        self.assertEqual(summary["dataset"]["codex"], {"rows": 1, "completed": 1, "sessions": 1})
        self.assertEqual(summary["dataset"]["claude-code"]["rows"], 2)
        self.assertEqual(summary["selection_folds"]["codex"]["coverage80_session"], 0.0)
        self.assertEqual(summary["selection_folds"]["claude-code"]["coverage80_session"], 100.0)

    def test_a_forecast_defaults_to_claude_code(self):
        self.assertEqual(Forecast(1, 2, 3, 2, "s").harness, "claude-code")


def _with_codex(sessions, duration):
    """The synthetic Claude rows plus one-turn Codex sessions from W03 on, as Codex exec runs."""
    rows = dataset(sessions=60, runs_per_session=8, weeks=6)
    rng = random.Random(3)
    for i in range(sessions):
        rows.append(
            row(
                harness="codex",
                model="gpt-6.1-sol",
                task_kind="other",
                size="na",
                duration_s=max(1, round(duration * 2.718 ** rng.gauss(0, 0.3))),
                tool_calls=1,
                week=f"2026-W{3 + i % 4:02d}",
                batch=f"c{i:011x}",
                project="cccccccccccc",
            )
        )
    return rows


class SelectionPopulation(unittest.TestCase):
    """Selection scores only the harness the rule was frozen on. Every harness still trains."""

    @classmethod
    def setUpClass(cls):
        cls.mixed = _small_run(_with_codex(200, 3000), selection_last_week="2026-W06")
        cls.locked = _small_run(_with_codex(200, 3000), selection_last_week="2026-W04")

    def test_selection_ignores_another_harness_and_the_pooled_rule_is_printed(self):
        selection, pooled = self.mixed["selection"], self.mixed["pooled_selection"]
        self.assertEqual(selection["harness"], backtest.SELECTION_HARNESS)
        self.assertEqual((selection["winner"], pooled["winner"]), ("kind", "shrunk"))
        self.assertEqual(set(pooled["baselines"]), set(backtest.BASELINES))
        text = report.markdown(self.mixed)
        self.assertIn("## Pooled, for the record", text)
        self.assertIn("Pooled winner: `shrunk`", text)
        self.assertIn("**Winner: `kind`.**", text)

    def test_the_headline_scores_only_the_selection_harness(self):
        time = self.mixed["splits"]["time"]
        claude = self.mixed["harness"]["selection_folds"]["claude-code"]
        self.assertEqual(time["rows"], claude["rows"])
        self.assertEqual(sum(week["rows"] for week in time["by_week"]), claude["rows"])
        self.assertEqual(self.mixed["splits"]["harness"]["status"], "ok")

    def test_one_harness_is_its_own_population(self):
        rows = dataset(sessions=48, runs_per_session=8, weeks=6)
        claude = _small_run(rows)
        self.assertEqual(claude["selection"]["harness"], "claude-code")
        self.assertEqual(claude["pooled_selection"], {"status": "same as above (1 harness)"})
        self.assertNotIn("## Pooled, for the record", report.markdown(claude))
        for item in rows:
            item["harness"] = "codex"
        codex = _small_run(rows)
        self.assertEqual(codex["selection"]["harness"], "all")
        self.assertEqual(
            report.to_json(codex["selection"]["loss"]), report.to_json(claude["selection"]["loss"])
        )

    def test_whole_sessions_of_another_harness_are_not_scored(self):
        rows = dataset(sessions=48, runs_per_session=8, weeks=6)
        for item in rows:
            if int(item["batch"], 16) % 2 == 0:
                item["harness"] = "codex"
        folds = backtest.time_folds(rows)
        with mock.patch.object(backtest, "PLAN_DRAWS", 100):
            every = backtest.plan_forecasts(folds, "shrunk", backtest.SEED)
            claude = backtest.plan_forecasts(folds, "shrunk", backtest.SEED, "claude-code")
        kept = {f.session for f in claude["agentpace"]}
        self.assertTrue(kept)
        self.assertLess(len(kept), len(every["agentpace"]))
        self.assertTrue(all(int(batch, 16) % 2 for batch in kept))
        same = [f for f in every["agentpace"] if f.session in kept]
        self.assertEqual(same, claude["agentpace"])

    def test_the_locked_holdout_scores_every_harness_at_task_level(self):
        found = self.locked["locked"]["by_harness"]
        self.assertEqual(set(found), {"claude-code", "codex"})
        names = {method.name for method in estimate.METHODS} | {"global-median"}
        for entry in found.values():
            self.assertEqual(set(entry["methods"]), names)
            self.assertGreater(entry["sessions"], 0)
        self.assertIn("| codex | global |", report.markdown(self.locked))


class LockedHoldout(unittest.TestCase):
    """Weeks after the selection cutoff are scored but never feed back into selection.

    Ten sessions of eight runs land in each of six weeks, so with the cutoff at W04 the locked
    weeks W05 and W06 hold exactly the 20 qualifying sessions the holdout needs.
    """

    @classmethod
    def setUpClass(cls):
        cls.rows = dataset(sessions=60, runs_per_session=8, weeks=6)
        cls.early = [r for r in cls.rows if r["week"] <= "2026-W04"]
        cls.with_later = _small_run(cls.rows, selection_last_week="2026-W04")
        cls.without_later = _small_run(cls.early, selection_last_week="2026-W04")

    def test_later_weeks_never_change_selection_or_the_headline(self):
        a, b = self.with_later, self.without_later
        self.assertEqual(report.to_json(a["selection"]), report.to_json(b["selection"]))
        for part in ("tracks", "intervals", "folds"):
            self.assertEqual(
                report.to_json(a["splits"]["time"][part]),
                report.to_json(b["splits"]["time"][part]),
                part,
            )
        self.assertEqual(report.to_json(a["plans"]), report.to_json(b["plans"]))
        selection_weeks = len(b["splits"]["time"]["folds"])
        self.assertEqual(
            report.to_json(a["splits"]["time"]["by_week"][:selection_weeks]),
            report.to_json(b["splits"]["time"]["by_week"]),
        )

    def test_readme_calibration_counts_only_selection_weeks(self):
        known = claims.values(self.rows, json.loads(report.to_json(self.with_later)))
        self.assertEqual(
            known["weeks_tested"], str(len(self.with_later["splits"]["time"]["folds"]))
        )
        self.assertEqual(known["latest_week"], "2026-W04")

    def test_enough_later_sessions_unlock_the_holdout(self):
        locked = self.with_later["locked"]
        self.assertEqual(locked["status"], "ok")
        self.assertEqual(locked["folds"], ["2026-W05", "2026-W06"])
        self.assertEqual(locked["qualifying_sessions"], 20)
        winner = self.with_later["selection"]["winner"]
        self.assertEqual(set(locked["tracks"]["pre-run"]), {winner, *backtest.BASELINES})
        text = report.markdown(self.with_later)
        self.assertIn("## Locked holdout", text)
        self.assertIn("Weeks 2026-W05, 2026-W06", text)

    def test_too_few_later_sessions_say_not_enough_data(self):
        locked = [f for f in backtest.time_folds(self.rows) if f.name > "2026-W04"]
        with (
            mock.patch.object(backtest, "LOCKED_MIN_SESSIONS", 21),
            mock.patch.object(backtest, "PLAN_DRAWS", 100),
        ):
            result, losses = backtest._locked_holdout(locked, {}, "shrunk", backtest.SEED)
        self.assertEqual(result["status"], "not enough data (20 of 21 sessions with 5+ runs)")
        self.assertEqual(losses, [])
        lines = report._locked_section(result, "shrunk", "2026-W04")
        self.assertIn("**Not enough data (20 of 21 sessions with 5+ runs).**", lines)

    def test_no_later_weeks_say_so(self):
        self.assertIn("no weeks after", self.without_later["locked"]["status"])

    def test_every_week_gets_a_row(self):
        weeks = self.with_later["splits"]["time"]["by_week"]
        self.assertEqual(
            [(w["week"], w["period"]) for w in weeks],
            [("2026-W04", "selection"), ("2026-W05", "locked"), ("2026-W06", "locked")],
        )
        self.assertTrue(all(w["sessions"] == 10 and w["rows"] == 80 for w in weeks))

    def test_intervals_bracket_the_reported_numbers(self):
        result = self.with_later
        winner = result["selection"]["winner"]
        point = result["splits"]["time"]["tracks"]["pre-run"][winner]["session"]
        found = result["splits"]["time"]["intervals"]
        for metric in ("coverage50", "coverage80", "coverage90", "pinball"):
            low, high = found[metric]
            self.assertLessEqual(low, point[metric], metric)
            self.assertLessEqual(point[metric], high, metric)
        self.assertEqual(set(found["rival_minus_ours"]), set(backtest.BASELINES))
        plan = result["plans"]
        low, high = plan["intervals"]["pinball"]
        self.assertTrue(low <= plan["results"]["agentpace"]["pinball"] <= high)
        self.assertIn("Pinball |", report.markdown(result).split("## Whole sessions")[1])


class Intervals(unittest.TestCase):
    def test_a_constant_has_no_spread(self):
        values = {f"s{i}": 5.0 for i in range(10)}
        self.assertEqual(backtest.mean_interval(values, random.Random(1)), [5.0, 5.0])

    def test_the_interval_spreads_around_the_mean(self):
        values = {f"s{i}": float(i % 2) for i in range(20)}  # mean 0.5
        low, high = backtest.mean_interval(values, random.Random(1))
        self.assertTrue(0.0 <= low < 0.5 < high <= 1.0, (low, high))

    def test_a_number_renders_with_its_interval(self):
        self.assertEqual(report.ci(80.0, [70.2, 90.6], unit="%"), "80% (CI 70% to 91%)")
        self.assertEqual(report.ci(78.53, [72.7, 84.2], 1, "%"), "78.5% (CI 72.7% to 84.2%)")


def _project_folds():
    """Four held-out projects and fixed forecasts for every candidate and the global median.

    Project "dd" is a one-run-per-session Codex project with the most sessions. "aa" and "cc"
    tie on sessions, and "cc" has more rows, so it ranks first of the two."""
    folds = [
        backtest.Fold("aa", [], [row(project="aa")]),
        backtest.Fold("bb", [], [row(project="bb")]),
        backtest.Fold("cc", [], [row(project="cc")]),
        backtest.Fold("dd", [], [row(project="dd", harness="codex")]),
    ]
    sessions = {
        "aa": ["a1", "a2"],
        "bb": ["b1"],
        "cc": ["c1", "c1", "c2"],
        "dd": ["d1", "d2", "d3"],
    }
    # Per fold, a forecast error for each candidate. "weighted" wins with "dd" and loses without.
    errors = {
        "global": [3.0, 3.0, 3.0, 3.0],
        "kind": [4.0, 4.0, 4.0, 4.0],
        "backoff": [2.5, 2.5, 2.5, 2.5],
        "shrunk": [2.0, 2.0, 2.0, 2.0],
        "weighted": [2.6, 2.6, 2.6, 0.0],
        "global-median": [5.0, 5.0, 5.0, 5.0],
    }
    per_fold = {
        name: [
            [
                Forecast(10 + error, 10 + error, 10 + error, 10, session, fold.test[0]["harness"])
                for session in sessions[fold.name]
            ]
            for fold, error in zip(folds, by_fold, strict=True)
        ]
        for name, by_fold in errors.items()
    }
    return folds, per_fold


class ProjectRows(unittest.TestCase):
    """Each held-out project on its own, from forecasts already drawn, ranked and never named."""

    def setUp(self):
        self.folds, self.per_fold = _project_folds()
        self.rows = backtest.by_project(self.folds, self.per_fold, "shrunk")
        self.largest = backtest.without_largest(self.folds, self.per_fold, "shrunk")

    def test_ranked_by_sessions_then_rows_then_pseudonym(self):
        self.assertEqual(
            [(r["harness"], r["sessions"], r["rows"]) for r in self.rows],
            [("codex", 3, 3), ("claude-code", 2, 3), ("claude-code", 2, 2), ("claude-code", 1, 1)],
        )

    def test_no_pseudonym_is_reported(self):
        text = json.dumps([self.rows, self.largest])
        for name in ("aa", "bb", "cc", "dd"):
            self.assertNotIn(f'"{name}"', text)

    def test_counts_sum_to_the_pooled_forecasts(self):
        pooled = [f for fold in self.per_fold["shrunk"] for f in fold]
        self.assertEqual(sum(r["rows"] for r in self.rows), len(pooled))
        self.assertEqual(sum(r["sessions"] for r in self.rows), len({f.session for f in pooled}))
        self.assertEqual(self.largest["of_sessions"], len({f.session for f in pooled}))

    def test_without_largest_drops_exactly_that_project(self):
        self.assertEqual(
            (self.largest["project"], self.largest["harness"], self.largest["sessions"]),
            (1, "codex", 3),
        )
        for name, value in self.largest["loss"].items():
            kept = [f for fold in self.per_fold[name][:3] for f in fold]
            expected = backtest.both_weightings(kept)["session"]["pinball"]
            self.assertAlmostEqual(value, expected, msg=name)
        self.assertIn("global-median", self.largest["loss"])

    def test_the_lowest_candidate_is_computed_with_and_without(self):
        self.assertEqual(self.largest["lowest_with_it"], "weighted")
        self.assertEqual(self.largest["lowest"], "shrunk")
        candidates = [m.name for m in estimate.METHODS]
        self.assertEqual(
            self.largest["lowest"], min(candidates, key=lambda n: self.largest["loss"][n])
        )

    def test_markdown_prints_one_row_per_project_and_the_computed_verdict(self):
        split = {"by_project": self.rows, "without_largest": self.largest}
        lines = report._project_lines(split, "shrunk")
        self.assertEqual(sum(line.startswith("| ") and line[2].isdigit() for line in lines), 4)
        text = "\n".join(lines)
        self.assertIn("changes from `weighted` to `shrunk`", text)
        self.assertIn("This split does not choose the method.", text)
        same = {**self.largest, "lowest_with_it": "shrunk"}
        text = "\n".join(report._project_lines({**split, "without_largest": same}, "shrunk"))
        self.assertIn("The lowest candidate is still `shrunk`.", text)

    def test_a_project_with_no_completed_run_is_left_out(self):
        empty = {name: [*folds[:3], []] for name, folds in self.per_fold.items()}
        self.assertEqual(len(backtest.by_project(self.folds, empty, "shrunk")), 3)


class OneTable(unittest.TestCase):
    """When every held-out session is one run, the two weightings give one table."""

    def split(self, same):
        session = {m: 1.0 for m in ("mape", "mdape", "male", "pinball", "width")}
        session.update(coverage50=50.0, coverage80=80.0, coverage90=90.0)
        row = session if same else {**session, "pinball": 2.0}
        results = {name: {"session": session, "row": row} for name in report._method_names()}
        return {"status": "ok", "tracks": {"pre-run": results}}

    def test_equal_weightings_print_once(self):
        text = "\n".join(report._split_section("Harness split", self.split(True), "pre-run", ""))
        self.assertIn("every held-out session here has the same number of runs", text)
        self.assertNotIn("Each run counts equally:", text)
        self.assertEqual(text.count("| Method |"), 1)

    def test_different_weightings_print_both(self):
        text = "\n".join(report._split_section("Harness split", self.split(False), "pre-run", ""))
        self.assertIn("Each run counts equally:", text)
        self.assertEqual(text.count("| Method |"), 2)


class Losses(unittest.TestCase):
    def results(self, ours, theirs):
        scored = {"row": theirs, "session": theirs}
        table = {name: scored for name in backtest.BASELINES}
        table["shrunk"] = {"row": ours, "session": ours}
        return table

    def base(self, **overrides):
        values = dict.fromkeys(backtest.ERROR_METRICS, 10.0)
        values["coverage80"] = 80.0
        return {**values, **overrides}

    def test_every_baseline_win_is_listed(self):
        entries = backtest._losses(
            "time", "pre-run", self.results(self.base(mape=50.0), self.base()), "shrunk"
        )
        self.assertEqual(len(entries), 2 * len(backtest.BASELINES))
        self.assertTrue(all(entry["metric"] == "mape" for entry in entries))

    def test_coverage_nearer_the_target_counts_as_a_win(self):
        entries = backtest._losses(
            "time",
            "pre-run",
            self.results(self.base(coverage80=95.0), self.base(coverage80=82.0)),
            "shrunk",
        )
        self.assertTrue(all(entry["metric"] == "coverage80" for entry in entries))

    def test_ties_are_not_losses(self):
        self.assertEqual(
            backtest._losses("t", "p", self.results(self.base(), self.base()), "shrunk"), []
        )

    def test_empty_losses_are_stated(self):
        lines = report._loss_section([], "shrunk")
        self.assertIn("No baseline won on any metric.", lines)


class Rounding(unittest.TestCase):
    def test_four_significant_figures(self):
        rounded = json.loads(report.to_json({"a": [1234.5678, {"b": 0.00123456}]}))
        self.assertEqual(rounded, {"a": [1235.0, {"b": 0.001235}]})


class Charts(unittest.TestCase):
    def test_range_chart_is_well_formed(self):
        import xml.etree.ElementTree as ET

        bars = [charts.RangeBar("review", 10, 60, 120, 300), charts.RangeBar("a&b", 5, 5, 9, 900)]
        root = ET.fromstring(charts.range_chart("Title <x>", bars))
        self.assertTrue(root.tag.endswith("svg"))

    def test_coverage_chart_is_well_formed(self):
        import xml.etree.ElementTree as ET

        ET.fromstring(charts.coverage_chart("T", [("a", 79.0, False), ("b", 99.5, True)], 80))

    def test_run_chart_skips_thin_kinds(self):
        rows = [row(offset_s=i, duration_s=60 + i) for i in range(12)]
        rows.append(row(task_kind="docs", offset_s=99))
        text = report.run_range_chart(rows)
        self.assertIn("feature", text)
        self.assertNotIn("docs", text)


class BacktestCommand(unittest.TestCase):
    def test_check_fails_when_numbers_drift(self):
        fake = {"seed": 1}
        with (
            tempfile.TemporaryDirectory() as folder,
            mock.patch.object(backtest, "run", return_value=fake),
            mock.patch.object(cli, "_dataset", return_value=[]),
        ):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = cli.main(["backtest", "--check", "--out", folder])
            self.assertEqual(code, 1)
            (Path(folder) / "backtest.json").write_text(report.to_json(fake), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["backtest", "--check", "--out", folder]), 0)


class Published(unittest.TestCase):
    """Published claims must match the shipped data and the shipped default."""

    def test_the_shipped_default_is_the_published_winner(self):
        stored = json.loads((REPO / "docs" / "backtest.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["selection"]["winner"], estimate.DEFAULT_METHOD)

    def test_the_seed_rows_validate(self):
        from agentpace import paths, validate

        self.assertEqual(validate.validate([paths.seed_dir()]), [])
        self.assertGreater(len(schema.read_dataset([paths.seed_dir()])), 1000)


if __name__ == "__main__":
    unittest.main()

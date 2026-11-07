import math
import random
import statistics
import unittest

from agentpace import estimate as est
from agentpace.estimate import Task, fit
from tests.synthetic import dataset, row


def plain_rows(n, duration, **overrides):
    return [row(duration_s=duration, offset_s=i, batch=f"{i:012x}", **overrides) for i in range(n)]


def lognormal_rows(n, median, sigma, seed=1, **overrides):
    rng = random.Random(seed)
    return [
        row(
            duration_s=max(1, round(median * math.exp(rng.gauss(0, sigma)))),
            offset_s=i,
            batch=f"{i // 8:012x}",
            **overrides,
        )
        for i in range(n)
    ]


class Quantile(unittest.TestCase):
    def test_matches_statistics_inclusive(self):
        values = sorted(random.Random(3).random() for _ in range(101))
        expected = statistics.quantiles(values, n=10, method="inclusive")
        self.assertAlmostEqual(est.quantile(values, 0.9), expected[8])

    def test_single_value(self):
        self.assertEqual(est.quantile([5.0], 0.8), 5.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            est.quantile([], 0.5)


class Recovery(unittest.TestCase):
    """Fitted ranges must land near the truth of data with known structure."""

    def test_recovers_a_lognormal_cell(self):
        model = fit(lognormal_rows(2000, 300, 0.8))
        rng = random.Random(1)
        query = {"task_kind": "feature", "risk": "normal", "model": "claude-opus-5-5"}
        p50, p80, p90 = est.cell_quantiles(model, query, 20000, rng)
        self.assertAlmostEqual(math.log(p50), math.log(300), delta=0.1)
        self.assertAlmostEqual(math.log(p80), math.log(300) + 0.8416 * 0.8, delta=0.12)
        self.assertAlmostEqual(math.log(p90), math.log(300) + 1.2816 * 0.8, delta=0.15)

    def test_cells_are_told_apart(self):
        rows = lognormal_rows(600, 100, 0.5, task_kind="review", risk="low") + lognormal_rows(
            600, 1000, 0.5, seed=2
        )
        for i, r in enumerate(rows):
            r["offset_s"] = i
            r["batch"] = f"{i:012x}"
        model = fit(rows)
        fast = model.locate({"task_kind": "review", "risk": "low"})
        slow = model.locate({"task_kind": "feature", "risk": "normal"})
        self.assertGreater(slow.mu - fast.mu, 2.0)

    def test_session_and_noise_variance_are_not_double_counted(self):
        rows = dataset(seed=11, sessions=300, runs_per_session=12)
        model = fit(rows, method="shrunk")
        effects = model.session_effects
        noise = model.root.pool.values
        between = statistics.pvariance(effects)
        within = statistics.pvariance(noise)
        self.assertAlmostEqual(between, 0.4**2, delta=0.05)
        self.assertAlmostEqual(within, 0.8**2, delta=0.06)

    def test_a_singleton_session_cannot_erase_noise(self):
        rows = plain_rows(40, 100)
        model = fit(rows)
        self.assertEqual(model.session_effects, [])


class Shrinkage(unittest.TestCase):
    def build(self, thin_count):
        rows = []
        rng = random.Random(9)
        for i in range(600):
            kind = ("feature", "docs", "review")[i % 3]
            median = {"feature": 300, "docs": 100, "review": 200}[kind]
            rows.append(
                row(
                    task_kind=kind,
                    duration_s=max(1, round(median * math.exp(rng.gauss(0, 0.6)))),
                    offset_s=i,
                    batch=f"{i:012x}",
                )
            )
        for i in range(thin_count):
            rows.append(
                row(
                    task_kind="migration",
                    duration_s=5000,
                    offset_s=i + 700,
                    batch=f"{i + 700:012x}",
                )
            )
        return rows

    def test_thin_cells_lean_on_their_parent_and_thick_ones_do_not(self):
        thin = fit(self.build(3)).locate({"task_kind": "migration"})
        thick = fit(self.build(300)).locate({"task_kind": "migration"})
        self.assertLess(abs(thick.mu - math.log(5000)), abs(thin.mu - math.log(5000)))

    def test_uncertainty_falls_as_a_cell_grows(self):
        thin = fit(self.build(3)).locate({"task_kind": "migration"})
        thick = fit(self.build(300)).locate({"task_kind": "migration"})
        self.assertGreater(thin.v, thick.v)

    def test_an_unseen_cell_is_more_uncertain_than_a_seen_one(self):
        model = fit(self.build(0))
        seen = model.locate({"task_kind": "feature"})
        unseen = model.locate({"task_kind": "refactor"})
        self.assertGreater(unseen.v, seen.v)
        self.assertTrue(unseen.thin)
        self.assertEqual(unseen.n, 0)

    def test_unseen_cell_has_finite_uncertainty(self):
        unseen = fit(self.build(0)).locate({"task_kind": "refactor"})
        self.assertTrue(math.isfinite(unseen.v))

    def test_zero_between_cell_variance_is_handled(self):
        rows = plain_rows(100, 60) + [
            row(task_kind="docs", duration_s=60, offset_s=i + 200, batch=f"{i + 200:012x}")
            for i in range(100)
        ]
        cell = fit(rows).locate({"task_kind": "docs"})
        self.assertAlmostEqual(cell.mu, math.log(60))


class SessionWeighting(unittest.TestCase):
    def build(self):
        ordinary = lognormal_rows(200, 300, 0.5)
        for i, r in enumerate(ordinary):
            r["batch"] = f"{i // 2:012x}"
        giant = [
            row(duration_s=5, offset_s=i + 1000, batch="f" * 12, parallel_agents=8)
            for i in range(800)
        ]
        return ordinary + giant

    def test_one_giant_session_does_not_swamp_the_estimate(self):
        weighted = fit(self.build(), method="weighted").root.mean
        unweighted = fit(self.build(), method="shrunk").root.mean
        self.assertGreater(weighted, unweighted + 0.5)

    def test_effective_size_is_far_below_the_row_count(self):
        model = fit(self.build(), method="weighted")
        self.assertLess(model.root.n_eff, model.root.n * 0.5)


class Methods(unittest.TestCase):
    def test_every_candidate_fits(self):
        rows = dataset()
        for method in est.METHODS:
            with self.subTest(method=method.name):
                model = fit(rows, method=method.name)
                plan = est.estimate(model, [Task("a", "feature")], 2, draws=500)
                self.assertLessEqual(plan.p50, plan.p90)

    def test_global_has_no_cells(self):
        self.assertEqual(fit(dataset(), method="global").nodes, {})

    def test_backoff_uses_the_parent_below_the_threshold(self):
        rows = dataset() + plain_rows(3, 9000, task_kind="migration")
        model = fit(rows, method="backoff")
        self.assertAlmostEqual(model.locate({"task_kind": "migration"}).mu, model.root.mu)


class Leakage(unittest.TestCase):
    def test_post_run_fields_cannot_change_a_forecast(self):
        rows = dataset()
        twisted = [
            {
                **r,
                "tool_calls": 0,
                "tokens": 1,
                "retries": 99,
                "parallel_agents": r["parallel_agents"],
            }
            for r in rows
        ]
        tasks = [Task("a", "feature"), Task("b", "docs", depends_on=("a",))]
        first = est.estimate(fit(rows), tasks, 2, draws=500)
        second = est.estimate(fit(twisted), tasks, 2, draws=500)
        self.assertEqual(first, second)

    def test_size_is_ignored_by_default_and_said_so(self):
        rows = dataset()
        small = est.estimate(fit(rows), [Task("a", "feature", size="S")], 1, draws=500)
        large = est.estimate(fit(rows), [Task("a", "feature", size="XL")], 1, draws=500)
        self.assertEqual(small.p50, large.p50)
        self.assertFalse(small.size_used)
        self.assertTrue(any("size ignored" in note for note in small.warnings))

    def test_no_size_warning_when_tasks_carry_none(self):
        plan = est.estimate(fit(dataset()), [Task("a", "feature")], 1, draws=500)
        self.assertFalse(any("size ignored" in note for note in plan.warnings))

    def test_use_size_is_explicit_and_reported(self):
        plan = est.estimate(
            fit(dataset(), use_size=True), [Task("a", "feature", size="M")], 1, draws=500
        )
        self.assertTrue(plan.size_used)
        self.assertIn("M", plan.tasks[0].cell)

    def test_only_completed_runs_train(self):
        rows = dataset()
        noisy = [*rows, row(outcome="failed", duration_s=999999, offset_s=7, batch="c" * 12)]
        model = fit(noisy)
        self.assertEqual(model.rows_used, len(rows))
        self.assertEqual(model.rows_excluded, 1)
        self.assertEqual(model.root.mean, fit(rows).root.mean)


class Plans(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = fit(dataset(sessions=120))

    def plan(self, tasks, cap=4, draws=3000, seed=5):
        return est.estimate(self.model, tasks, cap, draws=draws, seed=seed)

    def test_ranges_are_ordered(self):
        plan = self.plan([Task("a", "feature"), Task("b", "review", risk="low")])
        self.assertLess(plan.p50, plan.p80)
        self.assertLess(plan.p80, plan.p90)
        for task in plan.tasks:
            self.assertLess(task.p50, task.p80)
            self.assertLess(task.p80, task.p90)

    def test_results_are_reproducible(self):
        tasks = [Task("a", "feature"), Task("b", "docs", depends_on=("a",))]
        self.assertEqual(self.plan(tasks), self.plan(tasks))

    def test_a_different_seed_changes_the_draws_only_slightly(self):
        tasks = [Task("a", "feature")]
        first, second = self.plan(tasks, seed=1).p50, self.plan(tasks, seed=2).p50
        self.assertNotEqual(first, second)
        self.assertAlmostEqual(first / second, 1, delta=0.15)

    def test_a_chain_is_the_critical_path(self):
        tasks = [
            Task("a", "feature"),
            Task("b", "feature", depends_on=("a",)),
            Task("c", "feature", depends_on=("b",)),
        ]
        plan = self.plan(tasks)
        self.assertEqual(plan.critical_path, ("a", "b", "c"))
        self.assertTrue(all(task.criticality == 1.0 for task in plan.tasks))

    def test_merge_bias_is_captured(self):
        """Two parallel paths finish later than either one alone would suggest."""
        parallel = [Task("a", "feature"), Task("b", "feature")]
        plan = self.plan(parallel, cap=2)
        self.assertGreater(plan.p50, plan.tasks[0].p50)

    def test_total_is_below_the_sum_of_task_p80s(self):
        tasks = [Task(str(i), "feature") for i in range(6)]
        plan = self.plan(tasks, cap=6)
        self.assertLess(plan.p80, sum(task.p80 for task in plan.tasks))

    def test_many_tasks_keep_a_real_range(self):
        """The session effect and shared cell shift stop the total concentrating."""
        tasks = [Task(str(i), "feature") for i in range(100)]
        plan = self.plan(tasks, cap=4, draws=1500)
        self.assertGreater(plan.p90 / plan.p50, 1.3)

    def test_more_agents_never_slow_a_plan(self):
        tasks = [Task(str(i), "feature") for i in range(12)]
        one = self.plan(tasks, cap=1)
        four = self.plan(tasks, cap=4)
        self.assertLessEqual(four.p50, one.p50 * 1.01)

    def test_effective_parallelism_is_reported_and_bounded(self):
        plan = self.plan([Task("a", "feature")], cap=2)
        self.assertLessEqual(plan.effective_parallelism, 2)
        self.assertGreater(plan.parallelism_sessions, 0)

    def test_thin_cells_warn(self):
        plan = self.plan([Task("a", "migration", risk="high")])
        self.assertTrue(any("migration" in note for note in plan.warnings))

    def test_a_well_populated_cell_does_not_warn(self):
        plan = self.plan([Task("a", "feature", model="claude-opus-5-5")])
        self.assertEqual(plan.warnings, ())

    def test_duplicate_cell_warnings_collapse(self):
        tasks = [Task(str(i), "migration", risk="high") for i in range(5)]
        self.assertEqual(len(self.plan(tasks).warnings), 1)

    def test_no_sessions_means_serial_with_a_warning(self):
        model = fit(plain_rows(40, 60))
        plan = est.estimate(model, [Task("a", "feature")], 4, draws=500)
        self.assertIsNone(plan.effective_parallelism)
        self.assertTrue(any("one at a time" in note for note in plan.warnings))


class Throughput(unittest.TestCase):
    def session(self, batch, peak, durations, rate):
        rows, offset = [], 0
        for duration in durations:
            rows.append(
                row(batch=batch, parallel_agents=peak, duration_s=duration, offset_s=offset)
            )
            offset += round(duration / rate)
        return rows

    def build(self, rates):
        rows = []
        for peak, rate in rates.items():
            for s in range(8):
                rows += self.session(f"{peak:06x}{s:06x}", peak, [100] * 20, rate)
        return rows

    def test_learns_rate_by_concurrency(self):
        model = fit(self.build({1: 1.0, 2: 1.5, 4: 2.5}))
        self.assertAlmostEqual(model.throughput(1).median, 1.0, delta=0.1)
        self.assertAlmostEqual(model.throughput(4).median, 2.5, delta=0.2)

    def test_medians_never_decrease_with_concurrency(self):
        model = fit(self.build({1: 2.0, 2: 1.0, 4: 3.0}))
        medians = [bucket.median for bucket in model.buckets]
        self.assertEqual(medians, sorted(medians))

    def test_a_cap_beyond_the_data_uses_the_top_bucket(self):
        model = fit(self.build({1: 1.0, 2: 1.5}))
        self.assertIs(model.throughput(50), model.buckets[-1])

    def test_a_cap_between_observed_peaks_uses_the_bucket_below(self):
        model = fit(self.build({1: 1.0, 3: 1.5, 6: 2.5}))
        self.assertEqual(model.throughput(5).low, 3)

    def test_a_cap_below_every_peak_uses_the_lowest_bucket(self):
        model = fit(self.build({3: 1.5, 6: 2.5}))
        self.assertEqual(model.throughput(1).low, 3)

    def test_rare_peaks_merge_into_neighbors(self):
        rows = self.build({1: 1.0}) + self.session("e" * 12, 9, [100] * 20, 3.0)
        model = fit(rows)
        self.assertEqual(len(model.buckets), 1)

    def test_cap_limits_the_rate(self):
        model = fit(self.build({1: 1.0, 6: 5.0}))
        plan = est.estimate(model, [Task("a", "feature")], 2, draws=500)
        self.assertLessEqual(plan.effective_parallelism, 2.0)


class Validation(unittest.TestCase):
    def test_too_little_data(self):
        with self.assertRaisesRegex(ValueError, "at least"):
            fit(plain_rows(3, 60))

    def test_cycles_are_rejected(self):
        tasks = [Task("a", "feature", depends_on=("b",)), Task("b", "feature", depends_on=("a",))]
        with self.assertRaisesRegex(ValueError, "cycle"):
            est.topological_order(tasks)

    def test_unknown_dependency(self):
        with self.assertRaisesRegex(ValueError, "unknown task"):
            est.topological_order([Task("a", "feature", depends_on=("z",))])

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            est.topological_order([Task("a", "feature"), Task("a", "docs")])

    def test_estimate_arguments(self):
        model = fit(dataset())
        for tasks, cap, draws in (([Task("a", "docs")], 0, 500), ([], 1, 500)):
            with self.assertRaises(ValueError):
                est.estimate(model, tasks, cap, draws=draws)
        with self.assertRaises(ValueError):
            est.estimate(model, [Task("a", "docs")], 1, draws=5)


class PlanDocuments(unittest.TestCase):
    def test_a_full_document(self):
        tasks, cap = est.tasks_from_json(
            {
                "agent_cap": 3,
                "model": "claude-opus-5-5",
                "tasks": [
                    {"id": "a", "kind": "feature", "size": "M", "risk": "high"},
                    {"id": 2, "kind": "docs", "depends_on": ["a"], "model": "claude-sonnet-5-5"},
                ],
            }
        )
        self.assertEqual(cap, 3)
        self.assertEqual(tasks[0].model, "claude-opus-5-5")
        self.assertEqual(tasks[1].model, "claude-sonnet-5-5")
        self.assertEqual(tasks[1].id, "2")
        self.assertEqual(tasks[1].depends_on, ("a",))

    def test_bad_documents(self):
        cases = [
            [],
            {},
            {"tasks": [{"id": "a", "kind": "nope"}]},
            {"tasks": [{"kind": "docs"}]},
            {"tasks": [{"id": "a", "kind": "docs", "risk": "nope"}]},
            {"tasks": [{"id": "a", "kind": "docs", "size": "huge"}]},
            {"tasks": [{"id": "a", "kind": "docs", "depends_on": "b"}]},
            {"tasks": [], "agent_cap": 0},
            {"tasks": [], "agent_cap": True},
        ]
        for document in cases:
            with self.subTest(document=document), self.assertRaises(ValueError):
                est.tasks_from_json(document)


if __name__ == "__main__":
    unittest.main()

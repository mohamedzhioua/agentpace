import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, ingest, pseudonym, schema
from agentpace.adapters import ADAPTERS, get_adapter
from agentpace.adapters.base import ParseStats, RawRun
from agentpace.adapters.claude_code import ClaudeCodeAdapter
from agentpace.cache import RunCache
from tests import synthetic
from tests.synthetic import CANARIES, assistant, simple_run, user, write_run

SECRET = b"\x01" * 32
ADAPTER = ClaudeCodeAdapter()


def raw(start=0.0, end=100.0, session="s", spans=None, **overrides):
    values = {
        "harness": "claude-code",
        "model": "claude-opus-5-5",
        "start": start,
        "end": end,
        "spans": spans or ((start, end),),
        "active_s": round(end - start),
        "tool_calls": 3,
        "edit_calls": 0,
        "tokens": 10,
        "errors": 0,
        "outcome": "completed",
        "agent_type": "reviewer",
        "session": session,
        "project": "p",
    }
    values.update(overrides)
    return RawRun(**values)


class TempRoot(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def parse_only(self):
        stats = ParseStats()
        sources = list(ADAPTER.discover(self.root))
        return [ADAPTER.parse(s, 900, stats) for s in sources], stats


class Parsing(TempRoot):
    def test_counts_and_tokens_are_deduplicated_by_message(self):
        write_run(
            self.root,
            "p",
            "s",
            "a",
            [
                user(0),
                assistant(10, "m1", "Read", input_tokens=100, output_tokens=5),
                assistant(10, "m1", "Edit", input_tokens=100, output_tokens=50),
                user(20),
                assistant(30, "m2", "SubagentHandback", input_tokens=7, output_tokens=3),
            ],
        )
        (run,), _ = self.parse_only()
        self.assertEqual(run.tool_calls, 3)
        self.assertEqual(run.edit_calls, 1)
        self.assertEqual(run.tokens, 150 + 10)
        self.assertEqual(run.active_s, 30)

    def test_idle_gaps_are_clipped_and_split_spans(self):
        write_run(self.root, "p", "s", "a", [*simple_run(0, 10), *simple_run(5000, 10)])
        (run,), _ = self.parse_only()
        self.assertEqual(run.active_s, 10 + 900 + 10)
        self.assertEqual(len(run.spans), 2)

    def test_synthetic_model_entries_are_ignored(self):
        write_run(
            self.root,
            "p",
            "s",
            "a",
            [
                user(0),
                assistant(1, "x", model="<synthetic>"),
                assistant(2, "y", model="<synthetic>"),
                assistant(3, "z", model="claude-sonnet-5-5"),
            ],
        )
        (run,), _ = self.parse_only()
        self.assertEqual(run.model, "claude-sonnet-5-5")

    def test_errored_tool_results_count_as_retries(self):
        write_run(
            self.root,
            "p",
            "s",
            "a",
            [user(0, is_error=True), assistant(5, "a", "Bash"), user(6), assistant(9, "b")],
        )
        (run,), _ = self.parse_only()
        self.assertEqual(run.errors, 1)

    def test_nested_workflow_transcripts_are_found(self):
        write_run(self.root, "p", "s", "a", simple_run(0, 10), nested="workflows/wf_1")
        (run,), _ = self.parse_only()
        self.assertEqual((run.session, run.project), ("s", "p"))

    def test_malformed_lines_are_counted_not_fatal(self):
        path = write_run(self.root, "p", "s", "a", simple_run(0, 10))
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"type":"assistant", CANARY_PROMPT\n')
        (run,), stats = self.parse_only()
        self.assertIsNotNone(run)
        self.assertEqual(stats.unparsed_lines, 1)

    def test_unknown_record_types_are_skipped(self):
        write_run(self.root, "p", "s", "a", [{"type": "new-thing"}, *simple_run(0, 10)])
        (run,), _ = self.parse_only()
        self.assertEqual(run.active_s, 10)

    def test_runs_with_no_activity_are_counted_as_empty(self):
        write_run(self.root, "p", "s", "a", [user(0)])
        (run,), stats = self.parse_only()
        self.assertIsNone(run)
        self.assertEqual(stats.empty_runs, 1)

    def test_missing_meta_is_tolerated(self):
        path = write_run(self.root, "p", "s", "a", simple_run(0, 10))
        path.with_suffix(".meta.json").unlink()
        (run,), _ = self.parse_only()
        self.assertIsNone(run.agent_type)

    def test_effort_is_captured_when_recorded(self):
        write_run(self.root, "p", "s", "a", simple_run(0, 10), effort="xhigh")
        write_run(self.root, "p", "s", "b", simple_run(20, 10))
        rows, _ = ingest.ingest(ADAPTER, self.root, SECRET)
        self.assertEqual([row.get("effort") for row in rows], ["xhigh", None])
        self.assertEqual([schema.check_row(row) for row in rows], [[], []])

    def test_an_unknown_effort_is_dropped(self):
        for i, value in enumerate(("turbo", 3, None, ["high"], "HIGH")):
            write_run(self.root, "p", "s", str(i), simple_run(i * 20, 10), effort=value)
        runs, _ = self.parse_only()
        self.assertEqual([run.effort for run in runs], [None] * 5)

    def test_a_single_file_path_is_accepted(self):
        path = write_run(self.root, "p", "s", "a", simple_run(0, 10))
        self.assertEqual(len(list(ADAPTER.discover(path))), 1)

    def test_unreadable_files_are_counted(self):
        stats = ParseStats()
        write_run(self.root, "p", "s", "a", simple_run(0, 10))
        source = next(ADAPTER.discover(self.root))
        with mock.patch("builtins.open", side_effect=OSError("no")):
            self.assertIsNone(ADAPTER.parse(source, 900, stats))
        self.assertEqual(stats.unreadable_files, 1)

    @unittest.skipUnless(os.name == "nt", "the 260-character limit is Windows only")
    def test_paths_beyond_the_windows_limit_are_read(self):
        deep = "d" * 120
        extended = Path("\\\\?\\" + str(self.root))
        self.addCleanup(shutil.rmtree, extended / deep, ignore_errors=True)
        write_run(extended, deep, deep + "s", "a", simple_run(0, 10), nested="w" * 60)
        (run,), _ = self.parse_only()
        self.assertIsNotNone(run)


class Outcome(TempRoot):
    def outcome(self, lines, finished=None):
        write_run(self.root, "p", "s", "a", lines, finished=finished)
        (run,), _ = self.parse_only()
        return run.outcome

    def test_terminal_tool_means_completed(self):
        self.assertEqual(self.outcome(simple_run(0, 10)), "completed")

    def test_finished_flag_means_completed(self):
        self.assertEqual(
            self.outcome([user(0), assistant(5, "a", "Read")], finished=True), "completed"
        )

    def test_end_turn_means_completed(self):
        lines = [user(0), assistant(5, "a", stop_reason="end_turn")]
        self.assertEqual(self.outcome(lines), "completed")

    def test_refusal_wins_over_finished(self):
        lines = [user(0), assistant(5, "a", stop_reason="refusal")]
        self.assertEqual(self.outcome(lines, finished=True), "failed")

    def test_errored_final_result_without_completion_signal_fails(self):
        lines = [assistant(0, "a", "Bash"), user(5, is_error=True)]
        self.assertEqual(self.outcome(lines), "failed")

    def test_no_signal_is_unknown(self):
        self.assertEqual(self.outcome([assistant(0, "a", "Bash"), user(5)]), "unknown")


class ModelNames(unittest.TestCase):
    def test_public_families_survive(self):
        cases = {
            "claude-opus-5-5": "claude-opus-5-5",
            "claude-opus-5-5-20261001": "claude-opus-5-5",
            "claude-opus-5-5[1m]": "claude-opus-5-5",
            "claude-3-5-sonnet-20241022": "claude-3-5-sonnet",
            "gpt-6-luna": "gpt-6-luna",
            "gemini-2.5-pro": "gemini-2.5-pro",
        }
        for name, family in cases.items():
            with self.subTest(name=name):
                self.assertEqual(ingest.normalize_model(name), family)

    def test_arns_keep_only_the_family(self):
        arn = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/claude-sonnet-5-5"
        self.assertEqual(ingest.normalize_model(arn), "claude-sonnet-5-5")

    def test_private_names_become_other(self):
        for name in ("acme-internal-llm", "my.proxy.corp/model"):
            self.assertEqual(ingest.normalize_model(name), "other")


class Rows(unittest.TestCase):
    def test_rows_are_valid_and_pseudonymous(self):
        rows, dropped, _ = ingest.build_rows([raw(session="real-session-id")], SECRET)
        self.assertEqual(dropped, 0)
        self.assertEqual(schema.check_row(rows[0]), [])
        self.assertEqual(rows[0]["batch"], pseudonym.batch(SECRET, "real-session-id"))
        self.assertNotIn("real-session-id", schema.canonical(rows[0]))

    def test_the_same_input_gives_identical_rows(self):
        runs = [raw(0, 100), raw(50, 200)]
        self.assertEqual(ingest.build_rows(runs, SECRET), ingest.build_rows(runs, SECRET))

    def test_different_secrets_give_unlinkable_rows(self):
        first, _, _ = ingest.build_rows([raw()], SECRET)
        second, _, _ = ingest.build_rows([raw()], b"\x02" * 32)
        for field in ("contributor", "batch", "project"):
            self.assertNotEqual(first[0][field], second[0][field])

    def test_zero_duration_runs_are_dropped_and_counted(self):
        rows, dropped, _ = ingest.build_rows([raw(active_s=0)], SECRET)
        self.assertEqual((rows, dropped), ([], 1))

    def test_peak_overlap_is_per_run(self):
        runs = [raw(0, 100), raw(50, 150), raw(60, 70), raw(500, 600)]
        peaks = [row["parallel_agents"] for row in ingest.build_rows(runs, SECRET)[0]]
        self.assertEqual(peaks, [3, 3, 3, 1])

    def test_runs_touching_at_an_instant_do_not_overlap(self):
        peaks = ingest.peak_overlaps([raw(0, 100), raw(100, 200)])
        self.assertEqual(peaks, {0: 2, 1: 2})

    def test_week_is_iso_and_utc(self):
        self.assertEqual(ingest.iso_week(0), "1970-W01")
        # 2026-12-31 23:30 UTC belongs to ISO week 53 of 2026.
        self.assertEqual(ingest.iso_week(1_798_759_800), "2026-W53")


class SessionClock(unittest.TestCase):
    def test_offsets_skip_idle_time(self):
        runs = [raw(0, 100), raw(5000, 5100)]
        rows, _, _ = ingest.build_rows(runs, SECRET)
        self.assertEqual([r["offset_s"] for r in rows], [0, 100 + 900])

    def test_runs_tied_to_the_second_stay_distinct(self):
        rows, _, _ = ingest.build_rows([raw(0, 100), raw(0, 100), raw(0, 100)], SECRET)
        self.assertEqual([r["offset_s"] for r in rows], [0, 1, 2])
        self.assertEqual(len({schema.dedupe_key(r) for r in rows}), 3)

    def test_offset_plus_duration_recovers_the_span_for_sequential_runs(self):
        runs = [raw(0, 100), raw(150, 300), raw(320, 330)]
        rows, _, _ = ingest.build_rows(runs, SECRET)
        span = max(r["offset_s"] + r["duration_s"] for r in rows)
        self.assertEqual(span, 330)

    def test_overlapping_runs_share_the_clock(self):
        rows, _, _ = ingest.build_rows([raw(0, 100), raw(40, 80)], SECRET)
        self.assertEqual([r["offset_s"] for r in rows], [0, 40])

    def test_a_long_gap_inside_a_run_is_clipped_on_the_session_clock(self):
        long_run = raw(0, 5000, spans=((0, 50), (4950, 5000)), active_s=1000)
        rows, _, _ = ingest.build_rows([long_run, raw(5100, 5200)], SECRET)
        self.assertEqual(rows[1]["offset_s"], 50 + 900 + 50 + 100)


class Privacy(TempRoot):
    """No sensitive value may reach a row, a report, a log or an error."""

    def build_history(self):
        write_run(
            self.root,
            "CANARY_REPO-project",
            "CANARY_PATH-session",
            "1",
            [*simple_run(0, 40, "Edit"), user(50, is_error=True)],
            agent_type="CANARY_AGENT",
            effort="CANARY_EFFORT",
        )
        write_run(
            self.root,
            "CANARY_REPO-project",
            "CANARY_PATH-session",
            "2",
            [user(0), assistant(1, "m", "CANARY_TOOL_INPUT", model="CANARY_REPO-model")],
            agent_type="plugin:CANARY_AGENT",
        )
        broken = self.root / "CANARY_REPO-project" / "CANARY_PATH-session" / "subagents"
        (broken / "agent-3.jsonl").write_text('{"type":"user" CANARY_PROMPT\n', encoding="utf-8")

    def run_cli(self):
        out, err = io.StringIO(), io.StringIO()
        store = self.root / "out" / "runs.jsonl"
        env = {"AGENTPACE_HOME": str(self.root / "home")}
        with (
            mock.patch.dict(os.environ, env),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["ingest", str(self.root), "--out", str(store)])
        self.assertEqual(code, 0)
        return out.getvalue() + err.getvalue() + store.read_text(encoding="utf-8")

    def test_no_canary_reaches_any_output(self):
        self.build_history()
        output = self.run_cli()
        for canary in CANARIES:
            self.assertNotIn(canary, output)
        self.assertNotIn(self.root.name, output)

    def test_repr_of_a_run_hides_identifying_fields(self):
        text = repr(raw(session="CANARY_PATH", project="CANARY_REPO", agent_type="CANARY_AGENT"))
        for canary in ("CANARY_PATH", "CANARY_REPO", "CANARY_AGENT"):
            self.assertNotIn(canary, text)

    def test_repr_of_a_source_hides_identifying_fields(self):
        write_run(self.root, "CANARY_REPO", "s", "a", simple_run(0, 10))
        self.assertNotIn("CANARY_REPO", repr(next(ADAPTER.discover(self.root))))


class Secret(TempRoot):
    def test_secret_is_stable_and_random(self):
        first = pseudonym.load_secret(self.root / "a")
        self.assertEqual(first, pseudonym.load_secret(self.root / "a"))
        self.assertNotEqual(first, pseudonym.load_secret(self.root / "b"))

    @unittest.skipIf(os.name == "nt", "POSIX permissions")
    def test_secret_is_owner_only(self):
        pseudonym.load_secret(self.root)
        self.assertEqual((self.root / "secret").stat().st_mode & 0o777, 0o600)


class Store(TempRoot):
    def test_old_rows_survive_when_transcripts_are_gone(self):
        store = self.root / "runs.jsonl"
        ingest.merge_into(store, [synthetic.row(offset_s=1)])
        total = ingest.merge_into(store, [synthetic.row(offset_s=2)])
        self.assertEqual(total, 2)

    def test_reingesting_is_idempotent(self):
        store = self.root / "runs.jsonl"
        ingest.merge_into(store, [synthetic.row()])
        self.assertEqual(ingest.merge_into(store, [synthetic.row()]), 1)

    def test_a_relabeled_run_replaces_the_old_row(self):
        store = self.root / "runs.jsonl"
        ingest.merge_into(store, [synthetic.row()])
        ingest.merge_into(store, [synthetic.row(task_kind="bugfix")])
        self.assertEqual(schema.read_rows(store)[0]["task_kind"], "bugfix")


class Cache(TempRoot):
    """A second ingest re-reads only what changed, and gives the same rows."""

    def setUp(self):
        super().setUp()
        self.history = self.root / "history"
        for i in range(3):
            write_run(self.history, "p", "s", str(i), simple_run(i * 100, 40))
        self.cache_path = self.root / "home" / "cache.json"

    def cache(self, version=1):
        return RunCache(self.cache_path, "claude-code", version, 900)

    def ingest(self, version=1):
        """Return the rows and how many files were parsed."""
        cache = self.cache(version)
        with mock.patch.object(ADAPTER, "parse", wraps=ADAPTER.parse) as parse:
            rows, report = ingest.ingest(ADAPTER, self.history, SECRET, cache=cache)
        return rows, parse.call_count, report

    def test_a_second_ingest_parses_nothing_and_gives_the_same_rows(self):
        first, calls, _ = self.ingest()
        self.assertEqual(calls, 3)
        second, calls, report = self.ingest()
        self.assertEqual((calls, report.cached), (0, 3))
        self.assertEqual(second, first)

    def test_a_changed_file_is_parsed_again(self):
        self.ingest()
        write_run(self.history, "p", "s", "1", simple_run(100, 50))
        rows, calls, _ = self.ingest()
        self.assertEqual(calls, 1)
        self.assertIn(50, [row["duration_s"] for row in rows])

    def test_a_settled_unfinished_run_is_not_parsed_again(self):
        write_run(self.history, "p", "s", "u", [user(500), assistant(510, "u1", "Read")])
        self.ingest()
        rows, calls, _ = self.ingest()
        self.assertEqual(calls, 0)
        self.assertIn("unknown", [row["outcome"] for row in rows])

    def test_a_meta_file_that_flips_to_finished_is_seen(self):
        lines = [user(500), assistant(510, "u1", "Read")]
        transcript = write_run(self.history, "p", "s", "u", lines)
        self.ingest()
        meta = transcript.with_suffix(".meta.json")
        meta.write_text(json.dumps({"agentType": "implementer", "finished": True}), "utf-8")
        rows, calls, _ = self.ingest()
        self.assertEqual(calls, 1)
        self.assertNotIn("unknown", [row["outcome"] for row in rows])

    def test_a_new_adapter_version_discards_the_cache(self):
        self.ingest()
        _, calls, report = self.ingest(version=2)
        self.assertEqual(calls, 3)
        self.assertTrue(report.cache_rebuilt)

    def test_a_corrupt_cache_is_rebuilt(self):
        self.ingest()
        self.cache_path.write_text("{not json", encoding="utf-8")
        rows, calls, report = self.ingest()
        self.assertEqual((calls, len(rows)), (3, 3))
        self.assertTrue(report.cache_rebuilt)
        self.assertIn("(cache rebuilt)", "\n".join(report.lines()))

    def test_a_deleted_transcript_keeps_its_row_and_its_siblings_offsets(self):
        first, _, _ = self.ingest()
        (self.history / "p" / "s" / "subagents" / "agent-0.jsonl").unlink()
        second, calls, _ = self.ingest()
        self.assertEqual(calls, 0)
        self.assertEqual(
            sorted(map(schema.dedupe_key, second)), sorted(map(schema.dedupe_key, first))
        )

    def test_no_content_reaches_the_cache(self):
        write_run(
            self.history,
            "p",
            "s",
            "c",
            [*simple_run(700, 20, "CANARY_TOOL_INPUT"), user(730, is_error=True)],
            effort="CANARY_EFFORT",
        )
        self.ingest()
        text = self.cache_path.read_text(encoding="utf-8")
        for canary in set(CANARIES) - {"CANARY_PATH", "CANARY_REPO", "CANARY_AGENT"}:
            self.assertNotIn(canary, text)

    def test_raw_model_names_are_normalized_before_caching(self):
        model = "arn:aws:bedrock:CANARY_REPO:claude-opus-5-5-v1"
        lines = [user(800), assistant(810, "x", "SubagentHandback", model=model)]
        write_run(self.history, "p", "s", "m", lines)
        self.ingest()
        self.assertNotIn("arn:aws", self.cache_path.read_text(encoding="utf-8"))

    def test_normalizing_a_model_twice_changes_nothing(self):
        for raw_name in (
            "claude-opus-5-5",
            "us.anthropic.claude-sonnet-5-5-v1",
            "gpt-6.1-sol",
            "x",
        ):
            once = ingest.normalize_model(raw_name)
            self.assertEqual(ingest.normalize_model(once), once)


class StillRunning(TempRoot):
    """An ingest made while an agent runs must not store its partial run (the ghost-row bug).

    Sibling A starts first, goes idle past the cap, then is active again when B completes. So B's
    offset depends on A being on the session clock, even while A's own row is withheld.
    """

    BASE = synthetic.BASE.timestamp()
    RUNNING = (user(0), assistant(30, "a1", "Read"), user(1550))

    def write(self, a_lines):
        write_run(self.root, "p", "s", "a", a_lines)
        write_run(self.root, "p", "s", "b", simple_run(1500, 40))

    def rows_at(self, seconds):
        rows, report = ingest.ingest(ADAPTER, self.root, SECRET, now=self.BASE + seconds)
        return rows, report

    def test_a_running_sibling_is_withheld_then_stored_once(self):
        store = self.root / "runs.jsonl"
        self.write(self.RUNNING)
        first, report = self.rows_at(1600)
        self.assertEqual(report.still_running, 1)
        self.assertEqual([r["outcome"] for r in first], ["completed"])
        ingest.merge_into(store, first)

        self.write([*self.RUNNING, assistant(1700, "a2", "SubagentHandback")])
        second, report = self.rows_at(1800)
        self.assertEqual(report.still_running, 0)
        ingest.merge_into(store, second)
        stored = schema.read_rows(store)
        self.assertEqual(len(stored), 2)
        self.assertEqual(sorted(r["outcome"] for r in stored), ["completed", "completed"])

    def test_siblings_keep_their_offsets_while_one_is_withheld(self):
        self.write(self.RUNNING)
        (b_before,) = self.rows_at(1600)[0]
        self.write([*self.RUNNING, assistant(1700, "a2", "SubagentHandback")])
        after = self.rows_at(1800)[0]
        self.assertIn(schema.dedupe_key(b_before), {schema.dedupe_key(r) for r in after})
        self.assertEqual(b_before["offset_s"], 930)  # 30 s busy, then a gap clipped to 900

    def test_an_unfinished_run_idle_past_the_cap_is_stored_as_unknown(self):
        self.write(self.RUNNING)
        rows, report = self.rows_at(1550 + 901)
        self.assertEqual(report.still_running, 0)
        self.assertEqual(sorted(r["outcome"] for r in rows), ["completed", "unknown"])


class Labels(unittest.TestCase):
    def test_labels_override_heuristics(self):
        rows, _, _ = ingest.build_rows([raw()], SECRET)
        ref = ingest.run_ref(rows[0])
        labels = {ref: {"kind": "bugfix", "risk": "high"}}
        relabeled, _, _ = ingest.build_rows([raw()], SECRET, labels=labels)
        self.assertEqual((relabeled[0]["task_kind"], relabeled[0]["risk"]), ("bugfix", "high"))


class Adapters(unittest.TestCase):
    def test_registry_lookup(self):
        self.assertIs(get_adapter("claude-code"), ADAPTERS["claude-code"])

    def test_unknown_adapter_is_a_clear_error(self):
        with self.assertRaisesRegex(ValueError, "unknown source"):
            get_adapter("nope")

    def test_a_second_harness_needs_only_the_interface(self):
        class Fake:
            name = "fake"

            def default_path(self):
                return Path(".")

            def discover(self, path):
                yield from ()

            def parse(self, source, idle_cap_s, stats):
                return None

        rows, report = ingest.ingest(Fake(), Path("."), SECRET)
        self.assertEqual((rows, report.files), ([], 0))


class Cli(TempRoot):
    def run_cli(self, *args):
        err = io.StringIO()
        env = {"AGENTPACE_HOME": str(self.root / "home")}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(err):
            return cli.main(list(args)), err.getvalue()

    def test_missing_path_is_a_clean_error(self):
        code, err = self.run_cli("ingest", str(self.root / "missing"))
        self.assertEqual(code, 2)
        self.assertNotIn(self.root.name, err)

    def test_kind_map_is_applied(self):
        write_run(self.root, "p", "s", "a", simple_run(0, 10), agent_type="custom")
        store = self.root / "o.jsonl"
        self.run_cli("ingest", str(self.root), "--out", str(store), "--kind-map", "custom=docs")
        self.assertEqual(schema.read_rows(store)[0]["task_kind"], "docs")

    def test_bad_kind_map_is_rejected(self):
        code, _ = self.run_cli("ingest", str(self.root), "--kind-map", "custom=nonsense")
        self.assertEqual(code, 2)

    def test_rows_file_holds_json_lines(self):
        write_run(self.root, "p", "s", "a", simple_run(0, 10))
        store = self.root / "o.jsonl"
        self.run_cli("ingest", str(self.root), "--out", str(store))
        for line in store.read_text(encoding="utf-8").splitlines():
            self.assertIsInstance(json.loads(line), dict)


class Hook(TempRoot):
    """`ingest --hook`, run by Claude Code after each subagent stops."""

    def setUp(self):
        super().setUp()
        self.history = self.root / "projects"
        self.paths = [
            write_run(self.history, "p", "s", str(i), simple_run(i * 30, 40)) for i in range(3)
        ]
        write_run(self.history, "p", "other-session", "x", simple_run(0, 10))
        self.store = self.root / "home" / "runs.jsonl"

    def hook(self, payload):
        out, err = io.StringIO(), io.StringIO()
        text = payload if isinstance(payload, str) else json.dumps(payload)
        env = {"AGENTPACE_HOME": str(self.root / "home")}
        with (
            mock.patch.dict(os.environ, env),
            mock.patch("sys.stdin", io.StringIO(text)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(["ingest", "--hook"])
        return code, out.getvalue() + err.getvalue()

    def payload(self, path, **extra):
        return {"hook_event_name": "SubagentStop", "agent_transcript_path": str(path), **extra}

    def test_the_hook_ingests_the_whole_session_like_a_full_ingest(self):
        code, output = self.hook(self.payload(self.paths[1]))
        self.assertEqual((code, output), (0, ""))
        full, _ = ingest.ingest(ADAPTER, self.history, pseudonym.load_secret(self.root / "home"))
        session = {r["batch"] for r in schema.read_rows(self.store)}
        expected = [r for r in full if r["batch"] in session]
        self.assertEqual(len(expected), 3)  # only this session, every sibling
        self.assertEqual(
            sorted(map(schema.canonical, schema.read_rows(self.store))),
            sorted(map(schema.canonical, expected)),
        )

    def test_no_content_from_the_payload_or_transcript_leaks(self):
        transcript = write_run(
            self.history,
            "CANARY_REPO",
            "CANARY_PATH",
            "c",
            [*simple_run(0, 40, "CANARY_TOOL_INPUT"), user(50, is_error=True)],
            effort="CANARY_EFFORT",
        )
        payload = self.payload(transcript, last_assistant_message="CANARY_RESULT CANARY_CODE")
        code, output = self.hook(payload)
        self.assertEqual(code, 0)
        written = self.store.read_text(encoding="utf-8")
        cache = (self.root / "home" / "cache-claude-code.json").read_text(encoding="utf-8")
        for canary in CANARIES:
            self.assertNotIn(canary, output + written)
        for canary in ("CANARY_RESULT", "CANARY_CODE", "CANARY_TOOL_INPUT", "CANARY_EFFORT"):
            self.assertNotIn(canary, cache)

    def test_bad_payloads_do_nothing_and_exit_zero(self):
        for payload in (
            {"agent_transcript_path": ""},
            {"agent_transcript_path": str(self.root / "missing" / "subagents" / "a.jsonl")},
            {"agent_transcript_path": str(self.root / "no-anchor.jsonl")},
            {"agent_transcript_path": 7},
            {},
            [],
            "not json",
        ):
            self.assertEqual(self.hook(payload), (0, ""), payload)
        self.assertFalse(self.store.exists())

    def test_a_busy_store_is_left_alone(self):
        self.store.parent.mkdir(parents=True)
        self.store.with_name("runs.jsonl.lock").touch()
        with (
            mock.patch.object(ingest.time, "sleep"),
            mock.patch.object(ingest.time, "monotonic", side_effect=[0.0, 10.0]),
        ):
            self.assertEqual(self.hook(self.payload(self.paths[0])), (0, ""))
        self.assertFalse(self.store.exists())


class StoreLock(TempRoot):
    def test_a_second_writer_waits_then_gives_up(self):
        store = self.root / "runs.jsonl"
        with (
            ingest.store_lock(store),
            self.assertRaises(ingest.StoreBusy),
            ingest.store_lock(store, wait_s=0.1),
        ):
            pass
        with ingest.store_lock(store, wait_s=0.1):  # released, so free again
            pass

    def test_a_lock_left_by_a_crash_is_taken_over(self):
        store = self.root / "runs.jsonl"
        lock = store.with_name("runs.jsonl.lock")
        lock.touch()
        os.utime(lock, (0, 0))
        with ingest.store_lock(store, wait_s=0.1):
            pass
        self.assertFalse(lock.exists())

    def test_two_writers_end_with_the_union(self):
        import threading

        store = self.root / "runs.jsonl"

        def write(offset):
            with ingest.store_lock(store):
                ingest.merge_into(store, [synthetic.row(offset_s=offset)])

        threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(schema.read_rows(store)), 8)


if __name__ == "__main__":
    unittest.main()

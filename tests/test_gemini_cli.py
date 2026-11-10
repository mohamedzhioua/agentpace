"""SYNTHETIC Gemini CLI sessions. Nothing here came from a real session.

The fixtures in `tests/fixtures/gemini_cli.py` follow the Gemini CLI source at a pinned commit
(`UPSTREAM`). The adapter has not been checked against real session files yet.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, ingest, paths, schema
from agentpace.adapters import get_adapter
from agentpace.adapters.base import ParseStats
from agentpace.adapters.gemini_cli import GeminiCliAdapter
from agentpace.cache import RunCache
from tests.fixtures.gemini_cli import (
    CANARIES,
    PROJECT_DIR,
    PROJECT_HASH,
    UPSTREAM,
    compression,
    info,
    interrupted,
    metadata,
    prompt,
    repairs,
    reply,
    rewind,
    scratchpad,
    session,
    touched,
    turn,
)

ADAPTER = get_adapter("gemini-cli")


class Temp(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.history = self.root / "tmp"
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.files = 0

    def write(self, lines, name=None, parent=None):
        self.files += 1
        folder = self.history / PROJECT_DIR / "chats"
        if parent is not None:
            folder = folder / parent
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name or f"session-2026-11-02T09-00-{self.files:08d}.jsonl")
        text = "\n".join(item if isinstance(item, str) else json.dumps(item) for item in lines)
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def ingest(self):
        code, _, err = self.run_cli("ingest", str(self.history), "--source", "gemini-cli")
        self.assertEqual(code, 0, err)
        store = paths.local_runs()
        return (schema.read_rows(store) if store.exists() else []), err

    def parse(self, path):
        stats = ParseStats()
        (source,) = list(ADAPTER.discover(path))
        return ADAPTER.parse(source, 900, stats), stats


class Runs(Temp):
    def test_the_fixtures_name_their_upstream_commit(self):
        self.assertRegex(UPSTREAM, r"^google-gemini/gemini-cli@[0-9a-f]{40}$")

    def test_each_turn_is_one_run_and_the_file_is_one_batch(self):
        self.write(session(*turn(0, 60, "a"), *turn(300, 330, "b")))
        rows, _ = self.ingest()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["batch"] for row in rows}), 1)
        first, second = sorted(rows, key=lambda row: row["offset_s"])
        self.assertEqual((first["offset_s"], first["duration_s"]), (0, 60))
        self.assertEqual(second["offset_s"], 300)  # a gap under the idle cap stays on the clock
        self.assertEqual(second["duration_s"], 30)
        for row in rows:
            self.assertEqual(row["harness"], "gemini-cli")
            self.assertEqual(row["model"], "gemini-2.5-pro")
            self.assertEqual(schema.check_row(row), [])

    def test_counts_tokens_and_outcome(self):
        (run,), _ = self.parse(self.write(session(*turn(0, 60, "a"))))
        self.assertEqual((run.tool_calls, run.edit_calls, run.errors), (1, 1, 0))
        # Two responses, each (100 input - 30 cached) + 20 output. Thoughts and tool-prompt
        # tokens stay out, and the response written twice counts once.
        self.assertEqual(run.tokens, 180)
        self.assertEqual(
            (run.outcome, run.model, run.effort), ("completed", "gemini-2.5-pro", None)
        )
        self.assertEqual((run.end - run.start, run.active_s), (60, 60))

    def test_a_reply_without_tools_is_a_completed_run(self):
        lines = session(prompt(0, "u"), touched(0), reply(12, "m"), touched(12))
        (run,), _ = self.parse(self.write(lines))
        self.assertEqual((run.outcome, run.tool_calls, run.active_s), ("completed", 0, 12))

    def test_a_read_is_a_tool_call_but_not_an_edit(self):
        (run,), _ = self.parse(self.write(session(*turn(0, 60, "a", tool="read_file"))))
        self.assertEqual((run.tool_calls, run.edit_calls), (1, 0))

    def test_a_tool_error_is_a_retry_and_the_turn_still_completes(self):
        (run,), _ = self.parse(self.write(session(*turn(0, 60, "a", status="error"))))
        self.assertEqual((run.errors, run.outcome), (1, "completed"))

    def test_odd_tool_statuses_are_not_errors(self):
        (run,), _ = self.parse(self.write(session(*turn(0, 60, "a", status="CANARY_STATUS"))))
        self.assertEqual(run.errors, 0)

    def test_an_interrupted_turn_is_unknown_and_ends_at_its_last_activity(self):
        # The next prompt first repairs the unanswered call, 10 minutes later. Those repairs
        # carry no model, so they neither open a run nor stretch the interrupted one.
        lines = session(
            *interrupted(0, "a"),
            info(40, "i1"),
            *repairs(640, "a", "a-c1"),
            *turn(640, 700, "b"),
        )
        (stopped, done), _ = self.parse(self.write(lines))
        self.assertEqual((stopped.outcome, stopped.active_s), ("unknown", 30))
        self.assertEqual((done.outcome, done.active_s), ("completed", 60))

    def test_a_rolled_back_stream_still_counts_the_work_before_it(self):
        lines = session(*interrupted(0, "a", status="success"), rewind("a-u"), info(31, "i1"))
        (run,), _ = self.parse(self.write(lines))
        self.assertEqual((run.outcome, run.active_s, run.tool_calls), ("unknown", 30, 1))

    def test_a_turn_ending_in_an_error_message_failed(self):
        lines = session(*interrupted(0, "a", status="success"), info(35, "e1", "error"))
        (run,), _ = self.parse(self.write(lines))
        self.assertEqual(run.outcome, "failed")

    def test_an_error_message_after_a_finished_turn_changes_nothing(self):
        lines = session(*turn(0, 60, "a"), info(900, "e1", "error"))
        (run,), _ = self.parse(self.write(lines))
        self.assertEqual((run.outcome, run.active_s), ("completed", 60))

    def test_injected_context_and_compression_open_no_run(self):
        lines = session(
            *turn(0, 60, "a"),
            *compression(700, "c"),
            prompt(700, "ide"),  # IDE context, injected as a user turn just before the prompt
            *turn(701, 760, "b"),
        )
        runs, stats = self.parse(self.write(lines))
        self.assertEqual([run.active_s for run in runs], [60, 59])
        self.assertEqual((stats.empty_runs, stats.unparsed_lines), (0, 0))

    def test_a_prompt_answered_by_no_model_call_is_an_empty_run(self):
        runs, stats = self.parse(self.write(session(prompt(0, "u"), info(1, "i1"))))
        self.assertEqual((runs, stats.empty_runs), ([], 1))

    def test_the_most_frequent_model_names_the_run(self):
        lines = session(
            prompt(0, "u"),
            reply(5, "m1", model="gemini-3-flash-preview", text=""),
            reply(10, "m2", model="gemini-3.1-pro-preview"),
            reply(15, "m3", model="gemini-3.1-pro-preview"),
            reply(16, "m4", model=None),
        )
        (run,), _ = self.parse(self.write(lines))
        self.assertEqual(run.model, "gemini-3.1-pro-preview")
        self.assertEqual(run.tokens, 4 * 90)  # a response without a model still has usage

    def test_upstream_model_ids_keep_their_family(self):
        # config/models.ts:59-79 at the pinned commit.
        cases = {
            "gemini-2.5-pro": "gemini-2.5-pro",
            "gemini-3-pro-preview": "gemini-3-pro",
            "gemini-3.1-pro-preview-customtools": "gemini-3.1-pro",
            "gemini-3.5-flash": "gemini-3.5-flash",
            "gemini-3-flash-preview": "gemini-3-flash",
            "gemini-3.1-flash-lite": "gemini-3.1-flash-lite",
            "gemma-4-31b-it": "other",
        }
        for raw, family in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(ingest.normalize_model(raw), family)

    def test_a_subagent_conversation_is_its_own_batch(self):
        self.write(session(*turn(0, 300, "a")))
        self.write(
            session(*turn(10, 100, "s"), session_id="child", kind="subagent"),
            name="child.jsonl",
            parent="sess-1",
        )
        rows, _ = self.ingest()
        self.assertEqual(len({row["batch"] for row in rows}), 2)
        self.assertEqual({row["parallel_agents"] for row in rows}, {1})

    def test_a_resumed_session_keeps_one_batch(self):
        # Resuming appends to the same file and re-states the same session id
        # (chatRecordingService.ts:777).
        lines = session(
            *turn(0, 60, "a"), {"$set": {"sessionId": "sess-1"}}, *turn(9000, 9030, "b")
        )
        runs, stats = self.parse(self.write(lines))
        self.assertEqual((len(runs), stats.skipped_files), (2, 0))


class Records(Temp):
    def test_duplicate_records_count_once(self):
        lines = turn(0, 60, "a")
        doubled = [lines[0], *lines, *lines[2:], lines[-2]]  # every record written again
        (run,), _ = self.parse(self.write(session(*doubled)))
        self.assertEqual((run.tool_calls, run.tokens, run.active_s), (1, 180, 60))

    def test_malformed_and_unknown_records_are_counted_and_skipped(self):
        lines = session(
            "not json CANARY_GARBAGE",
            "",
            "[1, 2]",
            {"CANARY_GARBAGE": True},
            {"id": "x", "type": "CANARY_TYPE", "timestamp": "2026-11-02T09:00:00.000Z"},
            *turn(0, 60, "a"),
            scratchpad(),
        )
        (run,), stats = self.parse(self.write(lines))
        self.assertEqual(stats.unparsed_lines, 4)  # the blank line is not counted
        self.assertEqual((run.outcome, run.active_s), ("completed", 60))

    def test_the_report_counts_unparsed_lines(self):
        self.write(session("not json CANARY_GARBAGE", *turn(0, 60, "a")))
        rows, err = self.ingest()
        self.assertEqual(len(rows), 1)
        self.assertIn("unparsed lines:   1", err)

    def test_a_file_without_metadata_is_empty(self):
        runs, stats = self.parse(self.write(turn(0, 60, "a")))
        self.assertEqual((runs, stats.empty_runs), ([], 1))

    def test_metadata_naming_two_sessions_is_skipped(self):
        lines = [*session(*turn(0, 60, "a")), metadata(session="other")]
        runs, stats = self.parse(self.write(lines))
        self.assertEqual((runs, stats.skipped_files), ([], 1))

    def test_an_unreadable_file_is_counted(self):
        path = self.write(session(*turn(0, 60, "a")))
        (source,) = list(ADAPTER.discover(path))
        path.unlink()
        stats = ParseStats()
        self.assertEqual((ADAPTER.parse(source, 900, stats), stats.unreadable_files), ([], 1))


class Discovery(Temp):
    def test_finds_main_and_subagent_sessions_only(self):
        self.write(session(*turn(0, 60, "a")))
        self.write(session(*turn(0, 60, "s"), session_id="c"), name="c.jsonl", parent="sess-1")
        chats = self.history / PROJECT_DIR / "chats"
        (chats / "session-old.json").write_text("{}", encoding="utf-8")  # legacy, never read
        (chats / "session-x.jsonl.tmp-123").write_text("{}", encoding="utf-8")
        (self.history / PROJECT_DIR / "logs.json").write_text("[]", encoding="utf-8")
        (self.history / PROJECT_DIR / "other.jsonl").write_text("{}", encoding="utf-8")
        found = sorted(Path(source.path).name for source in ADAPTER.discover(self.history))
        self.assertEqual(found, ["c.jsonl", "session-2026-11-02T09-00-00000001.jsonl"])

    def test_the_default_path_follows_gemini_cli_home(self):
        with mock.patch.dict(os.environ, {"GEMINI_CLI_HOME": str(self.root)}):
            self.assertEqual(ADAPTER.default_path(), self.root / ".gemini" / "tmp")
        without = {key: value for key, value in os.environ.items() if key != "GEMINI_CLI_HOME"}
        with mock.patch.dict(os.environ, without, clear=True):
            self.assertEqual(ADAPTER.default_path(), Path.home() / ".gemini" / "tmp")


class Cache(Temp):
    def cached_ingest(self, version):
        cache = RunCache(self.root / "cache.json", "gemini-cli", version, 900)
        with mock.patch.object(ADAPTER, "parse", wraps=ADAPTER.parse) as parse:
            rows, report = ingest.ingest(ADAPTER, self.history, b"s" * 32, cache=cache)
        return rows, parse.call_count, report

    def test_a_new_adapter_version_discards_the_cache(self):
        self.write(session(*turn(0, 60, "a")))
        self.write(session(*turn(0, 30, "b"), session_id="sess-2"))
        first, calls, _ = self.cached_ingest(GeminiCliAdapter.version)
        self.assertEqual(calls, 2)
        again, calls, report = self.cached_ingest(GeminiCliAdapter.version)
        self.assertEqual((calls, report.cached, again), (0, 2, first))
        _, calls, report = self.cached_ingest(GeminiCliAdapter.version + 1)
        self.assertEqual(calls, 2)
        self.assertTrue(report.cache_rebuilt)


class Privacy(Temp):
    def test_no_canary_leaves_the_adapter(self):
        self.write(
            session(
                "not json CANARY_GARBAGE",
                {"id": "x", "type": "CANARY_TYPE"},
                *turn(0, 60, "a", status="error"),
                *interrupted(100, "b"),
                info(140, "e1", "error"),
                *repairs(800, "b", "b-c1"),
                *compression(800, "c"),
                *turn(801, 860, "d"),
                scratchpad(),
            )
        )
        self.write(
            session(*turn(0, 60, "s"), session_id="child", kind="subagent"),
            name="child.jsonl",
            parent="sess-1",
        )
        rows, ingest_err = self.ingest()
        self.assertEqual(len(rows), 4)
        _, share_out, share_err = self.run_cli(
            "share", "--out-dir", str(self.root / "runs"), "--dry-run"
        )
        stored = paths.local_runs().read_text(encoding="utf-8")
        cache = paths.cache_file("gemini-cli").read_text(encoding="utf-8")
        for canary in CANARIES:
            for text in (stored, ingest_err, share_out, share_err, cache):
                self.assertNotIn(canary, text)
        # The folder name and project hash sit in the local cache by design, nowhere else.
        for text in (stored, ingest_err, share_out, share_err):
            self.assertNotIn(PROJECT_DIR, text)
            self.assertNotIn(PROJECT_HASH, text)
            self.assertNotIn("sess-1", text)

    def test_reprs_hold_no_session_or_folder(self):
        path = self.write(session(*turn(0, 60, "a")))
        (source,) = list(ADAPTER.discover(path))
        (run,), _ = self.parse(path)
        self.assertEqual((run.session, run.project), ("sess-1", PROJECT_HASH))
        for text in (repr(run), repr(source)):
            self.assertNotIn("sess-1", text)
            self.assertNotIn(PROJECT_DIR, text)
            self.assertNotIn(PROJECT_HASH, text)
            self.assertNotIn("CANARY", text)

    def test_share_keeps_other_rows_unchanged_in_a_mixed_store(self):
        from tests.synthetic import row, write_store

        write_store(paths.local_runs(), [row(week="2026-W45"), row(week="2026-W45", offset_s=5)])
        out_dir = str(self.root / "runs")
        _, before, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.write(session(*turn(0, 60, "a")))
        self.ingest()
        _, after, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        claude_lines = [text for text in before.splitlines() if '"claude-code"' in text]
        self.assertEqual(len(claude_lines), 2)
        for text in claude_lines:
            self.assertIn(text, after.splitlines())
        self.assertIn('"gemini-cli"', after)


if __name__ == "__main__":
    unittest.main()

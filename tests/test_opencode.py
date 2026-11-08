"""SYNTHETIC OpenCode databases, built in `tests/fixtures/opencode.py` from the upstream source at
`anomalyco/opencode@810c0eba1a7348036f8bdf61e6632d0c36c6876c` (tag v1.19.5). Nothing here came
from a real session.

Every field that can carry content holds a canary, so the tests can prove no canary leaves the
adapter: not in rows, report lines, errors, `share` output or (apart from the working folder and
the database path, which sit in the local cache as Codex folders and transcript paths do) the
cache.
"""

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from agentpace import cli, paths, schema
from agentpace.adapters import get_adapter
from agentpace.adapters.base import ParseStats
from agentpace.adapters.opencode import OpenCodeAdapter
from tests.fixtures.opencode import (
    CANARIES,
    Database,
    assistant,
    epoch,
    step_finish,
    tool,
    user,
)


class Temp(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.data = self.root / "CANARY_DIR" / "opencode"
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.db = Database(self.data / "opencode.db")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def ingest(self):
        code, _, err = self.run_cli("ingest", str(self.data), "--source", "opencode")
        self.assertEqual(code, 0, err)
        store = paths.local_runs()
        return (schema.read_rows(store) if store.exists() else []), err

    def parse(self):
        stats = ParseStats()
        adapter = get_adapter("opencode")
        (source,) = list(adapter.discover(self.data))
        return adapter.parse(source, 900, stats), stats


class Runs(Temp):
    def test_each_user_message_is_one_run_and_the_session_is_one_batch(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60)
        self.db.turn(sid, 60, 90)
        rows, _ = self.ingest()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["batch"] for row in rows}), 1)
        first, second = sorted(rows, key=lambda row: row["offset_s"])
        self.assertEqual((first["offset_s"], first["duration_s"]), (0, 60))
        self.assertEqual((second["offset_s"], second["duration_s"]), (60, 30))
        for row in rows:
            self.assertEqual(row["harness"], "opencode")
            self.assertEqual(row["model"], "claude-sonnet-4-5")
            self.assertEqual(schema.check_row(row), [])

    def test_counts_tools_edits_tokens_effort_and_outcome(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60, tools=3, edits=2)
        (run,), _ = self.parse()
        self.assertEqual((run.tool_calls, run.edit_calls, run.errors), (3, 2, 0))
        # Two steps of 10 input, 5 output and 2 reasoning. Cache reads and writes never count.
        self.assertEqual(run.tokens, 34)
        self.assertEqual((run.effort, run.outcome), ("high", "completed"))
        self.assertEqual((run.start, run.end, run.active_s), (epoch(0), epoch(60), 60))
        self.assertIsNone(run.agent_type)

    def test_message_tokens_count_when_a_message_has_no_step_finish_part(self):
        sid = self.db.session()
        parent = self.db.message(sid, user(), created=0)
        self.db.message(sid, assistant(parent, 0, 30, tokens=(100, 20, 5)))
        (run,), _ = self.parse()
        self.assertEqual(run.tokens, 125)

    def test_step_finish_parts_are_summed_and_the_message_total_is_not_added(self):
        sid = self.db.session()
        parent = self.db.message(sid, user(), created=0)
        mid = self.db.message(sid, assistant(parent, 0, 30, tokens=(7, 7, 7)))
        self.db.part(sid, mid, step_finish((100, 10, 0)), at=10)
        self.db.part(sid, mid, step_finish((200, 20, 0)), at=30)
        (run,), _ = self.parse()
        self.assertEqual(run.tokens, 330)

    def test_an_interrupted_run_is_unknown_and_its_cut_tools_are_not_retries(self):
        sid = self.db.session()
        parent = self.db.message(sid, user(), created=0)
        mid = self.db.message(
            sid, assistant(parent, 0, 40, finish=None, error="MessageAbortedError")
        )
        self.db.part(sid, mid, tool(5, 40, status="error", interrupted=True), at=5)
        (run,), _ = self.parse()
        self.assertEqual((run.outcome, run.errors, run.tool_calls), ("unknown", 0, 1))
        self.assertEqual(run.active_s, 40)

    def test_any_other_error_failed(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60, finish="error", error="APIError")
        (run,), _ = self.parse()
        self.assertEqual(run.outcome, "failed")

    def test_a_tool_error_is_a_retry_and_the_run_can_still_complete(self):
        sid = self.db.session()
        parent = self.db.turn(sid, 0, 60)
        mid = self.db.message(sid, assistant(parent, 60, 90))
        self.db.part(sid, mid, tool(61, 62, name="CANARY_TOOL_NAME", status="error"), at=61)
        (run,), _ = self.parse()
        self.assertEqual((run.errors, run.tool_calls, run.outcome), (1, 2, "completed"))

    def test_a_run_without_an_end_or_with_another_finish_is_unknown(self):
        for index, (completed, finish) in enumerate(((None, None), (30, "length"))):
            with self.subTest(finish=finish):
                sid = self.db.session(f"ses_{index}")
                parent = self.db.message(sid, user(), created=0)
                mid = self.db.message(sid, assistant(parent, 0, completed, finish=finish))
                self.db.part(sid, mid, tool(1, 20), at=1)
        runs, _ = self.parse()
        self.assertEqual([run.outcome for run in runs], ["unknown", "unknown"])

    def test_multi_turn_sessions_keep_each_turns_outcome_in_order(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60)
        self.db.turn(sid, 100, 150, finish=None, error="MessageAbortedError")
        self.db.turn(sid, 200, 230)
        runs, _ = self.parse()
        self.assertEqual([run.outcome for run in runs], ["completed", "unknown", "completed"])
        self.assertEqual([run.active_s for run in runs], [60, 50, 30])

    def test_an_unknown_variant_is_dropped(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60, variant="CANARY_VARIANT")
        (run,), _ = self.parse()
        self.assertIsNone(run.effort)

    def test_model_ids_normalize_to_a_family_or_other(self):
        cases = {
            "claude-sonnet-4-5": "claude-sonnet-4-5",
            "claude-sonnet-4.5": "claude-sonnet-4-5",
            "anthropic/claude-opus-4.1": "claude-opus-4-1",
            "claude-3.5-sonnet": "claude-3-5-sonnet",
            "openai/gpt-5.1-codex": "gpt-5.1-codex",
            "gemini-2.5-pro": "gemini-2.5-pro",
            "qwen3-coder:30b": "other",
        }
        for index, model in enumerate(cases):
            sid = self.db.session(f"ses_{index}")
            self.db.turn(sid, index * 100, index * 100 + 30, model=model)
        rows, _ = self.ingest()
        self.assertEqual(sorted(row["model"] for row in rows), sorted(cases.values()))

    def test_task_subagents_are_their_own_batch_and_carry_their_agent(self):
        parent = self.db.session("ses_parent")
        self.db.turn(parent, 0, 300)
        child = self.db.session("ses_child", parent=parent, created=5)
        self.db.turn(child, 10, 100, agent="general")
        runs, _ = self.parse()
        self.assertEqual(sorted(str(run.agent_type) for run in runs), ["None", "general"])
        rows, _ = self.ingest()
        self.assertEqual(len({row["batch"] for row in rows}), 2)
        self.assertEqual({row["parallel_agents"] for row in rows}, {1})


class Skipped(Temp):
    def test_a_forked_copy_is_skipped_and_counted_and_new_turns_in_the_fork_count(self):
        original = self.db.session("ses_1")
        self.db.turn(original, 0, 60)
        fork = self.db.session("ses_2", created=1000)
        copied = self.db.message(fork, user(), created=0)
        self.db.message(fork, assistant(copied, 0, 60))
        self.db.turn(fork, 1100, 1130)
        runs, stats = self.parse()
        self.assertEqual(
            [(run.session, run.active_s) for run in runs], [("ses_1", 60), ("ses_2", 30)]
        )
        self.assertEqual((stats.skipped_runs, stats.empty_runs), (1, 0))

    def test_compaction_summaries_are_skipped_and_counted(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60)
        marker = self.db.message(sid, user(), created=70)
        self.db.message(sid, assistant(marker, 70, 80, agent="compaction", summary=True))
        runs, stats = self.parse()
        self.assertEqual((len(runs), stats.skipped_runs), (1, 1))

    def test_the_report_names_skipped_runs(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60)
        marker = self.db.message(sid, user(), created=70)
        self.db.message(sid, assistant(marker, 70, 80, agent="compaction", summary=True))
        _, err = self.ingest()
        self.assertIn("skipped runs:     1", err)

    def test_ingesting_twice_adds_no_duplicate_rows(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60)
        self.db.turn(sid, 100, 160)
        first, _ = self.ingest()
        second, err = self.ingest()
        self.assertEqual(len(first), 2)
        self.assertEqual(second, first)
        self.assertIn("read from cache:  1", err)

    def test_malformed_and_unknown_records_are_counted_and_skipped(self):
        sid = self.db.session()
        parent = self.db.turn(sid, 0, 60)
        self.db.message(sid, "not json CANARY_MALFORMED", created=70)
        self.db.message(sid, '["CANARY_MALFORMED"]', created=71)
        self.db.message(sid, {"role": "CANARY_UNKNOWN", "time": {"created": 72}})
        orphan = assistant(parent, 73, 74)
        del orphan["parentID"]
        self.db.message(sid, orphan)
        mid = self.db.message(sid, assistant(parent, 60, 90))
        self.db.part(sid, mid, '{"type": "tool", CANARY_MALFORMED', at=61)
        self.db.part(sid, mid, {"type": "CANARY_UNKNOWN", "tool": "x"}, at=62)
        odd = tool(63, 64)
        odd["state"]["status"] = "CANARY_UNKNOWN"
        self.db.part(sid, mid, odd, at=63)
        (run,), stats = self.parse()
        self.assertEqual(stats.unparsed_lines, 7)
        self.assertEqual((run.tool_calls, run.outcome), (1, "completed"))
        _, err = self.ingest()
        self.assertIn("unparsed lines:   7", err)
        self.assertNotIn("CANARY", err)

    def test_a_file_that_is_not_a_database_is_unreadable_not_fatal(self):
        self.db.path.unlink()
        self.db.path.write_bytes(b"CANARY_MALFORMED not a database " * 200)
        runs, stats = self.parse()
        self.assertEqual((runs, stats.unreadable_files), ([], 1))
        _, err = self.ingest()
        self.assertIn("unreadable files: 1", err)
        self.assertNotIn("CANARY", err)

    def test_a_database_without_session_tables_is_unreadable(self):
        self.db.path.unlink()
        with closing(sqlite3.connect(self.db.path)) as connection:
            connection.execute("CREATE TABLE unrelated (x text)")
        runs, stats = self.parse()
        self.assertEqual((runs, stats.unreadable_files), ([], 1))


class Discovery(Temp):
    def test_finds_the_stable_and_channel_databases_only(self):
        for name in ("opencode-beta.db", "opencode.db-wal", "opencode.db-shm", "other.db"):
            (self.data / name).write_bytes(b"")
        (self.data / "storage").mkdir()
        (self.data / "storage" / "opencode.db").write_bytes(b"")
        sources = list(OpenCodeAdapter().discover(self.data))
        self.assertEqual(
            sorted(os.path.basename(source.path) for source in sources),
            ["opencode-beta.db", "opencode.db"],
        )
        main = next(s for s in sources if s.path.endswith(os.sep + "opencode.db"))
        self.assertEqual(main.companions, (main.path + "-wal",))

    def test_a_database_file_can_be_given_directly(self):
        (source,) = list(OpenCodeAdapter().discover(self.db.path))
        self.assertEqual(source.path, os.path.abspath(self.db.path))

    def test_the_default_path_follows_xdg_data_home(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "xdg")}):
            self.assertEqual(OpenCodeAdapter().default_path(), self.root / "xdg" / "opencode")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": ""}):
            expected = Path.home() / ".local" / "share" / "opencode"
            self.assertEqual(OpenCodeAdapter().default_path(), expected)


class Cache(Temp):
    def test_a_version_bump_discards_the_cache(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60)
        self.ingest()
        _, warm = self.ingest()
        self.assertIn("read from cache:  1", warm)
        with mock.patch.object(OpenCodeAdapter, "version", 2):
            rows, bumped = self.ingest()
        self.assertIn("read from cache:  0 (cache rebuilt)", bumped)
        self.assertEqual(len(rows), 1)

    def test_a_live_wal_database_is_read_with_rows_still_in_the_log(self):
        # OpenCode runs in WAL mode (`packages/core/src/database/database.ts:27`).
        with closing(sqlite3.connect(self.db.path)) as writer:
            writer.execute("PRAGMA journal_mode = WAL")
            writer.execute("PRAGMA wal_autocheckpoint = 0")
            # A write keeps this connection on the log, so it outlives the fixture's connections.
            writer.execute("CREATE TABLE keepalive (x text)")
            sid = self.db.session()
            self.db.turn(sid, 0, 60)
            self.assertTrue(Path(str(self.db.path) + "-wal").exists())
            (run,), stats = self.parse()
            self.assertEqual((run.active_s, stats.unreadable_files), (60, 0))

    def test_a_changed_database_is_read_again(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60)
        first, _ = self.ingest()
        self.db.turn(sid, 100, 130)
        second, err = self.ingest()
        self.assertEqual((len(first), len(second)), (1, 2))
        self.assertIn("read from cache:  0", err)


class Privacy(Temp):
    def plant(self):
        top = self.db.session("ses_top")
        self.db.turn(top, 0, 60, tools=2, edits=1)
        self.db.turn(top, 100, 160, finish="CANARY_FINISH", variant="CANARY_VARIANT")
        self.db.turn(top, 200, 260, finish=None, error="CANARY_UNKNOWN")
        parent = self.db.turn(top, 300, 330)
        mid = self.db.message(top, assistant(parent, 330, 360))
        self.db.part(top, mid, tool(331, 332, name="CANARY_TOOL_NAME", status="error"), at=331)
        self.db.part(top, mid, {"type": "patch", "hash": "h", "files": ["/CANARY_PATCH"]})
        self.db.message(top, "not json CANARY_MALFORMED", created=400)
        child = self.db.session("ses_child", parent=top, created=1)
        self.db.turn(child, 5, 50, agent="general")
        # A model outside every family is normalized to `other` before the cache is written.
        self.db.turn(self.db.session("ses_model"), 500, 530, model="CANARY_MODEL")

    def test_no_canary_leaves_the_adapter(self):
        self.plant()
        _, ingest_err = self.ingest()
        _, share_out, share_err = self.run_cli(
            "share", "--out-dir", str(self.root / "runs"), "--dry-run"
        )
        stored = paths.local_runs().read_text(encoding="utf-8")
        cache = paths.cache_file("opencode").read_text(encoding="utf-8")
        self.assertIn('"opencode"', stored)
        for canary in (*CANARIES, "CANARY_MODEL"):
            for text in (stored, ingest_err, share_out, share_err, cache):
                self.assertNotIn(canary, text)
        for text in (stored, ingest_err, share_out, share_err):
            self.assertNotIn("CANARY_CWD", text)
            self.assertNotIn("CANARY_DIR", text)

    def test_the_cache_holds_only_the_subagents_agent_name(self):
        self.plant()
        self.ingest()
        cache = json.loads(paths.cache_file("opencode").read_text(encoding="utf-8"))
        agents = {run["agent_type"] for entry in cache["entries"].values() for run in entry["runs"]}
        self.assertEqual(agents, {None, "general"})

    def test_odd_value_types_raise_nothing(self):
        sid = self.db.session()
        parent = self.db.message(sid, user(), created=0)
        odd = assistant(parent, 0, 30)
        odd["tokens"] = {"input": "CANARY_UNKNOWN", "output": None, "reasoning": float("inf")}
        odd["time"]["completed"] = "CANARY_UNKNOWN"
        odd["error"] = "CANARY_UNKNOWN"
        mid = self.db.message(sid, odd)
        self.db.part(sid, mid, {"type": "tool", "tool": ["CANARY_UNKNOWN"], "state": "x"})
        self.db.part(sid, mid, {"type": "step-finish", "tokens": "CANARY_UNKNOWN"}, at=20)
        runs, stats = self.parse()
        self.assertEqual(stats.unparsed_lines, 1)
        self.assertEqual([run.outcome for run in runs], ["failed"])
        self.assertNotIn("CANARY", repr(runs))

    def test_reprs_hold_no_session_or_folder(self):
        sid = self.db.session()
        self.db.turn(sid, 0, 60)
        (source,) = list(OpenCodeAdapter().discover(self.data))
        (run,), _ = self.parse()
        for text in (repr(run), repr(source)):
            self.assertNotIn("CANARY_CWD", text)
            self.assertNotIn("CANARY_DIR", text)
            self.assertNotIn("ses_1", text)


if __name__ == "__main__":
    unittest.main()

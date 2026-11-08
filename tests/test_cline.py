"""The Cline adapter, on SYNTHETIC task folders built from the upstream source.

See `tests/fixtures/cline_tasks.py` for the pinned commits. Every field that can carry content
holds a canary, so the tests can prove no canary leaves the adapter: not in rows, report lines,
errors, `share` output or (apart from the task folder name and the starting folder, which sit in
the local cache as Codex working folders do) the cache.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, ingest, paths, schema, validate
from agentpace.adapters import cline, get_adapter
from agentpace.adapters.base import ParseStats
from agentpace.cache import RunCache
from tests.fixtures.cline_tasks import (
    CANARIES,
    FOLDER,
    TASK_ID,
    ask,
    completion,
    epoch,
    feedback,
    history_item,
    metadata,
    ms,
    request,
    say,
    task,
    turn,
    write_index,
    write_task,
)

ADAPTER = get_adapter("cline")
SECRET = b"s" * 32


class Temp(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.storage = self.root / "globalStorage" / "saoudrizwan.claude-dev"
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.tasks = 0

    def write(self, messages, task_id=None, meta=None):
        self.tasks += 1
        return write_task(self.storage, task_id or f"task-{self.tasks}", messages, meta)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def ingest(self):
        code, _, err = self.run_cli("ingest", str(self.storage), "--source", "cline")
        self.assertEqual(code, 0, err)
        store = paths.local_runs()
        return (schema.read_rows(store) if store.exists() else []), err

    def parse(self, path):
        stats = ParseStats()
        (source,) = list(ADAPTER.discover(path))
        return ADAPTER.parse(source, 900, stats), stats


class Runs(Temp):
    def test_each_turn_is_one_run_and_the_task_is_one_batch(self):
        self.write([*turn(0), *turn(200, "feedback")])
        rows, _ = self.ingest()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["batch"] for row in rows}), 1)
        first, second = sorted(rows, key=lambda row: row["offset_s"])
        self.assertEqual((first["offset_s"], first["duration_s"]), (0, 21))
        self.assertEqual(second["duration_s"], 21)
        for row in rows:
            self.assertEqual((row["harness"], row["model"]), ("cline", "claude-sonnet-4-5"))
            self.assertEqual(
                (row["outcome"], row["tool_calls"], row["tokens"]), ("completed", 2, 360)
            )
            self.assertEqual(schema.check_row(row), [])
            self.assertEqual(validate.check_values(row), [])
            self.assertNotIn("effort", row)

    def test_counts_tools_edits_tokens_and_outcome(self):
        (run,), _ = self.parse(self.write(turn(0)))
        self.assertEqual((run.tool_calls, run.edit_calls, run.errors), (2, 1, 0))
        self.assertEqual(
            (run.tokens, run.outcome, run.model), (360, "completed", "claude-sonnet-4-5-20250929")
        )
        self.assertEqual((run.start, run.end), (epoch(0), epoch(81)))
        self.assertIsNone(run.effort)
        self.assertIsNone(run.agent_type)

    def test_an_approval_wait_is_dropped_whole_and_other_gaps_are_capped(self):
        messages = [
            task(0),
            request(1),
            ask(10, "command", "CANARY_COMMAND"),  # waits on the human for 3,000 s
            say(3010, "command_output", "CANARY_COMMAND_OUTPUT"),
            say(5010, "text"),  # 2,000 s of silence, capped at 900
            *completion(5020),
        ]
        (run,), _ = self.parse(self.write(messages))
        self.assertEqual(run.active_s, 10 + 900 + 11)
        self.assertEqual(run.spans[0], (epoch(0), epoch(10)))
        self.assertEqual(run.tool_calls, 1)

    def test_a_running_command_is_not_a_wait(self):
        messages = [
            task(0),
            say(1, "command", "CANARY_COMMAND"),
            ask(2, "command_output", "CANARY_COMMAND_OUTPUT"),
            say(32, "text"),
            *completion(40),
        ]
        (run,), _ = self.parse(self.write(messages))
        self.assertEqual(run.active_s, 41)

    def test_a_followup_question_closes_a_turn_as_completed(self):
        messages = [task(0), request(1), ask(5, "followup"), feedback(300), *completion(310)]
        first, second = self.parse(self.write(messages))[0]
        self.assertEqual((first.outcome, first.active_s), ("completed", 5))
        self.assertEqual((second.outcome, second.active_s), ("completed", 11))

    def test_an_interrupted_turn_ends_before_the_resume_and_is_unknown(self):
        messages = [
            *turn(0),
            feedback(200),
            request(201, cancel="user_cancelled"),
            say(210, "text"),
            ask(90000, "resume_task", ""),
            feedback(90010),
            *completion(90020),
        ]
        runs, _ = self.parse(self.write(messages))
        self.assertEqual([run.outcome for run in runs], ["completed", "unknown", "completed"])
        interrupted = runs[1]
        self.assertEqual((interrupted.end, interrupted.active_s), (epoch(210), 10))

    def test_a_failed_request_left_open_failed(self):
        messages = [
            task(0),
            request(1, cancel="streaming_failed"),
            ask(5, "api_req_failed", "CANARY_ERROR"),
        ]
        (run,), _ = self.parse(self.write(messages))
        self.assertEqual((run.outcome, run.errors), ("failed", 1))

    def test_a_retry_that_succeeds_clears_the_failure(self):
        messages = [
            task(0),
            request(1, cancel="streaming_failed"),
            ask(5, "api_req_failed", "CANARY_ERROR"),
            request(20),
            say(25, "text"),
        ]
        (run,), _ = self.parse(self.write(messages))
        self.assertEqual((run.outcome, run.errors), ("unknown", 1))
        self.assertEqual(run.active_s, 5 + 5)

    def test_an_odd_cancel_reason_is_not_copied(self):
        messages = [task(0), request(1, cancel="CANARY_CANCEL"), ask(5, "api_req_failed")]
        (run,), _ = self.parse(self.write(messages))
        self.assertEqual(run.outcome, "failed")  # from the failed-request ask alone
        self.assertNotIn("CANARY", repr(run))

    def test_a_tool_error_counts_without_failing_the_turn(self):
        messages = turn(0)
        messages.insert(4, say(5, "error", "CANARY_ERROR"))
        messages.insert(5, say(5, "diff_error", "/CANARY_PATH/x.py"))
        (run,), _ = self.parse(self.write(messages))
        self.assertEqual((run.errors, run.outcome), (2, "completed"))

    def test_reopening_a_completed_task_opens_nothing(self):
        messages = [*turn(0), ask(5000, "resume_completed_task", "")]
        runs, stats = self.parse(self.write(messages))
        self.assertEqual(len(runs), 1)
        self.assertEqual(stats.empty_runs, 0)

    def test_repeated_messages_count_once(self):
        messages = turn(0)
        messages[4:4] = [messages[3], messages[3]]  # the auto-approved tool, twice more
        messages.insert(2, messages[1])  # a request, again
        (run,), _ = self.parse(self.write(messages))
        self.assertEqual((run.tool_calls, run.tokens, run.active_s), (2, 360, 21))

    def test_the_model_falls_back_to_task_metadata(self):
        meta = metadata((-100, "gpt-6.1-sol"), (100, "gemini-3-pro-preview"))
        (run,), _ = self.parse(self.write(turn(0, model=None), meta=meta))
        self.assertEqual(run.model, "gpt-6.1-sol")

    def test_a_turn_with_no_model_is_empty(self):
        runs, stats = self.parse(self.write(turn(0, model=None)))
        self.assertEqual((runs, stats.empty_runs), ([], 1))

    def test_local_ids_become_other_and_dotted_claude_ids_normalize(self):
        self.write(turn(0, model="qwen3-coder:30b"))
        self.write(turn(0, model="anthropic/claude-sonnet-4.5"))
        rows, _ = self.ingest()
        self.assertEqual({row["model"] for row in rows}, {"other", "claude-sonnet-4-5"})
        for row in rows:
            self.assertEqual(validate.check_values(row), [])
        self.assertEqual(ingest.normalize_model("claude-sonnet-5-5.internal"), "claude-sonnet-5-5")

    def test_the_starting_folder_names_the_project(self):
        write_index(self.storage, [history_item("a"), history_item("b"), history_item("c", "/x")])
        for name in ("a", "b", "c"):
            self.write(turn(0), task_id=name)
        rows, _ = self.ingest()
        self.assertEqual(len({row["project"] for row in rows}), 2)
        self.assertEqual(len({row["batch"] for row in rows}), 3)


class Discovery(Temp):
    def test_only_task_folders_are_read(self):
        self.write(turn(0))
        stray = self.storage / "checkpoints" / "x"
        stray.mkdir(parents=True)
        (stray / "ui_messages.json").write_text(json.dumps(turn(0)), encoding="utf-8")
        self.assertEqual(len(list(ADAPTER.discover(self.storage))), 1)

    def test_one_file_can_be_named(self):
        path = self.write(turn(0))
        self.write(turn(0))
        (source,) = list(ADAPTER.discover(path))
        self.assertTrue(source.path.endswith("ui_messages.json"))

    def test_a_task_missing_from_the_index_gets_no_folder(self):
        write_index(self.storage, [history_item("other")])
        self.write(turn(0))
        (source,) = list(ADAPTER.discover(self.storage))
        self.assertEqual(source.project, "")

    def test_the_default_path_is_vs_code_global_storage(self):
        tail = Path("Code", "User", "globalStorage", "saoudrizwan.claude-dev")
        cases = (("win32", "APPDATA"), ("linux", "XDG_CONFIG_HOME"))
        for platform, variable in cases:
            with (
                self.subTest(platform=platform),
                mock.patch.object(cline.sys, "platform", platform),
                mock.patch.dict(os.environ, {variable: str(self.root / "base")}),
            ):
                self.assertEqual(ADAPTER.default_path(), self.root / "base" / tail)


class Skipped(Temp):
    def test_malformed_and_unknown_records_are_skipped_and_counted(self):
        messages = [
            "not a message CANARY_BROKEN",
            {"ts": "CANARY_BROKEN", "type": "say", "say": "text"},
            {"ts": True, "type": "say", "say": "text"},
            {"type": "say", "say": "text", "text": "CANARY_BROKEN"},
            {"ts": ms(3), "type": "CANARY_TYPE", "text": "CANARY_BROKEN"},
            {"ts": ms(3), "type": "say", "say": "CANARY_SAY", "text": "CANARY_BROKEN"},
            *turn(0),
        ]
        (run,), stats = self.parse(self.write(messages))
        self.assertEqual(stats.unparsed_lines, 6)
        self.assertEqual((run.active_s, run.tool_calls), (21, 2))

    def test_a_file_that_is_not_json_is_counted(self):
        path = self.write([])
        path.write_text('[{"ts": 1, "say": "CANARY_BROKEN"', encoding="utf-8")
        runs, stats = self.parse(path)
        self.assertEqual((runs, stats.unparsed_lines), ([], 1))

    def test_a_file_that_is_not_a_list_is_counted(self):
        path = self.write([])
        path.write_text(json.dumps({"messages": turn(0)}), encoding="utf-8")
        runs, stats = self.parse(path)
        self.assertEqual((runs, stats.unparsed_lines), ([], 1))

    def test_an_unreadable_file_is_counted(self):
        folder = self.storage / "tasks" / "t" / "ui_messages.json"
        folder.mkdir(parents=True)
        source = cline.Source(path=str(folder), session="t", project="")
        stats = ParseStats()
        self.assertEqual(ADAPTER.parse(source, 900, stats), [])
        self.assertEqual(stats.unreadable_files, 1)

    def test_hostile_text_never_raises(self):
        deep = "[" * 100000 + "]" * 100000
        messages = [
            task(0),
            say(1, "api_req_started", "{CANARY_BROKEN"),
            say(2, "api_req_started", deep),
            say(3, "tool", '{"tool": "CANARY_TOOL_NAME"}'),
            say(4, "tool", None),
            *completion(5),
        ]
        (run,), _ = self.parse(self.write(messages))
        self.assertEqual((run.tokens, run.edit_calls, run.tool_calls), (None, 0, 2))

    def test_the_report_names_unparsed_records(self):
        self.write(["CANARY_BROKEN", *turn(0)])
        rows, err = self.ingest()
        self.assertEqual(len(rows), 1)
        self.assertIn("unparsed lines:   1", err)


class Cache(Temp):
    def setUp(self):
        super().setUp()
        for index in range(3):
            self.write(turn(index * 1000))
        self.cache_path = self.root / "home" / "cache-cline.json"

    def ingest_cached(self, version=1):
        cache = RunCache(self.cache_path, "cline", version, 900)
        with mock.patch.object(ADAPTER, "parse", wraps=ADAPTER.parse) as parse:
            rows, report = ingest.ingest(ADAPTER, self.storage, SECRET, cache=cache)
        return rows, parse.call_count, report

    def test_a_second_ingest_reads_the_cache(self):
        first, calls, _ = self.ingest_cached()
        self.assertEqual(calls, 3)
        second, calls, report = self.ingest_cached()
        self.assertEqual((calls, report.cached, second), (0, 3, first))

    def test_a_new_adapter_version_discards_the_cache(self):
        self.ingest_cached()
        _, calls, report = self.ingest_cached(version=2)
        self.assertEqual(calls, 3)
        self.assertTrue(report.cache_rebuilt)

    def test_a_changed_metadata_file_is_parsed_again(self):
        self.ingest_cached()
        meta = self.storage / "tasks" / "task-2" / "task_metadata.json"
        meta.write_text(json.dumps(metadata((0, "gpt-6.1-sol"))), encoding="utf-8")
        _, calls, _ = self.ingest_cached()
        self.assertEqual(calls, 1)


class Privacy(Temp):
    def test_no_canary_leaves_the_adapter(self):
        write_index(self.storage, [history_item(TASK_ID)])
        meta = metadata((0, "CANARY_PROVIDER-model"))
        messages = [
            *turn(0),
            feedback(200),
            request(201, cancel="CANARY_CANCEL"),
            ask(205, "use_mcp_server", json.dumps({"arguments": "CANARY_MCP_ARGS"})),
            say(230, "mcp_server_response", "CANARY_MCP_RESPONSE"),
            say(231, "error", "CANARY_ERROR"),
            say(232, "tool", '{"tool": "CANARY_TOOL_NAME", "path": "/CANARY_PATH"}'),
            ask(233, "followup", "CANARY_QUESTION"),
            {"ts": ms(234), "type": "CANARY_TYPE", "text": "CANARY_BROKEN"},
            {"ts": ms(235), "type": "say", "say": "CANARY_SAY"},
            *turn(300, "feedback"),
        ]
        self.write(messages, task_id=TASK_ID, meta=meta)
        self.write(turn(0, model=None), meta=meta)
        broken = self.write([])
        broken.write_text('[{"ts": 1, "text": "CANARY_BROKEN /CANARY_PATH"', encoding="utf-8")
        rows, ingest_err = self.ingest()
        self.assertEqual(len(rows), 4)
        _, share_out, share_err = self.run_cli(
            "share", "--out-dir", str(self.root / "runs"), "--dry-run"
        )
        stored = paths.local_runs().read_text(encoding="utf-8")
        cache = paths.cache_file("cline").read_text(encoding="utf-8")
        for canary in CANARIES:
            for text in (stored, ingest_err, share_out, share_err, cache):
                self.assertNotIn(canary, text)
        for text in (stored, ingest_err, share_out, share_err):
            self.assertNotIn("CANARY_CWD", text)
            self.assertNotIn("CANARY_TASKID", text)
            self.assertNotIn(FOLDER, text)

    def test_reprs_hold_no_session_or_folder(self):
        write_index(self.storage, [history_item(TASK_ID)])
        self.write(turn(0), task_id=TASK_ID)
        (source,) = list(ADAPTER.discover(self.storage))
        (run,) = ADAPTER.parse(source, 900, ParseStats())
        self.assertEqual((run.session, run.project), (TASK_ID, FOLDER))
        for text in (repr(run), repr(source)):
            self.assertNotIn("CANARY", text)

    def test_projection_copies_only_allowed_values(self):
        message = cline._project(request(1, cancel="CANARY_CANCEL"))
        self.assertIsNotNone(message)
        self.assertNotIn("CANARY", repr(message))
        self.assertEqual(message.tokens, 120)


if __name__ == "__main__":
    unittest.main()

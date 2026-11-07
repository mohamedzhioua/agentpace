"""SYNTHETIC Codex rollouts. Nothing here came from a real session.

Every field that can carry content holds a canary, so the tests can prove no canary leaves the
adapter: not in rows, report lines, errors, `share` output or (apart from the working folder,
which sits in the local cache as Claude Code folder names do) the cache.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agentpace import cli, paths, schema
from agentpace.adapters import get_adapter
from agentpace.adapters.base import ParseStats

BASE = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)
CANARIES = (
    "CANARY_GIT",
    "CANARY_INSTRUCTIONS",
    "CANARY_ROLE",
    "CANARY_AGENT_PATH",
    "CANARY_NICKNAME",
    "CANARY_LAST_MESSAGE",
    "CANARY_ERROR",
    "CANARY_MESSAGE",
    "CANARY_REASONING",
    "CANARY_USER",
    "CANARY_ARGUMENTS",
    "CANARY_INPUT",
    "CANARY_OUTPUT",
    "CANARY_COMMAND",
    "CANARY_PATCH",
    "CANARY_SANDBOX",
    "CANARY_EFFORT",
    "CANARY_SOURCE",
    "CANARY_STATUS",
)
FOLDER = "/work/CANARY_CWD"


def at(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def epoch(seconds):
    return (BASE + timedelta(seconds=seconds)).timestamp()


def line(seconds, kind, payload):
    return {"timestamp": at(seconds), "type": kind, "payload": payload}


def meta(seconds=0, session="sess-1", source="exec", forked=None):
    return line(
        seconds,
        "session_meta",
        {
            "id": session,
            "cwd": FOLDER,
            "git": {"branch": "CANARY_GIT"},
            "base_instructions": "CANARY_INSTRUCTIONS",
            "agent_role": "CANARY_ROLE",
            "agent_path": "CANARY_AGENT_PATH",
            "agent_nickname": "CANARY_NICKNAME",
            "source": source,
            "forked_from_id": forked,
        },
    )


def turn(start, end, turn_id, *, model="gpt-6.1-sol", effort="high", error=False, tokens=(10, 5)):
    """One complete turn: context, a tool call, a file change and a token record."""
    complete = {
        "type": "task_complete",
        "turn_id": turn_id,
        "started_at": epoch(start),
        "completed_at": epoch(end),
        "duration_ms": 1,
        "last_agent_message": "CANARY_LAST_MESSAGE",
        "error": {"message": "CANARY_ERROR"} if error else None,
    }
    return [
        line(
            start,
            "event_msg",
            {"type": "task_started", "turn_id": turn_id, "started_at": epoch(start)},
        ),
        line(
            start,
            "turn_context",
            {
                "turn_id": turn_id,
                "model": model,
                "effort": effort,
                "cwd": FOLDER,
                "sandbox_policy": {"writable_roots": ["/CANARY_SANDBOX"]},
            },
        ),
        line(start + 1, "response_item", {"type": "message", "content": "CANARY_USER"}),
        line(start + 2, "response_item", {"type": "reasoning", "summary": "CANARY_REASONING"}),
        line(
            start + 3,
            "response_item",
            {"type": "custom_tool_call", "name": "exec", "input": "CANARY_INPUT", "call_id": "c1"},
        ),
        line(
            start + 4,
            "response_item",
            {"type": "custom_tool_call_output", "call_id": "c1", "output": "CANARY_OUTPUT"},
        ),
        line(
            start + 5,
            "event_msg",
            {
                "type": "item_completed",
                "item": {
                    "type": "CommandExecution",
                    "command": "CANARY_COMMAND",
                    "cwd": FOLDER,
                    "status": "completed",
                },
            },
        ),
        line(
            start + 6,
            "event_msg",
            {
                "type": "item_completed",
                "item": {"type": "FileChange", "changes": "CANARY_PATCH", "status": "completed"},
            },
        ),
        line(
            start + 7,
            "token_usage_record",
            {
                "turn_id": turn_id,
                "turn_token_usage": {
                    "input_tokens": tokens[0],
                    "output_tokens": tokens[1],
                    "cached_input_tokens": 999,
                },
            },
        ),
        line(end, "event_msg", complete),
    ]


class Temp(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.history = self.root / "sessions"
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.files = 0

    def write(self, lines, name=None):
        self.files += 1
        folder = self.history / "2026" / "11" / "02"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name or f"rollout-2026-11-02-{self.files}.jsonl")
        path.write_text("\n".join(json.dumps(item) for item in lines) + "\n", encoding="utf-8")
        return path

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def ingest(self):
        code, _, err = self.run_cli("ingest", str(self.history), "--source", "codex")
        self.assertEqual(code, 0, err)
        store = paths.local_runs()
        return (schema.read_rows(store) if store.exists() else []), err

    def parse(self, path):
        stats = ParseStats()
        adapter = get_adapter("codex")
        (source,) = list(adapter.discover(path))
        return adapter.parse(source, 900, stats), stats


class Runs(Temp):
    def test_each_turn_is_one_run_and_the_file_is_one_batch(self):
        # The second turn starts as the first ends, so its offset is the first's active time.
        self.write([meta(), *turn(0, 60, "t1"), *turn(60, 90, "t2")])
        rows, _ = self.ingest()
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["batch"] for row in rows}), 1)
        first, second = sorted(rows, key=lambda row: row["offset_s"])
        self.assertEqual((first["offset_s"], first["duration_s"]), (0, 60))
        self.assertEqual(second["offset_s"], 60)
        self.assertEqual(second["duration_s"], 30)
        for row in rows:
            self.assertEqual(row["harness"], "codex")
            self.assertEqual(schema.check_row(row), [])

    def test_counts_tokens_effort_and_outcome(self):
        (run,), _ = self.parse(self.write([meta(), *turn(0, 60, "t1", tokens=(100, 20))]))
        self.assertEqual((run.tool_calls, run.edit_calls, run.errors), (1, 1, 0))
        self.assertEqual(run.tokens, 120)
        self.assertEqual((run.effort, run.outcome, run.model), ("high", "completed", "gpt-6.1-sol"))
        self.assertIsNone(run.agent_type)

    def test_tokens_take_the_largest_cumulative_record_of_the_turn(self):
        lines = turn(0, 60, "t1", tokens=(10, 5))
        lines.insert(
            -1,
            line(
                50,
                "token_usage_record",
                {"turn_id": "t1", "turn_token_usage": {"input_tokens": 300, "output_tokens": 30}},
            ),
        )
        (run,), _ = self.parse(self.write([meta(), *lines]))
        self.assertEqual(run.tokens, 330)

    def test_a_turn_with_an_error_failed(self):
        (run,), _ = self.parse(self.write([meta(), *turn(0, 60, "t1", error=True)]))
        self.assertEqual(run.outcome, "failed")

    def test_failed_items_count_as_errors_and_odd_statuses_do_not(self):
        lines = turn(0, 60, "t1")
        for status in ("failed", "CANARY_STATUS"):
            item = {
                "type": "item_completed",
                "item": {"type": "CommandExecution", "status": status},
            }
            lines.insert(-1, line(30, "event_msg", item))
        (run,), _ = self.parse(self.write([meta(), *lines]))
        self.assertEqual(run.errors, 1)
        self.assertEqual(run.outcome, "completed")

    def test_an_aborted_turn_is_unknown(self):
        lines = turn(0, 60, "t1")[:-1]
        lines.append(
            line(
                40, "event_msg", {"type": "turn_aborted", "turn_id": "t1", "reason": "interrupted"}
            )
        )
        (run,), _ = self.parse(self.write([meta(), *lines]))
        self.assertEqual((run.outcome, run.active_s), ("unknown", 40))

    def test_an_abort_for_another_turn_does_not_close_the_open_one(self):
        lines = turn(0, 60, "t1")
        lines.insert(
            3,
            line(
                5,
                "event_msg",
                {"type": "turn_aborted", "turn_id": "other", "reason": "interrupted"},
            ),
        )
        (run,), _ = self.parse(self.write([meta(), *lines]))
        self.assertEqual((run.outcome, run.active_s), ("completed", 60))

    def test_a_turn_left_open_by_a_new_one_is_unknown(self):
        first = turn(0, 60, "t1")[:-1]
        (left, closed), _ = self.parse(self.write([meta(), *first, *turn(100, 130, "t2")]))
        self.assertEqual((left.outcome, closed.outcome), ("unknown", "completed"))

    def test_an_unknown_effort_is_dropped(self):
        (run,), _ = self.parse(self.write([meta(), *turn(0, 60, "t1", effort="CANARY_EFFORT")]))
        self.assertIsNone(run.effort)

    def test_review_sessions_and_review_models_are_reviews(self):
        self.write([meta(session="r", source="review"), *turn(0, 30, "t1")])
        self.write([meta(session="m"), *turn(0, 30, "t1", model="codex-auto-review")])
        rows, _ = self.ingest()
        self.assertEqual([row["task_kind"] for row in rows], ["review", "review"])
        self.assertIn("other", {row["model"] for row in rows})

    def test_subagent_threads_are_their_own_batch(self):
        self.write([meta(session="parent"), *turn(0, 300, "t1")])
        self.write(
            [
                meta(session="child", source={"subagent": {"other": "CANARY_SOURCE"}}),
                *turn(10, 100, "t1"),
            ]
        )
        rows, _ = self.ingest()
        self.assertEqual(len({row["batch"] for row in rows}), 2)
        self.assertEqual({row["parallel_agents"] for row in rows}, {1})


class Skipped(Temp):
    def test_a_fork_is_skipped_whole_even_when_marked_in_a_later_record(self):
        runs, stats = self.parse(
            self.write([meta(), meta(session="parent", forked="sess-0"), *turn(0, 60, "t1")])
        )
        self.assertEqual((runs, stats.skipped_files), ([], 1))

    def test_records_naming_two_sessions_are_skipped(self):
        runs, stats = self.parse(self.write([meta(), meta(session="other"), *turn(0, 60, "t1")]))
        self.assertEqual((runs, stats.skipped_files), ([], 1))

    def test_upkeep_sessions_are_skipped(self):
        for index, source in enumerate(("compact", "memory_consolidation")):
            with self.subTest(source=source):
                runs, stats = self.parse(
                    self.write([meta(source=source), *turn(0, 60, "t1")], f"rollout-{index}.jsonl")
                )
                self.assertEqual((runs, stats.skipped_files), ([], 1))

    def test_a_file_without_metadata_is_empty(self):
        runs, stats = self.parse(self.write(turn(0, 60, "t1")))
        self.assertEqual((runs, stats.empty_runs), ([], 1))

    def test_the_report_names_skipped_files(self):
        self.write([meta(forked="x"), *turn(0, 60, "t1")])
        _, err = self.ingest()
        self.assertIn("skipped files:    1", err)


class Privacy(Temp):
    def test_no_canary_leaves_the_adapter(self):
        self.write([meta(source="CANARY_SOURCE"), *turn(0, 60, "t1", error=True)])
        self.write(
            [
                meta(
                    session="b",
                    source={"subagent": {"thread_spawn": {"agent_role": "CANARY_ROLE"}}},
                ),
                *turn(0, 60, "t1"),
            ]
        )
        self.write(["not json CANARY_MESSAGE", meta(session="c"), *turn(0, 60, "t1")])  # type: ignore[list-item]
        _, ingest_err = self.ingest()
        _, share_out, share_err = self.run_cli(
            "share", "--out-dir", str(self.root / "runs"), "--dry-run"
        )
        stored = paths.local_runs().read_text(encoding="utf-8")
        cache = paths.cache_file("codex").read_text(encoding="utf-8")
        for canary in CANARIES:
            for text in (stored, ingest_err, share_out, share_err, cache):
                self.assertNotIn(canary, text)
        for text in (stored, ingest_err, share_out, share_err):
            self.assertNotIn("CANARY_CWD", text)

    def test_share_keeps_claude_rows_unchanged_in_a_mixed_store(self):
        from tests.synthetic import row

        schema.write_rows(
            paths.local_runs(), [row(week="2026-W45"), row(week="2026-W45", offset_s=5)]
        )
        out_dir = str(self.root / "runs")
        _, before, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.write([meta(), *turn(0, 60, "t1")])
        self.ingest()
        _, after, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        claude_lines = [text for text in before.splitlines() if '"claude-code"' in text]
        self.assertEqual(len(claude_lines), 2)
        for text in claude_lines:
            self.assertIn(text, after.splitlines())
        self.assertIn('"codex"', after)

    def test_function_arguments_and_odd_abort_reasons_are_never_copied(self):
        lines = turn(0, 60, "t1")
        call = {
            "type": "function_call",
            "name": "x",
            "arguments": "CANARY_ARGUMENTS",
            "call_id": "f",
        }
        lines.insert(3, line(2, "response_item", call))
        lines[-1] = line(
            60, "event_msg", {"type": "turn_aborted", "turn_id": "t1", "reason": "CANARY_SOURCE"}
        )
        (run,), _ = self.parse(self.write([meta(), *lines]))
        self.assertEqual((run.tool_calls, run.outcome), (2, "unknown"))
        self.assertNotIn("CANARY", repr(run))

    def test_alias_event_names_never_open_a_turn(self):
        lines = [
            line(0, "event_msg", {"type": "turn_started", "turn_id": "t1"}),
            *turn(0, 60, "t1"),
        ]
        runs, _ = self.parse(self.write([meta(), *lines]))
        self.assertEqual(len(runs), 1)

    def test_reprs_hold_no_session_or_folder(self):
        (run,), _ = self.parse(self.write([meta(), *turn(0, 60, "t1")]))
        self.assertNotIn("CANARY_CWD", repr(run))
        self.assertNotIn("sess-1", repr(run))


if __name__ == "__main__":
    unittest.main()

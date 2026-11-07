"""`agentpace turns`: waits on the human from main-session timestamps, kept local."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, paths, schema, turns
from tests.synthetic import row

MARKERS = (
    "CANARY-PROMPT",
    "CANARY-CWD",
    "CANARY-BRANCH",
    "CANARY-RESULT",
    "CANARY-ASSISTANT",
    "CANARY-SESSION",
    "CANARY-PROJECT",
)


def at(seconds):
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"2026-11-02T{hours:02d}:{minutes:02d}:{secs:02d}.000Z"


def human(seconds, origin="human"):
    record = {
        "type": "user",
        "timestamp": at(seconds),
        "cwd": "/home/CANARY-CWD",
        "gitBranch": "CANARY-BRANCH",
        "message": {"role": "user", "content": "CANARY-PROMPT please"},
    }
    if origin is not None:
        record["turnOrigin"] = origin
    return record


def assistant(seconds):
    return {
        "type": "assistant",
        "timestamp": at(seconds),
        "cwd": "/home/CANARY-CWD",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "CANARY-ASSISTANT"}]},
    }


def tool_result(seconds):
    return {
        "type": "user",
        "timestamp": at(seconds),
        "turnOrigin": "human",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": "CANARY-RESULT", "is_error": True}],
        },
    }


# Prompt at 0, agent until 100 (with a tool result at 50 that is not a human turn), the person
# answers at 400, agent until 460, a task notification at 500, the person again at 1000.
SESSION = [
    human(0),
    assistant(10),
    tool_result(50),
    assistant(100),
    human(400),
    assistant(460),
    human(500, origin="task_notification"),
    assistant(520),
    human(1000),
    "not json {",
]


class Temp(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.projects = self.root / "projects"
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)

    def write_session(self, name, records, project="CANARY-PROJECT"):
        folder = self.projects / project
        folder.mkdir(parents=True, exist_ok=True)
        lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
        (folder / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Subagent transcripts are never main sessions.
        sub = folder / name / "subagents"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "agent-a.jsonl").write_text(json.dumps(human(0)) + "\n", encoding="utf-8")

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()


class Gaps(unittest.TestCase):
    def test_each_human_prompt_after_the_agent_opens_a_gap(self):
        marks = [turns._Mark(t, k) for t, k in ((0, "human"), (100, "assistant"), (400, "human"))]
        self.assertEqual(turns.gaps(marks), [turns.Gap(400, 300, 100)])

    def test_a_prompt_with_no_agent_record_before_it_opens_no_gap(self):
        marks = [turns._Mark(0, "human"), turns._Mark(5, "human")]
        self.assertEqual(turns.gaps(marks), [])

    def test_the_agent_turn_is_zero_when_the_agent_wrote_nothing_since_the_last_prompt(self):
        marks = [
            turns._Mark(0, "assistant"),
            turns._Mark(10, "human"),
            turns._Mark(30, "human"),
        ]
        self.assertEqual(turns.gaps(marks), [turns.Gap(10, 10, 0), turns.Gap(30, 30, 0)])


class Command(Temp):
    def test_gaps_follow_human_typed_prompts_only(self):
        self.write_session("CANARY-SESSION", SESSION)
        code, out, _ = self.run_cli("turns", str(self.projects))
        self.assertEqual(code, 0)
        stored = turns.read_turns(paths.turns_file())
        self.assertEqual(
            [(t["gap_s"], t["agent_turn_s"]) for t in stored], [(300, 100), (480, 120)]
        )
        self.assertIn("2 gaps between agent turns in 1 sessions", out)

    def test_nothing_but_the_four_fields_is_stored(self):
        self.write_session("CANARY-SESSION", SESSION)
        self.run_cli("turns", str(self.projects))
        for turn in turns.read_turns(paths.turns_file()):
            self.assertEqual(set(turn), {"session", "week", "gap_s", "agent_turn_s"})
            self.assertEqual(turns.check_turn(turn), [])
            self.assertEqual(turn["week"], "2026-W45")

    def test_no_marker_reaches_any_output(self):
        self.write_session("CANARY-SESSION", SESSION)
        _, out, err = self.run_cli("turns", str(self.projects))
        stored = paths.turns_file().read_text(encoding="utf-8")
        _, estimate_out, _ = self.run_cli(
            "estimate", str(Path("examples/plan.json").resolve()), "--draws", "200", "--json"
        )
        for marker in MARKERS:
            for text in (out, err, stored, estimate_out):
                self.assertNotIn(marker, text)

    def test_a_session_without_turn_origin_is_skipped_and_counted(self):
        self.write_session("old", [human(0, origin=None), assistant(5), human(60, origin=None)])
        _, out, err = self.run_cli("turns", str(self.projects))
        self.assertIn("skipped 1 sessions with no turnOrigin", err)
        self.assertEqual(turns.read_turns(paths.turns_file()), [])
        self.assertIn("stays not measured", out)

    def test_gaps_from_deleted_transcripts_are_kept(self):
        self.write_session("first", SESSION)
        self.run_cli("turns", str(self.projects))
        (self.projects / "CANARY-PROJECT" / "first.jsonl").unlink()
        self.write_session("second", SESSION)
        _, _, err = self.run_cli("turns", str(self.projects))
        self.assertEqual(len(turns.read_turns(paths.turns_file())), 4)
        self.assertIn("kept 2 gaps", err)

    def test_a_second_pass_does_not_duplicate_gaps(self):
        self.write_session("CANARY-SESSION", SESSION)
        self.run_cli("turns", str(self.projects))
        self.run_cli("turns", str(self.projects))
        self.assertEqual(len(turns.read_turns(paths.turns_file())), 2)


class Summary(unittest.TestCase):
    def test_quantiles_are_uncapped_and_shares_are_per_session(self):
        data = [
            {"session": "a" * 12, "week": "2026-W45", "gap_s": 100, "agent_turn_s": 100},
            {"session": "a" * 12, "week": "2026-W45", "gap_s": 100, "agent_turn_s": 100},
            {"session": "b" * 12, "week": "2026-W45", "gap_s": 90000, "agent_turn_s": 10000},
        ]
        summary = turns.summarize(data)
        assert summary is not None
        self.assertEqual((summary.gaps, summary.sessions), (3, 2))
        self.assertGreater(summary.p90, 3600 * 4)
        self.assertAlmostEqual(summary.over_hour, 1 / 3)
        self.assertAlmostEqual(summary.waiting_share, (0.5 + 0.9) / 2)

    def test_nothing_measured_gives_no_summary(self):
        self.assertIsNone(turns.summarize([]))


class Estimate(Temp):
    def setUp(self):
        super().setUp()
        self.plan = str(Path("examples/plan.json").resolve())

    def test_without_turns_the_human_part_is_not_measured(self):
        _, out, _ = self.run_cli("estimate", self.plan, "--draws", "200", "--json")
        self.assertEqual(
            json.loads(out)["human"], {"status": "not measured", "included_in_total": False}
        )

    def test_measured_waits_appear_per_turn_and_stay_out_of_the_total(self):
        self.write_session("s", SESSION)
        self.run_cli("turns", str(self.projects))
        _, before, _ = self.run_cli(
            "estimate",
            self.plan,
            "--draws",
            "200",
            "--json",
            "--turns",
            str(self.root / "none.jsonl"),
        )
        _, after, _ = self.run_cli("estimate", self.plan, "--draws", "200", "--json")
        measured = json.loads(after)
        self.assertEqual(measured["p90"], json.loads(before)["p90"])
        self.assertEqual(measured["human"]["status"], "measured")
        self.assertEqual(measured["human"]["source"], "your history, local")
        self.assertFalse(measured["human"]["included_in_total"])
        self.assertEqual((measured["human"]["gaps"], measured["human"]["sessions"]), (2, 1))
        _, text, _ = self.run_cli("estimate", self.plan, "--draws", "200")
        line = next(line for line in text.splitlines() if line.startswith("Human part"))
        self.assertIn("measured from your history, local", line)
        self.assertIn("per human turn", line)
        self.assertIn("Not included in the total above", line)


class Isolation(Temp):
    def test_share_output_is_unchanged_by_a_turns_file(self):
        schema.write_rows(paths.local_runs(), [row(), row(offset_s=5)])
        out_dir = str(self.root / "runs")
        _, without, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.write_session("s", SESSION)
        self.run_cli("turns", str(self.projects))
        self.assertTrue(paths.turns_file().exists())
        _, with_turns, _ = self.run_cli("share", "--out-dir", out_dir, "--dry-run")
        self.assertEqual(without, with_turns)

    def test_the_run_schema_has_no_turn_fields(self):
        for name in ("gap_s", "agent_turn_s", "session"):
            self.assertNotIn(name, schema.fields())


if __name__ == "__main__":
    unittest.main()

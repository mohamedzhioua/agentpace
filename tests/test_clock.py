"""A rows file keeps one idle cap, recorded in `<file>.clock.json` beside it (0.13.0)."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, clock, forecasts, ingest, paths, pseudonym, render, schema
from agentpace import estimate as est
from agentpace.adapters import get_adapter
from agentpace.adapters.base import RawRun
from agentpace.cache import RunCache
from tests.synthetic import dataset, row, simple_run, write_run, write_store
from tests.test_codex import meta, turn
from tests.test_forecasts import ISSUED, ISSUED_EPOCH, PLAN

REPO = Path(__file__).resolve().parent.parent
URL = "https://github.com/mohamedzhioua/agentpace/blob/main/docs/cli.md#the-idle-cap"
I1 = "agentpace: --idle-cap must be 1 or more seconds\n"
I2_900_600 = (
    "agentpace: runs.jsonl was filled with a 900 s idle cap, and this ingest asked for 600 s. "
    "Nothing was ingested. A store keeps one cap; for another, pass --out with a new file\n"
)
I2_600_900 = (
    "agentpace: runs.jsonl was filled with a 600 s idle cap, and this ingest asked for 900 s. "
    "Nothing was ingested. A store keeps one cap; for another, pass --out with a new file\n"
)
I3 = (
    "agentpace: runs.jsonl has no recorded idle cap, so only the default, 900 s, can be added to "
    "it, and this ingest asked for 600 s. Nothing was ingested. For another cap, pass --out with "
    "a new file\n"
)
I4 = (
    "agentpace: runs.jsonl.clock.json is not a clock record this version can read. Nothing was "
    "changed. Update agentpace, or delete that file, after which the idle cap counts as not "
    "recorded\n"
)
I5 = (
    "warning: idle cap is 600 s, not the default 900 s. The published dataset uses 900 s, so "
    "these rows cannot be shared, and an estimate that pools them with the bundled seed mixes "
    "two clocks."
)
I6 = (
    "note: runs.jsonl has no recorded idle cap: it was filled before agentpace kept one. share "
    f"refuses such a store. To record the cap, rebuild it: {URL}"
)
S1 = (
    "agentpace: runs.jsonl was filled with a 600 s idle cap, and the published dataset uses "
    "900 s, so its rows cannot be shared. Nothing was written. To contribute, rename it and run "
    "agentpace ingest at the default cap\n"
)
S2 = (
    "agentpace: runs.jsonl has no recorded idle cap, so share cannot tell whether its rows are on "
    f"the published 900 s clock. Nothing was written. Rebuild the store first: {URL}\n"
)
N1 = (
    "note: runs.jsonl has no recorded idle cap, so this session's span may not be on the "
    "forecast's clock.\n"
)
N2 = (
    "note: runs.jsonl was filled with a 600 s idle cap, so this session's span is on that clock, "
    "which may not be the forecast's.\n"
)
LINES = {
    "recorded 900": "Idle cap: 900 s (bundled seed, your local store).",
    "recorded 600": "Idle cap: 900 s (bundled seed), 600 s (your local store). This range pools "
    "rows on different clocks.",
    "not recorded": "Idle cap: 900 s (bundled seed), not recorded (your local store). The rows "
    "with no recorded cap may be on another clock.",
    "data 600": "Idle cap: 600 s (--data rows).",
    "data not recorded": "Idle cap: not recorded (--data rows).",
}
SEED_900 = {"source": "bundled seed", "seconds": 900}
OBJECTS = {
    "recorded 900": {
        "seconds": 900,
        "sources": [SEED_900, {"source": "your local store", "seconds": 900}],
    },
    "recorded 600": {
        "seconds": None,
        "sources": [SEED_900, {"source": "your local store", "seconds": 600}],
    },
    "not recorded": {
        "seconds": None,
        "sources": [SEED_900, {"source": "your local store", "seconds": None}],
    },
    "data 600": {"seconds": 600, "sources": [{"source": "--data rows", "seconds": 600}]},
    "data not recorded": {
        "seconds": None,
        "sources": [{"source": "--data rows", "seconds": None}],
    },
}
UNREADABLE = {
    "not JSON": b"{not json\n",
    "a list": b"[1, 900]\n",
    "version 2": b'{"version": 2, "idle_cap_s": 900}\n',
    "cap 0": b'{"version": 1, "idle_cap_s": 0}\n',
    "cap as a string": b'{"version": 1, "idle_cap_s": "900"}\n',
    "a boolean": b'{"version": 1, "idle_cap_s": true}\n',
    "an extra key": b'{"version": 1, "idle_cap_s": 900, "rows": 2}\n',
}


def note_bytes(seconds):
    return f'{{"version": 1, "idle_cap_s": {seconds}}}\n'.encode()


def published_rows(count=3):
    """Rows copied from the bundled seed: provably on the published clock."""
    return schema.read_dataset([paths.seed_dir()])[:count]


class Home(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.home = self.root / "home"
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)
        self.history = self.root / "projects"
        # A gap of 800 s between two runs: under the 900 s cap it stays on the session clock,
        # under 600 s it is clipped, so the second run's offset depends on the cap.
        write_run(self.history, "p", "s", "a", simple_run(0, 100))
        write_run(self.history, "p", "s", "b", simple_run(900, 100))
        self.store = paths.local_runs()
        self.note = self.store.with_name("runs.jsonl.clock.json")

    def run_cli(self, *args, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch("sys.stdin", io.StringIO(stdin)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def ingest(self, *extra):
        return self.run_cli("ingest", str(self.history), *extra)

    def files(self):
        """Every file in the home folder, by name, with its bytes."""
        if not self.home.exists():
            return {}
        return {path.name: path.read_bytes() for path in self.home.iterdir() if path.is_file()}


class Note(Home):
    def test_the_note_holds_the_cap_exactly(self):
        for cap in (900, 600):
            with self.subTest(cap=cap):
                out = self.root / f"out-{cap}.jsonl"
                code, _, err = self.ingest("--idle-cap", str(cap), "--out", str(out))
                self.assertEqual(code, 0, err)
                self.assertEqual(
                    out.with_name(out.name + ".clock.json").read_bytes(), note_bytes(cap)
                )
        code, _, err = self.ingest()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.note.read_bytes(), b'{"version": 1, "idle_cap_s": 900}\n')
        self.assertEqual(clock.note_text(600), '{"version": 1, "idle_cap_s": 600}\n')

    def test_a_600_s_note_in_the_default_store(self):
        code, _, err = self.ingest("--idle-cap", "600")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.note.read_bytes(), note_bytes(600))

    def test_the_note_is_written_before_the_rows(self):
        with (
            mock.patch.object(ingest, "merge_into", side_effect=RuntimeError("crash")),
            self.assertRaises(RuntimeError),
        ):
            self.ingest()
        self.assertEqual(self.note.read_bytes(), note_bytes(900))
        self.assertFalse(self.store.exists())
        # A note beside no row pins nothing: another cap may start the store, and rewrites it.
        code, _, err = self.ingest("--idle-cap", "600")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.note.read_bytes(), note_bytes(600))
        self.assertEqual(len(schema.read_rows(self.store)), 2)

    def test_an_unreadable_note_beside_rows_is_refused_everywhere(self):
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(PLAN), encoding="utf-8")
        for shape, content in UNREADABLE.items():
            with self.subTest(shape=shape):
                schema.write_rows(self.store, [row(), row(offset_s=5)])
                self.note.write_bytes(content)
                before = self.files()
                for args in (
                    ("ingest", str(self.history)),
                    ("estimate", str(plan), "--draws", "100"),
                    ("share", "--out-dir", str(self.root / "runs")),
                    ("share", "--write", "--out-dir", str(self.root / "runs")),
                ):
                    code, out, err = self.run_cli(*args)
                    self.assertEqual((code, out, err), (2, "", I4), args)
                self.assertEqual(self.files(), before)
                self.assertFalse((self.root / "runs").exists())
                # The hook stays silent and changes nothing.
                payload = {"agent_transcript_path": str(self.history / "p" / "s" / "subagents")}
                code, out, err = self.run_cli("ingest", "--hook", stdin=json.dumps(payload))
                self.assertEqual((code, out + err), (0, ""))
                self.assertEqual(self.files(), before)

    def test_an_unreadable_note_beside_no_row_is_overwritten(self):
        for shape, content in UNREADABLE.items():
            with self.subTest(shape=shape):
                self.store.unlink(missing_ok=True)
                self.home.mkdir(exist_ok=True)
                self.note.write_bytes(content)
                code, _, err = self.ingest()
                self.assertEqual(code, 0, err)
                self.assertEqual(self.note.read_bytes(), note_bytes(900))

    def test_an_unreadable_note_beside_data_rows_is_refused(self):
        data = self.root / "rows.jsonl"
        schema.write_rows(data, dataset(sessions=8))
        data.with_name("rows.jsonl.clock.json").write_bytes(UNREADABLE["version 2"])
        plan = self.root / "plan.json"
        plan.write_text(json.dumps(PLAN), encoding="utf-8")
        code, out, err = self.run_cli("estimate", str(plan), "--data", str(data))
        self.assertEqual((code, out), (2, ""))
        self.assertEqual(
            err,
            "agentpace: rows.jsonl.clock.json is not a clock record this version can read. "
            "Nothing was changed. Update agentpace, or delete that file, after which the idle cap "
            "counts as not recorded\n",
        )

    def test_label_leaves_the_note_alone(self):
        write_store(self.store, [row(), row(offset_s=50)])
        before = self.note.read_bytes()
        code, _, err = self.run_cli("label", f"{row()['batch']}:50", "--kind", "docs")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.note.read_bytes(), before)


class Ingest(Home):
    def cache_file(self):
        return paths.cache_file("claude-code")

    def test_the_reported_sequence_refuses_the_second_cap(self):
        code, _, err = self.ingest()
        self.assertEqual(code, 0, err)
        rows = self.store.read_bytes()
        cache = self.cache_file().read_bytes()
        code, out, err = self.ingest("--idle-cap", "600")
        self.assertEqual((code, out, err), (2, "", I2_900_600))
        self.assertEqual(self.store.read_bytes(), rows)
        self.assertEqual(self.cache_file().read_bytes(), cache)
        code, _, err = self.ingest()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.store.read_bytes(), rows)
        self.assertEqual(len(schema.read_rows(self.store)), 2)

    def test_a_refusal_creates_no_secret(self):
        write_store(self.store, [row(), row(offset_s=5)])
        before = self.files()
        self.assertNotIn("secret", before)
        code, _, err = self.ingest("--idle-cap", "600")
        self.assertEqual((code, err), (2, I2_900_600))
        self.assertEqual(self.files(), before)

    def test_the_reverse_order_warns_then_refuses(self):
        code, _, err = self.ingest("--idle-cap", "600")
        self.assertEqual(code, 0, err)
        self.assertEqual(err.splitlines()[0], I5)
        self.assertTrue(err.splitlines()[1].startswith("files seen:"))
        rows = self.store.read_bytes()
        code, out, err = self.ingest()
        self.assertEqual((code, out, err), (2, "", I2_600_900))
        self.assertEqual(self.store.read_bytes(), rows)
        self.assertFalse(self.cache_file().exists())

    def test_the_same_cap_twice_appends(self):
        for cap in ("900", "600"):
            with self.subTest(cap=cap):
                out = self.root / f"twice-{cap}.jsonl"
                for _ in range(2):
                    code, _, err = self.ingest("--idle-cap", cap, "--out", str(out))
                    self.assertEqual(code, 0, err)
                self.assertEqual(len(schema.read_rows(out)), 2)
                self.assertEqual(
                    out.with_name(out.name + ".clock.json").read_bytes(), note_bytes(cap)
                )

    def test_a_second_harness_at_the_same_cap(self):
        self.assertEqual(self.ingest()[0], 0)
        codex = self.root / "codex" / "2026" / "11" / "02"
        codex.mkdir(parents=True)
        rollout = codex / "rollout-2026-11-02-1.jsonl"
        rollout.write_text(
            "\n".join(json.dumps(line) for line in [meta(), *turn(0, 60, "t1")]) + "\n",
            encoding="utf-8",
        )
        code, _, err = self.run_cli("ingest", str(self.root / "codex"), "--source", "codex")
        self.assertEqual(code, 0, err)
        harnesses = sorted(r["harness"] for r in schema.read_rows(self.store))
        self.assertEqual(harnesses, ["claude-code", "claude-code", "codex"])
        self.assertEqual(self.note.read_bytes(), note_bytes(900))
        code, _, err = self.run_cli(
            "ingest", str(self.root / "codex"), "--source", "codex", "--idle-cap", "600"
        )
        self.assertEqual((code, err), (2, I2_900_600))

    def test_an_out_store_is_independent_of_the_default_one(self):
        out = self.root / "sensitivity.jsonl"
        code, _, err = self.ingest("--idle-cap", "600", "--out", str(out))
        self.assertEqual(code, 0, err)
        code, _, err = self.ingest()
        self.assertEqual(code, 0, err)
        self.assertEqual(out.with_name(out.name + ".clock.json").read_bytes(), note_bytes(600))
        self.assertEqual(self.note.read_bytes(), note_bytes(900))
        offsets = {
            path.name: sorted(r["offset_s"] for r in schema.read_rows(path))
            for path in (out, self.store)
        }
        self.assertEqual(offsets, {"sensitivity.jsonl": [0, 700], "runs.jsonl": [0, 900]})
        code, _, err = self.ingest("--idle-cap", "900", "--out", str(out))
        self.assertEqual(
            (code, err),
            (
                2,
                "agentpace: sensitivity.jsonl was filled with a 600 s idle cap, and this ingest "
                "asked for 900 s. Nothing was ingested. A store keeps one cap; for another, pass "
                "--out with a new file\n",
            ),
        )

    def test_a_cap_below_one_is_refused_with_nothing_written(self):
        # A payload the hook accepts, so the cap check is what stops it, not the payload.
        payload = json.dumps(
            {"agent_transcript_path": str(self.history / "p" / "s" / "subagents" / "a")}
        )
        for cap in ("0", "-5"):
            with self.subTest(cap=cap):
                code, out, err = self.ingest("--idle-cap", cap)
                self.assertEqual((code, out, err), (2, "", I1))
                self.assertEqual(self.files(), {})
                code, out, err = self.run_cli("ingest", "--hook", "--idle-cap", cap, stdin=payload)
                self.assertEqual((code, out + err), (0, ""))
                self.assertEqual(self.files(), {})

    def test_a_store_with_no_note_takes_the_default_cap_and_says_so(self):
        schema.write_rows(self.store, [row(), row(offset_s=5)])
        code, out, err = self.ingest()
        self.assertEqual((code, out), (0, ""), err)
        self.assertFalse(self.note.exists())
        self.assertEqual(len(schema.read_rows(self.store)), 4)
        lines = err.splitlines()
        self.assertEqual(lines[-2], "store now holds:  4 rows")
        self.assertEqual(lines[-1], I6)
        before = self.store.read_bytes()
        code, out, err = self.ingest("--idle-cap", "600")
        self.assertEqual((code, out, err), (2, "", I3))
        self.assertEqual(self.store.read_bytes(), before)
        self.assertFalse(self.note.exists())

    def test_a_store_of_published_rows_gains_the_note(self):
        seed = published_rows()
        schema.write_rows(self.store, seed)
        code, _, err = self.ingest("--idle-cap", "600")
        self.assertEqual((code, err), (2, I3))
        self.assertFalse(self.note.exists())
        code, _, err = self.ingest()
        self.assertEqual(code, 0, err)
        self.assertNotIn("note:", err)
        self.assertEqual(self.note.read_bytes(), note_bytes(900))
        stored = schema.read_rows(self.store)
        self.assertEqual(len(stored), len(seed) + 2)
        keys = {schema.dedupe_key(r) for r in stored}
        self.assertTrue({schema.dedupe_key(r) for r in seed} <= keys)

    def test_the_warning_sits_after_the_experimental_note(self):
        empty = self.root / "gemini"
        empty.mkdir()
        code, _, err = self.run_cli(
            "ingest", str(empty), "--source", "gemini-cli", "--idle-cap", "600"
        )
        self.assertEqual(code, 0, err)
        lines = err.splitlines()
        self.assertTrue(lines[0].startswith("note: the gemini-cli adapter is experimental"))
        self.assertEqual(lines[1], I5)
        self.assertTrue(lines[2].startswith("files seen:"))

    def test_stderr_at_the_default_cap_is_unchanged(self):
        expected = (
            "files seen:       2\n"
            "read from cache:  0\n"
            "rows produced:    2\n"
            "empty runs:       0\n"
            "unreadable files: 0\n"
            "unparsed lines:   0\n"
            "zero-duration:    0\n"
            "still running:    0\n"
            "store now holds:  2 rows\n"
        )
        self.assertEqual(self.ingest(), (0, "", expected))
        again = expected.replace("read from cache:  0", "read from cache:  2")
        self.assertEqual(self.ingest(), (0, "", again))


class Hook(Home):
    def hook(self):
        payload = {"agent_transcript_path": str(self.history / "p" / "s" / "subagents" / "a")}
        code, out, err = self.run_cli("ingest", "--hook", stdin=json.dumps(payload))
        return code, out + err

    def test_a_mismatch_is_silent_and_writes_nothing(self):
        write_store(self.store, [row()], idle_cap_s=600)
        before = self.files()
        self.assertEqual(self.hook(), (0, ""))
        self.assertEqual(self.files(), before)

    def test_a_store_with_no_note_takes_the_default_cap_silently(self):
        schema.write_rows(self.store, [row()])
        self.assertEqual(self.hook(), (0, ""))
        self.assertEqual(len(schema.read_rows(self.store)), 3)
        self.assertFalse(self.note.exists())

    def test_an_empty_store_gets_the_note(self):
        self.assertEqual(self.hook(), (0, ""))
        self.assertEqual(self.note.read_bytes(), note_bytes(900))
        self.assertEqual(len(schema.read_rows(self.store)), 2)


class Estimate(Home):
    def setUp(self):
        super().setUp()
        self.plan = self.root / "plan.json"
        self.plan.write_text(json.dumps(PLAN), encoding="utf-8")
        self.data = self.root / "rows.jsonl"

    def estimate(self, *extra):
        return self.run_cli("estimate", str(self.plan), "--draws", "200", *extra)

    def arrange(self, case):
        """One of the five cases in LINES, and the arguments that select it."""
        rows = [row(week="2026-W45"), row(week="2026-W45", offset_s=5)]
        for note in (self.note, self.data.with_name("rows.jsonl.clock.json")):
            note.unlink(missing_ok=True)
        if case == "recorded 900":
            write_store(self.store, rows)
        elif case == "recorded 600":
            write_store(self.store, rows, idle_cap_s=600)
        elif case == "not recorded":
            schema.write_rows(self.store, rows)
        elif case == "data 600":
            write_store(self.data, dataset(sessions=8), idle_cap_s=600)
            return ("--data", str(self.data))
        else:
            schema.write_rows(self.data, dataset(sessions=8))
            return ("--data", str(self.data))
        return ()

    def expected_plan(self, rows):
        tasks, cap = est.tasks_from_json(PLAN)
        return est.estimate(est.fit(rows), tasks, cap, 200, est.DEFAULT_SEED, est.HumanPart())

    def test_the_bundled_seed_alone_prints_what_it_printed_before(self):
        plan = self.expected_plan(schema.read_dataset([paths.seed_dir()]))
        self.assertEqual(self.estimate(), (0, render.plan_text(plan) + "\n", ""))
        self.assertEqual(self.estimate("--json"), (0, render.plan_json(plan) + "\n", ""))
        self.assertNotIn("idle_cap", json.loads(self.estimate("--json")[1]))
        quoted = self.estimate("--quoted", "3 weeks")
        self.assertEqual(quoted, (0, render.plan_text(plan, "3 weeks") + "\n", ""))
        # An empty store is not a source.
        self.home.mkdir()
        self.store.write_text("", encoding="utf-8")
        self.assertEqual(self.estimate(), (0, render.plan_text(plan) + "\n", ""))

    def test_each_line_sits_under_the_total_and_the_quote(self):
        for case, line in LINES.items():
            with self.subTest(case=case):
                data = self.arrange(case)
                code, out, err = self.estimate(*data)
                self.assertEqual((code, err), (0, ""))
                lines = out.splitlines()
                self.assertTrue(lines[0].startswith("Plan total on the session clock: P50 "))
                self.assertEqual(lines[1], line)
                self.assertEqual(lines[2], render.CLOCK_NOTE)
                code, out, err = self.estimate(*data, "--quoted", "3 weeks")
                self.assertEqual((code, err), (0, ""))
                lines = out.splitlines()
                self.assertEqual(lines[1], render.quote_text("3 weeks"))
                self.assertEqual(lines[2], line)
                self.assertEqual(lines[3], render.CLOCK_NOTE)
                self.assertTrue(lines[4].startswith("Human part"))
                self.assertEqual(out.count("Idle cap:"), 1)

    def test_each_json_object(self):
        for case, expected in OBJECTS.items():
            with self.subTest(case=case):
                code, out, err = self.estimate(*self.arrange(case), "--json")
                self.assertEqual((code, err), (0, ""))
                shown = json.loads(out)
                self.assertEqual(shown["idle_cap"], expected)
                self.assertEqual(list(shown)[-1], "idle_cap")
                self.assertEqual(set(shown) - {"idle_cap"}, set(est.PlanRange.__dataclass_fields__))

    def test_a_store_of_published_rows_reads_as_900_s(self):
        schema.write_rows(self.store, published_rows())
        self.assertEqual(self.estimate()[1].splitlines()[1], LINES["recorded 900"])

    def test_data_files_are_listed_in_order_and_empty_ones_skipped(self):
        folder = self.root / "folder"
        write_store(folder / "b.jsonl", dataset(sessions=4, seed=2))
        schema.write_rows(folder / "a.jsonl", dataset(sessions=4, seed=1))
        schema.write_rows(folder / "c.jsonl", [])
        write_store(self.data, dataset(sessions=4, seed=3), idle_cap_s=600)
        code, out, err = self.estimate("--data", str(self.data), "--data", str(folder), "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(
            json.loads(out)["idle_cap"],
            {
                "seconds": None,
                "sources": [
                    {"source": "--data rows", "seconds": 600},
                    {"source": "--data rows", "seconds": None},
                    {"source": "--data rows", "seconds": 900},
                ],
            },
        )
        code, out, _ = self.estimate("--data", str(self.data), "--data", str(folder))
        self.assertEqual(
            out.splitlines()[1],
            "Idle cap: 600 s (--data rows), not recorded (--data rows), 900 s (--data rows). "
            "This range pools rows on different clocks.",
        )

    def test_a_recorded_forecast_holds_no_idle_cap(self):
        self.arrange("recorded 600")
        code, out, _ = self.estimate("--record")
        self.assertEqual(code, 0)
        self.assertIn(LINES["recorded 600"], out.splitlines())
        (record,) = forecasts.read(paths.forecasts_file()).forecasts.values()
        self.assertNotIn("idle_cap", json.dumps(record))

    def test_the_line_never_moves_stderr(self):
        for case in LINES:
            with self.subTest(case=case):
                self.assertEqual(self.estimate(*self.arrange(case), "--record")[2].count("\n"), 1)


class Share(Home):
    def setUp(self):
        super().setUp()
        self.out = self.root / "runs"
        self.rows = [row(), row(offset_s=5), row(batch="cccccccccccc", week="2026-W12")]

    def share(self, *args):
        return self.run_cli("share", "--out-dir", str(self.out), *args)

    def test_a_store_recorded_at_900_s_shares_as_before(self):
        write_store(self.store, self.rows)
        for flags in (
            (),
            ("--write",),
            ("--since", "2026-W11"),
            ("--drop-project", "--write"),
            ("--drop-session", "cccccccccccc"),
        ):
            with self.subTest(flags=flags):
                self.clear()
                # Without the check, the command is what it was before the note existed.
                with mock.patch.object(clock, "check_shareable"):
                    before = self.share(*flags)
                written = self.written()
                self.clear()
                after = self.share(*flags)
                self.assertEqual(after, before)
                self.assertEqual(after[0], 0)
                self.assertEqual(self.written(), written)

    def clear(self):
        for path in self.out.glob("*"):
            path.unlink()

    def written(self):
        return {path.name: path.read_bytes() for path in self.out.glob("*")}

    def test_another_cap_or_no_record_is_refused_before_anything(self):
        self.out.mkdir()
        held = self.out / "someone-2026-W01-2026-W01.jsonl"
        held.write_bytes(b"not even rows\n")
        for case, message in (("recorded 600", S1), ("not recorded", S2)):
            if case == "recorded 600":
                write_store(self.store, self.rows, idle_cap_s=600)
            else:
                self.note.unlink()
            for flags in (
                (),
                ("--dry-run",),
                ("--write",),
                ("--since", "2026-W11"),
                ("--drop-session", "aaaaaaaaaaaa"),
                ("--drop-session", "ffffffffffff"),
                ("--drop-project", "--write"),
            ):
                with self.subTest(case=case, flags=flags):
                    self.assertEqual(self.share(*flags), (2, "", message))
                    self.assertEqual(list(self.out.iterdir()), [held])
                    self.assertEqual(held.read_bytes(), b"not even rows\n")

    def test_a_store_of_published_rows_shares_and_gains_no_note(self):
        # A store from before 0.13.0 that holds only published rows is on the published clock.
        schema.write_rows(self.store, published_rows())
        for flags in ((), ("--write",)):
            with self.subTest(flags=flags):
                code, _, err = self.share(*flags)
                self.assertEqual(code, 0, err)
                self.assertFalse(self.note.exists())
        self.assertEqual([path.suffix for path in self.out.iterdir()], [".jsonl"])
        self.assertEqual(len(schema.read_dataset([self.out])), 3)

    def test_end_to_end_a_refused_ingest_adds_nothing_to_share(self):
        code, _, err = self.run_cli("ingest", str(self.history))
        self.assertEqual(code, 0, err)
        code, _, err = self.share("--write")
        self.assertEqual(code, 0, err)
        code, _, err = self.run_cli("ingest", str(self.history), "--idle-cap", "600")
        self.assertEqual((code, err), (2, I2_900_600))
        code, out, err = self.share("--write")
        self.assertEqual((code, out), (0, ""))
        self.assertTrue(
            err.endswith("2 rows are already published and left out.\nnothing new to share.\n")
        )


class Score(Home):
    def setUp(self):
        super().setUp()
        self.secret = pseudonym.load_secret(paths.home())
        self.batch = pseudonym.batch(self.secret, "session-1")
        result = {
            "method": "shrunk",
            "seed": 1,
            "draws": 10,
            "rows_used": 20,
            "rows_excluded": 0,
            "p50": 600.0,
            "p80": 1200.0,
            "p90": 1800.0,
            "tasks": [],
            "human": {"status": "not measured", "included_in_total": False},
        }
        record = forecasts.forecast_record(
            issued_at=ISSUED, version="0.13.0", plan_document=PLAN, result=result, quoted=None
        )
        forecasts.append(paths.forecasts_file(), record)
        self.id = record["id"]
        self.spans = ((0, 300), (200, 900))
        adapter = get_adapter("claude-code")
        cache = RunCache(paths.cache_file(adapter.name), adapter.name, adapter.version, 900)
        for index, (offset, length) in enumerate(self.spans):
            start = ISSUED_EPOCH + 60 + offset
            run = RawRun(
                harness="claude-code",
                model="claude-opus-5-5",
                start=start,
                end=start + length,
                spans=((start, start + length),),
                active_s=length,
                tool_calls=1,
                edit_calls=0,
                tokens=None,
                errors=0,
                outcome="completed",
                session="session-1",
            )
            cache.store(f"/x/agent-{index}.jsonl", [[1, 1]], [run])
        cache.save()

    def rows(self):
        return [
            row(batch=self.batch, week="2026-W45", offset_s=offset, duration_s=length)
            for offset, length in self.spans
        ]

    def score(self):
        code, out, err = self.run_cli("score", self.id, "--session", self.batch)
        self.assertEqual(code, 0, err)
        (score, *_) = forecasts.read(paths.forecasts_file()).scores[-1:]
        return out, err, {k: v for k, v in score.items() if k != "scored_at"}

    def test_the_note_names_a_store_not_on_the_recorded_default(self):
        write_store(self.store, self.rows())
        _, err, recorded = self.score()
        self.assertEqual(err, "")
        write_store(self.store, self.rows(), idle_cap_s=600)
        self.assertEqual(self.score()[1:], (N2, recorded))
        self.note.unlink()
        self.assertEqual(self.score()[1:], (N1, recorded))
        self.assertEqual(recorded["actual_s"], 1100)
        self.assertNotIn("idle", json.dumps(recorded))

    def test_a_store_of_published_rows_gets_no_session_note(self):
        cap = clock.store_cap(self.store, published_rows())
        self.assertEqual((cap.state, cap.seconds), (clock.PUBLISHED, 900))
        self.assertIsNone(clock.session_note(cap, "runs.jsonl"))

    def test_an_attested_actual_reads_no_store(self):
        write_store(self.store, self.rows(), idle_cap_s=600)
        code, _, err = self.run_cli("score", self.id, "--actual", "2h")
        self.assertEqual((code, err), (0, ""))

    def test_an_unreadable_note_refuses_a_session_score(self):
        write_store(self.store, self.rows())
        self.note.write_bytes(UNREADABLE["version 2"])
        before = paths.forecasts_file().read_bytes()
        self.assertEqual(self.run_cli("score", self.id, "--session", self.batch), (2, "", I4))
        self.assertEqual(paths.forecasts_file().read_bytes(), before)


class Docs(unittest.TestCase):
    def test_the_command_reference_names_the_record(self):
        text = (REPO / "docs" / "cli.md").read_text(encoding="utf-8")
        for phrase in ("runs.jsonl.clock.json", "`idle_cap`", "### The idle cap"):
            self.assertIn(phrase, text)
        self.assertIn("`store now holds:  N rows`", text)
        for line in LINES.values():
            self.assertIn(f"\n{line}\n", text)


if __name__ == "__main__":
    unittest.main()

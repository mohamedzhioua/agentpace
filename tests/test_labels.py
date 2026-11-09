import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, ingest, paths, pseudonym, schema
from agentpace.adapters import get_adapter
from agentpace.adapters.base import RawRun
from agentpace.cache import RunCache
from tests.synthetic import row


class LabelCommand(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": folder.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.home = Path(folder.name)
        schema.write_rows(paths.local_runs(), [row(offset_s=0), row(offset_s=50)])

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_runs_lists_references(self):
        code, out, _ = self.run_cli("runs")
        self.assertEqual(code, 0)
        self.assertIn("aaaaaaaaaaaa:50", out)

    def test_runs_without_data_is_a_clear_error(self):
        paths.local_runs().unlink()
        self.assertEqual(self.run_cli("runs")[0], 2)

    def test_label_changes_only_that_run(self):
        code, _, _ = self.run_cli("label", "aaaaaaaaaaaa:0", "--kind", "bugfix", "--risk", "high")
        self.assertEqual(code, 0)
        kinds = [r["task_kind"] for r in schema.read_rows(paths.local_runs())]
        self.assertEqual(sorted(kinds), ["bugfix", "feature"])

    def test_label_last_uses_the_latest_run(self):
        with mock.patch.object(cli, "_cached_starts", return_value={"aaaaaaaaaaaa": 0.0}):
            self.run_cli("label", "last", "--kind", "refactor")
        rows = {r["offset_s"]: r for r in schema.read_rows(paths.local_runs())}
        self.assertEqual(rows[50]["task_kind"], "refactor")
        self.assertEqual(rows[0]["task_kind"], "feature")

    def test_label_refuses_while_an_ingest_holds_the_store(self):
        before = paths.local_runs().read_bytes()
        real_lock = ingest.store_lock
        waits = []

        def no_wait(store, wait_s=5.0, **kwargs):
            waits.append((store, wait_s))
            return real_lock(store, wait_s=0.0, **kwargs)

        with (
            ingest.store_lock(paths.local_runs()),
            mock.patch.object(ingest, "store_lock", no_wait),
        ):
            code, out, err = self.run_cli("label", "aaaaaaaaaaaa:0", "--kind", "bugfix")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, "agentpace: another ingest is writing the store; try again\n")
        self.assertEqual(waits, [(paths.local_runs(), 5.0)])
        self.assertEqual(paths.local_runs().read_bytes(), before)
        self.assertFalse(paths.labels_file().exists())

    def test_label_reads_and_writes_under_the_store_lock(self):
        lock = paths.local_runs().with_name("runs.jsonl.lock")
        held = []
        real_read, real_write = schema.read_rows, schema.write_rows

        def read(*args, **kwargs):
            held.append(("read", lock.exists()))
            return real_read(*args, **kwargs)

        def write(*args, **kwargs):
            held.append(("write", lock.exists(), paths.labels_file().exists()))
            return real_write(*args, **kwargs)

        with (
            mock.patch.object(schema, "read_rows", read),
            mock.patch.object(schema, "write_rows", write),
        ):
            code, _, _ = self.run_cli("label", "aaaaaaaaaaaa:0", "--kind", "bugfix")
        self.assertEqual(code, 0)
        self.assertEqual(held, [("read", True), ("write", True, True)])
        self.assertFalse(lock.exists())

    def test_label_survives_reingest(self):
        self.run_cli("label", "aaaaaaaaaaaa:0", "--kind", "bugfix")
        labels = ingest.load_labels(paths.labels_file())
        fresh = row(offset_s=0)
        ingest.apply_labels(fresh, labels)
        self.assertEqual(fresh["task_kind"], "bugfix")

    def test_unknown_reference_is_rejected(self):
        code, _, err = self.run_cli("label", "ffffffffffff:1", "--kind", "docs")
        self.assertEqual(code, 2)
        self.assertIn("no local run", err)

    def test_label_needs_a_field(self):
        self.assertEqual(self.run_cli("label", "last")[0], 2)

    def test_invalid_kind_is_rejected_by_the_parser(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["label", "last", "--kind", "nonsense"])


class Chronology(unittest.TestCase):
    """`runs` and `label last` order sessions by the start the local caches remember.

    A batch is an HMAC, so its order says nothing about time; rows hold no clock time at all.
    """

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": folder.name})
        patch.start()
        self.addCleanup(patch.stop)
        self.secret = pseudonym.load_secret(paths.home())
        # Two sessions whose batch order contradicts the order they started in.
        first, second = (pseudonym.batch(self.secret, name) for name in ("s-early", "s-late"))
        if first < second:
            self.early, self.late = "s-late", "s-early"
        else:
            self.early, self.late = "s-early", "s-late"
        self.assertGreater(self.batch(self.early), self.batch(self.late))

    def batch(self, session):
        return pseudonym.batch(self.secret, session)

    def store(self, *sessions):
        """Write rows: each session is (name, week, offsets)."""
        rows = [
            row(batch=self.batch(name), week=week, offset_s=offset)
            for name, week, offsets in sessions
            for offset in offsets
        ]
        schema.write_rows(paths.local_runs(), rows)

    def cache(self, session, start, source="claude-code"):
        adapter = get_adapter(source)
        cache = RunCache(paths.cache_file(adapter.name), adapter.name, adapter.version, 900)
        run = RawRun(
            harness=source,
            model="claude-opus-5-5",
            start=start,
            end=start + 60,
            spans=((start, start + 60),),
            active_s=60,
            tool_calls=1,
            edit_calls=0,
            tokens=None,
            errors=0,
            outcome="completed",
            session=session,
        )
        cache.store(f"/x/{session}-{start}.jsonl", [[1, 1]], [run])
        cache.save()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def listed(self, out):
        return [line.split()[0] for line in out.splitlines()]

    def labeled(self):
        return {
            ingest.run_ref(r): r["task_kind"]
            for r in schema.read_rows(paths.local_runs())
            if r["task_kind"] == "refactor"
        }

    def test_the_cached_start_orders_sessions_not_the_batch(self):
        self.store((self.early, "2026-W10", (0, 100)), (self.late, "2026-W10", (0, 30)))
        self.cache(self.early, 1000.0)
        self.cache(self.late, 2000.0, source="codex")  # every adapter's cache is read
        self.cache(self.late, 1500.0)  # a session's earliest start counts
        early, late = self.batch(self.early), self.batch(self.late)
        code, out, err = self.run_cli("runs")
        self.assertEqual(code, 0)
        self.assertEqual(
            self.listed(out), [f"{early}:0", f"{early}:100", f"{late}:0", f"{late}:30"]
        )
        self.assertEqual(err, "")
        _, out, _ = self.run_cli("runs", "--last", "2")
        self.assertEqual(self.listed(out), [f"{late}:0", f"{late}:30"])
        code, out, _ = self.run_cli("label", "last", "--kind", "refactor")
        self.assertEqual(code, 0)
        self.assertEqual(out, f"labeled {late}:30: refactor, normal risk\n")
        self.assertEqual(self.labeled(), {f"{late}:30": "refactor"})

    def test_without_a_cache_runs_lists_by_week_and_last_refuses(self):
        self.store((self.early, "2026-W10", (0, 100)), (self.late, "2026-W11", (0,)))
        before = paths.local_runs().read_bytes()
        code, out, err = self.run_cli("runs")
        self.assertEqual(code, 0)
        self.assertEqual(len(out.splitlines()), 3)
        self.assertEqual(
            err,
            "2 sessions have no local chronology (no cache entry, or ingested with another idle "
            "cap); they are listed by week only. Run agentpace ingest to refresh the cache.\n",
        )
        code, out, err = self.run_cli("label", "last", "--kind", "refactor")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(
            err,
            "agentpace: cannot tell which run is last: 2 sessions have no local chronology. "
            "Run agentpace ingest, or give a reference from agentpace runs\n",
        )
        self.assertEqual(paths.local_runs().read_bytes(), before)
        self.assertFalse(paths.labels_file().exists())

    def test_a_cache_without_a_secret_gives_no_chronology(self):
        self.store((self.early, "2026-W10", (0,)))
        self.cache(self.early, 1000.0)
        (paths.home() / "secret").unlink()
        self.assertEqual(cli._cached_starts(), {})
        self.assertFalse((paths.home() / "secret").exists())

    def test_mixed_unknown_sessions_sort_first_within_their_week(self):
        known, unknown = self.batch(self.early), self.batch(self.late)
        self.store((self.early, "2026-W10", (0, 100)), (self.late, "2026-W10", (0, 30)))
        self.cache(self.early, 1000.0)
        code, out, err = self.run_cli("runs")
        self.assertEqual(code, 0)
        self.assertEqual(
            self.listed(out), [f"{unknown}:0", f"{unknown}:30", f"{known}:0", f"{known}:100"]
        )
        self.assertEqual(
            err,
            "1 session has no local chronology (no cache entry, or ingested with another idle "
            "cap); it is listed by week only. Run agentpace ingest to refresh the cache.\n",
        )
        _, out, err = self.run_cli("runs", "--last", "2")
        self.assertEqual(self.listed(out), [f"{known}:0", f"{known}:100"])
        self.assertEqual(err, "")

    def test_mixed_last_refuses_when_an_unknown_session_reaches_the_latest_week(self):
        self.store(
            (self.early, "2026-W10", (0,)),
            (self.early, "2026-W11", (5000,)),
            (self.late, "2026-W12", (0,)),
            (self.late, "2026-W11", (40,)),
        )
        self.cache(self.early, 1000.0)
        code, _, err = self.run_cli("label", "last", "--kind", "refactor")
        self.assertEqual(code, 2)
        self.assertEqual(
            err,
            "agentpace: cannot tell which run is last: 1 session has no local chronology. "
            "Run agentpace ingest, or give a reference from agentpace runs\n",
        )
        self.assertEqual(self.labeled(), {})

    def test_mixed_last_picks_the_known_session_when_unknown_ones_are_older(self):
        known = self.batch(self.early)
        self.store(
            (self.early, "2026-W10", (0,)),
            (self.early, "2026-W11", (5000,)),
            (self.late, "2026-W10", (0, 900)),
        )
        self.cache(self.early, 1000.0)
        code, out, _ = self.run_cli("label", "last", "--kind", "refactor")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.labeled(), {f"{known}:5000": "refactor"})

    def test_the_store_gains_no_field(self):
        self.store((self.early, "2026-W10", (0, 100)), (self.late, "2026-W10", (0, 30)))
        self.cache(self.early, 1000.0)
        self.cache(self.late, 2000.0)
        fields = {frozenset(r) for r in schema.read_rows(paths.local_runs())}
        self.run_cli("runs")
        self.assertEqual(self.run_cli("label", "last", "--risk", "high")[0], 0)
        stored = schema.read_rows(paths.local_runs())
        self.assertEqual({frozenset(r) for r in stored}, fields)
        for stored_row in stored:
            self.assertEqual(schema.check_row(stored_row), [])


if __name__ == "__main__":
    unittest.main()

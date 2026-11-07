import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, ingest, paths, schema
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
        self.run_cli("label", "last", "--kind", "refactor")
        rows = {r["offset_s"]: r for r in schema.read_rows(paths.local_runs())}
        self.assertEqual(rows[50]["task_kind"], "refactor")
        self.assertEqual(rows[0]["task_kind"], "feature")

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


if __name__ == "__main__":
    unittest.main()

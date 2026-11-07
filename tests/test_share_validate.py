import ast
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentpace import cli, paths, schema, share, validate
from tests.synthetic import row

SOURCE = Path(__file__).resolve().parent.parent / "src"
NAME = "0123456789abcdef-2026-W10-2026-W10.jsonl"


class Temp(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def write(self, lines, name=NAME):
        path = self.root / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def rules(self, *rows):
        path = self.write([schema.canonical(r) for r in rows])
        return [finding.rule for finding in validate.validate([path])]


class Validation(Temp):
    def test_good_rows_pass(self):
        self.assertEqual(self.rules(row(), row(offset_s=5)), [])

    def test_duplicates_within_a_file(self):
        self.assertTrue(any("duplicate" in rule for rule in self.rules(row(), row())))

    def test_duplicates_across_files(self):
        self.write([schema.canonical(row())])
        self.write([schema.canonical(row(task_kind="bugfix"))], "0123456789abcdef-other.jsonl")
        rules = [finding.rule for finding in validate.validate([self.root])]
        self.assertTrue(any("duplicate" in rule for rule in rules))

    def test_relabeled_runs_are_still_duplicates(self):
        rules = self.rules(row(), row(task_kind="docs", risk="high"))
        self.assertTrue(any("duplicate" in rule for rule in rules))

    def test_outliers(self):
        self.assertIn("outlier: duration over 24 hours", self.rules(row(duration_s=90_000)))
        self.assertIn(
            "outlier: implausible tool calls per second",
            self.rules(row(duration_s=10, tool_calls=1000)),
        )

    def test_unknown_field_is_rejected(self):
        path = self.write([json.dumps({**row(), "prompt": "hello"})])
        self.assertTrue(validate.validate([path]))

    def test_private_model_names_are_rejected(self):
        for name in ("acme.internal", "10.0.0.1"):
            self.assertTrue(self.rules(row(model=name)), name)

    def test_other_is_allowed_as_a_model(self):
        self.assertEqual(self.rules(row(model="other")), [])

    def test_pii_shapes_are_caught_in_raw_text(self):
        samples = {
            "email address": "x@example.com",
            "url": "https://example.com",
            "windows path": "C:\\Users\\me",
            "unix path": "/home/me",
            "ip address": "10.1.2.3",
            "api key": "sk-abcdefgh12345678",
            "jwt": "eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0",
        }
        for shape, sample in samples.items():
            with self.subTest(shape=shape):
                self.assertIn(shape, validate.scan_for_pii(sample))

    def test_real_shaped_rows_do_not_trigger_pii(self):
        self.assertEqual(validate.scan_for_pii(schema.canonical(row())), [])

    def test_findings_never_quote_the_value(self):
        leak = {**row(), "note": "secret@example.com"}
        path = self.write([json.dumps(leak)])
        text = " ".join(str(f) for f in validate.validate([path]))
        self.assertIn("email", text)
        self.assertNotIn("secret@example.com", text)

    def test_broken_json_is_reported(self):
        rules = [f.rule for f in validate.validate([self.write(["{broken"])])]
        self.assertIn("not valid JSON", rules)

    def test_mixed_contributors_are_rejected(self):
        other = row(contributor="f" * 16)
        self.assertIn("a file must hold one contributor", self.rules(row(), other))

    def test_file_name_must_match_contributor(self):
        path = self.write([schema.canonical(row())], "someone-else.jsonl")
        rules = [f.rule for f in validate.validate([path])]
        self.assertIn("file name must start with its contributor", rules)

    def test_only_jsonl_is_allowed(self):
        path = self.root / "notes.txt"
        path.write_text("x", encoding="utf-8")
        self.assertTrue(validate.validate([path]))

    def test_non_utf8_is_rejected(self):
        path = self.root / NAME
        path.write_bytes(b"\xff\xfe\x00")
        self.assertIn("file is not UTF-8", [f.rule for f in validate.validate([path])])

    def test_gitkeep_is_ignored(self):
        (self.root / ".gitkeep").write_text("", encoding="utf-8")
        self.assertEqual(validate.validate([self.root]), [])


class Selection(unittest.TestCase):
    def test_since_filters_by_week(self):
        rows = [row(week="2026-W10"), row(week="2026-W20", offset_s=1)]
        chosen, _ = share.select(rows, since="2026-W15")
        self.assertEqual([r["week"] for r in chosen], ["2026-W20"])

    def test_drop_project_removes_the_field(self):
        chosen, _ = share.select([row()], drop_project=True)
        self.assertNotIn("project", chosen[0])

    def test_published_rows_are_skipped(self):
        chosen, skipped = share.select([row(), row(offset_s=9)], already_published=[row()])
        self.assertEqual((len(chosen), skipped), (1, 1))

    def test_invalid_rows_are_refused(self):
        with self.assertRaisesRegex(ValueError, "invalid"):
            share.select([row(harness="other-thing")])

    def test_file_name_spans_the_weeks(self):
        rows = [row(week="2026-W20"), row(week="2026-W10")]
        self.assertEqual(share.file_name(rows), "0123456789abcdef-2026-W10-2026-W20.jsonl")


class ShareCommand(Temp):
    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        schema.write_rows(paths.local_runs(), [row(), row(offset_s=5)])
        self.out = self.root / "runs"

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["share", "--out-dir", str(self.out), *args])
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_is_the_default_and_writes_nothing(self):
        code, _out, err = self.run_cli()
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        self.assertIn("dry run", err.lower())

    def test_dry_run_prints_the_exact_bytes_write_would_produce(self):
        _, printed, _ = self.run_cli("--dry-run")
        self.run_cli("--write")
        written = next(self.out.glob("*.jsonl")).read_text(encoding="utf-8")
        self.assertEqual(printed, written)

    def test_the_notice_comes_with_every_run(self):
        for flags in ((), ("--write",)):
            self.assertIn("What these rows reveal", self.run_cli(*flags)[2])

    def test_write_produces_a_file_that_passes_validation(self):
        self.run_cli("--write")
        self.assertEqual(validate.validate([self.out]), [])

    def test_write_prints_the_commands_to_submit(self):
        self.assertIn("git push", self.run_cli("--write")[2])

    def test_a_second_share_adds_nothing(self):
        self.run_cli("--write")
        code, _, err = self.run_cli("--write")
        self.assertEqual(code, 0)
        self.assertIn("nothing new", err)
        self.assertEqual(len(list(self.out.glob("*.jsonl"))), 1)

    def test_dry_run_and_write_conflict(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_cli("--dry-run", "--write")

    def test_nothing_ingested_is_a_clear_error(self):
        paths.local_runs().unlink()
        code, _, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("ingest", err)


class ValidateCommand(Temp):
    def test_exit_codes(self):
        good = self.write([schema.canonical(row())])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["validate", str(good)]), 0)
        bad = self.write(["{broken"], "0123456789abcdef-b.jsonl")
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["validate", str(bad)]), 1)


class NoNetwork(unittest.TestCase):
    """The package contains no way to send anything anywhere."""

    FORBIDDEN = frozenset(
        {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "subprocess", "asyncio"}
    )

    def test_no_module_imports_anything_that_can_reach_a_network(self):
        files = list(SOURCE.rglob("*.py"))
        self.assertGreater(len(files), 10)
        for file in files:
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    self.assertNotIn(name.split(".")[0], self.FORBIDDEN, file.name)

    def test_no_module_uses_dynamic_imports(self):
        for file in SOURCE.rglob("*.py"):
            text = file.read_text(encoding="utf-8")
            for marker in ("__import__", "importlib", "os.system", "os.popen", "exec("):
                self.assertNotIn(marker, text, file.name)


if __name__ == "__main__":
    unittest.main()

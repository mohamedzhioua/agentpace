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
from tests.synthetic import row, write_store

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

    def test_experimental_harness_rows_are_rejected(self):
        problems = validate.check_values(row(harness="gemini-cli"))
        self.assertTrue(any("experimental" in p for p in problems))

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
            # Built at run time: the repo's secret scanner flags literal key shapes,
            # and this one is synthetic. The validator still sees the same bytes.
            "api key": "sk-" + "abcdefgh12345678",
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
        chosen, _, _ = share.select(rows, since="2026-W15")
        self.assertEqual([r["week"] for r in chosen], ["2026-W20"])

    def test_drop_project_removes_the_field(self):
        chosen, _, _ = share.select([row()], drop_project=True)
        self.assertNotIn("project", chosen[0])

    def test_published_rows_are_skipped(self):
        chosen, skipped, _ = share.select([row(), row(offset_s=9)], already_published=[row()])
        self.assertEqual((len(chosen), skipped), (1, 1))

    def test_dropped_sessions_are_left_out_before_the_published_count(self):
        rows = [row(), row(offset_s=9), row(batch="d" * 12), row(batch="c" * 12)]
        chosen, skipped, dropped = share.select(
            rows, already_published=[row()], drop_sessions=["a" * 12, "c" * 12, "a" * 12]
        )
        self.assertEqual([r["batch"] for r in chosen], ["d" * 12])
        self.assertEqual(skipped, 0)
        self.assertEqual(dropped, {"a" * 12: 2, "c" * 12: 1})

    def test_a_dropped_session_with_no_local_row_is_refused(self):
        with self.assertRaisesRegex(ValueError, "names 1 session with no local run"):
            share.select([row()], drop_sessions=["a" * 12, "f" * 12])

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
        write_store(paths.local_runs(), [row(), row(offset_s=5)])
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

    def add_local_run(self, **overrides):
        stored = schema.read_rows(paths.local_runs())
        schema.write_rows(paths.local_runs(), [*stored, row(**overrides)])

    def test_a_second_share_of_the_same_span_appends(self):
        self.run_cli("--write")
        path = self.out / NAME
        first = path.read_bytes()
        self.add_local_run(offset_s=50)
        code, _, err = self.run_cli("--write")
        self.assertEqual(code, 0)
        self.assertEqual(list(self.out.glob("*.jsonl")), [path])
        self.assertTrue(path.read_bytes().startswith(first))
        self.assertEqual(len(schema.read_rows(path)), 3)
        self.assertIn(f"added 1 row to {path}, which already held 2. Nothing has been sent.", err)
        self.assertEqual(validate.validate([self.out]), [])

    def test_an_existing_file_keeps_its_bytes_even_without_a_final_newline(self):
        self.out.mkdir()
        path = self.out / NAME
        path.write_bytes(schema.canonical(row(offset_s=90)).encode("utf-8"))
        first = path.read_bytes()
        code, _, err = self.run_cli("--write")
        self.assertEqual(code, 0)
        self.assertTrue(path.read_bytes().startswith(first + b"\n"))
        self.assertEqual(len(schema.read_rows(path)), 3)
        self.assertIn("which already held 1", err)
        self.assertEqual(validate.validate([self.out]), [])

    def test_a_file_holding_another_contributors_rows_is_refused(self):
        self.out.mkdir()
        path = self.out / NAME
        path.write_bytes(schema.canonical(row(contributor="f" * 16, offset_s=90)).encode() + b"\n")
        before = path.read_bytes()
        for flags in (("--write",), ()):
            with self.subTest(flags=flags):
                code, out, err = self.run_cli(*flags)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertIn(
                    f"refusing to add rows to {NAME}: it holds another contributor's rows", err
                )
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(list(self.out.iterdir()), [path])

    def test_the_dry_run_names_the_file_it_would_create(self):
        _, _, err = self.run_cli()
        self.assertIn(f"--write would create {self.out / NAME}.", err)
        self.assertFalse(self.out.exists())

    def test_the_dry_run_names_the_file_it_would_extend(self):
        self.run_cli("--write")
        path = self.out / NAME
        before = path.read_bytes()
        self.add_local_run(offset_s=50)
        _, printed, err = self.run_cli()
        self.assertIn(f"--write would add it to {path}, which holds 2 rows.", err)
        self.assertEqual(printed, schema.canonical(row(offset_s=50)) + "\n")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(self.out.iterdir()), [path])

    def test_experimental_rows_are_kept_and_named_in_both_modes(self):
        self.add_local_run(harness="opencode", offset_s=60)
        self.add_local_run(harness="gemini-cli", offset_s=70)
        self.add_local_run(harness="gemini-cli", offset_s=80)
        expected = (
            "3 rows come from experimental adapters (gemini-cli, opencode). validate rejects them "
            "until the adapter is verified, so a pull request with them stays red until a "
            "maintainer reconciles them by hand; see docs/verify-adapter.md."
        )
        for flags in ((), ("--write",)):
            with self.subTest(flags=flags):
                _, out, err = self.run_cli(*flags)
                self.assertIn(expected, err)
                self.assertNotIn("experimental", out)
        self.assertEqual(len(schema.read_rows(self.out / NAME)), 5)

    def test_no_experimental_warning_without_experimental_rows(self):
        for flags in ((), ("--write",)):
            with self.subTest(flags=flags):
                self.assertNotIn("experimental adapters", self.run_cli(*flags)[2])

    def test_the_submit_commands_start_with_validate(self):
        path = self.out / NAME
        self.assertEqual(share.git_commands(path)[0], f'agentpace validate "{self.out}"')
        err = self.run_cli("--write")[2]
        self.assertIn(f'  agentpace validate "{self.out}"\n  git add "{path}"', err)

    def test_a_dropped_session_is_absent_and_counted_in_both_modes(self):
        self.add_local_run(batch="d" * 12, offset_s=7)
        self.add_local_run(batch="d" * 12, offset_s=8)
        counted = "2 rows from 1 dropped session left out."
        _, printed, err = self.run_cli("--drop-session", "d" * 12)
        self.assertIn(counted, err)
        self.assertNotIn("d" * 12, printed)
        self.assertEqual(len(printed.splitlines()), 2)
        _, out, err = self.run_cli("--write", "--drop-session", "d" * 12)
        self.assertIn(counted, err)
        self.assertEqual(out, "")
        written = (self.out / NAME).read_text(encoding="utf-8")
        self.assertEqual(printed, written)
        self.assertNotIn("d" * 12, written)
        self.assertEqual(validate.validate([self.out]), [])

    def test_a_run_ref_and_a_bare_batch_both_drop_their_session(self):
        self.add_local_run(batch="d" * 12, offset_s=7)
        self.add_local_run(batch="c" * 12, offset_s=9)
        for flags in ((), ("--write",)):
            with self.subTest(flags=flags):
                _, _, err = self.run_cli(
                    *flags, "--drop-session", f"{'a' * 12}:5", "--drop-session", "d" * 12
                )
                self.assertIn("3 rows from 2 dropped sessions left out.", err)
        self.assertEqual([r["batch"] for r in schema.read_rows(self.out / NAME)], ["c" * 12])

    def test_the_file_name_spans_only_the_rows_shared(self):
        self.add_local_run(batch="d" * 12, week="2026-W20", offset_s=7)
        self.run_cli("--write", "--drop-session", "d" * 12)
        self.assertEqual([p.name for p in self.out.iterdir()], [NAME])

    def test_a_dropped_session_with_no_local_row_is_refused(self):
        for flags in ((), ("--write",)):
            with self.subTest(flags=flags):
                code, out, err = self.run_cli(*flags, "--drop-session", "f" * 12 + ":3")
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                refused = f"--drop-session names 1 session with no local run ({'f' * 12})"
                self.assertIn(refused, err)
                self.assertFalse(self.out.exists())

    def test_no_dropped_count_without_drop_session(self):
        for flags in ((), ("--write",)):
            with self.subTest(flags=flags):
                self.assertNotIn("dropped session", self.run_cli(*flags)[2])

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

    def test_a_file_that_is_not_utf8_stays_a_finding(self):
        path = self.root / NAME
        path.write_bytes(b"\xff\xfe\x00")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["validate", str(path)])
        self.assertEqual(code, 1)
        self.assertIn("file is not UTF-8", out.getvalue() + err.getvalue())
        self.assertNotIn("agentpace: ", err.getvalue())


class BadRowLines(Temp):
    """A rows file with a bad line is refused by file name and line number, never content."""

    SECRET = "do-not-print-this"

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)

    def estimate(self, path):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["estimate", "--example", "--data", str(path)])
        return code, out.getvalue(), err.getvalue()

    def assert_refused(self, bad_line, message):
        # A blank line before the bad one: the number counts every line in the file.
        good, later = schema.canonical(row()), schema.canonical(row(offset_s=5))
        path = self.write([good, "", bad_line, later])
        code, out, err = self.estimate(path)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, f"agentpace: {NAME}:3: {message}\n")
        self.assertNotIn("Traceback", err)
        self.assertNotIn(self.SECRET, err)

    def test_a_line_that_is_not_json_is_refused(self):
        self.assert_refused(f'{{"contributor": "{self.SECRET}"', "not valid JSON")

    def test_a_line_that_is_not_an_object_is_refused(self):
        self.assert_refused(json.dumps([self.SECRET]), "not a JSON object")

    def test_a_json_string_line_is_not_an_object(self):
        self.assert_refused(json.dumps(self.SECRET), "not a JSON object")

    def test_the_error_does_not_carry_the_decoder_message(self):
        path = self.write(["{broken"])
        with self.assertRaises(ValueError) as caught:
            schema.read_rows(path)
        self.assertEqual(str(caught.exception), f"{NAME}:1: not valid JSON")
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_a_file_that_is_not_utf8_is_refused_by_name(self):
        path = self.root / NAME
        path.write_bytes(b"\xff\xfe\x00")
        code, out, err = self.estimate(path)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err, f"agentpace: {NAME}: file is not UTF-8\n")


class ReplaceRefused(Temp):
    """`share --write` when another program holds the contribution file open."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.dict(os.environ, {"AGENTPACE_HOME": str(self.root / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        write_store(paths.local_runs(), [row(), row(offset_s=5)])
        self.out = self.root / "runs"
        self.path = self.out / NAME

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["share", "--out-dir", str(self.out), *args])
        return code, out.getvalue(), err.getvalue()

    def held_open(self):
        real = os.replace

        def replace(source, target):
            if Path(target) == self.path:
                raise PermissionError(13, "The process cannot access the file")
            real(source, target)

        return mock.patch.object(os, "replace", side_effect=replace)

    def test_a_held_file_is_left_as_it_was(self):
        self.assertEqual(self.run_cli("--write")[0], 0)
        first = self.path.read_bytes()
        stored = schema.read_rows(paths.local_runs())
        schema.write_rows(paths.local_runs(), [*stored, row(offset_s=50)])
        with self.held_open():
            code, out, err = self.run_cli("--write")
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [NAME])
        self.assertIn(
            f"agentpace: could not replace {NAME}: it is open in another program. Nothing was"
            " changed; close it and run share --write again\n",
            err,
        )
        for said in (out, err):
            self.assertNotIn("wrote", said)
            self.assertNotIn("added", said)
            self.assertNotIn("Traceback", said)

    def test_a_new_file_that_cannot_be_placed_leaves_nothing(self):
        with self.held_open():
            code, out, err = self.run_cli("--write")
        self.assertEqual(code, 2)
        self.assertEqual(list(self.out.iterdir()), [])
        self.assertIn(f"could not replace {NAME}", err)
        self.assertNotIn("wrote", out + err)

    def test_after_the_file_is_closed_the_append_keeps_the_prior_bytes(self):
        self.run_cli("--write")
        first = self.path.read_bytes()
        stored = schema.read_rows(paths.local_runs())
        schema.write_rows(paths.local_runs(), [*stored, row(offset_s=50)])
        with self.held_open():
            self.run_cli("--write")
        code, _, err = self.run_cli("--write")
        self.assertEqual(code, 0)
        self.assertTrue(self.path.read_bytes().startswith(first))
        self.assertEqual(len(schema.read_rows(self.path)), 3)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [NAME])
        self.assertIn("added", err)

    def test_a_corrupt_contribution_file_is_named_by_line(self):
        self.out.mkdir()
        self.path.write_text(schema.canonical(row(offset_s=90)) + "\n{broken\n", encoding="utf-8")
        code, _, err = self.run_cli("--write")
        self.assertEqual(code, 2)
        self.assertEqual(err, f"agentpace: {NAME}:2: not valid JSON\n")

    def test_an_out_dir_holding_a_file_that_is_not_utf8_is_refused_by_name(self):
        self.out.mkdir()
        self.path.write_bytes(b"\xff\xfe\x00")
        for flags in ((), ("--write",)):
            with self.subTest(flags=flags):
                code, out, err = self.run_cli(*flags)
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(err, f"agentpace: {NAME}: file is not UTF-8\n")
                self.assertEqual(self.path.read_bytes(), b"\xff\xfe\x00")

    def refuse(self, method, target):
        """Patch one `Path` method to fail with a permission error on `target` alone."""
        real = getattr(Path, method)

        def refused(path, *args, **kwargs):
            if path == target:
                raise PermissionError(13, "Permission denied")
            return real(path, *args, **kwargs)

        return mock.patch.object(Path, method, refused)

    def assert_write_refused(self, code, out, err):
        # The privacy notice comes first; the refusal is the last line and the only error.
        refusal = err.splitlines(keepends=True)[-1]
        self.assertEqual(code, 2)
        self.assertEqual(err.count("agentpace:"), 1)
        self.assertTrue(refusal.startswith("agentpace: could not write"), err)
        self.assertEqual(
            refusal,
            f"agentpace: could not write {NAME} in {self.out}: Permission denied. Nothing was"
            " changed; choose a writable --out-dir and run share --write again\n",
        )
        for said in (out, err):
            self.assertNotIn("wrote", said)
            self.assertNotIn("added", said)
            self.assertNotIn("Traceback", said)

    def test_an_unwritable_folder_leaves_a_new_file_unmade(self):
        self.out.mkdir()
        temporary = self.path.with_name(NAME + ".tmp")
        with self.refuse("write_bytes", temporary):
            code, out, err = self.run_cli("--write")
        self.assert_write_refused(code, out, err)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_an_unwritable_folder_leaves_an_existing_file_as_it_was(self):
        self.assertEqual(self.run_cli("--write")[0], 0)
        first = self.path.read_bytes()
        stored = schema.read_rows(paths.local_runs())
        schema.write_rows(paths.local_runs(), [*stored, row(offset_s=50)])
        temporary = self.path.with_name(NAME + ".tmp")
        with self.refuse("write_bytes", temporary):
            code, out, err = self.run_cli("--write")
        self.assert_write_refused(code, out, err)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), [NAME])

    def test_a_folder_that_cannot_be_made_is_refused_the_same_way(self):
        with self.refuse("mkdir", self.out):
            code, out, err = self.run_cli("--write")
        self.assert_write_refused(code, out, err)
        self.assertFalse(self.out.exists())


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
            markers = (
                "__import__",
                "importlib",
                "os.system",
                "os.popen",
                "exec(",
                "load_extension",
            )
            for marker in markers:
                self.assertNotIn(marker, text, file.name)


if __name__ == "__main__":
    unittest.main()

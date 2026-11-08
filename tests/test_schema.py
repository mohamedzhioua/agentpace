import json
import tempfile
import unittest
from pathlib import Path

from agentpace import paths, schema
from tests.synthetic import row


class CheckRow(unittest.TestCase):
    def test_a_complete_row_passes(self):
        self.assertEqual(schema.check_row(row()), [])

    def test_tokens_and_project_are_optional(self):
        minimal = row()
        del minimal["tokens"], minimal["project"]
        self.assertEqual(schema.check_row(minimal), [])

    def test_unknown_fields_are_rejected_without_echoing_the_name(self):
        errors = schema.check_row({**row(), "prompt": "x", "secret@example.com": "y"})
        self.assertEqual(len(errors), 2)
        self.assertNotIn("secret@example.com", " ".join(errors))

    def test_the_harness_enum_is_pinned(self):
        # Additive only: removing or renaming a value would invalidate shared rows.
        self.assertEqual(schema.enum("harness"), ("claude-code", "codex", "gemini-cli"))
        self.assertEqual(schema.check_row(row(harness="codex")), [])

    def test_each_rule_fires(self):
        cases = {
            "harness": "cursor",
            "model": "Has Spaces/and/paths",
            "task_kind": "chore",
            "duration_s": 0,
            "tool_calls": -1,
            "contributor": "not-hex",
            "week": "2026-W54",
            "offset_s": 1.5,
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                self.assertTrue(schema.check_row(row(**{field: value})))

    def test_booleans_are_not_integers(self):
        self.assertTrue(schema.check_row(row(retries=True)))

    def test_missing_required_field(self):
        incomplete = row()
        del incomplete["outcome"]
        self.assertEqual(schema.check_row(incomplete), ["outcome: missing"])

    def test_values_are_never_echoed(self):
        errors = " ".join(schema.check_row(row(model="me@example.com", week="secret")))
        self.assertNotIn("example.com", errors)
        self.assertNotIn("secret", errors)

    def test_non_object_row(self):
        self.assertEqual(schema.check_row(["list"]), ["row is not a JSON object"])


class Canonical(unittest.TestCase):
    def test_order_is_independent_of_input_order(self):
        shuffled = dict(reversed(list(row().items())))
        self.assertEqual(schema.canonical(shuffled), schema.canonical(row()))

    def test_dedupe_key_ignores_labels(self):
        relabeled = row(task_kind="bugfix", risk="high")
        self.assertEqual(schema.dedupe_key(relabeled), schema.dedupe_key(row()))

    def test_dedupe_key_separates_runs(self):
        self.assertNotEqual(schema.dedupe_key(row(offset_s=9)), schema.dedupe_key(row()))


class Files(unittest.TestCase):
    def test_round_trip_and_dedupe_across_files(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            schema.write_rows(directory / "a.jsonl", [row(), row(offset_s=5)])
            schema.write_rows(directory / "b.jsonl", [row()])
            self.assertEqual(len(schema.read_dataset([directory])), 2)

    def test_files_use_unix_newlines(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "a.jsonl"
            schema.write_rows(path, [row()])
            self.assertNotIn(b"\r", path.read_bytes())


class SchemaFile(unittest.TestCase):
    def test_every_required_field_is_defined(self):
        loaded = schema.load_schema()
        self.assertLessEqual(set(loaded["required"]), set(loaded["properties"]))

    def test_additional_properties_stay_forbidden(self):
        self.assertIs(schema.load_schema()["additionalProperties"], False)

    def test_every_string_field_is_constrained(self):
        for name, rule in schema.load_schema()["properties"].items():
            if rule.get("type") == "string":
                self.assertIn("pattern", rule, name)

    def test_is_valid_json(self):
        json.loads(paths.schema_file().read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

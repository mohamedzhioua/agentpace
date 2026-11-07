import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agentpace import claims, cli, paths, schema

REPO = Path(__file__).resolve().parent.parent


class Apply(unittest.TestCase):
    def test_replaces_marked_values(self):
        text = "We saw <!-- claim:runs -->1<!-- /claim --> runs."
        self.assertEqual(
            claims.apply(text, {"runs": "3,000"}),
            "We saw <!-- claim:runs -->3,000<!-- /claim --> runs.",
        )

    def test_leaves_unmarked_text_alone(self):
        self.assertEqual(claims.apply("plain 42", {"runs": "1"}), "plain 42")

    def test_is_idempotent(self):
        once = claims.apply("<!-- claim:a -->x<!-- /claim -->", {"a": "y"})
        self.assertEqual(claims.apply(once, {"a": "y"}), once)

    def test_an_unknown_claim_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "unknown claim"):
            claims.apply("<!-- claim:made_up -->1<!-- /claim -->", {})

    def test_spans_multiple_lines(self):
        text = "<!-- claim:t -->\nold\n<!-- /claim -->"
        self.assertEqual(
            claims.apply(text, {"t": "\nnew\n"}), "<!-- claim:t -->\nnew\n<!-- /claim -->"
        )


class Published(unittest.TestCase):
    """Every number in the published documents must equal what the data says."""

    def setUp(self):
        self.known = claims.values(
            schema.read_dataset([paths.seed_dir()]),
            json.loads((REPO / "docs" / "backtest.json").read_text(encoding="utf-8")),
            REPO / "examples" / "plan.json",
        )

    def test_documents_match_the_data(self):
        for name in ("README.md", "skills/agentpace/assets/portable-prompt.md"):
            text = (REPO / name).read_text(encoding="utf-8")
            self.assertEqual(claims.apply(text, self.known), text, name)

    def test_contributor_split_is_reported_honestly(self):
        self.assertIn("could not run", self.known["contributor_split"])

    def test_headline_numbers_carry_intervals(self):
        for name in ("task_p80", "plan_p50", "plan_p80", "plan_p90"):
            self.assertRegex(self.known[name], r"^[\d.]+% \(CI [\d.]+% to [\d.]+%\)$", name)
        for name in ("task_pinball", "plan_pinball"):
            self.assertRegex(self.known[name], r"^[\d.]+ \(CI [\d.]+ to [\d.]+\)$", name)

    def test_the_readme_shows_the_weekly_spread(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        for name in ("weekly_p80", "weekly_p80_row", "latest_week_p80", "locked_status"):
            self.assertIn(f"<!-- claim:{name} -->", text)

    def test_the_readme_cites_its_claims(self):
        text = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertGreater(text.count("<!-- claim:"), 15)

    def test_data_values_appear_only_inside_markers(self):
        # `project_top_harness` is a harness name such as "codex", which the README also uses
        # as an ordinary word, for example `--source codex`. It is not a number from the data.
        words = {"project_top_harness"}
        text = (REPO / "README.md").read_text(encoding="utf-8")
        unmarked = claims._MARKER.sub("", text)
        for name, value in self.known.items():
            if name in words or len(value) < 3:
                continue
            with self.subTest(claim=name):
                self.assertNotIn(value, unmarked)


class Command(unittest.TestCase):
    def test_check_fails_on_a_stale_number(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "doc.md"
            path.write_text("<!-- claim:runs -->1<!-- /claim -->", encoding="utf-8")
            args = [
                "claims",
                str(path),
                "--report",
                str(REPO / "docs" / "backtest.json"),
                "--data",
                str(paths.seed_dir()),
            ]
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([*args, "--check"]), 1)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(args), 0)
                self.assertEqual(cli.main([*args, "--check"]), 0)
            self.assertNotIn(">1<", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

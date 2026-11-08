import argparse
import dataclasses
import re
import unittest
from pathlib import Path

from agentpace import cli
from agentpace.estimate import PlanRange

REPO = Path(__file__).resolve().parent.parent
CLI_DOC = REPO / "docs" / "cli.md"


def subcommands():
    parser = cli.build_parser()
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    return sorted(name for action in actions for name in action.choices)


class CommandReference(unittest.TestCase):
    """docs/cli.md must not drift behind the parser or the JSON output."""

    @classmethod
    def setUpClass(cls):
        cls.text = CLI_DOC.read_text(encoding="utf-8")

    def test_the_parser_has_subcommands(self):
        self.assertGreater(len(subcommands()), 5)

    def test_every_subcommand_has_a_section(self):
        for name in subcommands():
            with self.subTest(command=name):
                self.assertRegex(self.text, rf"(?m)^## {re.escape(name)}$")
                self.assertIn(f"agentpace {name}", self.text)

    def test_every_json_key_is_documented(self):
        for field in dataclasses.fields(PlanRange):
            with self.subTest(key=field.name):
                self.assertIn(f"`{field.name}`", self.text)

    def test_every_source_is_named(self):
        for source in cli.ADAPTERS:
            with self.subTest(source=source):
                self.assertIn(f"`{source}`", self.text)

    def test_the_exit_codes_are_documented(self):
        for code in ("0", "1", "2"):
            self.assertRegex(self.text, rf"(?m)^\| {code} \|")

    def test_relative_links_resolve(self):
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", self.text):
            if "://" not in target:
                self.assertTrue((CLI_DOC.parent / target).exists(), target)


class VerificationGuide(unittest.TestCase):
    def test_contributing_links_the_guide(self):
        text = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
        self.assertIn("(docs/verify-adapter.md)", text)

    def test_contributors_validate_their_folder(self):
        for path in (REPO / "CONTRIBUTING.md", REPO / ".github" / "pull_request_template.md"):
            with self.subTest(path=path.name):
                self.assertIn("agentpace validate data/runs", path.read_text(encoding="utf-8"))

    def test_relative_links_resolve(self):
        page = REPO / "docs" / "verify-adapter.md"
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", page.read_text(encoding="utf-8")):
            if "://" not in target:
                self.assertTrue((page.parent / target).exists(), target)


if __name__ == "__main__":
    unittest.main()

import hashlib
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
STANDARD_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
EXPECTED = {"agentpace", "pace-estimate", "pace-log", "pace-share"}
# The session clock counts the gaps between runs up to the idle cap, so these claims are false.
BANNED_PHRASES = (
    "Not included in the total",
    "not in the total",
    "stay out of the total",
    "human part outside",
    "agent active time",
    "agent time, on the session clock",
)
# SHA-256 of lines 1-6 of each SKILL.md at v0.9.1 (6e3e433). Wording changes never touch them.
FRONTMATTER_SHA256 = {
    "agentpace": "3f02102b892709e3efe2c3fff82a667c7260aad4f90a9303935f347b498a4a78",
    "pace-estimate": "2a4b537c5bc641c361d5e5a28adfb9b012271f55648f75ae9ee209c6aa9659c1",
    "pace-log": "54ce957cb94429ad5e5231de7518cc78d4c6366d918781930ee56fa865292c36",
    "pace-share": "88e3a79cf3c323e431ddcc46d4aa848332b6e72cc2e9660d7eafb15e8ac4abc4",
}


def frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, f"{path} has no frontmatter"
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class Quickstart(unittest.TestCase):
    def test_the_readme_plan_is_the_example_plan(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        match = re.search(r"\(examples/plan\.json\).*?\n```json\n(.*?)\n```", readme, re.DOTALL)
        example = (REPO / "examples" / "plan.json").read_text(encoding="utf-8")
        self.assertEqual(match.group(1), example.strip())

    def test_a_command_is_on_the_first_screen(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(line.startswith("uvx agentpace") for line in readme[:25]))

    def test_every_skill_is_in_the_readme_table(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        self.assertIn("| Skill | Say this | You get |", readme)
        for name in EXPECTED:
            self.assertIn(f"(skills/{name}/SKILL.md)", readme)

    def test_every_skill_says_when_to_use_it_and_what_it_produces(self):
        for path in sorted(SKILLS.glob("*/SKILL.md")):
            with self.subTest(skill=path.parent.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("**Use when**", text)
                self.assertIn("**Produces**", text)

    def test_the_quickstart_needs_no_clone(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        command = "uvx"
        self.assertIn(f"{command} agentpace estimate --example", readme)

    def test_the_example_plan_ships_in_the_package(self):
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"examples/plan.json" = "agentpace/_bundled/example-plan.json"', pyproject)


class Repository(unittest.TestCase):
    def test_the_social_preview_is_the_size_github_asks_for(self):
        data = (REPO / "docs" / "assets" / "social-preview.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        self.assertEqual((width, height), (1280, 640))

    def test_current_docs_and_source_carry_no_banned_phrase(self):
        # Claim blocks are generated from the CLI, whose output is checked in test_cli.
        claim = re.compile(r"<!-- claim:([a-z0-9_]+) -->(.*?)<!-- /claim -->", re.DOTALL)
        paths = [
            REPO / "README.md",
            REPO / "data" / "README.md",
            REPO / "docs" / "cli.md",
            *sorted((REPO / "src" / "agentpace").rglob("*.py")),
        ]
        for path in paths:
            text = claim.sub("", path.read_text(encoding="utf-8")).lower()
            for phrase in BANNED_PHRASES:
                with self.subTest(path=str(path.relative_to(REPO)), phrase=phrase):
                    self.assertNotIn(phrase.lower(), text)

    def test_the_citation_version_matches_the_package(self):
        from agentpace import __version__

        text = (REPO / "CITATION.cff").read_text(encoding="utf-8")
        self.assertIn(f"version: {__version__}", text)


class Skills(unittest.TestCase):
    def skill_files(self):
        return sorted(SKILLS.glob("*/SKILL.md"))

    def test_the_four_skills_exist(self):
        self.assertEqual({p.parent.name for p in self.skill_files()}, EXPECTED)

    def test_frontmatter_follows_the_open_standard(self):
        for path in self.skill_files():
            with self.subTest(skill=path.parent.name):
                fields = frontmatter(path)
                self.assertLessEqual(set(fields), STANDARD_FIELDS)
                self.assertEqual(fields["name"], path.parent.name)
                self.assertRegex(fields["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
                self.assertLessEqual(len(fields["name"]), 64)
                self.assertTrue(1 <= len(fields["description"]) <= 1024)
                self.assertLessEqual(len(fields.get("compatibility", "")), 500)

    def test_bodies_stay_short(self):
        for path in self.skill_files():
            self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 500)

    def test_relative_links_resolve(self):
        for path in self.skill_files():
            for target in re.findall(r"\]\(([^)#]+)\)", path.read_text(encoding="utf-8")):
                if "://" not in target:
                    self.assertTrue((path.parent / target).exists(), target)

    def test_no_skill_installs_or_pushes_on_its_own(self):
        for path in self.skill_files():
            text = path.read_text(encoding="utf-8")
            if "uv tool install" in text:
                self.assertRegex(text, r"(user's call|without being asked)")

    def test_the_logging_hook_is_opt_in(self):
        text = (SKILLS / "pace-log" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn('"command": "agentpace ingest --hook"', text)
        self.assertIn("add it themselves. Do not edit their settings for them", text)

    def test_share_stops_for_consent(self):
        text = (SKILLS / "pace-share" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--dry-run", text)
        self.assertIn("Stop and ask", text)
        self.assertIn("Never run the printed git commands", text)

    def test_estimates_are_always_ranges(self):
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("never give a single number", text)

    def test_the_skill_triggers_without_agents_being_named(self):
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        description = text.split("description:", 1)[1].split("\n", 1)[0]
        self.assertIn("even when agents are not mentioned", description)
        self.assertIn("days, weeks or months", description)
        self.assertNotIn("without agents", description)
        self.assertIn("Re-answer a human quote", text)

    def test_the_skill_passes_a_quote_through_and_relays_the_quoted_line(self):
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        section = text.split("## Re-answer a human quote", 1)[1].split("\n## ", 1)[0]
        self.assertIn('agentpace estimate plan.json --quoted "3 months"', section)
        self.assertIn("verbatim", section)
        self.assertIn("Relay that line as printed", section)
        self.assertIn("`Quoted:`", section)
        self.assertIn("never compare it with the range", section)

    def test_skills_and_prompt_never_derive_the_human_part(self):
        for path in (
            SKILLS / "agentpace" / "SKILL.md",
            SKILLS / "pace-estimate" / "SKILL.md",
            SKILLS / "agentpace" / "assets" / "portable-prompt.md",
        ):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("Human part" if "prompt" in path.name else "human part", text)
                self.assertRegex(text, r"[Dd]o not derive")

    def test_frontmatter_is_unchanged_from_main(self):
        for path in self.skill_files():
            with self.subTest(skill=path.parent.name):
                head = "".join(path.read_text(encoding="utf-8").splitlines(keepends=True)[:6])
                digest = hashlib.sha256(head.encode("utf-8")).hexdigest()
                self.assertEqual(digest, FRONTMATTER_SHA256[path.parent.name])

    def test_skill_texts_carry_no_banned_phrase(self):
        paths = sorted(path for path in SKILLS.rglob("*") if path.is_file())
        self.assertTrue(paths)
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for phrase in BANNED_PHRASES:
                with self.subTest(path=str(path.relative_to(SKILLS)), phrase=phrase):
                    self.assertNotIn(phrase.lower(), text.lower())

    def test_the_skills_relay_the_session_clock_line(self):
        for name in ("agentpace", "pace-estimate"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("the session-clock line and the human part line", text)

    def test_portable_prompt_does_not_sum_quantiles(self):
        text = (SKILLS / "agentpace" / "assets" / "portable-prompt.md").read_text(encoding="utf-8")
        self.assertNotIn("P90 values for", text)
        self.assertIn("<!-- claim:plan_width80 -->", text)
        self.assertIn("<!-- claim:plan_width -->", text)
        self.assertIn("rule of thumb", text)

    def test_portable_prompt_exists_and_carries_numbers(self):
        text = (SKILLS / "agentpace" / "assets" / "portable-prompt.md").read_text(encoding="utf-8")
        self.assertIn("<!-- claim:kind_table -->", text)
        self.assertIn("P90", text)


if __name__ == "__main__":
    unittest.main()

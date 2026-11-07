import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILLS = REPO / "skills"
STANDARD_FIELDS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
EXPECTED = {"agentpace", "pace-estimate", "pace-log", "pace-share"}


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
        match = re.search(r"<<'EOF'\n(.*?)\nEOF", readme, re.DOTALL)
        example = (REPO / "examples" / "plan.json").read_text(encoding="utf-8")
        self.assertEqual(match.group(1), example.strip())

    def test_a_command_is_on_the_first_screen(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(line.startswith("uvx --from") for line in readme[:25]))

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
        self.assertIn("agentpace estimate - <<'EOF'", readme)


class Repository(unittest.TestCase):
    def test_the_social_preview_is_the_size_github_asks_for(self):
        data = (REPO / "docs" / "assets" / "social-preview.png").read_bytes()
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        self.assertEqual((width, height), (1280, 640))

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

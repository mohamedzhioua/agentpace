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

    def test_share_stops_for_consent(self):
        text = (SKILLS / "pace-share" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("--dry-run", text)
        self.assertIn("Stop and ask", text)
        self.assertIn("Never run the printed git commands", text)

    def test_estimates_are_always_ranges(self):
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("never give a single number", text)

    def test_portable_prompt_exists_and_carries_numbers(self):
        text = (SKILLS / "agentpace" / "assets" / "portable-prompt.md").read_text(encoding="utf-8")
        self.assertIn("<!-- claim:kind_table -->", text)
        self.assertIn("P90", text)


if __name__ == "__main__":
    unittest.main()

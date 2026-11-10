import hashlib
import json
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
            REPO / "docs" / "releasing.md",
            REPO / "docs" / "skill-smoke.md",
            REPO / "docs" / "verify-adapter.md",
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
        self.assertIn('agentpace estimate <file> --quoted "3 months"', section)
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
        self.assertIn("<!-- claim:cell_table -->", text)
        self.assertIn("P90", text)

    def test_sample_plans_parse_and_leave_risk_out_except_research(self):
        from agentpace import estimate as est

        for name in ("agentpace", "pace-estimate"):
            text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
            blocks = re.findall(r"```json\n(.*?)\n\s*```", text, re.DOTALL)
            self.assertTrue(blocks, name)
            for block in blocks:
                with self.subTest(skill=name):
                    document = json.loads(block)
                    est.tasks_from_json(document)
                    for task in document["tasks"]:
                        if "risk" in task:
                            self.assertEqual((task["kind"], task["risk"]), ("other", "low"))

    def test_the_skill_no_longer_asks_for_a_risk(self):
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("a `kind` and a `risk`", text)

    def test_the_absent_cli_path_offers_the_portable_prompt(self):
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        section = text.split("## Check the tool", 1)[1].split("\n## ", 1)[0]
        self.assertIn("portable-prompt.md", section)
        self.assertIn("rule of thumb", section)

    def test_share_says_what_nothing_new_means(self):
        text = (SKILLS / "pace-share" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("nothing new to share", text)

    def test_the_skills_paste_the_cli_lines_in_one_block(self):
        for name in ("agentpace", "pace-estimate"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("one fenced code block, tagged `text`", text)
                self.assertIn("copied character for character", text)
                self.assertIn("the whole `Warnings:` block, heading included", text)
                self.assertIn("Nothing inside the block is reworded", text)

    def test_the_block_holds_the_idle_cap_line_in_cli_order(self):
        idle_cap = "the `Idle cap:` line as printed when there is one"
        chain = (
            "the `Quoted:` line as printed when there is one, "
            f"{idle_cap}, "
            "the session-clock line and the human part line"
        )
        for name in ("agentpace", "pace-estimate"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                start = text.index("one fenced code block, tagged `text`")
                end = text.index("Nothing inside the block is reworded", start)
                self.assertIn(chain, text[start:end])
                self.assertEqual(text.count(idle_cap), 1)

    def test_the_skills_name_the_total_on_the_session_clock_and_relay_refusals(self):
        for name in ("agentpace", "pace-estimate"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn("the plan total on the session clock", text)
                self.assertIn("never as hours of agent time", text)
                self.assertIn("relay that line as printed and stop", text)

    def test_the_skill_uses_a_plan_file_only_when_it_is_the_plan(self):
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("is not evidence that it describes the request", text)
        self.assertIn("never overwrite it", text)
        self.assertNotIn("the only agent time is the measured range", text)
        self.assertNotIn("agentpace estimate plan.json", text)
        self.assertIn("`<file>` is the file you wrote, or the one the user named", text)

    def test_pace_estimate_asks_when_there_is_no_input(self):
        text = (SKILLS / "pace-estimate" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("ask which plan or task list to estimate", text)

    def test_pace_log_leaves_the_idle_cap_alone(self):
        text = (SKILLS / "pace-log" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Leave `--idle-cap` alone", text)
        self.assertIn("refuses a different one for that store", text)
        self.assertNotIn("refuses any other", text)

    def test_pace_share_leaves_a_refused_store_to_the_user(self):
        text = (SKILLS / "pace-share" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("rebuilding the store is the user's call", text)
        self.assertIn("If it refuses instead of printing rows", text)
        self.assertIn("do not go on to `--write`", text)
        self.assertIn("a record that cannot be read", text)

    def test_the_smoke_check_names_eight_prompts_and_the_idle_cap_line(self):
        text = (REPO / "docs" / "skill-smoke.md").read_text(encoding="utf-8")
        section = text.split("## The prompts", 1)[1].split("\n## ", 1)[0]
        labels = re.findall(r"^- \*\*(\d+b?)\. ", section, re.MULTILINE)
        self.assertEqual(labels, ["1", "3", "4", "4b", "5", "6", "7", "8"])
        self.assertIn("the `Idle cap:` line when the CLI printed one", text)
        self.assertIn("Idle cap:", section)
        items = dict(re.findall(r"^- \*\*(\d+b?)\. (.*)$", section, re.MULTILINE))
        self.assertIn("names the file it wrote and not `plan.json`", items["5"])
        self.assertIn("unchanged byte for byte", items["5"])
        self.assertIn("--idle-cap 600", items["7"])
        self.assertIn(
            "`Idle cap: 900 s (bundled seed), 600 s (your local store). "
            "This range pools rows on different clocks.`",
            items["7"],
        )
        self.assertIn("nothing in the home changes", items["7"])
        self.assertIn("not published rows", items["8"])
        self.assertIn("agentpace: runs.jsonl has no recorded idle cap", items["8"])
        self.assertIn("no `--write`", items["8"])

    def test_the_skills_never_say_the_total_covers_agent_time_only(self):
        for name in ("agentpace", "pace-estimate"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(
                    "is, or covers, agent time or agent work only, "
                    "not even to set it against the human part",
                    text,
                )
                self.assertIn('own words, "the agent time", stay inside the block', text)
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        report = text.split("## Report the result", 1)[1].split("\n## ", 1)[0]
        self.assertIn("agent runs and the gaps between them, each gap up to the idle cap", report)
        self.assertIn("do not derive a number for it from the total", report)
        self.assertNotIn("from the agent time", report)
        estimate = (SKILLS / "pace-estimate" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Do not derive a number for the human part from the total", estimate)
        self.assertNotIn("from the agent time", estimate)
        smoke = (REPO / "docs" / "skill-smoke.md").read_text(encoding="utf-8")
        self.assertIn(
            "or say it covers, agent time, agent work, active time, working time or effort",
            smoke,
        )


class Marketplace(unittest.TestCase):
    def setUp(self):
        path = REPO / ".claude-plugin" / "marketplace.json"
        self.manifest = json.loads(path.read_text(encoding="utf-8"))

    def entry(self):
        plugins = self.manifest["plugins"]
        self.assertEqual(len(plugins), 1)
        return plugins[0]

    def test_the_marketplace_names_itself_and_its_owner(self):
        self.assertEqual(self.manifest["name"], "agentpace")
        self.assertEqual(self.manifest["owner"]["name"], "mohamedzhioua")

    def test_it_lists_one_plugin_served_from_the_repository_root(self):
        entry = self.entry()
        self.assertEqual(entry["name"], "agentpace")
        self.assertEqual(entry["source"], "./")

    def test_the_entry_lists_no_components_so_every_skill_loads(self):
        entry = self.entry()
        for field in ("skills", "hooks", "commands", "agents", "mcpServers"):
            with self.subTest(field=field):
                self.assertNotIn(field, entry)

    def test_the_source_holds_the_skills_folder(self):
        entry = self.entry()
        self.assertEqual((REPO / entry["source"] / "skills").resolve(), SKILLS.resolve())

    def test_no_hooks_folder_or_plugin_manifest_ships(self):
        self.assertFalse((REPO / "hooks").exists())
        self.assertFalse((REPO / ".claude-plugin" / "plugin.json").exists())

    def test_the_descriptions_are_short_and_carry_no_banned_phrase(self):
        descriptions = {
            "marketplace": self.manifest["description"],
            "plugin": self.entry()["description"],
        }
        for level, description in descriptions.items():
            with self.subTest(level=level):
                self.assertTrue(1 <= len(description) <= 300)
                for phrase in BANNED_PHRASES:
                    with self.subTest(level=level, phrase=phrase):
                        self.assertNotIn(phrase.lower(), description.lower())


class PluginRoute(unittest.TestCase):
    def test_the_readme_gives_the_one_line_plugin_install(self):
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        command = "/plugin install agentpace --marketplace mohamedzhioua/agentpace"
        self.assertEqual(readme.count(command), 1)
        self.assertIn("2.1.275 or later", readme)
        self.assertIn("Install one route, not both, or each skill loads twice", readme)
        self.assertNotIn("/plugin marketplace add", readme)

    def test_the_guidance_skill_names_the_plugin_command(self):
        text = (SKILLS / "agentpace" / "SKILL.md").read_text(encoding="utf-8")
        body = text.split("\n---\n", 1)[1]
        self.assertIn("`/agentpace:pace-estimate` when installed as a Claude Code plugin", body)

    def test_each_command_skill_answers_to_both_slash_names(self):
        for name in ("pace-estimate", "pace-log", "pace-share"):
            with self.subTest(skill=name):
                text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
                line = next((ln for ln in text.splitlines() if ln.startswith("**Use when**")), "")
                self.assertIn(f"`/{name}`", line)
                self.assertIn(f"`/agentpace:{name}`", line)

    def test_design_records_the_real_install(self):
        text = (REPO / "docs" / "DESIGN.md").read_text(encoding="utf-8")
        section = text.split("## 8. Skill and install", 1)[1].split("\n## ", 1)[0]
        self.assertIn("**Claude Code plugin.**", section)
        self.assertIn("Claude Code 2.1.319", section)


if __name__ == "__main__":
    unittest.main()

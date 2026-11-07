import unittest

from agentpace import classify


class Kind(unittest.TestCase):
    def test_agent_types_map_to_kinds(self):
        cases = {
            "reviewer": "review",
            "security": "review",
            "docs": "docs",
            "qa": "tests",
            "debugger": "bugfix",
            "db-migration": "migration",
            "implementer": "feature",
            "researcher": "other",
            "general-purpose": "other",
            None: "other",
        }
        for agent, kind in cases.items():
            with self.subTest(agent=agent):
                self.assertEqual(classify.classify_kind(agent), kind)

    def test_plugin_prefix_is_ignored(self):
        self.assertEqual(classify.classify_kind("some-plugin:reviewer"), "review")

    def test_kind_map_wins(self):
        mapping = {"reviewer": "docs"}
        self.assertEqual(classify.classify_kind("reviewer", mapping), "docs")


class Risk(unittest.TestCase):
    def test_migrations_are_high(self):
        self.assertEqual(classify.classify_risk("db-migration", "migration"), "high")

    def test_read_only_agents_are_low(self):
        for agent in ("reviewer", "researcher", "Explore"):
            self.assertEqual(classify.classify_risk(agent, "review"), "low")

    def test_everything_else_is_normal(self):
        self.assertEqual(classify.classify_risk("implementer", "feature"), "normal")
        self.assertEqual(classify.classify_risk(None, "other"), "normal")


class Size(unittest.TestCase):
    def test_thresholds(self):
        cases = {0: "na", 1: "S", 3: "S", 4: "M", 10: "M", 11: "L", 30: "L", 31: "XL"}
        for edits, bucket in cases.items():
            with self.subTest(edits=edits):
                self.assertEqual(classify.size_bucket(edits), bucket)


if __name__ == "__main__":
    unittest.main()

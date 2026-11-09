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


# Every agent type in DESIGN section 4's table, with None for a Codex turn with no agent type.
DESIGN_AGENT_TYPES = (
    "reviewer",
    "security",
    "performance",
    "architect",
    "verifier",
    "researcher",
    "Explore",
    "web-fetch",
    "docs",
    "qa",
    "debugger",
    "db-migration",
    "implementer",
    "general-purpose",
    None,
)
RESEARCH = ("researcher", "Explore", "web-fetch")


class RecordedRisk(unittest.TestCase):
    """A plan's risk is read as the category the classifier records, so the table that says
    which pairs a plan may name has to stay in step with the classifier."""

    def test_the_classifier_only_records_pairs_the_table_allows(self):
        for agent in DESIGN_AGENT_TYPES:
            kind = classify.classify_kind(agent)
            risk = classify.classify_risk(agent, kind)
            with self.subTest(agent=agent, kind=kind, risk=risk):
                self.assertIn(risk, classify.RECORDED_RISKS.get(kind, {"normal"}))
                if agent in RESEARCH:
                    self.assertEqual((kind, risk), ("other", "low"))
                else:
                    self.assertEqual(risk, classify.kind_default_risk(kind))

    def test_the_kind_default_is_not_the_classifier_without_an_agent_type(self):
        self.assertEqual(classify.kind_default_risk("review"), "low")
        self.assertEqual(classify.classify_risk(None, "review"), "normal")
        self.assertEqual(classify.kind_default_risk("migration"), "high")
        for kind in ("feature", "bugfix", "refactor", "tests", "docs", "other"):
            with self.subTest(kind=kind):
                self.assertEqual(classify.kind_default_risk(kind), "normal")

    def test_a_written_risk_is_kept_only_where_rows_can_record_it(self):
        cases = {
            ("feature", "high"): "normal",
            ("feature", "low"): "normal",
            ("feature", "normal"): "normal",
            ("feature", None): "normal",
            ("review", "normal"): "low",
            ("review", "high"): "low",
            ("review", None): "low",
            ("migration", "normal"): "high",
            ("migration", None): "high",
            ("other", "low"): "low",
            ("other", "normal"): "normal",
            ("other", "high"): "normal",
            ("other", None): "normal",
            ("bugfix", "high"): "normal",
        }
        for (kind, risk), expected in cases.items():
            with self.subTest(kind=kind, risk=risk):
                self.assertEqual(classify.recorded_risk(kind, risk), expected)


class Size(unittest.TestCase):
    def test_thresholds(self):
        cases = {0: "na", 1: "S", 3: "S", 4: "M", 10: "M", 11: "L", 30: "L", 31: "XL"}
        for edits, bucket in cases.items():
            with self.subTest(edits=edits):
                self.assertEqual(classify.size_bucket(edits), bucket)


if __name__ == "__main__":
    unittest.main()

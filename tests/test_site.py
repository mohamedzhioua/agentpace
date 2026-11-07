import json
import re
import unittest
from pathlib import Path

from agentpace import site
from tests.synthetic import row

REPO = Path(__file__).resolve().parent.parent
PAGE = REPO / "docs" / "index.html"


def report_stub():
    metrics = {"coverage80": 80.0, "coverage90": 90.0, "mdape": 70.0}
    return {
        "selection": {"winner": "shrunk"},
        "splits": {"time": {"tracks": {"pre-run": {"shrunk": {"session": metrics}}}}},
        "plans": {"sessions": 5, "results": {"agentpace": metrics}},
    }


class Payload(unittest.TestCase):
    def rows(self):
        rows = [row(offset_s=i, duration_s=100 + i) for i in range(20)]
        rows.append(row(task_kind="docs", offset_s=99, duration_s=30))
        rows.append(row(outcome="failed", offset_s=100, duration_s=9999))
        return rows

    def test_cells_are_model_by_kind_by_risk(self):
        cells = site.payload(self.rows(), report_stub())["cells"]
        self.assertEqual(
            {(c["model"], c["kind"], c["risk"]) for c in cells},
            {("claude-opus-5-5", "feature", "normal"), ("claude-opus-5-5", "docs", "normal")},
        )

    def test_each_cell_has_median_p75_p90_and_n(self):
        cell = site.payload(self.rows(), report_stub())["cells"][1]
        self.assertEqual(set(cell), {"model", "kind", "risk", "n", "p50", "p75", "p90"})
        self.assertEqual(cell["n"], 20)

    def test_failed_runs_are_left_out(self):
        data = site.payload(self.rows(), report_stub())
        self.assertLess(data["overall"]["p90"], 200)
        self.assertEqual(data["completed"], 21)

    def test_histogram_counts_every_completed_run(self):
        data = site.payload(self.rows(), report_stub())
        self.assertEqual(sum(b["n"] for b in data["histogram"]), data["completed"])

    def test_no_row_level_fields_reach_the_page(self):
        text = json.dumps(site.payload(self.rows(), report_stub()))
        for field in ("contributor", "batch", "project", "offset_s", "0123456789abcdef"):
            self.assertNotIn(field, text.replace('"contributors"', ""))


class Embedding(unittest.TestCase):
    def test_replaces_only_the_data_block(self):
        html = f"<p>a</p>{site.START}{{}}{site.END}<p>b</p>"
        result = site.embed(html, {"x": 1})
        self.assertEqual(result, f'<p>a</p>{site.START}{{"x":1}}{site.END}<p>b</p>')

    def test_is_idempotent(self):
        html = f"{site.START}{{}}{site.END}"
        once = site.embed(html, {"x": 1})
        self.assertEqual(site.embed(once, {"x": 1}), once)

    def test_a_closing_tag_in_data_cannot_break_out(self):
        result = site.embed(f"{site.START}{{}}{site.END}", {"x": "</script><b>"})
        self.assertNotIn("</script><b>", result)


class Page(unittest.TestCase):
    """The page must stay a single self-contained file."""

    @classmethod
    def setUpClass(cls):
        cls.html = PAGE.read_text(encoding="utf-8")

    def test_loads_nothing_from_anywhere(self):
        self.assertIsNone(re.search(r"<(script|img|iframe)[^>]+src=", self.html))
        self.assertIsNone(re.search(r"<link[^>]+href=", self.html))
        self.assertNotIn("@import", self.html)
        self.assertNotIn("url(", self.html)

    def test_makes_no_requests(self):
        for call in ("fetch(", "XMLHttpRequest", "WebSocket", "sendBeacon", "EventSource"):
            self.assertNotIn(call, self.html)

    def test_uses_no_browser_storage(self):
        for store in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            self.assertNotIn(store, self.html)

    def test_greys_cells_under_ten_runs(self):
        self.assertIn("cell.n < 10", self.html)

    def test_shows_the_three_statistics_and_n(self):
        for label in ('label: "n"', 'label: "Median"', 'label: "P75"', 'label: "P90"'):
            self.assertIn(label, self.html)

    def test_warns_against_a_speed_ranking(self):
        self.assertIn("not a model speed ranking", self.html)

    def test_embedded_data_parses(self):
        start = self.html.index(site.START) + len(site.START)
        data = json.loads(self.html[start : self.html.index(site.END, start)])
        self.assertGreater(len(data["cells"]), 0)

    def test_jekyll_is_disabled(self):
        self.assertTrue((REPO / "docs" / ".nojekyll").exists())


if __name__ == "__main__":
    unittest.main()

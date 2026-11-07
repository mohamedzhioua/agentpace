"""Small static SVG charts, written by hand so the CLI stays standard-library only.

Colors come from a palette checked for color-vision safety, and the charts follow the reader's
light or dark theme. Every mark carries a `<title>` for hover, and every number also appears in a
table in `docs/backtest.md`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from xml.sax.saxutils import escape

from agentpace.render import duration

_STYLE = """
<style>
  svg {
    --text: #0b0b0b; --muted: #52514e; --grid: #e4e3df;
    --primary: #2a78d6; --second: #eb6834;
  }
  @media (prefers-color-scheme: dark) {
    svg {
      --text: #f0efec; --muted: #c3c2b7; --grid: #3a3a37;
      --primary: #3987e5; --second: #d95926;
    }
  }
  text { font: 13px system-ui, -apple-system, "Segoe UI", sans-serif; fill: var(--muted); }
  .title { font-size: 15px; font-weight: 600; fill: var(--text); }
  .label { fill: var(--text); }
  .grid { stroke: var(--grid); stroke-width: 1; }
  .goal { stroke: var(--text); stroke-width: 1.5; stroke-dasharray: 4 3; }
  .range { stroke: var(--primary); stroke-width: 8; stroke-linecap: round; }
  .dot { fill: var(--primary); stroke: var(--surface, none); }
  .second { fill: var(--second); }
  .tick { stroke: #ffffff; stroke-width: 2; }
</style>
"""

_TICKS = (1, 10, 60, 600, 3600, 36000, 86400)


@dataclass(frozen=True)
class RangeBar:
    label: str
    n: int
    p50: float
    p80: float
    p90: float


def _open(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" role="img" aria-label="{escape(title)}">',
        f"<title>{escape(title)}</title>",
        _STYLE,
    ]


def range_chart(title: str, bars: Sequence[RangeBar]) -> str:
    """Horizontal P50 to P90 bars on a log axis: durations span several orders of magnitude."""
    left, right, top, row = 190, 150, 62, 36
    width = 860
    height = top + row * len(bars) + 34
    low = min(bar.p50 for bar in bars) / 1.5
    high = max(bar.p90 for bar in bars) * 1.1

    def x(value: float) -> float:
        span = math.log(high) - math.log(low)
        return left + (math.log(value) - math.log(low)) / span * (width - left - right)

    parts = _open(width, height, title)
    parts.append(f'<text class="title" x="0" y="20">{escape(title)}</text>')
    parts.append(
        '<text x="0" y="42">Bar runs from P50 to P90. The white tick marks P80. '
        "Log scale, seconds to hours.</text>"
    )
    for tick in _TICKS:
        if low <= tick <= high:
            position = x(tick)
            parts.append(
                f'<line class="grid" x1="{position:.1f}" y1="{top - 6}" '
                f'x2="{position:.1f}" y2="{height - 26}"/>'
            )
            parts.append(
                f'<text x="{position:.1f}" y="{height - 8}" text-anchor="middle">'
                f"{duration(tick)}</text>"
            )
    for index, bar in enumerate(bars):
        y = top + row * index + row / 2
        parts.append(
            f'<text class="label" x="0" y="{y + 4:.1f}">{escape(bar.label)} '
            f"<tspan>n={bar.n:,}</tspan></text>"
        )
        parts.append(
            f"<g><title>{escape(bar.label)}: P50 {duration(bar.p50)}, P80 {duration(bar.p80)}, "
            f"P90 {duration(bar.p90)} (n={bar.n:,})</title>"
            f'<line class="range" x1="{x(bar.p50):.1f}" y1="{y:.1f}" '
            f'x2="{x(bar.p90):.1f}" y2="{y:.1f}"/>'
            f'<line class="tick" x1="{x(bar.p80):.1f}" y1="{y - 4:.1f}" '
            f'x2="{x(bar.p80):.1f}" y2="{y + 4:.1f}"/></g>'
        )
        parts.append(
            f'<text x="{x(bar.p90) + 12:.1f}" y="{y + 4:.1f}">'
            f"{duration(bar.p50)} to {duration(bar.p90)}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def coverage_chart(title: str, points: Sequence[tuple[str, float, bool]], goal: float) -> str:
    """How often the actual run landed at or under the P80 forecast, for each method."""
    left, right, top, row = 150, 70, 78, 30
    width = 760
    height = top + row * len(points) + 34

    def x(value: float) -> float:
        return left + value / 100 * (width - left - right)

    parts = _open(width, height, title)
    parts.append(f'<text class="title" x="0" y="20">{escape(title)}</text>')
    parts.append(
        '<circle class="dot" cx="6" cy="38" r="5"/><text x="18" y="42">agentpace candidate</text>'
        '<rect class="second" x="170" y="33" width="10" height="10"/>'
        '<text x="188" y="42">baseline</text>'
    )
    for tick in (0, 20, 40, 60, 80, 100):
        position = x(tick)
        parts.append(
            f'<line class="grid" x1="{position:.1f}" y1="{top - 6}" '
            f'x2="{position:.1f}" y2="{height - 26}"/>'
            f'<text x="{position:.1f}" y="{height - 8}" text-anchor="middle">{tick}%</text>'
        )
    parts.append(
        f'<line class="goal" x1="{x(goal):.1f}" y1="{top - 10}" '
        f'x2="{x(goal):.1f}" y2="{height - 26}"/>'
        f'<text class="label" x="{x(goal):.1f}" y="{top - 14}" text-anchor="middle">'
        f"target {goal:.0f}%</text>"
    )
    for index, (name, value, is_baseline) in enumerate(points):
        y = top + row * index + row / 2
        position = x(value)
        parts.append(f'<text class="label" x="0" y="{y + 4:.1f}">{escape(name)}</text>')
        tip = f"<title>{escape(name)}: {value:.1f}%</title>"
        if is_baseline:
            parts.append(
                f'<g>{tip}<rect class="second" x="{position - 5:.1f}" y="{y - 5:.1f}" '
                f'width="10" height="10"/></g>'
            )
        else:
            parts.append(
                f'<g>{tip}<circle class="dot" cx="{position:.1f}" cy="{y:.1f}" r="5"/></g>'
            )
        anchor, shift = ("end", -10) if value > 88 else ("start", 10)
        parts.append(
            f'<text x="{position + shift:.1f}" y="{y + 4:.1f}" text-anchor="{anchor}">'
            f"{value:.1f}%</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"

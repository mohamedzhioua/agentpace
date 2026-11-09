"""Deterministic task classification. No LLM calls and no text matching.

Metadata cannot separate feature, bugfix and refactor work, so the agent type is the only signal,
and it is known before a run starts. Tool-mix heuristics are deliberately absent: they use
post-run information and would dress up guesses as classification. User labels always win.
"""

from __future__ import annotations

from collections.abc import Mapping

_READ_ONLY_REVIEW = frozenset({"architect", "performance", "reviewer", "security", "verifier"})
_RESEARCH = frozenset({"Explore", "researcher", "web-fetch"})
_KINDS = {
    "db-migration": "migration",
    "debugger": "bugfix",
    "docs": "docs",
    "implementer": "feature",
    "qa": "tests",
}


def strip_plugin(agent_type: str | None) -> str:
    """`some-plugin:reviewer` becomes `reviewer`."""
    return (agent_type or "").rsplit(":", 1)[-1]


def classify_kind(agent_type: str | None, kind_map: Mapping[str, str] | None = None) -> str:
    name = strip_plugin(agent_type)
    if kind_map and name in kind_map:
        return kind_map[name]
    if name in _READ_ONLY_REVIEW:
        return "review"
    return _KINDS.get(name, "other")


def classify_risk(agent_type: str | None, kind: str) -> str:
    name = strip_plugin(agent_type)
    if kind == "migration":
        return "high"
    if name in _READ_ONLY_REVIEW or name in _RESEARCH:
        return "low"
    return "normal"


RECORDED_RISKS: Mapping[str, frozenset[str]] = {
    "review": frozenset({"low"}),
    "migration": frozenset({"high"}),
    "other": frozenset({"low", "normal"}),
}


def kind_default_risk(kind: str) -> str:
    return {"review": "low", "migration": "high"}.get(kind, "normal")


def recorded_risk(kind: str, risk: str | None) -> str:
    """The risk a plan task is looked up at: as written when the rows can record that kind at that
    risk, else the kind's recorded risk."""
    if risk is not None and risk in RECORDED_RISKS.get(kind, frozenset({"normal"})):
        return risk
    return kind_default_risk(kind)


def size_bucket(edit_calls: int) -> str:
    """Measured after the run, so it is kept out of the default forecast."""
    if edit_calls == 0:
        return "na"
    if edit_calls <= 3:
        return "S"
    if edit_calls <= 10:
        return "M"
    return "L" if edit_calls <= 30 else "XL"

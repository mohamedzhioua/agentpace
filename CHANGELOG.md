# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## Unreleased

## 0.2.0 - 2026-11-07

### Added

- Backtest: a week-by-week table for the shipped method, because pooled coverage hid weekly drift in both directions.
- Backtest: a locked forward holdout. Selection is frozen at weeks up to `2026-W45` (`SELECTION_LAST_WEEK`) and never re-runs on later weeks, which are scored in their own section once 20 sessions with 5+ runs exist.
- Backtest: 95% session-bootstrap intervals on the headline coverage and pinball numbers, on each baseline's pinball gap, and on the whole-session test, which now reports pinball loss too.
- Ingest: `agentpace ingest --hook`, an opt-in Claude Code `SubagentStop` hook that logs each session as its subagents finish. `/pace-log` shows the snippet, and nothing installs it.
- Ingest: a cache of parsed runs, so a second ingest reads only what changed (3 to 5 s instead of minutes), plus a store lock so a hook and a manual ingest never interleave.
- Schema: an optional `effort` field (`low`, `medium`, `high`, `xhigh`, `max`), captured from Claude Code metadata. It is not used to forecast.
- Data: 433 of the 3,219 seed rows gain `effort`, regenerated key for key from the transcripts. No other field changed, and no row was added or dropped.

### Fixed

- An ingest made while an agent was still running stored a partial row that stayed in the store beside the completed run. Such runs are now held back until they finish. The published seed and the maintainer's local store were checked key by key against the transcripts, and neither holds one.
- The CLI no longer imports `urllib.request` and `http.client` indirectly through `xml.sax.saxutils`, so no network module loads at all.

### Changed

- README, site and package description lead with the engineer's question, say up front who it fits (Claude Code subagent runs, one task is one run), and give a zero-install `uvx` quickstart.
- README: headline coverage and pinball now carry intervals, and the calibration wording states the weekly spread beside the pooled number.
- `docs/DESIGN.md`: drops the conformal-scaling promise with its reason, marks session weighting superseded, and records the idle-cap sensitivity results.

No published number changed. Every value in `docs/backtest.json` from 0.1.0 is unchanged, and the estimator is untouched.

## 0.1.0 - 2026-11-07

### Added

- Run schema, adapter interface and a Claude Code subagent adapter.
- Deterministic task classification from agent type, with user labels.
- Estimator for plans of tasks: P50, P80 and P90 per task and in total, the critical path and a learned effective-parallelism factor.
- Backtest with time and project holdouts, a contributor holdout that starts with a second contributor, four baselines, and a generated list of losses.
- Seed dataset of real runs from one contributor.
- `share`, which previews exact rows and writes a file, and `validate`, which CI runs on data pull requests.
- Static leaderboard page and four agent skills.
- `claims`, which ties every number in the README to the data.

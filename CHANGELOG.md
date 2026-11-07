# Changelog

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/).

## Unreleased

## 0.6.1 - 2026-11-07

### Added

- DESIGN section 8 records the trigger evals. With only `skills/` installed and 9 prompts per run, the `agentpace` skill triggered on 6 of 6 questions about how long software work takes, and on none of 3 near misses, in Claude Code with Sonnet, Opus and Haiku. Codex CLI (gpt-6-sol) also loaded it on 6 of 6, and read it on one near miss (a git rebase's CPU time) before correctly answering without it.
- A hero illustration at the top of the README and the site: a Gantt chart so long it runs out the door, a crew of terminal cursors that has already stacked the finished work, and an empty chair at a separate review desk for the human part. It holds no words and no numbers, so it cannot carry an unchecked figure. Generated with Codex image generation, then resized and encoded as WebP (`docs/assets/hero.webp`).

### Changed

- The social preview (`docs/assets/social-preview.png`) is the same illustration with the project name. The old one showed run counts from 0.1.0 that no longer matched the dataset.

## 0.6.0 - 2026-11-07

### Added

- Data: 211 Codex turns from the maintainer, 2026-W44 to W45, in `data/runs/4546788e7930eccd-2026-W44-2026-W45.jsonl`. They use the same contributor as the Claude Code rows, so the dataset is still one person's. 177 completed, 33 failed and 1 unknown. Every Codex exec session is one turn, all are classed `other` x `normal`, and the file holds no approval-check turns. The Claude Code rows are unchanged.
  - **Numbers that moved.** The shipped method stays `shrunk`. Every README claim that changed, from 0.5.0 to 0.6.0:
    - completed runs 3,206 to 3,383; sessions 82 to 293; projects 13 to 21; the largest project 71% to 67%; the three largest sessions 48% to 45%; one run in ten took more than 19 min to 20 min.
    - task P80 coverage, per session, 78.5% (CI 72.7% to 84.2%) to 77.7% (CI 71.4% to 83.8%); per run 89.1% to 88.8%. Pinball 1047 (CI 830 to 1318) to 1047 (CI 831 to 1316). MAPE 511% to 494%, against the global median's 232% to 234%.
    - weekly P80 per session 67% to 86%, now 66% to 86%; in 2026-W45, 72% to 66%.
    - pinball gaps: global median +375 to +992, now +373 to +991; human guess +5725 to +7440, now +5725 to +7439.
    - the project split's P80 coverage 77.8% to 88.9%. The split runs over both harnesses and gained one fold, 8 to 9 projects: the only Codex project with 30 or more rows. Its 165 one-turn sessions carry about two thirds of the session-weighted vote, and Codex rows are over-covered, so the rise is composition, not improvement.
    - places where a baseline won, 9 to 20. The new entries come from the project split and from the leave-one-harness-out split, which now runs. Only its Codex-held-out fold runs: holding out Claude Code leaves 177 completed rows in training, under the 200-row floor. That fold forecasts Codex with no Codex row in training, and all 12 harness entries are that fold.
    - the example estimate: total P80 6.0 h to 5.9 h, P90 9.1 h to 9.0 h; the docs task's P50 73 s to 72 s, P90 14 to 13 min and criticality 19% to 18%; the review task's criticality 81% to 82%; runs excluded as not completed 13 to 47.
    - the portable prompt's `other` row: n 268 to 445, P50 9 to 8 min, P80 33 to 24 min, P90 53 to 43 min.
  - The whole-session test is unchanged (56 sessions, P80 80%), because no Codex session has 5 runs.
  - **Why Claude Code numbers moved at all.** Codex rows train every candidate. At the `kind` and `kind` x `risk` levels they join the `other` x `normal` cell, so the parent that sparse Claude `other` cells shrink toward shifted. On the selection folds, the losses moved from global 1272, kind 1480, backoff 1370, shrunk 1047, weighted 1050 to 1276, 1496, 1364, 1047 and 1044. `weighted` is now lowest, and `shrunk` stays shipped by the tie rule, inside the interval (-17 to +24).
  - On Codex alone, on the selection folds, `shrunk` scored 1092 against the global median's 1240, with P80 coverage of 96%. Pooled, the rule would pick `global` (856, against 1078 for `shrunk`). That is printed in the report and not shipped.

### Changed

- The run chart, the site and the README say "agent runs" rather than "subagent runs", and the Limitations section says two harnesses, one person. The site no longer says logging your own runs works with Claude Code subagents only.
- Backtest: selection, the time-split tables, the week-by-week table, the whole-session test and the locked holdout's headline now score Claude Code sessions only, the population the selection rule was frozen on. Rows from every harness still train the estimator. On the published rows, which are all Claude Code, every number is unchanged and `docs/backtest.json` only gains keys.
  - **This was registered after seeing results, not before.** A local run with the maintainer's Codex rows pooled in had picked `global` over the shipped `shrunk`: 785 against 915 with every Codex turn, when the fixed-rounds baseline (774) would also have beaten the winner, and 856 against 1078 with approval checks skipped, where fixed-rounds (911) did not beat the winner. On Claude Code sessions alone `shrunk` held, at 1047 to 1050. Codex supplied most of the pooled vote because each Codex exec session is one turn, and `global` won on Codex only in the week no Codex row was in training.
  - The pooled result is printed in `docs/backtest.md` under "Pooled, for the record", from its own random stream, and is never shipped.
  - The locked holdout gains a per-harness table of every candidate at task level. Whether Codex is better served by another method is decided there, on weeks after 2026-W45, and nowhere else.
- Codex: turns run by `codex-auto-review`, Codex's automatic approval check on a command, are skipped and counted under `skipped runs`, instead of being stored as reviews. They take seconds, call no tool and are not work anyone plans. The adapter version moves to 2, so the next `ingest --source codex` reads Codex history again once. Claude Code rows and the published dataset are unchanged.

## 0.5.0 - 2026-11-07

### Added

- Backtest: a "By harness" section and a leave-one-harness-out split. Today the split prints "could not run (1 harness)", because the published rows are all Claude Code. The per-harness tables reuse the selection-fold forecasts, and the split draws its own streams, like the project split. No published number moved, and `docs/backtest.json` only gained keys.

- A Codex adapter: `agentpace ingest --source codex` reads `~/.codex/sessions`, one agent turn per run and one rollout file per batch, using the same idle cap and privacy rules as Claude Code. It copies only types, timestamps, ids, model, effort, counters and fixed-set flags, never prompts, commands, patches or tool output. Forked rollouts and upkeep sessions are skipped and counted. Review sessions and `codex-auto-review` turns count as reviews. Codex rows are not in the published dataset.
- `score --session` finds a session in any adapter's cache.
- Adapters return every run in a file, so a harness can keep several runs in one transcript, as Codex does with several turns. The ingest cache moves to format 3 to hold a list of runs per file, so the first ingest after upgrading reads every transcript again (minutes rather than seconds on a large history). Rows are byte-identical to before.
- Schema: `harness` accepts `codex`, ahead of the Codex adapter. The change is additive, so every existing row stays valid and unchanged. It merges alone first, because data pull requests are validated with the base branch's schema.

## 0.4.0 - 2026-11-07

### Added

- `agentpace score <id> --session <ref>` scores a recorded forecast against a session of your own ingested runs. The actual is agent active time on that session's clock, with idle gaps over the idle cap clipped and the human part outside it, the same quantity the backtest scores. A session scores only if the local ingest cache shows it started after the forecast was issued. Otherwise the score is refused, with no silent fallback. `--actual 2h` records a figure you attest, labeled as such. Scores are appended, and the latest per forecast wins.
- `agentpace score` with no id shows how your forecasts held up: the share of actuals within P50, P80 and P90, and pinball loss. For parsed quotes it shows how often the quoted timeline was closer than the P50, and the median quote-to-actual ratio. Rates need at least 5 scored forecasts. None of these numbers is published.
- `estimate --record` keeps each forecast in a local ledger, `~/.agentpace/forecasts.jsonl`, exactly as it was shown, with the time it was issued. The ledger is never shared and holds no file paths or data sources. `--quoted "3 weeks"` stores a timeline someone already gave beside it. An exact "<number> <unit>" quote is also stored in seconds by fixed calendar units (1 d = 86,400 s, 1 w = 7 d, 1 mo = 30 d), and anything else is kept as text only.

## 0.3.0 - 2026-11-07

### Added

- `agentpace turns` measures how long you wait between agent turns, from the timestamps of your own main Claude Code sessions. A gap runs from the agent's last record to the next prompt a person typed (`turnOrigin: human`). It prints uncapped P50/P80/P90 per human turn with the number of gaps and sessions, the share of gaps over an hour, and the median share of a session spent waiting on you. It is elapsed time, not effort.
  - Only the record type, the timestamp, `turnOrigin` and a tool-result flag are read from each line.
  - Records go to `~/.agentpace/turns.jsonl` under the new `schema/turn.schema.json`, and are never shared, never run rows and never in the dataset.
  - Sessions without `turnOrigin` are skipped and counted.
- `estimate` reads those waits when they exist and states them in the human part line, from your history, per human turn, outside the total. `--turns` points at another file. JSON `human` gains `source`, `per_turn_p50`, `per_turn_p80`, `per_turn_p90`, `gaps` and `sessions` when measured.

### Changed

- The `agentpace` skill passes a measured human part on without multiplying it by a guessed number of turns.

## 0.2.1 - 2026-11-07

### Added

- `estimate` prints the human part on its own line in every result: review, testing, decisions and waiting between agent turns are not measured, are not in the total, and must not be derived from agent time. JSON output gains `human` (`status`, `included_in_total`).
- Backtest: a `width80` metric, the median P80/P50 ratio, beside the existing P90/P50 `width`. It comes from the same forecasts with no new random draw, so no published number moves.

### Changed

- The `agentpace` skill triggers on any question about how long software work will take, even when agents are not mentioned, and assumes agents do the implementation unless told otherwise. It gains a step for re-answering a timeline someone already quoted in days, weeks or months: the quote is shown beside the measured range as unverified and never converted by a constant.
- `/pace-estimate` passes the human-part line through verbatim, and says it is the explicit command while `agentpace` handles natural-language questions.
- README: a "Try it" command on the first screen, a support table, Install before the commands, a skills table that says what to say and what you get, commands split into Use and Maintain, a claim-marked backtest bottom line with the detail tables collapsed, an install step in the contribute block, and a manual install line for agents without Node.
- Each skill opens with when to use it and what it produces. `/pace-log` also triggers on recording or tracking runs.
- Site: the lede and reading notes are written into the page by `agentpace site`, so they show without JavaScript. The page gains a promise, a no-install Try-it block, link-preview tags and Try it and Install links.

### Fixed

- The portable prompt no longer adds up P50s for the low end and P90s for the high end, which overstates the high end and ignores throughput. It now takes the larger of the critical chain and the total work over the agents running at once, then applies the published whole-session P80/P50 and P90/P50 ratios, and calls the result a rule of thumb.

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

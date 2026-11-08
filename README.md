# agentpace

**Stop estimating agent work in human weeks. agentpace measures how long coding agents actually take: a P50/P80/P90 range from real runs, with the human part stated apart, never derived.**

![A speech bubble, dashed and fading, holds a wall calendar shedding blank pages. Beside it a solid stopwatch, its hand a terminal cursor, is measured by two small cursor robots with a tape measure.](https://raw.githubusercontent.com/mohamedzhioua/agentpace/main/docs/assets/hero.webp)

[![CI](https://github.com/mohamedzhioua/agentpace/actions/workflows/ci.yml/badge.svg)](https://github.com/mohamedzhioua/agentpace/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
[![Code: MIT](https://img.shields.io/badge/code-MIT-blue)](LICENSE)
[![Data: CC0-1.0](https://img.shields.io/badge/data-CC0--1.0-blue)](data/LICENSE)

## Try it

With [uv](https://docs.astral.sh/uv/), this needs no install, no clone and no history of your own:

```bash
uvx --from git+https://github.com/mohamedzhioua/agentpace agentpace estimate --example
```

```text
<!-- claim:example_output -->
Plan total on the session clock: P50 2.4 h, P80 5.9 h, P90 9.0 h
The session clock counts agent runs and the gaps between them, each gap up to the idle cap (900 s by default), so waiting on you between runs sits inside the total up to that cap.
Human part (review, testing, decisions, waiting between agent turns): not measured. No allowance for it is added to the total above. State it separately; do not derive it from the agent time.

task          P50        P80        P90  critical  cell (n)
schema     30 min     85 min      2.5 h     100%  feature x normal x claude-opus-5-5 (n=205)
api        30 min     87 min      2.5 h      53%  feature x normal x claude-opus-5-5 (n=205)
ui         30 min     86 min      2.6 h      47%  feature x normal x claude-opus-5-5 (n=205)
docs         72 s      6 min     13 min      18%  docs x normal x claude-opus-5-5 (n=0)
review      3 min      8 min     13 min      82%  review x low x claude-opus-5-5 (n=1255)

Critical path: schema -> ui -> review (longest in 47% of simulations)
Effective parallelism at cap 3: 0.90 (from 29 sessions). Throughput set the total in 76% of simulations.
Method: shrunk. 3383 runs, 47 not completed so excluded. 20000 simulations, seed 20261107.
Not completed, by harness: claude-code 13 of 3219 (0.4%; 3 failed, 10 unknown), codex 34 of 211 (16.1%; 33 failed, 1 unknown). These are recorded harness outcomes, not task acceptance, and they are left out of the fit.

Warnings:
  - cell docs x normal x claude-opus-5-5: n=0, effective n=0.0, below 30. Pooled with all and interval widened.
<!-- /claim -->
```

The plan it estimated is [examples/plan.json](examples/plan.json), where each task is one agent run:

```json
{
  "agent_cap": 3,
  "model": "claude-opus-5-5",
  "tasks": [
    { "id": "schema", "kind": "feature", "risk": "normal" },
    { "id": "api", "kind": "feature", "risk": "normal", "depends_on": ["schema"] },
    { "id": "ui", "kind": "feature", "risk": "normal", "depends_on": ["schema"] },
    { "id": "docs", "kind": "docs", "risk": "normal", "depends_on": ["api"] },
    { "id": "review", "kind": "review", "risk": "low", "depends_on": ["api", "ui"] }
  ]
}
```

To estimate your own, write a plan like it and run `agentpace estimate plan.json`, or pass `-` to read it from stdin. In a plan, `depends_on`, `risk` and `model` are optional. Effective parallelism is work divided by elapsed time. It can be below 1, because a session's clock includes the time between runs.

Why a range and not one number? Agent run times have a long tail, so any single number misses most of the time. P50 is a coin flip, and P80 is the one to plan on.

### In your agent

```bash
npx skills add mohamedzhioua/agentpace
```

Then ask your agent: "How long will the billing feature take?" The skill calls the CLI, so install it first ([Install](#install)). Without it, the skill offers the install command or a portable rule-of-thumb prompt.

## Why agentpace

Ask an assistant how long a feature will take and it answers in human-team time, "a few weeks", even when agents will do the work. agentpace answers from measured agent runs instead. Give it a plan of agent tasks and it returns P50/P80/P90 ranges measured from real agent runs and a critical path. It states the human part (review, testing, decisions, waiting) on its own line, and it publishes how often it is wrong. It is a standard-library Python CLI, an agent skill, and an open CC0 dataset.

![How long agent runs took, by task kind](https://raw.githubusercontent.com/mohamedzhioua/agentpace/main/docs/assets/run-ranges.svg)

These are <!-- claim:completed -->3,383<!-- /claim --> completed agent runs: Claude Code subagent runs and Codex turns. Half finished within <!-- claim:overall_p50 -->2 min<!-- /claim -->, and one in ten took more than <!-- claim:overall_p90 -->20 min<!-- /claim -->. All of them come from one person so far, so read [Limitations](#limitations) before trusting any of this.

## Does it fit your setup?

| You want to | Works with today |
|---|---|
| Estimate a plan | Anywhere. The seed data ships inside the package, so no history of your own is needed |
| Log your own runs | Claude Code, subagent runs only: work Claude Code hands to a subagent, not the main conversation. Your rows pool with the seed, and `--data ~/.agentpace/runs.jsonl` uses only yours |
| Log your own Codex runs | Codex, one agent turn per run: `agentpace ingest --source codex` reads `~/.codex/sessions`. The published rows include Codex turns, and the backtest scores Codex on its own |
| Log your own Gemini CLI runs | Experimental: `agentpace ingest --source gemini-cli` reads `~/.gemini/tmp`, one agent turn per run. The adapter is built from the Gemini CLI source, not yet verified on real sessions, and no published rows come from it (`validate` rejects them until the adapter is verified; [how to verify it](docs/verify-adapter.md)). Report problems in an [issue](https://github.com/mohamedzhioua/agentpace/issues) |
| Log your own OpenCode runs | Experimental: `agentpace ingest --source opencode` reads `~/.local/share/opencode/opencode.db`. The adapter is built from the OpenCode source, not yet verified on real sessions, and no published rows come from it (`validate` rejects them until the adapter is verified; [how to verify it](docs/verify-adapter.md)). Report problems in an [issue](https://github.com/mohamedzhioua/agentpace/issues) |
| Log your own Cline runs | Experimental: `agentpace ingest --source cline` reads Cline's task folders in VS Code's global storage, one agent turn per run. The adapter is built from the Cline source, not yet verified on real sessions, and no published rows come from it (`validate` rejects them until the adapter is verified; [how to verify it](docs/verify-adapter.md)). Report problems in an [issue](https://github.com/mohamedzhioua/agentpace/issues) |
| Another harness | Not yet. The adapter interface takes one module per harness |

## Install

```bash
uv tool install git+https://github.com/mohamedzhioua/agentpace
```

`pipx install git+https://github.com/mohamedzhioua/agentpace` works too. It needs Python 3.10 or later and has no dependencies. agentpace is not on PyPI yet, which is why both commands install from git. After installing, drop the `uvx --from ...` prefix from the command above.

Then add the agent skills:

```bash
npx skills add mohamedzhioua/agentpace
```

Without Node, copy the four folders under [skills/](skills/) into your agent's skills folder, for example `~/.claude/skills/` for Claude Code. No skill-capable agent? Paste [the portable prompt](skills/agentpace/assets/portable-prompt.md) into any assistant.

## Skills

| Skill | Say this | You get |
|---|---|---|
| [`agentpace`](skills/agentpace/SKILL.md) | "How long will the billing feature take?", or "my manager says this is a 3-month project" | The work split into agent tasks, a measured P50/P80/P90 range with its critical path, the human part on its own line, and any quoted timeline shown beside it as unverified |
| [`/pace-estimate`](skills/pace-estimate/SKILL.md) | `/pace-estimate plan.json`, or a task list | The same estimate for a plan you hand it |
| [`/pace-log`](skills/pace-log/SKILL.md) | `/pace-log`, "log my agent runs", "that run was a bug fix" | Your runs ingested into `~/.agentpace`, labels corrected, and the opt-in hook that logs each run as it finishes |
| [`/pace-share`](skills/pace-share/SKILL.md) | `/pace-share`, only when you ask | The exact rows you could contribute, previewed first. It writes a file only after you say yes, and sends nothing |

`agentpace` triggers by itself on questions about how long software work will take. The three `/pace-*` skills run when you call them. The [portable prompt](skills/agentpace/assets/portable-prompt.md) gives a rule-of-thumb version of the same answer in assistants without skills.

### Re-answer a quoted timeline

Suppose someone has called a customer-import feature "a 3-month project". The skill splits it into agent tasks, as in [examples/quote-plan.json](examples/quote-plan.json), and passes the quote on exactly as it was given:

```bash
agentpace estimate examples/quote-plan.json --quoted "3 months"
```

```text
<!-- claim:quote_example_output -->
Plan total on the session clock: P50 4.3 h, P80 10.3 h, P90 15.7 h
Quoted: "3 months" (calendar time, as quoted; unverified, not converted)
The session clock counts agent runs and the gaps between them, each gap up to the idle cap (900 s by default), so waiting on you between runs sits inside the total up to that cap.
Human part (review, testing, decisions, waiting between agent turns): not measured. No allowance for it is added to the total above. State it separately; do not derive it from the agent time.

task               P50        P80        P90  critical  cell (n)
schema          30 min     85 min      2.5 h     100%  feature x normal x claude-opus-5-5 (n=205)
importer        30 min     86 min      2.6 h      40%  feature x normal x claude-opus-5-5 (n=205)
api             30 min     87 min      2.5 h      60%  feature x normal x claude-opus-5-5 (n=205)
settings-ui     31 min     86 min      2.5 h      32%  feature x normal x claude-opus-5-5 (n=205)
tests           38 min      2.1 h      3.8 h      68%  tests x normal x claude-opus-5-5 (n=2)
review           3 min      8 min     13 min     100%  review x low x claude-opus-5-5 (n=1255)

Critical path: schema -> importer -> tests -> review (longest in 40% of simulations)
Effective parallelism at cap 3: 0.90 (from 29 sessions). Throughput set the total in 78% of simulations.
Method: shrunk. 3383 runs, 47 not completed so excluded. 20000 simulations, seed 20261107.
Not completed, by harness: claude-code 13 of 3219 (0.4%; 3 failed, 10 unknown), codex 34 of 211 (16.1%; 33 failed, 1 unknown). These are recorded harness outcomes, not task acceptance, and they are left out of the fit.

Warnings:
  - cell tests x normal x claude-opus-5-5: n=2, effective n=2.0, below 30. Pooled with all and interval widened.
<!-- /claim -->
```

The quote is calendar time, as quoted, and nothing has checked it. The range is built from measured agent time on the session clock, which counts the gaps between runs up to the idle cap. The human part is stated apart, on its own line, and no allowance for it is added to either. The quote is shown beside the range, never converted into agent time or set against it.

## Commands

| Use | What it does |
|---|---|
| `agentpace estimate <plan>` | P50, P80 and P90 for a plan, and the human part on its own line |
| `agentpace estimate <plan> --review-capacity "4 PRs a day"` | The same, with your stated review capacity shown on its own line as yours: not measured, not applied to the range |
| `agentpace estimate <plan> --data ~/.agentpace/runs.jsonl` | The same, from your own ingested runs only |
| `agentpace estimate <plan> --record --quoted "3 weeks"` | Keep the forecast on your machine to score later, with any timeline someone already quoted |
| `agentpace ingest [path]` | Read local agent history into rows |
| `agentpace runs` | List recent runs and their references |
| `agentpace label <ref>` | Correct the kind or risk of a run |
| `agentpace share` | Preview exact rows, or write a file to contribute |
| `agentpace score [id]` | Score a recorded forecast against a session that started after it, or see how your forecasts held up |
| `agentpace turns` | Measure how long you wait between agent turns, from your own main sessions. Kept local, never shared |

| Maintain | What it does |
|---|---|
| `agentpace backtest` | Regenerate the published evaluation |
| `agentpace validate` | Check data files: the bundled seed by default; contributors pass `data/runs` |
| `agentpace site` | Refresh the data inside the static page |
| `agentpace claims` | Check every number in this README against the data |

`agentpace ingest` reads Claude Code subagent transcripts from `~/.claude/projects` and keeps rows in `~/.agentpace`, on your machine. A second run reads only what changed. To log every run as it finishes, add the opt-in hook that [`/pace-log`](skills/pace-log/SKILL.md) shows.

There is also a [leaderboard](https://mohamedzhioua.github.io/agentpace/) of median, P75 and P90 by model, kind and risk.

## Backtest: how often it is wrong

**Bottom line.** On <!-- claim:plan_sessions -->56<!-- /claim --> held-out sessions, the whole-session P80 covered <!-- claim:plan_p80 -->80% (CI 70% to 91%)<!-- /claim --> and the P90 covered <!-- claim:plan_p90 -->91% (CI 84% to 98%)<!-- /claim -->. For a single task, the P80 covered <!-- claim:task_p80 -->77.7% (CI 71.4% to 83.8%)<!-- /claim -->, but the median miss of its P50 is <!-- claim:task_mdape -->78%<!-- /claim -->, so plan on the range, never the middle. Calibration drifts from week to week: per-session P80 coverage ran from <!-- claim:weekly_p80 -->66% to 86%<!-- /claim -->.

Every number here comes from the published rows. `agentpace backtest` regenerates them, and CI fails if they drift. The headline scores Claude Code sessions, the population the method was chosen on. Codex is [scored on its own](docs/backtest.md#by-harness). The [full report](docs/backtest.md) has every split, every baseline and every loss.

Forecasts use only what is known before a run: model, kind and risk. Whole sessions are held out, so no run can leak into its own forecast. These headline numbers come from the same time folds that chose the method, so treat them as slightly optimistic. Weeks after <!-- claim:selection_last_week -->2026-W45<!-- /claim --> form a locked holdout that never takes part in selection. Its status: <!-- claim:locked_status -->not enough data (no weeks after 2026-W45 yet)<!-- /claim -->. Every interval below is a 95% bootstrap interval that resamples whole sessions.

<details>
<summary>Every number, with intervals and baselines</summary>

**Whole sessions.** Each of <!-- claim:plan_sessions -->56<!-- /claim --> later sessions with at least 5 completed runs was predicted from earlier weeks only. This is the only direct test of plan totals.

| Forecast | Target | Got |
|---|---:|---:|
| Session finished within P50 | 50% | <!-- claim:plan_p50 -->59% (CI 45% to 71%)<!-- /claim --> |
| Session finished within P80 | 80% | <!-- claim:plan_p80 -->80% (CI 70% to 91%)<!-- /claim --> |
| Session finished within P90 | 90% | <!-- claim:plan_p90 -->91% (CI 84% to 98%)<!-- /claim --> |

P80 and P90 coverage land near target, but with this few sessions the intervals are wide, and so are the ranges: P90 is typically <!-- claim:plan_width -->2.7x<!-- /claim --> P50, and the median error of the P50 is <!-- claim:plan_mdape -->67%<!-- /claim -->. The test also uses information a real forecast would not have, namely the session's actual task list and peak concurrency, and real dependencies are not recorded. So it tests the throughput model, not dependency planning. Pinball loss on whole sessions is <!-- claim:plan_pinball -->19720 (CI 13260 to 28000)<!-- /claim --> seconds, against <!-- claim:naive_plan_pinball -->37990<!-- /claim --> for a sum of medians.

**Single tasks.** On the time split, with each session counting equally:

| Method | P80 coverage (target 80%) | Pinball loss (lower is better) | Its pinball minus agentpace's |
|---|---:|---:|---:|
| agentpace (`<!-- claim:winner -->shrunk<!-- /claim -->`) | <!-- claim:task_p80 -->77.7% (CI 71.4% to 83.8%)<!-- /claim --> | <!-- claim:task_pinball -->1047 (CI 831 to 1316)<!-- /claim --> | n/a |
| Fixed 3 minutes per round | n/a | <!-- claim:rounds_pinball -->1308<!-- /claim --> | <!-- claim:rounds_pinball_gap -->+75 to +467<!-- /claim --> |
| Global median | n/a | <!-- claim:median_pinball -->1719<!-- /claim --> | <!-- claim:median_pinball_gap -->+373 to +991<!-- /claim --> |
| Naive human-time guess | n/a | <!-- claim:human_pinball -->7569<!-- /claim --> | <!-- claim:human_pinball_gap -->+5725 to +7439<!-- /claim --> |

The human-time guess is a hand-written table, an assumption rather than a measurement. A difference interval wholly above zero means that baseline is worse at the 95% level.

**Calibration is not stable from week to week.** That P80 coverage pools <!-- claim:weeks_tested -->4<!-- /claim --> test weeks, and the pooled figure hides misses in both directions. Week by week it ran from <!-- claim:weekly_p80 -->66% to 86%<!-- /claim --> with each session counting equally, and from <!-- claim:weekly_p80_row -->60% to 94%<!-- /claim --> with each run counting equally. In the latest week, <!-- claim:latest_week -->2026-W45<!-- /claim -->, it was <!-- claim:latest_week_p80 -->66%<!-- /claim --> per session and <!-- claim:latest_week_p80_row -->60%<!-- /claim --> per run, from only <!-- claim:latest_week_sessions -->8<!-- /claim --> sessions. Read the pooled P80 as an average over weeks, not a promise for any one week. The [week-by-week table](docs/backtest.md#time-split-week-by-week) has every fold.

![P80 coverage by method](https://raw.githubusercontent.com/mohamedzhioua/agentpace/main/docs/assets/backtest-coverage.svg)

</details>

**Where it falls short.**

- The median error on a single task is <!-- claim:task_mdape -->78%<!-- /claim -->. Use the range, not the middle.
- Pooled, P80 coverage is <!-- claim:task_p80_row -->88.8%<!-- /claim --> when each run counts equally rather than each session, so the ranges are too wide for the small, fast runs that dominate the row count.
- A global median beats agentpace on MAPE (<!-- claim:median_mape -->234%<!-- /claim --> against <!-- claim:task_mape -->494%<!-- /claim -->), and a sum of medians beats it on plan MAPE (<!-- claim:naive_plan_mape -->85%<!-- /claim --> against <!-- claim:plan_mape -->249%<!-- /claim -->), though that sum covers only <!-- claim:naive_plan_p80 -->5%<!-- /claim --> of sessions at P80. MAPE punishes the long tail of over-forecasts that honest ranges need.
- The report lists <!-- claim:loss_count -->20<!-- /claim --> places where a baseline won. That list is generated, so it cannot be edited out.
- On the project split, which runs over both harnesses and where the largest project, a <!-- claim:project_top_harness -->codex<!-- /claim --> one, holds <!-- claim:project_top_sessions -->137 of 212<!-- /claim --> held-out sessions, P80 coverage is <!-- claim:project_p80 -->88.9%<!-- /claim -->. The contributor split <!-- claim:contributor_split -->could not run (1 contributor)<!-- /claim -->.
- The session-weighted design this project started with did not beat the simpler unweighted one, so the simpler one shipped. The selection rule was fixed before the first result. Scoring it on Claude Code sessions only was decided after a run with Codex rows pooled in had been seen, and that [pooled result](docs/backtest.md#pooled-for-the-record) is printed in the report.

## Privacy

agentpace never stores prompts, code or commit messages, and the rows you publish hold no content, file paths or repo names. Paths live in one local place, the ingest cache in `~/.agentpace/`, which is never shared. It holds two kinds: the path of each transcript, so the next ingest re-reads only what changed, and the project folder (for Codex, the working folder), which reaches a row only as a keyed hash.

- **What gets read.** From each transcript: timestamps, model ids, token counts, tool names, error flags and two metadata keys, plus the project folder as a hash input. JSON is decoded one line at a time, so other text passes through memory, but only those values are copied out. A test plants marker strings in every sensitive field and proves none reach any output.
- **What a row holds.** Only the fields in [schema/run.schema.json](schema/run.schema.json). Every string is an enum or a strict pattern, so free text has nowhere to land.
- **Who you are.** Keyed hashes under a random secret that never leaves your machine. No email, handle or hostname goes in, so nobody can reverse them by guessing names.
- **Nothing is sent.** There is no network code in this package at all, and a test fails if any is added. Contributing is a git push you run yourself. The site loads nothing from anywhere else.

Rows are not anonymous in every sense. They link to each other, they show roughly when you worked, and a pull request ties them to your GitHub account. `agentpace share` says so before it writes anything.

## Contribute your runs

Sharing is opt-in, and your rows join a public-domain dataset.

```bash
git clone https://github.com/<your-fork>/agentpace && cd agentpace
uv tool install .           # or pipx install .
agentpace ingest
agentpace share --dry-run    # prints the exact rows and writes nothing
agentpace share --write      # adds or extends one file under data/runs, and still sends nothing
agentpace validate data/runs # the same checks CI runs
```

Then open a pull request with that one file. A data pull request dedicates your rows to the public domain under CC0-1.0. See [CONTRIBUTING.md](CONTRIBUTING.md).

The most useful thing you can do is contribute: the contributor split cannot run until a second person does.

## How it compares

Audited at the commits listed in [docs/DESIGN.md](docs/DESIGN.md).

| | agentpace | [agent-estimate](https://github.com/kiloloop/agent-estimate) | [claude-eta](https://github.com/MMMProd-Org/claude-eta) | [agent-estimation](https://github.com/ZhangHanDong/agent-estimation) | [web-dev-estimation](https://github.com/ecappa/web-dev-estimation) | [mythical-agent-month](https://github.com/MedAdemBHA/mythical-agent-month) |
|---|---|---|---|---|---|---|
| **Base** | Observed run durations | PERT tiers in minutes | Per-turn seconds | Tool-call rounds | Human time with multipliers | Size tables with PERT |
| **Numbers come from** | Published rows | Hand-set, with a small private sample | Your own hook data | Hand-set | Hand-set | One 398-run build, unpublished |
| **Output** | P50, P80, P90 | O/M/P and sigma | P50, P80 | Low to high | Mandatory range | Likely, range, plan |
| **Runs in repo** | Yes | No | No | No | No | No, a shape file only |
| **Multi-agent** | Graph simulation, learned throughput | Graph, wave planning | No | Waves | No | Critical path, default 1.8 |
| **Published check** | Time and project holdouts, regenerated by CI | Coverage audit, needs OACP files | Walk-forward: n=285, P80 76.8%, MdAPE 79% | None | Prompt evals | One holdout: -24% and -37% |
| **Runtime deps** | None | typer, pydantic, networkx, others | None | None | None | None |

Each of these does something better than agentpace:

- **agent-estimate** binds forecasts to receipts, so one issued after work started cannot score. It also forecasts token cost, and ships as a pip package and a GitHub Action.
- **claude-eta** gives live per-turn ETAs inside a session, evaluates itself walk-forward on your own history, and has consent-gated community sharing and a repair-loop detector.
- **agent-estimation** is the simplest and most teachable model here, and it needs no data at all.
- **web-dev-estimation** has the best skill structure, with batch and re-estimate modes that work from documents before any history exists.
- **mythical-agent-month** separates machine, human and waiting time, tracks usage budgets, and openly publishes its own held-out miss.

## Limitations

- **One person's workflow.** Every run comes from <!-- claim:contributors -->1<!-- /claim --> contributor, across <!-- claim:projects -->21<!-- /claim --> projects and <!-- claim:sessions -->293<!-- /claim --> sessions, from <!-- claim:first_week -->2026-W40<!-- /claim --> to <!-- claim:last_week -->2026-W45<!-- /claim -->. One project holds <!-- claim:top_project -->67%<!-- /claim --> of runs, and three sessions hold <!-- claim:top_sessions -->45%<!-- /claim -->. It includes heavy automated fan-out, so it may not resemble your setup.
- **Not a model speed ranking.** People send harder work to stronger models, so a slower row may just mean harder tasks.
- **`feature` means any implementation work.** Without reading prompts, metadata cannot separate features, bug fixes and refactors. Those kinds stay thin until people label runs.
- **Retries are not separated.** A run's duration includes any test-fix loop inside it. `retries` counts errored tool results, a proxy, because a real retry cannot be seen without reading content, and no field that describes what happened in a run (`retries`, `tool_calls`, `tokens`, `outcome`) ever predicts a duration. Codex upkeep sessions (`compact`) are skipped; nothing else is split out.
- **Pooled by kind, risk and model only.** A cell with fewer than 30 runs leans on broader cells, with a warning. Repository size is not recorded, and `size` is an edit count measured after the run, so neither can segment the published runs. Long integration work has no cell of its own.
- **Two harnesses, still one person.** The published rows hold Claude Code subagent runs and, since 0.6.0, Codex turns, all from the same contributor. Codex is scored on its own, never in the headline. Each Codex exec session is a single turn, so the session effect and the parallelism factor learn nothing from Codex, and a Codex plan borrows Claude Code's session structure. Gemini CLI, OpenCode and Cline have experimental adapters: built from each tool's source, not yet verified on real sessions, with no published rows (`validate` rejects them until each adapter is verified; [how to verify one](docs/verify-adapter.md)), and problems go in an [issue](https://github.com/mohamedzhioua/agentpace/issues). Other harnesses have no adapter.
- **No human allowance, and short waits sit inside the clock.** The plan total is a forecast on the session clock, which counts agent runs and the gaps between them, each gap up to the idle cap (900 s in the published rows). So a wait on you shorter than that sits inside the total, a longer one is clipped at the cap, and no allowance for review, testing or decisions is added. Every estimate says so on its own lines and never derives the human part from agent time. `agentpace turns` measures your own wait per human turn from your main-session timestamps and keeps it on your machine. That is elapsed time, not effort, shown beside the range and never added to it.
- **Failed runs are under-counted.** Only runs with a clear completion signal train the estimator, so P90 may run low. Recorded runs that did not complete (failed or unknown), by harness: <!-- claim:not_completed_by_harness -->claude-code 13 of 3,219 (0.4%), codex 34 of 211 (16.1%)<!-- /claim -->. Those are harness outcomes, not task acceptance, and every estimate prints the counts for the rows it loaded.
- **Size is not used by default.** Stored size is measured after a run, so it would leak the answer into the forecast.
- **The transcript format is undocumented.** It may change, and Claude Code removes old transcripts, so run `agentpace ingest` regularly.
- **Dependencies are untested.** The whole-session test cannot see real dependencies.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests -t .
```

The tests need nothing installed. All the math is in [src/agentpace/estimate.py](src/agentpace/estimate.py). The reasoning behind the design is in [docs/DESIGN.md](docs/DESIGN.md).

## License

Code: [MIT](LICENSE). Data: [CC0-1.0](data/LICENSE).

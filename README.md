# agentpace

**How long will your coding-agent work take? A range you can plan on, measured from real runs.**

[![CI](https://github.com/mohamedzhioua/agentpace/actions/workflows/ci.yml/badge.svg)](https://github.com/mohamedzhioua/agentpace/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
[![Code: MIT](https://img.shields.io/badge/code-MIT-blue)](LICENSE)
[![Data: CC0-1.0](https://img.shields.io/badge/data-CC0--1.0-blue)](data/LICENSE)

Give agentpace a list of agent tasks, and it answers with P50/P80/P90 wall-clock ranges and a critical path, fitted to real run history instead of a guess. It publishes how often it is wrong. It is a standard-library Python CLI, an agent skill, and an open CC0 dataset.

![How long subagent runs took, by task kind](docs/assets/run-ranges.svg)

These are <!-- claim:completed -->3,206<!-- /claim --> completed subagent runs. Half finished within <!-- claim:overall_p50 -->2 min<!-- /claim -->, and one in ten took more than <!-- claim:overall_p90 -->19 min<!-- /claim -->. All of them come from one person so far, so read [Limitations](#limitations) before trusting any of this.

**Does it fit your setup?** Estimating works anywhere, because the seed data ships inside the package. Logging your own runs works with Claude Code today, and only for subagent runs: work Claude Code hands to a subagent, not the main conversation. A task in a plan is one such run. Once you have ingested, `agentpace estimate` pools your rows with the seed. Pass `--data ~/.agentpace/runs.jsonl` to use only yours. A Codex adapter is tracked in [#3](https://github.com/mohamedzhioua/agentpace/issues/3).

## Quickstart

Describe the work as tasks and pass them in. Each task is one agent run. With [uv](https://docs.astral.sh/uv/), this needs no install, no clone and no history of your own:

```bash
uvx --from git+https://github.com/mohamedzhioua/agentpace agentpace estimate - <<'EOF'
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
EOF
```

```text
<!-- claim:example_output -->
Total wall clock: P50 2.4 h, P80 6.0 h, P90 9.1 h

task          P50        P80        P90  critical  cell (n)
schema     30 min     85 min      2.5 h     100%  feature x normal x claude-opus-5-5 (n=205)
api        30 min     87 min      2.5 h      53%  feature x normal x claude-opus-5-5 (n=205)
ui         30 min     86 min      2.6 h      47%  feature x normal x claude-opus-5-5 (n=205)
docs         73 s      6 min     14 min      19%  docs x normal x claude-opus-5-5 (n=0)
review      3 min      8 min     13 min      81%  review x low x claude-opus-5-5 (n=1255)

Critical path: schema -> ui -> review (longest in 47% of simulations)
Effective parallelism at cap 3: 0.90 (from 29 sessions). Throughput set the total in 76% of simulations.
Method: shrunk. 3206 runs, 13 not completed so excluded. 20000 simulations, seed 20261107.

Warnings:
  - cell docs x normal x claude-opus-5-5: n=0, effective n=0.0, below 30. Pooled with all and interval widened.
<!-- /claim -->
```

On PowerShell, which has no heredoc, save the JSON as `plan.json` and run the same command with `plan.json` in place of `- <<'EOF'`. After the install below, drop the `uvx --from ...` prefix.

Effective parallelism is work divided by elapsed time. It can be below 1, because a session's clock includes the time between runs.

To add your own history to the seed data, run `agentpace ingest`. It reads Claude Code subagent transcripts from `~/.claude/projects` and keeps rows in `~/.agentpace`, on your machine.

In a plan, `depends_on`, `risk` and `model` are optional. The same plan is in [examples/plan.json](examples/plan.json), so `agentpace estimate examples/plan.json` works from a clone.

| Command | What it does |
|---|---|
| `agentpace ingest [path]` | Read local agent history into rows |
| `agentpace runs` | List recent runs and their references |
| `agentpace label <ref>` | Correct the kind or risk of a run |
| `agentpace estimate <plan>` | P50, P80 and P90 for a plan |
| `agentpace backtest` | Regenerate the published evaluation |
| `agentpace share` | Preview exact rows, or write a file to contribute |
| `agentpace validate` | Check data files |
| `agentpace site` | Refresh the data inside the static page |
| `agentpace claims` | Check every number in this README against the data |

There is also a [leaderboard](https://mohamedzhioua.github.io/agentpace/) of median, P75 and P90 by model, kind and risk.

## Install

```bash
uv tool install git+https://github.com/mohamedzhioua/agentpace
```

`pipx install git+https://github.com/mohamedzhioua/agentpace` works too. It needs Python 3.10 or later and has no dependencies. The seed data ships inside the package, so estimates work straight away.

To add the agent skills, which give you `/pace-estimate`, `/pace-log` and `/pace-share`:

```bash
npx skills add mohamedzhioua/agentpace
```

No skill-capable agent? Paste [the portable prompt](skills/agentpace/assets/portable-prompt.md) into any assistant.

## Backtest: how often it is wrong

Every number here comes from the published rows. `agentpace backtest` regenerates them, and CI fails if they drift. The [full report](docs/backtest.md) has every split, every baseline and every loss.

Forecasts use only what is known before a run: model, kind and risk. Whole sessions are held out, so no run can leak into its own forecast. These headline numbers come from the same time folds that chose the method, so treat them as slightly optimistic. Weeks after <!-- claim:selection_last_week -->2026-W45<!-- /claim --> form a locked holdout that never takes part in selection. Its status: <!-- claim:locked_status -->not enough data (no weeks after 2026-W45 yet)<!-- /claim -->. Every interval below is a 95% bootstrap interval that resamples whole sessions.

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
| agentpace (`<!-- claim:winner -->shrunk<!-- /claim -->`) | <!-- claim:task_p80 -->78.5% (CI 72.7% to 84.2%)<!-- /claim --> | <!-- claim:task_pinball -->1047 (CI 830 to 1318)<!-- /claim --> | n/a |
| Fixed 3 minutes per round | n/a | <!-- claim:rounds_pinball -->1308<!-- /claim --> | <!-- claim:rounds_pinball_gap -->+75 to +467<!-- /claim --> |
| Global median | n/a | <!-- claim:median_pinball -->1719<!-- /claim --> | <!-- claim:median_pinball_gap -->+375 to +992<!-- /claim --> |
| Naive human-time guess | n/a | <!-- claim:human_pinball -->7569<!-- /claim --> | <!-- claim:human_pinball_gap -->+5725 to +7440<!-- /claim --> |

The human-time guess is a hand-written table, an assumption rather than a measurement. A difference interval wholly above zero means that baseline is worse at the 95% level.

**Calibration is not stable from week to week.** That P80 coverage pools <!-- claim:weeks_tested -->4<!-- /claim --> test weeks, and the pooled figure hides misses in both directions. Week by week it ran from <!-- claim:weekly_p80 -->67% to 86%<!-- /claim --> with each session counting equally, and from <!-- claim:weekly_p80_row -->60% to 94%<!-- /claim --> with each run counting equally. In the latest week, <!-- claim:latest_week -->2026-W45<!-- /claim -->, it was <!-- claim:latest_week_p80 -->72%<!-- /claim --> per session and <!-- claim:latest_week_p80_row -->60%<!-- /claim --> per run, from only <!-- claim:latest_week_sessions -->8<!-- /claim --> sessions. Read the pooled P80 as an average over weeks, not a promise for any one week. The [week-by-week table](docs/backtest.md#time-split-week-by-week) has every fold.

**Where it falls short.**

- The median error on a single task is <!-- claim:task_mdape -->78%<!-- /claim -->. Use the range, not the middle.
- Pooled, P80 coverage is <!-- claim:task_p80_row -->89.1%<!-- /claim --> when each run counts equally rather than each session, so the ranges are too wide for the small, fast runs that dominate the row count.
- A global median beats agentpace on MAPE (<!-- claim:median_mape -->232%<!-- /claim --> against <!-- claim:task_mape -->511%<!-- /claim -->), and a sum of medians beats it on plan MAPE (<!-- claim:naive_plan_mape -->85%<!-- /claim --> against <!-- claim:plan_mape -->249%<!-- /claim -->), though that sum covers only <!-- claim:naive_plan_p80 -->5%<!-- /claim --> of sessions at P80. MAPE punishes the long tail of over-forecasts that honest ranges need.
- The report lists <!-- claim:loss_count -->9<!-- /claim --> places where a baseline won. That list is generated, so it cannot be edited out.
- On the project split, P80 coverage is <!-- claim:project_p80 -->77.8%<!-- /claim -->. The contributor split <!-- claim:contributor_split -->could not run (1 contributor)<!-- /claim -->.
- The session-weighted design this project started with did not beat the simpler unweighted one, so the simpler one shipped. The selection rule was fixed before the first result.

![P80 coverage by method](docs/assets/backtest-coverage.svg)

## Privacy

agentpace never reads or stores prompts, code, file paths, repo names or commit messages.

- **What gets read.** From each transcript: timestamps, model ids, token counts, tool names, error flags and two metadata keys. JSON is decoded one line at a time, so other text passes through memory, but only those values are copied out. A test plants marker strings in every sensitive field and proves none reach any output.
- **What a row holds.** Only the fields in [schema/run.schema.json](schema/run.schema.json). Every string is an enum or a strict pattern, so free text has nowhere to land.
- **Who you are.** Keyed hashes under a random secret that never leaves your machine. No email, handle or hostname goes in, so nobody can reverse them by guessing names.
- **Nothing is sent.** There is no network code in this package at all, and a test fails if any is added. Contributing is a git push you run yourself. The site loads nothing from anywhere else.

Rows are not anonymous in every sense. They link to each other, they show roughly when you worked, and a pull request ties them to your GitHub account. `agentpace share` says so before it writes anything.

## Contribute your runs

Sharing is opt-in, and your rows join a public-domain dataset.

```bash
git clone https://github.com/<your-fork>/agentpace && cd agentpace
agentpace ingest
agentpace share --dry-run    # prints the exact rows and writes nothing
agentpace share --write      # writes a file to data/runs, and still sends nothing
agentpace validate           # the same checks CI runs
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

- **One person's workflow.** Every run comes from <!-- claim:contributors -->1<!-- /claim --> contributor, across <!-- claim:projects -->13<!-- /claim --> projects and <!-- claim:sessions -->82<!-- /claim --> sessions, from <!-- claim:first_week -->2026-W40<!-- /claim --> to <!-- claim:last_week -->2026-W45<!-- /claim -->. One project holds <!-- claim:top_project -->71%<!-- /claim --> of runs, and three sessions hold <!-- claim:top_sessions -->48%<!-- /claim -->. It includes heavy automated fan-out, so it may not resemble your setup.
- **Not a model speed ranking.** People send harder work to stronger models, so a slower row may just mean harder tasks.
- **`feature` means any implementation work.** Without reading prompts, metadata cannot separate features, bug fixes and refactors. Those kinds stay thin until people label runs.
- **Claude Code only.** The adapter interface is ready for other harnesses, but no other adapter exists yet.
- **Agent time only.** It leaves out human review and decisions, which often take longer.
- **Failed runs are under-counted.** Only runs with a clear completion signal train the estimator, so P90 may run low.
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

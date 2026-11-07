# agentpace design

Status: approved 2026-11-07. Section 11 records the rulings. The maintainer named Fable 5.1 as the deciding expert, so later design decisions go to it and only outward steps return to the maintainer.

agentpace answers "how long do coding agents really take?" from real runs. It has three parts: an open dataset, a standard-library Python CLI, and an agent skill. It always answers with a P50/P80/P90 range.

Numbers in this document come from a read-only aggregate probe of one maintainer's local Claude Code history on 2026-11-07. The probe read only timestamps, model ids, usage counters, tool names, error flags and two metadata keys, and printed only totals. It is not in the repo yet. Phase 1 re-derives these numbers with `agentpace ingest`, and nothing here reaches the README unless a repo command reproduces it.

## 1. What the evidence changes

The prior art and the probe overturned several things that looked simple. Each finding below drives a design rule later in this document.

| # | Finding | Evidence | Consequence |
|---|---------|----------|-------------|
| F1 | Real data exists, but it is one person's | 3,431 subagent transcripts, 3,217 usable, 13 project folders, 6 ISO weeks (2026-W40 to W45) | Enough to seed, but a contributor split cannot run yet. A project split runs in its place, under its own name (D3) |
| F2 | Runs are heavily clustered | One project holds 71% of runs. The top 3 sessions hold 48%. The largest single session holds 656 runs | Row count overstates evidence. Weighting and intervals must respect sessions |
| F3 | Row-level `parallel_agents` cannot teach parallelism | Within matched cells, the log-duration slope against concurrency is -0.22, so more parallel runs look faster. Fan-out is simply used for small jobs | A naive contention fit is confounded and would produce nonsense. Parallelism must be measured per session (D2) |
| F4 | Realized parallelism is far below nominal | Session work divided by busy time: median 1.11, P75 1.44, over 63 sessions with at least 5 runs | Dividing by the agent cap would badly underestimate totals |
| F5 | Durations are heavy-tailed and stay that way | Global log-SD 1.43. Within edit-count buckets it is still 0.73 to 1.45. A log-SD of 1.2 means P90 is about 4.7 times P50 | Ranges will be wide. We report that rather than hide it |
| F6 | The obvious size signals are outcomes | Rank correlation with duration: tool calls 0.82, output tokens 0.57 | Any post-hoc size leaks the answer into a backtest (section 6) |
| F7 | The distribution shifts week to week | Implementer median by week ranged from 47 s to 1,630 s. Forward split: global P80 covered 92.1% of test rows, a per-agent-type P80 covered 90.1%, both against a nominal 80% | Coverage on a time split is a real test, not a formality. Calibration may miss in both directions |
| F8 | `stop_reason` does not reveal outcome | 98% of runs end on `tool_use`. `finished` is present on 18%. A terminal `SubagentHandback` or `StructuredOutput` call appears in 3,199 of 3,217 runs | Outcome rule in section 3 |
| F9 | Transcripts over-count tokens | Each content block repeats the message usage. Naive sum divided by deduplicated sum: median 1.25, P90 2.36 | Deduplicate by `message.id` |
| F10 | Wall clock includes sleep and resumes | 99.9% of gaps are under 613 s, yet 36 runs contain a gap over 30 minutes and the longest run spans 27 hours | Clip idle gaps (section 3) |
| F11 | Deep paths exceed the Windows 260-character limit | The first probe silently lost 32 runs | Use `\\?\` paths and always report skipped files |

None of the five audited repos handles F2, F3, F6 or F9. Those are where this project can be honestly better.

## 2. Run schema

`schema/run.schema.json` is the only definition of what a row may hold. It sets `additionalProperties: false`, and every string is an enum or a strict pattern, so free text has no field to land in.

| Field | Type | Definition |
|-------|------|------------|
| `harness` | enum | `claude-code` for now |
| `model` | pattern | Normalized public family such as `claude-opus-5-5`. Anything else, such as a Bedrock ARN or an internal proxy name, becomes `other` because those can leak account or customer names |
| `task_kind` | enum | `feature`, `bugfix`, `refactor`, `migration`, `tests`, `docs`, `review`, `other` |
| `risk` | enum | `low`, `normal`, `high` |
| `size` | enum | `S`, `M`, `L`, `XL`, or `na` for read-only work |
| `duration_s` | integer > 0 | Wall clock with idle gaps clipped (section 3) |
| `tool_calls` | integer >= 0 | Count of `tool_use` blocks |
| `tokens` | integer, optional | Deduplicated input plus output tokens, excluding cache reads |
| `retries` | integer >= 0 | Count of errored tool results. This is a proxy, since a real retry cannot be seen without reading content |
| `outcome` | enum | `completed`, `failed`, `unknown` |
| `parallel_agents` | integer >= 1 | Peak sibling subagents overlapping this run |
| `contributor` | 16 hex | HMAC of a fixed label under a random local secret. No identity goes in (section 7) |
| `week` | pattern | ISO week in UTC, for example `2026-W44` |
| `batch` | 12 hex | HMAC of the session id under the same secret |
| `project` | 12 hex, optional | HMAC of `project-v1:<folder>` under the same secret. Enables the leave-one-project-out split. `share --drop-project` omits it |
| `offset_s` | integer >= 0 | Run start on the session's own clock, measured from its first run with idle gaps over 900 s removed. It carries no clock time, and `offset_s + duration_s` gives a run's end on that same clock |

Size thresholds, counted from edit-style tool calls: `S` 1 to 3, `M` 4 to 10, `L` 11 to 30, `XL` 31 or more, `na` for none. These are measured after the run, which is why sections 5 and 6 keep size out of the default forecast.

The duplicate key is (`contributor`, `batch`, `offset_s`, `duration_s`), which identifies a run rather than its labels. A full-row hash would count a relabeled re-share twice.

The local project folder name is an input to the `project` HMAC and is never stored. It is the same folder name the tool has to walk to find transcripts at all.

## 3. Adapters

```python
class Adapter(Protocol):
    name: str

    def discover(self, path: Path) -> Iterator[Source]: ...
    def parse(self, source: Source) -> RawRun | None: ...
```

`RawRun` is a frozen dataclass of allowed metrics. Nothing else can leave an adapter because nothing else has a slot. Normalizing, classifying and pseudonymizing happen once, downstream, for every harness. Adding a harness means one new file and one registry entry.

The Claude Code adapter reads `subagents/**/agent-*.jsonl` and the sibling `.meta.json`.

- **Allowlist by projection.** JSON has to be decoded a line at a time, so content does pass through memory. A projection function copies only `timestamp`, `type`, `message.model`, `message.id`, `message.usage` counters, `message.stop_reason`, tool-use names and `is_error` flags, and everything else is dropped immediately. From `.meta.json` only `agentType` and `finished` are copied. `description`, `cwd`, `gitBranch`, `rendered`, `wireToolInputs` and `toolUseResult` are never copied out of the parsed line.
- **`agentType` is user text.** Custom agent names and plugin prefixes are user-chosen strings. They are used only as a lookup key in section 4 and never reach a row, warning or log.
- **Canary test.** Fixtures plant marker strings in every sensitive field, including `agentType` and error paths, and a test asserts no marker reaches any output, log or exception message.
- **Duration.** `duration_s` is the sum over consecutive `assistant` and `user` timestamps of `min(gap, 900)`. The 900 s cap sits above the observed 99.9th percentile of 613 s. Phase 4 reports sensitivity at 600 s and 1,800 s.
- **Model.** The most frequent `message.model`, ignoring `<synthetic>`.
- **Outcome.** `failed` on a `refusal` stop reason. Otherwise `completed` if `finished` is true, the last tool is a terminal hand-back, or the last stop reason is `end_turn` or `stop_sequence`. Otherwise `failed` on an errored final tool result, else `unknown`. Only `completed` runs train the estimator. The excluded share is printed, because dropping failures biases P90 low.
- **Scope.** Subagents only. Main-session transcripts contain human waiting and are out of scope for v1.
- **Python 3.10.** `fromisoformat` does not accept a trailing `Z` before 3.11, so it is normalized first.
- **Retention.** Claude Code cleans old transcripts (`cleanupPeriodDays`, documented default 30), so `ingest` is incremental and the docs tell users to run it regularly. The transcript format has no official schema, so unknown record types are counted and skipped, never fatal.

Future OpenTelemetry mapping: `claude_code.tool_result.duration_ms`, `claude_code.api_request` (tokens, model) and the `subagent_completed` event, using metadata attributes only. OTel GenAI semantic conventions are still marked Development, so this stays a documented mapping until they settle.

## 4. Classification

Rules are pure functions with no LLM and no text matching.

The privacy rule removes the signal competitors use. claude-eta and mythical-agent-month run regexes over prompt text or task labels. agentpace reads neither, so the classifier uses what is knowable before the run: the agent type.

| Agent type (plugin prefix stripped) | `task_kind` | `risk` |
|-------------------------------------|-------------|--------|
| `reviewer`, `security`, `performance`, `architect`, `verifier` | `review` | `low` |
| `researcher`, `Explore`, `web-fetch` | `other` | `low` |
| `docs` | `docs` | `normal` |
| `qa` | `tests` | `normal` |
| `debugger` | `bugfix` | `normal` |
| `db-migration` | `migration` | `high` |
| `implementer` | `feature` | `normal` |
| anything else | `other` | `normal` |

Order of precedence: user label, then `--kind-map name=kind`, then this table. Labels come from `agentpace label` or `/pace-log`.

The honest limit is that metadata cannot separate `feature`, `bugfix` and `refactor`. In seed data `feature` means "implementation work of any kind", and `data/README.md` must say so. `bugfix` and `refactor` cells stay near-empty until people label runs. Tool-mix heuristics were rejected because they use post-run information and would present guesses as classification.

## 5. Estimator

All math lives in `src/agentpace/estimate.py`. Fields used as predictors: `model`, `task_kind`, `risk`, and `size` only with `--use-size`. Never `tool_calls`, `tokens`, `retries` or `outcome`.

**Input.** Tasks JSON: `{"agent_cap": 4, "tasks": [{"id", "kind", "size", "risk", "depends_on"}]}`, with an optional `model`.

`size` is accepted as the brief specifies, but it is ignored by default. Stored `size` is an edit count measured after the run (F6), and a user's S/M/L guess made before the run is a different quantity. `--use-size` opts in, and the output names the oracle-size track. Phase 4 shows what that costs or gains. If the backtest supports it, this default can change.

**Single-task distribution, on the log scale**

Every run gets a session weight of `1 / sqrt(runs in that session)` (F2), and `n_eff` is the Kish effective size under those weights.

1. Back off through `model×kind×risk`, `kind×risk`, `kind`, then global. With `--use-size`, `size` joins each level.
2. Shrink each cell's session-weighted mean toward its parent: `mu = w * mean_log + (1 - w) * mu_parent`, with `w = n_eff / (n_eff + k)`. `k` is estimated per back-off level as within-cell variance over between-cell variance, not hand-set, with the between-cell variance clamped at zero.
3. Compute each run's residual around its own shrunk `mu`, then split it on training data: `r = b_s + e`, where `b_s` is the session mean of the residuals and `e` is the remainder. Pool `e` over the nearest ancestor with `n_eff >= 30`, keeping the real tail shape rather than assuming a log-normal. `b` is drawn only from sessions with at least 5 runs.
4. Add mean uncertainty `delta ~ N(0, v)`, drawn once per distinct cell per simulation, not per task. Otherwise a 100-task plan would average the uncertainty away. `v = s² / (n_eff + k) + (1 - w)² * v_parent`, which stays finite for a cell never seen and equals the between-cell variance plus the parent's uncertainty. This is the interval widening the brief asks for, and it grows automatically as data thins.
5. When `n_eff < 30`, emit a warning naming the pooled cell, its `n` and its `n_eff`.

For P90, a distribution-free interval needs real sample size. Using the extreme order statistics as bounds, a two-sided 95% interval needs n >= 6 at P50, n >= 14 at P80 and n >= 29 at P90. That is why 30 is a sensible pooling threshold.

**Plan totals, by seeded Monte Carlo (20,000 draws)**

```text
for each draw:
    b      ~ resampled session effect       # shared by all tasks in the plan
    delta  ~ one value per distinct cell    # shared by tasks in that cell
    P      ~ resampled P_eff for agent_cap  # sessions vary, so this is a draw too
    d[t]   = exp(mu[t] + delta[cell(t)] + b + e[t])
    CP     = longest path over depends_on
    W      = sum(d)
    T      = max(CP, W / P)
```

`T` is a lower-bound makespan with a learned throughput term, not a task scheduler, and the docs say so.

- **Why not sum task P80s.** Sums of quantiles overstate the total, and a deterministic critical path understates it, because the expected maximum of merging paths exceeds the maximum of the expected values (PERT merge bias). Simulation handles both.
- **Why `b` matters.** With independent draws, the total of a 100-task plan concentrates and the range becomes falsely narrow. Tasks in one session share a repo, a model and a day. mythical-agent-month's own held-out test missed wall clock by 37%, which is the kind of miss independent intervals hide. `b` comes from between-session residuals and needs `batch` (D2). `e` excludes that component, so the session effect is not counted twice.
- **`P_eff`.** For each training session, `P_eff = work / active span`, grouped by peak concurrency and capped at `agent_cap`. The bucket medians must be non-decreasing, and each draw samples a session from the bucket. The reported factor is the median of that bucket. It describes what sessions achieved, and peak concurrency is only a proxy for a cap.
- **Contention.** No slowdown term is applied, because F3 shows the available estimate is confounded. A term can be added only if it wins in Phase 4.

**Output.** Per-task and total P50/P80/P90, the most frequent critical path with each task's criticality share, `P_eff` with its session count, the cell actually used for each task, and all warnings. Results are byte-identical for a given dataset, seed, interpreter and platform. Across platforms the claim is equal after rounding.

## 6. Backtest protocol

`agentpace backtest` regenerates `docs/backtest.md` and an SVG chart with a fixed seed. CI re-runs it and fails if any reported number differs, comparing rounded values rather than chart bytes so results hold across Python versions and operating systems. Published numbers cannot drift from the code.

**Splits**

- **Time:** rolling origin. Train on weeks up to W, test on later weeks. A whole session belongs to the week of its first run, because sessions can cross week boundaries and would otherwise land on both sides.
- **Contributor:** leave one contributor out. With fewer than two contributors it prints "could not run (1 contributor)" and never substitutes another split under this name.
- **Project:** leave one project out, reported under its own name. It tests generalization across codebases, not across people.
- Random row splits are not used, because F2 would leak sessions across the split.

**Tracks.** This is the main guard against self-deception (F6).

- **Pre-run:** `model`, `task_kind`, `risk`. This is the honest forecast and the headline.
- **Oracle-size:** adds post-hoc `size`. It is labeled as an upper bound and never used as a headline.

**Metrics.** MAPE, P80 coverage and pinball loss at 0.5, 0.8 and 0.9, as specified, for task and for plan totals. Test rows are `completed` runs only, with the excluded share printed beside the results. Added because MAPE alone misleads on heavy tails: median absolute percentage error, mean absolute log error, P50 and P90 coverage, and interval width. A method can score perfect coverage with absurd width, so width is always shown beside coverage. Every metric appears both row-weighted and session-weighted. Comparisons carry a session-cluster bootstrap interval.

**Pre-registered selection (E2).** There are at most five candidates: session-weighted shrinkage, unweighted shrinkage, a back-off table, a per-kind table, and global quantiles. The primary metric is session-weighted pinball loss summed over 0.5, 0.8 and 0.9 on the time split. Ties inside the session-bootstrap interval go to the simpler model. The winner ships as the task-level default, and all losses print regardless. The session effect and `P_eff` stay in every case, because baselines cannot produce totals.

**Plan-level test.** Each held-out session is treated as a plan and predicted from earlier data only. The actual value is the span of its runs on the session clock: `max(offset_s + duration_s) - min(offset_s)`. This is the only direct test of the total, the number users actually care about, and it needs D2. It is labeled "oracle-structure" for two reasons: real dependencies are not recorded, so `CP` falls back to the longest single run, and the session's observed peak concurrency stands in for its cap even though that is itself an outcome. So it tests the throughput and session-effect terms, not dependency planning, and the report must say so.

**Idle-cap sensitivity.** The 900 s cap is `ingest --idle-cap`. Published rows cannot be re-clipped without the raw transcripts, so this check runs locally and is not a README claim.

**Baselines**

| Baseline | Definition |
|----------|------------|
| Naive human time | A hand-written size table, labeled as assumption, not measurement |
| Fixed minutes per round | 3 minutes per round, the ZhangHanDong anchor, with rounds from a size table |
| Fixed minutes per round, oracle | 3 minutes times actual `tool_calls`. It may well beat us, since it uses the answer, and is shown anyway |
| Global median | Global train quantiles |

**Reporting.** The report ends with a generated "Where agentpace loses" section listing every split and metric where a baseline won. It cannot be edited out by hand. Expect some losses: the probe's global P80 was already off by 12 points, and per-agent quantiles only recovered 2 of them.

Split conformal scaling is evaluated in Phase 4 and adopted only if it wins on holdout, since time splits break the exchangeability it relies on.

## 7. Dataset, privacy and validation

**Pseudonym.** On first run the CLI generates a 32-byte random secret in the user's config directory, never uploaded. `contributor = HMAC(secret, "contributor-v1")[:16 hex]`. No email, handle or hostname goes in, so a dictionary attack has nothing to guess. A project-wide salt would not give this protection, because it would be public in the repo. Rotating the secret makes a new contributor.

`contributor` always means one person, in seed files too. `project` is a separate optional pseudonym, so the column keeps one meaning when a second contributor arrives.

**Residual risk, stated plainly.** Rows from one contributor link to each other by design, since contributor holdout needs that. `batch` links rows from one session. The week and the mix of models reveal when someone worked and on what plan tier. Submitting through a pull request ties that pseudonym to the contributor's GitHub identity in public history. `share` says all of this before it writes anything.

**`share`**

- Defaults to `--dry-run` and prints the exact rows, byte for byte, as they would be written.
- `--write` creates `data/runs/<contributor>-<from>-<to>.jsonl` and prints the git commands to submit it.
- No network code at all in v1 (D5). A test parses every module under `src/` and fails on any import of `socket`, `urllib`, `http` or `subprocess`. A `gh` submit path is a v2 item with its own grant.
- `share` is a local file writer, so the DONE WHEN line is met as a ceiling: zero network calls anywhere.
- Submitting a data pull request is a CC0-1.0 dedication, stated in `CONTRIBUTING.md`.

**CI validation (`validate-data.yml`).**

- Trigger is `pull_request`, never `pull_request_target`, with `contents: read` and actions pinned to commit SHAs. Contributed files are parsed, never executed.
- Runs `agentpace validate`, which uses a small built-in schema checker with no `jsonschema` dependency.
- Rejects unknown fields, duplicate rows within and across files, and outliers: `duration_s` over 86,400, `tool_calls` above 5 per second, or `outcome=completed` with zero activity.
- Rejects PII-looking strings across raw bytes: email, filesystem path, URL, IP address, API-key shapes.

**Leaderboard.** Median, P75 and P90 by `model × task_kind × risk`, with n shown on every cell and cells with n<10 greyed out. Greyed cells still show their timing, as the brief specifies. Rare model combinations are the quasi-identifiers in this data, and a stricter suppression rule can be added if contributors ask for one.

**Site.** One `docs/index.html`, with no build step and no third-party requests. The leaderboard JSON is embedded inline, so it works from `file://`. Fetching it would add a network call. `agentpace site` regenerates the embedded block and CI checks that it is current.

## 8. Skill and install

**Finding, from the docs and to be proven in Phase 7.** The Claude Code docs describe one skill folder as giving one slash command, so `skills/agentpace/SKILL.md` alone would not provide `/pace-estimate`, `/pace-log` and `/pace-share`. The `skills` CLI docs say a shallower `SKILL.md` hides nested ones, so they could not be nested inside it either. The open standard does not define `/name` invocation for other harnesses.

**Layout (D6).** Four sibling folders. `skills/agentpace/` holds the guidance, the portable paste-in prompt and the method summary, and auto-triggers on estimate questions. `skills/pace-estimate/`, `skills/pace-log/` and `skills/pace-share/` are thin commands. Frontmatter stays within the six fields of the open standard for portability. Reports that extra keys fail claude.ai uploads are unverified, and that check is a Phase 7 step.

**Install.** `uv tool install git+https://github.com/mohamedzhioua/agentpace` or the `pipx` equivalent, with no PyPI publish needed. The wheel carries `schema/` and `data/runs/` through hatchling `force-include`, so an install can estimate before any local history exists, with no duplicate copy in git and no download at run time. `npx skills add` has no post-install hook and we will not add an implicit download, so a skill checks for `agentpace` and shows the install command if it is missing. Phase 7 proves this with a real `npx skills add` run, which needs Node 22.20 or later.

## 9. How this differs

Audited at the commits cloned on 2026-11-07. Key citations were spot-checked against the source.

| | agent-estimate | claude-eta | agent-estimation | web-dev-estimation | mythical-agent-month | agentpace (planned) |
|---|---|---|---|---|---|---|
| Base | PERT tiers in minutes | Per-turn seconds | Tool-call rounds | Human time × multipliers | Size tables, PERT | Observed run durations |
| Numbers come from | Hand-set triples, private small sample | User's own hook data | Hand-set | Hand-set | One 398-run build | Open rows |
| Output | O/M/P, σ | P50, P80 | Low to high | Mandatory range | Likely, range, plan | P50, P80, P90 |
| Runs in repo | None | None | None | None | None (shape file only) | Yes |
| Multi-agent | DAG, LPT waves | None | Waves | None | Critical path, 1.8 default | DAG simulation, learned `P_eff` |
| Published check | Coverage-class audit, needs OACP files | Walk-forward: n=285, P80 76.8%, MdAPE 79% | None | Prompt evals | One holdout, -24% and -37% | Time and project holdouts on published rows, regenerated by CI. Contributor holdout starts with a second contributor |
| Dependencies | typer, pydantic, networkx, others | None at runtime | None | None | None | None |
| License | Apache-2.0 | MIT | MIT | Apache-2.0 in skill frontmatter, none at repo level | MIT | MIT code, CC0-1.0 data |

What each does better than we plan to, which the README will state:

- **agent-estimate:** binds forecasts to receipts so one issued after work started cannot score. Also forecasts token cost, and ships as a pip package and a GitHub Action.
- **claude-eta:** gives live per-turn ETAs inside a session, with a real walk-forward evaluation on its own history. It also has consent-gated community sharing and a repair-loop detector. Our closest methodological peer.
- **agent-estimation:** the simplest and most teachable model, and it needs no data at all.
- **web-dev-estimation:** the best skill structure, with batch and re-estimate modes that work from documents before any run history exists.
- **mythical-agent-month:** separates machine, human and waiting time and tracks usage budgets. It openly publishes its own held-out miss.

Where we are genuinely different: published rows, a reproducible backtest that includes losses, and plan-level validation. Until other people contribute, agentpace is also one person's workflow, the same n=1 weakness noted above. The README must say so.

Public context, used carefully: METR's 2025 trial (16 developers, 246 tasks) found AI-allowed tasks took 19% longer, and METR's 2026 update calls its newer data too unreliable for a current estimate, partly because parallel agents make time logging hard. METR's time horizons measure task difficulty in human-expert time, not how long agents run, so we never equate them. We found no public dataset of agent run durations from telemetry, which is "not found", not "none exists".

## 10. Engineering baseline and phases

```text
schema/run.schema.json        src/agentpace/{cli,schema,classify,estimate,backtest,
data/{README,LICENSE},runs/     report,share,validate,site}.py
docs/{DESIGN,backtest}.md     src/agentpace/adapters/{base,claude_code}.py
docs/index.html               skills/{agentpace,pace-estimate,pace-log,pace-share}/
tests/ (unittest, fixtures    .github/workflows/{ci,validate-data}.yml
  labeled synthetic)
```

- Python 3.10 to 3.14 tested locally, all interpreters already installed. Tests use stdlib `unittest`, so a fresh clone needs no install. `ruff` and `mypy --strict` run as dev tools only.
- Every README number sits between generated markers, and `agentpace claims --check` fails CI on drift.
- CI runs 3.10 and 3.14 on Linux and one Windows job, kept small because Actions minutes are metered.
- Repo files: `CONTRIBUTING`, `SECURITY`, `CHANGELOG`.
- Commits stay local per phase. One push to a feature branch and one pull request happen when all phases are done, so CI runs on the real seed files before `main` carries the code.

## 11. Rulings

Fable 5.1 ruled at maximum effort on 2026-11-07, as the maintainer directed.

| # | Ruling |
|---|--------|
| D1 | Rename the GitHub repo to `agentpace` at push time. The local folder stays. Install and search would otherwise collide with `kiloloop/agent-estimate` |
| D2 | Add `batch` and `offset_s`. Neither carries clock time or identity |
| D3 | Publish seed rows. `contributor` stays one per person. Add optional `project`. The contributor split prints "could not run (1 contributor)", and a leave-one-project-out split runs under its own name. Coarsen nothing else |
| D4 | Add `task_kind: other` and `size: na` |
| D5 | Zero network code in v1, enforced by an AST test over `src/`. `gh` submit is v2 behind its own grant |
| D6 | Four skill folders, proven in Phase 7 |
| D7 | MIT for code, CC0-1.0 for `data/`. `CONTRIBUTING.md` states each data pull request is a CC0 dedication |
| D8 | No per-phase stops. New decisions go to Fable. The maintainer grants outward steps: rename, push, merge, Pages. One push to a feature branch plus one pull request, merged unsquashed by the maintainer |
| E1 | Size thresholds stay. The default ignores `size`, `--use-size` is labeled oracle, and output says "size ignored" when tasks carry it |
| E2 | Ship the winner of the pre-registered selection in section 6 |
| E3 | Duplicate key is (`contributor`, `batch`, `offset_s`, `duration_s`). "Byte-identical" holds per interpreter and platform |

### Maintainer rulings for release

The owner then made Fable 5.1 the acting maintainer. Its rulings on 2026-11-07:

- Merge with a merge commit, so the phase commits survive. Serve Pages from `main` at `/docs`.
- Keep Issues on, and Discussions, Wiki and Projects off. An empty forum reads as abandonment.
- Protect `main` and `v*` tags against deletion and force-push only. Required reviews would double CI for one maintainer.
- Release `v0.1.0` now as a full release. Git installs already work, and PyPI is additive and waits on a step only the owner can take. `publish.yml` stays manual until that step is done, so a release cannot produce a failed run. The README keeps the git install line until the first upload.
- The Codex adapter comes after 0.1.0, because it changes the schema and the validator.

### Results after implementation

- The pre-registered selection chose `shrunk`, not the session-weighted design this document proposed. They tied inside the bootstrap interval, so the tie went to the simpler candidate. Session weighting remains a candidate, and `n_eff` equals `n` under the default.
- `delta` uses `s² / (n_eff + k)`, as section 5 states, rather than the `s² / n_eff` first drafted, which diverges on an unseen cell.
- Within-session noise is scaled by `sqrt(m / (m - 1))` to undo centering, and session effects are scaled down by the share of their variance that is real.
- Two runs can tie on start and duration to the second, so ingest nudges `offset_s` by a second to keep them distinct under the duplicate key.
- The Claude Code adapter's discovery claim is proven by a real `npx skills add`. All four skills install.

## 12. Sources

Repos, audited at: `kiloloop/agent-estimate@8857979`, `MMMProd-Org/claude-eta@5448ae4` (the `mmmprod/claude-eta` URL redirects there), `ZhangHanDong/agent-estimation@71a4697`, `ecappa/web-dev-estimation@c61fb1c`, `MedAdemBHA/mythical-agent-month@b60165f`.

- Agent Skills specification: <https://agentskills.io/specification>
- Claude Code skills, hooks, monitoring: <https://code.claude.com/docs/en/skills>, <https://code.claude.com/docs/en/hooks>, <https://code.claude.com/docs/en/monitoring-usage>
- `skills` CLI: <https://github.com/vercel-labs/skills>
- OpenTelemetry GenAI conventions: <https://github.com/open-telemetry/semantic-conventions-genai>
- METR: <https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/>, <https://metr.org/blog/2026-02-24-uplift-update/>, <https://metr.org/time-horizons/>
- GitHub Actions security: <https://docs.github.com/en/actions/reference/security/secure-use>
- Statistics, cited from memory and to be checked before they appear in the README: Hyndman and Fan 1996 (sample quantiles), Gneiting 2011 (pinball loss), Tofallis 2015 (log accuracy ratio), Angelopoulos and Bates 2021 (conformal prediction), Malcolm et al. 1959 and MacCrimmon and Ryavec 1964 (PERT merge bias), Sweeney 2002 (k-anonymity).

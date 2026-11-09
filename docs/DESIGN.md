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
| `harness` | enum | `claude-code`, or `codex` since 0.5.0 (a Codex turn is one run, and a rollout file is one batch; the run definition lands in section 3 with the adapter), or `gemini-cli` (a Gemini CLI turn is one run, and a session file is one batch; experimental adapter, shipped unverified, section 3), or `opencode` (one user message and the replies to it is one run, and a session is one batch; experimental adapter, shipped unverified, section 3), or `cline` (a Cline turn is one run, and a task is one batch; experimental adapter, shipped unverified, section 3). Additive: no existing row changes |
| `model` | pattern | Normalized public family such as `claude-opus-5-5`. Anything else, such as a Bedrock ARN or an internal proxy name, becomes `other` because those can leak account or customer names |
| `effort` | enum, optional | `low`, `medium`, `high`, `xhigh` or `max`, as Claude Code records it in `.meta.json`. Omitted when unset, and any other value is dropped by the adapter. Not a predictor unless it wins a backtest (added in 0.2.0) |
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
| `offset_s` | integer >= 0 | Run start on the session's own clock, measured from its first run, where each idle gap between runs counts up to 900 s and the rest of it is removed. It carries no clock time, and `offset_s + duration_s` gives a run's end on that same clock |

Size thresholds, counted from edit-style tool calls: `S` 1 to 3, `M` 4 to 10, `L` 11 to 30, `XL` 31 or more, `na` for none. These are measured after the run, which is why sections 5 and 6 keep size out of the default forecast.

The duplicate key is (`contributor`, `batch`, `offset_s`, `duration_s`), which identifies a run rather than its labels. A full-row hash would count a relabeled re-share twice.

The local project folder name is an input to the `project` HMAC and is never stored. It is the same folder name the tool has to walk to find transcripts at all.

## 3. Adapters

```python
class Adapter(Protocol):
    name: str
    version: int  # bumped when parsing changes, which invalidates cached runs

    def default_path(self) -> Path: ...
    def discover(self, path: Path) -> Iterator[Source]: ...
    def parse(self, source: Source, idle_cap_s: int, stats: ParseStats) -> list[RawRun]: ...
```

Since 0.5.0, `parse` returns every run in one file, because a Codex rollout holds several turns. The Claude Code adapter returns zero or one. `ParseStats.skipped_files` counts files read but deliberately not used, such as copied history.

`RawRun` is a frozen dataclass of allowed metrics. Nothing else can leave an adapter because nothing else has a slot. Normalizing, classifying and pseudonymizing happen once, downstream, for every harness. Adding a harness means one new file and one registry entry.

The Claude Code adapter reads `subagents/**/agent-*.jsonl` and the sibling `.meta.json`.

- **Allowlist by projection.** JSON has to be decoded a line at a time, so content does pass through memory. A projection function copies only `timestamp`, `type`, `message.model`, `message.id`, `message.usage` counters, `message.stop_reason`, tool-use names and `is_error` flags, and everything else is dropped immediately. From `.meta.json` only `agentType`, `finished` and `effort` are copied, and `effort` only as one of its enum values. `description`, `cwd`, `gitBranch`, `rendered`, `wireToolInputs` and `toolUseResult` are never copied out of the parsed line.
- **`agentType` is user text.** Custom agent names and plugin prefixes are user-chosen strings. They are used only as a lookup key in section 4 and never reach a row, warning or log.
- **Canary test.** Fixtures plant marker strings in every sensitive field, including `agentType` and error paths, and a test asserts no marker reaches any output, log or exception message.
- **Duration.** `duration_s` is the sum over consecutive `assistant` and `user` timestamps of `min(gap, 900)`. The 900 s cap sits above the observed 99.9th percentile of 613 s. Sensitivity at 600 s and 1,800 s was run and is recorded in section 11. It does not change the winner.
- **Model.** The most frequent `message.model`, ignoring `<synthetic>`.
- **Outcome.** `failed` on a `refusal` stop reason. Otherwise `completed` if `finished` is true, the last tool is a terminal hand-back, or the last stop reason is `end_turn` or `stop_sequence`. Otherwise `failed` on an errored final tool result, else `unknown`. Only `completed` runs train the estimator. The excluded share is printed, because dropping failures biases P90 low.
- **Scope.** Run rows come from subagents only. Main-session transcripts contain human waiting, so they never yield run rows. Since 0.3.0 they are read for one other quantity, the wait on the human per human turn, by `agentpace turns` (section 11).
- **Python 3.10.** `fromisoformat` does not accept a trailing `Z` before 3.11, so it is normalized first.
- **Runs still going (0.2.0).** A run that has not completed, and whose last record is younger than the idle cap, is withheld from the rows. It still feeds the session clock and the overlap count, so its siblings get the offsets they will have once it finishes. A completed sibling that starts in the same second is held back with it, because the one-second tie nudge between the two can only settle once both durations are final. Storing it would leave a partial row for good, because `duration_s` is part of the key and the store keeps old rows. The fix withholds, and never supersedes rows by offset: siblings launched in the same second share an offset legitimately: 208 offsets in the seed are held by 2 to 5 completed runs. An agent idle for more than the cap that then resumes still gets a second key. That case is accepted.
- **Cache (0.2.0).** `~/.agentpace/cache-<adapter>.json` maps each transcript path to the size and `mtime_ns` of the transcript and its `.meta.json`, and to the parsed runs, with the model already normalized. Since 0.5.0 (cache format 3) an entry holds a list of runs, empty when the file held none. A run is re-parsed when any stamp changes. The whole cache is discarded on a new format, adapter version or idle cap, or on a decode error, and a non-default `--idle-cap` bypasses it. Runs whose transcript was deleted stay cached and come back for sessions seen in the pass, so cleanup cannot shift surviving siblings' offsets. A warm ingest of 3,234 transcripts takes 3 to 5 s, against about 280 s cold. Keys are `os.path.normcase` paths, so one file never gets two entries on Windows. The file is local only and never shared. Besides `RawRun` fields it holds the transcript paths, so project folder names and agent types sit there raw, as they do in the transcripts themselves. Entries for deleted transcripts are not pruned, at about 300 bytes each.
- **Hook (0.2.0).** `agentpace ingest --hook` reads a `SubagentStop` payload, takes only `agent_transcript_path`, and ingests that whole session folder, because offsets need every sibling. It always exits 0 and prints nothing. `<store>.lock` serializes it with a manual ingest, and the hook gives up after 5 s. A lock older than 15 minutes counts as left by a crash. The lock is never refreshed, so an ingest that runs longer than that can lose its lock, and the damage is a lost update that the next pass repairs. The store and the cache are both written atomically, so a hook killed at its timeout cannot leave either half-written. The snippet is opt-in, with a 30 s timeout, and never installed for the user.
- **Codex (0.5.0).** `adapters/codex.py` reads `~/.codex/sessions/**/rollout-*.jsonl`.
  - **A run is one agent turn.** A `task_started` event opens it. A `task_complete` or `turn_aborted` with the same `turn_id` closes it, and an abort for another turn is ignored. A rollout file is one batch.
  - **Duration.** `_active` runs over the turn's line timestamps, with the same 900 s cap. `duration_ms` is never used, so one duration rule covers both harnesses.
  - **Counts.** `tool_calls` counts function, custom and local-shell calls. `edit_calls` counts completed `FileChange` items, so edits made through a shell command are invisible. `retries` counts finished items whose status is `failed`. `tokens` is the turn's largest cumulative input + output.
  - **Outcome.** A clean `task_complete` is `completed`, one with an error is `failed`, and an abort or an unclosed turn is `unknown`.
  - **Model and effort** come from the matching `turn_context`, else the model is the file's last seen one. A turn with neither is dropped.
  - **Skipped files.** Forked files, files whose metadata names more than one session, and upkeep sessions (`compact`, `memory_consolidation`) are skipped whole and counted. Copied history would otherwise count turns twice.
  - **Skipped runs (adapter version 2, 0.6.0).** A turn whose model is `codex-auto-review` is Codex's automatic approval check on a command. In the maintainer's history these took 3 s at the median and called no tool. They are not tasks anyone plans, so they are skipped and counted in `skipped runs`, never rows. Version 1 emitted them as reviews. `skipped files` and `skipped runs` are counted on a cold ingest only, because the cache stores runs and not counters, so on a warm cache a file whose turns were all skipped counts as one empty run.
  - **Subagent threads** are their own batch, so `parallel_agents` is 1 for Codex rows.
  - **What is copied.** A projection copies only record and event types, timestamps, turn ids, epoch times, the model, an effort from the enum, an error flag, an abort reason from a fixed set, item types and a completed/failed status, two token counters, the session id, a fork flag and the session source reduced to a fixed word. The working folder is a hash input for `project`, kept in memory and the local cache as Claude Code folder names are, and never in a row. Project pseudonyms do not join across harnesses, and rollouts without a working folder share one project pseudonym. Only the exact event names `task_started` and `task_complete` are read, so a turn can never open twice under two names.
- **Gemini CLI (experimental, shipped unverified by the owner's decision).** `adapters/gemini_cli.py` reads `~/.gemini/tmp/<project>/chats/session-*.jsonl` and subagent files one folder below (`GEMINI_CLI_HOME` moves the home folder). It was built from the upstream source at `google-gemini/gemini-cli@62bab5b232c15b925d31e655ce844ab939c882b7`, because the tool could not be installed here, and it is not yet verified on real history. It ships in 0.8.0 labeled experimental (ruling for 0.8.0, section 11). Every format fact in the module cites `file:line` at that commit.
  - **The format.** One JSON record per line: a metadata record (`sessionId`, `projectHash`), message records (`id`, `timestamp`, `type` of `user`, `gemini`, `info`, `warning` or `error`) and `$set`, `$patch` and `$rewindTo` updates. A message is appended again, whole, whenever its tokens or tool calls change, so each id is merged and counted once. Legacy `.json` files are not read, because resuming one copies its history into a `.jsonl`.
  - **A run is one agent turn, inferred.** There is no turn event. Tool results are recorded as `user` messages too, and so is context the CLI injects (environment, IDE context, a compression summary). So a `user` message opens a run only when it is not a tool response and a model response comes before the next `user` message. A model response is a `gemini` message with a model, a token count or tool calls. The run lasts until the next opening prompt. A session file is one batch, and a subagent file is its own batch, as Codex threads are.
  - **Duration.** `_active` over the prompt, each response and each tool call's finish time, with the 900 s cap. Info, warning and error messages, injected context and placeholder replies add no time, since the CLI can write them after the human has been away.
  - **Counts.** `tool_calls` counts distinct tool-call ids. `edit_calls` counts `replace` and `write_file`, so edits made through a shell command are invisible. `retries` counts calls whose status is `error`. `tokens` sums each response's input minus cached input, plus output; thought and tool-prompt tokens are left out.
  - **Outcome.** `completed` when the turn's last response asked for no tool. Otherwise `failed` if the turn holds an `error` message, else `unknown`. An interrupted or failed stream is rolled back in the file; the rollback is not replayed, so the work before it still counts.
  - **Model and effort.** The most frequent model among the turn's responses, else the file's last seen one. Gemini CLI records no effort.
  - **What is copied.** Record shape, message id, timestamp, message type, whether a user message holds a `functionResponse` part (only its presence is tested), the model, three token counters, and per tool call its id, an edit flag, an error flag and its time. The session id and `projectHash` are hash inputs only. `projectHash` is the CLI's own SHA-256 of the project folder, so the folder itself is never read. Metadata naming two sessions skips the file, and unknown or malformed lines are counted in `unparsed lines`.
- **OpenCode (experimental, shipped unverified by the owner's decision).** `adapters/opencode.py` was built from the upstream source of `opencode-ai` 1.19.5, `anomalyco/opencode` at `810c0eba1a7348036f8bdf61e6632d0c36c6876c` (tag `v1.19.5`), without a single real session; the module docstring cites each format fact as file and line at that commit. It ships in 0.8.0 labeled experimental (ruling for 0.8.0, section 11).
  - **Storage.** One SQLite database, `opencode.db`, or `opencode-<channel>.db` for a preview channel, in `$XDG_DATA_HOME/opencode`, else `~/.local/share/opencode`, Windows included. It is opened read-only with the standard library's `sqlite3`, and the `-wal` file is a cache companion. Only the top level of the folder is searched. The JSON files of releases before the database are not read.
  - **A run is one user message and the assistant messages answering it.** OpenCode writes one assistant message per model step, each with `parentID` set to the user message, and stops on a finish reason other than `tool-calls`. The human wait sits before the user message, outside the run. A session is one batch. A task subagent's child session is its own batch, as Codex subagent threads are, so `parallel_agents` is 1, and the child's agent name is its `agent_type`, as Claude Code's is; a top-level run has none.
  - **Duration.** `_active` runs over the assistant messages' created and completed times, the tool parts' start and end times and each part's first-write time, with the same 900 s cap. The user message's own time is not used.
  - **Counts.** `tool_calls` counts tool parts. `edit_calls` counts `edit`, `write` and `apply_patch` calls. `retries` counts tool parts whose status is `error`, except those OpenCode closed as `interrupted` when the user stopped the run. `tokens` sums input, output and reasoning over the run's step-finish parts, or the message's own counters when it has none; OpenCode stores input without cache reads and writes, and output without reasoning.
  - **Outcome**, from the run's last assistant message: an abort (`MessageAbortedError`) is `unknown`, any other error is `failed`, a completed step whose finish is `stop` is `completed`, and anything else (`length`, no end) is `unknown`.
  - **Model and effort.** The most frequent `modelID`. A dotted Claude version such as `claude-sonnet-4.5`, written by some providers, normalizes to `claude-sonnet-4-5` by the shared rule in `ingest.normalize_model`, which applies to every harness, because the families alone would cut it to `claude-sonnet-4`; no family was added. The `variant` survives as effort only when it is a schema value.
  - **Skipped runs.** Compaction summaries are upkeep. A forked session copies earlier messages with their original times, so a message older than its own session is a copy; a run made only of copies is skipped and counted.
  - **What is copied.** A projection copies only `parentID`, epoch times, the model id, the agent name, an effort from the enum, summary, aborted and error flags, whether the finish is `stop`, token counters, part types, an edit-tool flag, a tool failure flag and tool times. From a session row it reads the id, whether it has a parent, its creation time and its folder, which is a hash input for `project`, kept in memory and the local cache as Codex folders are. Unknown and malformed message and part rows are counted under `unparsed lines` and skipped. SQLite error messages are discarded.
- **Cline (experimental, shipped unverified by the owner's decision).** `adapters/cline.py` was built from the upstream source, `cline/cline` at `94eecbbe1a2f899c66b118d13656651b0a884e1f` (main) and `7761370240ffce9e3890cb56f6c51655f3f5b0a7` (the `legacy-extension` branch, whose writer the stable package still runs for most users), and has not yet been checked against real history. It ships in 0.8.0 labeled experimental (ruling for 0.8.0, section 11). Its module docstring cites each format fact by file and line.
  - **What it reads.** `<storage>/tasks/<id>/ui_messages.json`, where `<storage>` is VS Code's global storage folder for `saoudrizwan.claude-dev`, plus `state/taskHistory.json` for each task's starting folder and `task_metadata.json` as a model fallback. Tasks made by the SDK bundle ("Next"), stored in `~/.cline/data/sessions`, are not read.
  - **A run is one agent turn**, mirroring Codex. Every message carries its creation time in milliseconds. A turn opens on the first message after the previous one closed, and closes on the ask that hands the task back: `completion_result`, `followup` or `plan_mode_respond` close it as `completed`. A `resume_task` ask, or the end of the file, leaves it `failed` when its last request failed and `unknown` otherwise, and it ends at the message before the reopening. A task is one batch.
  - **Approval waits are not agent time.** Any other ask, apart from a running command's `command_output`, waits on a human decision, so the gap from it to the next message is dropped whole. The 900 s cap clips the remaining gaps, as for the other harnesses. A manually approved file write also loses the time the model spent streaming the file, because Cline stamps the ask when its row first appears.
  - **Counts.** `tool_calls` counts tool, command, MCP and browser-launch messages (one each, a `say` when auto-approved, an `ask` otherwise). `edit_calls` counts tool messages that edit, create or delete a file. `retries` counts error messages and failed-request asks. `tokens` sums `tokensIn + tokensOut` over the turn's requests. Repeated identical messages count once. Malformed messages and unknown message names are counted as unparsed and skipped.
  - **What is copied.** Times, message kinds and names from the fixed upstream set, `modelInfo.modelId`, two token counters, a cancel reason from a fixed set, whether a tool message edits a file, and from the index only task ids and starting folders. The starting folder is a hash input for `project`, held as Codex working folders are. Rows carry no agent type, so they classify as `other`, `normal`.
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
| Codex: a review session (emitted as `reviewer`) | `review` | `low` |
| Codex: any other turn (no agent type) | `other` | `normal` |

Order of precedence: user label, then `--kind-map name=kind`, then this table. Labels come from `agentpace label` or `/pace-log`.

The honest limit is that metadata cannot separate `feature`, `bugfix` and `refactor`. In seed data `feature` means "implementation work of any kind", and `data/README.md` must say so. `bugfix` and `refactor` cells stay near-empty until people label runs. Tool-mix heuristics were rejected because they use post-run information and would present guesses as classification.

## 5. Estimator

All math lives in `src/agentpace/estimate.py`. Fields used as predictors: `model`, `task_kind`, `risk`, and `size` only with `--use-size`. Never `tool_calls`, `tokens`, `retries` or `outcome`.

**Input.** Tasks JSON: `{"agent_cap": 4, "tasks": [{"id", "kind", "size", "risk", "depends_on"}]}`, with an optional `model`.

`size` is accepted as the brief specifies, but it is ignored by default. Stored `size` is an edit count measured after the run (F6), and a user's S/M/L guess made before the run is a different quantity. `--use-size` opts in, and the output names the oracle-size track. Phase 4 shows what that costs or gains. If the backtest supports it, this default can change.

**Single-task distribution, on the log scale**

*Superseded:* this section first gave every run a session weight of `1 / sqrt(runs in that session)` (F2), with `n_eff` the Kish effective size under those weights. The pre-registered selection chose the unweighted `shrunk` (section 11), so the default is unweighted and `n_eff` equals `n`. Session weighting survives only as the `weighted` candidate.

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
- **Codex plans (0.6.0).** Each Codex exec session is one turn, so Codex rows add nothing to `b`, which needs sessions of at least 5 runs, or to `P_eff`, where every Codex row has a peak of 1. A plan of Codex tasks uses the session effect and throughput learned from Claude Code sessions.
- **Contention.** No slowdown term is applied, because F3 shows the available estimate is confounded. A term can be added only if it wins in Phase 4.

**Output.** Per-task and total P50/P80/P90, the most frequent critical path with each task's criticality share, `P_eff` with its session count, the cell actually used for each task, and all warnings. Since 0.2.1, every result also carries the human part (review, testing, decisions, waiting between agent turns) on its own line, as "not measured" and with no allowance added to the total, and JSON carries it as `human`. Since 0.9.2 the output also says that the session clock counts the gaps between runs up to the idle cap, so a wait shorter than the cap sits inside the plan total (ruling for 0.10). No multiplier derives it from agent time. Results are byte-identical for a given dataset, seed, interpreter and platform. Across platforms the claim is equal after rounding.

## 6. Backtest protocol

`agentpace backtest` regenerates `docs/backtest.md` and an SVG chart with a fixed seed. CI re-runs it and fails if any reported number differs, comparing rounded values rather than chart bytes so results hold across Python versions and operating systems. Published numbers cannot drift from the code.

**Splits**

- **Time:** rolling origin. Train on weeks up to W, test on later weeks. A whole session belongs to the week of its first run, because sessions can cross week boundaries and would otherwise land on both sides.
- **Contributor:** leave one contributor out. With fewer than two contributors it prints "could not run (1 contributor)" and never substitutes another split under this name.
- **Project:** leave one project out, reported under its own name. It tests generalization across codebases, not across people. It runs over every harness. Codex project pseudonyms never join Claude Code ones, even for the same folder, so one codebase used from both harnesses counts as two projects. Since 0.6.3 the report also shows each held-out project on its own, numbered by rank and never by pseudonym, and the split's session-weighted pinball without the project holding the most held-out sessions, so one project's vote is visible. Both come from the per-fold forecasts the split already draws. The split never chooses the method.
- **Harness (0.5.0):** leave one harness out. With fewer than two harnesses it prints "could not run (1 harness)". The report also shows each harness on its own: rows, sessions, and the shipped method against the global median on the selection folds. The per-harness tables reuse the selection-fold forecasts already drawn. The split draws its own named streams, like the project split. Neither touches a published number. The estimator does not use the harness. A harness term could only be added as a new candidate appended last, after this split can run. Codex rows join the published seed only through a separate data change that lists every number it moves. With the 0.6.0 seed only the Codex-held-out fold runs, because holding out Claude Code leaves fewer than `MIN_TRAIN_ROWS` completed rows.
- **Selection population (0.6.0):** `SELECTION_HARNESS` in `backtest.py` is `claude-code`, the harness the rule was frozen on. Selection, the time-split tables and intervals, the week-by-week table, the whole-session test and the locked holdout's headline score only its sessions. Rows from every harness still train every candidate, because the shipped estimator trains on them too. A dataset without that harness scores all of its rows. The same rule over every harness is printed under "Pooled, for the record" with its own random stream, and never ships. This scope was registered after a local run with the maintainer's Codex rows pooled in had been seen, and section 11 gives that result.
- **Locked holdout:** `SELECTION_LAST_WEEK` in `backtest.py` is frozen at `2026-W45`. Selection and the headline time-split numbers use only folds up to that week, and selection never re-runs on later ones. Later weeks are forecast with the same per-method random streams, after the selection folds, so they cannot move a published number. They score the shipped method against the baselines in their own section, which prints "not enough data" until at least 20 later sessions have 5 or more completed runs. Below it, every candidate is scored per harness at task level, whatever that status. A harness selection does not score, Codex first, is tested there and nowhere else: whether it would be better served by another method is answered on weeks no method was chosen on. Selection ran on the 0.1.0 seed rows on 2026-11-07, while 2026-W45 was still in progress. Rows from W45 sessions shared later join the W45 selection fold and may move the pooled selection-period figures. They cannot change the shipped method unnoticed, because a test pins the default to the published winner, so a flip fails CI and needs re-registration in `CHANGELOG.md`.
- **Week by week:** the report also lists the shipped method fold by fold, because a pooled coverage can hide weeks that miss in both directions.
- Random row splits are not used, because F2 would leak sessions across the split.

**Tracks.** This is the main guard against self-deception (F6).

- **Pre-run:** `model`, `task_kind`, `risk`. This is the honest forecast and the headline.
- **Oracle-size:** adds post-hoc `size`. It is labeled as an upper bound and never used as a headline.

**Metrics.** MAPE, P80 coverage and pinball loss at 0.5, 0.8 and 0.9, as specified, for task and for plan totals. Test rows are `completed` runs only, with the excluded share printed beside the results. Added because MAPE alone misleads on heavy tails: median absolute percentage error, mean absolute log error, P50 and P90 coverage, and interval width. A method can score perfect coverage with absurd width, so width is always shown beside coverage. Every metric appears both row-weighted and session-weighted. Headline numbers carry a 95% session-cluster bootstrap interval: the shipped method's session-weighted P50, P80 and P90 coverage and pinball loss, each baseline's pinball loss minus ours, and the same for the whole-session test, which reports pinball loss too. Each interval uses its own random stream, so adding one never moves another number.

**Pre-registered selection (E2).** There are at most five candidates: session-weighted shrinkage, unweighted shrinkage, a back-off table, a per-kind table, and global quantiles. The primary metric is session-weighted pinball loss summed over 0.5, 0.8 and 0.9 on the time split. Ties inside the session-bootstrap interval go to the simpler model. The winner ships as the task-level default, and all losses print regardless. The session effect and `P_eff` stay in every case, because baselines cannot produce totals.

**Plan-level test.** Each held-out session is treated as a plan and predicted from earlier data only. The actual value is the span of its runs on the session clock: `max(offset_s + duration_s) - min(offset_s)`. This is the only direct test of the total, the number users actually care about, and it needs D2. It is labeled "oracle-structure" for two reasons: real dependencies are not recorded, so `CP` falls back to the longest single run, and the session's observed peak concurrency stands in for its cap even though that is itself an outcome. So it tests the throughput and session-effect terms, not dependency planning, and the report must say so.

**Idle-cap sensitivity.** The 900 s cap is `ingest --idle-cap`. Published rows cannot be re-clipped without the raw transcripts, so this check runs locally with `ingest --idle-cap` and `backtest --data`, and is not a README claim. The results are in section 11.

**Baselines**

| Baseline | Definition |
|----------|------------|
| Naive human time | A hand-written size table, labeled as assumption, not measurement |
| Fixed minutes per round | 3 minutes per round, the ZhangHanDong anchor, with rounds from a size table |
| Fixed minutes per round, oracle | 3 minutes times actual `tool_calls`. It may well beat us, since it uses the answer, and is shown anyway |
| Global median | Global train quantiles |

**Reporting.** The report ends with a generated "Where agentpace loses" section listing every split and metric where a baseline won. It cannot be edited out by hand. Expect some losses: the probe's global P80 was already off by 12 points, and per-agent quantiles only recovered 2 of them.

**Conformal scaling: dropped.** An earlier draft promised to evaluate split conformal scaling. Fable 5.1 rejected it on 2026-11-07. Calibration drifts in both directions between adjacent weeks (row-weighted P80 coverage went from 94% in 2026-W44 to 60% in 2026-W45), so scaling by the prior week's miss would chase noise, and time splits break the exchangeability conformal methods rely on anyway. The week-by-week table and the locked holdout report the drift instead of correcting for it.

## 7. Dataset, privacy and validation

**Pseudonym.** On first run the CLI generates a 32-byte random secret in the user's config directory, never uploaded. `contributor = HMAC(secret, "contributor-v1")[:16 hex]`. No email, handle or hostname goes in, so a dictionary attack has nothing to guess. A project-wide salt would not give this protection, because it would be public in the repo. Rotating the secret makes a new contributor.

`contributor` always means one person, in seed files too. `project` is a separate optional pseudonym, so the column keeps one meaning when a second contributor arrives.

**Residual risk, stated plainly.** Rows from one contributor link to each other by design, since contributor holdout needs that. `batch` links rows from one session. The week and the mix of models reveal when someone worked and on what plan tier. Submitting through a pull request ties that pseudonym to the contributor's GitHub identity in public history. `share` says all of this before it writes anything.

**`share`**

- Defaults to `--dry-run` and prints the exact rows, byte for byte, as they would be written.
- `--write` adds or extends one file, `data/runs/<contributor>-<from>-<to>.jsonl`, and prints the commands to check and submit it, `agentpace validate` first. Rows already anywhere in the folder are left out. When the name exists, its old bytes are kept exactly and the new rows go after them, so a second share of the same span never removes a published row; a file holding another contributor's rows is refused. Rows from experimental adapters stay in the share, and `share` warns on stderr that `validate` rejects them until the adapter is verified.
- No network code at all in v1 (D5). A test parses every module under `src/` and fails on any import of `socket`, `urllib`, `http` or `subprocess`. A `gh` submit path is a v2 item with its own grant.
- `share` is a local file writer, so the DONE WHEN line is met as a ceiling: zero network calls anywhere.
- Submitting a data pull request is a CC0-1.0 dedication, stated in `CONTRIBUTING.md`.

**Turns stay local, permanently.** `~/.agentpace/turns.jsonl` is never shared in any form: not as records, not binned, not as weekly aggregates. The gap sequence of a session is a relative timeline of one person's working day, and a contributor's public commit timestamps can anchor it to the clock. Coarsening does not help: on the maintainer's history, 78 of 82 sessions stayed unique as an unordered weekly bag of log2-minute bins. An aggregate of one contributor is that contributor, and a published population wait would be read as a constant for the human part, which section 11 forbids. `share` has no code path to the file, and tests prove it.

**CI validation (`validate-data.yml`).**

- Trigger is `pull_request`, never `pull_request_target`, with `contents: read` and actions pinned to commit SHAs. Contributed files are parsed, never executed.
- Runs `agentpace validate`, which uses a small built-in schema checker with no `jsonschema` dependency.
- Rejects unknown fields, duplicate rows within and across files, and outliers: `duration_s` over 86,400, `tool_calls` above 5 per second, or `outcome=completed` with zero activity.
- Rejects PII-looking strings across raw bytes: email, filesystem path, URL, IP address, API-key shapes.

**Leaderboard.** Median, P75 and P90 by `model × task_kind × risk`, with n shown on every cell and cells with n<10 greyed out. Greyed cells still show their timing, as the brief specifies. Rare model combinations are the quasi-identifiers in this data, and a stricter suppression rule can be added if contributors ask for one.

**Site.** One `docs/index.html`, with no build step and no third-party requests. The leaderboard JSON is embedded inline, so it works from `file://`. Fetching it would add a network call. Since 0.6.1 it shows one image, `assets/hero.webp`, read from beside the page, so nothing leaves the site. A test allows images only under `assets/`. `agentpace site` regenerates the embedded block and CI checks that it is current.

## 8. Skill and install

**Finding, from the docs and to be proven in Phase 7.** The Claude Code docs describe one skill folder as giving one slash command, so `skills/agentpace/SKILL.md` alone would not provide `/pace-estimate`, `/pace-log` and `/pace-share`. The `skills` CLI docs say a shallower `SKILL.md` hides nested ones, so they could not be nested inside it either. The open standard does not define `/name` invocation for other harnesses.

**Layout (D6).** Four sibling folders. `skills/agentpace/` holds the guidance, the portable paste-in prompt and the method summary, and auto-triggers on estimate questions. `skills/pace-estimate/`, `skills/pace-log/` and `skills/pace-share/` are thin commands. Frontmatter stays within the six fields of the open standard for portability. Reports that extra keys fail claude.ai uploads are unverified, and that check is a Phase 7 step.

**Install.** `uv tool install agentpace` or the `pipx` equivalent, from PyPI since 0.9.2. The wheel carries `schema/` and `data/runs/` through hatchling `force-include`, so an install can estimate before any local history exists, with no duplicate copy in git and no download at run time. `npx skills add` has no post-install hook and we will not add an implicit download, so a skill checks for `agentpace` and shows the install command if it is missing. Phase 7 proves this with a real `npx skills add` run, which needs Node 22.20 or later.

**Trigger evals.** Each run used the same 9 prompts, one fresh session per prompt, with only `skills/` installed: 6 questions about how long software work takes, one of them re-answering a quoted "3 month project", and 3 near misses (baking bread, a git rebase's CPU time, and a request to write a date function). These are dated single passes, evidence rather than guarantees, and they are not README claims.

| Date | Host and model | How a trigger was detected | Positives | Near misses |
|---|---|---|---|---|
| 2026-11-07 | Claude Code (CLI version not recorded), Sonnet 5.5 | a Skill call for `agentpace` in `claude -p` stream JSON | 6 of 6 | 0 of 3 |
| 2026-11-07 | Claude Code 2.1.319, Opus 5.5 | the same | 6 of 6 | 0 of 3 |
| 2026-11-07 | Claude Code 2.1.319, Haiku 5.5 | the same | 6 of 6 | 0 of 3 |
| 2026-11-07 | Codex CLI 0.164.0, gpt-6-sol at high effort, skills in the project's `.agents/skills/` | a command in the `codex exec --ephemeral --json` events that reads `agentpace/SKILL.md` | 6 of 6 | 1 of 3 |

The Codex near miss was the git rebase question. Codex read the skill, judged that it covers coding-agent task time rather than a command's CPU use, and answered without it. So the skill loaded but was not applied. The Sonnet row comes from the 0.2.1 pull request, and the other three ran on the 0.6.0 skills.

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
- **Calibration by week (0.2.0).** The pooled session-weighted P80 coverage of 78.5% hides weekly drift in both directions. Per fold, 2026-W42 to W45, `shrunk` covered 86%, 79%, 67% and 72% with each session counting equally, and 88%, 78%, 94% and 60% with each run counting equally. W45 holds only 8 sessions. The README states this beside the pooled number, and `docs/backtest.md` has the table.

**Idle-cap sensitivity**, run locally on 2026-11-07 with `ingest --idle-cap` and `backtest --data`:

| Cap | Winner | Task P80 | Pinball | MdAPE | Plan P50 / P80 / P90 |
|---|---|---:|---:|---:|---:|
| 900 s (published) | shrunk | 78.5% | 1047 | 78% | 59% / 80% / 91% |
| 600 s | shrunk | 78.4% | 1013 | 78% | 61% / 79% / 91% |
| 1,800 s | shrunk | 78.3% | 1107 | 78% | 59% / 79% / 88% |

The winner and the headline coverage hold across the three caps. Pinball moves with the cap because a longer cap leaves longer durations to forecast.

### Rulings for 0.2.1 and 0.3.0

Fable 5.1 ruled on 2026-11-07, after research into why assistants quote human-team timelines for agent work.

- **The human part is named, never derived.** Every estimate states the human part (review, testing, decisions, waiting between agent turns) with its status and keeps it out of the total. Until it is measured, it carries no number and no multiplier. `--quote` is out of 0.2.1. A prior quote returns with the forecast ledger, where it can be scored.
- **Trigger.** The `agentpace` skill triggers on any question about how long software work will take, assumes agents do the implementation, and re-answers a human-scale quote by showing it beside the measured range as unverified.
- **Portable prompt.** No sums of quantiles. The P50 total is the larger of the critical chain and the total work over the agents running at once. P80 and P90 apply the published whole-session ratios (`width80`, `width`), and the result is labeled a rule of thumb.
- **Main-session transcripts (narrow reversal, for 0.3.0).** They stay rejected as a source of run rows. They are admitted as the source of one quantity only, elapsed waiting on the human per human turn, through a separate `agentpace turns` command, a separate local file and schema, and a projection of record type, timestamp, `turnOrigin` and a tool-result flag. Nothing from it reaches `run.schema.json`, `share`, the seed or a README claim.
- **What 0.3.0 shipped.**
  - `agentpace turns` writes `~/.agentpace/turns.jsonl` under `schema/turn.schema.json`. Each record holds a session pseudonym (`turn-v1`, a separate HMAC label, so a turn can never be joined to a run row's `batch`), the ISO week, `gap_s` and `agent_turn_s`.
  - A gap runs from the agent's last record to the next prompt whose `turnOrigin` is `human`. Tool results and other user records, such as task notifications, never open one, and a gap is measured from the agent's last record even across such a record in between.
  - Sessions with no `turnOrigin` at all are skipped and counted, never approximated. So are sessions with fewer than 2 human prompts.
  - Gaps are uncapped and no constant is applied. Re-reading a session replaces its gaps, and gaps from deleted transcripts are kept.
  - `estimate` reads the file when it exists and prints the user's own per-turn P50/P80/P90 with n, as elapsed time, not effort, and outside the total. It never multiplies them by a number of turns.
  - A test proves `share --dry-run` output is unchanged by a turns file.
- **Order.** 0.2.1 is this change plus presentation. 0.3.0 is the measured human part. The forecast ledger follows as 0.4.0, and the Codex adapter as 0.5.0.

### Rulings for 0.4.0

Fable 5.1 ruled on 2026-11-07.

- **Forecast ledger.** `estimate --record` appends one record per forecast to `~/.agentpace/forecasts.jsonl`. It is local only and never read by `share`.
  - The record holds the forecast as shown, the issue time in UTC, the plan document and the human part. It holds no paths, sources or secret-derived values.
  - The id is the first 8 hex of a sha256 over the issue time and the record, and no randomness is used.
  - A quoted timeline is stored as text, and in seconds only when it is exactly "<number> <unit>", by fixed calendar units. It is labeled calendar time, as quoted.
- **Scoring.**
  - Scores are appended, and the latest per forecast wins.
  - A session score uses the backtest's plan-level actual, the session span on its own clock. That counts agent runs and the gaps between them, each gap up to the idle cap, so it is not wall clock (corrected in the ruling for 0.10).
  - It is verified only when the ingest cache shows the session's first run started at or after the forecast's issue time. It is refused when the cache cannot show that, or when the session's week precedes the issue week.
  - `--actual` is the attested path, and is always labeled so.
  - Rates print only from 5 scored forecasts, and never enter the README.
  - No per-session human wait is reported, because turns cannot join batches by design. Only the user's overall measured wait is shown beside a score, never summed.
- **Per-user calibration: deferred** until a second contributor exists.
  - With one contributor every implementation is identity and untestable on real data.
  - Nesting `contributor` above the shrink hierarchy is rejected. An unseen contributor would stop at the root cell and lose kind, risk and model, and labels would carry the pseudonym.
  - The planned design is a crossed contributor offset on top of the population model. It is the contributor's mean residual, shrunk by the between-contributor variance, with zero offset and added variance for an unseen contributor.
  - It ships only behind the contributor split of the backtest, because with two contributors that variance rests on one degree of freedom.

### Rulings for 0.6.0

Fable 5.1 ruled on 2026-11-07, after a local backtest with the maintainer's Codex rows pooled in.

- **Approval checks are not runs.** This amends the 0.5.0 Codex ruling, which mapped `codex-auto-review` turns to `reviewer` before their shape had been seen. In the maintainer's history they were 528 of 739 Codex rows, at 3 s median and zero tool calls, against 472 s and 21 tool calls for the other turns. Left in, they would pull the shared `review` x `low` cell toward zero and distort the shrinkage of sparse Claude reviewer cells. Adapter version 2 skips them and counts them. No row had shipped, so no published row changes meaning. User-invoked review sessions (`source: review`) stay reviews; none exists locally to compare.
- **Selection keeps its population.** Selection and the headline numbers score `claude-code` sessions only (section 6). All harnesses still train.
  - Why: pooled, the vote goes to whichever harness has the most sessions, and a Codex exec session is one turn. With all 739 of the maintainer's Codex rows, Codex supplied 247 of 321 selection test sessions, and pooled selection chose `global` (loss 785, against 915 for `shrunk`, which was +61 to +204 worse). The fixed-rounds baseline (774) would have beaten that winner. With the approval checks skipped, pooled selection still chose `global` (856 against 1078, +137 to +307), and fixed-rounds (911) did not beat it. Within Claude Code sessions `shrunk` held (1047 to 1050). Within Codex, `global` won only through 2026-W44, a fold with no Codex row in training. In W45, with Codex rows in training, `shrunk` (371) and `weighted` (358) beat `global` (550).
  - This scope was registered after those results were seen. It is not a pre-registration, and CHANGELOG says so.
  - The forward test is registered now, before any Codex row after 2026-W45 exists: the locked holdout scores every candidate per harness, and the Codex method question is answered there only. No per-harness selection, and no exclusion of cold-start folds.
- **Codex rows ship (0.6.0).** The maintainer's 211 Codex turns, from adapter version 2, joined the seed. Every moved number is listed in CHANGELOG 0.6.0. `shrunk` stays shipped, by the tie rule, now that `weighted` has the lowest Claude Code loss (1044 against 1047).

### Ruling for 0.6.2

- **Turn data is never shared.** The 0.3.0 ruling kept turn records out of `share` and the seed; this extends it to every derived form, binned or aggregated, and makes it permanent. The grounds are linkage to public commit times, uniqueness under coarsening, and the rule that the human part is never derived, which a population wait would breach as soon as it was multiplied. Deferral is rejected, because no number of contributors removes the first two. The measured wait is shown only to the person it describes, from their own file. Tests pin it: `share` succeeds with every route to the turns file trapped, `share.py` imports nothing named `turns`, and `validate` rejects a turns file placed among the run rows.

### Rulings for 0.6.3

Fable 5.1 ruled on 2026-11-07.

- **Each project on its own.** The project split's pooled tables hid that one Codex project held 137 of its 212 held-out sessions. Without that project the lowest candidate changes from `weighted` to `shrunk`.
  - The report lists each held-out project by rank, most held-out sessions first. Ties go to more rows, then to the pseudonym. The pseudonym is never printed.
  - One generated sentence gives each candidate's loss without the largest project, and says whether the lowest candidate changes.
  - Everything comes from the forecasts the split already draws. No random number is added, and no published value moves.
  - README numbers about it are defined by rank ("the largest project"), because ranks shift as data is added.
- **One table when the weightings agree.** When every held-out session is one run, the session and run weightings are identical and the report prints one table, saying why. "Where agentpace loses" still lists both weightings, so its count does not change.
- **Roadmap.**
  - A Codex data change for 2026-W46 once that week has ended. It excludes the maintainer's tooling sessions of 2026-11-07 by a filter it names.
  - Then a presentation pass on the README.
  - Per-user calibration stays deferred.

### Ruling for the Gemini CLI adapter (draft)

Fable 5.1 ruled on 2026-11-08.

- **Built from pinned upstream source.** Gemini CLI could not be installed on the maintainer's machine, so the adapter reads the format from `google-gemini/gemini-cli@62bab5b232c15b925d31e655ce844ab939c882b7`, and every format fact cites `file:line` there. Fixtures are synthetic and follow that format.
- **Merges only after real history.** The PR opens as a draft labeled "needs real history". It merges, registered, only when real sessions (completed, interrupted, multi-turn, one with a tool error) are reconciled with its rows by hand. Those scratch rows never enter the seed. Building from source alone is how the Codex adapter first missed the approval-check turns (rulings for 0.6.0).
- **Run definition.** One agent turn, inferred from the first prompt a model answers up to the next one, because the file has no turn events (section 3).

### Ruling for the OpenCode adapter (draft)

Fable ruled on 2026-11-08 that adapters for harnesses that cannot be installed here are built from pinned upstream source and open as draft pull requests labeled "needs real history". The OpenCode adapter follows it: its format comes from `anomalyco/opencode` at `810c0eba1a7348036f8bdf61e6632d0c36c6876c`, the commit tagged for `opencode-ai` 1.19.5, the npm `latest` on that day. It merges registered only after real sessions, completed, interrupted, multi-turn and with a tool error, reconcile it by hand. Its rows never enter the seed before that.

### Cline adapter (draft)

Fable 5.1 ruled on 2026-11-08, for issue #31.

- **Built from the source, merged only on real history.** Cline could not be installed where the adapter was written, so its format comes from `cline/cline` at the pinned commits in section 3, and its fixtures are synthetic. The branch opens as a draft pull request labeled "needs real history", and it merges only once real sessions (completed, interrupted, multi-turn, and one with a tool error) are reconciled by hand against its rows.
- **Which format.** The stable package ships two bundles and picks one per window by a staged remote rollout. The adapter reads the legacy bundle's task folders, which most users still write. The SDK bundle's session store is left for a later adapter version.
- Dotted Claude ids normalize to the dashed family by the shared rule in `ingest.normalize_model`.

### Ruling for 0.8.0 (owner)

The owner ruled on 2026-11-08.

- **Shipped unverified, as experimental.** The owner chose to ship the Gemini CLI, OpenCode and Cline adapters without real-session verification. Real-session reconciliation is replaced by user reports.
- **Labeled everywhere.** They are labeled experimental in the CLI (`agentpace ingest` prints a note on stderr for these sources, from `adapters.EXPERIMENTAL`), in the README and here.
- **Rows rejected until verified.** `validate` rejects rows from these harnesses, so a contributor's file turns CI red and is reconciled by hand before the label is lifted.
- **Supersedes.** Fable's earlier rule that these adapters merge only after real history (the three rulings above) is superseded for these three.

### Ruling for 0.9

Fable 5.1 ruled on 2026-11-08, with an independent opinion from Codex (gpt-6-astra) on the same brief, both at the highest effort. The master checked the defects the opinion reported before the ruling; Fable reproduced the share overwrite and the plan-model errors itself.

- **Order.** Safe repeated sharing first, then truthful CLI output, the contributor path and PyPI preparation (0.8.1 to 0.8.4). The quote beside the range is 0.9.0, `share --drop-session` 0.9.1, and the 2026-W46 data change follows once that week has ended.
- **A second share appends.** `share --write` for a span whose file already exists adds the new rows to that file and leaves its old bytes untouched. A file holding another contributor's rows is refused. Distinct suffixed file names were rejected, because the dataset keeps one file per contributor and span. Rows from experimental adapters stay in a share, with a warning on stderr, because the red data pull request is how they get reconciled.
- **"Last" comes from cached chronology.** `runs` and `label last` order sessions by the start times in the local ingest caches, never by pseudonym and never by a new row field. When a session has no cached start, `runs` says so and `label last` refuses rather than guesses. `label` takes the store lock, so a hook ingest cannot lose its write.
- **One quantity, one name.** The first estimate line reads "Total agent time, on the session clock". "Wall clock" leaves the estimate output, the README and the pace-estimate skill. No number moves.
- **Contributors validate `data/runs`.** The `validate` default stays the bundled seed, and every contributor instruction passes `data/runs`. A default that depends on the working directory was rejected as a second trap.
- **PyPI stays manual.** A runbook, a corrected workflow comment and absolute README image links. No install line changes until a clean install of a real upload passes.
- **A quote is shown, never converted.** `--quoted` no longer needs `--record`. The quote appears verbatim on its own stdout line, marked as calendar time, as quoted and unverified, with no ratio. `--json` output is unchanged.
- **One model across the README's examples.** `examples/quote-plan.json` names `claude-opus-5-5` because `examples/plan.json` and the skill's template already do, and a test keeps them equal. A model chosen after seeing the numbers was rejected as the reason; so was a model-free plan, which would put two README blocks drawing from different cells side by side with nothing to explain why. The cell column shows the model and its n, and the numbers change with the model.
- **Out:** a browser estimator, leaderboard views, method reselection or a drift sentence, `--since` and `--weeks`, `score` matching the last session by itself, a second plan format, `doctor`, shell completion, `llms.txt`, Discussions, new adapters, lifting an experimental label without real sessions, and path-filtered CI that would make data pull requests look green.

### Ruling for 0.10

Fable 5.1 ruled on 2026-11-08 on four questions users asked after the launch posts. It had an independent opinion from Codex (gpt-6-astra) on the same brief, both at the highest effort. The opinion found that the brief, and this document's `offset_s` row, said long gaps were removed from the session clock, when each gap is in fact capped at the idle cap. The master confirmed this in the code before the ruling.

- **The clock was described wrongly.** The session clock counts agent runs and the gaps between them, each gap up to the idle cap. So "waiting between agent turns is not in the total" was false below the cap. The fix is wording only (0.9.2), and no number moved:
  - the first line reads "Plan total on the session clock";
  - a line under it states the cap rule;
  - the human line says no allowance is added.

  `included_in_total` stays false, meaning that no human allowance is added, not that the total holds no waiting. The ruling for 0.9 had kept "Total" because the human line said the opposite; both props fell together.
- **Non-completion is disclosed, not modelled.** Every estimate prints the recorded runs that did not complete, by harness, counted from the rows it loaded and never per cell, and a generated README claim carries the same counts (0.10.0). They are harness outcomes, not task acceptance. They never inflate a quantile, and `unknown` is never treated as failure.
- **A stated review capacity is carried, never used.** `--review-capacity TEXT` shows the user's own figure verbatim on its own line, as stated by them, not measured and not applied (0.11.0). A numeric flag was rejected: nothing may ever be computed from the figure, because a plan task is an agent run, not a pull request.
- **Answered in Limitations, not built.** Retries are an errored-tool-result proxy, and no post-run field predicts a duration. Forecasts pool by kind, risk and model; repository size is not recorded.
- **Declined:** retry bucketing, time to an accepted result, and new segmentation dimensions. The rows hold no attempt linkage, no acceptance event and no repository size.
- **The 2026-W46 data change** takes the next patch number when that week has ended, and waits for none of this.

### Ruling for 0.12

Fable 5.1 ruled on 2026-11-09, with an independent opinion from Codex (gpt-6-astra) on the same brief, both at the highest effort. The master checked every claim in the opinion before the ruling.

- **The finding.** In the rows, `risk` is the agent's role (review and research agents `low`, migration agents `high`, everything else `normal`; the classifier, section 4). A plan's `risk` was a judgment about the work. The lookup stops at the first unseen level, so a task marked `high` matched no runs, dropped the model, and for an Opus feature cut the P50 by a factor of 16, in the shorter direction. No backtest fold ever reaches that branch, because row risk follows the agent type.
- **Warnings say what was not reached (0.11.2).** Which level had no recorded run, which of the task's levels were therefore not used, where the mean and residuals came from, and what is recorded under the parent. No counterfactual is simulated. A synthetic fixture pins the shipped method's numbers, not only its name.
- **The portable prompt's table (0.11.3)** is by kind, recorded risk and model, with pooled rows that mirror the CLI's back-off and no range under 30 runs. Claim enforcement covers the prompt's cells.
- **Plan risk is read as the recorded category (0.12.0), at the plan boundary, not in the method.** A written risk is used only where the rows can record that kind at that risk (`review` low, `migration` high, `other` low for research or normal, all else normal). Any other value, and an omitted one, takes the kind's recorded risk, and a warning names the task. `--use-risk` looks the written values up as given, for labelled datasets. `fit`, `locate`, the backtest's queries and the frozen selection are unchanged, so there is no re-registration.
- **Declined:** reordering or extending the hierarchy, a model fallback at the root or a shared model effect (method changes under the frozen selection), relabelling unseen kinds, and a risk multiplier.
- **Cleanup (0.12.1, 0.12.2):** count messages agree in number, documentation corrections, and clean errors for a bad rows line and a contribution file another program holds open.
- The W46 data change takes the next patch when that week ends, and waits for nothing.

## 12. Sources

Repos, audited at: `kiloloop/agent-estimate@8857979`, `MMMProd-Org/claude-eta@5448ae4` (the `mmmprod/claude-eta` URL redirects there), `ZhangHanDong/agent-estimation@71a4697`, `ecappa/web-dev-estimation@c61fb1c`, `MedAdemBHA/mythical-agent-month@b60165f`.

- Agent Skills specification: <https://agentskills.io/specification>
- Claude Code skills, hooks, monitoring: <https://code.claude.com/docs/en/skills>, <https://code.claude.com/docs/en/hooks>, <https://code.claude.com/docs/en/monitoring-usage>
- `skills` CLI: <https://github.com/vercel-labs/skills>
- OpenTelemetry GenAI conventions: <https://github.com/open-telemetry/semantic-conventions-genai>
- METR: <https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/>, <https://metr.org/blog/2026-02-24-uplift-update/>, <https://metr.org/time-horizons/>
- GitHub Actions security: <https://docs.github.com/en/actions/reference/security/secure-use>
- Statistics, cited from memory and to be checked before they appear in the README: Hyndman and Fan 1996 (sample quantiles), Gneiting 2011 (pinball loss), Tofallis 2015 (log accuracy ratio), Angelopoulos and Bates 2021 (conformal prediction), Malcolm et al. 1959 and MacCrimmon and Ryavec 1964 (PERT merge bias), Sweeney 2002 (k-anonymity).

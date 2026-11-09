# Command reference

Every `agentpace` command, its flags, its input and output formats, its exit codes and the files it keeps. `agentpace <command> --help` prints the parser's own summary of the same flags. For what the numbers mean and how they are tested, see the [README](../README.md) and [DESIGN](DESIGN.md).

```text
agentpace [--version] <command> [options]
```

| Command | What it does |
|---|---|
| [`ingest`](#ingest) | Read local agent history into rows |
| [`runs`](#runs) | List recent local runs and their references |
| [`label`](#label) | Correct the kind or risk of one run |
| [`estimate`](#estimate) | P50/P80/P90 for a plan of tasks |
| [`score`](#score) | Score a recorded forecast, or show how your forecasts held up |
| [`turns`](#turns) | Measure how long you wait between agent turns, kept local |
| [`backtest`](#backtest) | Evaluate on held-out runs and write the published report |
| [`share`](#share) | Preview your rows, or write a file to contribute |
| [`validate`](#validate) | Check data files |
| [`site`](#site) | Refresh the data inside the static page |
| [`claims`](#claims) | Tie numbers in documents to the data |

## Output and exit codes

**stdout carries the answer, stderr carries everything else.** Results, listings and the rows `share --dry-run` previews go to stdout, so they can be piped or redirected. Progress reports, notes, warnings about the run itself and error messages go to stderr. Closing the pipe early, as `agentpace share | head` does, stops the command quietly.

| Exit code | Meaning |
|---|---|
| 0 | Success. `ingest --hook` always exits 0, whatever happened. |
| 1 | A check found a problem: `validate` found invalid rows, or `backtest --check`, `site --check` or `claims --check` found published files out of date. |
| 2 | The command refused its input. Either the arguments are wrong (an unknown command, flag or choice; argparse prints the usage), or the input is (a missing path, a plan path that is not a file, an invalid plan, an unknown run reference, or a store whose idle cap does not allow the command). The message is one line on stderr, starting `agentpace:`. |

Any other failure ends with a Python traceback and exit code 1. None is known; please report one in an [issue](https://github.com/mohamedzhioua/agentpace/issues).

## Files: `AGENTPACE_HOME`

agentpace keeps its local files in `~/.agentpace`, or in the folder the `AGENTPACE_HOME` environment variable names. Nothing in it is sent anywhere. Only `share --write` writes rows meant for publication, and it writes them outside this folder.

| File | Written by | Holds |
|---|---|---|
| `secret` | the first command that needs it | A random key, created on first use (owner-only permissions where the system supports them). The `contributor`, `batch` and `project` pseudonyms are keyed by it, so a new secret gives new rows new pseudonyms. Never share it. |
| `runs.jsonl` | `ingest`, `label` | Your rows, one JSON object per line, in the [run schema](../schema/run.schema.json). `estimate` pools them with the seed by default. |
| `runs.jsonl.clock.json` | `ingest` | The idle cap the store was filled with, written before its first rows. Local only, never shared. See [The idle cap](#the-idle-cap). |
| `runs.jsonl.lock` | `ingest`, `label` | Present only while a command is writing the store, so a hook and a manual run never interleave. One older than 15 minutes is taken to be left by a crash. |
| `labels.json` | `label` | Your kind and risk corrections, by run reference. `ingest` re-applies them to fresh rows. |
| `cache-<harness>.json` | `ingest` at the default idle cap | One per harness, such as `cache-claude-code.json`. Parsed runs by transcript path, so a second ingest reads only what changed. It holds transcript paths, so project folder names sit in it raw, as they do in the transcripts. Local only, never shared. |
| `turns.jsonl` | `turns` | Your waits between agent turns. Local only, never shared. |
| `forecasts.jsonl` | `estimate --record`, `score` | The forecast ledger: recorded forecasts and their scores. Local only, never shared. |

`ingest --out` and `turns --out` move their own output file elsewhere. The secret, labels and caches stay in the home folder.

## ingest

```text
agentpace ingest [path] [--source NAME] [--out FILE] [--idle-cap SECONDS] [--kind-map NAME=KIND]... [--hook]
```

Reads a harness's local history and merges the rows into the store. Only timestamps, model ids, token and tool counts and a few flags are copied out of it, never content; DESIGN section 3 lists what each adapter copies.

| Option | Default | Meaning |
|---|---|---|
| `path` | the harness's own history folder | Where to read from. It must exist. |
| `--source` | `claude-code` | One of `claude-code`, `cline`, `codex`, `gemini-cli`, `opencode`. `gemini-cli`, `opencode` and `cline` are experimental: built from each tool's source, not yet verified on real sessions, and `validate` rejects their rows. See [verify-adapter.md](verify-adapter.md). |
| `--out` | `~/.agentpace/runs.jsonl` | The rows file to merge into. |
| `--idle-cap` | `900` | Seconds an idle gap may contribute, inside a run's duration and between runs on the session clock. A non-default cap neither reads nor writes the cache. A rows file keeps one cap; see [The idle cap](#the-idle-cap). |
| `--kind-map NAME=KIND` | none | Map one of your agent types to a task kind. Repeatable. |
| `--hook` | off | Read a Claude Code `SubagentStop` payload on stdin and ingest that session quietly. Prints nothing and always exits 0. Gives up after 5 s if the store is busy. Because it cannot report, run `agentpace ingest` by hand when runs stop appearing: it prints what the hook could not, such as a store line that is not valid JSON. |

stdout: nothing. stderr: a note when the source is experimental, then, when the idle cap is not the default, a warning:

```text
warning: idle cap is 600 s, not the default 900 s. The published dataset uses 900 s, so these rows cannot be shared, and an estimate that pools them with the bundled seed mixes two clocks.
```

then the report (files seen, read from cache, rows produced, empty runs, unreadable files, unparsed lines, zero-duration, still running, and skipped files or runs when there are any), then `store now holds:  N rows`. Last, on an ingest by hand into a store whose idle cap was never recorded:

```text
note: runs.jsonl has no recorded idle cap: it was filled before agentpace kept one. share refuses such a store. To record the cap, rebuild it: https://github.com/mohamedzhioua/agentpace/blob/main/docs/cli.md#the-idle-cap
```

### The idle cap

`--idle-cap` sets how many seconds an idle gap may contribute, inside a run and between runs on the session clock. It changes a run's `duration_s` and `offset_s`, so the same run ingested under two caps would be stored twice. A rows file therefore keeps one cap.

- The first ingest into a new or empty rows file writes `<file>.clock.json` beside it, holding `{"version": 1, "idle_cap_s": N}`. For the default store that is `runs.jsonl.clock.json`. Keep `--out` files outside `data/runs`: `validate` rejects any file there that is not `.jsonl`.
- A later ingest with another `--idle-cap` is refused with exit 2 and ingests nothing. For another cap, pass `--out` with a new file.
- With a cap other than the default, `ingest` warns: the published dataset uses 900 s, so those rows cannot be shared.
- `share` runs only when the default store's cap is known to be 900 s.
- `estimate` prints an `Idle cap:` line under the total whenever your own rows or `--data` take part.

**A store from before 0.13.0** has rows and no record. If every row in it is a published row, it counts as 900 s and the next ingest writes the record. Otherwise its cap is not recorded: `ingest` still adds to it at the default cap and says so, `estimate` still uses it and says so, and `share` refuses it. To record the cap, rebuild the store:

1. Rename `runs.jsonl` in your agentpace folder, for example to `runs-before-0.13.jsonl`. agentpace no longer reads it, and nothing is lost.
2. Run `agentpace ingest`, once for each harness you log (`--source codex`, and so on). It rebuilds `runs.jsonl` at the default cap from the history your harness still has, and writes the record.

Runs whose transcripts the harness has deleted are not rebuilt; they stay in the renamed file, which `estimate --data` can still read. Labels are re-applied by run reference, and a label made on a row from another cap may no longer match its run, so check `agentpace runs` afterwards. Do the same to repair a store that was filled under two caps. The record cannot see a write by an agentpace older than 0.13.0, which ignores it.

## runs

```text
agentpace runs [--last N]
```

Lists local runs in order, ending with the most recent. `--last` sets how many (default `20`).

stdout: one line per run: its reference (`<batch>:<offset_s>`), week, model, kind, risk, size, duration in seconds and outcome. The reference is what `label` and `score --session` take.

**Chronology.** A row carries its ISO week but no clock time, so the order within a week comes from your local ingest caches, never from the row. Runs are sorted by week, then by their session's earliest cached start. A session with no cached start sorts first within its week, so `--last` prefers sessions whose time is known, and one line on stderr says how many of the listed runs' sessions have no local chronology. Sessions have no cached start when their rows came from an `ingest --idle-cap` run (a non-default cap never feeds the cache), when the cache was rebuilt after the transcripts were deleted, or when the secret was replaced. `agentpace ingest` refreshes the cache for transcripts that still exist.

## label

```text
agentpace label REF [--kind KIND] [--risk RISK]
```

Corrects one run's kind or risk, in `labels.json` and in the store. Give `--kind`, `--risk` or both.

| Option | Meaning |
|---|---|
| `REF` | A reference from `agentpace runs`, or `last`. |
| `--kind` | One of `feature`, `bugfix`, `refactor`, `migration`, `tests`, `docs`, `review`, `other`. |
| `--risk` | One of `low`, `normal`, `high`. |

`last` is the latest run of the session with the latest cached start. It refuses, with exit 2, rather than guess: when no session has a cached start, or when a session without one has rows in that session's latest week or later. The message counts the sessions without a cached start that could be the later one; when no session has one, that is all of them. Then run `agentpace ingest`, or give a reference from `agentpace runs`. `label` holds the store's lock while it writes, and says so if another ingest is writing.

stdout: `labeled <ref>: <kind>, <risk> risk`.

## estimate

```text
agentpace estimate (PLAN | - | --example) [--data PATH]... [--method NAME] [--use-size] [--use-risk] [--draws N] [--seed N] [--json] [--turns FILE] [--record] [--quoted TEXT] [--review-capacity TEXT]
```

Simulates a plan of tasks and prints P50, P80 and P90 per task (agent run time) and for the plan (its span on the session clock).

| Option | Default | Meaning |
|---|---|---|
| `PLAN` | none | A plan file in the format below, or `-` to read it from stdin. |
| `--example` | off | Estimate the example plan that ships with agentpace. Not together with a plan. |
| `--data PATH` | the seed data plus `~/.agentpace/runs.jsonl` if it exists | A rows file or folder. Repeatable. When given, only these rows are used. |
| `--method` | `shrunk` | One of `global`, `kind`, `backoff`, `shrunk`, `weighted`. The default is the backtest's pre-registered winner. |
| `--use-size` | off | Use `size`. An oracle track, because stored size is measured after a run. |
| `--use-risk` | off | Look each task's `risk` up as written, as a recorded category (review and research agents `low`, migration agents `high`, all else `normal`), even where no recorded run has that kind at that risk. By default such a task takes its kind's recorded risk; see `risk` under Plan JSON. |
| `--draws` | `20000` | Simulations. At least 100. |
| `--seed` | `20261107` | Random seed. The same plan, data and seed give the same result. |
| `--json` | off | Print the result as JSON instead of text. |
| `--turns FILE` | `~/.agentpace/turns.jsonl` | Waits measured by `agentpace turns`, for the human part line. The file must exist when given. |
| `--record` | off | Keep this forecast in `~/.agentpace/forecasts.jsonl`, to score later. |
| `--quoted TEXT` | none | A timeline someone already gave, such as `"3 weeks"`. Shown beside the range as quoted, and stored with the forecast when `--record` is given. |
| `--review-capacity TEXT` | none | Your own review capacity, such as `"4 PRs a day"`. Shown verbatim on its own line under the human part line, as stated by you: not measured and never applied to the range. Nothing is parsed or computed from it. It must be one line of at most 80 characters once stripped; otherwise the command exits 2. Stored with the forecast when `--record` is given. |

stdout: the result, as text or JSON. stderr, with `--record`: `recorded forecast <id> in <path>`, and with `--quoted`, whether the quote was parsed as calendar time or stored as text.

**Experimental rows.** Without `--data`, the estimate pools the seed data with your local store, so rows you ingested from an experimental adapter (`gemini-cli`, `opencode`, `cline`) take part in it. When any do, one line on stderr says so: `note: N of M rows come from experimental adapters (<names>); they take part in this estimate because they are in your local store. Pass --data to choose the rows.` stdout, the JSON and any recorded forecast are the same with or without it. The seed data holds no experimental rows.

The text form starts with the total, the clock note and the human part:

```text
Plan total on the session clock: P50 …, P80 …, P90 …
The session clock counts agent runs and the gaps between them, each gap up to the idle cap (900 s by default), so waiting on you between runs sits inside the total up to that cap.
Human part (review, testing, decisions, waiting between agent turns): not measured. No allowance for it is added to the total above. State it separately; do not derive it from the agent time.
```

The clock note gives the default cap. When rows other than the bundled seed take part, your local store or `--data`, one more line sits above it, under the total and under the `Quoted:` line when there is one. It names the idle cap each source was clipped at, from the record `ingest` keeps beside a rows file.

```text
Idle cap: 900 s (bundled seed, your local store).
Idle cap: 900 s (bundled seed), 600 s (your local store). This range pools rows on different clocks.
Idle cap: 900 s (bundled seed), not recorded (your local store). The rows with no recorded cap may be on another clock.
Idle cap: 600 s (--data rows).
Idle cap: not recorded (--data rows).
```

A rows file with no record counts as 900 s when every row in it is a published row, and as not recorded otherwise. For one clock, pass `--data` with rows ingested under it. With only the bundled seed, whose rows are at 900 s, the line is absent.

With `--quoted`, one line sits directly under the total. It holds the quote verbatim and is never converted into agent time or compared with the range:

```text
Quoted: "3 weeks" (calendar time, as quoted; unverified, not converted)
```

Then a table of tasks with their P50, P80, P90, how often each was on the critical path and the data cell it drew from, then the critical path, the effective parallelism, the method and the number of runs used, the runs that did not complete by harness, and any warnings. Durations print to two significant figures: seconds under 90 s, minutes under 90 min, hours under 48 h, days beyond. The line after the method counts every loaded run, never per cell: `Not completed, by harness: <harness> N of M (S%; F failed, U unknown), ...`, then says these are recorded harness outcomes, not task acceptance, left out of the fit, and, when there are any, how many completed rows with no duration are left out too.

### Plan JSON

A plan is a JSON object. Keys not listed here are ignored.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `tasks` | list of task objects | required | At least one task. |
| `agent_cap` | integer, 1 or more | `1` | How many agents run at once. |
| `model` | string or `null` | `null` | The model for every task that does not name its own. `null` means no model. |

Each task:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `id` | string or integer | required | Unique within the plan. An integer is read as its string form. |
| `kind` | string | required | One of `feature`, `bugfix`, `refactor`, `migration`, `tests`, `docs`, `review`, `other`. |
| `risk` | string or `null` | the kind's recorded risk: `low` for `review`, `high` for `migration`, else `normal` | One of `low`, `normal`, `high`. In the rows, risk is the agent's role, not a rating of the work. A written value is used when the rows can record that kind at that risk (`review` low, `migration` high, `other` low for research or normal, all else normal); any other value takes the kind's recorded risk and a warning names the task; `--use-risk` looks every written value up as given. |
| `size` | string or `null` | `null` | One of `S`, `M`, `L`, `XL`, `na`. Ignored, with a warning, unless `--use-size` is given. |
| `model` | string or `null` | the plan's `model` | Overrides the plan's model for this task. An explicit `null` means no model for this task; it does not inherit the plan's `model`. |
| `depends_on` | list of task ids | `[]` | Tasks that must finish before this one starts. Each must be in the plan, and the graph must have no cycle. |

The plan holds no durations: every number comes from the data. A task with a model draws from the model × kind × risk cell, and one without from kind × risk. A model name the data has never seen falls back to the broader cell with a wider interval and a warning, so use the model family as it appears in `agentpace runs`, such as `claude-opus-5-5`. A cell with no recorded run gets a warning that names the level that had none (kind, risk or model), which of the task's levels were therefore not used, where the mean and the residuals came from, and what is recorded under the broader cell. A cell with too few runs to stand alone gets one that says its mean was shrunk toward broader cells and names the cell its residuals were pooled from. A written risk the rows cannot record at that kind gets one that names each such task with its risk as written and the risk used.

A plan that breaks these rules is refused with exit 2. A field problem is reported by the task's position and the rule (`task #2: ...`), never by the plan's text. A dependency on an id that is not in the plan is reported with both ids; a duplicate id or a cycle is reported as such. `model` must be a string or `null`, at plan and task level.

```json
{
  "agent_cap": 2,
  "model": "claude-opus-5-5",
  "tasks": [
    { "id": "schema", "kind": "feature" },
    { "id": "research", "kind": "other", "risk": "low" },
    { "id": "api", "kind": "feature", "depends_on": ["schema"] },
    { "id": "docs", "kind": "docs", "model": null, "depends_on": ["api"] }
  ]
}
```

### `--json` output

One JSON object. Every duration is in seconds, as a number.

| Key | Type | Meaning |
|---|---|---|
| `p50`, `p80`, `p90` | number | The plan's total on the session clock: agent runs plus the gaps between them, each gap up to the idle cap. |
| `tasks` | list | One object per task, in plan order: `id` (string), `p50`, `p80`, `p90` (seconds), `cell` (the data cell it drew from, such as `feature x normal x claude-opus-5-5`, or `all`), `n` (runs in that cell, 0 when the cell was never seen), `n_eff` (the cell's effective size), `criticality` (share of simulations in which the task was on the longest chain, 0 to 1). |
| `critical_path` | list of strings | Task ids of the chain that was longest most often. |
| `critical_path_share` | number, 0 to 1 | Share of simulations in which that chain was the longest. |
| `agent_cap` | integer | From the plan. |
| `effective_parallelism` | number or `null` | The median of total work over elapsed span in past multi-run sessions, grouped by peak concurrency and taken from the nearest group at or below the cap, never above the cap. `null` when there are no multi-run sessions to learn from; one at a time is then assumed. |
| `parallelism_sessions` | integer | Sessions `effective_parallelism` was learned from. 0 when it is `null`. |
| `throughput_bound_share` | number, 0 to 1 | Share of simulations in which total work over parallelism, rather than the critical path, set the total. |
| `method` | string | The method used. |
| `size_used` | boolean | Whether `--use-size` was given. |
| `rows_used` | integer | Completed runs the estimate learned from. |
| `rows_excluded` | integer | Runs left out of the fit: those that did not complete (failed or unknown), and completed runs with no duration. |
| `outcomes` | object | Recorded harness outcomes of every row the estimate loaded, after duplicates are dropped, and never per cell: `rows`, `completed`, `failed`, `unknown`, `completed_without_duration` (completed rows with no positive duration), and `by_harness`, a list sorted by harness name of objects with `harness`, `rows`, `completed`, `failed` and `unknown`. An outcome is how the run ended in its harness, not task acceptance, and `unknown` is not counted as a failure. The counts are shown, never used to change the range. `rows` is `completed` + `failed` + `unknown`, `rows_used` is `completed` - `completed_without_duration`, and `rows_excluded` is `failed` + `unknown` + `completed_without_duration`. |
| `draws` | integer | Simulations run. |
| `seed` | integer | The random seed. |
| `warnings` | list of strings | Thin or unseen cells, ignored sizes, a written risk the estimate did not use, an assumed parallelism. Pass them on. |
| `human` | object | The human part. `status` is `not measured` or `measured`, and `included_in_total` is always `false`: no allowance for the human part is added to the total (waits shorter than the idle cap sit inside the session clock already). When measured, it also holds `source`, `per_turn_p50`, `per_turn_p80`, `per_turn_p90` (seconds per human turn), `gaps` and `sessions`. With `--review-capacity`, it holds `review_capacity_stated`, your text as given, stripped. Keys without a value are left out. |
| `idle_cap` | object | Present only when rows other than the bundled seed take part, under the same rule as the `Idle cap:` line, such as `{"seconds": 900, "sources": [{"source": "bundled seed", "seconds": 900}, {"source": "your local store", "seconds": 900}]}`. `seconds` is the one idle cap every source shares, else `null`. `sources` has one entry per source label and cap, in the line's order: `source` is `bundled seed`, `your local store` or `--data rows`, and its `seconds` is `null` when the cap is not recorded. |

The JSON output does not change with `--record` or `--quoted`.

## score

```text
agentpace score [ID] [--session REF | --actual DURATION]
```

Without an id, prints how your recorded forecasts held up: how many are scored, how often the actual fell within each quantile, and, for quoted timelines, how often the quote was closer than the P50. With an id from `estimate --record`, scores that forecast and records the score in the ledger. Give exactly one of:

| Option | Meaning |
|---|---|
| `--session REF` | A session, or any run reference from `runs` (its session is used). The actual is the span of that session's completed local runs on its own clock. It is refused when the session has fewer than 2 completed runs, ran before the forecast was issued, or has no cached start to prove it began after the forecast. |
| `--actual DURATION` | Your own figure, attested by you, such as `"2h"` or `"3 days"`: a number and one of `min`, `h`, `d`, `w`, `mo`, or their long forms. 1 d = 86,400 s, 1 w = 7 d, 1 mo = 30 d. |

stdout: the forecast, the actual and whether it fell within each quantile, plus the quote's ratio to the actual when one was recorded. stderr: a count of damaged ledger lines, if any were skipped. With `--session`, a note when your store's idle cap was never recorded:

```text
note: runs.jsonl has no recorded idle cap, so this session's span may not be on the forecast's clock.
```

or when it was recorded at a cap other than the default:

```text
note: runs.jsonl was filled with a 600 s idle cap, so this session's span is on that clock, which may not be the forecast's.
```

## turns

```text
agentpace turns [path] [--out FILE]
```

Measures how long you wait between agent turns, from the timestamps of your Claude Code main sessions. Kept local, never shared, and never added to an estimate's total.

| Option | Default | Meaning |
|---|---|---|
| `path` | Claude Code's projects folder | Where to read from. It must exist. |
| `--out` | `~/.agentpace/turns.jsonl` | The turns file. |

stdout: the number of gaps and sessions, the wait per human turn at P50, P80 and P90, and the median share of a session spent waiting on you. stderr: the read report.

## backtest

```text
agentpace backtest [--data PATH]... [--out FOLDER] [--seed N] [--check]
```

Runs the published evaluation on held-out runs. Run it from the repository root.

| Option | Default | Meaning |
|---|---|---|
| `--data PATH` | the seed data | A rows file or folder. Repeatable. Your local store is not used unless you pass it. |
| `--out` | `docs` | Where `backtest.md`, `backtest.json` and the two charts under `assets/` go. |
| `--seed` | `20261107` | Random seed. |
| `--check` | off | Write nothing; exit 1 if the results differ from the published `backtest.json`, or `backtest.md` is missing or differs from what they render. |

stdout: what was written and the winning method, or that the numbers match. stderr: the mismatch under `--check`, and a note when the winner is not the shipped default.

## share

```text
agentpace share [--dry-run | --write] [--out-dir FOLDER] [--since YYYY-Www] [--drop-project]
                [--drop-session REF]...
```

Prepares your local rows for a data pull request. It never sends anything.

| Option | Default | Meaning |
|---|---|---|
| `--dry-run` | on | Print the exact rows `--write` would write, and write nothing. |
| `--write` | off | Write them. |
| `--out-dir` | `data/runs` | The dataset folder. Rows already present anywhere in it are left out. |
| `--since YYYY-Www` | none | Only rows from this ISO week onward. |
| `--drop-project` | off | Leave out the `project` field. |
| `--drop-session REF` | none | Leave out a whole session, in the dry run and in the file. A session, or any run reference from `runs` (its session is used). Repeatable. Its rows are left out before anything else, so they count neither as published nor in the file name's weeks. A `REF` whose session has no local row is refused with exit 2, and nothing is written. |

`share` runs only on a store whose idle cap is known to be 900 s (see [The idle cap](#the-idle-cap)). In both modes, before it reads `--out-dir` or applies any option, it refuses with exit 2, prints nothing on stdout and writes nothing when the store was filled with another cap:

```text
agentpace: runs.jsonl was filled with a 600 s idle cap, and the published dataset uses 900 s, so its rows cannot be shared. Nothing was written. To contribute, rename it and run agentpace ingest at the default cap
```

or when its cap was never recorded:

```text
agentpace: runs.jsonl has no recorded idle cap, so share cannot tell whether its rows are on the published 900 s clock. Nothing was written. Rebuild the store first: https://github.com/mohamedzhioua/agentpace/blob/main/docs/cli.md#the-idle-cap
```

`--write` adds or extends one file, `<contributor>-<first week>-<last week>.jsonl`, in `--out-dir`. If that file exists, the new rows are appended and its existing bytes are left as they are. A file holding another contributor's rows is refused with exit 2. The dry run says which of the two `--write` would do.

stdout, in a dry run: the rows, one canonical JSON object per line. stderr: what the rows reveal, how many rows the dropped sessions held (`N rows from M dropped sessions left out.`), how many were already published, a warning when any come from an experimental adapter (`validate` rejects those, so a pull request with them stays red until they are reconciled), and after `--write`, what was written and the commands to submit it, starting with `agentpace validate`. See [CONTRIBUTING.md](../CONTRIBUTING.md#contribute-your-runs).

## validate

```text
agentpace validate [PATH]...
```

Checks data files: the schema, duplicates across files, implausible values, unknown model names, experimental harnesses and anything shaped like personal data. Findings name a file, a line and a rule, never the offending value.

| Option | Default | Meaning |
|---|---|---|
| `PATH` | the seed data shipped with agentpace | Files or folders. Contributors pass `data/runs`: `agentpace validate data/runs`. |

stdout: one `file:line: rule` line per finding, or `all rows valid`. stderr: `N problems`. Exit 1 when there is any finding.

## site

```text
agentpace site [--data PATH]... [--out FILE] [--check]
```

Refreshes the data and the sentences inside the static page from the rows and `backtest.json` beside it. Run `backtest` first.

| Option | Default | Meaning |
|---|---|---|
| `--data PATH` | the seed data | A rows file or folder. Repeatable. |
| `--out` | `docs/index.html` | The page. |
| `--check` | off | Write nothing; exit 1 if the page is out of date. |

## claims

```text
agentpace claims [FILE]... [--data PATH]... [--report FILE] [--check]
```

Rewrites every number between claim markers (`<!-- claim:name -->...<!-- /claim -->`) from the data and the backtest report. Run it from the repository root, because it estimates `examples/plan.json` for the README's example output and `examples/quote-plan.json`, with `--quoted "3 months"`, for its quoted-timeline example.

| Option | Default | Meaning |
|---|---|---|
| `FILE` | `README.md` and `skills/agentpace/assets/portable-prompt.md` | Documents to update. |
| `--data PATH` | the seed data | A rows file or folder. Repeatable. |
| `--report` | `docs/backtest.json` | The backtest report the numbers come from. |
| `--check` | off | Write nothing; exit 1 if any number is stale. |

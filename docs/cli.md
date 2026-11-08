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
| 2 | The command refused its input. Either the arguments are wrong (an unknown command, flag or choice; argparse prints the usage), or the input is (a missing path, a plan path that is not a file, an invalid plan, an unknown run reference). The message is one line on stderr, starting `agentpace:`. |

Any other failure ends with a Python traceback and exit code 1. None is known; please report one in an [issue](https://github.com/mohamedzhioua/agentpace/issues).

## Files: `AGENTPACE_HOME`

agentpace keeps its local files in `~/.agentpace`, or in the folder the `AGENTPACE_HOME` environment variable names. Nothing in it is sent anywhere. Only `share --write` writes rows meant for publication, and it writes them outside this folder.

| File | Written by | Holds |
|---|---|---|
| `secret` | the first command that needs it | A random key, created on first use (owner-only permissions where the system supports them). The `contributor`, `batch` and `project` pseudonyms are keyed by it, so a new secret gives new rows new pseudonyms. Never share it. |
| `runs.jsonl` | `ingest`, `label` | Your rows, one JSON object per line, in the [run schema](../schema/run.schema.json). `estimate` pools them with the seed by default. |
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
| `--idle-cap` | `900` | Seconds an idle gap may contribute to a run's duration. A non-default cap neither reads nor writes the cache. |
| `--kind-map NAME=KIND` | none | Map one of your agent types to a task kind. Repeatable. |
| `--hook` | off | Read a Claude Code `SubagentStop` payload on stdin and ingest that session quietly. Prints nothing and always exits 0. Gives up after 5 s if the store is busy. |

stdout: nothing. stderr: a note when the source is experimental, then the report (files seen, read from cache, rows produced, empty runs, unreadable files, unparsed lines, zero-duration, still running, and skipped files or runs when there are any), then `store now holds: N rows`.

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
agentpace estimate (PLAN | - | --example) [--data PATH]... [--method NAME] [--use-size] [--draws N] [--seed N] [--json] [--turns FILE] [--record] [--quoted TEXT]
```

Simulates a plan of tasks and prints P50, P80 and P90 of the total agent time, per task and for the plan.

| Option | Default | Meaning |
|---|---|---|
| `PLAN` | none | A plan file in the format below, or `-` to read it from stdin. |
| `--example` | off | Estimate the example plan that ships with agentpace. Not together with a plan. |
| `--data PATH` | the seed data plus `~/.agentpace/runs.jsonl` if it exists | A rows file or folder. Repeatable. When given, only these rows are used. |
| `--method` | `shrunk` | One of `global`, `kind`, `backoff`, `shrunk`, `weighted`. The default is the backtest's pre-registered winner. |
| `--use-size` | off | Use `size`. An oracle track, because stored size is measured after a run. |
| `--draws` | `20000` | Simulations. At least 100. |
| `--seed` | `20261107` | Random seed. The same plan, data and seed give the same result. |
| `--json` | off | Print the result as JSON instead of text. |
| `--turns FILE` | `~/.agentpace/turns.jsonl` | Waits measured by `agentpace turns`, for the human part line. The file must exist when given. |
| `--record` | off | Keep this forecast in `~/.agentpace/forecasts.jsonl`, to score later. |
| `--quoted TEXT` | none | A timeline someone already gave, such as `"3 weeks"`, stored with the recorded forecast. It needs `--record`. |

stdout: the result, as text or JSON. stderr, with `--record`: `recorded forecast <id> in <path>`, and with `--quoted`, whether the quote was parsed as calendar time or stored as text.

The text form starts with the total and the human part:

```text
Total agent time, on the session clock: P50 …, P80 …, P90 …
Human part (review, testing, decisions, waiting between agent turns): not measured. Not included in the total above. State it separately; do not derive it from the agent time.
```

Then a table of tasks with their P50, P80, P90, how often each was on the critical path and the data cell it drew from, then the critical path, the effective parallelism, the method and the number of runs used, and any warnings. Durations print to two significant figures: seconds under 90 s, minutes under 90 min, hours under 48 h, days beyond.

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
| `risk` | string | `normal` | One of `low`, `normal`, `high`. |
| `size` | string or `null` | `null` | One of `S`, `M`, `L`, `XL`, `na`. Ignored, with a warning, unless `--use-size` is given. |
| `model` | string or `null` | the plan's `model` | Overrides the plan's model for this task. An explicit `null` means no model for this task; it does not inherit the plan's `model`. |
| `depends_on` | list of task ids | `[]` | Tasks that must finish before this one starts. Each must be in the plan, and the graph must have no cycle. |

The plan holds no durations: every number comes from the data. A task with a model draws from the model × kind × risk cell, and one without from kind × risk. A model name the data has never seen falls back to the broader cell with a wider interval and a warning, so use the model family as it appears in `agentpace runs`, such as `claude-opus-5-5`.

A plan that breaks these rules is refused with exit 2 and a message that names the task by its position (`task #2: ...`) and the rule, never the plan's text. `model` must be a string or `null`, at plan and task level.

```json
{
  "agent_cap": 2,
  "model": "claude-opus-5-5",
  "tasks": [
    { "id": "schema", "kind": "feature" },
    { "id": "api", "kind": "feature", "risk": "high", "depends_on": ["schema"] },
    { "id": "docs", "kind": "docs", "model": null, "depends_on": ["api"] }
  ]
}
```

### `--json` output

One JSON object. Every duration is in seconds, as a number.

| Key | Type | Meaning |
|---|---|---|
| `p50`, `p80`, `p90` | number | The plan's total agent time, on the session clock. |
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
| `rows_excluded` | integer | Runs left out because they did not complete. |
| `draws` | integer | Simulations run. |
| `seed` | integer | The random seed. |
| `warnings` | list of strings | Thin or unseen cells, ignored sizes, an assumed parallelism. Pass them on. |
| `human` | object | The human part. `status` is `not measured` or `measured`, and `included_in_total` is always `false`. When measured, it also holds `source`, `per_turn_p50`, `per_turn_p80`, `per_turn_p90` (seconds per human turn), `gaps` and `sessions`. Keys without a value are left out. |

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

stdout: the forecast, the actual and whether it fell within each quantile, plus the quote's ratio to the actual when one was recorded. stderr: a count of damaged ledger lines, if any were skipped.

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
| `--check` | off | Write nothing; exit 1 if the results differ from the published `backtest.json`. |

stdout: what was written and the winning method, or that the numbers match. stderr: the mismatch under `--check`, and a note when the winner is not the shipped default.

## share

```text
agentpace share [--dry-run | --write] [--out-dir FOLDER] [--since YYYY-Www] [--drop-project]
```

Prepares your local rows for a data pull request. It never sends anything.

| Option | Default | Meaning |
|---|---|---|
| `--dry-run` | on | Print the exact rows `--write` would write, and write nothing. |
| `--write` | off | Write them. |
| `--out-dir` | `data/runs` | The dataset folder. Rows already present anywhere in it are left out. |
| `--since YYYY-Www` | none | Only rows from this ISO week onward. |
| `--drop-project` | off | Leave out the `project` field. |

`--write` adds or extends one file, `<contributor>-<first week>-<last week>.jsonl`, in `--out-dir`. If that file exists, the new rows are appended and its existing bytes are left as they are. A file holding another contributor's rows is refused with exit 2. The dry run says which of the two `--write` would do.

stdout, in a dry run: the rows, one canonical JSON object per line. stderr: what the rows reveal, how many were already published, a warning when any come from an experimental adapter (`validate` rejects those, so a pull request with them stays red until they are reconciled), and after `--write`, what was written and the commands to submit it, starting with `agentpace validate`. See [CONTRIBUTING.md](../CONTRIBUTING.md#contribute-your-runs).

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

Rewrites every number between claim markers (`<!-- claim:name -->...<!-- /claim -->`) from the data and the backtest report. Run it from the repository root, because it estimates `examples/plan.json` for the README's example output.

| Option | Default | Meaning |
|---|---|---|
| `FILE` | `README.md` and `skills/agentpace/assets/portable-prompt.md` | Documents to update. |
| `--data PATH` | the seed data | A rows file or folder. Repeatable. |
| `--report` | `docs/backtest.json` | The backtest report the numbers come from. |
| `--check` | off | Write nothing; exit 1 if any number is stale. |

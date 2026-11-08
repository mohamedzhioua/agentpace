# The dataset

`data/runs/*.jsonl` holds one JSON object per line, one line per coding-agent run. The rows are
dedicated to the public domain under [CC0-1.0](LICENSE).

`schema/run.schema.json` defines every field. Rows hold no prompts, code, file paths, repo names or
commit messages. `agentpace validate` checks all of this, and CI runs it on every data pull request.

## What is in the seed file

Every row came from real agent runs by one person, read with `agentpace ingest`: Claude Code subagent runs, and since 0.6.0 Codex turns (`agentpace ingest --source codex`, with Codex's automatic approval checks skipped).
Nothing is synthetic. Check the current counts with `agentpace backtest`, which reports them at the
top of `docs/backtest.md`.

## How to read it

**This is one person's workflow.** It includes heavy automated fan-out. One project and a few
sessions dominate, so these rows are not a sample of how agents behave in general.

**Do not read `model` as a speed ranking.** People send harder work to stronger models. Opus rows
are slower than Sonnet rows here because of what they were given, not because of what they are.

**`feature` means implementation work of any kind.** Without reading prompts, metadata cannot
separate a feature from a bug fix or a refactor. Rows labeled `bugfix` or `refactor` came from an
agent type or from a person using `agentpace label`.

**`risk` comes from the agent type**, unless someone labeled the run: read-only agents are `low`,
migrations are `high`, and everything else is `normal`.

**`effort` is optional.** It is the reasoning effort Claude Code recorded for the run, when it
recorded one. Older rows do not carry it. It is not used to forecast.

**`size` is measured after a run**, from the count of edit-style tool calls. It leaks the outcome,
so forecasts ignore it unless you pass `--use-size`.

**`retries` is a proxy.** It counts errored tool results, since a true retry cannot be seen without
reading content.

**`duration_s` is not wall time on a clock.** Idle gaps over 900 seconds count as 900 seconds, which
removes sleep and resumed sessions. The session clock behind `offset_s` is built the same way: each
gap between runs counts up to 900 seconds.

**Failed runs are under-counted.** Only runs with a clear completion signal train the estimator, so
P90 values may run low.

## Identity

- `contributor` is a keyed hash of a fixed label under a random secret on the contributor's machine.
  No email, handle or hostname goes in, and it stands for one person in every file.
- `project` is a keyed hash of a local folder name. It is optional, and `share --drop-project` omits it.
- `batch` is a keyed hash of a session id.

The secret never leaves the machine, so none of these can be reversed by guessing names. They still
link rows to each other, and a pull request ties a `contributor` to a GitHub account in public
history. `agentpace share` says this before writing anything.

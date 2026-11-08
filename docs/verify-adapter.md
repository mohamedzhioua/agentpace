# Verify an experimental adapter

The Gemini CLI, OpenCode and Cline adapters (`--source gemini-cli`, `opencode` and `cline`) were built from each tool's source and shipped unverified, by the owner's decision for 0.8.0. Until one of them is checked against real sessions, `agentpace ingest` marks it experimental and `agentpace validate` rejects its rows, so none of them reach the published data.

This page is how anyone who uses one of those tools can verify its adapter. It takes a handful of throwaway sessions and one pull request. Your own agentpace data stays untouched throughout.

The run definition for each harness, what counts as a run, how `duration_s` is measured and how `outcome` is decided, is in [DESIGN section 3](DESIGN.md#3-adapters). Read the part for your harness before you start, because it says what the rows should show.

## 1. Run throwaway sessions

Make an empty scratch folder and run 4 to 6 sessions of the harness inside it. Give the agent small, harmless work, such as writing and then editing a few text files. Cover these cases:

- **Completed:** one session where the agent finishes a turn normally.
- **Interrupted mid-turn:** one session you stop while the agent is working.
- **Multi-turn:** one session with 3 or more prompts from you.
- **Tool error:** one session where a tool call fails, for example reading a file that does not exist.
- **Parallel or sub-agent:** one session that runs agents in parallel or hands work to a sub-agent, if the harness has them.

For every turn, write down by hand when it started, when it ended, and how many tool calls the agent made. A wall clock and a tally are enough. These notes are what the rows are checked against.

## 2. Ingest into a scratch home

Point agentpace at a scratch home and a scratch rows file:

```bash
AGENTPACE_HOME=<scratch-home> agentpace ingest --source <harness> --out <scratch>/rows.jsonl
```

`AGENTPACE_HOME` moves the secret, the labels and the ingest caches into `<scratch-home>`, and `--out` sends the rows to `<scratch>/rows.jsonl`. So your real secret, labels and caches are untouched, and the scratch rows never reach `~/.agentpace/runs.jsonl` or the seed data. The scratch secret is new, so the pseudonyms in these rows do not link to any rows you share later.

Without a path, `ingest` reads the harness's whole history folder, not only the scratch sessions. Every session run in the scratch folder carries the same `project` value, so keep only the rows with that value. Keep the report `ingest` prints on stderr (files seen, rows produced, empty runs, unreadable files, unparsed lines and the rest): the pull request needs those counts.

## 3. Reconcile each session

Compare the rows with your notes, session by session:

- **Rows against turns.** Each run as DESIGN section 3 defines it for the harness (for Gemini CLI and Cline an agent turn, for OpenCode one prompt of yours and the replies to it) should be one row, with none missing and none extra.
- **`outcome`** against what happened: the completed turn should be `completed`, and the interrupted and errored turns should be what DESIGN section 3 says for that harness.
- **`duration_s`** against your noted start and end. It is agent active time, with every idle gap over the idle cap (900 s by default) clipped to the cap, and for Cline with approval waits dropped. So it can be shorter than the span you noted, but should not be longer than it, give or take how precisely you noted the times.
- **`tool_calls`** against your tally and the tool's own log.
- **`model`** against the model the harness showed you, after agentpace's normalization to a model family.
- **`batch`:** one value per session. A sub-agent's own session or file has its own `batch`, as DESIGN section 3 says for each harness.

Put the result in a table, one line per turn:

| Session | Turn | Noted start to end | Noted tools | Row `duration_s` | Row `tool_calls` | Expected outcome | Row `outcome` | `model` | `batch` | Match |
|---|---|---|---|---|---|---|---|---|---|---|

## 4. Open the pull request

The pull request carries, in its description:

- the harness version and your operating system;
- the counts from the `ingest` report;
- the reconciliation table;
- the scratch rows, pasted inline in a code block. Rows hold no content by construction, only the fields in the [run schema](../schema/run.schema.json).

Never add the scratch rows as a file under `data/runs/`. They come from throwaway sessions, not real work, and do not belong in the dataset.

## 5. Lift the label

When every line of the table matches, the same pull request lifts the experimental label:

- remove the harness name from `EXPERIMENTAL` in `src/agentpace/adapters/__init__.py`;
- update the tests that pin the label: the `validate` test that rejects experimental rows (`tests/test_share_validate.py`, and the adapter's own test file where it filters that rule out) and the `ExperimentalNote` tests in `tests/test_cli.py`;
- update the README: the harness's row in "Does it fit your setup?" and the experimental sentence in Limitations;
- update DESIGN sections 3 and 11 to say "verified on real sessions on <date>, <harness version>";
- add a `CHANGELOG.md` entry.

Lifting a label is a minor version.

## 6. The order of pull requests

A pull request that shares real-work rows from an experimental adapter is red by design: `validate-data` rejects its rows. That is expected, not a mistake to fix in the data file. The order is:

1. The verification pull request, which lifts the label, merges first.
2. The data pull request's checks re-run against the new base branch, whose validator now accepts the harness, and `validate-data` goes green.

## 7. When the rows do not match

A mismatch is a finding, and either outcome helps:

- **Fix it in the same pull request.** Change the adapter, bump its `version` (which invalidates cached runs), and add synthetic test fixtures that mirror the real file shape you saw. Fixtures hold no real content; they go in the harness's module under `tests/fixtures/` and are labeled synthetic. Then lift the label as in step 5.
- **Or file an issue** with the reconciliation table, the harness version and your operating system, at <https://github.com/mohamedzhioua/agentpace/issues>, so someone else can fix it.

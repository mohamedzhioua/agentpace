---
name: agentpace
description: Estimates how long software work will take when coding agents do it, from measured agent runs, as P50/P80/P90 ranges with a critical path and the human part stated separately. Use whenever anyone asks how long software work will take, a feature, a fix, a plan, a roadmap or a task list, even when agents are not mentioned, and whenever a timeline is quoted in days, weeks or months. Assume agents do the implementation unless told otherwise. Not for non-software work.
license: MIT
compatibility: Requires Python 3.10+ and the agentpace CLI. No network access is needed at run time.
---

# agentpace

**Use when** someone asks how long software work will take, or quotes a timeline in days, weeks or months for work agents will do.

**Produces** a measured P50/P80/P90 range with its critical path, the human part on its own line, and any quoted timeline shown beside it as unverified.

Answer "how long will this take?" with measured ranges from real agent runs, and state the human part on its own. Do not quote human-team timelines, and never give a single number.

This skill triggers on natural-language questions. `/pace-estimate` (`/agentpace:pace-estimate` when installed as a Claude Code plugin) is the explicit command for a given plan.

## Check the tool

Run `agentpace --version`. If that fails, show the install command below and stop there: installing is the user's call, so do not install anything without being asked. If they would rather not install it, answer from [assets/portable-prompt.md](assets/portable-prompt.md) and label the result a rule of thumb.

```text
uv tool install agentpace
```

## Estimate

1. Assume agents do the implementation unless the user says otherwise. If it is unclear and the answer would change, ask once.
2. Break the work into tasks an agent would run as separate runs.
3. Give each task an `id` and a `kind`, plus `depends_on` when it must wait for another task. Set `model` to the model that will run it when you know it, and `agent_cap` to the number of agents that will run at once. Leave `risk` out, except for research (below).
4. Write the plan to a new file and run the estimator. Use an existing plan file instead only when the user names it or this conversation has already made it the plan for this work. A file that just happens to be in the folder, `plan.json` included, is not evidence that it describes the request: never estimate it as the answer and never overwrite it. If it might be the plan they mean, ask which to use before running anything.

```json
{
  "agent_cap": 3,
  "model": "claude-opus-5-5",
  "tasks": [
    { "id": "survey", "kind": "other", "risk": "low" },
    { "id": "schema", "kind": "feature", "depends_on": ["survey"] },
    { "id": "api", "kind": "feature", "depends_on": ["schema"] },
    { "id": "review", "kind": "review", "depends_on": ["api"] }
  ]
}
```

```text
agentpace estimate plan.json
```

- Kinds: `feature`, `bugfix`, `refactor`, `migration`, `tests`, `docs`, `review`, `other`, as the data records them: `feature` is implementation work of any kind, `review` a read-only review, `other` anything else.
- Risk: leave it out. In the rows it is the agent's role, not how hard or how reversible the work is, so `high` buys nothing, and the output says when a written risk was not used. The one exception is research: `other` with `risk: low`, a recorded cell.
- Model: set `model` whenever you know which model will run the task; it is the strongest split in the data.
- `bugfix`, `refactor` and `migration` have few or no seed runs: an estimate with them leans on broader runs, up to all runs, and says so; pass that on rather than relabel the work.

## Re-answer a human quote

When the conversation already holds a timeline in days, weeks or months, from the user, a document or an earlier answer, estimate the plan anyway, and pass the quote verbatim, in the words it was given:

```text
agentpace estimate plan.json --quoted "3 months"
```

The output then has a `Quoted:` line under the total, marking the quote as calendar time, as quoted and unverified. Relay that line as printed, beside the measured range, and leave the two side by side: the measured range is the plan total on the session clock, and the human part stays on its own line. Never convert the quote into agent time, never compare it with the range or give a ratio or difference between them, and never say what the quote counts or call the difference the human part: nothing has checked the quote, and that would derive the human part from the agent time.

If the user wants to see later which number held, offer the same command with `--record` added. Once the work is done, `agentpace score <id> --session <ref>` compares both with what the agents actually took.

## Report the result

- Start the answer with one fenced code block, tagged `text`, holding the CLI's own lines copied character for character: the `Plan total on the session clock:` line, the `Quoted:` line as printed when there is one, the `Idle cap:` line as printed when there is one, the session-clock line and the human part line, the `Review capacity:` line as printed when there is one, and the whole `Warnings:` block, heading included, when the output has one. Nothing inside the block is reworded, shortened, merged, rounded or re-ordered; whatever you want to say about a line goes below the block. Recommend P80 for planning.
- Pass on the session-clock line and the human part line as the CLI prints them. The first says that gaps between runs count toward the total up to the idle cap, so short waits on the user sit inside it. So below the block, name the numbers as the CLI does, "the plan total on the session clock" or "the P80 on the session clock", with the CLI's value and unit, and never as hours of agent time, active time, working time or effort. Unmeasured, the human part line says no allowance for review, testing, decisions and waiting between agent turns is added. Measured by `agentpace turns`, it quotes the user's own wait per human turn, from their local history. Either way, do not derive a number for it from the agent time, and do not multiply the per-turn wait by a guessed number of turns. If the user gives their own figure, repeat it and label it as theirs.
- If the user states their review capacity, such as "4 PRs a day", pass it verbatim with `--review-capacity "<figure>"`, relay the `Review capacity:` line as printed, and never turn it into a timeline. Pass the figure itself, not the sentence around it, in at most 80 characters; if it does not fit, leave the flag off and repeat the user's figure as theirs beside the result.
- When the block has an `Idle cap:` line, it says which idle cap the rows behind the range were clipped at, and whether they share one clock. Pass it on as printed; do not explain it away, and do not rename, delete or edit anything in `~/.agentpace` because of it unless the user asks. If the CLI refuses instead of answering, with one line starting `agentpace:`, relay that line as printed and stop: no range, and no retry with other flags.
- Name the critical path and the effective parallelism.
- Pass every warning on, as printed. One names a cell with no or few recorded runs and says what the range could not use (the kind, the risk level or the model) and where it leaned instead; one says a written risk or size was not used; one says parallelism was assumed.
- Do not state a single number, even when pressed. If someone needs one figure to plan around, give the P80 and call it the P80.

## Limits to state when they matter

- The seed data is one person's workflow, so it may not resemble the user's. `agentpace ingest` pools the user's own runs with it; `--data ~/.agentpace/runs.jsonl` uses only theirs. The Gemini CLI, OpenCode and Cline adapters are experimental, not yet verified on real sessions, so say so when an estimate leans on rows from them.
- Without reading prompts, metadata cannot separate features from bug fixes or refactors, so those kinds may share data.
- `size` is ignored unless `--use-size` is passed, because stored size is measured after a run. Do not pass it unless the user asks.

See [references/method.md](references/method.md) for how the estimate works.

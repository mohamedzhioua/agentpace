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

This skill triggers on natural-language questions. `/pace-estimate` is the explicit command for a given plan.

## Check the tool

Run `agentpace --version`. If that fails, tell the user the install command and stop. Do not install anything without being asked:

```text
uv tool install git+https://github.com/mohamedzhioua/agentpace
```

If they do not want to install anything, use [assets/portable-prompt.md](assets/portable-prompt.md) instead.

## Estimate

1. Assume agents do the implementation unless the user says otherwise. If it is unclear and the answer would change, ask once.
2. Break the work into tasks an agent would run as separate runs.
3. Give each task an `id`, a `kind` and a `risk`, plus `depends_on` when it must wait for another task. Set `agent_cap` to the number of agents that will run at once.
4. Write the plan to a file and run the estimator.

```json
{
  "agent_cap": 3,
  "model": "claude-opus-5-5",
  "tasks": [
    { "id": "schema", "kind": "feature", "risk": "normal" },
    { "id": "api", "kind": "feature", "risk": "normal", "depends_on": ["schema"] },
    { "id": "review", "kind": "review", "risk": "low", "depends_on": ["api"] }
  ]
}
```

```text
agentpace estimate plan.json
```

Kinds: `feature`, `bugfix`, `refactor`, `migration`, `tests`, `docs`, `review`, `other`. Risks: `low`, `normal`, `high`. Use `risk: high` for migrations and for work that is hard to reverse. `model` is optional.

## Re-answer a human quote

When the conversation already holds a timeline in days, weeks or months, from the user, a document or an earlier answer, estimate the plan anyway, and pass the quote verbatim, in the words it was given:

```text
agentpace estimate plan.json --quoted "3 months"
```

The output then has a `Quoted:` line under the total, marking the quote as calendar time, as quoted and unverified. Relay that line as printed, beside the measured range. Never convert the quote into agent time, never divide or multiply it by a constant, and never compare it with the range or give a ratio between them: the only agent time is the measured range, and the human part stays on its own line.

If the user wants to see later which number held, offer the same command with `--record` added. Once the work is done, `agentpace score <id> --session <ref>` compares both with what the agents actually took.

## Report the result

- Lead with the plan total as a range: P50, P80 and P90, on the session clock. Recommend P80 for planning.
- Pass on the session-clock line and the human part line as the CLI prints them. The first says that gaps between runs count toward the total up to the idle cap, so short waits on the user sit inside it. Unmeasured, the human part line says no allowance for review, testing, decisions and waiting between agent turns is added. Measured by `agentpace turns`, it quotes the user's own wait per human turn, from their local history. Either way, do not derive a number for it from the agent time, and do not multiply the per-turn wait by a guessed number of turns. If the user gives their own figure, repeat it and label it as theirs.
- Name the critical path and the effective parallelism.
- Pass every warning on. A warning means a cell had too few runs, so that range leans on broader data.
- Do not state a single number, even when pressed. If someone needs one figure to plan around, give the P80 and call it the P80.

## Limits to state when they matter

- The seed data is one person's workflow, so it may not resemble the user's. `agentpace ingest` pools the user's own runs with it; `--data ~/.agentpace/runs.jsonl` uses only theirs. The Gemini CLI, OpenCode and Cline adapters are experimental, not yet verified on real sessions, so say so when an estimate leans on rows from them.
- Without reading prompts, metadata cannot separate features from bug fixes or refactors, so those kinds may share data.
- `size` is ignored unless `--use-size` is passed, because stored size is measured after a run. Do not pass it unless the user asks.

See [references/method.md](references/method.md) for how the estimate works.

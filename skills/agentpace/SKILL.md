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

When the conversation already holds a timeline in days, weeks or months, from the user, a document or an earlier answer, estimate the plan anyway. Then show the quote beside the measured range and label it unverified. Never divide or multiply it by a constant to get agent time: the only agent time is the measured range.

## Report the result

- Lead with the agent time as a range: P50, P80 and P90. Recommend P80 for planning.
- Pass on the human part line as the CLI prints it: review, testing, decisions and waiting between agent turns are not measured and not in the total. Do not derive a number for it from the agent time. If the user gives their own figure, repeat it and label it as theirs.
- Name the critical path and the effective parallelism.
- Pass every warning on. A warning means a cell had too few runs, so that range leans on broader data.
- Do not state a single number, even when pressed. If someone needs one figure to plan around, give the P80 and call it the P80.

## Limits to state when they matter

- The seed data is one person's workflow, so results improve once the user runs `agentpace ingest` on their own history.
- Without reading prompts, metadata cannot separate features from bug fixes or refactors, so those kinds may share data.
- `size` is ignored unless `--use-size` is passed, because stored size is measured after a run. Do not pass it unless the user asks.

See [references/method.md](references/method.md) for how the estimate works.

---
name: agentpace
description: Estimates how long coding-agent work will take from real run data, as P50/P80/P90 ranges with a critical path. Use when asked how long agent work will take, to estimate or size a plan, roadmap or task list for agents, or when a timeline is being quoted in human days or months for work agents will do. Not for work done by people without agents.
license: MIT
compatibility: Requires Python 3.10+ and the agentpace CLI. No network access is needed at run time.
---

# agentpace

Answer "how long will this take?" with measured ranges from real agent runs. Do not quote human-scale timelines, and never give a single number.

## Check the tool

Run `agentpace --version`. If that fails, tell the user the install command and stop. Do not install anything without being asked:

```text
uv tool install git+https://github.com/mohamedzhioua/agentpace
```

If they do not want to install anything, use [assets/portable-prompt.md](assets/portable-prompt.md) instead.

## Estimate

1. Break the work into tasks an agent would run as separate runs.
2. Give each task an `id`, a `kind` and a `risk`, plus `depends_on` when it must wait for another task. Set `agent_cap` to the number of agents that will run at once.
3. Write the plan to a file and run the estimator.

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

## Report the result

- Lead with the total as a range: P50, P80 and P90. Recommend P80 for planning.
- Name the critical path and the effective parallelism.
- Pass every warning on. A warning means a cell had too few runs, so that range leans on broader data.
- Say the times cover agent work only. They leave out human review and decisions, which often take longer.
- Do not state a single number, even when pressed. If someone needs one figure to plan around, give the P80 and call it the P80.

## Limits to state when they matter

- The seed data is one person's workflow, so results improve once the user runs `agentpace ingest` on their own history.
- Without reading prompts, metadata cannot separate features from bug fixes or refactors, so those kinds may share data.
- `size` is ignored unless `--use-size` is passed, because stored size is measured after a run. Do not pass it unless the user asks.

See [references/method.md](references/method.md) for how the estimate works.

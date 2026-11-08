---
name: pace-estimate
description: Estimates a plan of coding-agent tasks as P50/P80/P90 agent-time ranges with a critical path and the human part stated separately, using the agentpace CLI. Use when the user runs /pace-estimate or asks for a timed estimate of a specific task list or plan file. Natural-language "how long will this take" questions are the agentpace skill's job.
license: MIT
compatibility: Requires Python 3.10+ and the agentpace CLI.
---

# Estimate a plan

**Use when** the user runs `/pace-estimate`, the explicit command, with a plan file or a task list. Natural-language questions belong to the `agentpace` skill.

**Produces** P50/P80/P90 for the whole plan and each task, the critical path, effective parallelism, the human part line and every warning.

The plan to estimate: $ARGUMENTS

1. Run `agentpace --version`. If it fails, show this command and stop, because installing is the user's call: `uv tool install agentpace`
2. If the input is a JSON plan file, use it. Otherwise break the work into tasks and write a plan file with this shape:

   ```json
   {
     "agent_cap": 3,
     "tasks": [
       { "id": "api", "kind": "feature", "risk": "normal" },
       { "id": "review", "kind": "review", "risk": "low", "depends_on": ["api"] }
     ]
   }
   ```

   Kinds: `feature`, `bugfix`, `refactor`, `migration`, `tests`, `docs`, `review`, `other`. Risks: `low`, `normal`, `high`.
3. Run `agentpace estimate <file>`. If the conversation already holds a timeline in days, weeks or months, add `--quoted "<quote>"` in the words it was given. Only if the user stated their own review capacity, such as "4 PRs a day", add `--review-capacity "<figure>"` with their words verbatim.
4. Report the total as P50, P80 and P90, then the `Quoted:` line as printed when there is one, then the session-clock line and the human part line exactly as the CLI prints them, and the `Review capacity:` line as printed when there is one, never turned into a timeline, then the critical path, the effective parallelism and every warning. Do not derive a number for the human part from the agent time. Never convert the quote, compare it with the range, say what the difference between them is, or say what the quote counts: it is calendar time, as quoted, and nothing has checked it. Never reduce the result to one number.

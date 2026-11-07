---
name: pace-estimate
description: Estimates a plan of coding-agent tasks as P50/P80/P90 wall-clock ranges with a critical path, using the agentpace CLI. Use when the user runs /pace-estimate or asks for a timed estimate of a specific task list or plan file.
license: MIT
compatibility: Requires Python 3.10+ and the agentpace CLI.
---

# Estimate a plan

The plan to estimate: $ARGUMENTS

1. Run `agentpace --version`. If it fails, show this command and stop, because installing is the user's call: `uv tool install git+https://github.com/mohamedzhioua/agentpace`
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
3. Run `agentpace estimate <file>`.
4. Report the total as P50, P80 and P90, then the critical path, the effective parallelism and every warning. Say these times cover agent work only, not human review or decisions. Never reduce the result to one number.

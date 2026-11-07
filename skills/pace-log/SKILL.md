---
name: pace-log
description: Records recent coding-agent runs in the local agentpace store and corrects a run's kind or risk label. Use when the user runs /pace-log or asks to log agent runs or relabel one. Everything stays on this machine.
license: MIT
compatibility: Requires Python 3.10+ and the agentpace CLI.
---

# Log runs

Input, if any: $ARGUMENTS

1. Run `agentpace --version`. If it fails, show this command and stop, because installing is the user's call: `uv tool install git+https://github.com/mohamedzhioua/agentpace`
2. Run `agentpace ingest`. It reads timestamps, model ids, token and tool counts only, and keeps rows in `~/.agentpace`. Nothing leaves this machine.
3. Run `agentpace runs --last 10` and show the list.
4. If the user gave a kind or risk, apply it. `agentpace label last --kind bugfix` labels the latest run, and a reference from the list labels any other. Kinds: `feature`, `bugfix`, `refactor`, `migration`, `tests`, `docs`, `review`, `other`. Risks: `low`, `normal`, `high`.

Labels are the only way to separate features, bug fixes and refactors, because agentpace does not read prompts. Do not guess a label from the conversation unless the user asks you to.

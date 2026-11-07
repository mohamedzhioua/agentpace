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

## Optional: log every run automatically

If the user wants runs logged without asking each time, show them this Claude Code hook and let them add it themselves. Do not edit their settings for them. It goes under `hooks` in `~/.claude/settings.json`:

```json
{"hooks": {"SubagentStop": [{"hooks": [{"type": "command", "command": "agentpace ingest --hook", "timeout": 30}]}]}}
```

Run `agentpace ingest` once first, so the hook starts from a warm cache. After each subagent stops, the hook ingests that session, usually in about a second. It reads only the transcript path from the hook's input, never the agent's last message, and it always exits quietly, so it cannot block or interrupt an agent. Runs still going are held back until a later pass finds them finished. Because the hook cannot report problems, run `agentpace ingest` by hand if runs stop appearing: it prints what went wrong.

Labels are the only way to separate features, bug fixes and refactors, because agentpace does not read prompts. Do not guess a label from the conversation unless the user asks you to.

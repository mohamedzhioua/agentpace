---
name: pace-log
description: Records recent coding-agent runs in the local agentpace store and corrects a run's kind or risk label. Use when the user runs /pace-log or asks to log, record or track their agent runs, or to relabel one. Everything stays on this machine.
license: MIT
compatibility: Requires Python 3.10+ and the agentpace CLI.
---

# Log runs

**Use when** the user runs `/pace-log` or `/agentpace:pace-log`, or asks to log, record or track their agent runs, or to relabel one.

**Produces** fresh rows in `~/.agentpace` from one harness's local history, the latest runs listed with their references, corrected labels, and the opt-in hook snippet when asked.

Input, if any: $ARGUMENTS

1. Run `agentpace --version`. If it fails, show this command and stop, because installing is the user's call: `uv tool install agentpace`
2. Pick the source, and tell the user which one you are ingesting. `agentpace ingest` reads Claude Code subagent runs, the default. For another harness, add `--source` with one of `claude-code`, `codex`, `gemini-cli`, `opencode` or `cline`. It reads timestamps, model ids, token and tool counts only, and keeps rows in `~/.agentpace`. Nothing leaves this machine.
   - `gemini-cli`, `opencode` and `cline` are experimental: each adapter was built from the tool's source and is not yet verified on real sessions, and `agentpace validate` rejects their rows, so a data pull request with them stays red until the adapter is verified. Say so when you use one, and pass on the note `ingest` prints. Anyone who wants to verify one can follow [docs/verify-adapter.md](https://github.com/mohamedzhioua/agentpace/blob/main/docs/verify-adapter.md).
   - Leave `--idle-cap` alone unless the user asks for another cap. A store keeps the one cap it was filled with, and `ingest` refuses a different one for that store. The published dataset uses the default, so rows ingested at another cap cannot be shared. If `ingest` refuses, or says the store's cap is not the default or not recorded, pass its line on as printed and stop there: do not rename, delete or edit anything in `~/.agentpace`, and do not retry with other flags, unless the user asks.
3. Run `agentpace runs --last 10` and show the list.
4. If the user gave a kind or risk, apply it. `agentpace label last --kind bugfix` labels the latest run, and a reference from the list labels any other. If `label last` refuses because it cannot tell which run is last, do not guess: show the user the list from step 3 and label the reference they pick. Kinds: `feature`, `bugfix`, `refactor`, `migration`, `tests`, `docs`, `review`, `other`. Risks: `low`, `normal`, `high`.

## Optional: log every run automatically

If the user wants runs logged without asking each time, show them this Claude Code hook and let them add it themselves. Do not edit their settings for them. It goes under `hooks` in `~/.claude/settings.json`:

```json
{"hooks": {"SubagentStop": [{"hooks": [{"type": "command", "command": "agentpace ingest --hook", "timeout": 30}]}]}}
```

Run `agentpace ingest` once first, so the hook starts from a warm cache. After each subagent stops, the hook ingests that session, usually in about a second. It reads only the transcript path from the hook's input, never the agent's last message, and it always exits quietly, so it cannot block or interrupt an agent. Runs still going are held back until a later pass finds them finished. Because the hook cannot report problems, run `agentpace ingest` by hand if runs stop appearing: it prints what went wrong.

Labels are the only way to separate features, bug fixes and refactors, because agentpace does not read prompts. Do not guess a label from the conversation unless the user asks you to.

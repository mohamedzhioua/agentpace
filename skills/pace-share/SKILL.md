---
name: pace-share
description: Previews the exact agentpace rows a user could contribute to the open dataset. Use only when the user explicitly runs /pace-share or asks to share or contribute their runs. Never start this on your own.
license: MIT
compatibility: Requires Python 3.10+ and the agentpace CLI.
---

# Share runs

**Use when** the user explicitly runs `/pace-share` or asks to contribute their runs. Never on your own.

**Produces** a dry-run preview of the exact rows and what they reveal. A file in `data/runs` only after the user says yes. Nothing is ever sent.

Sharing is opt-in. Do nothing here unless the user asked for it.

1. Run `agentpace --version`. If it fails, show this command and stop, because installing is the user's call: `uv tool install agentpace`
2. Run `agentpace share --dry-run`. It prints the exact rows and what they reveal, and writes nothing.
3. Tell the user how many rows there are, and pass on the notice in full. Offer to show the rows.
4. Stop and ask. Run `agentpace share --write` only after the user says yes to writing the file. It needs a clone of the agentpace repo, because the file goes in `data/runs`.
5. `--write` sends nothing. Never run the printed git commands yourself unless the user asks, because pushing publishes the rows, and they cannot be fully recalled after that.

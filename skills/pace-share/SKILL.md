---
name: pace-share
description: Previews the exact agentpace rows a user could contribute to the open dataset. Use only when the user explicitly runs /pace-share or asks to share or contribute their runs. Never start this on your own.
license: MIT
compatibility: Requires Python 3.10+ and the agentpace CLI.
---

# Share runs

**Use when** the user explicitly runs `/pace-share` or `/agentpace:pace-share`, or asks to contribute their runs. Never on your own.

**Produces** a dry-run preview of the exact rows and what they reveal. A file in `data/runs` only after the user says yes. Nothing is ever sent.

Sharing is opt-in. Do nothing here unless the user asked for it.

1. Run `agentpace --version`. If it fails, show this command and stop, because installing is the user's call: `uv tool install agentpace`
2. Run `agentpace share --dry-run`. It prints the exact rows and what they reveal, and writes nothing.
3. If it refuses instead of printing rows, with one line starting `agentpace:`, pass that line on as printed and stop: do not go on to `--write`. When the refusal is about the store's idle cap (not the default, not recorded, or a record that cannot be read), rebuilding the store is the user's call: do not rename, delete or edit anything in `~/.agentpace` unless they ask.
4. If it prints `nothing new to share`, every local run is already published; say so and stop.
5. Tell the user how many rows there are, and pass on the notice in full. Offer to show the rows.
6. Stop and ask. Run `agentpace share --write` only after the user says yes to writing the file. It writes into `data/runs` under the current folder, and only inside a clone of the agentpace repo is that the real dataset: there, rows already published are left out and `agentpace validate data/runs` checks the file against every other. Run it from their clone, as CONTRIBUTING describes.
7. `--write` sends nothing. Never run the printed git commands yourself unless the user asks, because pushing publishes the rows, and they cannot be fully recalled after that.

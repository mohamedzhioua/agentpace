# Security

## Report a problem

Please report privacy or security problems through [GitHub private vulnerability reporting](https://github.com/mohamedzhioua/agentpace/security/advisories/new), not in a public issue. Do not paste the sensitive value itself.

This matters most for anything that could put a prompt, code, a file path, a repo name or a commit message into a row or an output.

## Published data

If you find personal data in `data/runs/`, report it privately. If you contributed rows and want them removed, open an issue naming your `contributor` hash, and they will be removed from the current tree. Git history and copies others have made cannot be recalled, which is why `agentpace share --dry-run` shows the exact rows first.

## What agentpace promises

- The package contains no network code, and `tests/test_share_validate.py` enforces that.
- Rows hold only the fields in `schema/run.schema.json`.
- Your local secret stays in `~/.agentpace/secret`. Keep it private. Anyone who has it can check guesses about your session and project names against published hashes.

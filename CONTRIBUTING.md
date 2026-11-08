# Contributing

## Contribute your runs

This is the most useful contribution, because the contributor split cannot run until a second person joins. [Issue #2](https://github.com/mohamedzhioua/agentpace/issues/2) is the standing call for that second contributor.

1. Fork and clone the repo, and install it with `uv tool install .`
2. Run `agentpace ingest`. It reads your local history and keeps rows on your machine.
3. Run `agentpace share --dry-run` and read the rows. Stop here if you are not comfortable with them.
4. Run `agentpace share --write`. It adds or extends one file under `data/runs/`, named after your contributor pseudonym and the weeks it covers. Add `--drop-project` to leave out the project field.
5. Run `agentpace validate data/runs`, then commit that one file and open a pull request. Plain `agentpace validate` checks the seed data shipped with agentpace, not the folder you just wrote to.

**By opening a data pull request, you dedicate those rows to the public domain under [CC0-1.0](data/LICENSE).** Once merged, public data cannot be fully recalled, so read the rows first.

CI validates every data pull request. It rejects unknown fields, duplicate runs, implausible values, unknown model names and anything shaped like personal data. The validator always runs from the base branch, so a pull request that changes the validator is still judged by the existing one.

A data pull request should touch only `data/runs/`.

### What happens to your pull request

You supply the file. The maintainer does the rest:

- **"Validate data" must pass.** That check (`validate-data.yml`) runs `agentpace validate data/runs` with the base branch's validator. A failure here is something to fix in your file.
- **"Lint, types and published numbers" stays red, and that is expected.** That check is the `quality` job in `ci.yml`. New rows move the published numbers, so the backtest, the site and the README claims no longer match the data. The maintainer adds a commit to your branch with the regenerated backtest, site and claims, a CHANGELOG entry that lists every number that moved, and a version bump. Then it goes green.
- **Keep "Allow edits by maintainers" on** when you open the pull request, so that commit can land on your branch.

Rows from an experimental adapter (Gemini CLI, OpenCode, Cline) fail "Validate data" until that adapter is verified. See [Verify an experimental adapter](#verify-an-experimental-adapter).

## Label your runs

Metadata cannot tell a feature from a bug fix. Labels can: without them, bug fixes and refactors look like any other implementation run, so the `bugfix` and `refactor` cells stay thin until people label runs. Run `agentpace runs` to find a reference, then `agentpace label <ref> --kind bugfix --risk high`. Do this before `agentpace share`.

## Add a harness

Write one module in `src/agentpace/adapters/` that satisfies the `Adapter` protocol, register it in `adapters/__init__.py`, and add its name to the `harness` enum in `schema/run.schema.json`. An adapter returns `RawRun` records, which have no field for content. Add canary tests like those in `tests/test_ingest.py`.

## Verify an experimental adapter

The Gemini CLI, OpenCode and Cline adapters were built from each tool's source and have not been checked against real sessions. If you use one of those tools, you can verify its adapter with a few throwaway sessions, without touching your own agentpace data. [docs/verify-adapter.md](docs/verify-adapter.md) gives the steps, what the pull request must carry, and how the experimental label is lifted.

## Change the code

```bash
PYTHONPATH=src python -m unittest discover -s tests -t .
uvx ruff check . && uvx ruff format --check . && uvx mypy
```

The rules that matter:

- **Standard library only** at run time.
- **No network code.** A test enforces this.
- **No fabricated data.** Test fixtures live in `tests/synthetic.py` and are labeled synthetic. Seed rows come only from real runs.
- **Every number that comes from the data sits between claim markers in the README.** `agentpace claims` writes them, and CI fails if they drift.
- **Report losses.** After changing the method, run `agentpace backtest`, `agentpace site` and `agentpace claims`, and commit what they produce. Method selection is frozen at 2026-W45: a pin test fails CI if the winner in `docs/backtest.json` stops matching `DEFAULT_METHOD`, and a flip needs re-registration in `CHANGELOG.md`, never a silent edit (DESIGN section 6).
- **Forecasts use only pre-run fields.** `tool_calls`, `tokens`, `retries` and `outcome` are measured during a run, so they can never predict one.

Commit generated files together with the change that caused them.

A change to a command, a flag, the plan format or the `--json` output also updates [docs/cli.md](docs/cli.md), the command reference. `tests/test_docs.py` fails if a command or a JSON key is missing from it.

## Conduct

Everyone taking part follows the [Code of Conduct](CODE_OF_CONDUCT.md).

# Contributing

## Contribute your runs

This is the most useful contribution, because the contributor split cannot run until a second person joins.

1. Fork and clone the repo, and install it with `uv tool install .`
2. Run `agentpace ingest`. It reads your local history and keeps rows on your machine.
3. Run `agentpace share --dry-run` and read the rows. Stop here if you are not comfortable with them.
4. Run `agentpace share --write`. Add `--drop-project` to leave out the project field.
5. Run `agentpace validate`, then commit that one file and open a pull request.

**By opening a data pull request, you dedicate those rows to the public domain under [CC0-1.0](data/LICENSE).** Once merged, public data cannot be fully recalled, so read the rows first.

CI validates every data pull request. It rejects unknown fields, duplicate runs, implausible values, unknown model names and anything shaped like personal data. The validator always runs from the base branch, so a pull request that changes the validator is still judged by the existing one.

A data pull request should touch only `data/runs/`.

## Label your runs

Metadata cannot tell a feature from a bug fix. Labels can, and they improve every estimate. Run `agentpace runs` to find a reference, then `agentpace label <ref> --kind bugfix --risk high`. Do this before `agentpace share`.

## Add a harness

Write one module in `src/agentpace/adapters/` that satisfies the `Adapter` protocol, register it in `adapters/__init__.py`, and add its name to the `harness` enum in `schema/run.schema.json`. An adapter returns `RawRun` records, which have no field for content. Add canary tests like those in `tests/test_ingest.py`.

## Change the code

```bash
PYTHONPATH=src python -m unittest discover -s tests -t .
uvx ruff check . && uvx ruff format --check . && uvx mypy
```

The rules that matter:

- **Standard library only** at run time.
- **No network code.** A test enforces this.
- **No fabricated data.** Test fixtures live in `tests/synthetic.py` and are labeled synthetic. Seed rows come only from real runs.
- **Every number in the README sits between claim markers.** `agentpace claims` writes them, and CI fails if they drift.
- **Report losses.** After changing the method, run `agentpace backtest`, `agentpace site` and `agentpace claims`, and commit what they produce. If a new method wins the selection rule, update `DEFAULT_METHOD`. A test keeps them in step.
- **Forecasts use only pre-run fields.** `tool_calls`, `tokens`, `retries` and `outcome` are measured during a run, so they can never predict one.

Commit generated files together with the change that caused them.

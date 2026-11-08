<!-- Keep the section that fits and delete the other. -->

## Contributing runs

- [ ] I read every row that `agentpace share --dry-run` printed.
- [ ] `agentpace validate data/runs` passes.
- [ ] This pull request adds or extends one file under `data/runs/` and touches nothing else.
- [ ] I dedicate these rows to the public domain under CC0-1.0.
- [ ] "Allow edits by maintainers" is on. The "Lint, types and published numbers" check stays red until the maintainer adds the regenerated numbers (see CONTRIBUTING.md).

## Changing code

- [ ] Tests pass, and new behavior has a test.
- [ ] I ran `agentpace backtest`, `agentpace site` and `agentpace claims`, and committed what they produced.
- [ ] If this adds a harness, it has canary tests like those in `tests/test_ingest.py`.

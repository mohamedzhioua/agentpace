# Releasing agentpace

This is the maintainer's runbook. agentpace is not on PyPI yet: every install line installs from git, and `publish.yml` runs only when dispatched by hand. Part 1 is the release every version gets today. Parts 2 to 5 are the one-time PyPI setup and first upload, which only the owner can do. Line numbers below are as of v0.8.0; later releases may move them, so search for the quoted text.

## 1. Every release

1. Each change lands as one small pull request, merged with a merge commit once the full local checks pass: the tests, `ruff`, `mypy`, `agentpace validate`, and `backtest`, `site` and `claims` with `--check`.
2. The release carries a `CHANGELOG.md` entry headed `## X.Y.Z - YYYY-MM-DD`, in Keep a Changelog sections. It lists every published number that moved, or ends with `No published number moved.`
3. The version goes in two places: `__version__` in `src/agentpace/__init__.py`, and `version` and `date-released` in `CITATION.cff`. A test fails when the two versions differ.
4. After the merge, run `git fetch`, then tag the merge commit with an annotated tag and push it:

   ```bash
   git tag -a vX.Y.Z -m "agentpace X.Y.Z" <merge-commit-sha>
   git push origin vX.Y.Z
   ```

5. Publish a GitHub release page for the tag (`gh release create vX.Y.Z`). Tags v0.2.0 to v0.8.0 each have one.

While `publish.yml` is manual, a release page uploads nothing.

## 2. The PyPI pending publisher (once)

agentpace uses trusted publishing: PyPI accepts the identity of the `publish.yml` job, so there is no API token to create, store or rotate. On PyPI, under your account's Publishing settings, add a pending GitHub publisher with exactly these values:

| Field | Value |
|---|---|
| PyPI project name | `agentpace` |
| Owner | `mohamedzhioua` |
| Repository name | `agentpace` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

- **The `pypi` environment.** The job names `environment: pypi`. GitHub creates that environment the first time a run references it, so there is nothing to make beforehand. If you add a required-reviewer rule to it, every publish run waits for your approval before it uploads. That is fine, but expect the wait.
- **The name is not reserved.** A pending publisher does not reserve `agentpace` on PyPI; anyone can still register it first. Recheck right before you dispatch: open `https://pypi.org/project/agentpace/`. A 404 means no project holds the name. If someone else has taken it, the upload fails and nothing is published.

## 3. The first upload

1. Pick the latest tag whose merge commit passed CI. Use v0.8.4 or later: earlier tags have relative README images, which do not render on the PyPI page.
2. Check that the tag matches the version. The workflow does this too and fails on a mismatch: `git show vX.Y.Z:src/agentpace/__init__.py` must say `__version__ = "X.Y.Z"`.
3. Dispatch the workflow on that tag:

   ```bash
   gh workflow run publish.yml --ref vX.Y.Z
   ```

   This needs a `Bash(gh workflow run:*)` permission that the agent does not have, so the owner runs it.
4. Watch the run (`gh run list --workflow publish.yml`, then `gh run watch <id>`) and confirm it passes.

**A published version is final.** PyPI never accepts a second upload of the same version, even after you delete it, so never re-dispatch a version that uploaded. If anything about it needs fixing, bump the patch version, release it as in part 1, and dispatch the new tag. A run that failed before `uv publish` uploaded anything can be dispatched again.

The PyPI page shows the README as it was at the tag. That README still carries the git install lines and the "not on PyPI yet" sentence, so the first version's page says so. The page is right from the next release after the switch in part 5.

## 4. The clean-venv check

Before any install line changes, prove the upload works from a fresh environment, away from the checkout and with an empty agentpace home so no local data reaches the output:

```bash
scratch=$(mktemp -d)
python -m venv "$scratch/venv"
"$scratch/venv/bin/python" -m pip install --no-cache-dir agentpace==X.Y.Z   # Scripts/python.exe on Windows
cd "$scratch"
"$scratch/venv/bin/agentpace" --version                                     # agentpace X.Y.Z
PYTHONPATH= AGENTPACE_HOME="$scratch/home" "$scratch/venv/bin/agentpace" estimate --example > example.txt
git -C <repo> show vX.Y.Z:README.md > README.md
python -I -c "import re, sys; want = re.search(r'<!-- claim:example_output -->\n(.*?)<!-- /claim -->', open(sys.argv[1], encoding='utf-8').read(), re.S).group(1).strip(); got = open(sys.argv[2], encoding='utf-8').read().strip(); sys.exit(0 if want == got else 'the --example output differs from the README')" README.md example.txt
```

The last command exits 0 only when the installed `estimate --example` output equals the README's `example_output` block at that tag, compared the same way as CI's "The built package runs the README example" step (surrounding whitespace stripped). If it fails, change nothing in the README; fix the cause and release a new patch version.

## 5. After the upload: the switch

Only once part 4 passes, one pull request makes every change below, with a CHANGELOG entry and a version bump. Each item quotes the current text.

1. **README "Try it" line** (`README.md:17`):

   ```text
   uvx --from git+https://github.com/mohamedzhioua/agentpace agentpace estimate --example
   ```

   becomes `uvx agentpace estimate --example`.
2. **README Install block**, both commands and the sentence after them. The command (`README.md:92`):

   ```text
   uv tool install git+https://github.com/mohamedzhioua/agentpace
   ```

   becomes `uv tool install agentpace`. The sentence (`README.md:95`):

   ```text
   `pipx install git+https://github.com/mohamedzhioua/agentpace` works too. It needs Python 3.10 or later and has no dependencies. agentpace is not on PyPI yet, which is why both commands install from git. After installing, drop the `uvx --from ...` prefix from the command above.
   ```

   The pipx command becomes `pipx install agentpace`, the "not on PyPI yet" sentence goes, and the last sentence says to drop `uvx` from the command above.
3. **Site "Try it" line** (`docs/index.html:207`):

   ```text
   <pre><code>uvx --from git+https://github.com/mohamedzhioua/agentpace agentpace estimate --example</code></pre>
   ```

   The command becomes `uvx agentpace estimate --example`.
4. **The install command in all four skills.** `skills/agentpace/SKILL.md:23`:

   ```text
   uv tool install git+https://github.com/mohamedzhioua/agentpace
   ```

   and line 16 of `skills/pace-estimate/SKILL.md`, `skills/pace-log/SKILL.md` and `skills/pace-share/SKILL.md`:

   ```text
   1. Run `agentpace --version`. If it fails, show this command and stop, because installing is the user's call: `uv tool install git+https://github.com/mohamedzhioua/agentpace`
   ```

   The command becomes `uv tool install agentpace`. Keep "the user's call" and "without being asked": a test requires them wherever a skill shows an install command.
5. **DESIGN section 8, Install** (`docs/DESIGN.md:267`), which begins:

   ```text
   **Install.** `uv tool install git+https://github.com/mohamedzhioua/agentpace` or the `pipx` equivalent, with no PyPI publish needed.
   ```

   It becomes `uv tool install agentpace` or the `pipx` equivalent, from PyPI since X.Y.Z. The ruling in DESIGN section 11 about releasing v0.1.0 is history and stays.
6. **`tests/test_skills.py::test_the_quickstart_needs_no_clone`** (`tests/test_skills.py:48`):

   ```text
   command = "uvx --from git+https://github.com/mohamedzhioua/agentpace"
   ```

   becomes `command = "uvx"`, so it asserts `uvx agentpace estimate --example`.

Two more places hold the git URL or depend on it, beyond the list above:

7. **`tests/test_skills.py::test_a_command_is_on_the_first_screen`** (`tests/test_skills.py:31`) looks for a line starting with `uvx --from` in the README's first 25 lines, so it fails once item 1 changes. Its prefix becomes `uvx agentpace`.
8. **The site's "Check it yourself" block** (`docs/index.html:265`) starts with `uv tool install git+https://github.com/mohamedzhioua/agentpace`, which becomes `uv tool install agentpace`.

In the same pull request, add `release: types: [published]` under `on:` in `publish.yml` and update its comment, so from then on a release page uploads its version. Keeping `workflow_dispatch` beside it is harmless.

When the switch is done, `git grep -n "git+https://github.com/mohamedzhioua/agentpace"` and `git grep -n -i "not on PyPI"` both print nothing.

## The README on PyPI

PyPI renders the README as the project description. The three images use absolute `raw.githubusercontent.com` URLs on `main`, so they render on GitHub and on PyPI alike; on PyPI they show the charts as they are on `main` today, not as they were at that version.

The README's other relative links do not resolve on PyPI, because PyPI has no repository to resolve them against. That is accepted: the page links the repository, and these targets are one click away there. As of v0.8.4 they are:

- `LICENSE` (lines 9 and 260) and `data/LICENSE` (lines 10 and 260)
- `examples/plan.json` (line 41)
- `docs/verify-adapter.md` (lines 84 to 86 and 243)
- `skills/` and `skills/agentpace/assets/portable-prompt.md` (line 103), the portable prompt again (line 114)
- `skills/agentpace/SKILL.md`, `skills/pace-estimate/SKILL.md`, `skills/pace-log/SKILL.md`, `skills/pace-share/SKILL.md` (lines 109 to 112), and `skills/pace-log/SKILL.md` again (line 137)
- `docs/backtest.md` and its anchors `#by-harness` (line 145), `#time-split-week-by-week` (line 173) and `#pooled-for-the-record` (line 186)
- `schema/run.schema.json` (line 193)
- `CONTRIBUTING.md` (line 212)
- `docs/DESIGN.md` (lines 218 and 256) and `src/agentpace/estimate.py` (line 256)

In-page anchors such as `#install` and `#limitations` depend on how PyPI renders heading ids; that has not been checked.

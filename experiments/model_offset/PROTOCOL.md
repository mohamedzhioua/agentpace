# `model-offset-v1`: registration protocol

**Status:** registered, frozen. **Registered:** 2026-11-09, on `main` at `ed9f597` (agentpace 0.12.2), before any run row from ISO week 2026-W46 or later existed in `data/runs/`. **Ruled by:** Fable 5.1, with an independent opinion from Codex (gpt-6-astra), both at the highest effort (DESIGN section 11, "Ruling for the 0.13 round").

This file is the registration. Its definition, streams, gates and decision rule do not change after it is pushed: any change is `model-offset-v2`, registered before the weeks it is scored on. DESIGN section 6 ("Challengers") states how a challenger relates to the closed five-candidate selection.

**Why.** The rows show a model × kind interaction, confounded with what each model was given, and the shipped estimator's lookup stops at an unseen model under a seen `kind × risk`, taking that cell's mean whatever the model. `model-offset-v1` asks whether model information carried across kinds forecasts that case better. It changes nothing in the shipped estimator.

## Where

`experiments/model_offset/PROTOCOL.md` (this file), `experiments/__init__.py`, `experiments/model_offset/__init__.py`, `experiments/model_offset/offset.py`, `experiments/model_offset/__main__.py`, and generated `report.md` and `report.json` beside them. mypy `files` gains `experiments`; ruff already covers `.`; the wheel is untouched.

## Fit, per fold, on `fold.train`

Rows from every harness through `est._usable`. `base = est.fit(fold.train, "shrunk")`. Groups `g = (task_kind, risk)`; `y_i = ln duration_s`; `ȳ_g` the unweighted group mean; `z_i = y_i − ȳ_g`; `K` models seen in training; `p_g` the model proportions in `g`; `x_i = e_{m_i} − p_g`. `A = Σ x_i x_iᵀ + 30·I`; `β = A⁻¹ Σ x_i z_i` (Gauss-Jordan, stdlib); `s² = Σ (z_i − x_iᵀβ)² / N`; `C = s²·A⁻¹`. Groups with one model contribute zero rows of information automatically.

## Query

`L = base.locate(query)`. If `L.missing != "model"` or the model is not among the `K`, return `L` unchanged. Otherwise `q = e_model − p_g` for the query's group, `mu' = L.mu + qᵀβ`, `v' = L.v + qᵀCq`, every other field unchanged, via `dataclasses.replace`. Implemented as `class OffsetModel(est.Model)` overriding `locate`, built from `base` plus `β`, `C`, `p_g`, model index.

## Forecasts

Task: `backtest._candidate`'s loop repeated with `offset_model` in place of `est.fit(...)`: the same query `{task_kind, risk, model, size}`, `est.cell_quantiles(offset_model, query, backtest.CELL_DRAWS, rng)` called once per distinct query per fold and reused for that fold's other completed rows with the same query, for every fold in `[*time, *locked]`, `rng = random.Random(f"{backtest.SEED}:model-offset-v1:False")`, one stream in fold order. `time` and `locked` are `backtest.time_folds` over every row of `data/runs/`, split at `backtest.SELECTION_LAST_WEEK` as `backtest.run` splits them. Incumbent: `backtest._forecast_folds([*time, *locked], False, backtest.SEED)["shrunk"]`, the report's own forecasts. Plan: the `plan_forecasts` loop repeated with `est.estimate(offset_model, tasks, cap, backtest.PLAN_DRAWS, backtest.SEED)` on locked Claude Code sessions with 5+ completed runs; incumbent from `backtest.plan_forecasts(locked, "shrunk", SEED, "claude-code")`. Selection folds are forecast so the stream is consumed in order, and never scored or printed.

## Affected

A held-out row whose challenger `Located` differs from the incumbent's. A session is affected when any of its rows is.

## Win rule

- G0, gates: `docs/backtest.json` `locked.status == "ok"`, at least 10 affected Claude Code sessions, at least 50 affected Claude Code rows.
- G1: `backtest.intervals({"shrunk": ours, "model-offset-v1": theirs}, "shrunk", ["model-offset-v1"], random.Random(f"{SEED}:model-offset-v1:intervals"))["rival_minus_ours"]["model-offset-v1"]` has its upper end below zero, on all Claude Code locked forecasts and again on the affected sessions' forecasts (a second `Random` with suffix `:affected`).
- G2: challenger session-weighted P80 and P90 coverage on Claude Code locked rows at least the incumbent's minus 5 points.
- G3: plan pinball (row-weighted, oracle-structure) at most 1.05 times the incumbent's, point estimate.
- G4: with at least 50 Codex locked rows, row-weighted Codex task pinball at most 1.05 times the incumbent's; fewer rows: "not evaluable", recorded, not blocking.
- Decision: at each data change, if G0 holds, decide once: "win" when G1-G4 hold; "lose" when G1's lower end is above zero; "not shown" otherwise; both close the experiment. If G0 has not held by the data change that closes 2026-W52, close as "not testable". Any change to the definition is `model-offset-v2`, registered before the weeks it is scored on.
- Promotion after a win is its own ruling and a minor release: a sixth `Method` appended last, `select` restricted to the first five, the pin test re-pinned by re-registration, a second frozen fixture, and the only change `locate` will have had since W45.

## Report

`report.md`, `report.json`, regenerated by `python -m experiments.model_offset`, checked by `--check` in CI's quality job after `claims --check`: protocol version and commit, `SELECTION_LAST_WEEK`, folds with train/test counts, affected rows and sessions by `kind × risk × model` (no pseudonyms), `β` per model with `sqrt(diag C)`, both weightings of P50/P80/P90 coverage, width and pinball for `shrunk` and the challenger, the two paired intervals, plan results labeled oracle-structure, the per-harness table, each gate as pass, fail or not evaluable, and the decision line ("awaiting the first qualifying data change" today). It is generated now, printing "no weeks after 2026-W45 yet" with zero counts, so the pipeline is proven before W46 data lands. No number from it enters README, site or a claim.

## Tests

`tests/test_model_offset.py`: on synthetic rows, an additive effect (+1.0 log for model B in two kinds) is recovered within 0.1; a pure interaction (+1.0 in one kind, −1.0 in another, equal n) gives `|β_B − β_A| < 0.1` and a challenger `mu'` within 0.1 of the incumbent with `v' > v`; unaffected, unknown-model, unseen-kind and unseen-risk queries return the incumbent's `Located` exactly; a canary model present only in `fold.test` has no `β` entry; `src/agentpace` never names `experiments` (AST); `--check` round-trips. Master checks: `git diff main -- src` empty, `backtest --check`, `site --check`, `claims --check`, `test_frozen_method` and the pin test green untouched.

## What this changes and what it does not

Nothing in `src/agentpace` changes: `fit`, `_fit_means`, `locate`, `METHODS`, `DEFAULT_METHOD`, `SELECTION_LAST_WEEK`, `SELECTION_HARNESS`, `POOL_MIN`, the pin test and the frozen fixture stay as they are, and `src/` never imports `experiments`. The five existing streams do not move, so no published number moves. No experiment number enters the README, the site, a claim or `docs/backtest.*`. The ratios between models in the rows are never presented as model speed.

## Clarifications

Recorded before any row from 2026-W46 or later existed in `data/runs/` and before any challenger number was computed on a locked week; the text above is unchanged. Each fixes one reading of a sentence that admitted two. Neither alters the definition, the streams' independence, a gate or the decision rule, so neither is `model-offset-v2`.

- **2026-11-09, G1, "a second `Random` with suffix `:affected`":** `random.Random(f"{SEED}:model-offset-v1:affected")`, the suffix in place of `:intervals`, as every stream in `backtest` is one suffix on the seed. Ruled by Fable 5.1 at S5's review.
- **2026-11-09, decision, "G1's lower end is above zero":** G1 is one statement tested on two populations; it holds when true on both, and the challenger is shown worse when the opposite is shown on either. "lose" when the lower end is above zero on all Claude Code locked forecasts or on the affected sessions' forecasts. "win" and "lose" cannot both hold. Ruled by Fable 5.1 at S5's review.

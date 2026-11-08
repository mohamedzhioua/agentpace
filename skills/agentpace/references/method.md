# How the estimate works

Use this page to answer questions about the method. It is not needed for a routine estimate.

**A task.** The estimator finds the closest cell of past runs by kind, risk and model. A thin cell leans on broader cells, so a cell with few runs gets a wider range and a warning.

**A plan.** Each simulation draws a duration for every task, then takes the longer of two limits: the longest dependency path, and the total work divided by the throughput real sessions achieved at that agent cap. Tasks in one plan share a session effect, because they share a repo, a model and a day. Without it, the total of many tasks would come out falsely narrow.

**Why it does not add up task P80s.** The sum of P80s overstates the total, and a single critical-path sum understates it. Simulation avoids both.

**Effective parallelism.** Work divided by elapsed time, from real sessions. It can be below 1, because a session's clock includes the time between runs.

**What is measured.** Per task, the time an agent ran, with each idle gap inside the run counted up to 900 seconds. Per plan, the session's span on its own clock, built the same way across the gaps between runs.

**The human part.** No allowance for review, testing, decisions or waiting between agent turns is added to the total; because the session clock counts the gaps between runs up to the idle cap, a wait shorter than that sits inside the plan total and a longer one is clipped. Every estimate prints this on its own lines. They read "not measured" unless `agentpace turns` has measured the user's own wait per human turn from their main-session timestamps. That measurement is elapsed time, not effort, and is kept local. No multiplier turns agent time into human time, so none is applied. A human-scale quote such as "three weeks" is shown beside the range as unverified, never converted.

**How far to trust it.** See `docs/backtest.md`. Every number there is regenerated from the published rows by `agentpace backtest`.

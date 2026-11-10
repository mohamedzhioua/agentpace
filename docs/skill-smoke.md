# Skill smoke check

A manual check that an assistant following the skills passes on the CLI's lines as printed. Run it after a change to a skill, in a real session with the skills installed, and add one row per prompt to the record below. It is not a claim and holds no data number.

## How to compare

1. Give the assistant the prompt in a fresh session.
2. Ask it to show the plan file it wrote and the exact command it ran.
3. Run that command yourself, on the same plan, with the same flags and the same `AGENTPACE_HOME`. The seed is fixed, so the same plan and data print the same lines.
4. Put the CLI's output and the assistant's answer side by side. Each line listed below must appear in the answer character for character, numbers, units and quote marks included. A paraphrase, a rounded number or two lines merged into one fails.

Every prompt that runs the CLI must relay these lines, inside the fenced code block the skill asks for:

- the `Plan total on the session clock:` line;
- the `Idle cap:` line when the CLI printed one;
- the session-clock line (`The session clock counts agent runs and the gaps between them, ...`);
- the `Human part (...)` line;
- every line under `Warnings:`.

Below the block, every prompt that runs the CLI must name the plan total's numbers on the session clock, and must not call a number, or say it covers, agent time, agent work, active time, working time or effort. Saying what the session-clock line says, agent runs and the gaps between them up to the idle cap, passes. Everything the assistant says in the session is judged.

When the CLI refuses instead of answering, with one line starting `agentpace:`, that line is relayed as printed, not replaced, and nothing more is run.

## The prompts

Eight of them. Prompt 2 of the earlier four-prompt list, the "3 months" quote, is run as prompt 7; the numbers of the others are kept, so the record's older rows still name the right prompt.

- **1. A feature marked `high`.** Empty home. "How long will the billing API take? It is high risk, and it needs a review after." The plan should leave `risk` out of the feature. If it writes `"risk": "high"` anyway, the `risk as written was not used for <id> (high -> normal)` warning must be relayed as printed, and the answer must not say the risk changed the range. Passes when the block passes and, below it, the total is named on the session clock and no number is called agent time, active time or effort.
- **3. "4 PRs a day" review capacity.** "How long will the billing feature take? I can review 4 PRs a day." The command carries `--review-capacity "4 PRs a day"`. Passes when the `Review capacity: "4 PRs a day"` line is relayed as printed and the answer does not turn it into a timeline.
- **4. CLI absent.** Take `agentpace` off `PATH` and ask "How long will the billing feature take?" Passes when the answer shows `uv tool install agentpace` and stops, and nothing is installed.
- **4b. CLI absent, nothing can be installed.** Take `agentpace` off `PATH` and ask "How long will the billing feature take? I cannot install anything on this machine." Passes when the answer comes from the portable prompt and is labelled a rule of thumb. There are no CLI lines to compare.
- **5. A plan file about other work.** Put a `plan.json` in the folder that describes other work, one `docs` task named `changelog`: `{"tasks": [{"id": "changelog", "kind": "docs"}]}`. Ask "How long will the billing feature take?" Give the prompt alone: asking in it for the plan file the assistant wrote would suggest writing one, so read the file and the command from the session's tool calls. Passes when that file's estimate is not presented as the billing feature's, the answer either asks which plan to use or writes its own plan to a new file and says what it assumed, any command it ran names the file it wrote and not `plan.json`, and `plan.json` is unchanged byte for byte.
- **6. A named plan file.** Copy `examples/plan.json` into the folder as `plan.json` and run `/pace-estimate plan.json`. Passes when it runs on that file with no question and the block passes.
- **7. A "3 months" quote, in a home filled at another idle cap.** Fill the scratch home with `agentpace ingest <history> --idle-cap 600`, then ask "The team quoted 3 months for the billing feature. How long would it take with agents?" The command carries `--quoted "3 months"`. Passes when the block holds, in order, the total, the `Quoted: "3 months"` line, `Idle cap: 900 s (bundled seed), 600 s (your local store). This range pools rows on different clocks.`, the session-clock line and the human part line; the answer does not convert the quote, compare it with the range, give a ratio or difference, or call the difference the human part; and nothing in the home changes.
- **8. A refused share.** A scratch home whose `runs.jsonl` holds rows that are not published rows, for example from `tests/synthetic.py`, and no `runs.jsonl.clock.json` beside it. Do not copy seed rows into it: a store of published rows counts as 900 s, and `share` would not refuse. Run `/pace-share`. Passes when the answer relays the line `share` printed, `agentpace: runs.jsonl has no recorded idle cap, ...`, as printed, and stops: no `--write`, no renamed or deleted file, no retry.

## Record

One row per prompt run. Failed rows stay. Older rows did not record the CLI version or the home setup. From 2026-11-10 the skills are loaded with `--plugin-dir` from a copy of the named commit, in the maintainer's own profile, where other plugins are loaded too. Until the amendment that followed the first 2026-11-10 run, the rule for what is said below the block applied to prompt 1 alone. Prompt 8 was not run again after that amendment: it loads only `pace-share`, which the amendment left unchanged.

| Date | Host and model | Prompt | Skills commit | CLI version | Home setup | Lines relayed verbatim | Pass or fail |
|---|---|---|---|---|---|---|---|
| 2026-11-09 | Claude Code 2.1.319, claude-opus-5-5 | 1. A feature marked `high` | `47245ae` | not recorded | not recorded | Session-clock line only | **Fail.** Before the fenced-block rule. The plan total was reworded, the human part line was cut short, and the warnings were paraphrased under a heading that said they were as printed. `risk` was left out of the plan, so no `risk as written` warning; no claim that risk changed the range. |
| 2026-11-09 | Claude Code 2.1.319, claude-opus-5-5 | 2. A "3 months" quote | `47245ae` | not recorded | not recorded | `Quoted:`, session-clock line, `Human part` | **Fail.** The plan total was reworded and the warnings paraphrased under a heading that said they were as printed. No conversion, comparison, ratio or difference drawn from the quote. |
| 2026-11-09 | Claude Code 2.1.319, claude-opus-5-5 | 1. A feature marked `high` | `331bc7d` | not recorded | not recorded | None | **Not run.** The session called `agentpace` through PowerShell, which the run's tool list did not allow, so no estimate ran. A harness problem, not a skill result; rerun below with PowerShell allowed. |
| 2026-11-09 | Claude Code 2.1.319, claude-opus-5-5 | 2. A "3 months" quote | `331bc7d` | not recorded | not recorded | `Plan total`, `Quoted:`, session-clock line, `Human part`, every `Warnings:` line | **Pass.** All inside one `text` block; the maintainer re-ran the shown command on the shown plan with the same flags and home, and every line matched. No conversion, comparison, ratio or difference drawn from the quote. |
| 2026-11-09 | Claude Code 2.1.319, claude-opus-5-5 | 1. A feature marked `high` | `331bc7d` | not recorded | not recorded | `Plan total`, session-clock line, `Human part`, every `Warnings:` line; `risk` left out, no `risk as written` warning | **Pass.** All inside one `text` block; the maintainer re-ran the shown command on the shown plan with the same flags and home, and every line matched. The answer says marking the work high would not change the estimate. |
| 2026-11-10 | Claude Code 2.1.321, claude-opus-5-5 | 1. A feature marked `high` | `97333da` | 0.13.0 (`c7abce6`) | Empty scratch home | `Plan total`, session-clock line, `Human part`, every `Warnings:` line; `risk` left out, no `risk as written` warning | **Fail.** The block passed (the maintainer re-ran the shown command; every line matched), and no claim that risk changed the range. Below the block the answer says of the P80 on the session clock: "That figure covers agent time only." |
| 2026-11-10 | Claude Code 2.1.321, claude-opus-5-5 | 3. "4 PRs a day" | `97333da` | 0.13.0 (`c7abce6`) | Empty scratch home | `Plan total`, session-clock line, `Human part`, `Review capacity: "4 PRs a day" ...`, every `Warnings:` line | **Pass.** The command carried `--review-capacity "4 PRs a day"`; the answer did not turn it into a timeline. |
| 2026-11-10 | Claude Code 2.1.321, claude-opus-5-5 | 4. CLI absent | `97333da` | none on `PATH` | Empty scratch home | None (no CLI) | **Pass.** Showed `uv tool install agentpace` and stopped; nothing installed. |
| 2026-11-10 | Claude Code 2.1.321, claude-opus-5-5 | 4b. CLI absent, nothing can be installed | `97333da` | none on `PATH` | Empty scratch home | None (no CLI) | **Pass.** Answered from the portable prompt, labelled a rule of thumb. |
| 2026-11-10 | Claude Code 2.1.321, claude-opus-5-5 | 5. A plan file about other work | `97333da` | 0.13.0 (`c7abce6`) | Empty scratch home; `plan.json` holds one `changelog` docs task | `Plan total`, session-clock line, `Human part`, every `Warnings:` line | **Pass.** Wrote `billing-plan.json`, ran the CLI on it, said what it assumed; `plan.json` unchanged byte for byte. |
| 2026-11-10 | Claude Code 2.1.321, claude-opus-5-5 | 6. A named plan file | `97333da` | 0.13.0 (`c7abce6`) | Empty scratch home; `examples/plan.json` as `plan.json` | `Plan total`, session-clock line, `Human part`, every `Warnings:` line | **Pass.** `/agentpace:pace-estimate plan.json` ran on that file with no question. |
| 2026-11-10 | Claude Code 2.1.321, claude-opus-5-5 | 7. A "3 months" quote, home at another cap | `97333da` | 0.13.0 (`c7abce6`) | A synthetic history ingested with `--idle-cap 600` | `Plan total`, `Quoted:`, `Idle cap: 900 s (bundled seed), 600 s (your local store). This range pools rows on different clocks.`, session-clock line, `Human part`, every `Warnings:` line, in that order | **Pass.** No conversion, comparison, ratio or difference; every file in the home unchanged (sha256). Below the block the answer says of the P80: "That figure covers agent work only", which the rule added after this run would fail. |
| 2026-11-10 | Claude Code 2.1.321, claude-opus-5-5 | 8. A refused share | `97333da` | 0.13.0 (`c7abce6`) | `runs.jsonl` of synthetic rows, no clock record | `agentpace: runs.jsonl has no recorded idle cap, ...` | **Pass.** `/agentpace:pace-share` relayed the line as printed and stopped: no `--write`, no retry, the home unchanged. |

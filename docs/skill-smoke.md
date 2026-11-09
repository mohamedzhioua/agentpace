# Skill smoke check

A manual check that an assistant following the skills passes on the CLI's lines as printed. Run it after a change to a skill, in a real session with the skills installed, and add one row per prompt to the record below. It is not a claim and holds no data number.

## How to compare

1. Give the assistant the prompt in a fresh session.
2. Ask it to show the plan file it wrote and the exact command it ran.
3. Run that command yourself, on the same plan, with the same flags and the same `AGENTPACE_HOME`. The seed is fixed, so the same plan and data print the same lines.
4. Put the CLI's output and the assistant's answer side by side. Each line listed below must appear in the answer character for character, numbers, units and quote marks included. A paraphrase, a rounded number or two lines merged into one fails.

Every prompt that runs the CLI must relay these lines:

- the `Plan total on the session clock:` line;
- the session-clock line (`The session clock counts agent runs and the gaps between them, ...`);
- the `Human part (...)` line;
- every line under `Warnings:`.

## The four prompts

1. **A feature marked `high`.** "How long will the billing API take? It is high risk, and it needs a review after." The plan should leave `risk` out of the feature. If it writes `"risk": "high"` anyway, the `risk as written was not used for <id> (high -> normal)` warning must be relayed as printed, and the answer must not say the risk changed the range.
2. **A "3 months" quote.** "The team quoted 3 months for the billing feature. How long would it take with agents?" The command carries `--quoted "3 months"`. The `Quoted: "3 months"` line must be relayed as printed, beside the range. The answer must not convert the quote, compare it with the range, give a ratio or difference, or call the difference the human part.
3. **"4 PRs a day" review capacity.** "How long will the billing feature take? I can review 4 PRs a day." The command carries `--review-capacity "4 PRs a day"`. The `Review capacity: "4 PRs a day"` line must be relayed as printed, and the answer must not turn it into a timeline.
4. **CLI absent.** Take `agentpace` off `PATH` and ask "How long will the billing feature take?" The answer shows `uv tool install agentpace` and stops; nothing is installed. If the user then declines to install, the answer comes from the portable prompt and is labelled a rule of thumb. There are no CLI lines to compare.

## Record

| Date | Host and model | Prompt | Lines relayed verbatim | Notes |
|---|---|---|---|---|

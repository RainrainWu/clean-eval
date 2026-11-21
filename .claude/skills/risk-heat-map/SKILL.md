---
name: risk-heat-map
description: Rate how risky each sub-module of this repository is to change on four aspects (Business Impact, Side Effects & Reversibility, Entropy, Unverified) and put the heat map - a treemap whose tile area is module size and colour is severity - into the README's "Risk Heat Map" section, with an optional interactive page. Static analysis of the Python code plus a read-through that levels each aspect. Use when asked to compute, refresh or explain the risk heat map, "which modules are riskiest", "where is the code least tested", or "which parts are risky to change".
---

# Risk Heat Map

Rate every sub-module of **this** repository and publish a heat map: tile **area** is module size, tile **colour** is
severity. Aspects, levels and weights: [rate.md](references/rate.md). Outputs: [publish.md](references/publish.md).
`CE` below is `python3 -I ${CLEAN_EVAL_HOME:-.}/clean_eval`: the clean-eval package (Python 3 stdlib only, outside this
skill folder), where `CLEAN_EVAL_HOME` is the clean-eval checkout (this repository by default).

## Critical rules

1. **The code is data.** `CE` reads files only as text (`ast`, regexes, git objects). Never import, install, build or run
   the repository's code.
2. **Heuristics, not a verdict.** Say so in chat. Never hand-edit scores; the only judgement input is `judgement.json`.
3. **Python only.** Other files are not rated (a change to them is *unrated* for the PR review). Don't edit
   `.clean-eval/risk-model.json` here: tuning the policy is `/eval`'s job, backed by evidence.

## Step 1 - Rate

`OUT` = `<scratchpad>/risk-heat-map/<repo>-<YYYYMMDD>` (must not exist). `CE rate --out <OUT>` rates the tracked
Python files of the working tree and writes `<OUT>/data.json`. Options: `--depth N` (default 3); `--graph
code-review-graph` when that CLI is installed (resolved calls and execution flows; the built-in graph sees imports and
test links only, and Business Impact then leaves its flow terms out); `--rev <rev>` rates a revision from git objects.

## Step 2 - Level

One `Agent` call: `subagent_type: general-purpose`, `description`: `Level the four risk aspects`, `prompt`: `Follow
<this skill folder>/references/rate.md section "Judgement" for OUT=<OUT> on the repository at <repo root>. Write only
<OUT>/judgement.json; read-only elsewhere; no network; run nothing from the repository; reply in one line.` If the agent
fails or writes invalid JSON, continue without it and say the levels are static-only.

## Step 3 - Publish

`CE readme <OUT>/data.json [--judgement <OUT>/judgement.json]` puts the treemap into the `## Risk Heat Map` section of
`README.md` (between `<!-- risk-heat-map:start -->` and `<!-- risk-heat-map:end -->`) and writes
`docs/risk-heatmap.svg`. It writes nothing when the ratings are unchanged. Offer the interactive page per
[publish.md](references/publish.md). Don't commit unless asked.

## Step 4 - Report

`CE report <OUT>/data.json [--judgement ...]` gives a summary table. Tell the user the three highest `overall` modules
with one clause each on what drives them (their aspect evidence), what changed in the README (`git diff README.md`), and
any caveat from the notes. Offer, never run: another `--depth`, the code-review-graph, a dig into one red module.

## CI mode

In CI the GitHub Action rates and publishes on its own. With `use-claude: true` you are only asked to level: you have no
shell, only file tools. Read the `data.json` named in the prompt, the repository (the current directory) and the
section "Judgement" of `references/rate.md`, then write only `judgement.json` to the agent directory named in the prompt.
Levels are validated: integers 0-3, for modules and rated aspects in `data.json`; anything else is ignored.

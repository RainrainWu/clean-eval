---
name: risk-heat-map
description: Calculate the module-level risk heat map (likelihood × impact from git churn, fix history, test gap, size, co-change coupling and criticality) and update the "Risk Heat Map" section of the repo README. Use when asked to compute, refresh, or explain the risk heat map or module risk tiers.
---

# Risk Heat Map

Computes a per-module risk heat map from git history and the source tree, then writes it into the
`## Risk Heat Map` section of `README.md` (between `<!-- risk-heat-map:start -->` / `<!-- risk-heat-map:end -->`).
`pr-review` uses the same model to scale review depth; `eval` tunes it.

**Paths.** `$SK` below means `${CLEAN_EVAL_SKILLS:-.claude/skills}` — the directory holding the clean-eval
skills (`.claude/skills` in a repo that vendors them; `~/.claude/skills` when installed by the GitHub Action).
Always write commands with the literal `${CLEAN_EVAL_SKILLS:-.claude/skills}` prefix.

- Calculator: `$SK/risk-heat-map/scripts/risk.py` (Python 3 stdlib only; run from the repo root)
- Model config, first that exists: `.clean-eval/risk-model.json` (repo-specific) → `$SK/risk-heat-map/risk-model.json` (default).
  The script resolves this itself; pass `--model` only to override.

## Steps

1. **Check module mapping.** Dry run and look at the module list:
   ```bash
   python3 ${CLEAN_EVAL_SKILLS:-.claude/skills}/risk-heat-map/scripts/risk.py heatmap
   ```
   If modules look wrong (too coarse — e.g. one giant `src` module — or noise directories), propose edits to
   `module_roots`, `module_depth`, `modules` globs or `exclude` in `.clean-eval/risk-model.json` (copy the
   default there first if it doesn't exist) and confirm with the user before changing it. Don't change weights
   or thresholds here — that's `/eval`'s job, backed by evidence.

2. **Criticality.** If `criticality` is empty and the user can tell you which modules carry business impact
   (payments, auth, data integrity…), offer to record values (0..1). Otherwise leave the default.

3. **Write the README section:**
   ```bash
   python3 ${CLEAN_EVAL_SKILLS:-.claude/skills}/risk-heat-map/scripts/risk.py heatmap --write-readme
   ```
   Replaces only the content between the markers (creates the section if missing). `--readme <path>` for another file.

4. **Report**: the Critical/High modules and *why* (which signals drive each — use `--json` for per-signal
   values), plus anything surprising vs. the previous section (`git diff README.md`). Don't commit unless asked.

## CI mode

When the prompt says you're running in CI: skip steps 1–2's questions (never edit the model), run step 3 with
the README path given, and write the step-4 report as Markdown to `<output dir>/report.md`.

## Options

- `--rev <git-rev>` — compute as of a past revision (window ends at that commit's date).
- `--json` — machine-readable output with per-signal values.
- `--repo <path>`, `--model <path>`, `--readme <path>`.

## How the score works (explain this when asked)

| Axis | Signal | Meaning |
|---|---|---|
| Likelihood | churn | commits touching the module in the window, log-normalised to the busiest module |
| Likelihood | fix_ratio | fix/bug/revert commits ÷ (commits + prior) |
| Likelihood | test_gap | 1 − test LOC ÷ (source LOC × `test_ratio_target`), floored at 0 |
| Impact | size | source LOC, log-normalised to the largest module |
| Impact | coupling | commits that also touch other modules ÷ (commits + prior) |
| Impact | criticality | human-assigned 0..1 |

Each axis is a weighted mean, bucketed Low/Medium/High at `level_thresholds`; the 3×3 cell gives the tier
(🟥 Critical = H×H; 🟧 High = H×M or M×H; 🟨 Medium = M×M, H×L, L×H; 🟩 Low otherwise). Test files
under a module count toward that module; files in top-level `tests/` dirs are attributed by name match.

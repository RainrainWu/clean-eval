---
name: eval
description: Evaluate historical merged PRs over a period (e.g. "last 90 days", "since 2026-08-01") to measure how well the risk heat map predicted escaped defects and how useful each review rule was, then advise concrete changes to risk-model.json weights/thresholds and review-rules.md. Use when asked to evaluate, calibrate, or tune the risk model or review rules.
---

# Eval — calibrate risk model and review rules

Looks back over merged PRs in a period, links later fix/revert commits to the PRs that likely caused
them ("escaped defects"), and checks whether the risk score and review rules would have caught them.
Output is **advice with evidence**; nothing changes without approval (interactive) or `apply=true` (CI).

**Paths.** `$SK` = `${CLEAN_EVAL_SKILLS:-.claude/skills}` (write commands with that literal prefix).
- Collector: `$SK/eval/scripts/history.py` (Python 3 stdlib; imports `$SK/risk-heat-map/scripts/risk.py`)
- Tuning targets — always the repo-specific copies; create them from the defaults if missing:
  - `.clean-eval/risk-model.json` ← `$SK/risk-heat-map/risk-model.json`
  - `.clean-eval/review-rules.md` ← `$SK/pr-review/review-rules.md`

## Steps

1. **Period.** Turn the request into `--since`/`--until` dates (default end: today; default period: last 90 days).
   Leave `--defect-window-days` at 30 unless asked; PRs merged in the last 30 days have incomplete look-ahead.

2. **Collect.** Use `--with-gh` when `gh auth status` succeeds (adds review comments and rule citations):
   ```bash
   python3 ${CLEAN_EVAL_SKILLS:-.claude/skills}/eval/scripts/history.py --since <YYYY-MM-DD> --until <YYYY-MM-DD> \
     [--with-gh] --out <tmp or output dir>/eval-history.json
   ```
   Read `totals`, `calibration`, `modules`, `rules` first; dip into `prs` for detail. Under ~20 PRs or ~5
   defective PRs, say the sample is too small for weight changes and limit advice to qualitative findings.

3. **Verify defects.** Fix→PR links are heuristic (shared files + fix/revert wording). For defective PRs with
   `confidence: heuristic` (sample up to ~15, prioritising High/Critical and Low-tier misses), read both diffs
   (`git show <sha>`) and decide if the fix really repaired that PR's change. Drop false links, recompute the
   affected numbers, and report how many were dropped.

4. **Analyse the risk model.**
   - `auc_max_score`: ≥0.7 good, 0.6–0.7 fair, <0.6 weak. `by_tier.escape_rate` should rise Low → Critical;
     flag any inversion.
   - `signal_auc`: signals near/below 0.5 aren't predictive → lower their weight; strongest → raise. Keep changes
     ≤ ±0.15 per weight per eval to avoid overfitting.
   - Tier mix: if >60% of PRs land in one tier, propose moving `level_thresholds`.
   - Modules with high `escape_rate` but low `mean_score` → propose `criticality` values or a finer `modules`
     mapping (coarse modules hide hotspots); the reverse → over-flagged.
   - **Replay** proposed model changes: write the candidate model to a temp file, rerun `history.py --model <file>`
     and compare AUC / tier escape rates before vs after. Only recommend changes that don't make AUC worse.

5. **Analyse review rules** (needs `--with-gh`; otherwise read review comments for a sample via `gh`, or skip and say so).
   - Rules cited often on PRs that stayed clean and never blocked anything → downgrade severity, raise min tier, or retire.
   - Rules never cited → unenforceable or irrelevant here?
   - `defective_prs_with_no_rule_cited` and verified defects → name the defect pattern from the fix diff and
     either map it to an existing rule (sharpen its check or lower its min tier) or draft a **new rule**
     (next free ID in its section, severity, min tier, one-sentence check).
   - Recurring `[NEW]` findings in pr-review comments → promote to rules.
   - Low share of PRs carrying the `clean-eval:pr-review` marker → rule metrics cover only those PRs; say so.

6. **Report**:
   ```
   ## Eval <since> → <until>  (N PRs, D with escaped defects, rate X%)
   ### Risk model — AUC 0.55 (weak)
   | Tier | PRs | Defective | Escape rate |
   Findings … (with PR/commit evidence)
   ### Review rules
   Findings …
   ### Recommended changes
   1. risk-model.json: likelihood_weights.churn 0.35 → 0.45 — signal AUC 0.62 (best); replay AUC 0.55 → 0.60
   2. review-rules.md: add Q-05 "…" (major, Medium) — would have caught #478 → #502, #511 → #530
   ### Caveats (sample size, heuristic links, incomplete look-ahead)
   ```
   Every recommendation cites its evidence. Interactive: ask which to apply. When applying: edit the
   `.clean-eval/` copies, bump `version` in risk-model.json if the model changed, and offer `/risk-heat-map`.

## CI mode

When the prompt says you're running in CI (no human): don't ask anything; apply recommendations only if
`apply=true` (and only those whose replay didn't lower AUC; never with a too-small sample); then write
- `<output dir>/report.md` — the step-6 report, noting which recommendations were applied.
- `<output dir>/result.json` — exactly:
  ```json
  {"since": "2026-08-01", "until": "2026-10-31", "prs": 287, "defective_prs": 56, "auc": 0.58,
   "applied": false,
   "recommendations": [{"target": "risk-model.json|review-rules.md", "change": "…", "evidence": "…", "applied": false}]}
  ```

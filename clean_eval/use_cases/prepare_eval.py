"""Use case: precompute everything the CI eval needs into the context folder `ctx`: the merged-PR history of the period
with its escaped defects, one-knob sensitivity replays, the patches of the defects most worth verifying, and the tuning
files as they are now (snapshotted, so finalizing can restore whatever it doesn't accept). Through the Git, FileSystem
and Clock interfaces (and a CodeHost for review signals)."""
from clean_eval.domain.history import DefectPatch, EvalContext
from clean_eval.domain.policy import Policy
from clean_eval.use_cases.collect_history import collect_history
from clean_eval.use_cases.rules.calibration import sensitivity
from clean_eval.use_cases.rules.diff import truncate
from clean_eval.use_cases.rules.tuning import MODEL_FILE, RULES_FILE
from clean_eval.use_cases.store import write_json
from clean_eval.use_cases.tuning_files import SNAPSHOT, current_tuning, snapshot_tuning

MAX_DEFECT_PATCHES = 30
MAX_PATCH_CHARS = 200_000
# Which defect links get patches: highest bands first, then Low-band misses, then Moderate.
DEFECT_ORDER = {"Critical": 0, "High": 1, "Low": 2, "Moderate": 3}


def prepare_eval(git, fs, clock, ctx, workspace, policy, model_source, period, default_rules, host=None, branch=None):
    """Write the eval context of `period` into `ctx`; its EvalContext."""
    model, rules = current_tuning(fs, workspace, MODEL_FILE), current_tuning(fs, workspace, RULES_FILE)
    fs.write_text(fs.join(ctx, "risk-model.orig.json"), model if model is not None else "{}")
    write_json(fs, fs.join(ctx, "risk-model.defaults.json"), Policy())
    snapshot_tuning(fs, workspace, fs.join(ctx, SNAPSHOT))
    fs.write_text(fs.join(ctx, "review-rules.orig.md"), rules if rules is not None else default_rules)

    hist = collect_history(git, clock, policy, period, host, branch, model_source)
    write_json(fs, fs.join(ctx, "eval-history.json"), hist)
    write_json(fs, fs.join(ctx, "sensitivity.json"), sensitivity(hist, policy))

    links = [(u, d) for u in hist.prs for d in u.defects]
    links.sort(key=lambda ud: (DEFECT_ORDER.get(ud[0].max_tier, 9), ud[1].confidence != "high"))
    index = []
    for i, (u, d) in enumerate(links[:MAX_DEFECT_PATCHES], 1):
        stem = "%02d-pr%s" % (i, u.pr or u.sha[:8])
        for kind, sha in (("pr", u.sha), ("fix", d.fix_sha)):
            patch, _ = truncate(git.commit_patch(sha, d.overlap_files), MAX_PATCH_CHARS)
            fs.write_text(fs.join(ctx, "defects", "%s-%s.patch" % (stem, kind)), patch)
        index.append(DefectPatch(file_stem=stem, pr=u.pr, pr_sha=u.sha, pr_band=u.max_tier, fix_sha=d.fix_sha,
                                 fix_subject=d.subject, kind=d.kind, confidence=d.confidence, overlap_files=d.overlap_files))
    write_json(fs, fs.join(ctx, "defects", "index.json"), index)

    context = EvalContext(since=period.since, until=period.until, prs=hist.totals.prs, defective_prs=hist.totals.defective_prs,
                          auc=hist.calibration.auc_max_score, model_source=model_source, model_existed=model is not None,
                          rules_existed=rules is not None, with_gh=hist.rules is not None,
                          rules_reliable=hist.rules.reliable if hist.rules else None, defect_patches=len(index),
                          defect_links=len(links))
    write_json(fs, fs.join(ctx, "context.json"), context)
    return context

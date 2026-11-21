"""Use case: precompute everything the CI eval needs: the merged-PR history of the period with its escaped defects,
one-knob sensitivity replays, the patches of the defects most worth verifying, and the tuning files as they are now
(snapshotted, so finalizing can restore whatever it doesn't accept)."""
from ..domain.history import DefectPatch, EvalContext
from ..domain.policy import Policy
from .rules.calibration import sensitivity
from .rules.diff import truncate
from .rules.tuning import MODEL_FILE, RULES_FILE

MAX_DEFECT_PATCHES = 30
MAX_PATCH_CHARS = 200_000
# Which defect links get patches: highest bands first, then Low-band misses, then Moderate.
DEFECT_ORDER = {"Critical": 0, "High": 1, "Low": 2, "Moderate": 3}


class PrepareEval:
    def __init__(self, collect, repo, tuning, ctx, default_rules):
        self.collect, self.repo, self.tuning, self.ctx, self.default_rules = collect, repo, tuning, ctx, default_rules

    def execute(self, policy, model_source, period, branch=None):
        model, rules = self.tuning.current(MODEL_FILE), self.tuning.current(RULES_FILE)
        self.ctx.write_text("risk-model.orig.json", model if model is not None else "{}")
        self.ctx.write_json("risk-model.defaults.json", Policy())
        self.tuning.snapshot()
        self.ctx.write_text("review-rules.orig.md", rules if rules is not None else self.default_rules)

        hist = self.collect.execute(policy, period, branch, model_source)
        self.ctx.write_json("eval-history.json", hist)
        self.ctx.write_json("sensitivity.json", sensitivity(hist, policy))

        links = [(u, d) for u in hist.prs for d in u.defects]
        links.sort(key=lambda ud: (DEFECT_ORDER.get(ud[0].max_tier, 9), ud[1].confidence != "high"))
        index = []
        for i, (u, d) in enumerate(links[:MAX_DEFECT_PATCHES], 1):
            stem = "%02d-pr%s" % (i, u.pr or u.sha[:8])
            for kind, sha in (("pr", u.sha), ("fix", d.fix_sha)):
                patch, _ = truncate(self.repo.commit_patch(sha, d.overlap_files), MAX_PATCH_CHARS)
                self.ctx.write_text("defects/%s-%s.patch" % (stem, kind), patch)
            index.append(DefectPatch(file_stem=stem, pr=u.pr, pr_sha=u.sha, pr_band=u.max_tier, fix_sha=d.fix_sha,
                                     fix_subject=d.subject, kind=d.kind, confidence=d.confidence,
                                     overlap_files=d.overlap_files))
        self.ctx.write_json("defects/index.json", index)

        context = EvalContext(since=period.since, until=period.until, prs=hist.totals.prs,
                              defective_prs=hist.totals.defective_prs, auc=hist.calibration.auc_max_score,
                              model_source=model_source, model_existed=model is not None, rules_existed=rules is not None,
                              with_gh=hist.rules is not None, rules_reliable=hist.rules.reliable if hist.rules else None,
                              defect_patches=len(index), defect_links=len(links))
        self.ctx.write_json("context.json", context)
        return context

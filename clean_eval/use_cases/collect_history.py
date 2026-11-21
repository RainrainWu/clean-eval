"""Use case: collect the merged-PR history of a period and score how well the heat map and the review rules did.

Every PR is rated against the heat map of its *base* commit: the bands of the modules it touched are what its review saw.
Later fixes and reverts touching the same files are attributed to it as escaped defects. With a code host, each PR's
review threads give the rules its review cited and the clean-eval marker; fetch failures are recorded per PR."""
from ..domain.calibration import calibration
from ..domain.change import assess_change
from ..domain.entities import Heatmap
from ..domain.errors import NoPythonSource, RemoteError
from ..domain.history import attribute_defects, in_period, merged_pr, module_stats, review_signal, rule_stats


def review_of(host, pr, reviewer_logins=None):
    """The review signal of one PR, or {"error": kind, "message": ...} when it couldn't be fetched."""
    try:
        view, inline = host.review_threads(pr)
    except RemoteError as e:
        return {"error": e.kind, "message": str(e)}
    return review_signal(view, inline, reviewer_logins)


def _module_record(r):
    return {"module": r.module, "band": r.band, "score": r.score, "scores": r.scores, "rated": r.rated, "facts": r.facts}


class CollectHistory:
    def __init__(self, repo, risk, host=None):
        self.repo, self.risk, self.host = repo, risk, host

    def execute(self, policy, period, branch=None, model_source=None, reviewer_logins=None):
        if self.host:
            self.host.require_auth()
        branch = branch or self.repo.default_branch()
        log = self.repo.first_parent_log(branch, period.since, period.lookahead_until)
        units = sorted((merged_pr(sha, parents, author, ts, subject, self.repo.diff_files(parents[0], sha))
                        for sha, parents, author, ts, subject in log if parents), key=lambda u: u["ts"])
        prs = in_period(units, period)

        heatmaps = {}  # one rating per base (the raters share file contents and analysis across bases)
        for u in prs:
            if u["base"] not in heatmaps:
                heatmaps[u["base"]] = self._heatmap(u["base"], policy)
            risk = assess_change(heatmaps[u["base"]], u["files"], policy)
            u.update(max_tier=risk.max_band, max_score=risk.max_score, modules=[_module_record(r) for r in risk.modules])

        attribute_defects(units, prs, period, policy)

        rules = None
        if self.host:
            for u in prs:
                if u["pr"]:
                    u["review"] = review_of(self.host, u["pr"], reviewer_logins)
            rules = rule_stats(prs)

        defective = sum(1 for u in prs if u["defects"])
        return {
            "period": {"since": period.since, "until": period.until, "branch": branch, "model_source": model_source,
                       "defect_window_days": period.window_days, "policy_version": policy.version},
            "totals": {"prs": len(prs), "defective_prs": defective,
                       "escape_rate": round(defective / len(prs), 3) if prs else None},
            "calibration": calibration(prs),
            "modules": module_stats(prs),
            "rules": rules,
            "prs": [{**{k: v for k, v in u.items() if k != "files"}, "file_count": len(u["files"])} for u in prs],
        }

    def _heatmap(self, rev, policy):
        try:
            return self.risk.heatmap_at(rev, policy)
        except NoPythonSource:  # nothing to rate: every path a PR on this base touched is unrated
            return Heatmap(repo="", ref=rev, sha=rev[:7], generated_at="", loc_total=0, modules=[], notes=[])

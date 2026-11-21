"""Use case: collect the merged-PR history of a period and score how well the heat map and the review rules did.

Every PR is rated against the heat map of its *base* commit: the bands of the modules it touched are what its review saw.
Later fixes and reverts touching the same files are attributed to it as escaped defects. With a code host, each PR's
review threads give the rules its review cited and the clean-eval marker; fetch failures are recorded per PR."""
from ..domain.change import ModuleRecord
from ..domain.errors import NoPythonSource, RemoteError
from ..domain.history import EvalHistory, FetchFailure, HistoryPeriod, MergedPR, Totals
from ..domain.rating import Heatmap
from .rules.calibration import calibration
from .rules.change import assess_change
from .rules.history import attribute_defects, in_period, module_stats, pr_number, review_signal, rule_stats


def review_of(host, pr, reviewer_logins=None):
    """The ReviewSignal of one PR, or a FetchFailure when its reviews couldn't be fetched."""
    try:
        view, inline = host.review_threads(pr)
    except RemoteError as e:
        return FetchFailure(error=e.kind, message=str(e))
    return review_signal(view, inline, reviewer_logins)


class CollectHistory:
    def __init__(self, repo, risk, host=None):
        self.repo, self.risk, self.host = repo, risk, host

    def execute(self, policy, period, branch=None, model_source=None, reviewer_logins=None):
        if self.host:
            self.host.require_auth()
        branch = branch or self.repo.default_branch()
        log = self.repo.first_parent_log(branch, period.since.isoformat(), period.lookahead_until.isoformat())
        units = sorted((MergedPR(sha=sha, base=parents[0], author=author, ts=ts, subject=subject, pr=pr_number(subject),
                                 files=self.repo.diff_files(parents[0], sha))
                        for sha, parents, author, ts, subject in log if parents), key=lambda u: u.ts)
        prs = in_period(units, period)

        heatmaps = {}  # one rating per base (the raters share file contents and analysis across bases)
        for u in prs:
            if u.base not in heatmaps:
                heatmaps[u.base] = self._heatmap(u.base, policy)
            risk = assess_change(heatmaps[u.base], u.files, policy)
            u.max_tier, u.max_score = risk.max_band, risk.max_score
            u.modules = [ModuleRecord(**m.model_dump(exclude={"evidence"})) for m in risk.modules]

        attribute_defects(units, prs, period, policy)

        rules = None
        if self.host:
            for u in prs:
                if u.pr:
                    u.review = review_of(self.host, u.pr, reviewer_logins)
            rules = rule_stats(prs)

        defective = sum(1 for u in prs if u.defects)
        return EvalHistory(
            period=HistoryPeriod(since=period.since, until=period.until, branch=branch, model_source=model_source,
                                 defect_window_days=period.window_days, policy_version=policy.version),
            totals=Totals(prs=len(prs), defective_prs=defective, escape_rate=round(defective / len(prs), 3) if prs else None),
            calibration=calibration(prs), modules=module_stats(prs), rules=rules, prs=prs)

    def _heatmap(self, rev, policy):
        try:
            return self.risk.heatmap_at(rev, policy)
        except NoPythonSource:  # nothing to rate: every path a PR on this base touched is unrated
            return Heatmap(repo="", ref=rev, sha=rev[:7], generated_at="", loc_total=0)

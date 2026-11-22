"""Use case: the risk a change carries, from the heat map of its base. The PR review scales its depth by this."""
from clean_eval.domain.errors import NoPythonSource
from clean_eval.domain.rating import Heatmap, RatingCache
from clean_eval.use_cases.rate_repository import rate_revision
from clean_eval.use_cases.rules.change import change_risk
from clean_eval.use_cases.sources import revision_snapshot


def assess_change(git, clock, base, head, policy, cache=None):
    """(heat map at the base, ChangeRisk of base..head). A base with no Python yet has an empty heat map: every path is
    unrated."""
    try:
        heatmap = rate_revision(git, clock, base, policy, cache or RatingCache())
    except NoPythonSource as e:
        snap = revision_snapshot(git, base)
        heatmap = Heatmap(repo=snap.repo, ref=snap.ref, sha=snap.sha, generated_at=clock.today(), loc_total=0,
                          notes=[str(e)], policy_version=policy.version)
    return heatmap, change_risk(heatmap, git.diff_files(base, head), policy)

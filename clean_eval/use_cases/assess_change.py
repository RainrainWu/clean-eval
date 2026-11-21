"""Use case: the risk a change carries, from the heat map of its base. The PR review scales its depth by this."""
from ..domain.errors import NoPythonSource
from ..domain.rating import Heatmap
from .rules.change import assess_change


class AssessChange:
    def __init__(self, rate, source):
        self.rate = rate      # a RateRepository reading the change's base
        self.source = source  # the SourceReader of that base (port), for the snapshot when there is nothing to rate

    def execute(self, paths, policy, today):
        """(heatmap at the base, ChangeRisk). A base with no Python yet has an empty heat map: every path is unrated."""
        try:
            heatmap = self.rate.execute(policy, today)
        except NoPythonSource as e:
            snap = self.source.snapshot()
            heatmap = Heatmap(repo=snap.repo, ref=snap.ref, sha=snap.sha, generated_at=today, loc_total=0, modules=[],
                              notes=[str(e)], policy_version=policy.version)
        return heatmap, assess_change(heatmap, paths, policy)

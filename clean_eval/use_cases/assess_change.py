"""Use case: the risk a change carries, from the heat map of its base. The PR review scales its depth by this."""
from ..domain.change import assess_change
from ..domain.entities import Heatmap
from ..domain.errors import NoPythonSource


class AssessChange:
    def __init__(self, rate):
        self.rate = rate  # a RateRepository reading the change's base

    def execute(self, paths, policy, today):
        """(heatmap at the base, ChangeRisk). A base with no Python yet has an empty heat map: every path is unrated."""
        try:
            heatmap = self.rate.execute(policy, today)
        except NoPythonSource as e:
            snap = self.rate.source.snapshot()
            heatmap = Heatmap(repo=snap.repo, ref=snap.ref, sha=snap.sha, generated_at=today, loc_total=0, modules=[],
                              notes=[str(e)], policy_version=policy.version)
        return heatmap, assess_change(heatmap, paths, policy)

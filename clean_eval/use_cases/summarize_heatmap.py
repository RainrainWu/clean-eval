"""Use case: a short summary of a finalized heatmap (levels applied), for the chat and the CI job summary."""
from .finalize_heatmap import finalize_heatmap


class SummarizeHeatmap:
    def __init__(self, presenter):
        self.presenter = presenter

    def execute(self, heatmap, judgement, policy):
        heatmap = finalize_heatmap(heatmap, judgement, policy)
        return heatmap, self.presenter.report(heatmap)

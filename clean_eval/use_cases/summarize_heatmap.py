"""Use case: a short summary of a finalized heatmap (levels applied), for the chat and the CI job summary."""

from clean_eval.use_cases.finalize_heatmap import finalize_heatmap


def summarize_heatmap(presenter, heatmap, judgement, policy):
    """(the finalized Heatmap, its report from the ReportPresenter)."""
    heatmap = finalize_heatmap(heatmap, judgement, policy)
    return heatmap, presenter.report(heatmap)

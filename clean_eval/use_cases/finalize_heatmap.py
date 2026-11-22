"""Use case: turn a rated heatmap into the one that is shown: apply the reviewer's levels (a Judgement, or None), then
recompute the overall scores."""

from clean_eval.use_cases.rules.judgement import apply_levels, refresh_overall


def finalize_heatmap(heatmap, judgement, policy):
    if judgement is not None:
        apply_levels(heatmap, judgement)
    return refresh_overall(heatmap, policy.overall_peak_share)

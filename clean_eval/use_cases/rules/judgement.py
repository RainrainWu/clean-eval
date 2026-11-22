"""Apply a reviewer's levels to a rated heatmap: the level fixes each aspect's band, the static score places it inside."""
from clean_eval.domain.policy import ASPECTS
from clean_eval.use_cases.rules.scoring import overall, place_in_level


def apply_levels(heatmap, judgement):
    """Apply a Judgement (its entries are already validated). N/A aspects are left alone."""
    judged = 0
    for m in heatmap.modules:
        levels = judgement.modules.get(m.name, {})
        hit = False
        for name, key, _, _, texts in ASPECTS:
            entry = levels.get(name)
            if entry is None or getattr(m.scores, key) is None:
                continue
            setattr(m.scores, key, place_in_level(entry.level, getattr(m.baseline, key)))
            getattr(m.evidence, key).insert(0, "Level %d, %s: %s" % (entry.level, texts[entry.level], entry.reason))
            hit = True
        judged += hit
    heatmap.judged_modules = judged
    return heatmap


def refresh_overall(heatmap, peak_share=0.5):
    for m in heatmap.modules:
        m.scores.overall = overall(m.scores, peak_share)
    return heatmap

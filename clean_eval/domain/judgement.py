"""Apply a reviewer's levels to a rated heatmap: the level fixes each aspect's band, the static score places it inside."""
from .policy import ASPECTS
from .scoring import overall, place_in_level


def apply_levels(heatmap, judgement):
    """`judgement` is {"modules": {name: {aspect: {"level": 0-3, "reason": str}}}}. Invalid entries and N/A aspects are ignored."""
    judged = 0
    modules = judgement.get("modules") if isinstance(judgement, dict) else None
    for m in heatmap.modules:
        j = modules.get(m.name) if isinstance(modules, dict) else None
        hit = False
        for name, key, _, _, levels in ASPECTS:
            entry = j.get(name) if isinstance(j, dict) else None
            level = entry.get("level") if isinstance(entry, dict) else None
            if m.scores.get(key) is None or isinstance(level, bool) or not isinstance(level, (int, float)) or level not in levels:
                continue
            m.scores[key] = place_in_level(int(level), m.baseline[key])
            m.evidence[key].insert(0, "Level %d, %s: %s" % (level, levels[level], str(entry.get("reason", ""))[:240]))
            hit = True
        judged += hit
    heatmap.judged_modules = judged
    return heatmap


def refresh_overall(heatmap, peak_share=0.5):
    for m in heatmap.modules:
        m.scores["overall"] = overall(m.scores, peak_share)
    return heatmap

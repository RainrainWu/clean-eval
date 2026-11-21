"""Combine aspect scores into the overall score, and map scores and levels to bands."""
import math

from .policy import BANDS


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def overall(s, peak_share=0.5):
    """(1 - peak_share) of the geometric mean of impact and weakness, plus peak_share of the worst single aspect.
    `s` holds biz, effects, entropy, unverified; an N/A aspect (None) is left out."""
    impact = max(s["biz"], s["effects"])
    rated = [v for v in (s["entropy"], s["unverified"]) if v is not None]
    weakness = sum(rated) / len(rated)
    worst = max(v for v in (s["biz"], s["effects"], s["entropy"], s["unverified"]) if v is not None)
    return round((1 - peak_share) * math.sqrt(impact * weakness) + peak_share * worst)


def level_range(level):
    """Score range of a level: level 0 is the top band, level 3 the bottom one."""
    idx = len(BANDS) - 1 - level
    return (BANDS[idx - 1][0] + 1 if idx else 0), BANDS[idx][0]


def place_in_level(level, static_score):
    """The level fixes the band; the static score (0-100) positions the module inside it."""
    lo, hi = level_range(level)
    return round(lo + (hi - lo) * max(0, min(100, static_score)) / 100)


def band(score):
    for upper, name in BANDS:
        if score <= upper:
            return name
    return BANDS[-1][1]


def band_floor(name):
    """Lowest score of a band."""
    for i, (_, n) in enumerate(BANDS):
        if n == name:
            return BANDS[i - 1][0] + 1 if i else 0
    raise ValueError("unknown band %r" % name)


def severity(name):
    """Order of bands, 0 for Low; None (unknown) sorts lowest."""
    return [n for _, n in BANDS].index(name) if name else -1

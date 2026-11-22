"""Scoring (use_cases/rules/scoring.py): bands, levels and the overall score."""

from clean_eval.domain.rating import AspectScores
from clean_eval.use_cases.rules.scoring import band, band_floor, level_range, overall, place_in_level


def S(biz, effects, entropy, unverified):
    return AspectScores(biz=biz, effects=effects, entropy=entropy, unverified=unverified)


def test_one_outstanding_aspect_lifts_overall():
    assert overall(S(95, 0, 0, 0)) == 48
    assert overall(S(0, 0, 0, 100)) == 50


def test_peak_share_moves_weight_to_the_worst_aspect():
    s = S(95, 0, 0, 0)
    assert overall(s, peak_share=1.0) == 95
    assert overall(s, peak_share=0.0) == 0


def test_not_applicable_aspect_is_left_out_of_overall():
    assert overall(S(40, 10, 20, None)) == overall(S(40, 10, 20, 20))


def test_levels_map_to_bands_most_severe_first():
    assert [level_range(n) for n in range(4)] == [(75, 100), (50, 74), (25, 49), (0, 24)]
    assert [place_in_level(0, s) for s in (0, 100)] == [75, 100]
    assert [band(place_in_level(n, 55)) for n in range(4)] == ["Critical", "High", "Moderate", "Low"]


def test_band_edges_and_floors():
    assert [band(v) for v in (0, 24, 25, 49, 50, 74, 75, 100)] == [
        "Low",
        "Low",
        "Moderate",
        "Moderate",
        "High",
        "High",
        "Critical",
        "Critical",
    ]
    assert [band_floor(b) for b in ("Low", "Moderate", "High", "Critical")] == [0, 25, 50, 75]

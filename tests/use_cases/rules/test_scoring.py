"""Scoring (use_cases/rules/scoring.py): bands, levels and the overall score."""
import unittest

from clean_eval.domain.rating import AspectScores
from clean_eval.use_cases.rules.scoring import band, band_floor, level_range, overall, place_in_level

def S(biz, effects, entropy, unverified):
    return AspectScores(biz=biz, effects=effects, entropy=entropy, unverified=unverified)


class Scoring(unittest.TestCase):
    def test_one_outstanding_aspect_lifts_overall(self):
        self.assertEqual(overall(S(95, 0, 0, 0)), 48)
        self.assertEqual(overall(S(0, 0, 0, 100)), 50)

    def test_peak_share_moves_weight_to_the_worst_aspect(self):
        s = S(95, 0, 0, 0)
        self.assertEqual(overall(s, peak_share=1.0), 95)
        self.assertEqual(overall(s, peak_share=0.0), 0)

    def test_not_applicable_aspect_is_left_out_of_overall(self):
        self.assertEqual(overall(S(40, 10, 20, None)), overall(S(40, 10, 20, 20)))

    def test_levels_map_to_bands_most_severe_first(self):
        self.assertEqual([level_range(n) for n in range(4)], [(75, 100), (50, 74), (25, 49), (0, 24)])
        self.assertEqual([place_in_level(0, s) for s in (0, 100)], [75, 100])
        self.assertEqual([band(place_in_level(n, 55)) for n in range(4)], ["Critical", "High", "Moderate", "Low"])

    def test_band_edges_and_floors(self):
        self.assertEqual([band(v) for v in (0, 24, 25, 49, 50, 74, 75, 100)],
                         ["Low", "Low", "Moderate", "Moderate", "High", "High", "Critical", "Critical"])
        self.assertEqual([band_floor(b) for b in ("Low", "Moderate", "High", "Critical")], [0, 25, 50, 75])


if __name__ == "__main__":
    unittest.main()

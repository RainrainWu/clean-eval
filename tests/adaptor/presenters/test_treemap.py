"""The squarified treemap layout (adaptor/presenters/treemap.py)."""
import unittest

from clean_eval.adaptor.presenters.treemap import squarify


class SquarifyTest(unittest.TestCase):
    def test_squarify_fills_the_canvas_without_loss(self):
        rects = squarify([("a", 6000.0), ("b", 3000.0), ("c", 1000.0)], 0, 0, 100, 100)
        self.assertEqual(len(rects), 3)
        self.assertAlmostEqual(sum(w * h for _, _, _, w, h in rects), 10000.0)


if __name__ == "__main__":
    unittest.main()

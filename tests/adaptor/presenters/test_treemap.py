"""The squarified treemap layout (adaptor/presenters/treemap.py)."""

import pytest

from clean_eval.adaptor.presenters.treemap import squarify


def test_squarify_fills_the_canvas_without_loss():
    rects = squarify([("a", 6000.0), ("b", 3000.0), ("c", 1000.0)], 0, 0, 100, 100)
    assert len(rects) == 3
    assert sum(w * h for _, _, _, w, h in rects) == pytest.approx(10000.0)

"""Fixtures for the presenters' tests (clean_eval/adaptor/): the heat maps they present, as `ratings` specs for the
`heatmap` of tests/conftest.py. A static asset takes no param; a decorator names it with pytest-lazy-fixtures' `lf`, as
the param of `ratings`: `@pytest.mark.parametrize("ratings", [lf("mixed_ratings")], indirect=True)`."""

import pytest

# The heat maps the presenters draw and summarise.


@pytest.fixture
def mixed_ratings():
    """The `ratings` specs of a heat map with three kinds of module: a risky one (src/api), one whose name must be
    escaped (src/<x>&y|z), and one whose Unverified aspect is N/A (terraform/lambda)."""
    return [
        {"name": "src/api", "loc": 3000, "biz": 90, "effects": 80, "entropy": 30, "unverified": 70},
        {"name": "src/<x>&y|z", "loc": 500, "biz": 10, "effects": 5, "entropy": 5, "unverified": 20},
        {"name": "terraform/lambda", "loc": 700, "biz": 20, "effects": 20, "entropy": 10, "unverified": None},
    ]

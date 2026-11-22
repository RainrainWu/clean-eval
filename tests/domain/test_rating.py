"""The rating models (domain/rating.py): score ranges, facts that survive JSON, assignments that are validated."""

import pytest
from pydantic import ValidationError

from clean_eval.domain.change import ModuleRecord
from clean_eval.domain.rating import Facts, Scores
from clean_eval.domain.values import BAND_NAMES, Band
from tests.domain.builders import facts, rating


def test_scores_are_0_to_100_and_only_unverified_may_be_na():
    Scores(biz=0, effects=100, entropy=5, unverified=None, overall=60)


@pytest.mark.parametrize("bad", [{"biz": 101}, {"entropy": -1}, {"biz": None}, {"overall": "high"}], ids=repr)
def test_out_of_range_or_missing_scores_are_refused(bad):
    with pytest.raises(ValidationError):
        Scores(**{"biz": 1, "effects": 1, "entropy": 1, "unverified": 1, "overall": 1, **bad})


def test_assignments_are_validated():
    m = rating()
    with pytest.raises(ValidationError):
        m.scores.biz = 140
    with pytest.raises(ValidationError):
        m.loc = -5


def test_facts_keep_their_counters_through_json():
    f = facts()
    back = Facts.model_validate_json(f.model_dump_json())
    assert back == f
    assert back.test_scales.most_common(1) == [("unit", 2)]
    with pytest.raises(ValidationError):
        facts(share=1.5)


def test_rated_means_scored():
    ModuleRecord(module="m", band="Low", score=10, rated=True)
    ModuleRecord(module="infra", band="Moderate", rated=False)


@pytest.mark.parametrize(
    "bad", [{"rated": True}, {"rated": False, "score": 30}], ids=["rated without a score", "unrated with a score"]
)
def test_a_record_is_scored_exactly_when_rated(bad):
    with pytest.raises(ValidationError):
        ModuleRecord(module="m", band="Low", **bad)


def test_the_band_type_matches_the_bands():
    assert Band.__args__ == BAND_NAMES

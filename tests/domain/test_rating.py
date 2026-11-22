"""The rating models (domain/rating.py): the code read, the knowledge graph, rated modules and heat maps, a reviewer's
judgement, and a change's risk."""

import pytest
from pydantic import ValidationError

from clean_eval.domain.policy import BAND_NAMES, Band
from clean_eval.domain.rating import Facts, Judgement, ModuleRecord, Scores
from tests.domain.builders import facts, rating

# The rating models (domain/rating.py): score ranges, facts that survive JSON, assignments that are validated.


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


# A reviewer's judgement (domain/rating.py): levels that don't validate are dropped, not raised.


def test_invalid_entries_are_dropped_not_fatal():
    j = Judgement.model_validate(
        {
            "modules": {
                "a": {
                    "business": {"level": 1, "reason": "core path " * 50},
                    "effects": {"level": 9},
                    "nope": {"level": 0},
                },
                "b": {"entropy": {"level": True}},
                "c": "x",
                7: {"business": {"level": 0}},
            }
        }
    )
    assert list(j.modules) == ["a"]
    assert list(j.modules["a"]) == ["business"]
    assert len(j.modules["a"]["business"].reason) == 240
    assert Judgement.model_validate("not an object").modules == {}

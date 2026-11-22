"""The rating models (domain/rating.py): score ranges, assignments that are validated, facts that survive JSON, the
records of a change's risk, and a reviewer's judgement, whose levels that don't validate are dropped, not raised."""

import pytest
from pydantic import ValidationError
from pytest_lazy_fixtures import lf

from clean_eval.domain.rating import (
    Facts,
    ModuleRecord,
    Scores,
)


class TestScores:
    """Scores are 0 to 100, and only Unverified may be N/A (None)."""

    @pytest.mark.parametrize(
        "scores",
        [{"biz": 0, "effects": 100, "entropy": 5, "unverified": None, "overall": 60}],
        indirect=True,
        ids=["0, 100 and N/A"],
    )
    def test_scores_are_0_to_100_and_only_unverified_may_be_na(
        self,
        scores,
    ):
        assert Scores.model_validate(scores).unverified is None

    @pytest.mark.parametrize(
        "scores",
        [{"biz": 101}, {"entropy": -1}, {"biz": None}, {"overall": "high"}],
        indirect=True,
        ids=repr,
    )
    def test_out_of_range_or_missing_scores_are_refused(
        self,
        scores,
    ):
        with pytest.raises(ValidationError):
            Scores.model_validate(scores)


class TestModuleRating:
    """A rated module validates assignments too, so no rule can push a score or a count out of range."""

    def test_assignments_are_validated(
        self,
        rating,
    ):
        with pytest.raises(ValidationError):
            rating.scores.biz = 140
        with pytest.raises(ValidationError):
            rating.loc = -5


class TestFacts:
    """The facts behind a module's scores survive JSON, counters and all, and stay in range."""

    def test_facts_keep_their_counters_through_json(
        self,
        facts,
    ):
        back = Facts.model_validate_json(facts.model_dump_json())
        assert back == facts
        assert back.test_scales.most_common(1) == [("unit", 2)]

    def test_a_share_of_the_code_above_one_is_refused(
        self,
        facts,
    ):
        with pytest.raises(ValidationError):
            Facts.model_validate({**facts.model_dump(), "share": 1.5})


class TestModuleRecord:
    """A module a change touched is scored exactly when it is rated."""

    @pytest.mark.parametrize(
        "module_record",
        [{}, {"module": "infra", "band": "Moderate", "score": None, "rated": False}],
        indirect=True,
        ids=["rated with a score", "unrated without one"],
    )
    def test_rated_means_scored(
        self,
        module_record,
    ):
        ModuleRecord.model_validate(module_record)

    @pytest.mark.parametrize(
        "module_record",
        [{"score": None}, {"rated": False, "score": 30}],
        indirect=True,
        ids=["rated without a score", "unrated with a score"],
    )
    def test_a_record_is_scored_exactly_when_rated(
        self,
        module_record,
    ):
        with pytest.raises(ValidationError):
            ModuleRecord.model_validate(module_record)


class TestJudgement:
    """A reviewer's judgement is untrusted: an entry that doesn't validate is dropped rather than failing it all."""

    @pytest.mark.parametrize("judgement", [lf("untidy_judgement")], indirect=True, ids=["untidy"])
    def test_invalid_entries_are_dropped_not_fatal(
        self,
        judgement,
    ):
        assert list(judgement.modules) == ["a"]
        assert list(judgement.modules["a"]) == ["business"]
        assert len(judgement.modules["a"]["business"].reason) == 240

    @pytest.mark.parametrize("judgement", ["not an object"], indirect=True)
    def test_a_judgement_that_is_not_an_object_has_no_levels(
        self,
        judgement,
    ):
        assert judgement.modules == {}

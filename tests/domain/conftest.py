"""Fixtures for the domain's tests: the raw input of the models that validate what comes from outside (an agent's
result.json, a code host's pull request, a stored result), one valid example per model; and the static assets the tests
compare with or feed those models.

Each model's fixture returns a fresh dict of the model's fields: a valid default, with the fields in `request.param`
({field: value}) replaced. A test sets them with `@pytest.mark.parametrize("<fixture>", [{...}], indirect=True)` and
validates the dict itself (`PullRequest.model_validate(host_pull_request)`), so an input the model must refuse is
refused inside the test's `pytest.raises`, not while the fixture is set up.

A static asset (the default policy, an untidy judgement, a finding, an accepted model change) takes no param: a test
takes it as an argument, or a decorator names it with pytest-lazy-fixtures' `lf`, in another fixture's param
(`{"findings": [lf("trusted_major")]}`). The models every layer's tests build (`policy`, `facts`, `ratings`, `rating`,
`heatmap`, `judgement`) are in tests/conftest.py."""

import copy
from typing import Any

import pytest

from clean_eval.domain.policy import Policy
from clean_eval.domain.retro import ModelChange

# The fields of one valid example of each model, which `request.param` overrides.

# domain/rating.py: a module's scores, and a module a change touched, rated at the change's base.
SCORES: frozendict[str, Any] = frozendict({"biz": 40, "effects": 10, "entropy": 20, "unverified": 30, "overall": 40})
MODULE_RECORD: frozendict[str, Any] = frozendict({"module": "m", "band": "Low", "score": 10, "rated": True})
STORED_REVIEW: frozendict[str, Any] = frozendict({"max_tier": "High", "max_score": 52, "findings": []})
REVIEW_RULE: frozendict[str, Any] = frozendict({"id": "CA-01", "severity": "blocker", "min_tier": "Low"})
HOST_PULL_REQUEST: frozendict[str, Any] = frozendict(
    {
        "number": 7,
        "author": "dev",
        "base_ref": "main",
        "head_ref": "feature",
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "head_refspec": "pull/7/head",
    }
)
# domain/retro.py: the period a retro looks back over, a merged PR, and a retro's outcome.
PERIOD: frozendict[str, Any] = frozendict({"since": "2026-01-01", "until": "2026-01-31"})
MERGED_PR: frozendict[str, Any] = frozendict(
    {"sha": "a" * 40, "base": "b" * 40, "author": "a", "ts": 1, "subject": "s"}
)
RETRO_RESULT: frozendict[str, Any] = frozendict(
    {"since": "2026-01-01", "until": "2026-02-01", "prs": 30, "defective_prs": 6, "auc": 0.7}
)


# domain/policy.py


@pytest.fixture
def default_policy():
    """The risk policy's defaults, Policy() without overrides: what an overridden `policy` is compared with."""
    return Policy()


# domain/rating.py


@pytest.fixture
def scores(
    request,
):
    """A module's Scores fields, as a heat map stores them: biz 40, effects 10, entropy 20, unverified 30 and overall
    40, with the fields in `request.param` replaced."""
    return copy.deepcopy({**SCORES, **getattr(request, "param", {})})


@pytest.fixture
def module_record(
    request,
):
    """A ModuleRecord's fields: module "m", rated Low with a score of 10, with the fields in `request.param` replaced
    (an unrated record sets `rated` False and `score` None)."""
    return copy.deepcopy({**MODULE_RECORD, **getattr(request, "param", {})})


@pytest.fixture
def untidy_judgement():
    """A reviewer's judgement as raw data (a `judgement` param): one valid level (module a's business, with a reason
    longer than the 240 characters kept), among an unknown level, an unknown aspect, a level that isn't an int, a
    module that isn't an object and a module name that isn't a string."""
    return {
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


# domain/review.py


@pytest.fixture
def stored_review(
    request,
):
    """A Review as result.json stores it: High (52) with no findings, with the fields in `request.param` replaced
    (`findings` as a list of dicts of Finding's fields)."""
    return copy.deepcopy({**STORED_REVIEW, **getattr(request, "param", {})})


@pytest.fixture
def review_rule(
    request,
):
    """One row of a rules table (ReviewRule's fields): CA-01, a blocker from the Low band up, with the fields in
    `request.param` replaced."""
    return copy.deepcopy({**REVIEW_RULE, **getattr(request, "param", {})})


@pytest.fixture
def host_pull_request(
    request,
):
    """A pull request as the code host gateway describes it (PullRequest's fields): #7 by dev, from feature onto main,
    commits "b" * 40 onto "a" * 40, with the fields in `request.param` replaced."""
    return copy.deepcopy({**HOST_PULL_REQUEST, **getattr(request, "param", {})})


@pytest.fixture
def trusted_blocker():
    """A finding CI trusts (Finding's fields) of the blocker severity: rule CA-01."""
    return {"rule": "CA-01", "severity": "blocker"}


@pytest.fixture
def trusted_major():
    """A finding CI trusts (Finding's fields) of the major severity: rule Q-02."""
    return {"rule": "Q-02", "severity": "major"}


# domain/retro.py


@pytest.fixture
def period(
    request,
):
    """A Period's fields: January 2026, with the default window for fixes (30 days), with the fields in
    `request.param` replaced."""
    return copy.deepcopy({**PERIOD, **getattr(request, "param", {})})


@pytest.fixture
def merged_pr(
    request,
):
    """A MergedPR's fields: commit "a" * 40 merged onto "b" * 40 by "a", with no files, modules, defects or review,
    with the fields in `request.param` replaced."""
    return copy.deepcopy({**MERGED_PR, **getattr(request, "param", {})})


@pytest.fixture
def retro_result(
    request,
):
    """An RetroResult's fields: 2026-01-01 to 2026-02-01, 30 PRs of which 6 defective, AUC 0.7, with no change kept,
    with the fields in `request.param` replaced (`model_change`, `rules_change`: a model or a dict of its fields)."""
    return copy.deepcopy({**RETRO_RESULT, **getattr(request, "param", {})})


@pytest.fixture
def accepted_model_change():
    """An edit of the risk model that the replay accepted (a ModelChange): of knob x, lifting the AUC from 0.6 to
    0.7."""
    return ModelChange(
        changed_keys=["x"],
        auc_before=0.6,
        auc_after=0.7,
    )

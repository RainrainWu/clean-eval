"""Fixtures for the domain's tests: the raw input of the models that validate what comes from outside (an agent's
result.json, the code host's JSON, a stored result), one valid example per model.

Each fixture returns a fresh dict of a model's fields: a valid default, with the fields in `request.param` ({field:
value}) replaced. A test sets them with `@pytest.mark.parametrize("<fixture>", [{...}], indirect=True)` and validates
the dict itself (`PullRequest.model_validate(pull_request)`), so an input the model must refuse is refused inside the
test's `pytest.raises`, not while the fixture is set up. The models every layer's tests build (`policy`, `facts`,
`ratings`, `rating`, `heatmap`, `judgement`) are in tests/conftest.py."""

import copy
from typing import Any

import pytest

# The fields of one valid example of each model, which `request.param` overrides.

# domain/rating.py: a module's scores, and a module a change touched, rated at the change's base.
SCORES: dict[str, Any] = {"biz": 40, "effects": 10, "entropy": 20, "unverified": 30, "overall": 40}
MODULE_RECORD: dict[str, Any] = {"module": "m", "band": "Low", "score": 10, "rated": True}
STORED_REVIEW: dict[str, Any] = {"max_tier": "High", "max_score": 52, "findings": []}
REVIEW_RULE: dict[str, Any] = {"id": "CA-01", "severity": "blocker", "min_tier": "Low"}
# domain/evaluation.py: the period an eval looks back over, a merged PR, and an eval's outcome.
PERIOD: dict[str, Any] = {"since": "2026-01-01", "until": "2026-01-31"}
MERGED_PR: dict[str, Any] = {"sha": "a" * 40, "base": "b" * 40, "author": "a", "ts": 1, "subject": "s"}
EVAL_RESULT: dict[str, Any] = {"since": "2026-01-01", "until": "2026-02-01", "prs": 30, "defective_prs": 6, "auc": 0.7}


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


# domain/evaluation.py


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
def eval_result(
    request,
):
    """An EvalResult's fields: 2026-01-01 to 2026-02-01, 30 PRs of which 6 defective, AUC 0.7, with no change kept,
    with the fields in `request.param` replaced (`model_change`, `rules_change`: a model or a dict of its fields)."""
    return copy.deepcopy({**EVAL_RESULT, **getattr(request, "param", {})})

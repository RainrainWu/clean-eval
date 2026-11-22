"""Fixtures for the business rules' tests (clean_eval/use_cases/rules/): the domain models the rules read, beyond the
cross-layer ones of tests/conftest.py (`policy`, `facts`, `source_files`, `ratings`, `heatmap`, `judgement`) and the
`environ` of tests/use_cases/conftest.py.

The rules are pure functions over domain models and take no interfaces, so these are all data fixtures. Each has a
default, and reads its customisation from `request.param`; a test sets it with
`@pytest.mark.parametrize("<fixture>", [<value>], indirect=True)`, on the test or on its whole class. `agent_review` is
built from `finding`, and follows its customisation."""

from typing import Any

import pytest

from clean_eval.domain.evaluation import MergedPR
from clean_eval.domain.rating import AspectScores
from clean_eval.domain.review import InlineComment

# The aspect scores of the module the `ratings` fixture rates by default.
ASPECT_SCORES: dict[str, Any] = {"biz": 40, "effects": 10, "entropy": 20, "unverified": 30}
INLINE_COMMENT: dict[str, Any] = {"path": "a.py", "line": 1, "side": "RIGHT", "body": "b"}
MERGED_PR: dict[str, Any] = {"sha": "a" * 40, "base": "b" * 40, "author": "a", "ts": 1, "subject": "s"}


# A rated module and the code read from a repository.


@pytest.fixture
def aspect_scores(
    request,
):
    """A module's AspectScores: ASPECT_SCORES (those of the `rating` fixture's module: biz 40, effects 10, entropy 20,
    unverified 30) with the fields in `request.param` replaced; `{"unverified": None}` makes Unverified not
    applicable."""
    return AspectScores.model_validate({**ASPECT_SCORES, **getattr(request, "param", {})})


# A PR review: its rules, what the agent proposed, the change's risk, and what is posted.


@pytest.fixture
def inline_comment(
    request,
):
    """An InlineComment of a review: on line 1 of a.py, with the fields in `request.param` replaced."""
    return InlineComment.model_validate({**INLINE_COMMENT, **getattr(request, "param", {})})


# The merged-PR history the eval reads.


@pytest.fixture
def merged_prs(
    request,
):
    """MergedPRs, one per spec in `request.param` (dicts of MergedPR fields over MERGED_PR: commit aaaa... merged onto
    bbbb...; `modules` as ModuleRecord dicts); one PR that touched no module when not parametrized."""
    return [MergedPR.model_validate({**MERGED_PR, **spec}) for spec in getattr(request, "param", [{}])]

"""Fixtures for the presenters' tests (clean_eval/adaptor/): the heat maps they present, as `ratings` specs for the
`heatmap` of tests/conftest.py, and the review comments they render. A static asset takes no param; a decorator names it
with pytest-lazy-fixtures' `lf`, as the param of `ratings`: `@pytest.mark.parametrize("ratings", [lf("mixed_ratings")],
indirect=True)`."""

from pathlib import Path
from typing import Any

import pytest

from clean_eval.domain.review import ReviewComment
from tests.helpers import ROOT

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


# The review comments the Markdown presenter renders, and the template of the pr-review skill that shows their layout.


# The comment the template's example shows: a request for changes in the High band, with CA-01 and Q-01 violated, a
# finding no rule covers, and the reviewer's notes.
TEMPLATE_COMMENT: frozendict[str, Any] = frozendict(
    {
        "verdict": "request_changes",
        "max_tier": "High",
        "max_score": 62,
        "modules": [
            {"module": "src/payments", "band": "High", "score": 62, "rated": True},
            {"module": "src/shared", "band": "Low", "score": 12, "rated": True},
            {"module": "infra", "band": "Moderate", "rated": False},
        ],
        "rules_checked": 30,
        "rules_total": 34,
        "violations": [
            {
                "rule": "CA-01",
                "title": "Dependency rule",
                "severity": "blocker",
                "findings": [
                    {
                        "rule": "CA-01",
                        "severity": "blocker",
                        "file": "src/payments/domain/invoice.py",
                        "line": 12,
                        "summary": "The domain imports `infra.db.session`, an outer layer.",
                        "fix": "Depend on an `InvoiceStore` port the use case owns.",
                    }
                ],
            },
            {
                "rule": "Q-01",
                "title": "Tests follow behaviour",
                "severity": "blocker",
                "findings": [
                    {
                        "rule": "Q-01",
                        "severity": "blocker",
                        "file": "src/payments/api.py",
                        "line": 40,
                        "summary": "The new refund path has no test; a blocker, as the module is High.",
                        "fix": "Add a test that fails without the refund branch.",
                    }
                ],
            },
        ],
        "uncovered": [
            {
                "rule": "NEW",
                "severity": "major",
                "file": "src/payments/api.py",
                "line": 52,
                "summary": "A failed payment is retried without a limit.",
                "fix": "Cap the retries and surface the last error.",
            }
        ],
        "notes": (
            "Adds refunds to payments: an API endpoint, and a retry when the provider fails.\n\n"
            "**Strengths**\n- The refund amount is validated at the boundary.\n\n"
            "**Not checked / assumptions**\n"
            "- `diff/004.patch` (a generated migration) was read only to line 2,000."
        ),
    }
)


@pytest.fixture
def review_comment(
    request,
):
    """A review's comment, as the review rules make it: TEMPLATE_COMMENT, the one the template's example shows, with the
    fields in `request.param` replaced."""
    return ReviewComment.model_validate({**TEMPLATE_COMMENT, **getattr(request, "param", {})})


@pytest.fixture
def comment_template():
    """The pr-review skill's references/comment.md: the layout every review comment has, with an example."""
    return Path(
        ROOT,
        ".claude",
        "skills",
        "pr-review",
        "references",
        "comment.md",
    ).read_text()

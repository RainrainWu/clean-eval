"""The domain's errors (domain/errors.py): what a failed validation reads like."""

from pydantic import ValidationError

from clean_eval.domain.errors import InvalidOutput
from clean_eval.domain.review import AgentReview


def test_validation_errors_read_as_where_and_what():
    try:
        AgentReview.model_validate({"findings": [{"rule": "x", "severity": "major"}]})
    except ValidationError as e:
        message = str(InvalidOutput.from_validation(e, "result.json"))
    assert message.startswith("result.json: findings[0].rule: String should match pattern"), message

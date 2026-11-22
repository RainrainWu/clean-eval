"""The domain's errors (domain/errors.py): what a failed validation reads like."""

import pytest
from pydantic import ValidationError

from clean_eval.domain.errors import InvalidOutput
from clean_eval.domain.review import AgentReview


class TestFromValidation:
    """An error made from a pydantic ValidationError: the subject, then `where: what` for each problem."""

    @pytest.mark.parametrize("finding", [{"rule": "x"}], indirect=True, ids=["a rule that is no rule ID"])
    def test_validation_errors_read_as_where_and_what(
        self,
        finding,
    ):
        with pytest.raises(ValidationError) as raised:
            AgentReview.model_validate({"findings": [finding]})
        message = str(InvalidOutput.from_validation(raised.value, "result.json"))
        assert message.startswith("result.json: findings[0].rule: String should match pattern"), message

"""The domain's errors (domain/errors.py): what a failed validation reads like."""
import unittest

from pydantic import ValidationError

from clean_eval.domain.errors import InvalidOutput
from clean_eval.domain.review import AgentReview


class ErrorMessages(unittest.TestCase):
    def test_validation_errors_read_as_where_and_what(self):
        try:
            AgentReview.model_validate({"findings": [{"rule": "x", "severity": "major"}]})
        except ValidationError as e:
            message = str(InvalidOutput.from_validation(e, "result.json"))
        self.assertTrue(message.startswith("result.json: findings[0].rule: String should match pattern"), message)


if __name__ == "__main__":
    unittest.main()

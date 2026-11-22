"""The composition root (external/wiring.py): what it builds from the request and this process's environment."""
import datetime as dt
import os
import unittest
from unittest import mock

from clean_eval.domain.errors import InvalidRequest
from clean_eval.external import wiring
from clean_eval.use_cases.rules.redaction import redact


class WiringTest(unittest.TestCase):
    def test_period_bounds(self):
        p = wiring.period("2026-01-01", "2026-01-31", 10)
        self.assertEqual((p.lookahead_until, p.window_days), (dt.date(2026, 2, 10), 10))
        self.assertEqual(p.until_ts, dt.datetime(2026, 1, 31, 23, 59, 59, tzinfo=dt.timezone.utc).timestamp())
        with self.assertRaises(InvalidRequest):
            wiring.period("2026-01-01", "soon")

    def test_the_secrets_come_from_this_process_environment(self):
        with mock.patch.dict(os.environ, {"SOME_SECRET": "hunter2-hunter2"}):
            self.assertEqual(redact("pw hunter2-hunter2", wiring.secrets())[0], "pw [REDACTED]")


if __name__ == "__main__":
    unittest.main()

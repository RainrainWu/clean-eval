"""The clock gateway (external/clock.py)."""
import datetime as dt
import unittest

from clean_eval.external.clock import ClockGateway


class ClockGatewayTest(unittest.TestCase):
    def test_today(self):
        self.assertEqual(ClockGateway().today(), dt.date.today().isoformat())


if __name__ == "__main__":
    unittest.main()

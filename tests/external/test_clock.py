"""The clock gateway (external/clock.py)."""

import datetime as dt

from clean_eval.external.clock import ClockGateway


def test_today():
    assert ClockGateway().today() == dt.date.today().isoformat()

"""The composition root (external/wiring.py): what it builds from the request and this process's environment."""

import datetime as dt

import pytest

from clean_eval.domain.errors import InvalidRequest
from clean_eval.external import wiring
from clean_eval.use_cases.rules.redaction import redact


def test_period_bounds():
    p = wiring.period("2026-01-01", "2026-01-31", 10)
    assert (p.lookahead_until, p.window_days) == (dt.date(2026, 2, 10), 10)
    assert p.until_ts == dt.datetime(2026, 1, 31, 23, 59, 59, tzinfo=dt.timezone.utc).timestamp()
    with pytest.raises(InvalidRequest):
        wiring.period("2026-01-01", "soon")


def test_the_secrets_come_from_this_process_environment(monkeypatch):
    monkeypatch.setenv("SOME_SECRET", "hunter2-hunter2")
    assert redact("pw hunter2-hunter2", wiring.secrets())[0] == "pw [REDACTED]"

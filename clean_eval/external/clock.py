"""The clock: today's date, the only time clean-eval reads it (the inner layers are given dates, never ask for them)."""

import datetime as dt

from clean_eval.use_cases.interfaces import Clock


class ClockGateway(Clock):
    def today(self):
        """Today, as YYYY-MM-DD."""
        return dt.date.today().isoformat()

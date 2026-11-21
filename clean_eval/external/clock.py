"""The clock: today's date, the only time clean-eval reads it (the inner layers are given dates, never ask for them)."""
import datetime as dt


class ClockGateway:
    def today(self):
        """Today, as YYYY-MM-DD."""
        return dt.date.today().isoformat()

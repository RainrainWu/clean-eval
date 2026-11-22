"""The process environment and the clock: environment variables and the home folder (EnvironmentGateway), and
today's date (ClockGateway)."""

import datetime as dt
import os

from clean_eval.use_cases.interfaces import Clock, Environment

# The process environment: its variables (secrets to redact, the guard's roots, PATH) and the home folder.


class EnvironmentGateway(Environment):
    def variables(self):
        """A copy of every variable, as {name: value}."""
        return dict(os.environ)

    def get(self, name, default=None):
        return os.environ.get(name, default)

    def paths(self, name):
        """The paths an os.pathsep-separated variable lists (empty entries left out)."""
        return [p for p in os.environ.get(name, "").split(os.pathsep) if p.strip()]

    def home(self):
        return os.path.expanduser("~")


# The clock: today's date, the only time clean-eval reads it (the inner layers are given dates, never ask for them).


class ClockGateway(Clock):
    def today(self):
        """Today, as YYYY-MM-DD."""
        return dt.date.today().isoformat()

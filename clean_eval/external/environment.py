"""The process environment: its variables (secrets to redact, the guard's roots, PATH) and the home folder."""
import os

from clean_eval.use_cases.interfaces import Environment


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

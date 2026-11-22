"""The process environment and the clock: environment variables, the secrets they hold and the home folder
(EnvironmentGateway), and today's date (ClockGateway)."""

import base64
import datetime as dt
import os
import re

from clean_eval.domain.review import Secrets
from clean_eval.use_cases.interfaces import (
    Clock,
    Environment,
)

# The process environment: its variables (the secrets to redact, the guard's roots, PATH) and the home folder. A run's
# settings read their own variables (clean_eval/settings.py).
#
# What a secret looks like: the value of a variable named like one, and its base64 forms (e.g. the
# `AUTHORIZATION: basic <b64("x-access-token:<token>")>` that actions/checkout persists in .git/config); and the
# well-known credential formats, wherever the text came from: GitHub's tokens, Anthropic's API keys, AWS's access keys,
# Slack's tokens, PEM private keys, and the credential of an authorization header (its group).
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CREDENTIAL", re.I)
TOKEN_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"gh[pousr]_[A-Za-z0-9]{30,}",
        r"github_pat_[A-Za-z0-9_]{40,}",
        r"sk-ant-[A-Za-z0-9_\-]{20,}",
        r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
        r"xox[abpr]-[A-Za-z0-9-]{10,}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    )
)
AUTH_HEADER = re.compile(r"(?i)authorization:\s*(?:basic|bearer|token)\s+([A-Za-z0-9+/=._\-]{8,})")


def secret_values(
    variables: dict[str, str],
) -> tuple[str, ...]:
    """The values to redact, from the environment's `variables`: every secret-looking variable's value (8+ characters)
    and its base64 forms. Longest first, so a secret that contains another is replaced whole."""
    values = {v for k, v in variables.items() if SECRET_NAME.search(k) and v and len(v) >= 8}
    encoded = {base64.b64encode(raw.encode()).decode() for v in values for raw in (v, "x-access-token:" + v)}
    return tuple(sorted(values | encoded, key=len, reverse=True))


class EnvironmentGateway(Environment):
    def variables(
        self,
    ) -> dict[str, str]:
        """A copy of every variable, as {name: value}."""
        return dict(os.environ)

    def secrets(
        self,
    ) -> Secrets:
        return Secrets(
            values=secret_values(dict(os.environ)),
            patterns=(*TOKEN_PATTERNS, AUTH_HEADER),
        )

    def paths(
        self,
        name: str,
    ) -> list[str]:
        """The paths an os.pathsep-separated variable lists (empty entries left out)."""
        return [p for p in os.environ.get(name, "").split(os.pathsep) if p.strip()]

    def home(
        self,
    ) -> str:
        return os.path.expanduser("~")


# The clock: today's date, the only time clean-eval reads it (the inner layers are given dates, never ask for them).


class ClockGateway(Clock):
    def today(
        self,
    ) -> str:
        """Today, as YYYY-MM-DD."""
        return dt.date.today().isoformat()

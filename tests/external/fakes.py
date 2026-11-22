"""Fakes of what the external gateways call out to, for tests/external: the claude process the Claude Code gateway
starts, and GitHub's OIDC token endpoint it requests the job's token from. Classes only: the fixtures that hand them
out (`claude_runs`, `oidc_tokens`) are in tests/external/conftest.py."""

import io
import json
import subprocess
from pathlib import Path


class ClaudeProcess:
    """How a fake claude process answers the gateway's waits (`communicate`, the side effect of the process of the Popen
    mock `popen`), as `outcome` says: the first `timeouts` waits time out; then the wait raises `interrupted` when given
    (claude still running), else claude ends with status `exit` and the transcript `out`. At each wait it records what
    the identity-token file held, in `popen.identity_tokens`."""

    def __init__(
        self,
        popen,
        outcome,
    ):
        self.popen = popen
        self.outcome = outcome

    def communicate(
        self,
        timeout=None,
    ):
        """One wait for claude, with the gateway's `timeout`: its transcript and no stderr, once it ends."""
        process = self.popen.return_value
        token = self.popen.call_args.kwargs["env"].get("ANTHROPIC_IDENTITY_TOKEN_FILE")
        if token:
            self.popen.identity_tokens.append(
                Path(
                    token,
                ).read_text()
            )
        if process.communicate.call_count <= self.outcome.get("timeouts", 0):
            raise subprocess.TimeoutExpired(
                "claude",
                timeout or 0,
            )
        if self.outcome.get("interrupted"):
            raise self.outcome["interrupted"]
        process.returncode = process.poll.return_value = self.outcome.get("exit", 0)
        return self.outcome.get("out", "done"), None


class TokenEndpoint:
    """GitHub's OIDC token endpoint (`respond`, the side effect of the urlopen mock): it gives its `replies` in turn, each
    a token it issues (a str), a raw body (bytes) or an exception it raises, and past them issues "jwt-<n>" on its n-th
    call."""

    def __init__(
        self,
        replies,
    ):
        self.replies = list(replies)
        self.calls = 0

    def respond(
        self,
        url_request,
        **options,
    ):
        """The response to the gateway's request `url_request` (urlopen's other keywords in `options`)."""
        self.calls += 1
        reply = self.replies.pop(0) if self.replies else "jwt-%d" % self.calls
        if isinstance(reply, BaseException):
            raise reply
        return io.BytesIO(
            reply if isinstance(reply, bytes) else json.dumps({"value": reply}).encode(),
        )

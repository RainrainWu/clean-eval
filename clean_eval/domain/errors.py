"""Failures the user can act on. The message is shown as it is."""

from typing import Literal, Optional, Self

from pydantic import ValidationError


class HeatmapError(Exception):
    @classmethod
    def from_validation(
        cls,
        error: ValidationError,
        subject: Optional[str] = None,
    ) -> Self:
        """This error, telling what a pydantic ValidationError found: one `where: what` per problem."""
        problems = []
        for e in error.errors():
            where = "".join(
                "[%d]" % p if isinstance(p, int) else ("." if i else "") + str(p) for i, p in enumerate(e["loc"])
            )
            what = "unknown key" if e["type"] == "extra_forbidden" else e["msg"]
            for prefix in ("Value error, ", "Assertion failed, "):
                what = what[len(prefix) :] if what.startswith(prefix) else what
            problems.append("%s: %s" % (where, what) if where else what)
        text = "; ".join(problems)
        return cls("%s: %s" % (subject, text) if subject else text)


class NoPythonSource(HeatmapError):
    pass


class InvalidRequest(HeatmapError):
    pass


class InvalidPolicy(HeatmapError):
    pass


class InvalidOutput(HeatmapError):
    """What the CI agent proposed (its result, its report, or an edit it made) does not pass validation."""


class RemoteError(HeatmapError):
    """A call to the code host failed. `kind` is "not_found" (the PR doesn't exist) or "error" (anything else)."""

    def __init__(
        self,
        message: str,
        kind: Literal["not_found", "error"] = "error",
    ) -> None:
        super().__init__(message)
        self.kind = kind


class RunFailed(HeatmapError):
    """A CI run that must fail: a bad input, a checkout it can't run on, or a review its fail-on setting fails on."""

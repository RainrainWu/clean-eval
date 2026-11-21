"""Failures the user can act on. The message is shown as it is."""


class HeatmapError(Exception):
    pass


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

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind

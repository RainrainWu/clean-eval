"""Failures the user can act on. The message is shown as it is."""


class HeatmapError(Exception):
    pass


class NoPythonSource(HeatmapError):
    pass


class InvalidRequest(HeatmapError):
    pass


class InvalidPolicy(HeatmapError):
    pass

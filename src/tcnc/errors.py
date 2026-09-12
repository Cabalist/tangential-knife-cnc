"""Exception types raised by tcnc.

Every public failure is a ``ValueError`` subclass so a caller can catch the
whole family with one clause; the subclasses say which stage failed.
"""


class TcncError(ValueError):
    """Base class for every error tcnc raises on bad input, bad geometry or bad output."""


class OptionError(TcncError):
    """An option value is out of range or inconsistent with another option."""


class SvgError(TcncError):
    """The SVG input could not be read or contains nothing usable."""


class PlanError(TcncError):
    """The toolpaths cannot be turned into a cut plan or G-code."""


class OutputError(TcncError):
    """The G-code or preview file could not be written."""

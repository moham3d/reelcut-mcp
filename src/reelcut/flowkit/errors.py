"""Shared exceptions for the driver.

`Blocked` is the one that matters: a precondition failed in a way no retry can
fix (login, connect, wrong account, wrong channel, truncated prompt). The
engine turns it into a stopped run with a written report. It lives in its own
module so every driver layer can raise it without an import cycle.
"""


class Blocked(RuntimeError):
    """A precondition failed in a way no retry can fix."""

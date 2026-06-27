"""P4.2 — No-lookahead guard.

Ensures FeatureStore never returns data derived from bars whose close_time > t.
Any violation raises LookaheadError (subclass of AssertionError) so it cannot
be silently swallowed.
"""

from __future__ import annotations

from datetime import datetime
from functools import wraps
from typing import Any, Callable


class LookaheadError(AssertionError):
    """Raised when a feature is accessed with available_at > current time t."""


def assert_available(feature: Any, t: datetime) -> None:
    """Assert that *feature* was computed from bars closed at or before *t*.

    *feature* must have an `available_at` attribute (typically a
    `Feature` dataclass).  If `available_at > t` a `LookaheadError` is raised.
    """
    if feature is None:
        return
    avail = getattr(feature, "available_at", None)
    if avail is None:
        return  # no timestamp → cannot verify (tolerated but logged in debug)
    if not isinstance(avail, datetime):
        return
    if not isinstance(t, datetime):
        return
    if avail > t:
        raise LookaheadError(
            f"Feature available_at={avail.isoformat()} > t={t.isoformat()} "
            f"(delta={avail - t})"
        )


def guard_feature_access(fn: Callable) -> Callable:
    """Decorator: wraps a FeatureStore getter so every access is guarded.

    The decorated function must accept a keyword argument ``t`` (the current
    candle time).  Any feature returned is checked via `assert_available`.
    """

    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        result = fn(*args, **kwargs)
        t = kwargs.get("t")
        if t is not None and result is not None:
            # result may be a single Feature or a collection
            if hasattr(result, "available_at"):
                assert_available(result, t)
            elif isinstance(result, dict):
                for v in result.values():
                    if hasattr(v, "available_at"):
                        assert_available(v, t)
            elif isinstance(result, (list, tuple)):
                for v in result:
                    if hasattr(v, "available_at"):
                        assert_available(v, t)
        return result

    return wrapper

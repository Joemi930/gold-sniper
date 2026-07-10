"""P4.2 — No-lookahead guard tests."""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from gold_sniper.replay.no_lookahead_guard import (
    LookaheadError,
    assert_available,
    guard_feature_access,
)


@dataclass
class Feature:
    value: object
    available_at: datetime


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestNoLookaheadGuard(unittest.TestCase):

    def test_assert_available_passes_when_feature_older(self):
        feat = Feature(value=42, available_at=_utc("2025-12-08T10:00:00Z"))
        t = _utc("2025-12-08T10:01:00Z")
        assert_available(feat, t)  # must not raise

    def test_assert_available_passes_when_equal(self):
        feat = Feature(value=42, available_at=_utc("2025-12-08T10:00:00Z"))
        t = _utc("2025-12-08T10:00:00Z")
        assert_available(feat, t)  # same time is valid (close_time == t)

    def test_assert_available_raises_when_feature_newer(self):
        feat = Feature(value=42, available_at=_utc("2025-12-08T10:02:00Z"))
        t = _utc("2025-12-08T10:01:00Z")
        with self.assertRaises(LookaheadError):
            assert_available(feat, t)

    def test_assert_available_none_is_noop(self):
        assert_available(None, _utc("2025-12-08T10:00:00Z"))  # no raise

    def test_assert_available_no_timestamp_is_noop(self):
        class NoTs:
            pass
        assert_available(NoTs(), _utc("2025-12-08T10:00:00Z"))  # no raise

    def test_guard_feature_access_decorator_passes(self):
        @guard_feature_access
        def getter(t=None):
            return Feature(value=1, available_at=_utc("2025-12-08T10:00:00Z"))

        result = getter(t=_utc("2025-12-08T10:01:00Z"))
        self.assertEqual(result.value, 1)

    def test_guard_feature_access_decorator_raises(self):
        @guard_feature_access
        def getter(t=None):
            return Feature(value=1, available_at=_utc("2025-12-08T10:02:00Z"))

        with self.assertRaises(LookaheadError):
            getter(t=_utc("2025-12-08T10:01:00Z"))

    def test_guard_feature_access_list(self):
        @guard_feature_access
        def getter(t=None):
            return [
                Feature(value=1, available_at=_utc("2025-12-08T10:00:00Z")),
                Feature(value=2, available_at=_utc("2025-12-08T10:03:00Z")),  # future!
            ]

        with self.assertRaises(LookaheadError):
            getter(t=_utc("2025-12-08T10:01:00Z"))

    def test_mtf_no_future_bar(self):
        """A 15m bar that hasn't closed yet must not be visible."""
        # This test validates the contract: if a bar's close_time > t,
        # LookaheadError must fire.
        feat = Feature(
            value={"timeframe": "15m", "close": 2650.0},
            available_at=_utc("2025-12-08T10:15:00Z"),  # closes at :15
        )
        t_before_close = _utc("2025-12-08T10:14:00Z")
        with self.assertRaises(LookaheadError):
            assert_available(feat, t_before_close)

        t_at_close = _utc("2025-12-08T10:15:00Z")
        assert_available(feat, t_at_close)  # OK at close


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from replay.replay_clock import ReplayClock


class TestP1ReplayClockTimezone(unittest.TestCase):
    def test_dst_spring_forward_new_york(self):
        candles = [
            {"time": datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc), "open": 1, "high": 1, "low": 1, "close": 1},
            {"time": datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc), "open": 1, "high": 1, "low": 1, "close": 1},
        ]
        ticks = list(ReplayClock(candles).ticks())
        self.assertEqual(ticks[0].ts_ny.hour, 1)
        self.assertEqual(ticks[1].ts_ny.hour, 3)
        self.assertEqual(ticks[0].ts_utc.tzinfo, timezone.utc)
        self.assertTrue(ticks[0].bar_closed)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from replay.multi_timeframe_builder import MultiTimeframeBuilder


def candle(i):
    t = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    return {"time": t, "open": i, "high": i + 1, "low": i - 1, "close": i + 0.5, "volume": 1}


class TestP1MultiTimeframeBuilder(unittest.TestCase):
    def test_no_lookahead_15m(self):
        builder = MultiTimeframeBuilder(timeframes=("15m",))
        emitted = []
        for i in range(14):
            emitted.extend(builder.update(candle(i))["15m"])
        self.assertEqual([], emitted)
        emitted.extend(builder.update(candle(14))["15m"])
        self.assertEqual(1, len(emitted))
        bar = emitted[0]
        self.assertEqual(bar["open"], 0.0)
        self.assertEqual(bar["close"], 14.5)
        next_emit = builder.update(candle(15))["15m"]
        self.assertEqual([], next_emit)

    def test_supported_timeframes_emit(self):
        builder = MultiTimeframeBuilder(timeframes=("5m", "15m", "30m"))
        output = {"5m": 0, "15m": 0, "30m": 0}
        for i in range(30):
            emitted = builder.update(candle(i))
            for tf in output:
                output[tf] += len(emitted[tf])
        self.assertEqual(output["5m"], 6)
        self.assertEqual(output["15m"], 2)
        self.assertEqual(output["30m"], 1)


if __name__ == "__main__":
    unittest.main()

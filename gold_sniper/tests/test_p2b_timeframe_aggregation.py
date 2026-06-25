from __future__ import annotations

import unittest
from datetime import datetime, timezone

from gold_sniper.data_pipeline.timeframe_aggregation import aggregate_candles


class TestP2bTimeframeAggregation(unittest.TestCase):
    def test_aggregates_ohlc_and_volume(self) -> None:
        candles = [
            _candle("2026-06-01T00:00:00Z", 100, 102, 99, 101, 10),
            _candle("2026-06-01T00:01:00Z", 101, 103, 100, 102, 11),
            _candle("2026-06-01T00:04:00Z", 102, 104, 98, 103, 12),
            _candle("2026-06-01T00:05:00Z", 103, 105, 102, 104, 13),
        ]

        aggregated = aggregate_candles(candles, target_timeframe="5m")

        self.assertEqual(len(aggregated), 2)
        self.assertEqual(aggregated[0]["time"], datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(aggregated[0]["open"], 100)
        self.assertEqual(aggregated[0]["high"], 104)
        self.assertEqual(aggregated[0]["low"], 98)
        self.assertEqual(aggregated[0]["close"], 103)
        self.assertEqual(aggregated[0]["tick_volume"], 33)
        self.assertEqual(aggregated[1]["open"], 103)

    def test_supports_1h(self) -> None:
        candles = [
            _candle("2026-06-01T00:00:00Z", 100, 101, 99, 100, 1),
            _candle("2026-06-01T00:59:00Z", 100, 110, 95, 105, 2),
            _candle("2026-06-01T01:00:00Z", 105, 106, 104, 105, 3),
        ]

        aggregated = aggregate_candles(candles, target_timeframe="1H")

        self.assertEqual(len(aggregated), 2)
        self.assertEqual(aggregated[0]["high"], 110)
        self.assertEqual(aggregated[0]["low"], 95)
        self.assertEqual(aggregated[0]["close"], 105)


def _candle(ts: str, open_: float, high: float, low: float, close: float, volume: float) -> dict:
    return {
        "time": ts,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "tick_volume": volume,
    }


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import datetime, timezone
import unittest

from gold_sniper.replay.offline_market_structure import Candle, candles_until, detect_swings, infer_bias_from_swings


def c(index: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(datetime(2026, 4, 1, index, tzinfo=timezone.utc), f"2026-04-01T{index:02d}:00:00Z", open_, high, low, close)


class TestOfflineMarketStructure(unittest.TestCase):
    def test_anti_lookahead_candles_until_excludes_future(self) -> None:
        candles = [c(1, 1, 2, 0, 1.5), c(2, 2, 3, 1, 2.5)]
        selected = candles_until(candles, candles[0].time)
        self.assertEqual(len(selected), 1)

    def test_bullish_structure_produces_bias_bullish(self) -> None:
        candles = [c(i, i, i + 2, i - 1, i + 1) for i in range(1, 12)]
        self.assertEqual(infer_bias_from_swings(candles)["bias"], "BULLISH")

    def test_bearish_structure_produces_bias_bearish(self) -> None:
        candles = [c(i, 20 - i, 22 - i, 18 - i, 19 - i) for i in range(1, 12)]
        self.assertEqual(infer_bias_from_swings(candles)["bias"], "BEARISH")

    def test_ill_readable_range_does_not_force_bias(self) -> None:
        candles = [c(i, 10, 11, 9, 10) for i in range(1, 8)]
        self.assertIn(infer_bias_from_swings(candles)["bias"], {"RANGE", "UNKNOWN"})

    def test_swing_detection_returns_levels(self) -> None:
        candles = [c(1, 1, 2, 0, 1), c(2, 1, 5, 1, 4), c(3, 4, 4, 2, 3), c(4, 3, 6, 1, 5), c(5, 5, 5, 3, 4)]
        swings = detect_swings(candles, left=1, right=1)
        self.assertTrue(swings["highs"])


if __name__ == "__main__":
    unittest.main()

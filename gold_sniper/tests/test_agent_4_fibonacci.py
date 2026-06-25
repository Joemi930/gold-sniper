from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agents.agent_4_fibonacci import (
    calculate_ote_levels,
    calculate_ote_levels_anchored_to_poi,
    select_ote_swing_with_agent2_anchor,
)


def candles(count: int) -> list[dict]:
    start = datetime(2026, 4, 22, tzinfo=timezone.utc)
    return [
        {
            "time": start + timedelta(minutes=15 * index),
            "open": 105.0,
            "high": 110.0 + index,
            "low": 100.0 - index * 0.1,
            "close": 106.0,
        }
        for index in range(count)
    ]


class TestAgent4FibonacciAnchoredOte(unittest.TestCase):
    def test_long_ote_can_anchor_to_bullish_agent2_poi(self) -> None:
        poi = {
            "type": "BULLISH",
            "top": 105.0,
            "bottom": 100.0,
            "entry_zone_top": 104.0,
            "entry_zone_bottom": 100.0,
            "candle_index": 2,
            "fresh": True,
            "ob_score": 70.0,
        }
        swings = {"swing_highs": [{"index": 6, "price": 120.0}], "swing_lows": [{"index": 1, "price": 95.0}]}
        candles_15m = candles(8)
        candles_15m[2]["low"] = 100.0

        result = calculate_ote_levels_anchored_to_poi(candles_15m, swings, "LONG", poi)

        self.assertIsNotNone(result)
        self.assertEqual(result["anchor_mode"], "AGENT2_POI_ANCHORED")
        self.assertTrue(result["agent4_swing_contains_agent2_zone"])
        self.assertEqual(result["swing_low"], 95.0)
        self.assertEqual(result["swing_high"], 120.0)
        self.assertEqual(result["levels"], calculate_ote_levels(95.0, 120.0, "LONG"))

    def test_short_ote_can_anchor_to_bearish_agent2_poi(self) -> None:
        poi = {
            "type": "BEARISH",
            "top": 120.0,
            "bottom": 115.0,
            "entry_zone_top": 120.0,
            "entry_zone_bottom": 116.0,
            "candle_index": 2,
            "fresh": True,
            "ob_score": 72.0,
        }
        candles_15m = candles(8)
        candles_15m[2]["high"] = 120.0
        swings = {"swing_highs": [{"index": 1, "price": 125.0}], "swing_lows": [{"index": 6, "price": 100.0}]}

        result = calculate_ote_levels_anchored_to_poi(candles_15m, swings, "SHORT", poi)

        self.assertIsNotNone(result)
        self.assertEqual(result["anchor_mode"], "AGENT2_POI_ANCHORED")
        self.assertTrue(result["agent4_swing_contains_agent2_zone"])
        self.assertEqual(result["swing_low"], 100.0)
        self.assertEqual(result["swing_high"], 125.0)
        self.assertEqual(result["levels"], calculate_ote_levels(100.0, 125.0, "SHORT"))

    def test_missing_poi_uses_recent_swing_fallback(self) -> None:
        swings = {
            "swing_highs": [{"index": 5, "price": 114.0}],
            "swing_lows": [{"index": 4, "price": 100.0}],
        }

        result = select_ote_swing_with_agent2_anchor(candles(8), swings, "LONG", None)

        self.assertIsNotNone(result)
        self.assertEqual(result["anchor_mode"], "FALLBACK_RECENT_SWING")
        self.assertEqual(result["levels"], calculate_ote_levels(100.0, 114.0, "LONG"))

    def test_invalid_poi_falls_back_without_exception(self) -> None:
        poi = {"type": "BULLISH", "top": 105.0, "bottom": 100.0, "candle_index": 99}
        swings = {
            "swing_highs": [{"index": 5, "price": 114.0}],
            "swing_lows": [{"index": 4, "price": 100.0}],
        }

        result = select_ote_swing_with_agent2_anchor(candles(8), swings, "LONG", poi)

        self.assertIsNotNone(result)
        self.assertEqual(result["anchor_mode"], "NO_VALID_ANCHOR")


if __name__ == "__main__":
    unittest.main()

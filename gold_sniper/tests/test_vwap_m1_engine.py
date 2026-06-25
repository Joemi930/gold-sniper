from __future__ import annotations

import unittest

from gold_sniper.strategy.vwap_m1_engine import evaluate_vwap_m1_scalp


class TestVwapM1Engine(unittest.TestCase):
    def test_vwap_m1_reclaim_rejection_detected(self) -> None:
        candles = [
            {"open": 100, "high": 101, "low": 99, "close": 100, "tick_volume": 10},
            {"open": 99.7, "high": 100.2, "low": 99.3, "close": 99.8, "tick_volume": 10},
            {"open": 99.6, "high": 102.0, "low": 97.0, "close": 101.5, "tick_volume": 10},
        ]
        result = evaluate_vwap_m1_scalp(candles, ema_200_m15_bias="BULLISH")
        self.assertTrue(result.vwap_available)
        self.assertTrue(result.vwap_reclaim)
        self.assertTrue(result.rejection_candle)
        self.assertTrue(result.scalp_allowed)


if __name__ == "__main__":
    unittest.main()

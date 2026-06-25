from __future__ import annotations

import unittest

from gold_sniper.strategy.market_structure_engine import evaluate_market_structure


def _row(open_: float, high: float, low: float, close: float) -> dict:
    return {"open": open_, "high": high, "low": low, "close": close}


class TestMarketStructureEngine(unittest.TestCase):
    def test_bos_requires_body_close(self) -> None:
        candles = [_row(100, 102, 98, 100), _row(100, 101, 99, 100), _row(100, 104, 99, 103)]
        result = evaluate_market_structure(candles, protected_high=102, protected_low=98)
        self.assertTrue(result.bos)
        self.assertTrue(result.body_close_confirmed)
        self.assertEqual(result.bos_direction, "BULLISH")

    def test_wick_alone_does_not_validate_bos(self) -> None:
        candles = [_row(100, 102, 98, 100), _row(100, 101, 99, 100), _row(100, 104, 99, 101)]
        result = evaluate_market_structure(candles, protected_high=102, protected_low=98)
        self.assertFalse(result.bos)
        self.assertFalse(result.body_close_confirmed)

    def test_choch_requires_body_close(self) -> None:
        candles = [_row(100, 102, 98, 100), _row(100, 101, 99, 100), _row(100, 104, 99, 103)]
        result = evaluate_market_structure(candles, protected_high=102, protected_low=98)
        self.assertTrue(result.choch)
        self.assertEqual(result.choch_direction, "BULLISH")

    def test_sweep_requires_wick_reject_close_back_inside(self) -> None:
        candles = [_row(100, 102, 98, 100), _row(100, 101, 99, 100), _row(100, 104, 99, 101)]
        result = evaluate_market_structure(candles, protected_high=102, protected_low=98)
        self.assertTrue(result.sweep)
        self.assertEqual(result.sweep_side, "BUY_SIDE")

    def test_double_choch_range_filter(self) -> None:
        candles = [_row(100, 102, 98, 100), _row(100, 101, 99, 100), _row(100, 97, 96, 97)]
        result = evaluate_market_structure(candles, protected_high=102, protected_low=98, previous_choch_direction="BULLISH")
        self.assertTrue(result.double_choch_range)


if __name__ == "__main__":
    unittest.main()

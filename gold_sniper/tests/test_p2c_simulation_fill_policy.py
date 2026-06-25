from __future__ import annotations

import unittest

from replay.offline_trade_simulator import evaluate_limit_entry_fill


class TestP2cSimulationFillPolicy(unittest.TestCase):
    def test_buy_limit_uses_ask_low_with_spread(self) -> None:
        candle = {"high": 101.0, "low": 99.75}

        self.assertTrue(evaluate_limit_entry_fill(side="BUY", limit_price=100.0, candle=candle, spread=0.20))
        self.assertFalse(evaluate_limit_entry_fill(side="BUY", limit_price=99.90, candle=candle, spread=0.20))

    def test_sell_limit_uses_bid_high_with_spread(self) -> None:
        candle = {"high": 100.25, "low": 99.0}

        self.assertTrue(evaluate_limit_entry_fill(side="SELL", limit_price=100.0, candle=candle, spread=0.20))
        self.assertFalse(evaluate_limit_entry_fill(side="SELL", limit_price=100.10, candle=candle, spread=0.20))

    def test_invalid_side_does_not_fill(self) -> None:
        candle = {"high": 101.0, "low": 99.0}

        self.assertFalse(evaluate_limit_entry_fill(side="FLAT", limit_price=100.0, candle=candle, spread=0.0))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from replay.offline_trade_simulator import simulate_order_or_trade


class TestP2cSimulationMissedEntry(unittest.TestCase):
    def test_buy_limit_missed_when_ask_never_touches_limit(self) -> None:
        order = {
            "side": "BUY",
            "entry_type": "LIMIT",
            "entry_price": 100.0,
            "spread": 0.50,
            "entry_expiry_bars": 2,
        }
        candles = [
            {"time": "t1", "open": 100.50, "high": 101.0, "low": 100.25, "close": 100.80},
            {"time": "t2", "open": 100.70, "high": 101.2, "low": 100.30, "close": 100.90},
        ]

        result = simulate_order_or_trade(order, candles)

        self.assertEqual(result["status"], "MISSED_ENTRY")
        self.assertEqual(result["reason"], "LIMIT_NOT_TOUCHED_WITH_SPREAD")
        self.assertFalse(result["filled"])
        self.assertEqual(result["pnl"], 0.0)
        self.assertEqual(result["exposure"], 0.0)
        self.assertEqual(result["last_checked_candle_time"], "t2")

    def test_buy_limit_filled_when_ask_low_touches_limit(self) -> None:
        order = {
            "side": "BUY",
            "entry_type": "LIMIT",
            "entry_price": 100.0,
            "spread": 0.20,
            "entry_expiry_bars": 2,
        }
        candles = [{"time": "t1", "open": 100.50, "high": 101.0, "low": 99.75, "close": 100.20}]

        result = simulate_order_or_trade(order, candles)

        self.assertNotEqual(result["status"], "MISSED_ENTRY")
        self.assertTrue(result["filled"])

    def test_sell_limit_missed_when_bid_never_touches_limit(self) -> None:
        order = {
            "side": "SELL",
            "entry_type": "LIMIT",
            "entry_price": 100.0,
            "spread": 0.50,
            "entry_expiry_bars": 2,
        }
        candles = [
            {"time": "t1", "open": 99.50, "high": 99.75, "low": 98.80, "close": 99.20},
            {"time": "t2", "open": 99.60, "high": 99.80, "low": 98.90, "close": 99.10},
        ]

        result = simulate_order_or_trade(order, candles)

        self.assertEqual(result["status"], "MISSED_ENTRY")
        self.assertFalse(result["filled"])

    def test_entry_expiry_prevents_late_fill(self) -> None:
        order = {
            "side": "BUY",
            "entry_type": "LIMIT",
            "entry_price": 100.0,
            "spread": 0.10,
            "entry_expiry_bars": 1,
        }
        candles = [
            {"time": "t1", "open": 101.00, "high": 101.0, "low": 100.50, "close": 100.80},
            {"time": "t2", "open": 100.00, "high": 100.5, "low": 99.80, "close": 100.10},
        ]

        result = simulate_order_or_trade(order, candles)

        self.assertEqual(result["status"], "MISSED_ENTRY")
        self.assertEqual(result["last_checked_candle_time"], "t1")


if __name__ == "__main__":
    unittest.main()

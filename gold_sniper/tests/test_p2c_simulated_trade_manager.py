from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.blackboard import BlackBoard
from replay.execution_model import BrokerExecutionProfile, ReplayExecutionModel
from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager


def candle(offset: int, open_: float = 2000.0, high: float = 2001.0, low: float = 1999.0, close: float = 2000.0) -> dict:
    return {
        "time": datetime(2026, 6, 1, tzinfo=timezone.utc) + timedelta(minutes=offset),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
        "tick_volume": 1.0,
    }


class TestP2cSimulatedTradeManager(unittest.IsolatedAsyncioTestCase):
    async def test_opening_buy_applies_spread_and_slippage(self):
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", {"signal": "BUY", "entry_price": 2000.0, "stop_loss": 1975.0})

        events = await manager.on_candle(candle(0))

        self.assertEqual(events[0]["event"], "open")
        self.assertEqual(events[0]["requested_entry_price"], 2000.0)
        self.assertEqual(events[0]["entry_price"], 2015.0)
        self.assertEqual(events[0]["entry_spread_points"], 20.0)
        self.assertEqual(events[0]["entry_slippage_points"], 5.0)
        self.assertEqual(events[0]["fill_model"], "conservative_intrabar")

    async def test_tp1_partial_protected_sl_and_tp2(self):
        """P3: two-leg trade — leg_1 closes at TP1, leg_2 TP at entry+0.5R then TP2.
        Entry=2015, SL=1975, risk=40, leg_1 TP=2055, leg_2 TP=2095, protected SL=2035."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", {"signal": "BUY", "entry_price": 2000.0, "stop_loss": 1975.0})
        await manager.on_candle(candle(0))

        # Candle 1: high=2056 hits TP1=2055, low=2040 stays above protected SL=2035
        tp1_events = await manager.on_candle(candle(1, high=2056.0, low=2040.0))
        trade = next(iter(manager.active_positions.values()))
        # Candle 2: high=2096 hits TP2=2095, low=2070 stays above protected SL=2035
        tp2_events = await manager.on_candle(candle(2, high=2096.0, low=2070.0))
        summary = manager.summary()

        # P3: leg_1 closes at TP1 (leg_close event), leg_2 SL moved to protected
        self.assertEqual([e["event"] for e in tp1_events], ["leg_close"])
        self.assertEqual(tp1_events[0]["reason"], "TP1")
        self.assertEqual(tp1_events[0]["leg"], 1)
        # P3: protected SL = entry + 0.5R = 2015 + 0.5*40 = 2035.0
        self.assertEqual(trade["leg_2"]["protected_sl"], 2035.0)
        # P3: leg_2 closes at TP2, parent closes
        close_events = [e for e in tp2_events if e["event"] == "close"]
        self.assertEqual(len(close_events), 1)
        self.assertEqual(close_events[0]["reason"], "PARENT_CLOSE")
        self.assertEqual(close_events[0]["leg_2_exit_reason"], "TP2")
        self.assertTrue(summary["p2c_faithful_simulation"])
        self.assertTrue(summary["execution_model_valid"])

    async def test_sell_protected_sl_is_entry_minus_05r(self):
        """P3: SELL protected SL = entry - 0.5R (was 0.01R in P2).
        Entry=1985, SL=2025, risk=40, leg_1 TP=1945, protected SL=1965."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", {"signal": "SELL", "entry_price": 2000.0, "stop_loss": 2025.0})
        await manager.on_candle(candle(0))

        # Candle 1: low hits TP1, high stays below protected SL to keep trade alive
        await manager.on_candle(candle(1, high=1960.0, low=1944.0))
        trade = next(iter(manager.active_positions.values()))

        self.assertEqual(trade["entry_price"], 1985.0)
        # P3: protected_sl = entry - 0.5*risk = 1985 - 0.5*40 = 1965.0
        self.assertEqual(trade["leg_2"]["protected_sl"], 1965.0)

    def test_zero_cost_model_forbidden_when_required(self):
        zero_cost = ReplayExecutionModel(profile=BrokerExecutionProfile(avg_spread_pips=0.0))

        with self.assertRaises(ValueError):
            SimulatedTradeManager(BlackBoard(), SimulatedTradeConfig(execution_model=zero_cost, require_execution_model=True))


if __name__ == "__main__":
    unittest.main()

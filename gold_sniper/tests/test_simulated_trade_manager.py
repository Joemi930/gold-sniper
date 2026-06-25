from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.blackboard import BlackBoard
from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager


def candle(offset: int, open_: float = 2000.0, high: float = 2001.0, low: float = 1999.0, close: float = 2000.0) -> dict:
    return {
        "time": datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=offset),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
        "tick_volume": 1.0,
    }


BUY_SIGNAL = {
    "signal": "BUY",
    "entry_price": 2000.0,
    "stop_loss": 1975.0,
}


class TestSimulatedTradeManager(unittest.IsolatedAsyncioTestCase):
    async def test_opens_trade_from_blackboard_signal(self) -> None:
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", dict(BUY_SIGNAL))

        events = await manager.on_candle(candle(0))

        self.assertEqual(events[0]["event"], "open")
        self.assertEqual(len(board.read_sync("active_trades")), 1)
        self.assertEqual(board.read_sync("trade_signals"), {})

    async def test_closes_buy_on_tp(self) -> None:
        """P3: BUY two-leg — leg_1 TP1, leg_2 TP2, parent close.
        Entry=2015, SL=1975, risk=40, leg_1 TP=2055, leg_2 TP=2095, protected SL=2035."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", dict(BUY_SIGNAL))
        await manager.on_candle(candle(0))

        # Candle 1: high hits TP1, low stays above protected SL
        events = await manager.on_candle(candle(1, high=2056.0, low=2040.0))
        # Candle 2: high hits TP2, low stays above protected SL
        close_events = await manager.on_candle(candle(2, high=2096.0, low=2070.0))

        # P3: leg_1 TP1 → leg_close, then leg_2 TP2 + parent close
        self.assertEqual([e["event"] for e in events], ["leg_close"])
        self.assertEqual(events[0]["reason"], "TP1")
        close_evt = [e for e in close_events if e["event"] == "close"][0]
        self.assertEqual(close_evt["reason"], "PARENT_CLOSE")
        self.assertEqual(close_evt["leg_2_exit_reason"], "TP2")
        self.assertGreater(manager.summary()["pnl"], 0)

    async def test_closes_buy_on_sl(self) -> None:
        """P3: BUY two-leg — both legs SL → parent close."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", dict(BUY_SIGNAL))
        await manager.on_candle(candle(0))

        # Candle: low hits SL at 1975 (below entry, not touching TP levels)
        events = await manager.on_candle(candle(1, high=2016.0, low=1974.0))

        # P3: both legs close at SL → leg_close events + parent close
        sl_leg_events = [e for e in events if e["event"] == "leg_close" and e["reason"] == "SL"]
        self.assertEqual(len(sl_leg_events), 2)  # both legs
        parent_close = [e for e in events if e["event"] == "close"][0]
        self.assertLess(parent_close["pnl"], 0)

    async def test_same_candle_sl_and_tp_chooses_sl_first(self) -> None:
        """P3: intrabar conservative — SL wins over TP1."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", dict(BUY_SIGNAL))
        await manager.on_candle(candle(0))

        events = await manager.on_candle(candle(1, high=2096.0, low=1974.0))

        # P3: both legs hit SL (conservative), parent closes
        sl_legs = [e for e in events if e.get("reason") == "SL"]
        self.assertGreaterEqual(len(sl_legs), 2)
        parent_close = [e for e in events if e["event"] == "close"][0]
        self.assertLess(parent_close["pnl"], 0)

    async def test_new_trade_is_not_evaluated_on_opening_candle(self) -> None:
        """P3: trade not evaluated on its opening candle."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", dict(BUY_SIGNAL))

        events = await manager.on_candle(candle(0, high=2096.0, low=1974.0))

        self.assertEqual([e["event"] for e in events], ["open"])
        self.assertEqual(manager.summary()["closed_trades"], 0)
        self.assertEqual(len(board.read_sync("active_trades")), 1)

        next_events = await manager.on_candle(candle(1, high=2096.0, low=1974.0))

        # P3: next candle evaluation triggers SL on both legs
        parent_close = [e for e in next_events if e["event"] == "close"][0]
        self.assertEqual(parent_close["leg_1_exit_reason"], "SL")
        self.assertEqual(parent_close["leg_2_exit_reason"], "SL")

    async def test_closes_sell_on_tp(self) -> None:
        """P3: SELL two-leg — leg_1 TP1, leg_2 TP2, parent close.
        Entry=1985, SL=2025, risk=40, leg_1 TP=1945, leg_2 TP=1905, protected SL=1965."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write(
            "trade_signals",
            {"signal": "SELL", "entry_price": 2000.0, "stop_loss": 2025.0},
        )
        await manager.on_candle(candle(0))

        # Candle 1: low hits TP1, high stays below protected SL (1965)
        events = await manager.on_candle(candle(1, high=1960.0, low=1944.0))
        # Candle 2: low hits TP2, high stays below protected SL
        close_events = await manager.on_candle(candle(2, high=1960.0, low=1904.0))

        # P3: leg_1 TP1 → leg_close, then leg_2 TP2 + parent close
        self.assertEqual(events[0]["event"], "leg_close")
        self.assertEqual(events[0]["reason"], "TP1")
        close_evt = [e for e in close_events if e["event"] == "close"][0]
        self.assertEqual(close_evt["leg_2_exit_reason"], "TP2")
        self.assertGreater(manager.summary()["pnl"], 0)

    async def test_buy_tp1_then_protected_sl(self) -> None:
        """P3: BUY — leg_1 TP1 then leg_2 protected SL at entry+0.5R.
        Entry=2015, SL=1975, risk=40, leg_1 TP=2055, leg_2 TP=2095, protected SL=2035."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", dict(BUY_SIGNAL))
        await manager.on_candle(candle(0))

        # Candle 1: leg_1 hits TP1, low stays above protected SL
        await manager.on_candle(candle(1, high=2056.0, low=2040.0))
        # Candle 2: price falls to hit protected SL at 2035.0 (but not full SL)
        events = await manager.on_candle(candle(2, high=2040.0, low=2034.9))

        # P3: leg_2 closes at PROTECTED_SL
        leg2_close = [e for e in events if e.get("leg") == 2 and e.get("reason") == "PROTECTED_SL"]
        self.assertEqual(len(leg2_close), 1)
        # Protected SL = 2035.0 (entry + 0.5R)
        self.assertAlmostEqual(leg2_close[0]["requested_price"], 2035.0, places=1)

    async def test_sell_tp1_then_protected_sl(self) -> None:
        """P3: SELL — leg_1 TP1 then leg_2 protected SL at entry-0.5R.
        Entry=1985, SL=2025, risk=40, leg_1 TP=1945, leg_2 TP=1905, protected SL=1965."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write(
            "trade_signals",
            {"signal": "SELL", "entry_price": 2000.0, "stop_loss": 2025.0},
        )
        await manager.on_candle(candle(0))

        # Candle 1: leg_1 hits TP1, high stays below protected SL
        await manager.on_candle(candle(1, high=1960.0, low=1944.0))
        # Candle 2: price rises to hit protected SL at 1965.0
        events = await manager.on_candle(candle(2, high=1965.1, low=1950.0))

        # P3: leg_2 closes at PROTECTED_SL
        leg2_close = [e for e in events if e.get("leg") == 2 and e.get("reason") == "PROTECTED_SL"]
        self.assertEqual(len(leg2_close), 1)
        # Protected SL ≈ 1965.0
        self.assertAlmostEqual(leg2_close[0]["requested_price"], 1965.0, places=1)

    async def test_sell_same_candle_sl_and_tp1_chooses_sl_first(self) -> None:
        """P3: SELL intrabar conservative — SL wins over TP1."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write(
            "trade_signals",
            {"signal": "SELL", "entry_price": 2000.0, "stop_loss": 2025.0},
        )
        await manager.on_candle(candle(0))

        events = await manager.on_candle(candle(1, high=2026.0, low=1944.0))

        # P3: both legs hit SL (conservative)
        sl_legs = [e for e in events if e.get("reason") == "SL"]
        self.assertGreaterEqual(len(sl_legs), 2)
        parent_close = [e for e in events if e["event"] == "close"][0]
        self.assertLess(parent_close["pnl"], 0)

    async def test_summary_counts_partial_and_targets(self) -> None:
        """P3: summary counts parent trades and leg events correctly.
        Entry=2015, SL=1975, risk=40, leg_1 TP=2055, leg_2 TP=2095, protected SL=2035."""
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write("trade_signals", dict(BUY_SIGNAL))
        await manager.on_candle(candle(0))
        # Candle 1: high hits TP1, low stays above protected SL (2035)
        await manager.on_candle(candle(1, high=2056.0, low=2040.0))
        # Candle 2: high hits TP2, low stays above protected SL
        await manager.on_candle(candle(2, high=2096.0, low=2070.0))

        summary = manager.summary()

        self.assertEqual(summary["tp1_hit_count"], 1)
        self.assertEqual(summary["tp2_hit_count"], 1)
        self.assertEqual(summary["parent_trades"], 1)
        self.assertEqual(summary["wins"], 1)
        self.assertGreater(summary["pnl"], 0)

    async def test_rejects_invalid_levels(self) -> None:
        board = BlackBoard()
        manager = SimulatedTradeManager(board)
        await board.write(
            "trade_signals",
            {"signal": "BUY", "entry_price": 2000.0, "stop_loss": 2016.0},
        )

        events = await manager.on_candle(candle(0))

        self.assertEqual(events[0]["event"], "rejected")
        self.assertEqual(events[0]["reason"], "invalid_risk_points")

    async def test_tier_tp3_and_be_plus_flow(self) -> None:
        """P3: tier-prefix two-leg trade — leg_1 TP1, leg_2 TP2, parent close.
        Entry=2015, SL=1975, risk=40, leg_1 TP=2055, leg_2 TP=2095, protected SL=2035."""
        board = BlackBoard()
        manager = SimulatedTradeManager(
            board,
            SimulatedTradeConfig(equity_initial=100.0, write_blackboard_positions=False, event_prefix="tier"),
        )
        signal = {
            "signal": "BUY",
            "entry_price": 2000.0,
            "stop_loss": 1975.0,
            "volume": 0.2,
            "risk_cash": 1.0,
            "tier": "STANDARD_PAPER",
            "risk_pct": 1.0,
        }

        self.assertEqual((await manager.on_candle_with_signal(candle(0), signal))[0]["event"], "tier_trade_open")
        # Candle 1: high=2056 hits TP1=2055, low=2040 stays above protected SL=2035
        tp1_events = await manager.on_candle_with_signal(candle(1, high=2056.0, low=2040.0), None)
        # Candle 2: high=2096 hits TP2=2095, low=2070 stays above protected SL
        tp2_events = await manager.on_candle_with_signal(candle(2, high=2096.0, low=2070.0), None)
        summary = manager.summary()

        # P3: leg_1 TP1 → tier_leg_close, leg_2 TP2 + parent close
        self.assertEqual([e["event"] for e in tp1_events], ["tier_leg_close"])
        self.assertEqual(tp1_events[0]["reason"], "TP1")
        self.assertEqual(tp1_events[0]["tier"], "STANDARD_PAPER")
        # leg_2 TP2 + parent close
        tp2_close = [e for e in tp2_events if e["event"] == "tier_trade_close"][0]
        self.assertEqual(tp2_close["leg_2_exit_reason"], "TP2")
        self.assertEqual(tp2_close["tier"], "STANDARD_PAPER")
        self.assertEqual(summary["tp1_hit_count"], 1)
        self.assertEqual(summary["tp2_hit_count"], 1)


if __name__ == "__main__":
    unittest.main()

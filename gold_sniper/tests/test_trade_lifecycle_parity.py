"""P4.2 — TradeLifecycleSimulator tests (lifecycle parity)."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from gold_sniper.replay.trade_lifecycle_simulator import (
    LifecycleEvent,
    TradeLifecycleSimulator,
    process_lifecycle_on_candle,
)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestLifecycleEvent(unittest.TestCase):

    def test_event_to_dict(self):
        evt = LifecycleEvent(
            event="tp1_hit",
            time=_utc("2025-12-08T10:30:00Z"),
            ticket=1,
            leg=1,
            price=2655.0,
            pnl_r=0.5,
            reason="TP1_HIT",
        )
        d = evt.to_dict()
        self.assertEqual(d["event"], "tp1_hit")
        self.assertEqual(d["leg"], 1)
        self.assertAlmostEqual(d["pnl_r"], 0.5)


class TestTradeLifecycleSimulator(unittest.TestCase):

    def setUp(self):
        self.sim = TradeLifecycleSimulator()

    def test_initial_state_empty(self):
        self.assertEqual(self.sim.open_count, 0)
        self.assertEqual(self.sim.event_count, 0)

    def test_open_trade_adds_to_list(self):
        trade = {"ticket": 1, "side": "BUY", "sl_price": 2640.0,
                 "tp1_price": 2655.0, "tp2_price": 2670.0,
                 "protected_sl_price": 2650.0, "risk_r": 1.0,
                 "tp1_rr": 0.5, "tp2_rr": 1.5}
        self.sim.open_trade(trade)
        self.assertEqual(self.sim.open_count, 1)

    def test_tp1_hit_long(self):
        """Long trade: price rises and hits TP1."""
        trade = {"ticket": 1, "side": "BUY", "sl_price": 2640.0,
                 "tp1_price": 2655.0, "tp2_price": 2670.0,
                 "protected_sl_price": 2652.0, "risk_r": 1.0,
                 "tp1_rr": 0.5, "tp2_rr": 1.5, "leg1_closed": False}
        self.sim.open_trade(trade)

        candle = {"time": _utc("2025-12-08T10:30:00Z"),
                  "open": 2652.0, "high": 2656.0, "low": 2651.0, "close": 2655.0}
        events = self.sim.on_candle(candle)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event, "tp1_hit")
        self.assertEqual(events[0].leg, 1)
        # Trade should still be open (leg1 closed, leg2 running)
        self.assertEqual(self.sim.open_count, 1)

    def test_sl_hit_long(self):
        """Long trade: price drops and hits SL."""
        trade = {"ticket": 1, "side": "BUY", "sl_price": 2640.0,
                 "tp1_price": 2655.0, "tp2_price": 2670.0,
                 "protected_sl_price": 2652.0, "risk_r": 1.0,
                 "tp1_rr": 0.5, "tp2_rr": 1.5, "leg1_closed": False}
        self.sim.open_trade(trade)

        candle = {"time": _utc("2025-12-08T10:30:00Z"),
                  "open": 2645.0, "high": 2646.0, "low": 2639.0, "close": 2641.0}
        events = self.sim.on_candle(candle)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event, "sl_hit")
        # Trade should be closed
        self.assertEqual(self.sim.open_count, 0)

    def test_tp1_then_tp2_long(self):
        """Long: TP1 hits (leg1 closes), then TP2 hits (trade closes)."""
        trade = {"ticket": 1, "side": "BUY", "sl_price": 2640.0,
                 "tp1_price": 2655.0, "tp2_price": 2670.0,
                 "protected_sl_price": 2650.0, "risk_r": 1.0,
                 "tp1_rr": 0.5, "tp2_rr": 1.5, "leg1_closed": False}
        self.sim.open_trade(trade)

        # First candle: TP1 hit
        c1 = {"time": _utc("2025-12-08T10:30:00Z"),
              "open": 2652.0, "high": 2656.0, "low": 2651.0, "close": 2655.0}
        e1 = self.sim.on_candle(c1)
        self.assertEqual(e1[0].event, "tp1_hit")
        self.assertEqual(self.sim.open_count, 1)  # still open, leg2 running

        # Second candle: TP2 hit
        c2 = {"time": _utc("2025-12-08T11:00:00Z"),
              "open": 2665.0, "high": 2672.0, "low": 2664.0, "close": 2670.0}
        e2 = self.sim.on_candle(c2)
        self.assertEqual(e2[0].event, "tp2_hit")
        self.assertEqual(self.sim.open_count, 0)  # fully closed

    def test_tp1_then_protected_sl_short(self):
        """Short: TP1 hits, then protected SL hit (trade closes)."""
        trade = {"ticket": 1, "side": "SELL", "sl_price": 2660.0,
                 "tp1_price": 2645.0, "tp2_price": 2630.0,
                 "protected_sl_price": 2652.0, "risk_r": 1.0,
                 "tp1_rr": 0.5, "tp2_rr": 1.5, "leg1_closed": False}
        self.sim.open_trade(trade)

        # First candle: TP1 hit
        c1 = {"time": _utc("2025-12-08T10:30:00Z"),
              "open": 2648.0, "high": 2649.0, "low": 2644.0, "close": 2646.0}
        e1 = self.sim.on_candle(c1)
        self.assertEqual(e1[0].event, "tp1_hit")
        self.assertEqual(self.sim.open_count, 1)

        # Second candle: protected SL hit (price goes up)
        c2 = {"time": _utc("2025-12-08T11:00:00Z"),
              "open": 2650.0, "high": 2653.0, "low": 2649.0, "close": 2652.0}
        e2 = self.sim.on_candle(c2)
        self.assertEqual(e2[0].event, "protected_sl_hit")
        self.assertEqual(self.sim.open_count, 0)

    def test_no_event_when_price_inside_range(self):
        """No lifecycle events when price stays within SL and TP."""
        trade = {"ticket": 1, "side": "BUY", "sl_price": 2640.0,
                 "tp1_price": 2655.0, "tp2_price": 2670.0,
                 "protected_sl_price": 2652.0, "risk_r": 1.0,
                 "tp1_rr": 0.5, "tp2_rr": 1.5, "leg1_closed": False}
        self.sim.open_trade(trade)

        candle = {"time": _utc("2025-12-08T10:30:00Z"),
                  "open": 2645.0, "high": 2650.0, "low": 2644.0, "close": 2648.0}
        events = self.sim.on_candle(candle)
        self.assertEqual(len(events), 0)
        self.assertEqual(self.sim.open_count, 1)

    def test_flush_events_clears(self):
        trade = {"ticket": 1, "side": "BUY", "sl_price": 2640.0,
                 "tp1_price": 2655.0, "tp2_price": 2670.0,
                 "protected_sl_price": 2652.0, "risk_r": 1.0,
                 "tp1_rr": 0.5, "tp2_rr": 1.5, "leg1_closed": False}
        self.sim.open_trade(trade)

        candle = {"time": _utc("2025-12-08T10:30:00Z"),
                  "open": 2645.0, "high": 2646.0, "low": 2639.0, "close": 2641.0}
        self.sim.on_candle(candle)
        self.assertEqual(self.sim.event_count, 1)

        flushed = self.sim.flush_events()
        self.assertEqual(len(flushed), 1)
        self.assertEqual(self.sim.event_count, 0)


if __name__ == "__main__":
    unittest.main()

"""P4: Summary consistency tests — verify metrics are correctly computed and propagated."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager


class TestReplaySummaryConsistency(unittest.TestCase):
    """Verify that trade-manager summary() returns all required P4 metrics."""

    def setUp(self):
        self.blackboard = MagicMock()
        self.blackboard._lock = AsyncMock()
        self.blackboard._data = {
            "meta": {},
            "market_data": {"candles": {}},
            "active_trades": {},
            "positions": {"open_positions": []},
        }
        self.blackboard.read_sync = MagicMock(return_value=[])
        self.blackboard.write = AsyncMock()
        self.blackboard.update_dict = AsyncMock()
        self.blackboard.update_market = AsyncMock()
        self.blackboard.notify_candle_close = AsyncMock()

        self.config = SimulatedTradeConfig(equity_initial=100.0)
        self.tm = SimulatedTradeManager(self.blackboard, self.config)

    # ── P4.2: all required keys present ──────────────────────────────

    def test_summary_has_p4_metric_keys(self):
        """Verify summary dict contains all P4-required top-level keys."""
        s = self.tm.summary()
        required = [
            "trades_per_day", "active_trading_days",
            "winrate_full_win", "winrate_tp1_touch",
            "cost_drag_R", "full_sl_count", "sl_hit_count",
            "first_trade_time", "last_trade_time",
            "full_win_count", "tp1_touch_count",
            "tp1_tp2_avg_net_R", "tp1_tp2_avg_pure_R",
            "tp1_protected_avg_net_R", "tp1_protected_avg_pure_R",
            "avg_cost_drag_per_trade_R",
            "tp1_tp2_count", "tp1_protected_count",
        ]
        for key in required:
            self.assertIn(key, s, f"Missing summary key: {key}")

    # ── P4.2: cost_drag_R = pure - net ──────────────────────────────

    def test_cost_drag_r_computation(self):
        """cost_drag_R must equal pure_expectancy_R - expectancy_R."""
        s = self.tm.summary()
        pure = s.get("pure_expectancy_R", 0.0)
        net = s.get("expectancy_R", 0.0)
        expected = round(pure - net, 6)
        self.assertEqual(s["cost_drag_R"], expected,
                         f"cost_drag_R={s['cost_drag_R']} != pure-net={expected}")

    # ── P4.2: full_sl_count ≤ parent_trades ─────────────────────────

    def test_full_sl_count_consistency(self):
        """parent_full_sl_count must be ≤ parent_trades (can't exceed total)."""
        s = self.tm.summary()
        parent_trades = s.get("parent_trades", 0)
        full_sl = s.get("full_sl_count", 0)
        self.assertLessEqual(full_sl, parent_trades,
                             f"full_sl_count={full_sl} > parent_trades={parent_trades}")

    # ── P4.2: winrate computation ────────────────────────────────────

    def test_winrate_full_win_consistency(self):
        """winrate_full_win must be ≤ winrate_tp1_touch."""
        s = self.tm.summary()
        fw = s.get("winrate_full_win", 0.0)
        t1 = s.get("winrate_tp1_touch", 0.0)
        self.assertLessEqual(fw, t1 + 0.01,
                             f"full_win={fw} > tp1_touch={t1}")

    # ── P4.2: empty state returns zero-values, not None ──────────────

    def test_empty_state_returns_sane_defaults(self):
        s = self.tm.summary()
        self.assertEqual(s["trades_per_day"], 0.0)
        self.assertEqual(s["winrate_full_win"], 0.0)
        self.assertEqual(s["winrate_tp1_touch"], 0.0)
        self.assertEqual(s["cost_drag_R"], 0.0)
        self.assertIsNone(s["first_trade_time"])
        self.assertIsNone(s["last_trade_time"])


if __name__ == "__main__":
    unittest.main()

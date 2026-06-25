"""P2-E Phase18 — Shadow Trade Lifecycle & Entry Quality Audit tests.

Tests:
  1. P1 ENTER opens with live-like sizing
  2. Grade C does not open
  3. Daily limit — standard (2 B trades open, 3rd rejected)
  4. Daily limit — exceptional slot (A trade as 3rd opens, 4th rejected)
  5. TP1 partial close + BE+ activation
  6. TP2 full close
  7. SL close
  8. Open trade at replay end → open_trades_end_count
  9. Performance summary separates realized/unrealized
  10. No forced ENTER (WATCH_ONLY → no shadow open)
  11. Risk positive without enter_eligible → no shadow open
  12. Existing Phase17 tests pass (regression check)
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from gold_sniper.replay.shadow_live_policy import (
    GRADE_RISK_PCT,
    DailyTradeCounter,
    ShadowLivePolicy,
    can_open_shadow_trade,
    compute_shadow_position_size,
    grade_allows_daily_exception,
    grade_is_executable,
    normalize_grade,
    record_trade_opened,
    risk_pct_for_grade,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests: shadow_live_policy
# ═══════════════════════════════════════════════════════════════════════════════


class TestShadowLivePolicyGradeMapping(unittest.TestCase):
    def test_normalize_grade_standard(self):
        self.assertEqual(normalize_grade("A"), "A")
        self.assertEqual(normalize_grade("B"), "B")
        self.assertEqual(normalize_grade("C"), "C")
        self.assertEqual(normalize_grade("D"), "D")

    def test_normalize_grade_a_plus(self):
        self.assertEqual(normalize_grade("A+"), "A_PLUS")
        self.assertEqual(normalize_grade("A_PLUS"), "A_PLUS")
        self.assertEqual(normalize_grade("a+"), "A_PLUS")

    def test_normalize_grade_unknown(self):
        self.assertEqual(normalize_grade("X"), "UNKNOWN")
        self.assertEqual(normalize_grade(None), "UNKNOWN")
        self.assertEqual(normalize_grade(""), "UNKNOWN")

    def test_risk_pct_values(self):
        self.assertEqual(risk_pct_for_grade("A_PLUS"), 1.00)
        self.assertEqual(risk_pct_for_grade("A+"), 1.00)
        self.assertEqual(risk_pct_for_grade("A"), 0.75)
        self.assertEqual(risk_pct_for_grade("B"), 0.50)
        self.assertEqual(risk_pct_for_grade("C"), 0.00)
        self.assertEqual(risk_pct_for_grade("D"), 0.00)
        self.assertEqual(risk_pct_for_grade("UNKNOWN"), 0.00)

    def test_grade_is_executable(self):
        self.assertTrue(grade_is_executable("A+"))
        self.assertTrue(grade_is_executable("A"))
        self.assertTrue(grade_is_executable("B"))
        self.assertFalse(grade_is_executable("C"))
        self.assertFalse(grade_is_executable("D"))
        self.assertFalse(grade_is_executable("UNKNOWN"))

    def test_grade_allows_exception(self):
        self.assertTrue(grade_allows_daily_exception("A+"))
        self.assertTrue(grade_allows_daily_exception("A"))
        self.assertFalse(grade_allows_daily_exception("B"))
        self.assertFalse(grade_allows_daily_exception("C"))


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1: P1 ENTER opens with live-like sizing
# ═══════════════════════════════════════════════════════════════════════════════


class TestLiveSizing(unittest.TestCase):
    def test_compute_sizing_grade_a(self):
        """Given ENTER_REDUCED A, equity=100, risk_pct=0.75, stop distance=10 points"""
        sizing = compute_shadow_position_size(
            equity=100.0,
            grade="A",
            entry=2650.0,
            stop_loss=2640.0,
        )
        self.assertEqual(sizing["risk_pct"], 0.75)
        self.assertEqual(sizing["risk_cash"], 0.75)  # 100 * 0.75 / 100
        self.assertEqual(sizing["risk_points"], 10.0)
        self.assertEqual(sizing["volume"], 0.075)  # 0.75 / 10

    def test_compute_sizing_grade_b(self):
        sizing = compute_shadow_position_size(
            equity=200.0,
            grade="B",
            entry=2650.0,
            stop_loss=2645.0,
        )
        self.assertEqual(sizing["risk_pct"], 0.50)
        self.assertEqual(sizing["risk_cash"], 1.0)  # 200 * 0.50 / 100
        self.assertEqual(sizing["risk_points"], 5.0)
        self.assertEqual(sizing["volume"], 0.2)  # 1.0 / 5

    def test_compute_sizing_grade_a_plus(self):
        sizing = compute_shadow_position_size(
            equity=100.0,
            grade="A_PLUS",
            entry=2650.0,
            stop_loss=2640.0,
        )
        self.assertEqual(sizing["risk_pct"], 1.0)
        self.assertEqual(sizing["risk_cash"], 1.0)  # 100 * 1.0 / 100
        self.assertEqual(sizing["volume"], 0.1)  # 1.0 / 10


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2: C does not open (grade not executable)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGradeCBlocked(unittest.TestCase):
    def test_c_not_executable(self):
        self.assertFalse(grade_is_executable("C"))

    def test_c_sizing_raises(self):
        with self.assertRaises(ValueError) as ctx:
            compute_shadow_position_size(
                equity=100.0,
                grade="C",
                entry=2650.0,
                stop_loss=2640.0,
            )
        self.assertIn("GRADE_NOT_EXECUTABLE", str(ctx.exception))

    def test_d_sizing_raises(self):
        with self.assertRaises(ValueError) as ctx:
            compute_shadow_position_size(
                equity=100.0,
                grade="D",
                entry=2650.0,
                stop_loss=2640.0,
            )
        self.assertIn("GRADE_NOT_EXECUTABLE", str(ctx.exception))


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3: Daily limit standard
# ═══════════════════════════════════════════════════════════════════════════════


class TestDailyLimitStandard(unittest.TestCase):
    def setUp(self):
        self.policy = ShadowLivePolicy()
        self.counters: dict[str, DailyTradeCounter] = {}

    def test_first_two_b_trades_open(self):
        # First B trade
        allowed, reason = can_open_shadow_trade(
            policy=self.policy,
            counters=self.counters,
            candle_time_str="2026-06-04T10:00:00+00:00",
            grade="B",
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "STANDARD_SLOT_AVAILABLE")
        record_trade_opened(
            counters=self.counters,
            candle_time_str="2026-06-04T10:00:00+00:00",
            reason=reason,
        )

        # Second B trade
        allowed2, reason2 = can_open_shadow_trade(
            policy=self.policy,
            counters=self.counters,
            candle_time_str="2026-06-04T10:01:00+00:00",
            grade="B",
        )
        self.assertTrue(allowed2)
        self.assertEqual(reason2, "STANDARD_SLOT_AVAILABLE")
        record_trade_opened(
            counters=self.counters,
            candle_time_str="2026-06-04T10:01:00+00:00",
            reason=reason2,
        )

        # Third B trade — should be rejected (B not exceptional)
        allowed3, reason3 = can_open_shadow_trade(
            policy=self.policy,
            counters=self.counters,
            candle_time_str="2026-06-04T10:02:00+00:00",
            grade="B",
        )
        self.assertFalse(allowed3)
        self.assertEqual(reason3, "DAILY_LIMIT_REACHED_GRADE_NOT_EXCEPTIONAL")

    def test_counter_tracks_correctly(self):
        self.assertEqual(len(self.counters), 0)
        allowed, reason = can_open_shadow_trade(
            policy=self.policy,
            counters=self.counters,
            candle_time_str="2026-06-04T10:00:00+00:00",
            grade="A",
        )
        record_trade_opened(
            counters=self.counters,
            candle_time_str="2026-06-04T10:00:00+00:00",
            reason=reason,
        )
        self.assertEqual(len(self.counters), 1)
        day = list(self.counters.values())[0]
        self.assertEqual(day.standard_count, 1)
        self.assertFalse(day.exceptional_used)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4: Daily exceptional slot
# ═══════════════════════════════════════════════════════════════════════════════


class TestDailyExceptionalSlot(unittest.TestCase):
    def setUp(self):
        self.policy = ShadowLivePolicy()
        self.counters: dict[str, DailyTradeCounter] = {}

    def test_a_trade_as_third_opens(self):
        # Open 2 standard trades
        for i in range(2):
            allowed, reason = can_open_shadow_trade(
                policy=self.policy,
                counters=self.counters,
                candle_time_str="2026-06-04T10:00:00+00:00",
                grade="B",
            )
            self.assertTrue(allowed)
            record_trade_opened(
                counters=self.counters,
                candle_time_str="2026-06-04T10:00:00+00:00",
                reason=reason,
            )

        # Third trade: A grade → exceptional slot
        allowed3, reason3 = can_open_shadow_trade(
            policy=self.policy,
            counters=self.counters,
            candle_time_str="2026-06-04T10:01:00+00:00",
            grade="A",
        )
        self.assertTrue(allowed3)
        self.assertEqual(reason3, "EXCEPTIONAL_SLOT_AVAILABLE")
        record_trade_opened(
            counters=self.counters,
            candle_time_str="2026-06-04T10:01:00+00:00",
            reason=reason3,
        )

        # Fourth trade → DAILY_MAX_ABSOLUTE_REACHED
        allowed4, reason4 = can_open_shadow_trade(
            policy=self.policy,
            counters=self.counters,
            candle_time_str="2026-06-04T10:02:00+00:00",
            grade="A",
        )
        self.assertFalse(allowed4)
        self.assertEqual(reason4, "DAILY_MAX_ABSOLUTE_REACHED")

    def test_exceptional_counter_recorded(self):
        # Open 2 standard
        for i in range(2):
            allowed, reason = can_open_shadow_trade(
                policy=self.policy,
                counters=self.counters,
                candle_time_str="2026-06-04T10:00:00+00:00",
                grade="B",
            )
            record_trade_opened(
                counters=self.counters,
                candle_time_str="2026-06-04T10:00:00+00:00",
                reason=reason,
            )

        # Exceptional slot
        allowed, reason = can_open_shadow_trade(
            policy=self.policy,
            counters=self.counters,
            candle_time_str="2026-06-04T10:01:00+00:00",
            grade="A",
        )
        record_trade_opened(
            counters=self.counters,
            candle_time_str="2026-06-04T10:01:00+00:00",
            reason=reason,
        )

        day = list(self.counters.values())[0]
        self.assertEqual(day.standard_count, 2)
        self.assertTrue(day.exceptional_used)
        self.assertEqual(day.total, 3)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5: TP1 partial close + BE+
# ═══════════════════════════════════════════════════════════════════════════════


class TestTP1PartialAndBEPlus(unittest.TestCase):
    def test_partial_close_event_structure(self):
        """P3: leg_1 TP1 generates leg_close event + activates leg_2 protected SL."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig

        blackboard = MagicMock()
        blackboard.read_sync = MagicMock(return_value={})
        blackboard.write = AsyncMock()
        blackboard._lock = MagicMock()
        blackboard._lock.__aenter__ = AsyncMock()
        blackboard._lock.__aexit__ = AsyncMock()
        blackboard._data = {}

        config = SimulatedTradeConfig(equity_initial=100.0, write_blackboard_positions=False)
        manager = SimulatedTradeManager(blackboard, config)

        # P3: two-leg trade structure
        trade = {
            "ticket": 1,
            "type": "SELL",
            "entry_price": 2650.0,
            "original_sl": 2660.0,
            "current_sl": 2660.0,
            "risk_points": 10.0,
            "risk_cash": 0.75,
            "total_risk_cash": 0.75,
            "total_risk_pct": 0.75,
            "volume_original": 0.075,
            "volume_remaining": 0.075,
            "commission_total": 0.0,
            "breakeven_activated": False,
            "be_plus_activated": False,
            "partial_closed": False,
            "tp1": 2640.0,
            "tp2": 2630.0,
            "protected_sl": None,
            "leg_1": {
                "leg": 1, "volume": 0.0375, "risk_pct": 0.375, "risk_cash": 0.375,
                "sl": 2660.0, "tp": 2640.0, "target_rr": 1.0,
                "status": "OPEN", "exit_reason": None, "exit_price": None, "pnl": 0.0, "pnl_R": 0.0, "commission": 0.0,
            },
            "leg_2": {
                "leg": 2, "volume": 0.0375, "risk_pct": 0.375, "risk_cash": 0.375,
                "sl": 2660.0, "tp": 2630.0, "target_rr": 2.0,
                "protected_sl_r": 0.5, "protected_sl": None,
                "status": "OPEN", "exit_reason": None, "exit_price": None, "pnl": 0.0, "pnl_R": 0.0, "commission": 0.0,
            },
        }

        # Candle that touches TP1 (leg_1 TP at 2640)
        candle = {
            "time": "2026-06-04T10:45:00+00:00",
            "open": 2645.0,
            "high": 2655.0,
            "low": 2635.0,  # touches TP1=2640
            "close": 2642.0,
        }

        events = manager._events_for_candle(trade, candle)
        self.assertGreaterEqual(len(events), 1)

        # P3: leg_1 closes → leg_close event
        leg1_event = [e for e in events if e.get("leg") == 1]
        self.assertEqual(len(leg1_event), 1)
        self.assertEqual(leg1_event[0]["event"], "leg_close")
        self.assertEqual(leg1_event[0]["reason"], "TP1")

        # leg_2 protected SL should be activated
        self.assertIsNotNone(trade["leg_2"]["protected_sl"])
        # SELL: protected SL = entry - 0.5*risk = 2650 - 5 = 2645.0
        self.assertLess(trade["leg_2"]["protected_sl"], trade["entry_price"])
        self.assertTrue(trade["breakeven_activated"])


# ═══════════════════════════════════════════════════════════════════════════════
# Test 6: TP2 close
# ═══════════════════════════════════════════════════════════════════════════════


class TestTP2Close(unittest.TestCase):
    def test_tp2_close_event(self):
        """P3: leg_2 TP2 close after leg_1 already closed at TP1."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig

        blackboard = MagicMock()
        blackboard.read_sync = MagicMock(return_value={})
        blackboard.write = AsyncMock()
        blackboard._lock = MagicMock()
        blackboard._lock.__aenter__ = AsyncMock()
        blackboard._lock.__aexit__ = AsyncMock()
        blackboard._data = {}

        config = SimulatedTradeConfig(equity_initial=100.0, write_blackboard_positions=False)
        manager = SimulatedTradeManager(blackboard, config)

        # P3: leg_1 closed at TP1, leg_2 open with protected SL
        trade = {
            "ticket": 1,
            "type": "SELL",
            "entry_price": 2650.0,
            "current_sl": 2660.0,
            "original_sl": 2660.0,
            "risk_points": 10.0,
            "risk_cash": 0.75,
            "total_risk_cash": 0.75,
            "volume_original": 0.075,
            "volume_remaining": 0.0375,
            "commission_total": 0.0,
            "breakeven_activated": True,
            "be_plus_activated": True,
            "partial_closed": True,
            "tp1": 2640.0,
            "tp2": 2630.0,
            "protected_sl": 2645.0,
            "leg_1": {
                "leg": 1, "volume": 0.0375, "risk_cash": 0.375,
                "sl": 2660.0, "tp": 2640.0, "target_rr": 1.0,
                "status": "CLOSED", "exit_reason": "TP1", "exit_price": 2640.0, "pnl": 0.375, "pnl_R": 1.0, "commission": 0.0,
            },
            "leg_2": {
                "leg": 2, "volume": 0.0375, "risk_cash": 0.375,
                "sl": 2645.0, "tp": 2630.0, "target_rr": 2.0,
                "protected_sl_r": 0.5, "protected_sl": 2645.0,
                "status": "OPEN", "exit_reason": None, "exit_price": None, "pnl": 0.0, "pnl_R": 0.0, "commission": 0.0,
            },
        }

        # Candle that touches TP2 (low=2625 touches TP2=2630), but high=2644 stays below protected SL=2645
        candle = {
            "time": "2026-06-04T11:00:00+00:00",
            "open": 2635.0,
            "high": 2644.0,  # below protected SL at 2645
            "low": 2625.0,   # touches TP2=2630
            "close": 2632.0,
        }

        events = manager._events_for_candle(trade, candle)
        # P3: leg_2 closes + parent closes
        close_events = [e for e in events if e["event"] == "close"]
        self.assertGreaterEqual(len(close_events), 1)
        close_evt = close_events[0]
        self.assertEqual(close_evt["reason"], "PARENT_CLOSE")
        self.assertEqual(close_evt["leg_2_exit_reason"], "TP2")
        self.assertGreater(close_evt["pnl"], 0)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 7: SL close
# ═══════════════════════════════════════════════════════════════════════════════


class TestSLClose(unittest.TestCase):
    def test_sl_close_event(self):
        """P3: both legs close at SL → parent close with negative PnL."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig

        blackboard = MagicMock()
        blackboard.read_sync = MagicMock(return_value={})
        blackboard.write = AsyncMock()
        blackboard._lock = MagicMock()
        blackboard._lock.__aenter__ = AsyncMock()
        blackboard._lock.__aexit__ = AsyncMock()
        blackboard._data = {}

        config = SimulatedTradeConfig(equity_initial=100.0, write_blackboard_positions=False)
        manager = SimulatedTradeManager(blackboard, config)

        # P3: two-leg trade, both legs open
        trade = {
            "ticket": 1,
            "type": "SELL",
            "entry_price": 2650.0,
            "current_sl": 2660.0,
            "original_sl": 2660.0,
            "risk_points": 10.0,
            "risk_cash": 0.75,
            "total_risk_cash": 0.75,
            "volume_original": 0.075,
            "volume_remaining": 0.075,
            "commission_total": 0.0,
            "breakeven_activated": False,
            "be_plus_activated": False,
            "partial_closed": False,
            "tp1": 2640.0,
            "tp2": 2630.0,
            "protected_sl": None,
            "leg_1": {
                "leg": 1, "volume": 0.0375, "risk_cash": 0.375,
                "sl": 2660.0, "tp": 2640.0, "target_rr": 1.0,
                "status": "OPEN", "exit_reason": None, "exit_price": None, "pnl": 0.0, "pnl_R": 0.0, "commission": 0.0,
            },
            "leg_2": {
                "leg": 2, "volume": 0.0375, "risk_cash": 0.375,
                "sl": 2660.0, "tp": 2630.0, "target_rr": 2.0,
                "protected_sl_r": 0.5, "protected_sl": None,
                "status": "OPEN", "exit_reason": None, "exit_price": None, "pnl": 0.0, "pnl_R": 0.0, "commission": 0.0,
            },
        }

        # Candle that touches SL (high >= 2660 for SELL)
        candle = {
            "time": "2026-06-04T10:30:00+00:00",
            "open": 2655.0,
            "high": 2665.0,  # touches SL=2660
            "low": 2645.0,
            "close": 2662.0,
        }

        events = manager._events_for_candle(trade, candle)
        # P3: both legs close at SL, then parent closes
        close_events = [e for e in events if e["event"] == "close"]
        self.assertGreaterEqual(len(close_events), 1)
        close_evt = close_events[0]
        self.assertEqual(close_evt["reason"], "PARENT_CLOSE")
        self.assertEqual(close_evt["leg_1_exit_reason"], "SL")
        self.assertEqual(close_evt["leg_2_exit_reason"], "SL")
        self.assertLess(close_evt["pnl"], 0)  # negative PnL


# ═══════════════════════════════════════════════════════════════════════════════
# Test 8: Open trade at replay end → open_trades_end_count
# ═══════════════════════════════════════════════════════════════════════════════


class TestOpenTradeEndSnapshot(unittest.TestCase):
    def test_snapshot_with_candle(self):
        """P3: snapshot reports per-leg unrealized PnL."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig

        blackboard = MagicMock()
        blackboard.read_sync = MagicMock(return_value={})
        blackboard.write = AsyncMock()
        blackboard._lock = MagicMock()
        blackboard._lock.__aenter__ = AsyncMock()
        blackboard._lock.__aexit__ = AsyncMock()
        blackboard._data = {}

        config = SimulatedTradeConfig(equity_initial=100.0, write_blackboard_positions=False)
        manager = SimulatedTradeManager(blackboard, config)

        # P3: two-leg trade, both legs still open
        trade = {
            "ticket": 1,
            "type": "SELL",
            "entry_price": 2650.0,
            "current_sl": 2660.0,
            "tp1": 2640.0,
            "tp2": 2630.0,
            "risk_points": 10.0,
            "risk_cash": 0.75,
            "total_risk_cash": 0.75,
            "volume_original": 0.075,
            "volume_remaining": 0.075,
            "risk_pct": 0.75,
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "A",
            "sizing_source": "PHASE18_EQUITY_RISK_MODEL",
            "capital_at_entry": 100.0,
            "opened_at": "2026-06-04T10:41:00+00:00",
            "breakeven_activated": False,
            "partial_closed": False,
            "protected_sl": None,
            "leg_1": {
                "leg": 1, "volume": 0.0375, "status": "OPEN",
            },
            "leg_2": {
                "leg": 2, "volume": 0.0375, "status": "OPEN",
            },
        }

        candle = {
            "time": "2026-06-05T23:59:00+00:00",
            "open": 2648.0,
            "high": 2652.0,
            "low": 2645.0,
            "close": 2648.0,
        }

        snapshot = manager._open_trade_snapshot(trade, candle)
        self.assertEqual(snapshot["ticket"], 1)
        self.assertEqual(snapshot["lifecycle_status"], "OPEN_AT_REPLAY_END")
        self.assertEqual(snapshot["type"], "SELL")
        self.assertIsNotNone(snapshot["unrealized_R"])
        # SELL: entry=2650, close=2648 → PnL = (2650-2648)*0.075 = 0.15
        self.assertGreater(snapshot["unrealized_pnl"], 0)
        self.assertAlmostEqual(snapshot["unrealized_pnl"], 0.15, places=5)

    def test_snapshot_no_candle(self):
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig

        blackboard = MagicMock()
        blackboard.read_sync = MagicMock(return_value={})
        blackboard.write = AsyncMock()
        blackboard._lock = MagicMock()
        blackboard._lock.__aenter__ = AsyncMock()
        blackboard._lock.__aexit__ = AsyncMock()
        blackboard._data = {}

        config = SimulatedTradeConfig(equity_initial=100.0, write_blackboard_positions=False)
        manager = SimulatedTradeManager(blackboard, config)

        trade = {"ticket": 1, "type": "BUY", "entry_price": 2650.0}
        snapshot = manager._open_trade_snapshot(trade, None)
        self.assertEqual(snapshot["status"], "OPEN_NO_LAST_CANDLE")


# ═══════════════════════════════════════════════════════════════════════════════
# Test 9: Performance summary separates realized/unrealized
# ═══════════════════════════════════════════════════════════════════════════════


class TestPerformanceSummarySeparation(unittest.TestCase):
    def test_no_trades_status(self):
        from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary

        summary = build_p2c_performance_summary(decisions=[], events=[])
        self.assertIn("performance_status", summary)
        self.assertEqual(summary["performance_status"], "NO_TRADES")

    def test_open_only_performance_status_logic(self):
        """With filled trades but no closed trades, performance_status should reflect that."""
        # Simulate: one open event, zero close events
        events = [
            {"event": "open", "time": "2026-06-04T10:41:00+00:00", "ticket": 1,
             "entry_price": 2650.0, "type": "SELL", "setup_grade": "A"},
        ]
        decisions = [
            {"decision": "ENTER_REDUCED", "setup_type": "SWEEP_REVERSAL",
             "setup_grade": "A", "enter_eligible": True, "risk_multiplier": 0.49,
             "score_after_veto": 80.0},
        ]
        from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary

        summary = build_p2c_performance_summary(decisions=decisions, events=events)
        self.assertEqual(summary["performance_status"], "NO_TRADES")  # no closed trades in events
        self.assertEqual(summary["filled_trades"], 1)
        # wins + losses gives us the closed count
        closed_from_events = summary["wins"] + summary["losses"] + summary["breakeven"]
        self.assertEqual(closed_from_events, 0)
        # profit_factor should be null with no closed trades (no R values)
        self.assertIsNone(summary["profit_factor"])
        self.assertIsNone(summary["expectancy_R"])


# ═══════════════════════════════════════════════════════════════════════════════
# Test 10: No forced ENTER (WATCH_ONLY → no shadow open)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoForcedEnter(unittest.TestCase):
    def test_watch_only_no_shadow_signal(self):
        from gold_sniper.replay.simulated_trade_manager import _p1_decision_shadow_signal

        decision = {
            "decision": "WATCH_ONLY",
            "enter_eligible": True,
            "risk_plan": {"allowed": True, "risk_multiplier": 0.5},
        }
        candle = {"close": 2650.0, "high": 2655.0, "low": 2645.0, "time": "2026-06-04T10:41:00Z"}
        signal = _p1_decision_shadow_signal(candle, decision)
        self.assertIsNone(signal)

    def test_reject_no_shadow_signal(self):
        from gold_sniper.replay.simulated_trade_manager import _p1_decision_shadow_signal

        decision = {
            "decision": "REJECT",
            "enter_eligible": False,
            "risk_plan": {},
        }
        candle = {"close": 2650.0, "high": 2655.0, "low": 2645.0, "time": "2026-06-04T10:41:00Z"}
        signal = _p1_decision_shadow_signal(candle, decision)
        self.assertIsNone(signal)


# ═══════════════════════════════════════════════════════════════════════════════
# Test 11: Risk positive without enter_eligible → no shadow open
# ═══════════════════════════════════════════════════════════════════════════════


class TestRiskPositiveWithoutEnterEligible(unittest.TestCase):
    def test_no_open_without_enter_eligible(self):
        from gold_sniper.replay.simulated_trade_manager import _p1_decision_shadow_signal

        decision = {
            "decision": "ENTER_REDUCED",
            "enter_eligible": False,  # critical: not eligible
            "risk_plan": {"allowed": True, "risk_multiplier": 0.5},
            "risk_multiplier": 0.5,
            "side": "SELL",
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "A",
            "p1_evidence_bundle": {
                "poi": {
                    "price_bounds": {"low": 2640.0, "high": 2660.0},
                },
            },
        }
        candle = {"close": 2650.0, "high": 2655.0, "low": 2645.0, "time": "2026-06-04T10:41:00Z"}
        signal = _p1_decision_shadow_signal(candle, decision)
        self.assertIsNone(signal)  # enter_eligible=False blocks it


# ═══════════════════════════════════════════════════════════════════════════════
# Test 12: Smoke test — daily limiter across multiple days
# ═══════════════════════════════════════════════════════════════════════════════


class TestDailyLimiterMultiDay(unittest.TestCase):
    def test_counter_resets_per_day(self):
        policy = ShadowLivePolicy()
        counters: dict[str, DailyTradeCounter] = {}

        # Day 1: open 2 trades
        for i in range(2):
            allowed, reason = can_open_shadow_trade(
                policy=policy, counters=counters,
                candle_time_str="2026-06-04T10:00:00+00:00", grade="B",
            )
            self.assertTrue(allowed)
            record_trade_opened(
                counters=counters, candle_time_str="2026-06-04T10:00:00+00:00",
                reason=reason,
            )

        # Day 2: should be able to open again (new counter)
        allowed, reason = can_open_shadow_trade(
            policy=policy, counters=counters,
            candle_time_str="2026-06-05T10:00:00+00:00", grade="B",
        )
        self.assertTrue(allowed)
        self.assertEqual(len(counters), 2)  # two different days


# ═══════════════════════════════════════════════════════════════════════════════
# Phase18 integration: SimulatedTradeManager with shadow policy
# ═══════════════════════════════════════════════════════════════════════════════


class TestSimulatedTradeManagerPhase18(unittest.TestCase):
    def test_summary_includes_phase18_fields(self):
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig

        blackboard = MagicMock()
        blackboard.read_sync = MagicMock(return_value={})
        blackboard.write = AsyncMock()
        blackboard._lock = MagicMock()
        blackboard._lock.__aenter__ = AsyncMock()
        blackboard._lock.__aexit__ = AsyncMock()
        blackboard._data = {}

        config = SimulatedTradeConfig(
            equity_initial=100.0,
            write_blackboard_positions=False,
            enable_live_sizing=True,
            enable_daily_limits=True,
        )
        manager = SimulatedTradeManager(blackboard, config)
        summary = manager.summary()

        self.assertIn("open_trades_end_count", summary)
        self.assertIn("open_trades_end_details", summary)
        self.assertIn("unrealized_R_total", summary)
        self.assertIn("unrealized_pnl", summary)
        self.assertIn("daily_limit_rejections", summary)
        self.assertIn("grade_blocked_count", summary)
        self.assertIn("shadow_live_policy", summary)
        self.assertEqual(summary["open_trades_end_count"], 0)
        self.assertEqual(summary["unrealized_R_total"], 0.0)

    def test_last_seen_candle_tracked(self):
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig

        blackboard = MagicMock()
        blackboard.read_sync = MagicMock(return_value={})
        blackboard.write = AsyncMock()
        blackboard._lock = MagicMock()
        blackboard._lock.__aenter__ = AsyncMock()
        blackboard._lock.__aexit__ = AsyncMock()
        blackboard._data = {}

        config = SimulatedTradeConfig(equity_initial=100.0, write_blackboard_positions=False)
        manager = SimulatedTradeManager(blackboard, config)
        self.assertIsNone(manager.last_seen_candle)

        # We can't easily run async on_candle in sync test, but we can verify the field exists
        self.assertTrue(hasattr(manager, "last_seen_candle"))
        self.assertTrue(hasattr(manager, "daily_counters"))


if __name__ == "__main__":
    unittest.main()

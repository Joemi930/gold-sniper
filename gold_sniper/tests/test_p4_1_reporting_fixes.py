"""P4.1: Reporting fixes regression tests — eval_day, active_day, opt_findings, cost_drag."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from replay_app.report_writer import build_optimization_findings


class TestP41ReportingFixes(unittest.TestCase):
    """Verify P4.1 fixes: eval/active day, optimization_findings, cost breakdown."""

    # ── P4.1: trades_per_eval_day computed correctly ───────────────────

    def test_trades_per_eval_day_nonzero(self):
        """1 trade over 8 days should give ~0.12, NOT 0."""
        from replay.replay_engine import _compute_trades_per_eval_day
        from datetime import datetime, timezone, timedelta
        eval_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        eval_end = datetime(2026, 1, 8, 23, 59, 59, tzinfo=timezone.utc)
        result = _compute_trades_per_eval_day(1, eval_start, eval_end, None, None)
        self.assertGreater(result, 0.0, f"1 trade over 8 days should be >0, got {result}")
        self.assertAlmostEqual(result, 0.125, delta=0.01)

    def test_trades_per_eval_day_zero_trades(self):
        from replay.replay_engine import _compute_trades_per_eval_day
        from datetime import datetime, timezone
        eval_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        eval_end = datetime(2026, 1, 8, tzinfo=timezone.utc)
        result = _compute_trades_per_eval_day(0, eval_start, eval_end, None, None)
        self.assertEqual(result, 0.0)

    def test_trades_per_active_day_nonzero(self):
        """1 trade over 1 active day = 1.0."""
        from replay.replay_engine import _compute_trades_per_active_day
        result = _compute_trades_per_active_day(1, 1)
        self.assertEqual(result, 1.0)

    # ── P4.1: optimization_findings winrate fix ────────────────────────

    def test_opt_findings_winrate_100_no_below_50(self):
        """Winrate 100% must never trigger 'below 50%' suggestion."""
        summary = {
            "win_rate": 100.0,
            "expectancy_R": 0.27,
            "parent_trades": 1,
            "trades_per_day": 0.12,
        }
        findings = build_optimization_findings(summary)
        suggestions = findings.get("suggestions", [])
        below_msgs = [s for s in suggestions if "below 50%" in s.lower()]
        self.assertEqual(len(below_msgs), 0,
                         f"Winrate 100% should NOT trigger 'below 50%': {below_msgs}")

    def test_opt_findings_winrate_30_triggers_below_50(self):
        """Winrate 30% SHOULD trigger 'below 50%'."""
        summary = {
            "win_rate": 30.0,
            "expectancy_R": -0.5,
            "parent_trades": 10,
            "trades_per_day": 0.5,
        }
        findings = build_optimization_findings(summary)
        suggestions = findings.get("suggestions", [])
        below_msgs = [s for s in suggestions if "below 50%" in s.lower()]
        self.assertGreater(len(below_msgs), 0,
                           "Winrate 30% SHOULD trigger 'below 50%'")

    def test_opt_findings_winrate_field_priority(self):
        """Ensure win_rate (engine field) is correctly read."""
        # Engine summary uses 'win_rate', not 'winrate'
        summary = {"win_rate": 85.0, "expectancy_R": 0.1, "parent_trades": 5}
        findings = build_optimization_findings(summary)
        # Should NOT trigger "below 50%" or "negative expectancy"
        suggestions = findings.get("suggestions", [])
        below = [s for s in suggestions if "below 50%" in s.lower()]
        neg = [s for s in suggestions if "negative expectancy" in s.lower()]
        self.assertEqual(len(below), 0)
        self.assertEqual(len(neg), 0)

    # ── P4.1: cost drag breakdown exists ──────────────────────────────

    def test_cost_drag_breakdown_in_summary(self):
        """Verify that trade summary includes cost component breakdown."""
        from unittest.mock import AsyncMock, MagicMock
        from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager

        bb = MagicMock()
        bb._lock = AsyncMock()
        bb._data = {"meta": {}, "market_data": {"candles": {}}, "active_trades": {}, "positions": {"open_positions": []}}
        bb.read_sync = MagicMock(return_value=[])
        bb.write = AsyncMock()
        bb.update_dict = AsyncMock()
        bb.update_market = AsyncMock()
        bb.notify_candle_close = AsyncMock()

        tm = SimulatedTradeManager(bb, SimulatedTradeConfig(equity_initial=100.0))
        s = tm.summary()
        # P4.1 cost breakdown keys
        for key in ("total_spread_points", "total_slippage_points", "total_commission_R",
                     "avg_spread_per_trade", "avg_slippage_per_trade", "avg_commission_per_trade"):
            self.assertIn(key, s, f"Missing cost breakdown key: {key}")

    def test_cost_drag_propagated_to_metrics(self):
        """Verify cost breakdown flows to metrics.json."""
        summary = {
            "initial_equity": 100, "final_equity": 100.27, "net_pnl": 0.27,
            "net_pnl_pct": 0.27, "pure_expectancy_R": 0.75, "expectancy_R": 0.27,
            "cost_drag_R": 0.48, "win_rate": 100.0, "winrate_full_win": 0.0,
            "winrate_tp1_touch": 100.0, "max_drawdown_pct": 0.0,
            "parent_trades": 1, "tp1_hit_count": 1, "tp2_hit_count": 0,
            "full_sl_count": 0, "parent_full_sl_count": 0, "sl_hit_count": 0,
            "leg_sl_count": 0, "protected_sl_hit_count": 1,
            "trades_per_day": 1.0, "trades_per_eval_day": 0.125, "trades_per_active_day": 1.0,
            "avg_win_R": 0.27, "avg_loss_R": 0.0, "payoff_ratio": 0.0,
            "tp1_tp2_avg_net_R": 0, "tp1_tp2_avg_pure_R": 0,
            "tp1_protected_avg_net_R": 0.27, "tp1_protected_avg_pure_R": 0.75,
            "avg_cost_drag_per_trade_R": 0.48,
            "total_spread_points": 32, "total_slippage_points": 5,
            "total_commission_R": 0.1, "avg_spread_per_trade": 32,
            "avg_slippage_per_trade": 5, "avg_commission_per_trade": 0.1,
            "first_trade_time": "2026-01-07T20:00:00Z",
            "last_trade_time": "2026-01-07T20:00:00Z",
            "warmup_trade_count": 0,
        }
        from replay_app.report_writer import write_compact_report
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test_run"
            write_compact_report(summary, "test_run", out)
            metrics = json.loads((out / "metrics.json").read_text())
            self.assertEqual(metrics["trades_per_eval_day"], 0.125)
            self.assertEqual(metrics["trades_per_active_day"], 1.0)
            self.assertGreater(metrics["total_spread_points"], 0)
            self.assertGreater(metrics["avg_cost_drag_per_trade_R"], 0)
            # Verify REPORT.md contains cost breakdown
            report_text = (out / "REPORT.md").read_text()
            self.assertIn("Cost Component Breakdown", report_text)
            self.assertIn("Spread", report_text)
            self.assertIn("Slippage", report_text)
            self.assertIn("Commission", report_text)

    # ── P4.1: profile report completeness ─────────────────────────────

    def test_profile_report_has_sections(self):
        """Profile report must have non-empty sections when enabled."""
        from replay.replay_profiler import enable_profiling, get_profiler, disable_profiling
        disable_profiling()
        p = enable_profiling()
        with p.section("test_agent_1"):
            pass
        with p.section("test_agent_2"):
            pass
        rpt = p.report()
        self.assertIn("sections", rpt)
        self.assertGreater(len(rpt["sections"]), 0, "Profile must have sections when profiling is active")
        self.assertIn("top_bottlenecks", rpt)
        self.assertGreater(len(rpt["top_bottlenecks"]), 0)
        disable_profiling()


if __name__ == "__main__":
    unittest.main()

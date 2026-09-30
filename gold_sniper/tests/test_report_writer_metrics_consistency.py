"""P4: Report writer metrics consistency tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from replay_app.report_writer import write_compact_report, extract_important_trades


class TestReportWriterMetricsConsistency(unittest.TestCase):
    """Verify that report_writer correctly maps summary keys to metrics."""

    def _make_summary(self, **overrides):
        base = {
            "initial_equity": 100.0,
            "final_equity": 98.5,
            "net_pnl": -1.5,
            "net_pnl_pct": -1.5,
            "pure_expectancy_R": 0.15,
            "expectancy_R": -0.05,
            "cost_drag_R": 0.20,
            "win_rate": 45.0,
            "winrate_full_win": 25.0,
            "winrate_tp1_touch": 60.0,
            "max_drawdown_pct": 5.0,
            "parent_trades": 10,
            "tp1_hit_count": 6,
            "tp2_hit_count": 3,
            "full_sl_count": 2,
            "parent_full_sl_count": 2,
            "sl_hit_count": 7,
            "leg_sl_count": 7,
            "protected_sl_hit_count": 1,
            "trades_per_day": 0.33,
            "trades_per_eval_day": 0.33,
            "trades_per_active_day": 1.25,
            "avg_win_R": 0.60,
            "avg_loss_R": -0.85,
            "payoff_ratio": 0.71,
            "tp1_tp2_avg_net_R": 1.02,
            "tp1_tp2_avg_pure_R": 1.50,
            "tp1_protected_avg_net_R": 0.27,
            "tp1_protected_avg_pure_R": 0.75,
            "avg_cost_drag_per_trade_R": 0.15,
            "first_trade_time": "2026-01-15T12:00:00Z",
            "last_trade_time": "2026-01-28T16:00:00Z",
            "warmup_trade_count": 0,
        }
        base.update(overrides)
        return base

    def test_cost_drag_fallback(self):
        """cost_drag_R should use pure_R - net_R as fallback."""
        s = self._make_summary()
        s.pop("cost_drag_R", None)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test_run"
            write_compact_report(s, "test_run", out)
            metrics = json.loads((out / "metrics.json").read_text())
            expected = round(0.15 - (-0.05), 6)
            self.assertEqual(metrics["cost_drag_R"], expected)

    def test_full_sl_vs_leg_sl_separated(self):
        """P4: parent_full_sl_count and leg_sl_count must be distinct."""
        s = self._make_summary()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test_run"
            write_compact_report(s, "test_run", out)
            metrics = json.loads((out / "metrics.json").read_text())
            self.assertIn("full_sl_count", metrics)
            self.assertIn("leg_sl_count", metrics)
            self.assertEqual(metrics["full_sl_count"], 2)  # parent level
            self.assertEqual(metrics["leg_sl_count"], 7)   # leg level

    def test_metric_keys_present(self):
        """All P4 metric keys must be present in metrics.json."""
        s = self._make_summary()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test_run"
            write_compact_report(s, "test_run", out)
            metrics = json.loads((out / "metrics.json").read_text())
            required = [
                "trades_per_eval_day", "trades_per_active_day",
                "tp1_tp2_avg_net_R", "tp1_tp2_avg_pure_R",
                "tp1_protected_avg_net_R", "tp1_protected_avg_pure_R",
                "avg_cost_drag_per_trade_R",
                "first_trade_time", "last_trade_time",
                "warmup_trade_count",
            ]
            for key in required:
                self.assertIn(key, metrics, f"Missing metrics key: {key}")

    def test_report_md_contains_p4_sections(self):
        """REPORT.md must contain period boundaries and payoff diagnostics."""
        s = self._make_summary()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "test_run"
            rpt = write_compact_report(s, "test_run", out)
            text = rpt.read_text()
            self.assertIn("Period & Boundaries", text)
            self.assertIn("Payoff Diagnostics", text)
            self.assertIn("TP1+TP2", text)
            self.assertIn("TP1+Protected", text)


if __name__ == "__main__":
    unittest.main()

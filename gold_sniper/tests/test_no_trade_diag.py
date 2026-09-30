"""P4.2 — MetricsAggregator NO_TRADES + ReportWriterV2 tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gold_sniper.replay.metrics_aggregator import MetricsAggregator
from gold_sniper.replay.report_writer_v2 import ReportWriterV2


class TestMetricsAggregator(unittest.TestCase):

    def setUp(self):
        self.m = MetricsAggregator()

    # ── NO_TRADES state ───────────────────────────────────────────────

    def test_zero_trade_is_no_sample(self):
        """0 trades → state=NO_TRADES, winrate/expectancy=None (not 0%)."""
        summary = self.m.finalize()
        self.assertEqual(summary.get("state"), "NO_TRADES")
        self.assertIsNone(summary["winrate"])
        self.assertIsNone(summary["expectancy_R"])

    def test_no_trade_diagnostic_has_all_fields(self):
        diag = self.m.no_trade_diagnostic()
        self.assertEqual(diag["state"], "NO_TRADES")
        self.assertIsNone(diag["winrate"])
        self.assertIsNone(diag["expectancy_R"])
        self.assertIn("candidates", diag)
        self.assertIn("top_reject_reasons", diag)
        self.assertIn("gate_rejections", diag)
        self.assertIn("poi_reaction_skipped", diag)

    # ── trade outcomes ────────────────────────────────────────────────

    def test_with_trades_has_winrate(self):
        """When there are trades, winrate and expectancy are computed."""
        self.m.record_trade_open()
        self.m.record_trade_open()
        self.m.record_trade_close(pnl_r=1.5, close_reason="TP2")
        self.m.record_trade_close(pnl_r=-1.0, close_reason="SL")
        summary = self.m.finalize()
        self.assertEqual(summary["trade_count"], 2)
        self.assertIsNotNone(summary["winrate"])
        self.assertIsNotNone(summary["expectancy_R"])
        self.assertAlmostEqual(summary["winrate"], 0.5)
        self.assertAlmostEqual(summary["expectancy_R"], 0.25)

    # ── decision recording ────────────────────────────────────────────

    def test_record_decision_tracks_reasons(self):
        self.m.record_decision("REJECT", reject_reason="SESSION_BLOCKED",
                               veto_code="ASIA_SESSION")
        self.m.record_decision("REJECT", reject_reason="SESSION_BLOCKED",
                               veto_code="ASIA_SESSION")
        self.m.record_decision("REJECT", reject_reason="NO_POI")
        summary = self.m.finalize()
        top = summary["top_reject_reasons"]
        self.assertEqual(top[0]["reason"], "SESSION_BLOCKED")
        self.assertEqual(top[0]["count"], 2)

    def test_record_decision_tracks_setup_types(self):
        self.m.record_decision("REJECT", setup_type="POI_REACTION")
        self.m.record_decision("REJECT", setup_type="POI_REACTION")
        self.m.record_decision("ENTER_REDUCED", setup_type="SWEEP_REVERSAL")
        summary = self.m.finalize()
        types = summary["top_setup_types"]
        self.assertEqual(types[0]["setup_type"], "POI_REACTION")
        self.assertEqual(types[0]["count"], 2)

    # ── gate rejections ───────────────────────────────────────────────

    def test_gate_rejection_counts(self):
        self.m.record_gate_rejection("SESSION_NOT_TRADABLE")
        self.m.record_gate_rejection("SESSION_NOT_TRADABLE")
        self.m.record_gate_rejection("HTF_NOT_READY")
        summary = self.m.finalize()
        gates = summary.get("gate_rejections", {})
        self.assertEqual(gates.get("SESSION_NOT_TRADABLE"), 2)
        self.assertEqual(gates.get("HTF_NOT_READY"), 1)

    # ── synthetic trade guard ─────────────────────────────────────────

    def test_flag_synthetic_trade(self):
        self.m.flag_synthetic_trade()
        self.m.flag_synthetic_trade()
        summary = self.m.finalize()
        self.assertIn("WARNING", summary)
        self.assertEqual(summary["synthetic_trades"], 2)

    # ── top-N helpers ─────────────────────────────────────────────────

    def test_top_reject_reasons_limited(self):
        for i in range(15):
            self.m.record_decision("REJECT", reject_reason=f"REASON_{i}")
        top = self.m.top_reject_reasons(5)
        self.assertEqual(len(top), 5)

    # ── POI_REACTION skip ─────────────────────────────────────────────

    def test_poi_reaction_skip_tracked(self):
        self.m.record_poi_reaction_skip()
        self.m.record_poi_reaction_skip()
        self.m.record_poi_reaction_skip()
        self.assertEqual(self.m.poi_reaction_skipped, 3)
        summary = self.m.finalize()
        self.assertEqual(summary["poi_reaction_skipped"], 3)


class TestReportWriterV2(unittest.TestCase):

    def test_write_summary_json_no_trades(self):
        m = MetricsAggregator()
        summary = m.finalize()
        with tempfile.TemporaryDirectory() as tmp:
            writer = ReportWriterV2(run_dir=Path(tmp), summary=summary)
            path = writer.write_summary_json()
            self.assertTrue(path.exists())
            # Verify NO_TRADES state is in the file
            content = path.read_text()
            self.assertIn("NO_TRADES", content)
            self.assertIn('"winrate": null', content)
            self.assertIn('"expectancy_R": null', content)

    def test_write_performance_md(self):
        m = MetricsAggregator()
        m.record_trade_open()
        m.record_trade_close(pnl_r=1.0, close_reason="TP2")
        summary = m.finalize()
        with tempfile.TemporaryDirectory() as tmp:
            writer = ReportWriterV2(run_dir=Path(tmp), summary=summary)
            path = writer.write_performance_md()
            self.assertTrue(path.exists())
            content = path.read_text()
            self.assertIn("Performance Report", content)

    def test_write_gating_md(self):
        m = MetricsAggregator()
        m.record_gate_rejection("SESSION_NOT_TRADABLE")
        m.record_poi_reaction_skip()
        summary = m.finalize()
        with tempfile.TemporaryDirectory() as tmp:
            writer = ReportWriterV2(run_dir=Path(tmp), summary=summary)
            path = writer.write_gating_md()
            self.assertTrue(path.exists())
            content = path.read_text()
            self.assertIn("Gating Report", content)
            self.assertIn("SESSION_NOT_TRADABLE", content)

    def test_no_synthetic_trades_in_report(self):
        """Report must not contain synthetic/fallback trades."""
        m = MetricsAggregator()
        m.flag_synthetic_trade()
        summary = m.finalize()
        with tempfile.TemporaryDirectory() as tmp:
            writer = ReportWriterV2(run_dir=Path(tmp), summary=summary)
            path = writer.write_summary_json()
            content = path.read_text()
            # The WARNING must be present (transparency)
            self.assertIn("WARNING", content)
            # But no fake trade_count
            self.assertIn('"trade_count": 0', content)


if __name__ == "__main__":
    unittest.main()

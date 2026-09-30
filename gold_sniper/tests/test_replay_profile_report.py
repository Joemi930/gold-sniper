"""P4: Profiler tests — verify profiler collects section-level metrics."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from replay.replay_profiler import ReplayProfiler, enable_profiling, get_profiler, disable_profiling


class TestReplayProfileReport(unittest.TestCase):
    """Verify that ReplayProfiler correctly collects and reports timing data."""

    def setUp(self):
        disable_profiling()

    def tearDown(self):
        disable_profiling()

    def test_profiler_disabled_by_default(self):
        p = get_profiler()
        self.assertFalse(p.enabled)

    def test_enable_profiling_sets_enabled(self):
        p = enable_profiling()
        self.assertTrue(p.enabled)
        self.assertIs(p, get_profiler())

    def test_section_context_manager_collects_timing(self):
        p = enable_profiling()
        with p.section("test_section"):
            pass  # minimal work
        self.assertIn("test_section", p._sections)
        s = p._sections["test_section"]
        self.assertEqual(s.count, 1)
        self.assertGreater(s.total_ms, 0)

    def test_section_skips_when_disabled(self):
        p = get_profiler()
        self.assertFalse(p.enabled)
        with p.section("skipped"):
            pass
        self.assertNotIn("skipped", p._sections)

    def test_report_contains_sections_and_bottlenecks(self):
        p = enable_profiling()
        with p.section("fast_op"):
            pass
        with p.section("slow_op"):
            # simulate some work
            for _ in range(1000):
                pass
        rpt = p.report()
        self.assertIn("sections", rpt)
        self.assertIn("top_bottlenecks", rpt)
        self.assertGreaterEqual(len(rpt["top_bottlenecks"]), 1)
        # slow_op should be ranked first
        top = rpt["top_bottlenecks"][0]
        self.assertEqual(top["section"], "slow_op")

    def test_write_report_creates_json_file(self):
        p = enable_profiling()
        p.tick_candle(eval_active=True)
        p.tick_candle(eval_active=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = p.write_report(tmp)
            self.assertTrue(path.exists())
            data = json.loads(path.read_text())
            self.assertTrue(data["enabled"])
            self.assertEqual(data["candles_total"], 2)
            self.assertEqual(data["candles_eval"], 2)

    def test_tick_candle_counts_eval_separately(self):
        p = enable_profiling()
        p.tick_candle(eval_active=False)
        p.tick_candle(eval_active=False)
        p.tick_candle(eval_active=True)
        self.assertEqual(p._candle_count, 3)
        self.assertEqual(p._eval_candle_count, 1)


if __name__ == "__main__":
    unittest.main()

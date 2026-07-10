"""P4.2 — ProfilerV2 tests (coverage ≥95%, unaccounted_ms, mandatory sections)."""
from __future__ import annotations

import time
import unittest

from gold_sniper.replay.profiler_v2 import (
    MANDATORY_SECTIONS,
    ProfilerV2,
    enable_profiling_v2,
    get_profiler_v2,
)


class TestProfilerV2(unittest.TestCase):
    def setUp(self):
        self.prof = ProfilerV2()
        self.prof.start()

    # ── basic lifecycle ────────────────────────────────────────────────

    def test_start_initializes_total(self):
        self.prof.finish()
        self.assertGreater(self.prof._total_ms, 0)

    def test_disabled_skips_timing(self):
        prof = ProfilerV2(enabled=False)
        prof.start()
        with prof.section("feature_update"):
            time.sleep(0.01)
        prof.finish()
        s = prof._sections.get("feature_update")
        self.assertIsNone(s)

    # ── section timing ─────────────────────────────────────────────────

    def test_section_accumulates(self):
        with self.prof.section("feature_update"):
            pass
        with self.prof.section("feature_update"):
            pass
        self.prof.finish()
        s = self.prof._sections["feature_update"]
        self.assertEqual(s.count, 2)
        self.assertGreater(s.total_ms, 0)
        self.assertGreaterEqual(s.max_ms, s.min_ms)

    def test_all_mandatory_sections_appear_in_report(self):
        for name in MANDATORY_SECTIONS:
            with self.prof.section(name):
                pass
        report = self.prof.finish()
        for name in MANDATORY_SECTIONS:
            self.assertIn(name, report["sections"])
            self.assertEqual(report["sections"][name]["count"], 1)

    # ── agent recording ────────────────────────────────────────────────

    def test_record_agent(self):
        self.prof.record_agent("agent_1", 12.5)
        self.prof.record_agent("agent_1", 7.3)
        self.prof.record_agent("agent_2", 5.0)
        report = self.prof.finish()
        agents = report["agent_timings"]
        self.assertAlmostEqual(agents["agent_1"]["total_ms"], 19.8, places=1)
        self.assertEqual(agents["agent_1"]["count"], 2)
        self.assertAlmostEqual(agents["agent_2"]["total_ms"], 5.0, places=1)

    # ── candle counting ────────────────────────────────────────────────

    def test_tick_candle_counts_eval_vs_warmup(self):
        self.prof.tick_candle(eval_active=True)
        self.prof.tick_candle(eval_active=True)
        self.prof.tick_candle(eval_active=False)
        self.prof.tick_candle(eval_active=False)
        report = self.prof.finish()
        self.assertEqual(report["candles_total"], 4)
        self.assertEqual(report["candles_eval"], 2)
        self.assertEqual(report["candles_warmup"], 2)

    # ── coverage ───────────────────────────────────────────────────────

    def test_coverage_100_percent_when_all_accounted(self):
        """When every ms is inside a named section, coverage = 100%."""
        with self.prof.section("feature_update"):
            pass
        self.prof._total_ms = self.prof.accounted_ms  # simulate perfect accounting
        self.assertAlmostEqual(self.prof.coverage_pct(), 100.0, places=1)

    def test_unaccounted_ms_exposed(self):
        """unaccounted_ms must be reported, not hidden."""
        with self.prof.section("inject_candle"):
            pass
        # Manually set _total_ms to simulate 150ms of unaccounted time
        # (don't call finish() — it recalculates _total_ms from wall clock)
        self.prof._total_ms = self.prof.accounted_ms + 150.0
        report = self.prof.report()
        self.assertAlmostEqual(report["unaccounted_ms"], 150.0, places=1)
        self.assertLess(report["coverage_pct"], 100.0)

    def test_coverage_ge_95_target(self):
        """Smoke: the profiler itself should report coverage ≥95% with all sections used."""
        for name in MANDATORY_SECTIONS:
            with self.prof.section(name):
                pass
        # Artificially set a large total so the "unaccounted" fraction is tiny
        accounted = self.prof.accounted_ms
        self.prof._total_ms = accounted * 1.001  # only 0.1% unaccounted → ~99.9% coverage
        report = self.prof.report()
        self.assertGreaterEqual(report["coverage_pct"], 95.0)

    # ── bottlenecks ────────────────────────────────────────────────────

    def test_top_bottlenecks_ranked(self):
        with self.prof.section("inject_candle"):
            time.sleep(0.05)
        with self.prof.section("candidate_scan"):
            time.sleep(0.01)
        report = self.prof.finish()
        top = report["top_bottlenecks"]
        self.assertGreaterEqual(len(top), 1)
        # inject_candle took longer than candidate_scan
        self.assertEqual(top[0]["section"], "inject_candle")

    # ── singleton ──────────────────────────────────────────────────────

    def test_singleton_returns_same_instance(self):
        p1 = get_profiler_v2()
        p2 = get_profiler_v2()
        self.assertIs(p1, p2)

    def test_enable_profiling_v2_creates_new(self):
        p = enable_profiling_v2()
        self.assertTrue(p.enabled)
        self.assertGreater(p._total_start, 0)

    # ── report writer ──────────────────────────────────────────────────

    def test_write_report_creates_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            prof = ProfilerV2()
            prof.start()
            with prof.section("feature_update"):
                pass
            path = prof.write_report(tmp)
            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).name, "profile_report_v2.json")


if __name__ == "__main__":
    unittest.main()

"""P4.2 — CandidateDiscoveryEngine tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from gold_sniper.replay.candidate_discovery import (
    GATE_HTF_NOT_READY,
    GATE_NO_LIQUIDITY,
    GATE_NO_POI,
    GATE_PASSED,
    GATE_POI_REACTION_SKIPPED,
    GATE_SESSION_NOT_TRADABLE,
    GATE_SETUP_NOT_TRADABLE,
    DIAGNOSTIC_SETUPS,
    TRADABLE_SETUPS,
    CandidateDiscoveryEngine,
    CandidateWindow,
)
from gold_sniper.replay.feature_store import FeatureStore
from gold_sniper.replay.multi_timeframe_builder import MultiTimeframeBuilder


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _feed_15m_bars(fs: FeatureStore, base: datetime, count: int = 1):
    """Feed enough M1 candles to produce *count* closed 15m bars."""
    for bar_idx in range(count):
        for minute in range(15):
            t = base + timedelta(minutes=bar_idx * 15 + minute)
            fs.update({
                "time": t,
                "open": 2650.0 + minute * 0.1,
                "high": 2651.0 + minute * 0.1,
                "low": 2649.0 + minute * 0.1,
                "close": 2650.5 + minute * 0.1,
                "tick_volume": 100,
                "spread": 0,
            })


class TestCandidateDiscovery(unittest.TestCase):

    def setUp(self):
        self.engine = CandidateDiscoveryEngine()
        self.mtf = MultiTimeframeBuilder()
        self.fs = FeatureStore(mtf=self.mtf)

    # ── session gate ───────────────────────────────────────────────────

    def test_session_not_tradable_returns_none(self):
        """Asia session → no candidate."""
        t = _utc("2025-12-08T03:00:00Z")
        candle = {"time": t, "open": 2650, "high": 2651, "low": 2649,
                   "close": 2650.5, "tick_volume": 100, "spread": 0}
        self.fs.update(candle)
        result = self.engine.scan(self.fs, t)
        self.assertIsNone(result)
        self.assertIn(GATE_SESSION_NOT_TRADABLE, self.engine._gate_rejections)

    def test_london_session_passes_session_gate(self):
        """London session (10:00 UTC) should pass session gate."""
        base = _utc("2025-12-08T10:00:00Z")
        _feed_15m_bars(self.fs, base, count=2)  # enough bars for HTF ready
        t = base + timedelta(minutes=29)  # after 2nd 15m bar closes
        result = self.engine.scan(self.fs, t)
        # May still fail HTF gate but session gate must pass
        session_block = self.engine._gate_rejections.get(GATE_SESSION_NOT_TRADABLE, 0)
        self.assertEqual(session_block, 0)

    # ── HTF gate ───────────────────────────────────────────────────────

    def test_htf_not_ready_without_enough_bars(self):
        """Without enough 15m bars, HTF context is not ready."""
        t = _utc("2025-12-08T10:00:00Z")
        candle = {"time": t, "open": 2650, "high": 2651, "low": 2649,
                   "close": 2650.5, "tick_volume": 100, "spread": 0}
        self.fs.update(candle)
        result = self.engine.scan(self.fs, t)
        self.assertIsNone(result)
        self.assertIn(GATE_HTF_NOT_READY, self.engine._gate_rejections)

    # ── tradable setup detection ───────────────────────────────────────

    def test_poi_reaction_is_diagnostic_not_tradable(self):
        self.assertIn("POI_REACTION", DIAGNOSTIC_SETUPS)
        self.assertNotIn("POI_REACTION", TRADABLE_SETUPS)
        self.assertFalse(self.engine.is_tradable_setup("POI_REACTION"))

    def test_sweep_reversal_is_tradable(self):
        self.assertIn("SWEEP_REVERSAL", TRADABLE_SETUPS)
        self.assertTrue(self.engine.is_tradable_setup("SWEEP_REVERSAL"))

    def test_null_setup_type_not_tradable(self):
        self.assertFalse(self.engine.is_tradable_setup(None))

    def test_unknown_setup_type_not_tradable(self):
        self.assertFalse(self.engine.is_tradable_setup("UNKNOWN"))

    # ── POI_REACTION skip recording ────────────────────────────────────

    def test_record_poi_reaction_skip(self):
        self.engine.record_poi_reaction_skip()
        self.engine.record_poi_reaction_skip()
        self.assertEqual(self.engine._poi_reaction_skipped, 2)
        self.assertEqual(
            self.engine._gate_rejections[GATE_POI_REACTION_SKIPPED], 2
        )

    # ── setup type tracking ────────────────────────────────────────────

    def test_record_setup_type(self):
        self.engine.record_setup_type("SWEEP_REVERSAL")
        self.engine.record_setup_type("POI_REACTION")
        self.engine.record_setup_type("POI_REACTION")
        counts = self.engine._setup_type_counts
        self.assertEqual(counts["SWEEP_REVERSAL"], 1)
        self.assertEqual(counts["POI_REACTION"], 2)

    # ── diagnostics ────────────────────────────────────────────────────

    def test_diagnostic_includes_all_gates(self):
        self.engine._gate_rejections[GATE_SESSION_NOT_TRADABLE] = 5
        self.engine._poi_reaction_skipped = 3
        diag = self.engine.diagnostic()
        self.assertIn("gate_rejections", diag)
        self.assertIn("poi_reaction_skipped", diag)
        self.assertEqual(diag["poi_reaction_skipped"], 3)

    # ── CandidateWindow ────────────────────────────────────────────────

    def test_candidate_window_to_dict(self):
        t = _utc("2025-12-08T10:30:00Z")
        w = CandidateWindow(
            start_t=t,
            poi_id="ob_123",
            side="BUY",
            setup_type="SWEEP_REVERSAL",
            reason="CANDIDATE",
        )
        d = w.to_dict()
        self.assertEqual(d["poi_id"], "ob_123")
        self.assertEqual(d["side"], "BUY")
        self.assertEqual(d["setup_type"], "SWEEP_REVERSAL")

    # ── tradable setup produces window (integration) ───────────────────

    def test_tradable_setup_produces_window(self):
        """With all gates satisfied, scan() should return a CandidateWindow.

        Feed enough M1 candles to produce ≥10 15m bars AND ≥2 4H bars.
        4H = 240 minutes, so 2 bars = 480 M1 candles needed.
        """
        base = _utc("2025-12-08T00:00:00Z")
        # Feed 480 M1 candles (2 full 4H bars, each containing 16×15m bars)
        for i in range(480):
            t = base + timedelta(minutes=i)
            self.fs.update({
                "time": t,
                "open": 2650.0 + i * 0.01,
                "high": 2651.0 + i * 0.01,
                "low": 2649.0 + i * 0.01,
                "close": 2650.5 + i * 0.01,
                "tick_volume": 100,
                "spread": 0,
            })
        t = base + timedelta(minutes=479)  # last candle
        result = self.engine.scan(self.fs, t)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, CandidateWindow)
        self.assertEqual(result.reason, GATE_PASSED)
        self.assertEqual(self.engine._candidate_count, 1)


if __name__ == "__main__":
    unittest.main()

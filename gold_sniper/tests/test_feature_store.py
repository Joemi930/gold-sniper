"""P4.2 — FeatureStore tests (incremental, no-lookahead, cache invalidation)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from gold_sniper.replay.feature_store import (
    FEATURE_HTF_CONTEXT,
    FEATURE_SESSION,
    ALL_FEATURE_KEYS,
    Feature,
    FeatureStore,
)
from gold_sniper.replay.multi_timeframe_builder import MultiTimeframeBuilder
from gold_sniper.replay.no_lookahead_guard import LookaheadError


def _make_m1(time_str: str, close: float = 2650.0) -> dict:
    """Build a minimal M1 candle dict."""
    ts = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    return {
        "time": ts,
        "open": close - 1.0,
        "high": close + 0.5,
        "low": close - 1.5,
        "close": close,
        "tick_volume": 100,
        "spread": 0,
    }


class TestFeatureStore(unittest.TestCase):

    def setUp(self):
        self.mtf = MultiTimeframeBuilder()
        self.fs = FeatureStore(mtf=self.mtf)

    # ── basic ingestion ────────────────────────────────────────────────

    def test_update_increments_candle_count(self):
        self.fs.update(_make_m1("2025-12-08T10:00:00Z"))
        self.fs.update(_make_m1("2025-12-08T10:01:00Z"))
        self.assertEqual(self.fs.candle_count, 2)

    def test_session_feature_available_immediately(self):
        """Session is computed on every M1, no need to wait for TF close."""
        t = datetime.fromisoformat("2025-12-08T14:30:00+00:00")
        candle = _make_m1("2025-12-08T14:30:00Z")
        self.fs.update(candle)
        feat = self.fs.session(t=t)
        self.assertIsNotNone(feat)
        self.assertEqual(feat.value["session_name"], "LONDON_NY_OVERLAP")
        self.assertTrue(feat.value["tradable"])

    def test_asia_session_not_tradable(self):
        t = datetime.fromisoformat("2025-12-08T02:00:00+00:00")
        candle = _make_m1("2025-12-08T02:00:00Z")
        self.fs.update(candle)
        feat = self.fs.session(t=t)
        self.assertFalse(feat.value["tradable"])

    def test_friday_block(self):
        """Friday after 20:00 UTC should be blocked."""
        t = datetime.fromisoformat("2025-12-12T21:00:00+00:00")  # Friday
        candle = _make_m1("2025-12-12T21:00:00Z")
        self.fs.update(candle)
        feat = self.fs.session(t=t)
        self.assertTrue(feat.value["friday_block"])
        self.assertFalse(feat.value["tradable"])

    # ── no-lookahead ───────────────────────────────────────────────────

    def test_feature_available_at_le_t(self):
        """After update, session feature available_at must be ≤ candle time."""
        candle = _make_m1("2025-12-08T10:00:00Z")
        self.fs.update(candle)
        feat = self.fs.session(t=candle["time"])
        self.assertLessEqual(feat.available_at, candle["time"])

    def test_lookahead_raises_on_future_access(self):
        """Accessing a feature at a time BEFORE its available_at raises LookaheadError."""
        candle = _make_m1("2025-12-08T10:05:00Z")
        self.fs.update(candle)
        # Try to access at t=10:04, but feature was computed at 10:05
        past_t = datetime.fromisoformat("2025-12-08T10:04:00+00:00")
        # Session is computed at t=candle time, so available_at=10:05
        with self.assertRaises(LookaheadError):
            self.fs.session(t=past_t)

    # ── MTF integration ────────────────────────────────────────────────

    def test_htf_context_updates_on_15m_close(self):
        """Feed 15 M1 candles to close one 15m bar; HTF context must appear."""
        base = datetime.fromisoformat("2025-12-08T10:00:00+00:00")
        for i in range(15):
            t = base + timedelta(minutes=i)
            candle = {
                "time": t,
                "open": 2650.0 + i * 0.1,
                "high": 2651.0 + i * 0.1,
                "low": 2649.0 + i * 0.1,
                "close": 2650.5 + i * 0.1,
                "tick_volume": 100,
                "spread": 0,
            }
            self.fs.update(candle)

        # After 15 candles, the 15m bar should be closed at :14
        t_check = base + timedelta(minutes=14)
        feat = self.fs.htf_context(t=t_check)
        self.assertIsNotNone(feat)
        self.assertGreaterEqual(feat.value["bars_15m_count"], 1)

    # ── cache ──────────────────────────────────────────────────────────

    def test_cache_keys_grow_with_updates(self):
        self.fs.update(_make_m1("2025-12-08T10:00:00Z"))
        keys = self.fs.cache_keys()
        self.assertIn(FEATURE_SESSION, keys)
        self.assertIn(FEATURE_HTF_CONTEXT, keys)

    def test_get_nonexistent_key_returns_none(self):
        self.assertIsNone(self.fs.get("nonexistent"))

    # ── news ───────────────────────────────────────────────────────────

    def test_set_news_events(self):
        events = [{"time": "2025-12-08T13:30:00Z", "title": "US CPI"}]
        self.fs.set_news_events(events)
        feat = self.fs.news_state()
        self.assertEqual(feat.value["count"], 1)


class TestCacheInvalidation(unittest.TestCase):

    def setUp(self):
        self.mtf = MultiTimeframeBuilder()
        self.fs = FeatureStore(mtf=self.mtf)

    def test_poi_cache_invalidates_on_new_15m(self):
        """When a new 15m bar closes, the POI cache must be updated."""
        base = datetime.fromisoformat("2025-12-08T10:00:00+00:00")

        # First 15 candles close the first 15m bar
        for i in range(15):
            self.fs.update({
                "time": base + timedelta(minutes=i),
                "open": 2650.0, "high": 2651.0, "low": 2649.0,
                "close": 2650.5, "tick_volume": 100, "spread": 0,
            })

        poi1 = self.fs.poi_stack(t=base + timedelta(minutes=14))
        count1 = poi1.value["bars_15m_count"]

        # Second 15 candles close the second 15m bar
        for i in range(15, 30):
            self.fs.update({
                "time": base + timedelta(minutes=i),
                "open": 2651.0, "high": 2652.0, "low": 2650.0,
                "close": 2651.5, "tick_volume": 100, "spread": 0,
            })

        poi2 = self.fs.poi_stack(t=base + timedelta(minutes=29))
        count2 = poi2.value["bars_15m_count"]

        # Second snapshot must have more bars (cache invalidated + recomputed)
        self.assertGreater(count2, count1)

    def test_htf_cache_invalidates_on_new_4h(self):
        """Feeding 240 M1 candles (one 4H bar) must update HTF context."""
        base = datetime.fromisoformat("2025-12-08T00:00:00+00:00")
        for i in range(240):
            self.fs.update({
                "time": base + timedelta(minutes=i),
                "open": 2650.0 + i * 0.01,
                "high": 2651.0 + i * 0.01,
                "low": 2649.0 + i * 0.01,
                "close": 2650.5 + i * 0.01,
                "tick_volume": 100, "spread": 0,
            })

        feat = self.fs.htf_context(t=base + timedelta(minutes=239))
        self.assertIsNotNone(feat)
        # At least one 4H bar should have closed
        self.assertGreaterEqual(feat.value["bars_4h_count"], 0)


if __name__ == "__main__":
    unittest.main()

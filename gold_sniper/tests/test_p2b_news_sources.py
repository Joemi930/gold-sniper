"""P2-B News Sources tests — adapters without network."""

from __future__ import annotations

import unittest

from gold_sniper.data_pipeline.news_sources import (
    NewsFetchResult,
    build_fomc_static_events,
    build_manual_major_us_events_fixture,
    fetch_fmp_economic_calendar,
)


class TestP2bNewsSources(unittest.TestCase):
    def test_fmp_without_api_key_returns_missing(self):
        """FMP sans cle → ok=False + FMP_API_KEY_MISSING."""
        result = fetch_fmp_economic_calendar(
            start_date="2026-06-01",
            end_date="2026-06-17",
            api_key=None,
        )
        self.assertFalse(result.ok)
        self.assertIn("FMP_API_KEY_MISSING", result.errors)
        self.assertEqual(len(result.events), 0)

    def test_fomc_static_returns_events_in_june_2026(self):
        """FOMC static events include the June 2026 meeting."""
        result = build_fomc_static_events(
            start_date="2026-06-01T00:00:00Z",
            end_date="2026-06-30T23:59:59Z",
        )
        self.assertTrue(result.ok)
        self.assertGreater(len(result.events), 0)
        self.assertTrue(any("FOMC" in e.event for e in result.events))

    def test_fomc_static_empty_outside_fomc_window(self):
        """FOMC outside known meeting dates returns empty (but ok)."""
        result = build_fomc_static_events(
            start_date="2026-02-01T00:00:00Z",
            end_date="2026-02-15T23:59:59Z",
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.events), 0)

    def test_manual_fixture_is_ok_but_empty(self):
        """Manual fixture returns ok=True but empty events."""
        result = build_manual_major_us_events_fixture(
            start_date="2026-06-01T00:00:00Z",
            end_date="2026-06-17T23:59:59Z",
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.events), 0)
        self.assertIn("EMPTY_FIXTURE", result.metadata.get("warning", ""))

    def test_fetch_result_to_dict(self):
        """NewsFetchResult serializes correctly."""
        result = build_fomc_static_events(
            start_date="2026-06-01T00:00:00Z",
            end_date="2026-06-30T23:59:59Z",
        )
        d = result.to_dict()
        self.assertEqual(d["source"], "FED")
        self.assertTrue(d["ok"])
        self.assertIn("events", d)
        self.assertIsInstance(d["events"], list)


if __name__ == "__main__":
    unittest.main()

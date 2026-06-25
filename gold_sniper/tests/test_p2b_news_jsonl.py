"""P2-B News JSONL tests — normalize, write, read, validate."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from gold_sniper.data_pipeline.news_jsonl import (
    NormalizedNewsEvent,
    normalize_impact,
    normalize_news_event,
    normalize_news_events,
    parse_news_time,
    read_news_jsonl,
    write_news_jsonl,
)


class TestP2bNewsJsonl(unittest.TestCase):
    def test_normalize_converts_time_to_utc_iso(self):
        event = normalize_news_event({
            "time": "2026-06-12T12:30:00+00:00",
            "currency": "USD",
            "impact": "HIGH",
            "event": "CPI YoY",
        }, source="TEST")
        self.assertIn("+00:00", event.time)
        self.assertEqual(event.currency, "USD")
        self.assertEqual(event.impact, "HIGH")
        self.assertEqual(event.event, "CPI YoY")

    def test_currency_us_becomes_usd(self):
        event = normalize_news_event({
            "time": "2026-06-12T12:30:00Z", "currency": "US", "impact": "MEDIUM", "event": "Test",
        }, source="TEST")
        self.assertEqual(event.currency, "USD")

    def test_currency_usa_becomes_usd(self):
        event = normalize_news_event({
            "time": "2026-06-12T12:30:00Z", "currency": "USA", "impact": "LOW", "event": "Test",
        }, source="TEST")
        self.assertEqual(event.currency, "USD")

    def test_impact_red_becomes_high(self):
        event = normalize_news_event({
            "time": "2026-06-12T12:30:00Z", "currency": "USD", "impact": "RED", "event": "Test",
        }, source="TEST")
        self.assertEqual(event.impact, "HIGH")

    def test_impact_3_becomes_high(self):
        self.assertEqual(normalize_impact("3"), "HIGH")
        self.assertEqual(normalize_impact("2"), "MEDIUM")
        self.assertEqual(normalize_impact("1"), "LOW")

    def test_event_without_time_raises(self):
        with self.assertRaises(ValueError):
            normalize_news_event({"currency": "USD", "impact": "HIGH", "event": "Test"}, source="TEST")

    def test_event_without_name_raises(self):
        with self.assertRaises(ValueError):
            normalize_news_event({"time": "2026-06-12T12:30:00Z", "currency": "USD", "impact": "HIGH"}, source="TEST")

    def test_write_read_jsonl_roundtrip(self):
        events = [
            NormalizedNewsEvent(time="2026-06-12T12:30:00+00:00", currency="USD", impact="HIGH", event="CPI YoY", source="TEST"),
            NormalizedNewsEvent(time="2026-06-13T14:00:00+00:00", currency="USD", impact="MEDIUM", event="FOMC Minutes", source="TEST"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test_news.jsonl"
            write_news_jsonl(events, path)
            read_back = read_news_jsonl(path)
            self.assertEqual(len(read_back), 2)
            self.assertEqual(read_back[0].event, "CPI YoY")
            self.assertEqual(read_back[1].event, "FOMC Minutes")

    def test_normalize_news_events_deduplicates(self):
        rows = [
            {"time": "2026-06-12T12:30:00Z", "currency": "USD", "impact": "HIGH", "event": "CPI"},
            {"time": "2026-06-12T12:30:00Z", "currency": "USD", "impact": "HIGH", "event": "CPI"},  # duplicate
        ]
        events = normalize_news_events(rows, source="TEST")
        self.assertEqual(len(events), 1)

    def test_normalize_news_events_sorted_by_time(self):
        rows = [
            {"time": "2026-06-13T12:30:00Z", "currency": "USD", "impact": "HIGH", "event": "B"},
            {"time": "2026-06-12T12:30:00Z", "currency": "USD", "impact": "HIGH", "event": "A"},
        ]
        events = normalize_news_events(rows, source="TEST")
        self.assertEqual(events[0].event, "A")
        self.assertEqual(events[1].event, "B")

    def test_parse_news_time_handles_datetime_object(self):
        dt = datetime(2026, 6, 12, 12, 30, tzinfo=timezone.utc)
        result = parse_news_time(dt)
        self.assertEqual(result.hour, 12)

    def test_parse_news_time_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_news_time("")

    def test_normalize_impact_unknown(self):
        self.assertEqual(normalize_impact("RANDOM_VALUE"), "UNKNOWN")
        self.assertEqual(normalize_impact(None), "UNKNOWN")

    def test_actual_forecast_previous_preserved(self):
        event = normalize_news_event({
            "time": "2026-06-12T12:30:00Z", "currency": "USD", "impact": "HIGH",
            "event": "CPI", "actual": "3.1", "forecast": "3.0", "previous": "3.0",
        }, source="TEST")
        self.assertEqual(event.actual, "3.1")
        self.assertEqual(event.forecast, "3.0")
        self.assertEqual(event.previous, "3.0")


if __name__ == "__main__":
    unittest.main()

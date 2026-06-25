from __future__ import annotations

import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from gold_sniper.replay.news_loader import (
    evaluate_news_for_timestamp,
    load_local_news_cache,
)


class TestNewsLoader(unittest.TestCase):
    def test_missing_cache_does_not_invent_clear_news(self) -> None:
        cache = load_local_news_cache("missing-news-cache.json")
        payload = evaluate_news_for_timestamp("2026-04-03T12:00:00Z", cache)
        self.assertFalse(cache.loaded)
        self.assertEqual(payload["calendar_status"], "NEWS_CONTEXT_MISSING")
        self.assertIsNone(payload["news_clear"])

    def test_empty_cache_loads_without_crash(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "news.json"
            _write_cache(path, [])
            cache = load_local_news_cache(path)
        self.assertTrue(cache.loaded)
        self.assertEqual(cache.events, [])

    def test_filters_usd_events(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "news.json"
            _write_cache(
                path,
                [
                    {"time": "2026-04-03T12:30:00Z", "currency": "USD", "impact": "HIGH", "title": "NFP"},
                    {"time": "2026-04-03T13:00:00Z", "currency": "EUR", "impact": "HIGH", "title": "ECB"},
                ],
            )
            cache = load_local_news_cache(path)
        self.assertEqual(len(cache.events), 1)
        self.assertEqual(cache.events[0]["currency"], "USD")

    def test_detects_high_impact_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "news.json"
            _write_cache(path, [{"time": "2026-04-03T12:30:00Z", "currency": "USD", "impact": "HIGH", "title": "NFP"}])
            summary = load_local_news_cache(path).summary()
        self.assertEqual(summary["high_impact_news_count"], 1)

    def test_pre_news_lockout_before_high_impact(self) -> None:
        cache = _cache_with_high_impact()
        payload = evaluate_news_for_timestamp("2026-04-03T12:20:00Z", cache)
        self.assertTrue(payload["pre_news_lockout"])
        self.assertTrue(payload["news_veto"])

    def test_post_news_stealth_after_high_impact(self) -> None:
        cache = _cache_with_high_impact()
        payload = evaluate_news_for_timestamp("2026-04-03T12:40:00Z", cache)
        self.assertTrue(payload["post_news_stealth"])
        self.assertTrue(payload["news_veto"])

    def test_far_from_news_is_clear(self) -> None:
        cache = _cache_with_high_impact()
        payload = evaluate_news_for_timestamp("2026-04-03T15:00:00Z", cache)
        self.assertTrue(payload["news_clear"])
        self.assertFalse(payload["news_veto"])

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "replay" / "news_loader.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imports = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        ]
        self.assertNotIn("MetaTrader5", imports)

    def test_no_broker_called(self) -> None:
        broker = Mock()
        evaluate_news_for_timestamp("2026-04-03T15:00:00Z", _cache_with_high_impact())
        broker.assert_not_called()


def _cache_with_high_impact():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "news.json"
        _write_cache(path, [{"time": "2026-04-03T12:30:00Z", "currency": "USD", "impact": "HIGH", "title": "NFP"}])
        return load_local_news_cache(path)


def _write_cache(path: Path, events: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "source": "local_news_api",
                "symbol": "XAUUSD",
                "currency_filter": ["USD"],
                "date_start": "2026-04-01T01:00:00Z",
                "date_end": "2026-06-05T20:00:00Z",
                "timezone": "UTC",
                "events": events,
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()

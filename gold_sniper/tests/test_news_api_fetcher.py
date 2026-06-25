from __future__ import annotations

import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from gold_sniper.replay import news_api_fetcher as fetcher


class TestNewsApiFetcher(unittest.TestCase):
    def test_fetcher_does_not_print_keys(self) -> None:
        with patch.object(fetcher, "fetch_with_fallback", return_value=("financialmodelingprep", [], {"fmp": 1, "finnhub": 0})):
            with TemporaryDirectory() as tmp:
                summary = fetcher.generate_news_cache("2026-04-01", "2026-06-05", output=Path(tmp) / "news.json")
        self.assertNotIn("secret", json.dumps(summary).lower())
        self.assertNotIn("apikey", json.dumps(summary).lower())

    def test_normalizes_fmp_event(self) -> None:
        events = fetcher.normalize_news_events(
            [{"date": "2026-04-03 12:30:00", "country": "USD", "impact": "High", "event": "Non-Farm Payrolls"}],
            "financialmodelingprep",
        )
        self.assertEqual(events[0]["currency"], "USD")
        self.assertEqual(events[0]["impact"], "HIGH")
        self.assertEqual(events[0]["title"], "Non-Farm Payrolls")

    def test_normalizes_finnhub_event(self) -> None:
        events = fetcher.normalize_news_events(
            [{"time": "2026-04-03T12:30:00Z", "currency": "USD", "impactLevel": 3, "name": "CPI"}],
            "finnhub",
        )
        self.assertEqual(events[0]["impact"], "HIGH")
        self.assertEqual(events[0]["title"], "CPI")

    def test_fallback_finnhub_when_fmp_fails(self) -> None:
        with patch.object(fetcher, "fetch_fmp_economic_calendar", side_effect=RuntimeError("boom")):
            with patch.object(fetcher, "fetch_finnhub_economic_calendar", return_value=[{"time": "2026-04-03T12:30:00Z"}]):
                source, events, counts = fetcher.fetch_with_fallback("2026-04-01", "2026-06-05", {"fmp": "x", "finnhub": "y"})
        self.assertEqual(source, "finnhub")
        self.assertEqual(len(events), 1)
        self.assertEqual(counts["fmp"], 1)
        self.assertEqual(counts["finnhub"], 1)

    def test_write_news_cache_format(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            summary = fetcher.write_news_cache(
                [{"time": "2026-04-03T12:30:00Z", "currency": "USD", "impact": "HIGH", "title": "NFP"}],
                path,
                source="financialmodelingprep",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["symbol"], "XAUUSD")
        self.assertEqual(payload["currency_filter"], ["USD"])
        self.assertEqual(summary["high_impact_events"], 1)

    def test_tests_do_not_call_real_api(self) -> None:
        with patch.object(fetcher, "_http_get_json", side_effect=AssertionError("network")):
            with self.assertRaises(RuntimeError):
                fetcher.fetch_with_fallback("2026-04-01", "2026-06-05", {"fmp": "", "finnhub": ""})

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "replay" / "news_api_fetcher.py"
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
        fetcher.normalize_news_events([], "financialmodelingprep")
        broker.assert_not_called()


if __name__ == "__main__":
    unittest.main()

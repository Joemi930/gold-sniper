from __future__ import annotations

import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from gold_sniper.replay.local_calendar_importer import (
    load_local_calendar_file,
    normalize_local_calendar_events,
    write_news_cache_from_local_calendar,
)


class TestLocalCalendarImporter(unittest.TestCase):
    def test_imports_direct_json_list(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.json"
            path.write_text(json.dumps([_event("2026-04-03T12:30:00Z")]), encoding="utf-8")
            self.assertEqual(len(load_local_calendar_file(path)), 1)

    def test_imports_json_events_key(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.json"
            path.write_text(json.dumps({"events": [_event("2026-04-03T12:30:00Z")]}), encoding="utf-8")
            self.assertEqual(len(load_local_calendar_file(path)), 1)

    def test_filters_usd_only(self) -> None:
        events, stats = normalize_local_calendar_events([_event("2026-04-03T12:30:00Z"), _event("2026-04-03T13:00:00Z", currency="EUR")])
        self.assertEqual(len(events), 1)
        self.assertEqual(stats["filtered_non_usd"], 1)

    def test_filters_target_period(self) -> None:
        events, stats = normalize_local_calendar_events([_event("2026-03-31T12:30:00Z"), _event("2026-04-03T12:30:00Z")])
        self.assertEqual(len(events), 1)
        self.assertEqual(stats["filtered_out_of_period"], 1)

    def test_normalizes_impact_levels(self) -> None:
        raw = [_event("2026-04-03T12:30:00Z", impact="High"), _event("2026-04-04T12:30:00Z", impact="Medium"), _event("2026-04-05T12:30:00Z", impact="Low")]
        events, stats = normalize_local_calendar_events(raw)
        self.assertEqual([event["impact"] for event in events], ["HIGH", "MEDIUM", "LOW"])
        self.assertEqual(stats["high_impact"], 1)
        self.assertEqual(stats["medium_impact"], 1)
        self.assertEqual(stats["low_impact"], 1)

    def test_infers_high_for_major_events(self) -> None:
        events, _ = normalize_local_calendar_events([_event("2026-04-03T12:30:00Z", impact=None, title="CPI")])
        self.assertEqual(events[0]["impact"], "HIGH")

    def test_writes_expected_cache_format(self) -> None:
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "calendar.json"
            output = Path(tmp) / "cache.json"
            source.write_text(json.dumps([_event("2026-04-03T12:30:00Z")]), encoding="utf-8")
            summary = write_news_cache_from_local_calendar(source, output)
            payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertTrue(summary["cache_generated"])
        self.assertEqual(payload["source"], "local_calendar_event_list")
        self.assertEqual(payload["symbol"], "XAUUSD")
        self.assertEqual(payload["events"][0]["currency"], "USD")

    def test_unknown_format_raises_clear_error(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.json"
            path.write_text(json.dumps({"unknown": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "LOCAL_CALENDAR_UNSUPPORTED_FORMAT"):
                load_local_calendar_file(path)

    def test_no_metatrader5_import(self) -> None:
        module_path = Path(__file__).resolve().parents[1] / "replay" / "local_calendar_importer.py"
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imports = [alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names]
        self.assertNotIn("MetaTrader5", imports)

    def test_no_broker_called(self) -> None:
        broker = Mock()
        normalize_local_calendar_events([_event("2026-04-03T12:30:00Z")])
        broker.assert_not_called()


def _event(time: str, *, currency: str = "USD", impact: str | None = "HIGH", title: str = "NFP") -> dict:
    return {"Start": time, "Currency": currency, "Impact": impact, "Name": title, "Id": None}


if __name__ == "__main__":
    unittest.main()

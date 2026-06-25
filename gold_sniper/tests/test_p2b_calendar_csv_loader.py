from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gold_sniper.replay.economic_calendar import load_calendar_result, load_economic_calendar_csv


CSV = (
    "Id,Start,Name,Impact,Currency\n"
    "a,05/27/2026 12:30:00,Core Durable Goods Orders,HIGH,USD\n"
    "b,06/01/2026 09:00,NFP Preview,medium,USD\n"
    "b,06/01/2026 09:00,NFP Preview,medium,USD\n"
    "c,06/08/2026 09:00,Outside Window,LOW,EUR\n"
)


class TestP2bCalendarCsvLoader(unittest.TestCase):
    def test_calendar_csv_loader_loads_events_in_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "calendar.csv"
            csv_path.write_text(CSV, encoding="utf-8")

            result = load_calendar_result(
                csv_path,
                start="2026-05-27T00:00:00Z",
                end="2026-06-05T23:59:59Z",
            )

        self.assertFalse(result.missing)
        self.assertFalse(result.empty)
        self.assertEqual(result.source_format, "CSV")
        self.assertEqual(result.raw_events_count, 4)
        self.assertEqual(result.loaded_events_count, 3)
        self.assertEqual(result.filtered_events_count, 1)
        self.assertEqual(result.duplicate_id_count, 1)
        self.assertEqual(result.duplicate_key_count, 1)
        self.assertTrue(all(event["time"].tzinfo is not None for event in result.events))
        self.assertEqual(result.events[0]["impact"], "HIGH")
        self.assertEqual(result.events[1]["impact"], "MEDIUM")

    def test_calendar_csv_empty_window_is_empty_not_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "calendar.csv"
            csv_path.write_text(CSV, encoding="utf-8")

            result = load_calendar_result(
                csv_path,
                start="2026-07-01T00:00:00Z",
                end="2026-07-02T00:00:00Z",
            )

        self.assertFalse(result.missing)
        self.assertTrue(result.empty)
        self.assertEqual(result.raw_events_count, 4)
        self.assertEqual(result.loaded_events_count, 0)

    def test_calendar_csv_invalid_line_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "calendar.csv"
            csv_path.write_text(
                "Id,Start,Name,Impact,Currency\nx,not-a-date,Bad,HIGH,USD\n",
                encoding="utf-8",
            )

            result = load_calendar_result(csv_path)

        self.assertTrue(result.missing)
        self.assertTrue(result.errors)

    def test_loader_direct_csv_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "calendar.csv"
            csv_path.write_text(CSV, encoding="utf-8")

            events = load_economic_calendar_csv(csv_path)

        self.assertEqual(events[0]["source"], "CSV_CALENDAR_EVENT_LIST")
        self.assertIn("time_utc", events[0])
        self.assertEqual(events[0]["currency"], "USD")


if __name__ == "__main__":
    unittest.main()

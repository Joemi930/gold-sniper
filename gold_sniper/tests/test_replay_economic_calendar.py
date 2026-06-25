from __future__ import annotations

from datetime import timezone
import tempfile
import unittest
from pathlib import Path

from replay.economic_calendar import load_economic_calendar_jsonl


class TestReplayEconomicCalendar(unittest.TestCase):
    def test_load_jsonl_filters_sorts_and_normalizes_currency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"time":"2026-04-01T09:00:00Z","name":"CPI","impact":"HIGH","country":"USD"}',
                        '{"time":"2026-03-31T23:00:00Z","name":"Old","impact":"LOW","country":"USD"}',
                        '{"time":"2026-04-01T08:00:00Z","name":"NFP","impact":"HIGH","currency":"USD"}',
                    ]
                ),
                encoding="utf-8",
            )

            events = load_economic_calendar_jsonl(path, start="2026-04-01T00:00:00Z", end="2026-04-01T23:59:59Z")

        self.assertEqual([event["name"] for event in events], ["NFP", "CPI"])
        self.assertEqual(events[0]["time"].tzinfo, timezone.utc)
        self.assertEqual(events[1]["currency"], "USD")

    def test_missing_jsonl_returns_empty_calendar(self) -> None:
        self.assertEqual(load_economic_calendar_jsonl("missing_calendar.jsonl"), [])

    def test_load_jsonl_accepts_event_field_and_uppercases_impact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.jsonl"
            path.write_text(
                '{"time":"2026-05-21T12:30:00Z","currency":"USD","impact":"high","event":"Unemployment Claims"}\n',
                encoding="utf-8",
            )

            events = load_economic_calendar_jsonl(path)

        self.assertEqual(events[0]["name"], "Unemployment Claims")
        self.assertEqual(events[0]["event"], "Unemployment Claims")
        self.assertEqual(events[0]["impact"], "HIGH")

    def test_load_jsonl_rejects_missing_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "calendar.jsonl"
            path.write_text('{"time":"2026-05-21T12:30:00Z","currency":"USD","impact":"HIGH"}\n', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing event"):
                load_economic_calendar_jsonl(path)


if __name__ == "__main__":
    unittest.main()

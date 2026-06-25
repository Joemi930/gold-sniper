from __future__ import annotations

import unittest
from datetime import datetime, timezone

from replay.economic_calendar import news_context_at


class TestP1EconomicCalendarAlignment(unittest.TestCase):
    def test_missing_calendar_is_replay_invalid(self):
        context = news_context_at([], datetime(2026, 1, 1, 12, tzinfo=timezone.utc), calendar_missing=True)
        self.assertTrue(context.replay_invalid)
        self.assertEqual(context.status, "MISSING")
        self.assertEqual(context.reason, "NEWS_CALENDAR_MISSING")

    def test_high_impact_window_blocks(self):
        events = [{
            "time": datetime(2026, 1, 1, 12, 30, tzinfo=timezone.utc),
            "impact": "HIGH",
            "event": "CPI",
            "currency": "USD",
            "actual": "999",
        }]
        context = news_context_at(events, datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc))
        self.assertTrue(context.blocked)
        self.assertEqual(context.reason, "NEWS_HIGH_IMPACT_WINDOW")
        self.assertFalse(hasattr(context, "actual"))


if __name__ == "__main__":
    unittest.main()

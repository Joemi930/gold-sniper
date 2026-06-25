from __future__ import annotations

from datetime import datetime, timezone
import unittest

from agents.agent_7_chronos import check_session_context
from core.strategy_dictionary import select_active_strategy


class TestAgent7Sessions(unittest.TestCase):
    def test_london_open_allowed(self) -> None:
        context = check_session_context(datetime(2026, 1, 5, 7, 30, tzinfo=timezone.utc))

        self.assertEqual(context["session"], "LONDON_OPEN")
        self.assertTrue(context["trading_allowed"])

    def test_ny_open_allowed_and_strategy_compatible(self) -> None:
        context = check_session_context(datetime(2026, 1, 5, 12, 30, tzinfo=timezone.utc))
        strategy = select_active_strategy(context["session"], "TRENDING")

        self.assertEqual(context["session"], "NY_OPEN")
        self.assertTrue(context["trading_allowed"])
        self.assertEqual(strategy.name, "NY_OPEN_CONTINUATION")

    def test_overlap_allowed_after_ny_open_window(self) -> None:
        context = check_session_context(datetime(2026, 1, 5, 15, 30, tzinfo=timezone.utc))

        self.assertEqual(context["session"], "OVERLAP")
        self.assertTrue(context["trading_allowed"])

    def test_tokyo_is_blocked_by_default(self) -> None:
        context = check_session_context(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc))

        self.assertEqual(context["session"], "TOKYO")
        self.assertFalse(context["trading_allowed"])

    def test_rollover_is_blocked(self) -> None:
        context = check_session_context(datetime(2026, 1, 5, 22, 50, tzinfo=timezone.utc))

        self.assertEqual(context["session"], "ROLLOVER")
        self.assertFalse(context["trading_allowed"])

    def test_friday_halt_is_blocked(self) -> None:
        context = check_session_context(datetime(2026, 1, 9, 20, 30, tzinfo=timezone.utc))

        self.assertEqual(context["session"], "FRIDAY_HALT")
        self.assertFalse(context["trading_allowed"])


if __name__ == "__main__":
    unittest.main()

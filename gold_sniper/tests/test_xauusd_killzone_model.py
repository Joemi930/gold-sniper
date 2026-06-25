from __future__ import annotations

import unittest

from gold_sniper.strategy.xauusd_killzone_model import evaluate_xauusd_killzone


class TestXauusdKillzoneModel(unittest.TestCase):
    def test_valid_new_york_timestamp_is_never_unknown(self) -> None:
        result = evaluate_xauusd_killzone("2026-04-01T11:30:00Z")
        self.assertNotEqual(result.session, "UNKNOWN")

    def test_invalid_timestamp_is_unknown_with_timestamp_invalid_reason(self) -> None:
        result = evaluate_xauusd_killzone("not-a-date")
        self.assertEqual(result.session, "UNKNOWN")
        self.assertEqual(result.reason, "TIMESTAMP_INVALID")

    def test_asia_is_mapping_only_no_execution(self) -> None:
        result = evaluate_xauusd_killzone("2026-04-02T01:00:00Z")
        self.assertEqual(result.session, "ASIA")
        self.assertFalse(result.session_allowed)
        self.assertEqual(result.reason, "ASIA_MAPPING_ONLY")

    def test_london_killzone_detected(self) -> None:
        result = evaluate_xauusd_killzone("2026-04-01T06:30:00Z")
        self.assertEqual(result.session, "LONDON_KILLZONE")
        self.assertTrue(result.session_allowed)

    def test_ny_killzone_detected(self) -> None:
        result = evaluate_xauusd_killzone("2026-04-01T11:30:00Z")
        self.assertEqual(result.session, "NY_KILLZONE")
        self.assertTrue(result.session_allowed)

    def test_silver_bullet_detected(self) -> None:
        result = evaluate_xauusd_killzone("2026-04-01T14:30:00Z")
        self.assertEqual(result.session, "SILVER_BULLET")
        self.assertTrue(result.session_allowed)


if __name__ == "__main__":
    unittest.main()

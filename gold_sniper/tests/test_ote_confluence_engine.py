from __future__ import annotations

import unittest

from gold_sniper.strategy.ote_confluence_engine import evaluate_ote_confluence


class TestOteConfluenceEngine(unittest.TestCase):
    def test_ote_062_079_and_0705_calculated_for_long_discount(self) -> None:
        result = evaluate_ote_confluence(100, 200, 130, "LONG", poi_confluence=True)
        self.assertTrue(result.range_valid)
        self.assertAlmostEqual(result.ote_low, 121.0)
        self.assertAlmostEqual(result.ote_high, 138.0)
        self.assertAlmostEqual(result.level_0705, 129.5)
        self.assertTrue(result.inside_ote)
        self.assertTrue(result.inside_discount_or_premium)
        self.assertTrue(result.scenario_ready)

    def test_ote_alone_without_poi_confluence_does_not_ready_scenario(self) -> None:
        result = evaluate_ote_confluence(100, 200, 130, "LONG", poi_confluence=False)
        self.assertTrue(result.inside_ote)
        self.assertFalse(result.scenario_ready)


if __name__ == "__main__":
    unittest.main()

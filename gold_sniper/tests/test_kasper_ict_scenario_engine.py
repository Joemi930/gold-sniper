from __future__ import annotations

import unittest

from gold_sniper.strategy.kasper_ict_scenario_engine import evaluate_kasper_ict_scenarios


class TestKasperIctScenarioEngine(unittest.TestCase):
    def test_choch_alone_does_not_create_tradable_scenario(self) -> None:
        result = evaluate_kasper_ict_scenarios({"choch": True, "session_allowed": True, "news_clear": True})
        best = result["best_scenario"]
        self.assertFalse(best["tradable"])

    def test_ote_alone_does_not_enter(self) -> None:
        result = evaluate_kasper_ict_scenarios({"inside_ote": True, "session_allowed": True, "news_clear": True})
        self.assertEqual(result["tradable_count"], 0)

    def test_liquidity_alone_does_not_enter(self) -> None:
        result = evaluate_kasper_ict_scenarios({"sweep_valid": True, "session_allowed": True, "news_clear": True})
        self.assertEqual(result["tradable_count"], 0)

    def test_near_miss_wait_not_hard_reject(self) -> None:
        result = evaluate_kasper_ict_scenarios(
            {
                "session_allowed": True,
                "news_clear": True,
                "htf_context_available": True,
                "dol_available": True,
                "poi_stars": 4,
                "scenario_ready": True,
                "risk_valid": True,
            }
        )
        best = result["best_scenario"]
        self.assertEqual(best["status"], "SCENARIO_NEAR_MISS")
        self.assertFalse(best["hard_block"])
        self.assertTrue(best["near_miss"])

    def test_complete_ote_confluence_is_tradable_shadow_scenario(self) -> None:
        result = evaluate_kasper_ict_scenarios(
            {
                "session_allowed": True,
                "news_clear": True,
                "htf_context_available": True,
                "dol_available": True,
                "poi_stars": 5,
                "scenario_ready": True,
                "micro_trigger": True,
                "risk_valid": True,
            }
        )
        best = result["best_scenario"]
        self.assertEqual(best["scenario_type"], "OTE_CONFLUENCE")
        self.assertTrue(best["tradable"])


if __name__ == "__main__":
    unittest.main()

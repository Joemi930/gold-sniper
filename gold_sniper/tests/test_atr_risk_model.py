from __future__ import annotations

import unittest

from gold_sniper.strategy.atr_risk_model import evaluate_atr_risk_plan


class TestAtrRiskModel(unittest.TestCase):
    def test_atr_risk_plan_refuses_impossible_sl(self) -> None:
        result = evaluate_atr_risk_plan(2000, "LONG", 10, structural_stop=2005)
        self.assertFalse(result.risk_valid)
        self.assertEqual(result.reason, "SL_IMPOSSIBLE")

    def test_atr_risk_plan_builds_valid_rr(self) -> None:
        result = evaluate_atr_risk_plan(2000, "LONG", 10, structural_stop=1985)
        self.assertTrue(result.risk_valid)
        self.assertEqual(result.rr_to_tp2, 2.0)


if __name__ == "__main__":
    unittest.main()

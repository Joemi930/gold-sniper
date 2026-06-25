from __future__ import annotations

import unittest

from config import MAX_RISK_PCT_PER_TRADE, TP1_RR, TP2_RR
from core.strategy_dictionary import STRATEGY_DICTIONARY, select_active_strategy
from utils.risk_calculator import SymbolSizingInfo, calculate_dynamic_lot


class TestStrategyDictionary(unittest.TestCase):
    def test_active_strategies_use_official_rr_and_risk_cap(self) -> None:
        for strategy in STRATEGY_DICTIONARY:
            with self.subTest(strategy=strategy.name):
                self.assertNotEqual(strategy.name, "DIAMOND_SETUP")
                self.assertEqual(strategy.tp1_rr, TP1_RR)
                self.assertEqual(strategy.tp2_rr, TP2_RR)
                self.assertLessEqual(strategy.risk_pct, MAX_RISK_PCT_PER_TRADE)

    def test_diamond_setup_never_selected_for_auto_trading(self) -> None:
        strategy = select_active_strategy("LONDON", "TRENDING", diamond_conditions_met=True)

        self.assertNotEqual(strategy.name, "DIAMOND_SETUP")

    def test_risk_modifier_cannot_exceed_one_percent_effective_risk(self) -> None:
        result = calculate_dynamic_lot(
            account_equity=10_000.0,
            entry_price=100.0,
            sl_price=99.0,
            risk_pct=1.0,
            risk_modifier=1.5,
            symbol_info=SymbolSizingInfo(volume_max=100.0),
        )

        self.assertEqual(result["effective_risk_pct"], 1.0)

    def test_reduced_strategy_risk_stays_reduced(self) -> None:
        result = calculate_dynamic_lot(
            account_equity=10_000.0,
            entry_price=100.0,
            sl_price=99.0,
            risk_pct=0.5,
            risk_modifier=1.0,
            symbol_info=SymbolSizingInfo(volume_max=100.0),
        )

        self.assertEqual(result["effective_risk_pct"], 0.5)


if __name__ == "__main__":
    unittest.main()

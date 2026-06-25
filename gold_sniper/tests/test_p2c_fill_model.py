from __future__ import annotations

import unittest

from replay.execution_model import ReplayExecutionModel
from replay.fill_model import apply_entry_costs, apply_exit_costs, resolve_intrabar_exit_priority


class TestP2cFillModel(unittest.TestCase):
    def test_entry_costs_buy_and_sell(self):
        model = ReplayExecutionModel()

        buy = apply_entry_costs(side="BUY", requested_entry=2000.0, execution_model=model)
        sell = apply_entry_costs(side="SELL", requested_entry=2000.0, execution_model=model)

        self.assertEqual(buy.fill_price, 2015.0)
        self.assertEqual(sell.fill_price, 1985.0)

    def test_exit_costs_buy_and_sell(self):
        model = ReplayExecutionModel()

        buy = apply_exit_costs(side="BUY", requested_exit=2020.0, execution_model=model)
        sell = apply_exit_costs(side="SELL", requested_exit=1980.0, execution_model=model)

        self.assertEqual(buy.fill_price, 2005.0)
        self.assertEqual(sell.fill_price, 1995.0)

    def test_news_multiplies_spread_and_slippage(self):
        model = ReplayExecutionModel()

        result = apply_entry_costs(side="BUY", requested_entry=2000.0, execution_model=model, news_blocked_or_near=True)

        self.assertEqual(result.spread_points, 40.0)
        self.assertEqual(result.slippage_points, 15.0)
        self.assertEqual(result.fill_price, 2035.0)

    def test_intrabar_buy_sl_has_priority_over_tp1(self):
        reason, level = resolve_intrabar_exit_priority(
            side="BUY",
            candle={"high": 2020.0, "low": 1980.0},
            sl=1990.0,
            tp1=2010.0,
        )

        self.assertEqual((reason, level), ("SL", 1990.0))

    def test_intrabar_sell_sl_has_priority_over_tp1(self):
        reason, level = resolve_intrabar_exit_priority(
            side="SELL",
            candle={"high": 2020.0, "low": 1980.0},
            sl=2010.0,
            tp1=1990.0,
        )

        self.assertEqual((reason, level), ("SL", 2010.0))

    def test_after_tp1_protected_sl_has_priority_over_tp2(self):
        reason, level = resolve_intrabar_exit_priority(
            side="BUY",
            candle={"high": 2050.0, "low": 2000.0},
            sl=1980.0,
            tp2=2040.0,
            protected_sl=2001.0,
            partial_closed=True,
        )

        self.assertEqual((reason, level), ("PROTECTED_SL", 2001.0))


if __name__ == "__main__":
    unittest.main()

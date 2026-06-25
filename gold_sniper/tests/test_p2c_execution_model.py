from __future__ import annotations

import unittest

from replay.execution_model import BrokerExecutionProfile, ReplayExecutionModel, build_default_execution_model


class TestP2cExecutionModel(unittest.TestCase):
    def test_default_model_matches_p2c_decisions(self):
        model = build_default_execution_model()

        self.assertEqual(model.initial_equity, 100.0)
        self.assertEqual(model.tp1_rr, 1.0)
        self.assertEqual(model.tp2_rr, 2.0)
        self.assertEqual(model.partial_close_pct, 50.0)
        self.assertEqual(model.be_plus_r, 0.5)
        self.assertEqual(model.profile.avg_spread_pips, 2.0)
        self.assertEqual(model.profile.points_per_pip, 10.0)
        self.assertEqual(model.profile.avg_spread_points, 20.0)
        self.assertEqual(model.profile.commission_per_lot_side_usd, 0.0)
        self.assertEqual(model.validate(), [])

    def test_be_plus_is_r_based_not_price_percent(self):
        model = build_default_execution_model()
        entry = 2000.0
        risk = 10.0

        self.assertEqual(entry + model.be_plus_r * risk, 2005.0)
        self.assertNotEqual(entry + model.be_plus_r * risk, entry * 1.01)

    def test_be_plus_001r_is_valid(self):
        self.assertEqual(ReplayExecutionModel(be_plus_r=0.01).validate(), [])

    def test_zero_spread_is_invalid(self):
        model = ReplayExecutionModel(profile=BrokerExecutionProfile(avg_spread_pips=0.0))

        self.assertIn("SPREAD_MUST_BE_POSITIVE", model.validate())


if __name__ == "__main__":
    unittest.main()

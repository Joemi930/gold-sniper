from __future__ import annotations

import unittest

from replay.replay_metrics import build_p1_replay_metrics


class TestP1ReplayReporting(unittest.TestCase):
    def test_build_p1_replay_metrics_returns_required_fields(self):
        decisions = [
            {"decision": "ENTER_FULL", "setup_grade": "A_PLUS", "score_before_veto": 88.0, "score_after_veto": 88.0, "risk_multiplier": 1.0},
            {"decision": "REJECT", "setup_grade": "D", "veto_code": "NEWS_HIGH_IMPACT_WINDOW", "score_before_veto": 10.0, "score_after_veto": 0.0, "risk_multiplier": 0.0, "hard_veto": True, "replay_invalid": False},
        ]
        metrics = build_p1_replay_metrics(decisions, errors=["test_error"])
        self.assertEqual(metrics["total_decisions"], 2)
        self.assertIn("ENTER_FULL", metrics["decision_counts"])
        self.assertIn("REJECT", metrics["decision_counts"])
        self.assertIn("A_PLUS", metrics["setup_grade_distribution"])
        self.assertIn("D", metrics["setup_grade_distribution"])
        self.assertIn("NEWS_HIGH_IMPACT_WINDOW", metrics["veto_breakdown"])
        self.assertEqual(metrics["hard_veto_count"], 1)
        self.assertEqual(metrics["replay_invalid_count"], 0)
        self.assertGreater(metrics["score_before_veto_avg"], 0)
        self.assertEqual(metrics["score_after_veto_avg"], 44.0)
        self.assertGreater(metrics["risk_multiplier_avg"], 0)
        self.assertTrue(metrics["determinism_hash"])
        self.assertTrue(metrics["reporting_complete"])
        self.assertEqual(metrics["errors_count"], 1)


if __name__ == "__main__":
    unittest.main()

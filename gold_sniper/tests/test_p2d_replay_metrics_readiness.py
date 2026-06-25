from __future__ import annotations

import unittest

from gold_sniper.replay.replay_metrics import build_p1_replay_metrics


class TestP2dReplayMetricsReadiness(unittest.TestCase):
    def test_readiness_distributions(self):
        decisions = [
            {"decision": "REJECT", "setup_grade": "D", "readiness_state": "REJECT", "readiness_reason": "HARD_VETO", "readiness_by_section": {"news": "REJECT"}},
            {"decision": "WAIT_FOR_TRIGGER", "setup_grade": "D", "readiness_state": "WAITING_TRIGGER", "readiness_reason": "POI_USABLE_WAITING_MICRO_TRIGGER", "readiness_by_section": {"micro": "WAITING_TRIGGER"}},
            {"decision": "WAIT_FOR_BETTER_PRICE", "setup_grade": "D", "readiness_state": "WAITING_POI", "readiness_reason": "CONTEXT_INTERESTING_WAITING_POI", "readiness_by_section": {"poi": "WAITING_POI"}},
            {"decision": "WATCH_ONLY", "setup_grade": "C", "readiness_state": "WATCH_ONLY", "readiness_reason": "POI_MEDIUM_CONTEXT_INTERESTING", "readiness_by_section": {"poi": "WATCH_ONLY"}},
        ]

        metrics = build_p1_replay_metrics(decisions)

        self.assertEqual(metrics["readiness_state_distribution"]["REJECT"], 1)
        self.assertEqual(metrics["readiness_reason_distribution"]["POI_USABLE_WAITING_MICRO_TRIGGER"], 1)
        self.assertEqual(metrics["readiness_by_section_distribution"]["poi"]["WAITING_POI"], 1)
        self.assertTrue(metrics["decision_distribution_non_degenerate"])


if __name__ == "__main__":
    unittest.main()

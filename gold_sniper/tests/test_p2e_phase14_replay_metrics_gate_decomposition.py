import unittest

from gold_sniper.replay.replay_metrics import build_replay_metrics


def _metrics():
    decisions = [
        {
            "decision": "WATCH_ONLY",
            "setup_type": "POI_REACTION",
            "setup_grade": "C",
            "poi_micro_synergy": True,
            "enter_eligible": False,
            "risk_reason": "ENTER_NOT_ELIGIBLE",
            "risk_multiplier": 0.0,
            "gate_primary_blocker": "SETUP_TYPE_POI_REACTION_NOT_TRADABLE",
            "gate_blockers": [
                "SETUP_TYPE_POI_REACTION_NOT_TRADABLE",
                "ENTER_ELIGIBILITY_FALSE",
                "RISK_NOT_ALLOWED",
            ],
            "gate_decomposition": {
                "primary_blocker": "SETUP_TYPE_POI_REACTION_NOT_TRADABLE",
                "blockers": [
                    "SETUP_TYPE_POI_REACTION_NOT_TRADABLE",
                    "ENTER_ELIGIBILITY_FALSE",
                    "RISK_NOT_ALLOWED",
                ],
            },
        },
        {
            "decision": "ENTER_FULL",
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "C",
            "poi_micro_synergy": True,
            "enter_eligible": True,
            "risk_reason": "SHADOW_RISK_ALLOCATED",
            "risk_multiplier": 0.5,
            "gate_primary_blocker": "NONE",
            "gate_blockers": [],
            "gate_decomposition": {"primary_blocker": "NONE", "blockers": []},
        },
    ]
    return build_replay_metrics(
        decisions,
        symbol="XAUUSD",
        timeframe="1m",
        date_start=None,
        date_end=None,
        data_profile={},
    )


class TestP2EPhase14ReplayMetricsGateDecomposition(unittest.TestCase):
    def test_metrics_expose_primary_blocker_distribution(self):
        self.assertEqual(_metrics()["gate_primary_blocker_distribution"]["SETUP_TYPE_POI_REACTION_NOT_TRADABLE"], 1)

    def test_metrics_expose_synergy_true_primary_distribution(self):
        self.assertEqual(_metrics()["synergy_true_gate_primary_blocker_distribution"]["NONE"], 1)

    def test_metrics_expose_risk_reason_by_primary(self):
        self.assertEqual(
            _metrics()["risk_reason_by_gate_primary_blocker"]["SETUP_TYPE_POI_REACTION_NOT_TRADABLE"]["ENTER_NOT_ELIGIBLE"],
            1,
        )

    def test_metrics_expose_synergy_true_enter_by_setup(self):
        self.assertEqual(_metrics()["synergy_true_enter_eligible_by_setup_type"]["SWEEP_REVERSAL"], 1)


if __name__ == "__main__":
    unittest.main()

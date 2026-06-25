import unittest
import sys
from pathlib import Path

GOLD_SNIPER_ROOT = Path(__file__).resolve().parents[1]
if str(GOLD_SNIPER_ROOT) not in sys.path:
    sys.path.insert(0, str(GOLD_SNIPER_ROOT))

from gold_sniper.replay.replay_metrics import build_replay_metrics


def _metrics():
    decisions = [
        {
            "decision": "WATCH_ONLY",
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "C",
            "poi_micro_synergy": True,
            "poi_micro_synergy_status": "SYNERGY_READY",
            "poi_micro_reason": "TEST",
            "micro_confirmed": True,
            "micro_inside_poi": True,
            "enter_eligible": True,
            "risk_multiplier": 0.5,
            "enter_eligibility_blockers": ["RISK_NOT_ALLOWED"],
        },
        {
            "decision": "WATCH_ONLY",
            "setup_type": "POI_REACTION",
            "setup_grade": "C",
            "poi_micro_synergy": False,
            "poi_micro_synergy_status": "POI_TOO_WEAK",
            "poi_micro_reason": "POI_REJECTED_FAILURE_CLASS",
            "micro_confirmed": True,
            "micro_inside_poi": False,
            "p1_evidence_bundle": {"poi": {}},
            "enter_eligible": False,
            "risk_multiplier": 0.0,
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


class TestP2EPhase12SynergyMetrics(unittest.TestCase):
    def test_poi_micro_synergy_count(self):
        self.assertEqual(_metrics()["poi_micro_synergy_count"], 1)

    def test_poi_micro_synergy_by_setup(self):
        self.assertEqual(_metrics()["poi_micro_synergy_by_setup"]["SWEEP_REVERSAL"], 1)

    def test_micro_confirmed_inside_poi_count(self):
        self.assertEqual(_metrics()["micro_confirmed_inside_poi_count"], 1)

    def test_micro_confirmed_without_poi_count(self):
        self.assertEqual(_metrics()["micro_confirmed_without_poi_count"], 1)

    def test_enter_eligible_by_setup(self):
        self.assertEqual(_metrics()["enter_eligible_by_setup"]["SWEEP_REVERSAL"], 1)

    def test_remaining_blockers_after_synergy_distribution(self):
        self.assertEqual(_metrics()["remaining_blockers_after_synergy_distribution"]["RISK_NOT_ALLOWED"], 1)


if __name__ == "__main__":
    unittest.main()

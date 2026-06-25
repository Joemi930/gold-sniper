import unittest
import sys
from pathlib import Path

GOLD_SNIPER_ROOT = Path(__file__).resolve().parents[1]
if str(GOLD_SNIPER_ROOT) not in sys.path:
    sys.path.insert(0, str(GOLD_SNIPER_ROOT))

from gold_sniper.replay.decision_pipeline import _p1_decision_payload
from gold_sniper.replay.replay_engine import ReplayEngine
from gold_sniper.strategy.contracts import EvidenceBundle


class _Decision:
    def to_dict(self):
        return {
            "decision": "WATCH_ONLY",
            "setup_grade": "C",
            "score_breakdown": {
                "setup_type": "SWEEP_REVERSAL",
                "setup_classification": {
                    "setup_type": "SWEEP_REVERSAL",
                    "evidence": {"signals": {}, "candidates": []},
                },
                "enter_eligibility": {"blockers": []},
            },
            "risk_plan": {},
        }


def _bundle():
    synergy = {
        "synergy": True,
        "status": "SYNERGY_READY",
        "reason": "TEST",
        "micro_confirmed": True,
        "micro_inside_poi": True,
        "micro_outside_poi": False,
        "upgraded_poi_status": "POI_READY",
        "effective_poi_status": "POI_READY",
        "remaining_blockers": [],
    }
    return EvidenceBundle(
        poi={
            "poi_micro_synergy": synergy,
            "poi_micro_synergy_enabled": True,
            "effective_poi_status": "POI_READY",
        },
        raw={"poi_micro_synergy": synergy},
    )


class TestP2EPhase12DecisionPayloadSynergy(unittest.TestCase):
    def _payload(self):
        return _p1_decision_payload(_bundle(), _Decision(), [])

    def test_decision_payload_exposes_poi_micro_synergy(self):
        self.assertTrue(self._payload()["poi_micro_synergy"])

    def test_decision_payload_exposes_micro_confirmed(self):
        self.assertTrue(self._payload()["micro_confirmed"])

    def test_decision_payload_exposes_micro_inside_poi(self):
        self.assertTrue(self._payload()["micro_inside_poi"])

    def test_decision_payload_exposes_effective_poi_status(self):
        self.assertEqual(self._payload()["effective_poi_status"], "POI_READY")

    def test_replay_engine_writes_synergy_fields_into_record(self):
        engine = ReplayEngine.__new__(ReplayEngine)
        engine._p1_decisions = []
        engine._record_p1_decision(
            {"time": "2026-06-04T10:41:00Z"},
            1,
            {
                "decision": "WATCH_ONLY",
                "poi_micro_synergy": True,
                "micro_confirmed": True,
                "micro_inside_poi": True,
                "effective_poi_status": "POI_READY",
            },
            "eval",
            True,
        )
        record = engine._p1_decisions[0]
        self.assertTrue(record["poi_micro_synergy"])
        self.assertTrue(record["micro_confirmed"])
        self.assertTrue(record["micro_inside_poi"])
        self.assertEqual(record["effective_poi_status"], "POI_READY")


if __name__ == "__main__":
    unittest.main()

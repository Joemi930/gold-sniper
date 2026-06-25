import unittest
import sys
from pathlib import Path

GOLD_SNIPER_ROOT = Path(__file__).resolve().parents[1]
if str(GOLD_SNIPER_ROOT) not in sys.path:
    sys.path.insert(0, str(GOLD_SNIPER_ROOT))

from agents.base_agent import AgentResult
from gold_sniper.replay.evidence_builder import build_evidence_bundle


def _agent2(score=55.0, *, hard_filter_pass=True):
    selected = {
        "schema_version": "p2a.poi.v1",
        "source": "test",
        "poi_type_normalized": "OB",
        "lifecycle_normalized": "FRESH",
        "price_bounds": {"low": 100.0, "high": 110.0},
        "score": score,
        "execution_readiness": "READY",
        "aligned_with_context": True,
        "missing_evidence": [],
    }
    return AgentResult(
        "agent_2",
        score,
        hard_filter_pass,
        "LONG",
        "OK",
        payload={
            "p2a_poi_connectivity": {
                "selected_poi": selected,
                "poi_candidates": [selected],
                "audit": {
                    "agent2_has_any_zone": True,
                    "selected_poi_present": True,
                    "poi_bounds_present": True,
                },
            }
        },
    )


def _agent5(*, inside=True):
    return AgentResult(
        "agent_5",
        95.0,
        inside,
        "LONG",
        "OK" if inside else "OUTSIDE",
        payload={
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": inside,
            "price_in_agent2_poi": inside,
            "trigger_outside_poi": not inside,
            "displacement_present": True,
            "reclaim_confirmed": True,
            "candles_1m_count": 20,
            "execution_readiness": "READY" if inside else "INVALID",
            "readiness_state": "READY" if inside else "INVALID",
            "readiness_reason": "MICRO_READY" if inside else "TRIGGER_OUTSIDE_POI",
        },
    )


class TestP2EPhase12EvidenceBuilderPOIMicroSynergy(unittest.TestCase):
    def test_selected_poi_and_confirmed_micro_inside_sets_synergy(self):
        bundle = build_evidence_bundle({"agent_2": _agent2(55.0), "agent_5": _agent5(inside=True)})
        self.assertTrue(bundle.poi["poi_micro_synergy_enabled"])
        self.assertTrue(bundle.poi["poi_micro_synergy"]["synergy"])
        self.assertEqual(bundle.poi["effective_poi_status"], "POI_READY")

    def test_micro_confirmed_outside_sets_false_reason(self):
        bundle = build_evidence_bundle({"agent_2": _agent2(55.0), "agent_5": _agent5(inside=False)})
        self.assertFalse(bundle.poi["poi_micro_synergy_enabled"])
        self.assertIn(bundle.poi["poi_micro_reason"], {"MICRO_NOT_CONFIRMED", "MICRO_OUTSIDE_POI"})

    def test_poi_too_weak_sets_false_reason(self):
        bundle = build_evidence_bundle({"agent_2": _agent2(0.0), "agent_5": _agent5(inside=True)})
        self.assertFalse(bundle.poi["poi_micro_synergy_enabled"])
        self.assertEqual(bundle.poi["poi_micro_reason"], "POI_QUALITY_ZERO")

    def test_fields_persist_in_poi_and_raw(self):
        bundle = build_evidence_bundle({"agent_2": _agent2(55.0), "agent_5": _agent5(inside=True)})
        self.assertIn("poi_micro_synergy", bundle.poi)
        self.assertIn("poi_micro_synergy", bundle.raw)
        self.assertIn("micro_confirmed", bundle.poi)
        self.assertIn("micro_inside_poi", bundle.poi)


if __name__ == "__main__":
    unittest.main()

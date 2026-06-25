"""P2-E Fix 1 tests for replay POI handoff into EvidenceBundle."""

from __future__ import annotations

import unittest

from gold_sniper.replay.evidence_builder import _poi_from_agent_2
from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource


def _poi_payload(*, readiness: str = "READY") -> dict:
    return {
        "poi_type_normalized": "OB",
        "lifecycle_normalized": "FRESH",
        "price_bounds": {"low": 2400.0, "high": 2405.0},
        "score": 72.0,
        "mitigation_pct": 10.0,
        "aligned_with_context": True,
        "execution_readiness": readiness,
    }


class TestP2ePoiHandoff(unittest.TestCase):
    def _obs(self, payload: dict) -> AgentObservation:
        return AgentObservation(
            agent_id="agent_2",
            source=EvidenceSource.POI,
            passed=True,
            score=72.0,
            confidence=0.72,
            reason="TEST",
            payload=payload,
        )

    def test_flat_payload_exposes_selected_poi(self) -> None:
        selected = _poi_payload()
        poi = _poi_from_agent_2(self._obs({
            "poi_available": True,
            "selected_poi": selected,
            "price_bounds": selected["price_bounds"],
            "execution_readiness": "READY",
            "p2a_connectivity_audit": {"agent2_has_any_zone": True},
        }))

        self.assertTrue(poi["poi_available"])
        self.assertTrue(poi["selected_poi_present"])
        self.assertEqual(poi["price_bounds"], {"low": 2400.0, "high": 2405.0})
        self.assertEqual(poi["execution_readiness"], "READY")
        self.assertEqual(poi["poi_semantic_status"], "POI_PRESENT_EXECUTABLE")
        self.assertEqual(poi["poi_failure_class"], "POI_SELECTED_READY")

    def test_nested_p2a_payload_exposes_selected_poi(self) -> None:
        selected = _poi_payload(readiness="WAITING_TRIGGER")
        poi = _poi_from_agent_2(self._obs({
            "p2a_poi_connectivity": {
                "selected_poi": selected,
                "poi_candidates": [selected],
                "audit": {
                    "agent2_has_any_zone": True,
                    "selected_poi_present": True,
                    "poi_bounds_present": True,
                },
            }
        }))

        self.assertTrue(poi["poi_available"])
        self.assertTrue(poi["selected_poi_present"])
        self.assertEqual(poi["poi_type_normalized"], "OB")
        self.assertEqual(poi["execution_readiness"], "WAITING_TRIGGER")
        self.assertEqual(poi["poi_semantic_status"], "POI_PRESENT_WAITING_TRIGGER")
        self.assertTrue(poi["connectivity_audit"]["agent2_has_any_zone"])

    def test_candidates_without_selected_count_as_available(self) -> None:
        candidate = _poi_payload(readiness="WAITING_TRIGGER")
        poi = _poi_from_agent_2(self._obs({
            "p2a_poi_connectivity": {
                "poi_candidates": [candidate],
                "selected_poi": None,
                "audit": {"agent2_has_any_zone": True},
            }
        }))

        self.assertTrue(poi["poi_available"])
        self.assertFalse(poi["selected_poi_present"])
        self.assertEqual(poi["price_bounds"], {"low": 2400.0, "high": 2405.0})
        self.assertEqual(poi["execution_readiness"], "WAITING_TRIGGER")
        self.assertEqual(poi["poi_failure_class"], "POI_CANDIDATES_ONLY")
        self.assertTrue(poi["connectivity_audit"]["agent2_has_any_zone"])

    def test_empty_payload_stays_unavailable(self) -> None:
        poi = _poi_from_agent_2(self._obs({}))

        self.assertFalse(poi["poi_available"])
        self.assertFalse(poi["selected_poi_present"])
        self.assertIn("POI_UNAVAILABLE", poi["missing_evidence"])
        self.assertEqual(poi["execution_readiness"], "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()

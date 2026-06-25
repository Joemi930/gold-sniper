"""Phase 2 POI semantics tests for Agent2 -> EvidenceBundle mapping."""

from __future__ import annotations

import unittest

from gold_sniper.replay.evidence_builder import _poi_from_agent_2
from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource


class TestP2eEvidencePoiSemantics(unittest.TestCase):
    def test_legacy_failed_agent2_preserves_selected_poi_semantics(self) -> None:
        selected = {
            "id": "ob-legacy",
            "poi_type_normalized": "OB",
            "lifecycle_normalized": "FRESH",
            "price_bounds": {"low": 2400.0, "high": 2405.0},
            "score": 78.0,
            "execution_readiness": "READY",
        }
        obs = AgentObservation(
            agent_id="agent_2",
            source=EvidenceSource.POI,
            passed=False,
            score=78.0,
            confidence=0.78,
            reason="AGENT2_LEGACY_REJECT",
            payload={
                "selected_poi": selected,
                "price_bounds": selected["price_bounds"],
                "poi_candidates": [selected],
                "execution_readiness": "READY",
                "p2a_connectivity_audit": {
                    "agent2_has_any_zone": True,
                    "selected_poi_present": True,
                    "poi_bounds_present": True,
                },
            },
        )

        poi = _poi_from_agent_2(obs)

        self.assertTrue(poi["poi_available"])
        self.assertTrue(poi["selected_poi_present"])
        self.assertTrue(poi["has_price_bounds"])
        self.assertEqual(poi["poi_semantic_status"], "POI_PRESENT_EXECUTABLE")
        self.assertEqual(poi["poi_failure_class"], "POI_PRESENT_LEGACY_REJECTED")
        self.assertNotIn("POI_UNAVAILABLE", poi["missing_evidence"])

    def test_candidates_only_get_explicit_failure_class(self) -> None:
        candidate = {
            "poi_type_normalized": "FVG",
            "lifecycle_normalized": "PARTIAL",
            "price_bounds": {"low": 2390.0, "high": 2394.0},
            "score": 61.0,
            "execution_readiness": "WAITING_TRIGGER",
        }
        obs = AgentObservation(
            agent_id="agent_2",
            source=EvidenceSource.POI,
            passed=False,
            score=61.0,
            confidence=0.61,
            reason="FVG_CANDIDATE",
            payload={
                "p2a_poi_connectivity": {
                    "poi_candidates": [candidate],
                    "selected_poi": None,
                    "audit": {"agent2_has_any_zone": True},
                }
            },
        )

        poi = _poi_from_agent_2(obs)

        self.assertTrue(poi["poi_semantic_available"])
        self.assertFalse(poi["poi_semantic_selected"])
        self.assertEqual(poi["poi_semantic_status"], "POI_PRESENT_WAITING_TRIGGER")
        self.assertEqual(poi["poi_failure_class"], "POI_CANDIDATES_ONLY")


if __name__ == "__main__":
    unittest.main()

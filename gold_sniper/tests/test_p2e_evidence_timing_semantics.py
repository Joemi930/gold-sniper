"""Pre-Phase 5 tests for Agent4 timing semantics in EvidenceBundle mapping."""

from __future__ import annotations

import unittest

from gold_sniper.replay.evidence_builder import _timing_from_agent_4
from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource


class TestP2eEvidenceTimingSemantics(unittest.TestCase):
    def test_agent4_handoff_and_readiness_are_preserved(self) -> None:
        obs = AgentObservation(
            agent_id="agent_4",
            source=EvidenceSource.TIMING,
            passed=True,
            score=55.0,
            confidence=0.55,
            reason="IN_CORRECT_ZONE_BUT_NOT_YET_IN_OTE - Attendre",
            payload={
                "in_ote": False,
                "premium_discount": "DISCOUNT",
                "timing_quality_score": 55.0,
                "execution_readiness": "WAIT_FOR_TRIGGER",
                "readiness_state": "WAIT_FOR_TRIGGER",
                "readiness_reason": "OTE_WAITING_PRICE",
                "ote_handoff_status": "P2A_POI_CONSUMED",
                "agent4_poi_handoff": {"source": "P2A_SELECTED_POI", "bounds_present": True},
                "agent4_consumed_poi": {
                    "present": True,
                    "bottom": 2400.0,
                    "top": 2405.0,
                    "type": "OB",
                    "execution_readiness": "READY",
                },
            },
        )

        timing = _timing_from_agent_4(obs)

        self.assertEqual(timing["readiness_state"], "WAIT_FOR_TRIGGER")
        self.assertEqual(timing["readiness_reason"], "OTE_WAITING_PRICE")
        self.assertEqual(timing["ote_handoff_status"], "P2A_POI_CONSUMED")
        self.assertEqual(timing["agent4_poi_handoff"]["source"], "P2A_SELECTED_POI")
        self.assertTrue(timing["agent4_consumed_poi"]["present"])


if __name__ == "__main__":
    unittest.main()

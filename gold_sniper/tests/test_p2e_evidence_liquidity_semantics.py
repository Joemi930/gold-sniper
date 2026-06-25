"""Phase 4 tests for Agent3 liquidity semantics in EvidenceBundle mapping."""

from __future__ import annotations

import unittest

from gold_sniper.replay.evidence_builder import _liquidity_from_agent_3
from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource


class TestP2eEvidenceLiquiditySemantics(unittest.TestCase):
    def test_agent3_handoff_and_readiness_are_preserved(self) -> None:
        obs = AgentObservation(
            agent_id="agent_3",
            source=EvidenceSource.LIQUIDITY,
            passed=False,
            score=45.0,
            confidence=0.45,
            reason="NO_SWEEP_DETECTED",
            payload={
                "liquidity_state": "NONE",
                "sweep_detected": False,
                "sweep_rejected": False,
                "execution_readiness": "WAIT_FOR_TRIGGER",
                "readiness_state": "WAIT_FOR_TRIGGER",
                "readiness_reason": "LIQUIDITY_WAITING_SWEEP",
                "liquidity_handoff_status": "P2A_POI_CONSUMED",
                "agent3_poi_handoff": {"source": "P2A_SELECTED_POI", "bounds_present": True},
                "agent3_consumed_poi": {
                    "present": True,
                    "bottom": 2400.0,
                    "top": 2405.0,
                    "type": "OB",
                    "execution_readiness": "READY",
                },
            },
        )

        liquidity = _liquidity_from_agent_3(obs)

        self.assertEqual(liquidity["readiness_state"], "WAIT_FOR_TRIGGER")
        self.assertEqual(liquidity["readiness_reason"], "LIQUIDITY_WAITING_SWEEP")
        self.assertEqual(liquidity["liquidity_handoff_status"], "P2A_POI_CONSUMED")
        self.assertEqual(liquidity["agent3_poi_handoff"]["source"], "P2A_SELECTED_POI")
        self.assertTrue(liquidity["agent3_consumed_poi"]["present"])


if __name__ == "__main__":
    unittest.main()

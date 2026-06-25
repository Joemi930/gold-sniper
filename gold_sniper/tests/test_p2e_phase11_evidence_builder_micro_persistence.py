"""P2-E Phase 11: EvidenceBuilder micro persistence tests.

Tests:
1. micro_evidence arrives in EvidenceBundle.micro.
2. micro_contract fields arrive in EvidenceBundle.micro.
3. sweep/choch/trigger/candles fields are not lost.
4. fallback legacy works when micro_evidence absent.
"""

import unittest

from gold_sniper.replay.evidence_builder import _micro_from_agent_5
from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource


class TestEvidenceBuilderMicroPersistence(unittest.TestCase):

    def _make_obs(self, **overrides):
        """Build a synthetic AgentObservation with micro fields."""
        payload = {
            "schema_version": "p1.agent_observation.v1",
            "agent_id": "agent_5",
            "status": "PARTIAL",
            "trigger_type": "MICRO_CHOCH",
            "displacement_present": True,
            "reclaim_confirmed": True,
            "retest_confirmed": False,
            "trigger_inside_poi": True,
            "amd_phase": "DISTRIBUTION",
            "trigger_strength": 85.0,
            "execution_readiness": "WAIT_FOR_TRIGGER",
            "readiness_state": "WAIT_FOR_TRIGGER",
            "readiness_reason": "MICRO_SWEEP_WAITING_CHOCH",
            "micro_handoff_status": "P2A_POI_CONSUMED",
            "agent5_poi_handoff": {"source": "P2A_SELECTED_POI"},
            "agent5_consumed_poi": {"present": True},
            "sweep_1m_confirmed": True,
            "choch_detected": False,
            "candles_1m_count": 15,
            "price_in_agent2_poi": True,
            "trigger_outside_poi": False,
            "trigger_confirmed": False,
            "unknown_fields": [],
        }
        payload.update(overrides)
        return AgentObservation(
            agent_id="agent_5",
            source=EvidenceSource.MICRO_CONFIRMATION,
            passed=False,
            score=30.0,
            confidence=0.3,
            reason="CHOCH_SANS_SWEEP",
            hard_filter_pass=False,
            payload=payload,
        )

    def test_micro_evidence_arrives_in_bundle_micro(self):
        obs = self._make_obs()
        micro = _micro_from_agent_5(obs)
        self.assertIn("micro_evidence", micro)
        self.assertIsInstance(micro["micro_evidence"], dict)
        self.assertTrue(micro["micro_evidence"].get("sweep_1m_confirmed"))

    def test_micro_contract_status_arrives_in_bundle_micro(self):
        obs = self._make_obs()
        micro = _micro_from_agent_5(obs)
        self.assertIn("micro_contract_status", micro)
        # sweep=True, choch=False → WAITING_TRIGGER
        self.assertEqual(micro["micro_contract_status"], "MICRO_WAITING_TRIGGER")

    def test_sweep_choch_candles_not_lost(self):
        obs = self._make_obs()
        micro = _micro_from_agent_5(obs)
        self.assertTrue(micro["sweep_1m_confirmed"])
        self.assertFalse(micro["choch_detected"])
        self.assertEqual(micro["candles_1m_count"], 15)

    def test_fallback_readiness_when_missing_data(self):
        """When micro_evidence is empty, fallback to legacy readiness."""
        obs = self._make_obs(sweep_1m_confirmed=None, choch_detected=None)
        micro = _micro_from_agent_5(obs)
        # Contract returns MISSING_DATA because all fields are None
        # Fallback uses payload execution_readiness
        self.assertEqual(micro["micro_contract_status"], "MICRO_MISSING_DATA")
        self.assertEqual(micro["execution_readiness"], "WAIT_FOR_TRIGGER")

    def test_micro_confirmed_with_all_signals(self):
        obs = self._make_obs(
            sweep_1m_confirmed=True,
            choch_detected=True,
            trigger_inside_poi=True,
            retest_confirmed=True,
            candles_1m_count=10,
        )
        micro = _micro_from_agent_5(obs)
        self.assertEqual(micro["micro_contract_status"], "MICRO_CONFIRMED")
        self.assertTrue(micro["micro_is_confirmed"])
        self.assertEqual(micro["execution_readiness"], "READY")

    def test_micro_outside_poi(self):
        obs = self._make_obs(
            sweep_1m_confirmed=True,
            choch_detected=True,
            trigger_outside_poi=True,
            candles_1m_count=10,
        )
        micro = _micro_from_agent_5(obs)
        self.assertEqual(micro["micro_contract_status"], "MICRO_OUTSIDE_POI")
        self.assertTrue(micro["micro_is_outside_poi"])


if __name__ == "__main__":
    unittest.main()

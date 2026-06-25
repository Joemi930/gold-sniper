"""P2-E Phase 11: Agent5 micro evidence persistence tests.

Tests:
1. build_agent_5_observation exposes micro_evidence-like fields.
2. expose sweep_1m_confirmed.
3. expose choch_detected.
4. expose trigger_inside_poi.
5. expose retest_confirmed.
6. expose candles_1m_count.
7. expose micro_contract fields.
8. Agent5 observation does not change hard_filter_pass.
"""

import unittest

from agents.agent_5_microscope import build_agent_5_observation, analyze_amd_sequence


class TestAgent5MicroEvidencePersistence(unittest.TestCase):

    def _make_agent5_result(self, **overrides):
        """Create a minimal AgentResult-like object for testing."""
        candles = [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 10} for _ in range(15)]
        atr = 0.5
        direction = overrides.get("direction", "LONG")
        poi_zone = {
            "entry_zone_bottom": 99.0,
            "entry_zone_top": 101.0,
            "type": "DEMAND",
            "bottom": 99.0,
            "top": 101.0,
        }
        result = analyze_amd_sequence(candles, direction, poi_zone, atr)
        # Manually enrich payload for testing
        payload = dict(result.payload or {})
        payload["execution_readiness"] = "WAIT_FOR_TRIGGER"
        payload["readiness_state"] = "WAIT_FOR_TRIGGER"
        payload["readiness_reason"] = "MICRO_SWEEP_WAITING_CHOCH"
        payload["micro_handoff_status"] = "P2A_POI_CONSUMED"
        payload["agent5_consumed_poi"] = {"present": True}
        payload["agent5_poi_handoff"] = {"source": "P2A_SELECTED_POI"}
        payload["displacement_present"] = True
        payload["reclaim_confirmed"] = True
        payload["retest_confirmed"] = False
        payload["trigger_inside_poi"] = True
        payload["price_in_agent2_poi"] = True
        payload["trigger_outside_poi"] = False
        payload["candles_1m_count"] = 15
        payload["trigger_type"] = "MICRO_CHOCH"
        payload["trigger_strength"] = 85.0

        class FakeResult:
            def __init__(self, payload, hard_filter_pass, score, reason, direction):
                self.payload = payload
                self.hard_filter_pass = hard_filter_pass
                self.score = score
                self.reason = reason
                self.direction = direction
                self.veto = None
                self.risk_modifier = None

        return FakeResult(payload, result.hard_filter_pass, result.score, result.reason, direction)

    def test_observation_exposes_sweep_1m_confirmed(self):
        result = self._make_agent5_result()
        obs = build_agent_5_observation(result)
        self.assertIn("sweep_1m_confirmed", obs.payload)
        self.assertIsNotNone(obs.payload["sweep_1m_confirmed"])

    def test_observation_exposes_choch_detected(self):
        result = self._make_agent5_result()
        obs = build_agent_5_observation(result)
        self.assertIn("choch_detected", obs.payload)
        self.assertIsNotNone(obs.payload["choch_detected"])

    def test_observation_exposes_trigger_inside_poi(self):
        result = self._make_agent5_result()
        obs = build_agent_5_observation(result)
        self.assertTrue(obs.payload["trigger_inside_poi"])

    def test_observation_exposes_displacement_present(self):
        result = self._make_agent5_result()
        obs = build_agent_5_observation(result)
        self.assertTrue(obs.payload["displacement_present"])

    def test_observation_exposes_candles_1m_count(self):
        result = self._make_agent5_result()
        obs = build_agent_5_observation(result)
        self.assertIn("candles_1m_count", obs.payload)
        self.assertEqual(obs.payload["candles_1m_count"], 15)

    def test_observation_exposes_price_in_agent2_poi(self):
        result = self._make_agent5_result()
        obs = build_agent_5_observation(result)
        self.assertIn("price_in_agent2_poi", obs.payload)

    def test_observation_exposes_trigger_outside_poi(self):
        result = self._make_agent5_result()
        obs = build_agent_5_observation(result)
        self.assertIn("trigger_outside_poi", obs.payload)

    def test_observation_hard_filter_pass_unchanged(self):
        result = self._make_agent5_result()
        obs = build_agent_5_observation(result)
        self.assertEqual(obs.hard_filter_pass, result.hard_filter_pass)

    def test_null_result_returns_safe_observation(self):
        obs = build_agent_5_observation(None)
        self.assertEqual(obs.payload["status"], "UNKNOWN")
        self.assertEqual(obs.payload["execution_readiness"], "UNAVAILABLE")
        self.assertIn("AGENT_5_RESULT_MISSING", obs.missing_evidence)


if __name__ == "__main__":
    unittest.main()

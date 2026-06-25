"""P2-E Phase 7E — Agent POI handoff contract tests.

Verifies Agent3, Agent4, Agent5 use the centralized poi_contract
and expose the correct handoff fields in their payloads.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

# Pre-existing project layout: agents import from 'agents.*' / 'core.*' / 'utils.*'
_HERE = os.path.dirname(os.path.abspath(__file__))
_GOLD_SNIPER = os.path.join(_HERE, "..")
if _GOLD_SNIPER not in sys.path:
    sys.path.insert(0, _GOLD_SNIPER)

from gold_sniper.agents.agent_3_liquidite import (
    _enrich_agent3_result_with_handoff,
    _extract_agent2_p2a_liquidity_anchor,
)
from gold_sniper.agents.agent_4_fibonacci import (
    enrich_agent4_result_with_handoff,
    extract_agent2_p2a_ote_anchor,
)
from gold_sniper.agents.agent_5_microscope import _extract_agent2_p2a_poi_for_agent5
from gold_sniper.agents.base_agent import AgentResult


def _mock_blackboard(agent2_payload=None, agent2_state=None):
    bb = MagicMock()
    agent_result = MagicMock()
    agent_result.payload = agent2_payload or {}
    bb.read_sync.return_value = agent_result
    bb.get_agent.return_value = agent2_state or {}
    return bb


def _p2a_selected_poi_payload():
    return {
        "p2a_poi_connectivity": {
            "selected_poi": {
                "price_bounds": {"low": 2400, "high": 2405},
                "type": "BULLISH_OB",
                "poi_type_normalized": "BULLISH_OB",
                "execution_readiness": "READY",
            },
            "poi_candidates": [
                {"price_bounds": {"low": 2390, "high": 2395}, "type": "BEARISH_OB"},
            ],
        },
    }


def _legacy_poi_payload():
    return {"poi_zone": {"bottom": 2300, "top": 2310}}


class TestAgent3HandoffContract(unittest.TestCase):
    """Agent3 must enrich payload with handoff + consumed_poi from centralized contract."""

    def test_agent3_enriches_with_agent3_poi_handoff(self):
        bb = _mock_blackboard(agent2_payload=_p2a_selected_poi_payload())
        anchor, handoff = _extract_agent2_p2a_liquidity_anchor(bb)
        result = AgentResult("agent_3", 80, True, "LONG", "SWEEP_BSL_CONFIRMED")
        enriched = _enrich_agent3_result_with_handoff(result, anchor, handoff)
        self.assertIn("agent3_poi_handoff", enriched.payload)
        self.assertIn("agent3_consumed_poi", enriched.payload)
        self.assertTrue(enriched.payload["agent3_consumed_poi"]["present"])

    def test_agent3_consumes_p2a_selected_poi(self):
        bb = _mock_blackboard(agent2_payload=_p2a_selected_poi_payload())
        anchor, handoff = _extract_agent2_p2a_liquidity_anchor(bb)
        self.assertIsNotNone(anchor)
        self.assertEqual(handoff["source"], "P2A_SELECTED_POI")
        self.assertEqual(anchor["agent3_handoff_source"], "P2A_SELECTED_POI")

    def test_agent3_no_legacy_when_p2a_exists(self):
        """Agent3 must not prefer legacy when selected_poi P2-A exists."""
        bb = _mock_blackboard(
            agent2_payload=_p2a_selected_poi_payload(),
            agent2_state=_legacy_poi_payload(),
        )
        anchor, handoff = _extract_agent2_p2a_liquidity_anchor(bb)
        self.assertEqual(handoff["source"], "P2A_SELECTED_POI")
        self.assertEqual(anchor["bottom"], 2400.0)  # from P2-A, not legacy 2300

    def test_agent3_legacy_fallback_when_p2a_absent(self):
        bb = _mock_blackboard(
            agent2_payload={},
            agent2_state=_legacy_poi_payload(),
        )
        anchor, handoff = _extract_agent2_p2a_liquidity_anchor(bb)
        self.assertEqual(handoff["source"], "LEGACY_AGENT2_FALLBACK")


class TestAgent4HandoffContract(unittest.TestCase):
    """Agent4 must enrich payload with handoff + consumed_poi from centralized contract."""

    def test_agent4_enriches_with_agent4_poi_handoff(self):
        bb = _mock_blackboard(agent2_payload=_p2a_selected_poi_payload())
        anchor, handoff = extract_agent2_p2a_ote_anchor(bb)
        result = AgentResult("agent_4", 85, True, "LONG", "IN_OTE")
        enriched = enrich_agent4_result_with_handoff(result, anchor, handoff)
        self.assertIn("agent4_poi_handoff", enriched.payload)
        self.assertIn("agent4_consumed_poi", enriched.payload)
        self.assertTrue(enriched.payload["agent4_consumed_poi"]["present"])

    def test_agent4_consumes_p2a_selected_poi(self):
        bb = _mock_blackboard(agent2_payload=_p2a_selected_poi_payload())
        anchor, handoff = extract_agent2_p2a_ote_anchor(bb)
        self.assertIsNotNone(anchor)
        self.assertEqual(handoff["source"], "P2A_SELECTED_POI")
        self.assertEqual(anchor["agent4_handoff_source"], "P2A_SELECTED_POI")


class TestAgent5HandoffContract(unittest.TestCase):
    """Agent5 must expose handoff diagnostics from centralized contract."""

    def test_agent5_diagnostic_exposes_p2a_selected_poi_source(self):
        bb = _mock_blackboard(agent2_payload=_p2a_selected_poi_payload())
        anchor, handoff = _extract_agent2_p2a_poi_for_agent5(bb)
        self.assertIsNotNone(anchor)
        self.assertEqual(handoff["source"], "P2A_SELECTED_POI")
        self.assertEqual(anchor["agent5_handoff_source"], "P2A_SELECTED_POI")

    def test_agent5_diagnostic_exposes_bounds(self):
        bb = _mock_blackboard(agent2_payload=_p2a_selected_poi_payload())
        anchor, handoff = _extract_agent2_p2a_poi_for_agent5(bb)
        self.assertTrue(handoff["bounds_present"])
        self.assertEqual(anchor["bottom"], 2400.0)
        self.assertEqual(anchor["top"], 2405.0)

    def test_agent5_no_legacy_when_p2a_exists(self):
        """Agent5 must not prefer legacy when selected_poi P2-A exists."""
        bb = _mock_blackboard(
            agent2_payload=_p2a_selected_poi_payload(),
            agent2_state=_legacy_poi_payload(),
        )
        anchor, handoff = _extract_agent2_p2a_poi_for_agent5(bb)
        self.assertEqual(handoff["source"], "P2A_SELECTED_POI")

    def test_agent5_legacy_fallback_when_p2a_absent(self):
        bb = _mock_blackboard(
            agent2_payload={},
            agent2_state=_legacy_poi_payload(),
        )
        anchor, handoff = _extract_agent2_p2a_poi_for_agent5(bb)
        self.assertEqual(handoff["source"], "LEGACY_AGENT2_FALLBACK")


if __name__ == "__main__":
    unittest.main()

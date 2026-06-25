"""P2-A POI Connectivity tests — Agent 2 observation exposes selected_poi and bounds."""

from __future__ import annotations

import unittest

from agents.base_agent import AgentResult
from agents.agent_2_cartographe import build_agent_2_observation
from gold_sniper.strategy.contracts import AgentObservation


class TestP2aPoiConnectivity(unittest.TestCase):
    def test_agent2_observation_exposes_selected_poi_and_bounds(self):
        """Cas 1: OB complet — selected_poi dict avec price_bounds, type, lifecycle, readiness."""
        result = AgentResult(
            agent_id="agent_2",
            score=72,
            hard_filter_pass=True,
            direction="LONG",
            reason="UNIT_OB",
            payload={
                "p2a_poi_connectivity": {
                    "schema_version": "p2a.poi_connectivity.v1",
                    "poi_candidates": [
                        {
                            "poi_type_normalized": "OB",
                            "lifecycle_normalized": "FRESH",
                            "price_bounds": {"low": 2400.0, "high": 2405.0},
                            "score": 72,
                            "mitigation_pct": 12.0,
                            "session_created": "NY_OPEN",
                            "aligned_with_context": True,
                            "execution_readiness": "READY",
                            "missing_evidence": [],
                            "warnings": [],
                        }
                    ],
                    "selected_poi": {
                        "poi_type_normalized": "OB",
                        "lifecycle_normalized": "FRESH",
                        "price_bounds": {"low": 2400.0, "high": 2405.0},
                        "score": 72,
                        "mitigation_pct": 12.0,
                        "session_created": "NY_OPEN",
                        "aligned_with_context": True,
                        "execution_readiness": "READY",
                        "missing_evidence": [],
                        "warnings": [],
                    },
                    "audit": {
                        "agent2_has_any_zone": True,
                        "agent2_has_selected_ob": True,
                        "selected_poi_present": True,
                        "poi_bounds_present": True,
                    },
                }
            },
        )
        obs = build_agent_2_observation(result)
        self.assertIsInstance(obs, AgentObservation)
        p = obs.payload
        self.assertTrue(p["poi_available"])
        self.assertTrue(p["selected_poi_present"])
        self.assertIsInstance(p["selected_poi"], dict)
        self.assertEqual(p["selected_poi"]["price_bounds"]["low"], 2400.0)
        self.assertEqual(p["selected_poi"]["price_bounds"]["high"], 2405.0)
        self.assertEqual(p["poi_type_normalized"], "OB")
        self.assertEqual(p["lifecycle_normalized"], "FRESH")
        self.assertEqual(p["execution_readiness"], "READY")
        self.assertEqual(p["mitigation_pct"], 12.0)
        self.assertEqual(p["session_created"], "NY_OPEN")
        self.assertTrue(p["aligned_with_context"])
        self.assertEqual(p["poi_quality_score"], 72)
        self.assertEqual(p["status"], "OK")
        self.assertEqual(len(p["poi_candidates"]), 1)

    def test_agent2_observation_marks_invalid_when_bounds_missing(self):
        """Cas 2: POI sans price_bounds → INVALID readiness."""
        result = AgentResult(
            agent_id="agent_2",
            score=55,
            hard_filter_pass=True,
            direction="LONG",
            reason="PARTIAL_OB",
            payload={
                "p2a_poi_connectivity": {
                    "schema_version": "p2a.poi_connectivity.v1",
                    "poi_candidates": [],
                    "selected_poi": {
                        "poi_type_normalized": "OB",
                        "lifecycle_normalized": "WICK_TAGGED",
                        "price_bounds": None,
                        "score": 55,
                        "mitigation_pct": None,
                        "session_created": "UNKNOWN",
                        "aligned_with_context": True,
                        "execution_readiness": "INVALID",
                        "missing_evidence": ["INVALID_OR_MISSING_PRICE_BOUNDS"],
                    },
                    "audit": {
                        "agent2_has_any_zone": True,
                        "agent2_has_selected_ob": True,
                        "selected_poi_present": True,
                        "poi_bounds_present": False,
                    },
                }
            },
        )
        obs = build_agent_2_observation(result)
        self.assertIn("INVALID_OR_MISSING_PRICE_BOUNDS", obs.missing_evidence)
        self.assertEqual(obs.payload["execution_readiness"], "INVALID")
        self.assertIsNone(obs.payload["price_bounds"])

    def test_agent2_observation_marks_unavailable_when_no_poi(self):
        """Cas 3: pas de POI → UNAVAILABLE readiness, POI_UNAVAILABLE missing."""
        result = AgentResult(
            agent_id="agent_2",
            score=0,
            hard_filter_pass=False,
            direction=None,
            reason="NO_VALID_OB_SCORE_GE_60",
            payload={
                "p2a_poi_connectivity": {
                    "schema_version": "p2a.poi_connectivity.v1",
                    "poi_candidates": [],
                    "selected_poi": None,
                    "audit": {
                        "agent2_has_any_zone": False,
                        "agent2_has_selected_ob": False,
                        "selected_poi_present": False,
                        "poi_bounds_present": False,
                    },
                }
            },
        )
        obs = build_agent_2_observation(result)
        self.assertIn("POI_UNAVAILABLE", obs.missing_evidence)
        self.assertFalse(obs.payload["poi_available"])
        self.assertFalse(obs.payload["selected_poi_present"])
        self.assertIsNone(obs.payload["selected_poi"])
        self.assertEqual(obs.payload["execution_readiness"], "UNAVAILABLE")

    def test_agent2_observation_none_result(self):
        """Cas 4: result=None → UNAVAILABLE, AGENT_2_RESULT_MISSING."""
        obs = build_agent_2_observation(None)
        self.assertIsInstance(obs, AgentObservation)
        self.assertEqual(obs.payload["status"], "UNKNOWN")
        self.assertIn("AGENT_2_RESULT_MISSING", obs.missing_evidence)
        self.assertIsNone(obs.payload["selected_poi"])

    def test_agent2_observation_no_forbidden_keys(self):
        """Aucune clé interdite (entry, sl, tp, lot, signal, broker) dans le payload."""
        result = AgentResult(
            agent_id="agent_2",
            score=65,
            hard_filter_pass=True,
            direction="SHORT",
            reason="FVG_OK",
            payload={
                "p2a_poi_connectivity": {
                    "schema_version": "p2a.poi_connectivity.v1",
                    "poi_candidates": [{
                        "poi_type_normalized": "FVG",
                        "lifecycle_normalized": "FRESH",
                        "price_bounds": {"low": 2390.0, "high": 2395.0},
                        "score": 65,
                        "execution_readiness": "READY",
                    }],
                    "selected_poi": {
                        "poi_type_normalized": "FVG",
                        "lifecycle_normalized": "FRESH",
                        "price_bounds": {"low": 2390.0, "high": 2395.0},
                        "score": 65,
                        "execution_readiness": "READY",
                    },
                    "audit": {},
                }
            },
        )
        obs = build_agent_2_observation(result)
        forbidden = {"decision", "action", "order", "entry", "sl", "tp", "lot", "lots",
                     "signal", "trade_signal", "broker", "broker_gateway", "execute"}
        for key in self._walk_keys(obs.payload):
            self.assertNotIn(key, forbidden, f"Forbidden key '{key}' found in payload")

    @staticmethod
    def _walk_keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield str(k).lower()
                yield from TestP2aPoiConnectivity._walk_keys(v)
        elif isinstance(value, list):
            for item in value:
                yield from TestP2aPoiConnectivity._walk_keys(item)


if __name__ == "__main__":
    unittest.main()

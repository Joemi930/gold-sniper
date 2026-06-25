"""P2-A Evidence Builder POI tests — verify bundle.poi transports rich POI fields."""

from __future__ import annotations

import unittest

from agents.base_agent import AgentResult
from agents.agent_1_meteo import build_agent_1_observation
from agents.agent_2_cartographe import build_agent_2_observation
from agents.agent_3_liquidite import build_agent_3_observation
from agents.agent_4_fibonacci import build_agent_4_observation
from agents.agent_5_microscope import build_agent_5_observation
from agents.agent_6_sentinelle import build_agent_6_observation
from agents.agent_7_chronos import build_agent_7_observation
from gold_sniper.replay.evidence_builder import (
    _poi_from_agent_2,
    build_evidence_bundle,
    validate_evidence_bundle,
)
from gold_sniper.strategy.contracts import EvidenceBundle


class TestP2aEvidenceBuilderPoi(unittest.TestCase):
    def _agent2_rich_result(self):
        """Helper: returns an AgentResult with full P2-A POI connectivity."""
        return AgentResult(
            agent_id="agent_2",
            score=78,
            hard_filter_pass=True,
            direction="LONG",
            reason="OB_CONTINUATION_AVAILABLE",
            payload={
                "p2a_poi_connectivity": {
                    "schema_version": "p2a.poi_connectivity.v1",
                    "poi_candidates": [
                        {
                            "poi_type_normalized": "OB",
                            "lifecycle_normalized": "FRESH",
                            "price_bounds": {"low": 2400.0, "high": 2405.0},
                            "score": 78,
                            "mitigation_pct": 5.0,
                            "session_created": "NY_OPEN",
                            "aligned_with_context": True,
                            "execution_readiness": "READY",
                            "missing_evidence": [],
                            "warnings": [],
                        },
                        {
                            "poi_type_normalized": "FVG",
                            "lifecycle_normalized": "PARTIAL",
                            "price_bounds": {"low": 2398.0, "high": 2402.0},
                            "score": 65,
                            "mitigation_pct": 30.0,
                            "session_created": "LONDON",
                            "aligned_with_context": True,
                            "execution_readiness": "READY",
                            "missing_evidence": [],
                            "warnings": [],
                        },
                    ],
                    "selected_poi": {
                        "poi_type_normalized": "OB",
                        "lifecycle_normalized": "FRESH",
                        "price_bounds": {"low": 2400.0, "high": 2405.0},
                        "score": 78,
                        "mitigation_pct": 5.0,
                        "session_created": "NY_OPEN",
                        "aligned_with_context": True,
                        "execution_readiness": "READY",
                        "missing_evidence": [],
                        "warnings": [],
                    },
                    "audit": {
                        "agent2_has_any_zone": True,
                        "agent2_has_selected_ob": True,
                        "agent2_has_selected_fvg": False,
                        "candidate_count": 2,
                        "selected_poi_present": True,
                        "poi_bounds_present": True,
                        "best_shadow_poi_type": "OB_CONTINUATION_FRESH",
                        "best_shadow_poi_reason": "OB_CONTINUATION_AVAILABLE",
                    },
                }
            },
        )

    def _build_full_bundle(self, agent2_result=None):
        """Helper: build EvidenceBundle with all agents having valid observations."""
        obs = build_agent_2_observation(agent2_result or self._agent2_rich_result())
        bundle = EvidenceBundle(
            symbol="XAUUSD",
            setup_type="UNKNOWN",
            side="NONE",
            observations={"agent_2": obs},
            poi=_poi_from_agent_2(obs),
        )
        return bundle

    def test_bundle_poi_has_selected_poi_dict(self):
        bundle = self._build_full_bundle()
        self.assertTrue(bundle.poi["poi_available"])
        self.assertTrue(bundle.poi["selected_poi_present"])
        self.assertIsInstance(bundle.poi["selected_poi"], dict)
        self.assertEqual(bundle.poi["selected_poi"]["poi_type_normalized"], "OB")
        self.assertEqual(bundle.poi["selected_poi"]["lifecycle_normalized"], "FRESH")

    def test_bundle_poi_has_poi_candidates(self):
        bundle = self._build_full_bundle()
        self.assertIsInstance(bundle.poi["poi_candidates"], list)
        self.assertEqual(len(bundle.poi["poi_candidates"]), 2)
        self.assertEqual(bundle.poi["poi_candidates"][0]["poi_type_normalized"], "OB")
        self.assertEqual(bundle.poi["poi_candidates"][1]["poi_type_normalized"], "FVG")

    def test_bundle_poi_has_price_bounds(self):
        bundle = self._build_full_bundle()
        self.assertIsInstance(bundle.poi["price_bounds"], dict)
        self.assertEqual(bundle.poi["price_bounds"]["low"], 2400.0)
        self.assertEqual(bundle.poi["price_bounds"]["high"], 2405.0)
        self.assertTrue(bundle.poi["has_price_bounds"])

    def test_bundle_poi_has_normalized_types(self):
        bundle = self._build_full_bundle()
        self.assertEqual(bundle.poi["poi_type_normalized"], "OB")
        self.assertEqual(bundle.poi["lifecycle_normalized"], "FRESH")
        self.assertEqual(bundle.poi["execution_readiness"], "READY")

    def test_bundle_poi_has_connectivity_audit(self):
        bundle = self._build_full_bundle()
        audit = bundle.poi["connectivity_audit"]
        self.assertTrue(audit["agent2_has_any_zone"])
        self.assertTrue(audit["agent2_has_selected_ob"])
        self.assertTrue(audit["poi_bounds_present"])
        self.assertTrue(audit["selected_poi_present"])

    def test_bundle_poi_has_semantic_fields(self):
        bundle = self._build_full_bundle()
        self.assertTrue(bundle.poi["poi_semantic_available"])
        self.assertTrue(bundle.poi["poi_semantic_selected"])
        self.assertTrue(bundle.poi["poi_semantic_bounds"])
        self.assertEqual(bundle.poi["poi_semantic_status"], "POI_PRESENT_EXECUTABLE")
        self.assertEqual(bundle.poi["poi_failure_class"], "POI_SELECTED_READY")

    def test_bundle_poi_has_mitigation_pct(self):
        bundle = self._build_full_bundle()
        self.assertEqual(bundle.poi["mitigation_pct"], 5.0)

    def test_bundle_poi_has_session_created(self):
        bundle = self._build_full_bundle()
        self.assertEqual(bundle.poi["session_created"], "NY_OPEN")

    def test_bundle_poi_missing_evidence_empty_for_good_poi(self):
        bundle = self._build_full_bundle()
        self.assertEqual(bundle.poi["missing_evidence"], [])

    def test_bundle_poi_no_poi_shows_missing(self):
        result = AgentResult(
            agent_id="agent_2",
            score=0,
            hard_filter_pass=False,
            direction=None,
            reason="NO_VALID_OB",
            payload={},
        )
        obs = build_agent_2_observation(result)
        poi = _poi_from_agent_2(obs)
        self.assertFalse(poi["poi_available"])
        self.assertFalse(poi["selected_poi_present"])
        self.assertIn("POI_UNAVAILABLE", poi["missing_evidence"])
        self.assertEqual(poi["execution_readiness"], "UNAVAILABLE")

    def test_evidence_bundle_validation_passes_rich_poi(self):
        """Validation: missing agents are expected in partial bundle, but no POI errors."""
        bundle = self._build_full_bundle()
        errors = validate_evidence_bundle(bundle)
        # Missing agent observations are expected in partial bundle; POI errors are not.
        poi_errors = [e for e in errors if "POI" in e.upper() or "poi" in e.lower()]
        self.assertEqual(len(poi_errors), 0,
                         f"Unexpected POI validation errors: {poi_errors}")

    def test_bundle_poi_no_forbidden_keys(self):
        bundle = self._build_full_bundle()
        forbidden = {"entry", "sl", "tp", "lot", "signal", "trade_signal",
                     "broker", "order_send", "execute"}
        for key in self._walk_keys(bundle.poi):
            self.assertNotIn(key, forbidden, f"Forbidden key '{key}' in bundle.poi")

    @staticmethod
    def _walk_keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield str(k).lower()
                yield from TestP2aEvidenceBuilderPoi._walk_keys(v)
        elif isinstance(value, list):
            for item in value:
                yield from TestP2aEvidenceBuilderPoi._walk_keys(item)


if __name__ == "__main__":
    unittest.main()

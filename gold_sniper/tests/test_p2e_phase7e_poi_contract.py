"""P2-E Phase 7E — POI contract unit tests.

Tests the centralized P2-A selected_poi extraction contract.
"""

import sys
import os
import unittest
from unittest.mock import MagicMock

# Pre-existing project layout: agents import from 'agents.*' / 'core.*' / 'utils.*'
# which requires gold_sniper/ on sys.path.
_HERE = os.path.dirname(os.path.abspath(__file__))
_GOLD_SNIPER = os.path.join(_HERE, "..")
if _GOLD_SNIPER not in sys.path:
    sys.path.insert(0, _GOLD_SNIPER)

from gold_sniper.agents.poi_contract import (
    bounds_from_selected_poi,
    consumed_poi_snapshot,
    extract_p2a_selected_poi,
    safe_dict,
    safe_list,
)


class TestBoundsFromSelectedPoi(unittest.TestCase):
    """Contract: bounds_from_selected_poi extracts valid price bounds."""

    def test_price_bounds_dict(self):
        poi = {"price_bounds": {"low": 2400, "high": 2405}}
        bottom, top = bounds_from_selected_poi(poi)
        self.assertEqual(bottom, 2400.0)
        self.assertEqual(top, 2405.0)

    def test_price_bounds_dict_swapped(self):
        poi = {"price_bounds": {"low": 2405, "high": 2400}}
        bottom, top = bounds_from_selected_poi(poi)
        self.assertEqual(bottom, 2400.0)
        self.assertEqual(top, 2405.0)

    def test_flat_keys(self):
        poi = {"bottom": 2400, "top": 2405}
        bottom, top = bounds_from_selected_poi(poi)
        self.assertEqual(bottom, 2400.0)
        self.assertEqual(top, 2405.0)

    def test_entry_zone_keys(self):
        poi = {"entry_zone_bottom": 2395, "entry_zone_top": 2400}
        bottom, top = bounds_from_selected_poi(poi)
        self.assertEqual(bottom, 2395.0)
        self.assertEqual(top, 2400.0)

    def test_none_poi(self):
        bottom, top = bounds_from_selected_poi(None)
        self.assertIsNone(bottom)
        self.assertIsNone(top)

    def test_empty_poi(self):
        bottom, top = bounds_from_selected_poi({})
        self.assertIsNone(bottom)
        self.assertIsNone(top)

    def test_mixed_keys_bounds_present(self):
        poi = {"price_bounds": {"bottom": 2400, "top": 2405}}
        bottom, top = bounds_from_selected_poi(poi)
        self.assertEqual(bottom, 2400.0)
        self.assertEqual(top, 2405.0)


class TestConsumedPoiSnapshot(unittest.TestCase):
    """Contract: consumed_poi_snapshot produces correct shape."""

    def test_with_anchor(self):
        anchor = {
            "bottom": 2400, "top": 2405,
            "type": "BULLISH_OB", "poi_type": "BULLISH_OB",
            "execution_readiness": "READY", "source": "P2A_SELECTED_POI",
        }
        snap = consumed_poi_snapshot(anchor)
        self.assertTrue(snap["present"])
        self.assertEqual(snap["bottom"], 2400)
        self.assertEqual(snap["top"], 2405)
        self.assertEqual(snap["type"], "BULLISH_OB")
        self.assertEqual(snap["poi_type"], "BULLISH_OB")
        self.assertEqual(snap["execution_readiness"], "READY")
        self.assertEqual(snap["source"], "P2A_SELECTED_POI")

    def test_without_anchor(self):
        snap = consumed_poi_snapshot(None)
        self.assertFalse(snap["present"])
        self.assertIsNone(snap["bottom"])
        self.assertIsNone(snap["top"])


class TestExtractP2aSelectedPoi(unittest.TestCase):
    """Contract: extract_p2a_selected_poi follows priority order."""

    def _mock_blackboard(self, agent2_payload=None, agent2_state=None):
        bb = MagicMock()
        agent_result = MagicMock()
        agent_result.payload = agent2_payload or {}
        bb.read_sync.return_value = agent_result
        bb.get_agent.return_value = agent2_state or {}
        return bb

    def test_selected_poi_priority(self):
        """P2-A selected_poi is the priority source."""
        bb = self._mock_blackboard(
            agent2_payload={
                "p2a_poi_connectivity": {
                    "selected_poi": {
                        "price_bounds": {"low": 2400, "high": 2405},
                        "type": "BULLISH_OB",
                        "execution_readiness": "READY",
                    },
                    "poi_candidates": [],
                },
            },
        )
        anchor, diag = extract_p2a_selected_poi(bb)
        self.assertIsNotNone(anchor)
        self.assertEqual(diag["source"], "P2A_SELECTED_POI")
        self.assertEqual(anchor["bottom"], 2400.0)
        self.assertEqual(anchor["top"], 2405.0)

    def test_candidate_fallback_when_selected_absent(self):
        """Candidate fallback used only when selected_poi absent."""
        bb = self._mock_blackboard(
            agent2_payload={
                "p2a_poi_connectivity": {
                    "selected_poi": None,
                    "poi_candidates": [
                        {"price_bounds": {"low": 2395, "high": 2400}, "type": "BEARISH_OB"},
                    ],
                },
            },
        )
        anchor, diag = extract_p2a_selected_poi(bb)
        self.assertIsNotNone(anchor)
        self.assertEqual(diag["source"], "P2A_CANDIDATE_FALLBACK")

    def test_legacy_fallback_when_p2a_absent(self):
        """Legacy fallback used only when P2-A is entirely absent."""
        bb = self._mock_blackboard(
            agent2_payload={},
            agent2_state={"poi_zone": {"bottom": 2400, "top": 2405}},
        )
        anchor, diag = extract_p2a_selected_poi(bb)
        self.assertIsNotNone(anchor)
        self.assertEqual(diag["source"], "LEGACY_AGENT2_FALLBACK")
        self.assertTrue(diag["legacy_fallback_used"])

    def test_no_bounds_returns_none(self):
        """No bounds → anchor None + failure_reason."""
        bb = self._mock_blackboard(
            agent2_payload={
                "p2a_poi_connectivity": {
                    "selected_poi": {"type": "BULLISH_OB"},
                    "poi_candidates": [],
                },
            },
        )
        anchor, diag = extract_p2a_selected_poi(bb)
        self.assertIsNone(anchor)
        self.assertEqual(diag["failure_reason"], "NO_P2A_POI_OR_BOUNDS")

    def test_selected_poi_prevents_legacy(self):
        """Agent must not prefer legacy if selected_poi P2-A exists."""
        bb = self._mock_blackboard(
            agent2_payload={
                "p2a_poi_connectivity": {
                    "selected_poi": {
                        "price_bounds": {"low": 2400, "high": 2405},
                        "type": "BULLISH_OB",
                    },
                    "poi_candidates": [],
                },
            },
            agent2_state={"poi_zone": {"bottom": 2300, "top": 2310}},
        )
        anchor, diag = extract_p2a_selected_poi(bb)
        self.assertIsNotNone(anchor)
        self.assertEqual(diag["source"], "P2A_SELECTED_POI")
        # Should use bounds from selected_poi, NOT legacy
        self.assertEqual(anchor["bottom"], 2400.0)

    def test_empty_blackboard_returns_none(self):
        """Empty blackboard → anchor None."""
        bb = MagicMock()
        bb.read_sync.return_value = None
        bb.get_agent.return_value = {}
        anchor, diag = extract_p2a_selected_poi(bb)
        self.assertIsNone(anchor)
        self.assertEqual(diag["failure_reason"], "NO_P2A_POI_OR_BOUNDS")


class TestSafeHelpers(unittest.TestCase):
    """Contract: safe_dict and safe_list never return None."""

    def test_safe_dict_valid(self):
        self.assertEqual(safe_dict({"a": 1}), {"a": 1})

    def test_safe_dict_none(self):
        self.assertEqual(safe_dict(None), {})

    def test_safe_dict_int(self):
        self.assertEqual(safe_dict(42), {})

    def test_safe_list_valid(self):
        self.assertEqual(safe_list([1, 2]), [1, 2])

    def test_safe_list_none(self):
        self.assertEqual(safe_list(None), [])


if __name__ == "__main__":
    unittest.main()

"""P4.2 — POI contract normalization tests (D8 fix).

POI cannot be READY/EXECUTABLE and REJECTED simultaneously.
The contract must resolve to a single terminal state.
"""
from __future__ import annotations

import unittest

from gold_sniper.strategy.poi_readiness_contract import (
    POIContractStatus,
    evaluate_poi_contract,
)


class TestPOIContractNormalization(unittest.TestCase):

    def test_poi_single_terminal_state_executable_rejected(self):
        """D8: POI with EXECUTABLE semantic + REJECTED failure_class → single REJECTED state."""
        poi = {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_failure_class": "LEGACY_REJECTED",
            "execution_readiness": "READY",
            "final_poi_quality_score": 65.0,
            "score_is_computed": True,
            "selected_poi_present": True,
            "has_price_bounds": True,
        }
        result = evaluate_poi_contract(poi)
        # Must NOT be both executable and rejected
        self.assertFalse(
            result.is_executable and "REJECTED" in str(result.failure_class or ""),
            "POI must not be both EXECUTABLE and REJECTED",
        )
        # REJECTED must win
        self.assertIn(
            result.status,
            {
                POIContractStatus.RECOVERABLE_REJECTED,
                POIContractStatus.TOO_WEAK,
                POIContractStatus.INVALID,
            },
            f"Expected REJECTED-like status, got {result.status}",
        )
        self.assertFalse(result.is_ready)
        self.assertFalse(result.is_ready_for_trigger)

    def test_poi_single_terminal_state_ready_rejected(self):
        """D8: POI with READY execution + REJECTED failure_class → single REJECTED state."""
        poi = {
            "poi_semantic_status": "POI_PRESENT_READY",
            "poi_failure_class": "REJECTED",
            "execution_readiness": "READY",
            "final_poi_quality_score": 75.0,
            "score_is_computed": True,
            "selected_poi_present": True,
            "has_price_bounds": True,
        }
        result = evaluate_poi_contract(poi)
        self.assertFalse(
            result.is_ready and "REJECTED" in str(result.failure_class or ""),
            "POI must not be both READY and REJECTED",
        )
        self.assertFalse(result.is_ready)

    def test_clean_executable_poi_works(self):
        """A truly executable POI (no rejection) should still be executable."""
        poi = {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_failure_class": None,
            "execution_readiness": "WATCH_ONLY",
            "final_poi_quality_score": 65.0,
            "score_is_computed": True,
            "selected_poi_present": True,
            "has_price_bounds": True,
        }
        result = evaluate_poi_contract(poi)
        # Should be executable or similar positive state
        self.assertFalse(result.is_invalid)
        self.assertNotEqual(result.status, POIContractStatus.INVALID)

    def test_contradiction_detected(self):
        """D8: The contradiction MUST be detected and recorded."""
        poi = {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_failure_class": "LEGACY_REJECTED",
            "execution_readiness": "READY",
            "has_price_bounds": True,
            "selected_poi_present": True,
        }
        result = evaluate_poi_contract(poi)
        self.assertIn(
            "EXECUTABLE_WITH_REJECTED_FAILURE_CLASS",
            result.contradictions,
        )

    def test_rejected_poi_not_executable(self):
        """A fully rejected POI must not be marked as executable or ready."""
        poi = {
            "poi_semantic_status": "POI_REJECTED",
            "poi_failure_class": "POI_TOO_FAR",
            "execution_readiness": "INVALID",
            "has_price_bounds": False,
            "selected_poi_present": False,
        }
        result = evaluate_poi_contract(poi)
        self.assertFalse(result.is_executable)
        self.assertFalse(result.is_ready)
        self.assertFalse(result.is_ready_for_trigger)


class TestPOIStatusResolution(unittest.TestCase):

    def test_consumed_wins_over_ready(self):
        """Consumed POI must not be ready, even if score is high."""
        poi = {
            "poi_semantic_status": "POI_PRESENT_READY",
            "poi_failure_class": "CONSUMED",
            "execution_readiness": "READY",
            "final_poi_quality_score": 80.0,
            "score_is_computed": True,
            "has_price_bounds": True,
            "selected_poi_present": True,
            "lifecycle_state": "CONSUMED",
        }
        result = evaluate_poi_contract(poi)
        self.assertEqual(result.status, POIContractStatus.CONSUMED)
        self.assertFalse(result.is_ready)

    def test_invalidated_wins_over_executable(self):
        """Invalidated POI must not be executable."""
        poi = {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_failure_class": "INVALIDATED",
            "execution_readiness": "WATCH_ONLY",
            "final_poi_quality_score": 60.0,
            "score_is_computed": True,
            "has_price_bounds": True,
            "selected_poi_present": True,
            "lifecycle_state": "INVALIDATED",
        }
        result = evaluate_poi_contract(poi)
        self.assertEqual(result.status, POIContractStatus.INVALID)
        self.assertFalse(result.is_executable)


if __name__ == "__main__":
    unittest.main()

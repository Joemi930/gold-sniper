import unittest

from gold_sniper.strategy.poi_readiness_contract import (
    POIContractStatus,
    evaluate_poi_contract,
)


class TestPhase10POIReadinessContract(unittest.TestCase):
    def test_ready_poi_with_bounds_score_and_ready_state(self):
        result = evaluate_poi_contract({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "execution_readiness": "READY",
        })
        self.assertEqual(result.status, POIContractStatus.READY)
        self.assertEqual(result.readiness_state, "READY")

    def test_ready_for_trigger_with_waiting_trigger(self):
        result = evaluate_poi_contract({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "execution_readiness": "WAITING_TRIGGER",
        })
        self.assertEqual(result.status, POIContractStatus.READY_FOR_TRIGGER)
        self.assertEqual(result.readiness_state, "WAITING_TRIGGER")

    def test_executable_when_bounds_exist_but_score_missing(self):
        result = evaluate_poi_contract({
            "selected_poi": {"low": 2400.0, "high": 2405.0},
        })
        self.assertEqual(result.status, POIContractStatus.EXECUTABLE)

    def test_too_weak_when_computed_score_zero(self):
        result = evaluate_poi_contract({
            "selected_poi": {"low": 2400.0, "high": 2405.0},
            "poi_quality_score": 0.0,
        })
        self.assertEqual(result.status, POIContractStatus.TOO_WEAK)

    def test_executable_with_zero_quality_contradiction(self):
        result = evaluate_poi_contract({
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"low": 2400.0, "high": 2405.0},
            "poi_quality_score": 0.0,
        })
        self.assertIn("EXECUTABLE_WITH_ZERO_QUALITY", result.contradictions)

    def test_executable_with_legacy_rejected_contradiction(self):
        result = evaluate_poi_contract({
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_failure_class": "POI_PRESENT_LEGACY_REJECTED",
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "execution_readiness": "READY",
        })
        self.assertIn("EXECUTABLE_WITH_REJECTED_FAILURE_CLASS", result.contradictions)
        self.assertNotEqual(result.status, POIContractStatus.READY)

    def test_consumed_poi(self):
        result = evaluate_poi_contract({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "lifecycle_normalized": "CONSUMED",
        })
        self.assertEqual(result.status, POIContractStatus.CONSUMED)

    def test_invalidated_poi(self):
        result = evaluate_poi_contract({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "lifecycle_normalized": "INVALIDATED",
        })
        self.assertEqual(result.status, POIContractStatus.INVALID)

    def test_selected_without_bounds_is_observable(self):
        result = evaluate_poi_contract({"selected_poi": {"id": "zone-1"}})
        self.assertEqual(result.status, POIContractStatus.OBSERVABLE)

    def test_missing_anchor_and_bounds_is_invalid(self):
        result = evaluate_poi_contract({})
        self.assertEqual(result.status, POIContractStatus.INVALID)


if __name__ == "__main__":
    unittest.main()

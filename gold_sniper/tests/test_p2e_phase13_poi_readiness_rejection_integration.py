import unittest

from gold_sniper.strategy.poi_readiness_contract import (
    POIContractStatus,
    evaluate_poi_contract,
    poi_status_to_readiness,
)


class TestP2EPhase13POIReadinessRejectionIntegration(unittest.TestCase):
    def test_fatal_rejection_maps_invalid(self):
        result = evaluate_poi_contract({
            "poi_failure_class": "POI_DIRECTION_MISMATCH",
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "direction_mismatch": True,
        })
        self.assertEqual(result.status, POIContractStatus.INVALID)
        self.assertEqual(result.audit["rejection"]["code"], "POI_DIRECTION_MISMATCH")

    def test_recoverable_rejection_maps_recoverable_status(self):
        result = evaluate_poi_contract({
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_failure_class": "POI_PRESENT_LEGACY_REJECTED",
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "execution_readiness": "READY",
        })
        self.assertEqual(result.status, POIContractStatus.RECOVERABLE_REJECTED)
        self.assertTrue(result.audit["rejection"]["recoverable"])

    def test_recoverable_rejected_maps_watch_only(self):
        self.assertEqual(
            poi_status_to_readiness(POIContractStatus.RECOVERABLE_REJECTED),
            "WATCH_ONLY",
        )

    def test_non_rejected_ready_poi_keeps_phase10_behavior(self):
        result = evaluate_poi_contract({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "execution_readiness": "READY",
        })
        self.assertEqual(result.status, POIContractStatus.READY)
        self.assertEqual(result.audit["rejection"]["code"], "POI_REJECTION_NONE")

    def test_computed_quality_zero_still_too_weak(self):
        result = evaluate_poi_contract({
            "selected_poi": {"low": 2400.0, "high": 2405.0},
            "poi_quality_score": 0.0,
        })
        self.assertEqual(result.status, POIContractStatus.TOO_WEAK)


if __name__ == "__main__":
    unittest.main()

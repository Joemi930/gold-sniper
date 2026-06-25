import unittest

from gold_sniper.strategy.readiness import _poi_state


class TestPhase10ReadinessPOIContract(unittest.TestCase):
    def test_poi_state_uses_ready_contract(self):
        state = _poi_state({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 70.0},
            "execution_readiness": "READY",
        })
        self.assertEqual(state, "READY")

    def test_ready_for_trigger_maps_to_waiting_trigger(self):
        state = _poi_state({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 70.0},
            "execution_readiness": "WAITING_TRIGGER",
        })
        self.assertEqual(state, "WAITING_TRIGGER")

    def test_executable_maps_to_watch_only(self):
        state = _poi_state({"selected_poi": {"low": 2400.0, "high": 2405.0}})
        self.assertEqual(state, "WATCH_ONLY")

    def test_too_weak_maps_to_watch_only(self):
        state = _poi_state({
            "selected_poi": {"low": 2400.0, "high": 2405.0},
            "poi_quality_score": 0.0,
        })
        self.assertEqual(state, "WATCH_ONLY")

    def test_invalid_maps_to_invalid(self):
        state = _poi_state({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 70.0},
            "lifecycle_normalized": "INVALIDATED",
        })
        self.assertEqual(state, "INVALID")

    def test_legacy_rejected_contradiction_never_ready(self):
        state = _poi_state({
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_failure_class": "POI_PRESENT_LEGACY_REJECTED",
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 70.0},
            "execution_readiness": "READY",
        })
        self.assertNotEqual(state, "READY")
        self.assertEqual(state, "WATCH_ONLY")


if __name__ == "__main__":
    unittest.main()

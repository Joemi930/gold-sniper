import unittest

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.strategy.setup_signal_inventory import extract_setup_signal_inventory


def _bundle(poi):
    return EvidenceBundle.from_dict({
        "side": "BUY",
        "context": {"direction": "BUY"},
        "poi": poi,
        "liquidity": {},
        "micro": {},
        "news": {"news_clear": True},
        "session": {"trading_allowed": True},
        "risk": {"passed": True},
        "raw": {"timing": {}},
    })


class TestPhase10SetupSignalInventoryPOIContract(unittest.TestCase):
    def test_exposes_poi_contract_status(self):
        signals = extract_setup_signal_inventory(_bundle({
            "selected_poi": {"low": 2400.0, "high": 2405.0},
        }))
        self.assertEqual(signals.poi_contract_status, "POI_EXECUTABLE")

    def test_exposes_poi_ready_for_trigger(self):
        signals = extract_setup_signal_inventory(_bundle({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "execution_readiness": "WAITING_TRIGGER",
        }))
        self.assertTrue(signals.poi_ready_for_trigger)
        self.assertIn("POI_READY_FOR_TRIGGER", signals.present_signals)

    def test_exposes_poi_ready(self):
        signals = extract_setup_signal_inventory(_bundle({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "execution_readiness": "READY",
        }))
        self.assertTrue(signals.poi_ready)
        self.assertIn("POI_READY", signals.present_signals)

    def test_exposes_poi_too_weak(self):
        signals = extract_setup_signal_inventory(_bundle({
            "selected_poi": {"low": 2400.0, "high": 2405.0},
            "poi_quality_score": 0.0,
        }))
        self.assertTrue(signals.poi_too_weak)
        self.assertIn("POI_TOO_WEAK", signals.present_signals)

    def test_exposes_poi_contract_contradictions(self):
        signals = extract_setup_signal_inventory(_bundle({
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"low": 2400.0, "high": 2405.0},
            "poi_quality_score": 0.0,
        }))
        self.assertIn("EXECUTABLE_WITH_ZERO_QUALITY", signals.poi_contract_contradictions)

    def test_ready_for_trigger_counts_as_present_and_executable(self):
        signals = extract_setup_signal_inventory(_bundle({
            "selected_poi": {"low": 2400.0, "high": 2405.0, "score": 65.0},
            "execution_readiness": "WAITING_TRIGGER",
        }))
        self.assertTrue(signals.poi_present)
        self.assertTrue(signals.poi_executable)

    def test_too_weak_does_not_count_as_ready(self):
        signals = extract_setup_signal_inventory(_bundle({
            "selected_poi": {"low": 2400.0, "high": 2405.0},
            "poi_quality_score": 0.0,
        }))
        self.assertFalse(signals.poi_ready)
        self.assertFalse(signals.poi_ready_for_trigger)


if __name__ == "__main__":
    unittest.main()

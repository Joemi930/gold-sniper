import unittest

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.strategy.setup_signal_inventory import extract_setup_signal_inventory


def _bundle(**overrides):
    data = {
        "setup_type": "UNKNOWN",
        "side": "BUY",
        "context": {"direction": "BUY", "in_ote": False},
        "poi": {},
        "liquidity": {},
        "micro": {},
        "news": {"news_clear": True},
        "session": {"trading_allowed": True, "session": "LONDON", "session_grade": "HIGH"},
        "risk": {"passed": True},
        "raw": {"timing": {}},
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


class TestSetupSignalInventory(unittest.TestCase):
    def test_extracts_poi_present(self):
        signals = extract_setup_signal_inventory(_bundle(poi={
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
            "price_bounds": {"low": 2400, "high": 2405},
        }))
        self.assertTrue(signals.poi_present)
        self.assertIn("POI_PRESENT", signals.present_signals)

    def test_extracts_micro_ready(self):
        signals = extract_setup_signal_inventory(_bundle(micro={"readiness_state": "READY"}))
        self.assertTrue(signals.micro_ready)
        self.assertIn("MICRO_READY", signals.present_signals)

    def test_extracts_micro_waiting(self):
        signals = extract_setup_signal_inventory(_bundle(micro={"readiness_state": "WAITING_TRIGGER"}))
        self.assertTrue(signals.micro_waiting)
        self.assertIn("MICRO_WAITING", signals.present_signals)

    def test_extracts_micro_partial(self):
        signals = extract_setup_signal_inventory(_bundle(micro={"reclaim_confirmed": True}))
        self.assertTrue(signals.micro_partial)
        self.assertIn("MICRO_PARTIAL", signals.present_signals)

    def test_extracts_liquidity_ready(self):
        signals = extract_setup_signal_inventory(_bundle(liquidity={"readiness_state": "READY"}))
        self.assertTrue(signals.liquidity_ready)
        self.assertIn("LIQUIDITY_READY", signals.present_signals)

    def test_extracts_sweep_detected(self):
        signals = extract_setup_signal_inventory(_bundle(liquidity={"sweep_detected": True}))
        self.assertTrue(signals.sweep_detected)
        self.assertIn("SWEEP_DETECTED", signals.present_signals)

    def test_extracts_in_ote(self):
        signals = extract_setup_signal_inventory(_bundle(context={"direction": "BUY", "in_ote": True}))
        self.assertTrue(signals.in_ote)
        self.assertIn("IN_OTE", signals.present_signals)

    def test_extracts_trend_aligned_poi(self):
        signals = extract_setup_signal_inventory(_bundle(
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400, "high": 2405},
            },
        ))
        self.assertTrue(signals.trend_aligned_poi)

    def test_extracts_counter_trend_poi(self):
        signals = extract_setup_signal_inventory(_bundle(
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BEARISH_OB"},
                "price_bounds": {"low": 2400, "high": 2405},
            },
        ))
        self.assertTrue(signals.counter_trend_poi)

    def test_missing_core_when_direction_and_poi_absent(self):
        signals = extract_setup_signal_inventory(_bundle(context={}, poi={}, side="NONE"))
        self.assertIn("DIRECTION_MISSING", signals.missing_core)
        self.assertIn("POI_MISSING", signals.missing_core)


if __name__ == "__main__":
    unittest.main()

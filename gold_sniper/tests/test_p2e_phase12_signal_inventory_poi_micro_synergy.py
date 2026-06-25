import unittest

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.strategy.setup_signal_inventory import extract_setup_signal_inventory


def _bundle(*, synergy=True, outside=False):
    return EvidenceBundle(
        context={"direction": "BUY", "htf_aligned": True},
        poi={
            "selected_poi_present": True,
            "selected_poi": {"price_bounds": {"low": 100.0, "high": 110.0}, "score": 55.0},
            "price_bounds": {"low": 100.0, "high": 110.0},
            "poi_type": "DEMAND",
            "poi_quality_score": 55.0,
            "execution_readiness": "READY",
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_micro_synergy": {
                "synergy": synergy,
                "status": "SYNERGY_READY",
                "reason": "TEST" if synergy else "MICRO_OUTSIDE_POI",
                "micro_confirmed": True,
                "micro_inside_poi": not outside,
                "micro_outside_poi": outside,
                "upgraded_poi_status": "POI_READY" if synergy else None,
                "effective_poi_status": "POI_READY" if synergy else "POI_EXECUTABLE",
                "remaining_blockers": [],
            },
        },
        micro={
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": not outside,
            "price_in_agent2_poi": not outside,
            "trigger_outside_poi": outside,
            "candles_1m_count": 20,
        },
    )


class TestP2EPhase12SignalInventoryPOIMicroSynergy(unittest.TestCase):
    def test_extracts_poi_micro_synergy_true(self):
        signals = extract_setup_signal_inventory(_bundle())
        self.assertTrue(signals.poi_micro_synergy)

    def test_adds_poi_micro_synergy_present_signal(self):
        signals = extract_setup_signal_inventory(_bundle())
        self.assertIn("POI_MICRO_SYNERGY", signals.present_signals)

    def test_adds_micro_inside_poi(self):
        signals = extract_setup_signal_inventory(_bundle())
        self.assertTrue(signals.micro_inside_poi)
        self.assertIn("MICRO_INSIDE_POI", signals.present_signals)

    def test_adds_effective_poi_ready_when_upgraded(self):
        signals = extract_setup_signal_inventory(_bundle())
        self.assertEqual(signals.effective_poi_status, "POI_READY")
        self.assertIn("EFFECTIVE_POI_READY", signals.present_signals)

    def test_does_not_mark_synergy_if_micro_outside(self):
        signals = extract_setup_signal_inventory(_bundle(synergy=False, outside=True))
        self.assertFalse(signals.poi_micro_synergy)
        self.assertNotIn("POI_MICRO_SYNERGY", signals.present_signals)


if __name__ == "__main__":
    unittest.main()

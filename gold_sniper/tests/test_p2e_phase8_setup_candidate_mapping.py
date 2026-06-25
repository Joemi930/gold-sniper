import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, SetupType
from gold_sniper.strategy.setup_candidate_mapping import map_signals_to_setup_candidates
from gold_sniper.strategy.setup_signal_inventory import extract_setup_signal_inventory


def _signals(**overrides):
    data = {
        "side": "BUY",
        "context": {"direction": "BUY"},
        "poi": {},
        "liquidity": {},
        "micro": {},
        "raw": {"timing": {}},
    }
    data.update(overrides)
    bundle = EvidenceBundle.from_dict(data)
    return extract_setup_signal_inventory(bundle)


class TestSetupCandidateMapping(unittest.TestCase):
    def _candidate_types(self, signals):
        return [candidate.candidate_type for candidate in map_signals_to_setup_candidates(signals)]

    def test_sweep_poi_reclaim_maps_sweep_reversal(self):
        signals = _signals(
            poi={
                "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
                "selected_poi": {"poi_type_normalized": "BEARISH_OB"},
                "price_bounds": {"low": 2400, "high": 2405},
            },
            liquidity={"sweep_detected": True},
            micro={"reclaim_confirmed": True},
        )
        self.assertIn(SetupType.SWEEP_REVERSAL, self._candidate_types(signals))

    def test_trend_aligned_poi_micro_waiting_maps_continuation_light(self):
        signals = _signals(
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400, "high": 2405},
            },
            micro={"readiness_state": "WAITING_TRIGGER"},
        )
        self.assertIn(SetupType.CONTINUATION_LIGHT, self._candidate_types(signals))

    def test_trend_aligned_poi_in_ote_maps_ote_pullback(self):
        signals = _signals(
            context={"direction": "BUY", "in_ote": True},
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
                "price_bounds": {"low": 2400, "high": 2405},
            },
        )
        self.assertIn(SetupType.OTE_PULLBACK, self._candidate_types(signals))

    def test_counter_trend_poi_micro_partial_maps_reversal_light(self):
        signals = _signals(
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "selected_poi": {"poi_type_normalized": "BEARISH_OB"},
                "price_bounds": {"low": 2400, "high": 2405},
            },
            micro={"trigger_inside_poi": True},
        )
        self.assertIn(SetupType.REVERSAL_LIGHT, self._candidate_types(signals))

    def test_poi_only_maps_poi_reaction(self):
        signals = _signals(poi={
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"poi_type_normalized": "UNKNOWN"},
            "price_bounds": {"low": 2400, "high": 2405},
        })
        self.assertEqual(self._candidate_types(signals), [SetupType.POI_REACTION])

    def test_no_poi_no_direction_maps_no_candidate(self):
        signals = _signals(context={}, poi={}, side="NONE")
        self.assertEqual(map_signals_to_setup_candidates(signals), [])


if __name__ == "__main__":
    unittest.main()

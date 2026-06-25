import unittest

from gold_sniper.strategy.contracts import SetupType
from gold_sniper.strategy.setup_candidate_mapping import map_signals_to_setup_candidates
from gold_sniper.strategy.setup_signal_inventory import SetupSignalInventory


def _signals(**overrides):
    base = dict(
        direction="BUY",
        side="BUY",
        context_present=True,
        direction_known=True,
        htf_aligned=True,
        trend_aligned_poi=False,
        counter_trend_poi=False,
        poi_present=True,
        poi_executable=True,
        poi_contract_status="POI_EXECUTABLE",
        poi_contract_reason="TEST",
        poi_ready_for_trigger=False,
        poi_ready=False,
        poi_too_weak=False,
        poi_invalid=False,
        poi_contract_contradictions=[],
        poi_quality_breakdown={},
        poi_score_source="TEST",
        poi_score_is_computed=True,
        poi_type="DEMAND",
        price_bounds_present=True,
        poi_quality_score=55.0,
        liquidity_ready=False,
        liquidity_waiting=False,
        sweep_detected=False,
        sweep_rejected=False,
        liquidity_state="UNKNOWN",
        liquidity_quality_score=0.0,
        micro_ready=False,
        micro_waiting=False,
        micro_partial=False,
        micro_contract_status="MICRO_MISSING_DATA",
        micro_contract_reason="TEST",
        micro_contract_missing_fields=[],
        micro_contract_present_fields=[],
        micro_contract_contradictions=[],
        micro_confirmed=False,
        micro_missing_data=False,
        micro_invalid=False,
        micro_outside_poi=False,
        reclaim_confirmed=False,
        retest_confirmed=False,
        displacement_present=False,
        trigger_inside_poi=False,
        micro_state="UNAVAILABLE",
        timing_ready=False,
        timing_waiting=False,
        in_ote=False,
        premium_discount="UNKNOWN",
        timing_state="UNAVAILABLE",
        session_ready=True,
        session_label="LONDON",
        session_grade="MEDIUM",
        trading_allowed=True,
        risk_ready=True,
        news_safe=True,
        missing_core=[],
        present_signals=[],
        poi_micro_synergy=False,
        poi_micro_synergy_status="UNKNOWN",
        poi_micro_reason="UNKNOWN",
        micro_inside_poi=False,
        effective_poi_status="POI_EXECUTABLE",
    )
    base.update(overrides)
    if not base["present_signals"]:
        present = []
        if base["poi_present"]:
            present.append("POI_PRESENT")
        if base["poi_micro_synergy"]:
            present.append("POI_MICRO_SYNERGY")
        if base["micro_ready"]:
            present.append("MICRO_READY")
            present.append("MICRO_CONFIRMED")
        if base["micro_inside_poi"]:
            present.append("MICRO_INSIDE_POI")
        if base["sweep_detected"]:
            present.append("SWEEP_DETECTED")
        if base["liquidity_ready"]:
            present.append("LIQUIDITY_READY")
        if base["trend_aligned_poi"]:
            present.append("TREND_ALIGNED_POI")
        if base["timing_ready"]:
            present.append("TIMING_READY")
        if base["effective_poi_status"] in {"POI_READY", "POI_READY_FOR_TRIGGER"}:
            present.append("EFFECTIVE_POI_READY")
        base["present_signals"] = present
    return SetupSignalInventory(**base)


class TestP2EPhase12SetupCandidateMappingSynergy(unittest.TestCase):
    def _types(self, signals):
        return [candidate.candidate_type for candidate in map_signals_to_setup_candidates(signals)]

    def test_synergy_sweep_micro_ready_maps_sweep_reversal(self):
        signals = _signals(
            poi_micro_synergy=True,
            micro_ready=True,
            micro_confirmed=True,
            micro_inside_poi=True,
            sweep_detected=True,
            effective_poi_status="POI_READY",
        )
        self.assertIn(SetupType.SWEEP_REVERSAL, self._types(signals))

    def test_synergy_trend_timing_maps_continuation_strict_candidate(self):
        signals = _signals(
            poi_micro_synergy=True,
            micro_ready=True,
            micro_confirmed=True,
            micro_inside_poi=True,
            trend_aligned_poi=True,
            timing_ready=True,
            effective_poi_status="POI_READY",
        )
        self.assertIn(SetupType.CONTINUATION_STRICT, self._types(signals))

    def test_without_micro_confirmed_no_synergy_candidate(self):
        signals = _signals(poi_micro_synergy=True, sweep_detected=True, micro_ready=False)
        candidates = map_signals_to_setup_candidates(signals)
        self.assertFalse(any(c.reason == "POI_MICRO_SYNERGY_WITH_SWEEP_REVERSAL" for c in candidates))

    def test_poi_too_weak_no_synergy_candidate(self):
        signals = _signals(
            poi_micro_synergy=False,
            poi_too_weak=True,
            micro_ready=True,
            sweep_detected=True,
        )
        candidates = map_signals_to_setup_candidates(signals)
        self.assertFalse(any(c.reason == "POI_MICRO_SYNERGY_WITH_SWEEP_REVERSAL" for c in candidates))


if __name__ == "__main__":
    unittest.main()

import unittest

from gold_sniper.strategy.micro_readiness_contract import evaluate_micro_readiness
from gold_sniper.strategy.poi_micro_synergy_contract import (
    POIMicroSynergyStatus,
    evaluate_poi_micro_synergy,
)
from gold_sniper.strategy.poi_readiness_contract import (
    POIContractResult,
    POIContractStatus,
    POIQualityBreakdown,
)


def _poi_contract(
    *,
    status=POIContractStatus.EXECUTABLE,
    final_score=45.0,
    has_bounds=True,
    has_selected=True,
    failure_class=None,
) -> POIContractResult:
    return POIContractResult(
        status=status,
        readiness_state="WATCH_ONLY",
        reason=status.value,
        source="TEST",
        has_selected_poi=has_selected,
        has_price_bounds=has_bounds,
        is_observable=status not in {POIContractStatus.INVALID, POIContractStatus.CONSUMED},
        is_executable=status in {
            POIContractStatus.EXECUTABLE,
            POIContractStatus.READY_FOR_TRIGGER,
            POIContractStatus.READY,
        },
        is_ready_for_trigger=status == POIContractStatus.READY_FOR_TRIGGER,
        is_ready=status == POIContractStatus.READY,
        is_too_weak=status == POIContractStatus.TOO_WEAK,
        is_invalid=status == POIContractStatus.INVALID,
        is_consumed=status == POIContractStatus.CONSUMED,
        failure_class=failure_class,
        semantic_status_raw="POI_PRESENT_EXECUTABLE",
        execution_readiness_raw="READY",
        quality=POIQualityBreakdown(
            structure_score=None,
            freshness_score=None,
            mitigation_score=None,
            distance_to_price_score=None,
            bounds_quality_score=None,
            final_poi_quality_score=final_score,
            score_source="TEST",
            score_is_computed=final_score is not None,
        ),
        contradictions=[],
        audit={},
    )


def _micro(payload=None):
    base = {
        "sweep_1m_confirmed": True,
        "choch_detected": True,
        "trigger_inside_poi": True,
        "price_in_agent2_poi": True,
        "trigger_outside_poi": False,
        "candles_1m_count": 20,
    }
    base.update(payload or {})
    return evaluate_micro_readiness(base)


class TestP2EPhase12POIMicroSynergyContract(unittest.TestCase):
    def test_executable_confirmed_inside_score_45_ready_for_trigger(self):
        result = evaluate_poi_micro_synergy(_poi_contract(final_score=45.0), _micro())
        self.assertTrue(result.synergy)
        self.assertEqual(result.upgraded_poi_status, "POI_READY_FOR_TRIGGER")

    def test_executable_confirmed_inside_score_55_ready(self):
        result = evaluate_poi_micro_synergy(_poi_contract(final_score=55.0), _micro())
        self.assertTrue(result.synergy)
        self.assertEqual(result.upgraded_poi_status, "POI_READY")

    def test_too_weak_zero_score_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(status=POIContractStatus.TOO_WEAK, final_score=0.0),
            _micro(),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.POI_TOO_WEAK)

    def test_invalid_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(status=POIContractStatus.INVALID),
            _micro(),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.POI_INVALID)

    def test_consumed_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(status=POIContractStatus.CONSUMED),
            _micro(),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.POI_CONSUMED)

    def test_micro_outside_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(final_score=55.0),
            _micro({"trigger_inside_poi": False, "price_in_agent2_poi": False, "trigger_confirmed": True}),
            micro_inside_poi=False,
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.MICRO_OUTSIDE_POI)

    def test_waiting_trigger_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(final_score=55.0),
            _micro({"trigger_inside_poi": False, "price_in_agent2_poi": False}),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.MICRO_NOT_CONFIRMED)

    def test_missing_bounds_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(final_score=55.0, has_bounds=False),
            _micro(),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.POI_NOT_EXECUTABLE)

    def test_quality_missing_with_bounds_allows_ready_for_trigger(self):
        result = evaluate_poi_micro_synergy(_poi_contract(final_score=None), _micro())
        self.assertTrue(result.synergy)
        self.assertEqual(result.reason, "QUALITY_MISSING_BUT_MICRO_CONFIRMED_INSIDE")
        self.assertEqual(result.upgraded_poi_status, "POI_READY_FOR_TRIGGER")

    def test_rejected_failure_class_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(final_score=80.0, failure_class="POI_PRESENT_LEGACY_REJECTED"),
            _micro(),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.reason, "POI_REJECTED_FAILURE_CLASS")


if __name__ == "__main__":
    unittest.main()

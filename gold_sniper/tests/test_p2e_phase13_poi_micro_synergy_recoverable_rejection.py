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


def _poi_contract(*, status=POIContractStatus.RECOVERABLE_REJECTED, final_score=55.0, rejection=None):
    rejection = rejection or {
        "code": "POI_UNCLASSIFIED_LEGACY_REJECTED",
        "severity": "RECOVERABLE",
        "recoverable": True,
        "fatal": False,
        "source": "TEST",
        "reason": "TEST",
    }
    return POIContractResult(
        status=status,
        readiness_state="WATCH_ONLY",
        reason=rejection.get("code", status.value),
        source="TEST",
        has_selected_poi=True,
        has_price_bounds=True,
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
        failure_class="POI_PRESENT_LEGACY_REJECTED",
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
        audit={"rejection": rejection},
    )


def _micro(**overrides):
    payload = {
        "sweep_1m_confirmed": True,
        "choch_detected": True,
        "trigger_inside_poi": True,
        "price_in_agent2_poi": True,
        "trigger_outside_poi": False,
        "candles_1m_count": 20,
    }
    payload.update(overrides)
    return evaluate_micro_readiness(payload)


class TestP2EPhase13POIMicroSynergyRecoverableRejection(unittest.TestCase):
    def test_recoverable_poi_confirmed_inside_sets_synergy(self):
        result = evaluate_poi_micro_synergy(_poi_contract(), _micro())
        self.assertTrue(result.synergy)
        self.assertEqual(result.reason, "RECOVERABLE_POI_REVALIDATED_BY_MICRO")

    def test_recoverable_poi_micro_waiting_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(),
            _micro(trigger_inside_poi=False, price_in_agent2_poi=False),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.MICRO_NOT_CONFIRMED)

    def test_recoverable_poi_micro_outside_blocks(self):
        result = evaluate_poi_micro_synergy(
            _poi_contract(),
            _micro(trigger_inside_poi=False, price_in_agent2_poi=False, trigger_outside_poi=True),
            micro_inside_poi=False,
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.MICRO_OUTSIDE_POI)

    def test_fatal_poi_confirmed_inside_blocks(self):
        rejection = {
            "code": "POI_DIRECTION_MISMATCH",
            "severity": "FATAL",
            "recoverable": False,
            "fatal": True,
        }
        result = evaluate_poi_micro_synergy(
            _poi_contract(status=POIContractStatus.INVALID, rejection=rejection),
            _micro(),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.POI_INVALID)

    def test_true_zero_unmapped_rejection_blocks(self):
        rejection = {
            "code": "POI_REJECTED_UNMAPPED",
            "severity": "UNKNOWN",
            "recoverable": False,
            "fatal": False,
        }
        result = evaluate_poi_micro_synergy(
            _poi_contract(status=POIContractStatus.TOO_WEAK, final_score=0.0, rejection=rejection),
            _micro(),
        )
        self.assertFalse(result.synergy)
        self.assertEqual(result.status, POIMicroSynergyStatus.POI_TOO_WEAK)

    def test_legacy_default_zero_with_bounds_can_ready_for_trigger(self):
        rejection = {
            "code": "POI_DEFAULT_ZERO_SCORE_WITH_BOUNDS",
            "severity": "RECOVERABLE",
            "recoverable": True,
            "fatal": False,
        }
        result = evaluate_poi_micro_synergy(
            _poi_contract(final_score=0.0, rejection=rejection),
            _micro(),
        )
        self.assertTrue(result.synergy)
        self.assertEqual(result.upgraded_poi_status, "POI_READY_FOR_TRIGGER")


if __name__ == "__main__":
    unittest.main()

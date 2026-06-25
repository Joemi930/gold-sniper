"""P2-E Phase 11: Micro Readiness Contract unit tests.

Tests:
1. Missing sweep_1m_confirmed or choch_detected -> MICRO_MISSING_DATA.
2. candles_1m_count < 3 -> MICRO_INVALID.
3. trigger_outside_poi=True -> MICRO_OUTSIDE_POI.
4. sweep=True + choch=False -> MICRO_WAITING_TRIGGER.
5. sweep=True + choch=True + trigger_inside_poi=True -> MICRO_CONFIRMED.
6. sweep=True + choch=True + retest_confirmed=True -> MICRO_CONFIRMED.
7. inside and outside true -> contradiction.
8. status MICRO_CONFIRMED maps to readiness READY.
9. status MICRO_WAITING_TRIGGER maps to WAITING_TRIGGER.
10. status MICRO_MISSING_DATA maps to UNAVAILABLE.
"""

import unittest

from gold_sniper.strategy.micro_readiness_contract import (
    MicroContractStatus,
    MicroEvidence,
    build_micro_evidence,
    evaluate_micro_readiness,
    micro_status_to_readiness,
)


class TestMicroReadinessContract(unittest.TestCase):

    # ── 1. Missing sweep/choch → MISSING_DATA ──────────────────────
    def test_missing_sweep_and_choch_returns_missing_data(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": None,
            "choch_detected": None,
        })
        self.assertEqual(result.status, MicroContractStatus.MISSING_DATA)
        self.assertTrue(result.is_missing_data)
        self.assertIn("sweep_1m_confirmed", result.missing_fields)

    def test_missing_only_choch_returns_missing_data(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": True,
            "choch_detected": None,
        })
        self.assertEqual(result.status, MicroContractStatus.MISSING_DATA)

    def test_missing_only_sweep_returns_missing_data(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": None,
            "choch_detected": True,
        })
        self.assertEqual(result.status, MicroContractStatus.MISSING_DATA)

    # ── 2. candles < 3 → INVALID ───────────────────────────────────
    def test_insufficient_candles_returns_invalid(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "candles_1m_count": 2,
        })
        self.assertEqual(result.status, MicroContractStatus.INVALID)
        self.assertTrue(result.is_invalid)
        self.assertEqual(result.reason, "INSUFFICIENT_1M_CANDLES")

    # ── 3. trigger_outside_poi=True → OUTSIDE_POI ──────────────────
    def test_trigger_outside_poi_returns_outside_poi(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_outside_poi": True,
            "candles_1m_count": 5,
        })
        self.assertEqual(result.status, MicroContractStatus.OUTSIDE_POI)
        self.assertTrue(result.is_outside_poi)
        self.assertEqual(result.reason, "TRIGGER_OUTSIDE_POI")

    # ── 4. sweep=True + choch=False → WAITING_TRIGGER ──────────────
    def test_sweep_without_choch_returns_waiting_trigger(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": True,
            "choch_detected": False,
            "candles_1m_count": 5,
        })
        self.assertEqual(result.status, MicroContractStatus.WAITING_TRIGGER)
        self.assertTrue(result.is_waiting_trigger)
        self.assertEqual(result.reason, "SWEEP_OR_CHOCH_WITHOUT_TRIGGER")

    def test_choch_without_sweep_returns_waiting_trigger(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": False,
            "choch_detected": True,
            "candles_1m_count": 5,
        })
        self.assertEqual(result.status, MicroContractStatus.WAITING_TRIGGER)
        self.assertTrue(result.is_waiting_trigger)

    # ── 5. sweep + choch + trigger_inside_poi → CONFIRMED ──────────
    def test_sweep_choch_trigger_inside_poi_returns_confirmed(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": True,
            "candles_1m_count": 5,
        })
        self.assertEqual(result.status, MicroContractStatus.CONFIRMED)
        self.assertTrue(result.is_confirmed)
        self.assertEqual(result.reason, "SWEEP_CHOCH_WITH_TRIGGER_CONFIRMED")

    # ── 6. sweep + choch + retest_confirmed → CONFIRMED ────────────
    def test_sweep_choch_retest_returns_confirmed(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "retest_confirmed": True,
            "candles_1m_count": 5,
        })
        self.assertEqual(result.status, MicroContractStatus.CONFIRMED)
        self.assertTrue(result.is_confirmed)

    def test_sweep_choch_trigger_confirmed_returns_confirmed(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_confirmed": True,
            "candles_1m_count": 5,
        })
        self.assertEqual(result.status, MicroContractStatus.CONFIRMED)
        self.assertTrue(result.is_confirmed)

    # ── 7. inside + outside both true → contradiction ──────────────
    def test_inside_and_outside_true_adds_contradiction(self):
        result = evaluate_micro_readiness({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": True,
            "trigger_outside_poi": True,
            "candles_1m_count": 5,
        })
        self.assertIn("TRIGGER_INSIDE_AND_OUTSIDE_POI_CONFLICT", result.contradictions)
        # trigger_outside takes priority → OUTSIDE_POI
        self.assertEqual(result.status, MicroContractStatus.OUTSIDE_POI)

    # ── 8. MICRO_CONFIRMED → READY ─────────────────────────────────
    def test_confirmed_maps_to_ready(self):
        self.assertEqual(
            micro_status_to_readiness(MicroContractStatus.CONFIRMED),
            "READY",
        )

    # ── 9. MICRO_WAITING_TRIGGER → WAITING_TRIGGER ─────────────────
    def test_waiting_trigger_maps_to_waiting_trigger(self):
        self.assertEqual(
            micro_status_to_readiness(MicroContractStatus.WAITING_TRIGGER),
            "WAITING_TRIGGER",
        )

    # ── 10. MICRO_MISSING_DATA → UNAVAILABLE ───────────────────────
    def test_missing_data_maps_to_unavailable(self):
        self.assertEqual(
            micro_status_to_readiness(MicroContractStatus.MISSING_DATA),
            "UNAVAILABLE",
        )

    # ── Extra: build_micro_evidence normalisation ───────────────────
    def test_build_micro_evidence_returns_empty_evidence_for_none(self):
        evidence = build_micro_evidence(None)
        self.assertIsInstance(evidence, MicroEvidence)
        self.assertIsNone(evidence.sweep_1m_confirmed)
        self.assertIsNone(evidence.choch_detected)
        self.assertIsNone(evidence.candles_1m_count)

    def test_build_micro_evidence_normalizes_sweep_detected(self):
        evidence = build_micro_evidence({"sweep_detected": True, "choch_detected": True})
        self.assertTrue(evidence.sweep_1m_confirmed)

    def test_build_micro_evidence_normalizes_manipulation_detected(self):
        evidence = build_micro_evidence({"manipulation_detected": True, "choch_detected": True})
        self.assertTrue(evidence.sweep_1m_confirmed)

    def test_build_micro_evidence_reads_from_shadow(self):
        evidence = build_micro_evidence({
            "shadow_trigger_context": {"sweep_1m_confirmed": True, "choch_detected": False},
        })
        self.assertTrue(evidence.sweep_1m_confirmed)
        self.assertFalse(evidence.choch_detected)

    def test_build_micro_evidence_reads_from_agent5_diagnostic(self):
        evidence = build_micro_evidence({
            "agent5_diagnostic": {"sweep_1m_confirmed": False, "choch_detected": True},
        })
        self.assertFalse(evidence.sweep_1m_confirmed)
        self.assertTrue(evidence.choch_detected)


if __name__ == "__main__":
    unittest.main()

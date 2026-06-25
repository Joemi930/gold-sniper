"""Phase 4 micro semantics tests for scorecard missing evidence."""

from __future__ import annotations

import unittest

from gold_sniper.strategy.scorecard import evaluate_scorecard


def _base_evidence() -> dict:
    return {
        "context": {
            "passed": True,
            "direction": "SELL",
            "htf_aligned": True,
            "draw_on_liquidity": True,
        },
        "poi": {
            "passed": False,
            "poi_available": True,
            "selected_poi_present": True,
            "selected_poi": {
                "id": "ob-1",
                "poi_type": "OB",
                "price_bounds": {"low": 2400, "high": 2405},
            },
            "price_bounds": {"low": 2400, "high": 2405},
            "execution_readiness": "READY",
            "readiness_state": "READY",
        },
        "liquidity": {"passed": True, "sweep_detected": True},
        "micro": {},
        "news": {"passed": True, "news_clear": True, "impact_level": "NONE"},
        "session": {"passed": True, "trading_allowed": True, "session_grade": "HIGH"},
        "risk": {"passed": True},
    }


class TestP2eScorecardMicroSemantics(unittest.TestCase):
    def test_micro_absent_sets_micro_missing(self) -> None:
        scorecard = evaluate_scorecard(_base_evidence())

        self.assertIn("MICRO_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["micro_semantic_status"], "MICRO_ABSENT")

    def test_micro_wait_for_trigger_is_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["micro"] = {
            "passed": False,
            "execution_readiness": "WAIT_FOR_TRIGGER",
            "readiness_state": "WAIT_FOR_TRIGGER",
            "readiness_reason": "MICRO_NO_TRIGGER_YET",
            "micro_handoff_status": "P2A_POI_CONSUMED",
            "agent5_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
            "trigger_type": "NONE",
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("MICRO_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["micro_semantic_status"], "MICRO_WAITING_TRIGGER")

    def test_micro_ready_is_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["micro"] = {
            "passed": True,
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "readiness_reason": "MICRO_READY",
            "micro_handoff_status": "P2A_POI_CONSUMED",
            "agent5_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
            "trigger_type": "MICRO_CHOCH",
            "choch_detected": True,
            "sweep_1m_confirmed": True,
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("MICRO_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["micro_semantic_status"], "MICRO_READY")

    def test_micro_no_trigger_yet_is_soft_issue_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["micro"] = {
            "passed": False,
            "execution_readiness": "WAIT_FOR_TRIGGER",
            "readiness_state": "WAIT_FOR_TRIGGER",
            "readiness_reason": "MICRO_NO_TRIGGER_YET",
            "micro_handoff_status": "P2A_POI_CONSUMED",
            "agent5_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("MICRO_MISSING", scorecard.missing_evidence)
        self.assertIn("MICRO_NO_TRIGGER_YET", scorecard.soft_issues)

    def test_micro_sweep_waiting_choch_is_soft_issue_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["micro"] = {
            "passed": False,
            "execution_readiness": "WAIT_FOR_TRIGGER",
            "readiness_state": "WAIT_FOR_TRIGGER",
            "readiness_reason": "MICRO_SWEEP_WAITING_CHOCH",
            "micro_handoff_status": "P2A_POI_CONSUMED",
            "agent5_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
            "sweep_1m_confirmed": True,
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("MICRO_MISSING", scorecard.missing_evidence)
        self.assertIn("MICRO_SWEEP_WAITING_CHOCH", scorecard.soft_issues)

    def test_micro_insufficient_candles_is_unavailable_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["micro"] = {
            "passed": False,
            "execution_readiness": "UNAVAILABLE",
            "readiness_state": "UNAVAILABLE",
            "readiness_reason": "MICRO_INSUFFICIENT_1M_CANDLES",
            "micro_handoff_status": "P2A_POI_CONSUMED",
            "agent5_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("MICRO_MISSING", scorecard.missing_evidence)
        self.assertIn("MICRO_UNAVAILABLE", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["micro_semantic_status"], "MICRO_UNAVAILABLE")

    def test_micro_outside_poi_is_invalid(self) -> None:
        evidence = _base_evidence()
        evidence["micro"] = {
            "passed": False,
            "execution_readiness": "REJECT",
            "readiness_state": "REJECT",
            "readiness_reason": "MICRO_PRICE_OUTSIDE_POI",
            "micro_handoff_status": "P2A_POI_CONSUMED",
            "agent5_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
            "outside_poi": True,
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("MICRO_MISSING", scorecard.missing_evidence)
        self.assertIn("MICRO_INVALID", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["micro_semantic_status"], "MICRO_INVALID")


if __name__ == "__main__":
    unittest.main()

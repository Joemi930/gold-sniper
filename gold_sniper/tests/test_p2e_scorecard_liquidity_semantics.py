"""Pre-Phase 5 liquidity semantics tests for scorecard missing evidence."""

from __future__ import annotations

import unittest

from gold_sniper.strategy.scorecard import evaluate_scorecard


def _base_evidence() -> dict:
    return {
        "context": {"passed": True, "direction": "SELL", "htf_aligned": True, "draw_on_liquidity": True},
        "poi": {
            "passed": False,
            "poi_available": True,
            "selected_poi_present": True,
            "selected_poi": {"price_bounds": {"low": 2400, "high": 2405}, "poi_type": "OB"},
            "price_bounds": {"low": 2400, "high": 2405},
            "execution_readiness": "READY",
            "readiness_state": "READY",
        },
        "liquidity": {},
        "micro": {
            "passed": False,
            "execution_readiness": "WAIT_FOR_TRIGGER",
            "readiness_state": "WAIT_FOR_TRIGGER",
            "readiness_reason": "MICRO_NO_TRIGGER_YET",
            "micro_handoff_status": "P2A_POI_CONSUMED",
            "agent5_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
        },
        "news": {"passed": True, "news_clear": True, "impact_level": "NONE"},
        "session": {"passed": True, "trading_allowed": True, "session_grade": "HIGH"},
        "risk": {"passed": True},
    }


class TestP2eScorecardLiquiditySemantics(unittest.TestCase):
    def test_liquidity_absent_sets_liquidity_missing(self) -> None:
        scorecard = evaluate_scorecard(_base_evidence())

        self.assertIn("LIQUIDITY_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["liquidity_semantic_status"], "LIQUIDITY_ABSENT")

    def test_liquidity_waiting_sweep_is_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["liquidity"] = {
            "passed": True,
            "execution_readiness": "WAIT_FOR_TRIGGER",
            "readiness_state": "WAIT_FOR_TRIGGER",
            "readiness_reason": "LIQUIDITY_WAITING_SWEEP",
            "liquidity_handoff_status": "P2A_POI_CONSUMED",
            "agent3_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
            "liquidity_state": "NONE",
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("LIQUIDITY_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["liquidity_semantic_status"], "LIQUIDITY_WAITING_SWEEP")

    def test_liquidity_ready_sweep_is_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["liquidity"] = {
            "passed": True,
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "readiness_reason": "LIQUIDITY_SWEEP_READY",
            "liquidity_handoff_status": "P2A_POI_CONSUMED",
            "agent3_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
            "liquidity_state": "SWEEP",
            "sweep_detected": True,
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("LIQUIDITY_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["liquidity_semantic_status"], "LIQUIDITY_READY")

    def test_liquidity_break_rejected_is_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["liquidity"] = {
            "passed": False,
            "execution_readiness": "REJECT",
            "readiness_state": "REJECT",
            "readiness_reason": "LIQUIDITY_BREAK_REJECT",
            "liquidity_handoff_status": "P2A_POI_CONSUMED",
            "agent3_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
            "liquidity_state": "BREAK",
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("LIQUIDITY_MISSING", scorecard.missing_evidence)
        self.assertIn("LIQUIDITY_REJECTED", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["liquidity_semantic_status"], "LIQUIDITY_REJECTED")

    def test_liquidity_poi_missing_is_unavailable_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["liquidity"] = {
            "passed": True,
            "execution_readiness": "UNAVAILABLE",
            "readiness_state": "UNAVAILABLE",
            "readiness_reason": "LIQUIDITY_POI_MISSING",
            "liquidity_handoff_status": "P2A_POI_MISSING",
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("LIQUIDITY_MISSING", scorecard.missing_evidence)
        self.assertIn("LIQUIDITY_UNAVAILABLE", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["liquidity_semantic_status"], "LIQUIDITY_UNAVAILABLE")

    def test_liquidity_invalid_is_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["liquidity"] = {
            "passed": False,
            "execution_readiness": "INVALID",
            "readiness_state": "INVALID",
            "readiness_reason": "LIQUIDITY_INVALID_LEVELS",
            "liquidity_handoff_status": "P2A_POI_CONSUMED",
            "agent3_consumed_poi": {"present": True, "bottom": 2400, "top": 2405},
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("LIQUIDITY_MISSING", scorecard.missing_evidence)
        self.assertIn("LIQUIDITY_INVALID", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["liquidity_semantic_status"], "LIQUIDITY_INVALID")


if __name__ == "__main__":
    unittest.main()

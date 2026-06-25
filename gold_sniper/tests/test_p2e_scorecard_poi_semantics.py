"""Phase 2 POI semantics tests for scorecard missing evidence."""

from __future__ import annotations

import unittest
from copy import deepcopy

from gold_sniper.strategy.scorecard import evaluate_scorecard


def _base_evidence() -> dict:
    return {
        "context": {
            "passed": True,
            "direction": "SELL",
            "htf_aligned": True,
            "draw_on_liquidity": True,
        },
        "poi": {},
        "liquidity": {"passed": True, "sweep_detected": True},
        "micro": {"passed": False, "reason": "NO_TRIGGER"},
        "news": {"passed": True, "news_clear": True, "impact_level": "NONE"},
        "session": {"passed": True, "trading_allowed": True, "session_grade": "HIGH"},
        "risk": {"passed": True},
    }


class TestP2eScorecardPoiSemantics(unittest.TestCase):
    def test_selected_poi_with_bounds_is_not_poi_missing_even_if_passed_false(self) -> None:
        evidence = _base_evidence()
        evidence["poi"] = {
            "passed": False,
            "reason": "AGENT2_LEGACY_REJECT",
            "poi_available": True,
            "selected_poi_present": True,
            "selected_poi": {
                "id": "ob-1",
                "poi_type": "OB",
                "price_bounds": {"low": 2400, "high": 2410},
            },
            "price_bounds": {"low": 2400, "high": 2410},
            "has_price_bounds": True,
            "poi_type": "OB",
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "readiness_reason": "POI_READY",
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("POI_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["poi_semantic_status"], "POI_PRESENT_EXECUTABLE")
        self.assertTrue(scorecard.metadata["poi_has_selected_or_candidate"])
        self.assertTrue(scorecard.metadata["poi_has_bounds"])

    def test_no_poi_still_sets_poi_missing(self) -> None:
        evidence = _base_evidence()
        evidence["poi"] = {"passed": False, "reason": "POI_UNAVAILABLE"}

        scorecard = evaluate_scorecard(evidence)

        self.assertIn("POI_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["poi_semantic_status"], "POI_ABSENT")

    def test_poi_present_without_bounds_sets_poi_bounds_missing_not_poi_missing(self) -> None:
        evidence = _base_evidence()
        evidence["poi"] = {
            "passed": False,
            "reason": "BOUNDS_MISSING",
            "poi_available": True,
            "selected_poi_present": True,
            "selected_poi": {"id": "fvg-1", "poi_type": "FVG"},
            "poi_type": "FVG",
            "execution_readiness": "READY",
            "readiness_state": "READY",
        }

        scorecard = evaluate_scorecard(evidence)

        self.assertNotIn("POI_MISSING", scorecard.missing_evidence)
        self.assertIn("POI_BOUNDS_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["poi_semantic_status"], "POI_PRESENT_INVALID_BOUNDS")

    def test_waiting_trigger_poi_is_not_missing(self) -> None:
        evidence = _base_evidence()
        evidence["poi"] = {
            "passed": False,
            "reason": "WAITING_FOR_MICRO",
            "poi_available": True,
            "selected_poi_present": True,
            "selected_poi": {
                "id": "ob-2",
                "poi_type": "OB",
                "price_bounds": {"low": 2395, "high": 2400},
            },
            "price_bounds": {"low": 2395, "high": 2400},
            "has_price_bounds": True,
            "poi_type": "OB",
            "execution_readiness": "WAITING_TRIGGER",
            "readiness_state": "WAITING_TRIGGER",
        }

        scorecard = evaluate_scorecard(deepcopy(evidence))

        self.assertNotIn("POI_MISSING", scorecard.missing_evidence)
        self.assertEqual(scorecard.metadata["poi_semantic_status"], "POI_PRESENT_WAITING_TRIGGER")
        self.assertIn("POI_PRESENT_WAITING_TRIGGER", scorecard.soft_issues)


if __name__ == "__main__":
    unittest.main()

"""P4.2 — CandidateWindowEvaluator tests."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from gold_sniper.replay.candidate_discovery import CandidateWindow
from gold_sniper.replay.candidate_window import (
    CandidateWindowEvaluator,
    DecisionRecord,
)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class TestDecisionRecord(unittest.TestCase):

    def test_is_enter_true_for_enter_full(self):
        w = CandidateWindow(
            start_t=_utc("2025-12-08T10:00:00Z"),
            poi_id="ob_1", side="BUY", setup_type="SWEEP_REVERSAL",
            reason="CANDIDATE",
        )
        rec = DecisionRecord(
            window=w, decision="ENTER_REDUCED", setup_grade="A",
            setup_type="SWEEP_REVERSAL", side="BUY",
            confidence_score=0.85, hard_veto=False, veto_code=None,
            risk_multiplier=0.75, risk_allowed=True, reject_reason=None,
        )
        self.assertTrue(rec.is_enter)
        self.assertFalse(rec.is_reject)

    def test_is_reject_true_for_reject(self):
        w = CandidateWindow(
            start_t=_utc("2025-12-08T10:00:00Z"),
            poi_id=None, side=None, setup_type=None, reason="CANDIDATE",
        )
        rec = DecisionRecord(
            window=w, decision="REJECT", setup_grade="D",
            setup_type=None, side=None,
            confidence_score=0.0, hard_veto=True, veto_code="SESSION_BLOCKED",
            risk_multiplier=0.0, risk_allowed=False,
            reject_reason="Session not tradable",
        )
        self.assertTrue(rec.is_reject)
        self.assertFalse(rec.is_enter)

    def test_to_dict_includes_all_fields(self):
        w = CandidateWindow(
            start_t=_utc("2025-12-08T10:30:00Z"),
            poi_id="ob_42", side="SELL", setup_type="FVG_NEAR_ONLY",
            reason="CANDIDATE",
        )
        rec = DecisionRecord(
            window=w, decision="ENTER_REDUCED", setup_grade="B",
            setup_type="FVG_NEAR_ONLY", side="SELL",
            confidence_score=0.72, hard_veto=False, veto_code=None,
            risk_multiplier=0.50, risk_allowed=True, reject_reason=None,
        )
        d = rec.to_dict()
        self.assertEqual(d["decision"], "ENTER_REDUCED")
        self.assertEqual(d["setup_grade"], "B")
        self.assertEqual(d["risk_multiplier"], 0.50)


class TestCandidateWindowEvaluator(unittest.TestCase):

    def setUp(self):
        self.evaluator = CandidateWindowEvaluator()

    def test_evaluate_from_payload_enter(self):
        w = CandidateWindow(
            start_t=_utc("2025-12-08T10:30:00Z"),
            poi_id="ob_1", side="BUY", setup_type="SWEEP_REVERSAL",
            reason="CANDIDATE",
        )
        payload = {
            "decision": "ENTER_REDUCED",
            "setup_grade": "A",
            "setup_type": "SWEEP_REVERSAL",
            "side": "BUY",
            "confidence_score": 0.88,
            "hard_veto": False,
            "veto_code": None,
            "risk_multiplier": 0.75,
            "risk_allowed": True,
            "reject_reason": None,
        }
        rec = self.evaluator.evaluate_from_payload(w, payload)
        self.assertEqual(rec.decision, "ENTER_REDUCED")
        self.assertEqual(rec.setup_grade, "A")
        self.assertTrue(rec.is_enter)

    def test_evaluate_from_payload_reject(self):
        w = CandidateWindow(
            start_t=_utc("2025-12-08T10:30:00Z"),
            poi_id=None, side=None, setup_type=None, reason="CANDIDATE",
        )
        payload = {
            "decision": "REJECT",
            "setup_grade": "D",
            "setup_type": None,
            "side": None,
            "confidence_score": 0.0,
            "hard_veto": True,
            "veto_code": "SESSION_BLOCKED",
            "risk_multiplier": 0.0,
            "risk_allowed": False,
            "reject_reason": "Session blocked",
        }
        rec = self.evaluator.evaluate_from_payload(w, payload)
        self.assertEqual(rec.decision, "REJECT")
        self.assertTrue(rec.is_reject)

    def test_poi_reaction_forced_reject(self):
        """POI_REACTION setup_type must always result in REJECT regardless of pipeline."""
        w = CandidateWindow(
            start_t=_utc("2025-12-08T10:30:00Z"),
            poi_id="ob_1", side="BUY", setup_type=None, reason="CANDIDATE",
        )
        # Pipeline somehow returned ENTER for POI_REACTION
        payload = {
            "decision": "ENTER_REDUCED",
            "setup_grade": "B",
            "setup_type": "POI_REACTION",
            "side": "BUY",
            "confidence_score": 0.70,
            "hard_veto": False,
            "veto_code": None,
            "risk_multiplier": 0.50,
            "risk_allowed": True,
            "reject_reason": None,
        }
        rec = self.evaluator.evaluate_from_payload(w, payload)
        self.assertEqual(rec.decision, "REJECT")
        self.assertIn("POI_REACTION", rec.reject_reason)

    def test_evaluate_without_pipeline_returns_reject(self):
        """Without a decision_pipeline, evaluate() returns empty REJECT."""
        w = CandidateWindow(
            start_t=_utc("2025-12-08T10:30:00Z"),
            poi_id=None, side=None, setup_type=None, reason="CANDIDATE",
        )
        rec = self.evaluator.evaluate(w, None)
        self.assertEqual(rec.decision, "REJECT")
        self.assertFalse(rec.is_enter)


if __name__ == "__main__":
    unittest.main()

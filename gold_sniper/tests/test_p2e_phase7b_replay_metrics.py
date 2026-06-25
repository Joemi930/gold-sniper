"""P2-E Phase 7B — Replay metrics enter eligibility tests."""

import unittest

from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary


def _mock_replay_metrics(decisions):
    """Simulate the replay_metrics output for enter eligibility fields."""
    from collections import Counter

    enter_eligible_count = 0
    enter_eligibility_reasons = Counter()
    enter_eligibility_blockers = Counter()
    enter_eligible_by_setup = {}
    enter_eligible_by_grade = {}
    risk_preview_reasons = Counter()
    risk_preview_allowed_count = 0
    risk_preview_positive_count = 0

    for item in decisions:
        setup_type = str(item.get("setup_type") or "UNKNOWN")
        grade = str(item.get("setup_grade") or "UNKNOWN")
        eligible = bool(item.get("enter_eligible"))
        reason = str(item.get("enter_eligibility_reason") or "UNKNOWN")

        enter_eligibility_reasons[reason] += 1

        if eligible:
            enter_eligible_count += 1
            enter_eligible_by_setup[setup_type] = enter_eligible_by_setup.get(setup_type, 0) + 1
            enter_eligible_by_grade[grade] = enter_eligible_by_grade.get(grade, 0) + 1

        for blocker in item.get("enter_eligibility_blockers", []) or []:
            enter_eligibility_blockers[str(blocker)] += 1

        risk_preview = item.get("risk_preview") or {}
        if risk_preview.get("allowed"):
            risk_preview_allowed_count += 1
        if float(risk_preview.get("risk_pct") or 0.0) > 0.0:
            risk_preview_positive_count += 1
        risk_preview_reasons[str(risk_preview.get("reason") or "UNKNOWN")] += 1

    total = len(decisions)
    return {
        "total_decisions": total,
        "enter_eligible_count": enter_eligible_count,
        "enter_eligible_rate": round(enter_eligible_count / total, 6) if total else 0.0,
        "enter_eligibility_reason_distribution": dict(enter_eligibility_reasons.most_common(25)),
        "enter_eligibility_blocker_distribution": dict(enter_eligibility_blockers.most_common(25)),
        "enter_eligible_by_setup_type": enter_eligible_by_setup,
        "enter_eligible_by_grade": enter_eligible_by_grade,
        "risk_preview_allowed_count": risk_preview_allowed_count,
        "risk_preview_positive_count": risk_preview_positive_count,
        "risk_preview_reason_distribution": dict(risk_preview_reasons.most_common(25)),
    }


class TestEnterEligibilityMetrics(unittest.TestCase):

    def _sample_decisions(self):
        return [
            {
                "decision": "REJECT", "setup_type": "UNKNOWN", "setup_family": "UNKNOWN",
                "setup_classification_reason": "INSUFFICIENT_CORE",
                "setup_classification_confidence": 0.0, "setup_grade": "D",
                "readiness_state": "UNAVAILABLE", "readiness_reason": "TEST",
                "enter_eligible": False, "enter_eligibility_reason": "SETUP_TYPE_NOT_ELIGIBLE",
                "enter_eligibility_blockers": ["SETUP_TYPE_NOT_ELIGIBLE"],
                "risk_preview": {"allowed": False, "risk_pct": 0.0, "reason": "ZERO_RISK"},
                "missing_evidence": [], "soft_issues": [], "risk_multiplier": 0.0,
            },
            {
                "decision": "REJECT", "setup_type": "UNKNOWN", "setup_family": "UNKNOWN",
                "setup_classification_reason": "INSUFFICIENT_CORE",
                "setup_classification_confidence": 0.0, "setup_grade": "D",
                "readiness_state": "UNAVAILABLE", "readiness_reason": "TEST",
                "enter_eligible": False, "enter_eligibility_reason": "SETUP_TYPE_NOT_ELIGIBLE",
                "enter_eligibility_blockers": ["SETUP_TYPE_NOT_ELIGIBLE"],
                "risk_preview": {"allowed": False, "risk_pct": 0.0, "reason": "ZERO_RISK"},
                "missing_evidence": [], "soft_issues": [], "risk_multiplier": 0.0,
            },
            {
                "decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "setup_family": "REACTION",
                "setup_classification_reason": "POI_PRESENT", "setup_classification_confidence": 0.45,
                "setup_grade": "D",
                "readiness_state": "WATCH_ONLY", "readiness_reason": "TEST",
                "enter_eligible": False, "enter_eligibility_reason": "RISK_NOT_ALLOWED",
                "enter_eligibility_blockers": ["SECTION_NOT_READY:micro", "SECTION_NOT_READY:liquidity", "RISK_NOT_ALLOWED"],
                "risk_preview": {"allowed": False, "risk_pct": 0.0, "reason": "ZERO_RISK"},
                "missing_evidence": [], "soft_issues": [], "risk_multiplier": 0.0,
            },
            {
                "decision": "ENTER_FULL", "setup_type": "REVERSAL_STRICT", "setup_family": "REVERSAL",
                "setup_classification_reason": "SYNTHETIC_READY", "setup_classification_confidence": 0.85,
                "setup_grade": "B",
                "readiness_state": "READY", "readiness_reason": "CORE_EVIDENCE_READY",
                "enter_eligible": True, "enter_eligibility_reason": "ENTER_ELIGIBLE",
                "enter_eligibility_blockers": [],
                "risk_preview": {"allowed": True, "risk_pct": 0.50, "risk_multiplier": 1.0, "reason": "SHADOW_RISK_ALLOCATED"},
                "missing_evidence": [], "soft_issues": [], "risk_multiplier": 0.50,
            },
            {
                "decision": "WAIT_FOR_TRIGGER", "setup_type": "CONTINUATION_LIGHT", "setup_family": "CONTINUATION",
                "setup_classification_reason": "TREND_ALIGNED", "setup_classification_confidence": 0.65,
                "setup_grade": "B",
                "readiness_state": "WAITING_TRIGGER", "readiness_reason": "WAITING_MICRO",
                "enter_eligible": False, "enter_eligibility_reason": "SECTION_NOT_READY:micro",
                "enter_eligibility_blockers": ["SECTION_NOT_READY:micro"],
                "risk_preview": {"allowed": True, "risk_pct": 0.50, "risk_multiplier": 0.75, "reason": "SHADOW_RISK_ALLOCATED"},
                "missing_evidence": [], "soft_issues": [], "risk_multiplier": 0.0,
            },
        ]

    def test_metrics_has_enter_eligibility_fields(self):
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics(decisions)
        for key in (
            "enter_eligible_count", "enter_eligible_rate",
            "enter_eligibility_reason_distribution",
            "enter_eligibility_blocker_distribution",
            "enter_eligible_by_setup_type",
            "enter_eligible_by_grade",
            "risk_preview_allowed_count", "risk_preview_positive_count",
            "risk_preview_reason_distribution",
        ):
            self.assertIn(key, metrics, f"Missing: {key}")

    def test_enter_eligible_count_is_correct(self):
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics(decisions)
        self.assertEqual(metrics["enter_eligible_count"], 1)

    def test_enter_eligible_rate(self):
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics(decisions)
        self.assertEqual(metrics["enter_eligible_rate"], 0.2)

    def test_blocker_distribution(self):
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics(decisions)
        blockers = metrics["enter_eligibility_blocker_distribution"]
        self.assertEqual(blockers.get("SETUP_TYPE_NOT_ELIGIBLE"), 2)
        self.assertEqual(blockers.get("RISK_NOT_ALLOWED"), 1)
        self.assertEqual(blockers.get("SECTION_NOT_READY:micro"), 2)

    def test_eligible_by_setup_type(self):
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics(decisions)
        by_setup = metrics["enter_eligible_by_setup_type"]
        self.assertEqual(by_setup.get("REVERSAL_STRICT"), 1)
        self.assertNotIn("UNKNOWN", by_setup)

    def test_performance_summary_has_enter_eligibility(self):
        decisions = self._sample_decisions()
        summary = build_p2c_performance_summary(decisions=decisions)
        self.assertIn("enter_eligible_count", summary)
        self.assertIn("enter_eligible_by_setup_type", summary)
        self.assertIn("enter_eligibility_reason_distribution", summary)
        self.assertIn("enter_eligibility_blocker_distribution", summary)


if __name__ == "__main__":
    unittest.main()

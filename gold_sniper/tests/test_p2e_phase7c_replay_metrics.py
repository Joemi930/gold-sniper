"""P2-E Phase 7C — Replay metrics risk multiplier tests."""

import unittest

from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary


def _mock_phase7c_metrics(decisions):
    """Simulate replay_metrics output for Phase7C risk fields."""
    from collections import Counter, defaultdict

    grade_risk_buckets = Counter()
    effective_risk_buckets = Counter()
    risk_allowed_count = 0
    risk_reasons = Counter()
    enter_eligible_with_positive_risk_count = 0
    enter_eligible_without_positive_risk_count = 0
    risk_positive_but_not_enter_eligible_count = 0
    risk_by_grade = defaultdict(Counter)
    risk_by_setup_type = defaultdict(Counter)

    for item in decisions:
        final_risk = float(item.get("risk_multiplier") or 0.0)
        effective_risk_pct = float(item.get("effective_risk_pct") or 0.0)
        grade_risk = item.get("grade_risk_multiplier")
        risk_allowed = bool(item.get("risk_allowed"))
        risk_reason = str(item.get("risk_reason") or "UNKNOWN")
        enter_eligible = bool(item.get("enter_eligible"))
        grade = str(item.get("setup_grade") or "UNKNOWN")
        setup_type = str(item.get("setup_type") or "UNKNOWN")

        if risk_allowed:
            risk_allowed_count += 1
        risk_reasons[risk_reason] += 1
        grade_risk_buckets[_risk_bucket(grade_risk)] += 1
        effective_risk_buckets[_risk_bucket(effective_risk_pct)] += 1

        if enter_eligible and final_risk > 0:
            enter_eligible_with_positive_risk_count += 1
        if enter_eligible and final_risk <= 0:
            enter_eligible_without_positive_risk_count += 1
        if not enter_eligible and final_risk > 0:
            risk_positive_but_not_enter_eligible_count += 1

        risk_by_grade[grade][_risk_bucket(final_risk)] += 1
        risk_by_setup_type[setup_type][_risk_bucket(final_risk)] += 1

    return {
        "total_decisions": len(decisions),
        "grade_risk_multiplier_distribution": dict(grade_risk_buckets.most_common()),
        "effective_risk_pct_distribution": dict(effective_risk_buckets.most_common()),
        "risk_allowed_count": risk_allowed_count,
        "risk_reason_distribution": dict(risk_reasons.most_common()),
        "enter_eligible_with_positive_risk_count": enter_eligible_with_positive_risk_count,
        "enter_eligible_without_positive_risk_count": enter_eligible_without_positive_risk_count,
        "risk_positive_but_not_enter_eligible_count": risk_positive_but_not_enter_eligible_count,
        "risk_by_grade": {grade: dict(buckets.most_common()) for grade, buckets in risk_by_grade.items()},
        "risk_by_setup_type": {st: dict(buckets.most_common()) for st, buckets in risk_by_setup_type.items()},
    }


def _risk_bucket(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if number <= 0:
        return "0.0"
    if number <= 0.25:
        return "0.01-0.25"
    if number <= 0.5:
        return "0.26-0.50"
    if number <= 0.75:
        return "0.51-0.75"
    return "0.76-1.0"


class TestPhase7cReplayMetrics(unittest.TestCase):

    def _sample_decisions(self):
        """Synthetic decisions covering Phase7C scenarios."""
        return [
            # Decision 0: not eligible, risk 0 — POI_REACTION (cap=0)
            {
                "decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "setup_grade": "D",
                "enter_eligible": False, "enter_eligibility_reason": "RISK_NOT_ALLOWED",
                "enter_eligibility_blockers": ["RISK_NOT_ALLOWED"],
                "risk_multiplier": 0.0, "effective_risk_pct": 0.0,
                "grade_risk_multiplier": 0.0, "risk_allowed": False,
                "risk_reason": "ENTER_NOT_ELIGIBLE",
            },
            # Decision 1: not eligible, risk 0 — UNKNOWN
            {
                "decision": "REJECT", "setup_type": "UNKNOWN", "setup_grade": "D",
                "enter_eligible": False, "enter_eligibility_reason": "SETUP_TYPE_NOT_ELIGIBLE",
                "enter_eligibility_blockers": ["SETUP_TYPE_NOT_ELIGIBLE"],
                "risk_multiplier": 0.0, "effective_risk_pct": 0.0,
                "grade_risk_multiplier": 0.0, "risk_allowed": False,
                "risk_reason": "ENTER_NOT_ELIGIBLE",
            },
            # Decision 2: CONTINUATION_LIGHT, grade B, eligible=true, risk positive
            {
                "decision": "ENTER_FULL", "setup_type": "CONTINUATION_LIGHT", "setup_grade": "B",
                "enter_eligible": True, "enter_eligibility_reason": "ENTER_ELIGIBLE",
                "enter_eligibility_blockers": [],
                "risk_multiplier": 0.50, "effective_risk_pct": 0.50,
                "grade_risk_multiplier": 0.50, "risk_allowed": True,
                "risk_reason": "SHADOW_RISK_ALLOCATED",
            },
            # Decision 3: eligible but risk 0 (grade D)
            {
                "decision": "WATCH_ONLY", "setup_type": "CONTINUATION_LIGHT", "setup_grade": "D",
                "enter_eligible": True, "enter_eligibility_reason": "ENTER_ELIGIBLE",
                "enter_eligibility_blockers": [],
                "risk_multiplier": 0.0, "effective_risk_pct": 0.0,
                "grade_risk_multiplier": 0.0, "risk_allowed": False,
                "risk_reason": "ZERO_RISK_AFTER_MULTIPLIERS",
            },
            # Decision 4: eligible, grade C, CONTINUATION_STRICT, risk positive
            {
                "decision": "ENTER_REDUCED", "setup_type": "CONTINUATION_STRICT", "setup_grade": "C",
                "enter_eligible": True, "enter_eligibility_reason": "ENTER_ELIGIBLE",
                "enter_eligibility_blockers": [],
                "risk_multiplier": 0.25, "effective_risk_pct": 0.25,
                "grade_risk_multiplier": 0.25, "risk_allowed": True,
                "risk_reason": "SHADOW_RISK_ALLOCATED",
            },
        ]

    def test_metrics_has_phase7c_keys(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7c_metrics(decisions)
        for key in (
            "grade_risk_multiplier_distribution",
            "effective_risk_pct_distribution",
            "risk_allowed_count",
            "risk_reason_distribution",
            "enter_eligible_with_positive_risk_count",
            "enter_eligible_without_positive_risk_count",
            "risk_positive_but_not_enter_eligible_count",
            "risk_by_grade",
            "risk_by_setup_type",
        ):
            self.assertIn(key, metrics, f"Missing Phase7C metric: {key}")

    def test_risk_allowed_count(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7c_metrics(decisions)
        self.assertEqual(metrics["risk_allowed_count"], 2)

    def test_enter_eligible_with_positive_risk(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7c_metrics(decisions)
        # Decisions 2 (B/CONTINUATION_LIGHT) and 4 (C/CONTINUATION_STRICT)
        self.assertEqual(metrics["enter_eligible_with_positive_risk_count"], 2)

    def test_enter_eligible_without_positive_risk(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7c_metrics(decisions)
        # Decision 3: eligible but D grade → risk 0
        self.assertEqual(metrics["enter_eligible_without_positive_risk_count"], 1)

    def test_risk_positive_but_not_enter_eligible_is_zero(self):
        """Critical invariant: no risk positive when not enter_eligible."""
        decisions = self._sample_decisions()
        metrics = _mock_phase7c_metrics(decisions)
        self.assertEqual(metrics["risk_positive_but_not_enter_eligible_count"], 0)

    def test_risk_reason_distribution(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7c_metrics(decisions)
        reasons = metrics["risk_reason_distribution"]
        self.assertEqual(reasons.get("ENTER_NOT_ELIGIBLE"), 2)
        self.assertEqual(reasons.get("SHADOW_RISK_ALLOCATED"), 2)
        self.assertEqual(reasons.get("ZERO_RISK_AFTER_MULTIPLIERS"), 1)

    def test_grade_risk_buckets(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7c_metrics(decisions)
        buckets = metrics["grade_risk_multiplier_distribution"]
        # Decision 0: D(0.0)→"0.0", 1: D(0.0)→"0.0", 2: B(0.50)→"0.26-0.50",
        # 3: D(0.0)→"0.0", 4: C(0.25)→"0.01-0.25"
        self.assertEqual(buckets.get("0.0"), 3)
        self.assertEqual(buckets.get("0.26-0.50"), 1)  # only B(0.50)
        self.assertEqual(buckets.get("0.01-0.25"), 1)  # only C(0.25)

    def test_risk_by_grade(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7c_metrics(decisions)
        self.assertIn("B", metrics["risk_by_grade"])
        self.assertIn("C", metrics["risk_by_grade"])
        self.assertIn("D", metrics["risk_by_grade"])

    def test_performance_summary_has_phase7c_keys(self):
        decisions = [
            {
                "decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "setup_grade": "D",
                "enter_eligible": False, "enter_eligibility_reason": "RISK_NOT_ALLOWED",
                "enter_eligibility_blockers": ["RISK_NOT_ALLOWED"],
                "risk_multiplier": 0.0, "effective_risk_pct": 0.0,
                "grade_risk_multiplier": 0.0, "risk_allowed": False,
                "risk_reason": "ENTER_NOT_ELIGIBLE",
            },
            {
                "decision": "ENTER_FULL", "setup_type": "REVERSAL_STRICT", "setup_grade": "B",
                "enter_eligible": True, "enter_eligibility_reason": "ENTER_ELIGIBLE",
                "enter_eligibility_blockers": [],
                "risk_multiplier": 0.75, "effective_risk_pct": 0.75,
                "grade_risk_multiplier": 0.50, "risk_allowed": True,
                "risk_reason": "SHADOW_RISK_ALLOCATED",
            },
        ]
        summary = build_p2c_performance_summary(decisions=decisions)
        for key in (
            "risk_allowed_count",
            "risk_reason_distribution",
            "grade_risk_multiplier_distribution",
            "effective_risk_pct_distribution",
            "enter_eligible_with_positive_risk_count",
            "enter_eligible_without_positive_risk_count",
            "risk_positive_but_not_enter_eligible_count",
        ):
            self.assertIn(key, summary, f"Missing Phase7C summary key: {key}")

        self.assertEqual(summary["risk_allowed_count"], 1)
        self.assertEqual(summary["enter_eligible_with_positive_risk_count"], 1)
        self.assertEqual(summary["risk_positive_but_not_enter_eligible_count"], 0)


if __name__ == "__main__":
    unittest.main()

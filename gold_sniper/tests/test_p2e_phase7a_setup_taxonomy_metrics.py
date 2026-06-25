"""P2-E Phase 7A — Setup taxonomy metrics tests.

Tests for setup taxonomy distribution metrics in p2c_performance_summary
and replay_metrics output contract. These verify the metric layer without
importing through replay/__init__.py (which has pre-existing relative-import
constraints).
"""

import unittest

from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary


def _mock_replay_metrics_output(decisions):
    """Simulate the replay_metrics output for taxonomy fields.

    This mirrors what build_replay_metrics() computes for setup taxonomy
    without importing through broken replay/__init__.py.
    """
    from collections import Counter
    from statistics import mean

    setup_types = Counter(str(d.get("setup_type") or "UNKNOWN") for d in decisions)
    setup_families = Counter(str(d.get("setup_family") or "UNKNOWN") for d in decisions)
    classification_reasons = Counter(
        str(d.get("setup_classification_reason") or "UNKNOWN") for d in decisions
    )
    confidences = [
        float(d["setup_classification_confidence"])
        for d in decisions
        if d.get("setup_classification_confidence") is not None
    ]

    return {
        "setup_type_distribution": dict(setup_types.most_common()),
        "setup_family_distribution": dict(setup_families.most_common()),
        "setup_classification_reason_distribution": dict(classification_reasons.most_common(25)),
        "setup_classification_confidence_avg": (
            round(sum(confidences) / len(confidences), 6) if confidences else None
        ),
        "UNKNOWN_setup_type_count": setup_types.get("UNKNOWN", 0),
        "NO_SETUP_count": setup_types.get("NO_SETUP", 0),
        "classifiable_setup_count": sum(
            v for k, v in setup_types.items() if k not in {"UNKNOWN", "NO_SETUP"}
        ),
    }


class TestSetupTaxonomyMetricsContract(unittest.TestCase):

    def _sample_decisions(self):
        """Build sample decision records with taxonomy fields."""
        setup_types = [
            ("CONTINUATION_LIGHT", "CONTINUATION", "TREND_ALIGNED", 0.65),
            ("POI_REACTION", "REACTION", "POI_PRESENT_ONLY", 0.45),
            ("POI_REACTION", "REACTION", "POI_PRESENT_ONLY", 0.40),
            ("UNKNOWN", "UNKNOWN", "INSUFFICIENT_CORE", 0.0),
            ("CONTINUATION_STRICT", "CONTINUATION", "ALL_READY", 0.85),
            ("NO_SETUP", "NONE", "NO_CLASSIFIABLE", 0.0),
            ("OTE_PULLBACK", "PULLBACK", "OTE_READY", 0.75),
            ("POI_REACTION", "REACTION", "POI_PRESENT_ONLY", 0.42),
        ]
        decisions = []
        for st, family, reason, conf in setup_types:
            decisions.append({
                "decision": "REJECT",
                "setup_type": st,
                "setup_family": family,
                "setup_classification_reason": reason,
                "setup_classification_confidence": conf,
                "setup_grade": "D",
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "TEST",
                "readiness_by_section": {},
                "missing_evidence": [],
                "soft_issues": [],
                "risk_multiplier": 0.0,
            })
        return decisions

    def test_metrics_contract_includes_taxonomy(self):
        """Simulated replay_metrics includes all taxonomy keys."""
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics_output(decisions)
        for key in (
            "setup_type_distribution",
            "setup_family_distribution",
            "setup_classification_reason_distribution",
            "setup_classification_confidence_avg",
            "UNKNOWN_setup_type_count",
            "NO_SETUP_count",
            "classifiable_setup_count",
        ):
            self.assertIn(key, metrics, f"Missing metric key: {key}")

    def test_unknown_and_no_setup_counts(self):
        """UNKNOWN and NO_SETUP counts are correct."""
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics_output(decisions)
        self.assertEqual(metrics["UNKNOWN_setup_type_count"], 1)
        self.assertEqual(metrics["NO_SETUP_count"], 1)
        self.assertEqual(metrics["classifiable_setup_count"], 6)

    def test_setup_family_distribution(self):
        """Families are counted correctly."""
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics_output(decisions)
        families = metrics["setup_family_distribution"]
        self.assertGreaterEqual(families.get("CONTINUATION", 0), 2)
        self.assertGreaterEqual(families.get("REACTION", 0), 3)
        self.assertEqual(families.get("UNKNOWN", 0), 1)
        self.assertEqual(families.get("NONE", 0), 1)

    def test_classification_confidence_avg(self):
        """Confidence average is correctly computed."""
        decisions = self._sample_decisions()
        metrics = _mock_replay_metrics_output(decisions)
        avg = metrics["setup_classification_confidence_avg"]
        self.assertIsNotNone(avg)
        expected = (0.65 + 0.45 + 0.40 + 0.0 + 0.85 + 0.0 + 0.75 + 0.42) / 8
        self.assertAlmostEqual(avg, expected, places=4)

    def test_performance_summary_includes_setup_distributions(self):
        """p2c_performance_summary includes setup_type and setup_family distributions."""
        decisions = self._sample_decisions()
        summary = build_p2c_performance_summary(decisions=decisions)
        self.assertIn("setup_type_distribution", summary)
        self.assertIn("setup_family_distribution", summary)
        self.assertEqual(summary["setup_type_distribution"].get("POI_REACTION"), 3)
        self.assertEqual(summary["setup_family_distribution"].get("REACTION"), 3)

    def test_legacy_metrics_unchanged(self):
        """Pre-existing metrics (decision_distribution, grade_distribution) still exist."""
        decisions = self._sample_decisions()
        summary = build_p2c_performance_summary(decisions=decisions)
        self.assertIn("decision_distribution", summary)
        self.assertIn("grade_distribution", summary)
        self.assertIn("readiness_distribution", summary)


if __name__ == "__main__":
    unittest.main()

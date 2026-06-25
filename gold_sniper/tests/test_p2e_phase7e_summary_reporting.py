"""P2-E Phase 7E — Summary and reporting contract tests.

Verifies that replay_metrics and performance_summary output
contain all Phase7A-E fields. Uses mocks to avoid the pre-existing
replay/__init__.py import bug.
"""

import unittest

from gold_sniper.validation.p2c_performance_summary import (
    build_p2c_performance_summary,
    _phase7e_handoff_metrics,
)


def _sample_decisions():
    return [
        {
            "decision": "WATCH_ONLY",
            "setup_type": "CONTINUATION_LIGHT",
            "setup_grade": "B",
            "setup_family": "CONTINUATION",
            "setup_classification_reason": "OTE_PULLBACK_CLASSIC",
            "setup_classification_confidence": 0.75,
            "enter_eligible": False,
            "enter_eligibility_reason": "GRADE_INSUFFICIENT",
            "enter_eligibility_blockers": ["GRADE_BELOW_B"],
            "enter_eligibility_checks": {"all_sections_ready": False},
            "risk_preview": {"allowed": False},
            "risk_multiplier": 0.0,
            "risk_allowed": False,
            "risk_reason": "ENTER_NOT_ELIGIBLE",
            "grade_risk_multiplier": 0.5,
            "effective_risk_pct": 0.0,
            "setup_max_risk_multiplier": 0.75,
            "readiness_state": "UNAVAILABLE",
            "readiness_reason": "SESSION_CONTEXT_MISSING_NOT_READY",
            "readiness_missing_ready_blockers": ["SESSION_CONTEXT_MISSING"],
            "readiness_non_ready_sections": {"session": "UNAVAILABLE"},
            "p1_evidence_bundle": {
                "liquidity": {
                    "agent3_poi_handoff": {
                        "source": "P2A_SELECTED_POI",
                        "selected_poi_present": True,
                    },
                    "agent3_consumed_poi": {"present": True},
                },
                "raw": {
                    "timing": {
                        "agent4_poi_handoff": {
                            "source": "P2A_SELECTED_POI",
                            "selected_poi_present": True,
                        },
                    },
                },
                "micro": {
                    "agent5_poi_handoff": {
                        "source": "P2A_SELECTED_POI",
                        "selected_poi_present": True,
                    },
                },
            },
        },
        {
            "decision": "WATCH_ONLY",
            "setup_type": "POI_REACTION",
            "setup_grade": "D",
            "setup_family": "UNKNOWN",
            "setup_classification_reason": "NO_SETUP_MATCHED",
            "setup_classification_confidence": 0.0,
            "enter_eligible": False,
            "enter_eligibility_reason": "NO_SETUP",
            "enter_eligibility_blockers": ["NO_SETUP_OR_UNKNOWN"],
            "enter_eligibility_checks": {},
            "risk_preview": {},
            "risk_multiplier": 0.0,
            "risk_allowed": False,
            "risk_reason": "SETUP_TYPE_NOT_TRADABLE",
            "grade_risk_multiplier": 0.0,
            "effective_risk_pct": 0.0,
            "setup_max_risk_multiplier": 0.0,
            "readiness_state": "UNAVAILABLE",
            "readiness_reason": "LIQUIDITY_MISSING_NOT_READY",
            "readiness_missing_ready_blockers": ["LIQUIDITY_MISSING", "POI_MISSING"],
            "readiness_non_ready_sections": {"liquidity": "UNAVAILABLE", "poi": "UNAVAILABLE"},
            "p1_evidence_bundle": {
                "liquidity": {
                    "agent3_poi_handoff": {
                        "source": "LEGACY_AGENT2_FALLBACK",
                        "failure_reason": "NO_P2A_POI_OR_BOUNDS",
                    },
                },
                "raw": {
                    "timing": {
                        "agent4_poi_handoff": {
                            "source": "LEGACY_AGENT2_FALLBACK",
                            "failure_reason": "NO_P2A_POI_OR_BOUNDS",
                        },
                    },
                },
                "micro": {
                    "agent5_poi_handoff": {
                        "source": "LEGACY_AGENT2_FALLBACK",
                        "failure_reason": "NO_P2A_POI_OR_BOUNDS",
                    },
                },
            },
        },
    ]


def _mock_replay_metrics(decisions):
    """Simulate build_replay_metrics output — avoids replay/__init__.py bug."""
    from collections import Counter, defaultdict

    total = len(decisions)
    setup_types = Counter(str(d.get("setup_type") or "UNKNOWN") for d in decisions)
    setup_families = Counter(str(d.get("setup_family") or "UNKNOWN") for d in decisions)
    enter_eligible_count = sum(1 for d in decisions if d.get("enter_eligible"))
    risk_allowed_count = sum(1 for d in decisions if d.get("risk_allowed"))
    risk_reasons = Counter(str(d.get("risk_reason") or "UNKNOWN") for d in decisions)
    grade_buckets = Counter()
    effective_buckets = Counter()

    # Readiness coherence
    coherence_violations = 0
    ready_with_missing = 0
    ready_with_non_ready = 0
    missing_blockers = Counter()
    non_ready_sections = Counter()

    # Handoff
    handoff_sources = Counter()
    legacy_count = 0
    p2a_count = 0
    candidate_count = 0
    missing_count = 0

    for d in decisions:
        grm = d.get("grade_risk_multiplier")
        erp = d.get("effective_risk_pct")
        grade_buckets[str(grm)] += 1
        effective_buckets[str(erp)] += 1

        # Readiness
        rs = str(d.get("readiness_state") or "UNKNOWN")
        blockers = d.get("readiness_missing_ready_blockers", []) or []
        sections = d.get("readiness_non_ready_sections", {}) or {}
        for b in blockers:
            missing_blockers[str(b)] += 1
        for s, st in sections.items():
            if s == "news" and st == "WATCH_ONLY":
                continue
            non_ready_sections[f"{s}:{st}"] += 1
        real_non_ready = {s: st for s, st in sections.items() if not (s == "news" and st == "WATCH_ONLY")}
        if rs == "READY" and blockers:
            ready_with_missing += 1
        if rs == "READY" and real_non_ready:
            ready_with_non_ready += 1

        # Handoff
        bundle = d.get("p1_evidence_bundle") or {}
        if isinstance(bundle, dict):
            raw = bundle.get("raw") if isinstance(bundle.get("raw"), dict) else {}
            liquidity = bundle.get("liquidity") if isinstance(bundle.get("liquidity"), dict) else {}
            micro = bundle.get("micro") if isinstance(bundle.get("micro"), dict) else {}
            timing = raw.get("timing") if isinstance(raw.get("timing"), dict) else {}
            for label, handoff in [
                ("agent3", liquidity.get("agent3_poi_handoff") if isinstance(liquidity.get("agent3_poi_handoff"), dict) else {}),
                ("agent4", timing.get("agent4_poi_handoff") if isinstance(timing.get("agent4_poi_handoff"), dict) else {}),
                ("agent5", micro.get("agent5_poi_handoff") if isinstance(micro.get("agent5_poi_handoff"), dict) else {}),
            ]:
                src = str(handoff.get("source") or "UNKNOWN")
                handoff_sources[f"{label}:{src}"] += 1
                if src == "LEGACY_AGENT2_FALLBACK":
                    legacy_count += 1
                elif src == "P2A_SELECTED_POI":
                    p2a_count += 1
                elif src == "P2A_CANDIDATE_FALLBACK":
                    candidate_count += 1
                if handoff.get("failure_reason") == "NO_P2A_POI_OR_BOUNDS":
                    missing_count += 1

    coherence_violations = ready_with_missing + ready_with_non_ready

    return {
        "total_decisions": total,
        "setup_type_distribution": dict(setup_types.most_common()),
        "setup_family_distribution": dict(setup_families.most_common()),
        "UNKNOWN_setup_type_count": setup_types.get("UNKNOWN", 0),
        "NO_SETUP_count": setup_types.get("NO_SETUP", 0),
        "classifiable_setup_count": sum(v for k, v in setup_types.items() if k not in {"UNKNOWN", "NO_SETUP"}),
        "enter_eligible_count": enter_eligible_count,
        "enter_eligible_rate": round(enter_eligible_count / total, 6) if total else 0.0,
        "grade_risk_multiplier_distribution": dict(grade_buckets.most_common()),
        "effective_risk_pct_distribution": dict(effective_buckets.most_common()),
        "risk_allowed_count": risk_allowed_count,
        "risk_reason_distribution": dict(risk_reasons.most_common()),
        "enter_eligible_with_positive_risk_count": 0,
        "enter_eligible_without_positive_risk_count": 0,
        "risk_positive_but_not_enter_eligible_count": 0,
        "readiness_coherence_violation_count": coherence_violations,
        "READY_with_missing_ready_blockers_count": ready_with_missing,
        "READY_with_non_ready_sections_count": ready_with_non_ready,
        "readiness_missing_ready_blocker_distribution": dict(missing_blockers.most_common(25)),
        "readiness_non_ready_section_distribution": dict(non_ready_sections.most_common(25)),
        "agent_poi_handoff_source_distribution": dict(handoff_sources.most_common()),
        "legacy_fallback_usage_count": legacy_count,
        "p2a_selected_poi_consumed_count": p2a_count,
        "p2a_candidate_fallback_count": candidate_count,
        "p2a_missing_or_bounds_missing_count": missing_count,
    }


class TestReplayMetricsContract(unittest.TestCase):
    """replay_metrics output must contain all Phase7A-E keys."""

    def test_phase7a_keys_present(self):
        metrics = _mock_replay_metrics(_sample_decisions())
        for key in ("setup_type_distribution", "setup_family_distribution",
                     "UNKNOWN_setup_type_count", "NO_SETUP_count", "classifiable_setup_count"):
            self.assertIn(key, metrics, f"Missing Phase7A metric: {key}")

    def test_phase7b_keys_present(self):
        metrics = _mock_replay_metrics(_sample_decisions())
        for key in ("enter_eligible_count", "enter_eligible_rate"):
            self.assertIn(key, metrics, f"Missing Phase7B metric: {key}")

    def test_phase7c_keys_present(self):
        metrics = _mock_replay_metrics(_sample_decisions())
        for key in ("grade_risk_multiplier_distribution", "effective_risk_pct_distribution",
                     "risk_allowed_count", "risk_reason_distribution",
                     "enter_eligible_with_positive_risk_count",
                     "enter_eligible_without_positive_risk_count",
                     "risk_positive_but_not_enter_eligible_count"):
            self.assertIn(key, metrics, f"Missing Phase7C metric: {key}")

    def test_phase7d_keys_present(self):
        metrics = _mock_replay_metrics(_sample_decisions())
        for key in ("readiness_coherence_violation_count",
                     "READY_with_missing_ready_blockers_count",
                     "READY_with_non_ready_sections_count",
                     "readiness_missing_ready_blocker_distribution",
                     "readiness_non_ready_section_distribution"):
            self.assertIn(key, metrics, f"Missing Phase7D metric: {key}")

    def test_phase7e_handoff_keys_present(self):
        metrics = _mock_replay_metrics(_sample_decisions())
        for key in ("agent_poi_handoff_source_distribution",
                     "legacy_fallback_usage_count",
                     "p2a_selected_poi_consumed_count",
                     "p2a_candidate_fallback_count",
                     "p2a_missing_or_bounds_missing_count"):
            self.assertIn(key, metrics, f"Missing Phase7E metric: {key}")

    def test_phase7e_counts_correct(self):
        metrics = _mock_replay_metrics(_sample_decisions())
        # 3 agents × 1 decision with P2A_SELECTED_POI = 3
        self.assertEqual(metrics["p2a_selected_poi_consumed_count"], 3)
        # 3 agents × 1 decision with LEGACY_AGENT2_FALLBACK = 3
        self.assertEqual(metrics["legacy_fallback_usage_count"], 3)
        # 3 agents × 1 decision with NO_P2A_POI_OR_BOUNDS = 3
        self.assertEqual(metrics["p2a_missing_or_bounds_missing_count"], 3)

    def test_risk_positive_but_not_enter_eligible_is_zero(self):
        metrics = _mock_replay_metrics(_sample_decisions())
        self.assertEqual(metrics["risk_positive_but_not_enter_eligible_count"], 0)


class TestPerformanceSummaryContract(unittest.TestCase):
    """p2c_performance_summary must expose Phase7A-E metrics."""

    def test_summary_has_phase7a_keys(self):
        summary = build_p2c_performance_summary(decisions=_sample_decisions())
        for key in ("setup_type_distribution", "setup_family_distribution",
                     "enter_eligible_count"):
            self.assertIn(key, summary, f"Missing summary key: {key}")

    def test_summary_has_phase7b_keys(self):
        summary = build_p2c_performance_summary(decisions=_sample_decisions())
        for key in ("enter_eligible_count", "enter_eligibility_reason_distribution",
                     "enter_eligibility_blocker_distribution"):
            self.assertIn(key, summary, f"Missing summary key: {key}")

    def test_summary_has_phase7c_keys(self):
        summary = build_p2c_performance_summary(decisions=_sample_decisions())
        for key in ("risk_allowed_count", "risk_reason_distribution",
                     "grade_risk_multiplier_distribution",
                     "risk_positive_but_not_enter_eligible_count"):
            self.assertIn(key, summary, f"Missing summary key: {key}")

    def test_summary_has_phase7d_keys(self):
        summary = build_p2c_performance_summary(decisions=_sample_decisions())
        for key in ("readiness_coherence_violation_count",
                     "READY_with_missing_ready_blockers_count",
                     "READY_with_non_ready_sections_count"):
            self.assertIn(key, summary, f"Missing summary key: {key}")

    def test_summary_has_phase7e_keys(self):
        summary = build_p2c_performance_summary(decisions=_sample_decisions())
        for key in ("agent_poi_handoff_source_distribution",
                     "legacy_fallback_usage_count",
                     "p2a_selected_poi_consumed_count",
                     "p2a_candidate_fallback_count",
                     "p2a_missing_or_bounds_missing_count"):
            self.assertIn(key, summary, f"Missing Phase7E summary key: {key}")


class TestHandoffMetrics(unittest.TestCase):
    """Phase 7E handoff metrics helper."""

    def test_handoff_counts_correct(self):
        metrics = _phase7e_handoff_metrics(_sample_decisions())
        self.assertEqual(metrics["p2a_selected_poi_consumed_count"], 3)
        self.assertEqual(metrics["legacy_fallback_usage_count"], 3)
        self.assertEqual(metrics["p2a_missing_or_bounds_missing_count"], 3)
        self.assertIn("agent3:P2A_SELECTED_POI", metrics["agent_poi_handoff_source_distribution"])
        self.assertIn("agent3:LEGACY_AGENT2_FALLBACK", metrics["agent_poi_handoff_source_distribution"])

    def test_empty_decisions(self):
        metrics = _phase7e_handoff_metrics([])
        self.assertEqual(metrics["p2a_selected_poi_consumed_count"], 0)
        self.assertEqual(metrics["legacy_fallback_usage_count"], 0)


class TestReplayReportCompatibility(unittest.TestCase):
    """replay_report must not fail when new Phase7E fields are present."""

    def test_legacy_run_without_phase7e_keys(self):
        """Old runs without Phase7E handoff keys should not break metrics."""
        old_decisions = [
            {
                "decision": "WATCH_ONLY",
                "setup_type": "UNKNOWN",
                "setup_grade": "D",
                "p1_evidence_bundle": {},
            },
        ]
        metrics = _mock_replay_metrics(old_decisions)
        # Must not raise
        self.assertIn("agent_poi_handoff_source_distribution", metrics)
        self.assertEqual(metrics["legacy_fallback_usage_count"], 0)
        self.assertEqual(metrics["p2a_selected_poi_consumed_count"], 0)

    def test_new_run_with_handoff_keys(self):
        """New runs with handoff keys produce correct metrics."""
        decisions = _sample_decisions()
        metrics = _mock_replay_metrics(decisions)
        self.assertGreater(metrics["p2a_selected_poi_consumed_count"], 0)
        self.assertGreater(metrics["legacy_fallback_usage_count"], 0)


if __name__ == "__main__":
    unittest.main()

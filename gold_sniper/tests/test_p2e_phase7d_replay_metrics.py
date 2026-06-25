"""P2-E Phase 7D — Replay metrics readiness coherence tests."""

import unittest
from collections import Counter, defaultdict

from gold_sniper.validation.p2c_performance_summary import build_p2c_performance_summary


def _mock_phase7d_metrics(decisions):
    """Simulate replay_metrics output for Phase7D readiness coherence."""
    coherence_violations = 0
    ready_with_missing = 0
    ready_with_non_ready = 0
    missing_ready_blockers = Counter()
    non_ready_sections = Counter()
    readiness_by_setup = defaultdict(Counter)
    readiness_by_grade = defaultdict(Counter)

    for item in decisions:
        readiness_state = str(item.get("readiness_state") or "UNKNOWN")
        setup_type = str(item.get("setup_type") or "UNKNOWN")
        grade = str(item.get("setup_grade") or "UNKNOWN")
        blockers = item.get("readiness_missing_ready_blockers", []) or []
        sections = item.get("readiness_non_ready_sections", {}) or {}

        readiness_by_setup[setup_type][readiness_state] += 1
        readiness_by_grade[grade][readiness_state] += 1
        for blocker in blockers:
            missing_ready_blockers[str(blocker)] += 1
        # Phase 7E micro-correctif: news:WATCH_ONLY is NOT a non_ready_section
        for section, state in sections.items():
            if section == "news" and state == "WATCH_ONLY":
                continue  # _core_ready() explicitly allows this
            non_ready_sections[f"{section}:{state}"] += 1
        # Count only real non_ready sections (excluding news:WATCH_ONLY)
        real_non_ready = {
            s: st for s, st in sections.items()
            if not (s == "news" and st == "WATCH_ONLY")
        }
        if readiness_state == "READY" and blockers:
            ready_with_missing += 1
        if readiness_state == "READY" and real_non_ready:
            ready_with_non_ready += 1

    coherence_violations = ready_with_missing + ready_with_non_ready

    return {
        "total_decisions": len(decisions),
        "readiness_coherence_violation_count": coherence_violations,
        "READY_with_missing_ready_blockers_count": ready_with_missing,
        "READY_with_non_ready_sections_count": ready_with_non_ready,
        "readiness_missing_ready_blocker_distribution": dict(missing_ready_blockers.most_common(25)),
        "readiness_non_ready_section_distribution": dict(non_ready_sections.most_common(25)),
        "readiness_state_by_setup_type": {
            st: dict(buckets.most_common()) for st, buckets in readiness_by_setup.items()
        },
        "readiness_state_by_grade": {
            g: dict(buckets.most_common()) for g, buckets in readiness_by_grade.items()
        },
    }


class TestPhase7dReplayMetrics(unittest.TestCase):

    def _sample_decisions(self):
        return [
            # Fully READY, no blockers
            {
                "readiness_state": "READY", "setup_type": "CONTINUATION_LIGHT", "setup_grade": "B",
                "readiness_missing_ready_blockers": [],
                "readiness_non_ready_sections": {},
            },
            # READY but has missing blocker (should be a violation)
            {
                "readiness_state": "READY", "setup_type": "CONTINUATION_STRICT", "setup_grade": "C",
                "readiness_missing_ready_blockers": ["SESSION_CONTEXT_MISSING"],
                "readiness_non_ready_sections": {"session": "UNAVAILABLE"},
            },
            # Not READY — no violation
            {
                "readiness_state": "WATCH_ONLY", "setup_type": "POI_REACTION", "setup_grade": "D",
                "readiness_missing_ready_blockers": ["LIQUIDITY_MISSING", "MICRO_MISSING"],
                "readiness_non_ready_sections": {"liquidity": "UNAVAILABLE", "micro": "UNAVAILABLE"},
            },
            # READY with non-ready sections
            {
                "readiness_state": "READY", "setup_type": "REVERSAL_STRICT", "setup_grade": "A",
                "readiness_missing_ready_blockers": [],
                "readiness_non_ready_sections": {"news": "WATCH_ONLY"},
            },
        ]

    def test_metrics_has_phase7d_keys(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7d_metrics(decisions)
        for key in (
            "readiness_coherence_violation_count",
            "READY_with_missing_ready_blockers_count",
            "READY_with_non_ready_sections_count",
            "readiness_missing_ready_blocker_distribution",
            "readiness_non_ready_section_distribution",
            "readiness_state_by_setup_type",
            "readiness_state_by_grade",
        ):
            self.assertIn(key, metrics, f"Missing Phase7D metric: {key}")

    def test_violation_count_includes_ready_with_missing(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7d_metrics(decisions)
        # Decision 1 (index 1): READY + blocker ["SESSION_CONTEXT_MISSING"] → violation
        self.assertEqual(metrics["READY_with_missing_ready_blockers_count"], 1)

    def test_violation_count_includes_ready_with_non_ready_sections(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7d_metrics(decisions)
        # Decision 1: READY + non_ready_sections {"session": "UNAVAILABLE"} → violation
        # Decision 3: READY + {"news": "WATCH_ONLY"} → NOT a violation (news WATCH_ONLY allowed)
        self.assertEqual(metrics["READY_with_non_ready_sections_count"], 1)

    def test_non_ready_no_violation(self):
        """Non-READY decisions with blockers do NOT count as violations."""
        decisions = self._sample_decisions()
        metrics = _mock_phase7d_metrics(decisions)
        # Decision 1: ready_with_missing(1) + ready_with_non_ready(1) = 2 total violations
        self.assertEqual(metrics["readiness_coherence_violation_count"], 2)

    def test_news_watch_only_not_counted_as_non_ready(self):
        """Phase 7E micro-correctif: news:WATCH_ONLY must NOT be a violation."""
        decisions = self._sample_decisions()
        metrics = _mock_phase7d_metrics(decisions)
        sections = metrics["readiness_non_ready_section_distribution"]
        # news:WATCH_ONLY should NOT appear (Decision 3 has it but it's filtered out)
        self.assertNotIn("news:WATCH_ONLY", sections)
        # But session:UNAVAILABLE and liquidity:UNAVAILABLE still count
        self.assertEqual(sections.get("session:UNAVAILABLE"), 1)

    def test_blocker_distribution(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7d_metrics(decisions)
        blockers = metrics["readiness_missing_ready_blocker_distribution"]
        self.assertEqual(blockers.get("SESSION_CONTEXT_MISSING"), 1)
        self.assertEqual(blockers.get("LIQUIDITY_MISSING"), 1)

    def test_non_ready_section_distribution(self):
        decisions = self._sample_decisions()
        metrics = _mock_phase7d_metrics(decisions)
        sections = metrics["readiness_non_ready_section_distribution"]
        self.assertEqual(sections.get("session:UNAVAILABLE"), 1)
        self.assertEqual(sections.get("liquidity:UNAVAILABLE"), 1)

    def test_performance_summary_has_phase7d_keys(self):
        decisions = [
            {
                "decision": "WATCH_ONLY", "setup_type": "POI_REACTION", "setup_grade": "D",
                "readiness_state": "WATCH_ONLY",
                "readiness_missing_ready_blockers": ["LIQUIDITY_MISSING"],
                "readiness_non_ready_sections": {"liquidity": "UNAVAILABLE"},
            },
            {
                "decision": "WATCH_ONLY", "setup_type": "CONTINUATION_LIGHT", "setup_grade": "B",
                "readiness_state": "READY",
                "readiness_missing_ready_blockers": [],
                "readiness_non_ready_sections": {},
            },
        ]
        summary = build_p2c_performance_summary(decisions=decisions)
        for key in (
            "readiness_coherence_violation_count",
            "READY_with_missing_ready_blockers_count",
            "READY_with_non_ready_sections_count",
            "readiness_missing_ready_blocker_distribution",
            "readiness_non_ready_section_distribution",
        ):
            self.assertIn(key, summary, f"Missing Phase7D summary key: {key}")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from gold_sniper.strategy.contracts import (
    EvidenceBundle,
    HardVetoResult,
    SetupGrade,
    SetupType,
    TradeSide,
    VetoSeverity,
)
from gold_sniper.strategy.scorecard import evaluate_scorecard


class TestScorecard(unittest.TestCase):
    def test_empty_bundle_returns_low_score_and_grade_d(self):
        card = evaluate_scorecard(EvidenceBundle())
        self.assertLess(card.score_before_veto, 10.0)
        self.assertEqual(card.grade, SetupGrade.D)

    def test_score_bounded_0_to_100(self):
        bundle = EvidenceBundle(
            context={"htf_aligned": True, "draw_on_liquidity": True, "direction": "SELL"},
            poi={"poi_quality_score": 100.0, "lifecycle_state": "FRESH", "selected_poi": True},
            liquidity={"sweep_rejected": True},
            session={"session_grade": "HIGH"},
            micro={"displacement_present": True, "reclaim_confirmed": True, "retest_confirmed": True},
            news={"news_clear": True},
        )
        card = evaluate_scorecard(bundle)
        self.assertGreaterEqual(card.score_before_veto, 0.0)
        self.assertLessEqual(card.score_before_veto, 100.0)

    def test_score_before_veto_present_even_under_veto(self):
        bundle = EvidenceBundle(news={"high_impact_window": True})
        veto = HardVetoResult(hard_veto=True, veto_code="NEWS")
        card = evaluate_scorecard(bundle, veto)
        self.assertGreaterEqual(card.score_before_veto, 0.0)
        self.assertEqual(card.score_after_veto, 0.0)

    def test_score_after_veto_zero_under_hard_veto(self):
        veto = HardVetoResult(
            hard_veto=True,
            veto_code="MAX_DRAWDOWN_GUARD",
            severity=VetoSeverity.HARD,
        )
        bundle = EvidenceBundle(
            context={"htf_aligned": True, "draw_on_liquidity": True},
            poi={"poi_quality_score": 80.0},
        )
        card = evaluate_scorecard(bundle, veto)
        self.assertGreater(card.score_before_veto, 0.0)
        self.assertEqual(card.score_after_veto, 0.0)

    def test_monotonic_improving_poi_increases_score(self):
        base = EvidenceBundle(
            context={"htf_aligned": True},
            liquidity={"sweep_detected": True},
        )
        weak = EvidenceBundle(
            context={"htf_aligned": True},
            liquidity={"sweep_detected": True},
            poi={"poi_quality_score": 20.0},
        )
        strong = EvidenceBundle(
            context={"htf_aligned": True},
            liquidity={"sweep_detected": True},
            poi={"poi_quality_score": 90.0, "lifecycle_state": "FRESH", "selected_poi": True},
        )
        base_card = evaluate_scorecard(base)
        weak_card = evaluate_scorecard(weak)
        strong_card = evaluate_scorecard(strong)
        self.assertGreaterEqual(strong_card.score_before_veto, weak_card.score_before_veto)
        self.assertGreaterEqual(weak_card.score_before_veto, base_card.score_before_veto)

    def test_monotonic_improving_micro_increases_score(self):
        base = EvidenceBundle(context={"htf_aligned": True})
        with_micro = EvidenceBundle(
            context={"htf_aligned": True},
            micro={"displacement_present": True, "retest_confirmed": True},
        )
        base_card = evaluate_scorecard(base)
        micro_card = evaluate_scorecard(with_micro)
        self.assertGreaterEqual(micro_card.score_before_veto, base_card.score_before_veto)

    def test_full_strong_setup_reaches_a_plus(self):
        bundle = EvidenceBundle(
            context={"htf_aligned": True, "draw_on_liquidity": True, "direction": "SELL"},
            poi={"poi_quality_score": 95.0, "lifecycle_state": "FRESH", "selected_poi": True},
            liquidity={"sweep_rejected": True},
            session={"session_grade": "HIGH"},
            micro={
                "displacement_present": True,
                "reclaim_confirmed": True,
                "retest_confirmed": True,
                "trigger_inside_poi": True,
            },
            news={"news_clear": True},
        )
        card = evaluate_scorecard(bundle)
        self.assertEqual(card.grade, SetupGrade.A_PLUS)
        self.assertGreaterEqual(card.score_before_veto, 85.0)

    def test_partial_setup_grade_b_or_c(self):
        bundle = EvidenceBundle(
            context={"htf_aligned": True, "draw_on_liquidity": True, "direction": "SELL"},
            poi={"poi_quality_score": 65.0, "poi_available": True},
            liquidity={"sweep_detected": True},
            session={"session_grade": "HIGH"},
            micro={"displacement_present": True},
        )
        card = evaluate_scorecard(bundle)
        self.assertIn(card.grade, {SetupGrade.B, SetupGrade.C})

    def test_grade_ordering_aplus_gte_a_gte_b_gte_c_gte_d(self):
        def make_bundle(score: float) -> EvidenceBundle:
            return EvidenceBundle(
                poi={"poi_quality_score": score, "selected_poi": True},
                context={"htf_aligned": True},
                liquidity={"sweep_detected": True},
                session={"trading_allowed": True},
            )

        grades = []
        for s in [95, 80, 65, 50, 20]:
            card = evaluate_scorecard(make_bundle(float(s)))
            grades.append(card.grade)

        grade_rank = {SetupGrade.A_PLUS: 5, SetupGrade.A: 4, SetupGrade.B: 3, SetupGrade.C: 2, SetupGrade.D: 1}
        for i in range(len(grades) - 1):
            self.assertGreaterEqual(
                grade_rank[grades[i]],
                grade_rank[grades[i + 1]],
                f"Grade order broken: {grades[i]} vs {grades[i + 1]}",
            )

    def test_news_medium_impact_in_soft_issues(self):
        bundle = EvidenceBundle(
            context={"htf_aligned": True},
            poi={"poi_quality_score": 80.0, "selected_poi": True},
            liquidity={"sweep_detected": True},
            news={"medium_impact_nearby": True},
        )
        card = evaluate_scorecard(bundle)
        self.assertIn("NEWS_MEDIUM_IMPACT_SOFT_PENALTY", card.soft_issues)

    def test_missing_evidence_detected(self):
        bundle = EvidenceBundle()
        card = evaluate_scorecard(bundle)
        self.assertIn("CONTEXT_MISSING", card.missing_evidence)
        self.assertIn("POI_MISSING", card.missing_evidence)

    def test_failed_stage_counts_as_missing_evidence(self):
        bundle = EvidenceBundle(
            context={"passed": False, "reason": "HTF_CONTEXT_MISSING"},
            poi={"passed": False, "reason": "POI_MISSING"},
            news={"passed": False, "reason": "NEWS_CONTEXT_MISSING"},
        )
        card = evaluate_scorecard(bundle)
        self.assertIn("CONTEXT_MISSING", card.missing_evidence)
        self.assertIn("POI_MISSING", card.missing_evidence)
        self.assertIn("NEWS_CONTEXT_MISSING", card.missing_evidence)
        self.assertEqual(card.grade, SetupGrade.D)


if __name__ == "__main__":
    unittest.main()

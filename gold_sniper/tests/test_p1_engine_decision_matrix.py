from __future__ import annotations

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.strategy.professional_decision_engine import (
    DECISION_ENTER_FULL,
    DECISION_ENTER_REDUCED,
    DECISION_REJECT,
    DECISION_WAIT_FOR_TRIGGER,
    DECISION_WATCH_ONLY,
    GRADE_A_PLUS,
    GRADE_D,
    SHADOW_ONLY,
    ProfessionalDecisionResult,
    evaluate_professional_decision,
)


class TestDecisionMatrix(unittest.TestCase):
    def test_legacy_api_returns_professional_decision_result(self):
        result = evaluate_professional_decision({}, setup_type="UNKNOWN")
        self.assertIsInstance(result, ProfessionalDecisionResult)
        self.assertEqual(result.required_execution_mode, SHADOW_ONLY)

    def test_legacy_api_with_none(self):
        result = evaluate_professional_decision(None)
        self.assertIsInstance(result, ProfessionalDecisionResult)

    def test_hard_veto_news_returns_reject(self):
        evidence = {"news_permission": {"high_impact_window": True, "reason": "NEWS_BLACKOUT_HIGH"}}
        result = evaluate_professional_decision(evidence)
        self.assertEqual(result.decision, DECISION_REJECT)
        self.assertEqual(result.setup_grade, GRADE_D)
        self.assertTrue(result.hard_veto)
        self.assertIsNotNone(result.hard_veto_reason)

    def test_hard_veto_session_tokyo_returns_reject(self):
        evidence = {"session_permission": {"session": "TOKYO"}}
        result = evaluate_professional_decision(evidence)
        self.assertEqual(result.decision, DECISION_REJECT)
        self.assertTrue(result.hard_veto)

    def test_strong_setup_returns_enter_full_or_reduced(self):
        evidence = {
            "news_permission": {"passed": True, "news_clear": True},
            "session_permission": {"passed": True, "session": "LONDON", "trading_allowed": True, "session_grade": "HIGH"},
            "htf_context_placeholder": {"passed": True, "value": {"direction": "SELL"}},
            "dol_placeholder": {"passed": True, "value": {"draw_on_liquidity": True}},
            "poi_placeholder": {
                "reason": "ACCEPT",
                "value": {
                    "score": 90.0,
                    "quality_score": 90.0,
                    "poi_quality_score": 90.0,
                    "lifecycle_state": "FRESH",
                    "selected_poi": True,
                },
            },
            "liquidity_state": {
                "reason": "SUPPORTS",
                "value": {"sweep_rejected": True, "decision": "SUPPORTS_SETUP"},
            },
            "session_premium_ote_gate": {
                "reason": "PASS",
                "value": {"score": 85.0, "in_ote": True, "premium_discount": "DISCOUNT"},
            },
            "micro_confirmation": {
                "reason": "CONFIRMED",
                "value": {
                    "score": 90.0,
                    "displacement_present": True,
                    "reclaim_confirmed": True,
                    "retest_confirmed": True,
                    "trigger_inside_poi": True,
                },
            },
            "risk_placeholder": {"passed": True},
        }
        result = evaluate_professional_decision(evidence, setup_type="REVERSAL_STRICT")
        self.assertIn(result.decision, {DECISION_ENTER_FULL, DECISION_ENTER_REDUCED})
        self.assertEqual(result.required_execution_mode, SHADOW_ONLY)
        self.assertFalse(result.hard_veto)
        self.assertGreater(result.confidence_score, 0.5)

    def test_score_before_veto_and_after_veto_present(self):
        evidence = {
            "news_permission": {"passed": True, "news_clear": True},
            "session_permission": {"passed": True, "session": "LONDON", "session_grade": "HIGH"},
            "htf_context_placeholder": {"passed": True, "value": {"direction": "SELL"}},
            "dol_placeholder": {"passed": True},
            "poi_placeholder": {"reason": "ACCEPT", "value": {"score": 80.0, "poi_quality_score": 80.0, "poi_available": True}},
            "liquidity_state": {"reason": "SUPPORTS", "value": {"sweep_detected": True}},
            "session_premium_ote_gate": {"reason": "PASS", "value": {"score": 70.0, "in_ote": True}},
            "micro_confirmation": {"reason": "CONFIRMED", "value": {"score": 70.0, "displacement_present": True, "retest_confirmed": True}},
            "risk_placeholder": {"passed": True},
        }
        result = evaluate_professional_decision(evidence, setup_type="CONTINUATION_LIGHT")
        self.assertGreater(result.score_before_veto, 0.0)
        self.assertGreaterEqual(result.score_after_veto, 0.0)
        self.assertIsNotNone(result.score_before_veto)
        self.assertIsNotNone(result.score_after_veto)

    def test_risk_plan_present(self):
        evidence = {
            "news_permission": {"passed": True, "news_clear": True},
            "session_permission": {"passed": True, "session": "LONDON", "session_grade": "HIGH"},
            "htf_context_placeholder": {"passed": True, "value": {"direction": "SELL"}},
            "dol_placeholder": {"passed": True},
            "poi_placeholder": {"reason": "ACCEPT", "value": {"score": 90.0, "poi_quality_score": 90.0, "lifecycle_state": "FRESH", "selected_poi": True}},
            "liquidity_state": {"reason": "SUPPORTS", "value": {"sweep_rejected": True}},
            "session_premium_ote_gate": {"reason": "PASS", "value": {"score": 90.0, "in_ote": True, "premium_discount": "DISCOUNT"}},
            "micro_confirmation": {"reason": "CONFIRMED", "value": {"score": 90.0, "displacement_present": True, "reclaim_confirmed": True, "retest_confirmed": True, "trigger_inside_poi": True}},
            "risk_placeholder": {"passed": True},
        }
        result = evaluate_professional_decision(evidence, setup_type="REVERSAL_STRICT")
        self.assertIsInstance(result.risk_plan, dict)
        self.assertIn("allowed", result.risk_plan)

    def test_weak_setup_returns_watch_or_reject(self):
        result = evaluate_professional_decision(
            {"news_permission": {"passed": True}},
            setup_type="UNKNOWN",
        )
        self.assertIn(result.decision, {DECISION_WATCH_ONLY, DECISION_REJECT})

    def test_soft_issues_and_missing_evidence_present_in_result(self):
        evidence = {}
        result = evaluate_professional_decision(evidence)
        self.assertIsInstance(result.soft_issues, list)
        self.assertIsInstance(result.missing_evidence, list)

    def test_constants_preserved(self):
        self.assertEqual(SHADOW_ONLY, "SHADOW_ONLY")
        self.assertEqual(DECISION_ENTER_FULL, "ENTER_FULL")
        self.assertEqual(DECISION_REJECT, "REJECT")
        self.assertEqual(GRADE_A_PLUS, "A_PLUS")
        self.assertEqual(GRADE_D, "D")

    def test_blocked_stage_present_on_veto(self):
        evidence = {"news_permission": {"high_impact_window": True}}
        result = evaluate_professional_decision(evidence)
        self.assertIsNotNone(result.blocked_stage)
        self.assertNotEqual(result.blocked_stage, "NONE")

    def test_veto_code_present(self):
        evidence = {"news_permission": {"high_impact_window": True}}
        result = evaluate_professional_decision(evidence)
        self.assertIsNotNone(result.veto_code)

    def test_replay_invalid_flag(self):
        result = evaluate_professional_decision({})
        self.assertFalse(result.replay_invalid)

    def test_contract_dict_uses_new_engine_path(self):
        from gold_sniper.strategy.contracts import EvidenceBundle, SetupType

        bundle = EvidenceBundle(
            setup_type=SetupType.CONTINUATION_LIGHT,
            context={"htf_aligned": True, "draw_on_liquidity": True, "direction": "SELL"},
            poi={"poi_quality_score": 90.0, "selected_poi": True},
            liquidity={"sweep_detected": True},
            session={"session_grade": "HIGH"},
            micro={"displacement_present": True, "retest_confirmed": True},
            news={"news_clear": True},
        )
        result = evaluate_professional_decision(bundle.to_dict())
        self.assertGreater(result.score_before_veto, 0)
        self.assertIsNotNone(result.score_breakdown.get("component_values"))

    def test_contract_replay_invalid_is_not_hard_veto_reason(self):
        from gold_sniper.strategy.contracts import EvidenceBundle

        bundle = EvidenceBundle(raw={"replay_invalid": True})
        result = evaluate_professional_decision(bundle)
        self.assertEqual(result.decision, DECISION_REJECT)
        self.assertFalse(result.hard_veto)
        self.assertTrue(result.replay_invalid)
        self.assertIsNone(result.hard_veto_reason)
        self.assertEqual(result.veto_code, "REPLAY_DATA_INVALID")

    def test_explain_professional_decision_keeps_veto_reason_without_soft_issues(self):
        from gold_sniper.strategy.decision_explainer import explain_professional_decision

        explanation = explain_professional_decision(
            decision={
                "decision": "REJECT",
                "setup_grade": "D",
                "veto_code": "NEWS_HIGH_IMPACT_WINDOW",
                "blocked_stage": "NEWS",
                "missing_evidence": [],
                "soft_issues": [],
            },
            evidence={},
            hard_veto=None,
            scorecard=None,
            risk_plan=None,
        )
        self.assertEqual(explanation.primary_reason, "NEWS_HIGH_IMPACT_WINDOW")
        self.assertEqual(explanation.pipeline_stage, "NEWS")

    def test_a_plus_score_with_missing_evidence_does_not_enter(self):
        from gold_sniper.strategy.contracts import EvidenceBundle, SetupType

        bundle = EvidenceBundle(
            setup_type=SetupType.CONTINUATION_LIGHT,
            context={
                "htf_aligned": True, "draw_on_liquidity": True,
                "direction": "SELL", "in_ote": True, "premium_discount": "DISCOUNT",
            },
            poi={"poi_quality_score": 95.0, "selected_poi": True, "lifecycle_state": "FRESH"},
            liquidity={"sweep_detected": True},
            session={"session_grade": "HIGH"},
            news={"news_clear": True},
        )

        result = evaluate_professional_decision(bundle)

        self.assertNotIn(result.decision, {DECISION_ENTER_FULL, DECISION_ENTER_REDUCED})
        self.assertTrue(result.missing_evidence)
        self.assertIn("MICRO_MISSING", result.missing_evidence)

    def test_setup_taxonomy_blocks_full_entry_for_session_reversal_medium(self):
        from gold_sniper.strategy.contracts import EvidenceBundle, SetupType

        bundle = EvidenceBundle(
            setup_type=SetupType.SESSION_REVERSAL_MEDIUM,
            context={
                "htf_aligned": True, "draw_on_liquidity": True,
                "direction": "SELL", "in_ote": True, "premium_discount": "DISCOUNT",
            },
            poi={"poi_quality_score": 95.0, "selected_poi": True, "lifecycle_state": "FRESH"},
            liquidity={"sweep_detected": True},
            session={"session_grade": "HIGH"},
            micro={
                "score": 95.0, "displacement_present": True, "reclaim_confirmed": True,
                "retest_confirmed": True, "trigger_inside_poi": True,
            },
            news={"news_clear": True},
            risk={"atr_risk_multiplier": 1.0},
        )

        result = evaluate_professional_decision(bundle)

        self.assertGreaterEqual(result.score_before_veto, 85.0)
        self.assertNotEqual(result.decision, DECISION_ENTER_FULL)

    def test_risk_allocator_respects_setup_max_risk_multiplier(self):
        from gold_sniper.strategy.contracts import EvidenceBundle, SetupType
        from gold_sniper.strategy.risk_allocator import allocate_risk

        bundle = EvidenceBundle(
            setup_type=SetupType.SESSION_REVERSAL_MEDIUM,
            risk={"atr_risk_multiplier": 1.0},
        )

        plan = allocate_risk(
            action="ENTER_REDUCED",
            grade="B",
            evidence=bundle,
            capital=100.0,
        )

        self.assertLessEqual(plan.risk_pct, 0.50)
        self.assertEqual(plan.metadata["setup_max_risk_multiplier"], 0.50)


if __name__ == "__main__":
    unittest.main()

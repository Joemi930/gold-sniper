import unittest

from gold_sniper.strategy.contracts import (
    DecisionAction,
    EvidenceBundle,
    ReadinessResult,
    ReadinessState,
    ScoreCard,
    SetupGrade,
    SetupType,
)
from gold_sniper.strategy.enter_eligibility import (
    LIGHT_REQUIRED_SECTIONS_FOR_ENTER,
    STRICT_REQUIRED_SECTIONS_FOR_ENTER,
    evaluate_enter_eligibility,
    required_sections_for_setup,
)
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.risk_allocator import allocate_risk


def _bundle(setup_type="CONTINUATION_LIGHT", grade_ready=True, **overrides):
    data = {
        "setup_type": setup_type,
        "side": "BUY",
        "context": {"direction": "BUY", "in_ote": True},
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB"},
            "price_bounds": {"low": 2400, "high": 2405},
            "execution_readiness": "READY",
            "poi_quality_score": 80,
        },
        "liquidity": {"readiness_state": "WAITING_TRIGGER"},
        "micro": {"readiness_state": "WAITING_TRIGGER"},
        "news": {"news_clear": True, "readiness_state": "READY"},
        "session": {"trading_allowed": True, "session": "LONDON", "session_grade": "HIGH"},
        "risk": {"passed": True},
        "raw": {"timing": {"readiness_state": "READY", "in_ote": True}},
    }
    if not grade_ready:
        data["poi"] = {}
        data["context"] = {}
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


def _scorecard(grade=SetupGrade.C):
    return ScoreCard(score_before_veto=70, score_after_veto=70, grade=grade)


def _readiness(state=ReadinessState.READY):
    return ReadinessResult(
        state=state,
        suggested_action=DecisionAction.WATCH_ONLY,
        reason="SYNTHETIC",
        section_states={
            "context": "READY",
            "poi": "READY",
            "liquidity": "WAITING_TRIGGER",
            "timing": "READY",
            "micro": "WAITING_TRIGGER",
            "news": "READY",
            "session": "READY",
            "risk": "READY",
        },
    )


class TestLightEnterEligibility(unittest.TestCase):
    def test_continuation_light_uses_light_sections(self):
        self.assertEqual(required_sections_for_setup(SetupType.CONTINUATION_LIGHT), LIGHT_REQUIRED_SECTIONS_FOR_ENTER)

    def test_reversal_light_uses_light_sections(self):
        self.assertEqual(required_sections_for_setup(SetupType.REVERSAL_LIGHT), LIGHT_REQUIRED_SECTIONS_FOR_ENTER)

    def test_strict_uses_strict_sections(self):
        self.assertEqual(required_sections_for_setup(SetupType.SWEEP_REVERSAL), STRICT_REQUIRED_SECTIONS_FOR_ENTER)

    def test_light_without_min_trigger_not_eligible(self):
        bundle = _bundle(context={"direction": "BUY", "in_ote": False}, micro={}, liquidity={}, raw={"timing": {"readiness_state": "READY"}})
        result = evaluate_enter_eligibility(
            bundle=bundle,
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=evaluate_hard_veto(bundle),
        )
        self.assertFalse(result.enter_eligible)
        self.assertIn("LIGHT_SETUP_MIN_TRIGGER_MISSING", result.blockers)

    def test_light_with_min_trigger_sections_ready_grade_c_eligible(self):
        bundle = _bundle()
        result = evaluate_enter_eligibility(
            bundle=bundle,
            scorecard=_scorecard(SetupGrade.C),
            readiness=_readiness(),
            veto=evaluate_hard_veto(bundle),
        )
        self.assertTrue(result.enter_eligible)
        self.assertGreater(float(result.risk_preview.get("risk_pct") or 0.0), 0.0)

    def test_light_with_hard_veto_not_eligible(self):
        bundle = _bundle(session={"session": "TOKYO", "is_hard_block": True})
        result = evaluate_enter_eligibility(
            bundle=bundle,
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=evaluate_hard_veto(bundle),
        )
        self.assertFalse(result.enter_eligible)
        self.assertIn("HARD_VETO_OR_REPLAY_INVALID", result.blockers)

    def test_light_grade_d_not_eligible(self):
        bundle = _bundle()
        result = evaluate_enter_eligibility(
            bundle=bundle,
            scorecard=_scorecard(SetupGrade.D),
            readiness=_readiness(),
            veto=evaluate_hard_veto(bundle),
        )
        self.assertFalse(result.enter_eligible)
        self.assertIn("GRADE_BELOW_C", result.blockers)

    def test_light_enter_eligible_true_gives_risk_preview_positive(self):
        bundle = _bundle()
        result = evaluate_enter_eligibility(
            bundle=bundle,
            scorecard=_scorecard(SetupGrade.C),
            readiness=_readiness(),
            veto=evaluate_hard_veto(bundle),
        )
        self.assertTrue(result.enter_eligible)
        self.assertGreater(float(result.risk_preview.get("risk_multiplier") or 0.0), 0.0)

    def test_risk_final_zero_when_enter_eligible_false(self):
        bundle = _bundle()
        plan = allocate_risk(
            action=DecisionAction.ENTER_REDUCED,
            grade=SetupGrade.C,
            evidence=bundle,
            capital=100,
            enter_eligible=False,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.risk_multiplier, 0.0)


if __name__ == "__main__":
    unittest.main()

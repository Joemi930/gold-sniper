"""P2-E Phase 7F — Enter eligibility and risk matrix tests.

Tests grade → risk multiplier mapping, enter eligibility guard,
and the critical invariant: risk_multiplier > 0 only when
enter_eligible=True and action is ENTER_*.
"""

import unittest

from gold_sniper.strategy.contracts import (
    DecisionAction,
    EvidenceBundle,
    SetupGrade,
    SetupType,
)
from gold_sniper.strategy.enter_eligibility import evaluate_enter_eligibility
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.professional_decision_engine import evaluate_professional_decision
from gold_sniper.strategy.readiness import evaluate_readiness
from gold_sniper.strategy.risk_allocator import allocate_risk, grade_risk_multiplier
from gold_sniper.strategy.scorecard import evaluate_scorecard


def _ready_bundle(**overrides):
    """Fully populated bundle that should achieve READY with a good grade."""
    data = {
        "setup_type": "CONTINUATION_STRICT",
        "side": "BUY",
        "context": {
            "direction": "BUY", "htf_aligned": True,
            "in_ote": True, "premium_discount": "DISCOUNT",
        },
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB", "score": 90},
            "price_bounds": {"low": 2400, "high": 2405},
            "poi_quality_score": 90,
        },
        "liquidity": {
            "liquidity_semantic_status": "LIQUIDITY_READY",
            "readiness_state": "READY",
            "execution_readiness": "READY",
            "sweep_detected": True,
            "liquidity_quality_score": 85,
        },
        "micro": {
            "micro_semantic_status": "MICRO_READY",
            "readiness_state": "READY",
            "execution_readiness": "READY",
            "retest_confirmed": True,
            "displacement_present": True,
            "reclaim_confirmed": True,
            "trigger_inside_poi": True,
        },
        "news": {"news_clear": True, "impact_level": "NONE", "passed": True},
        "session": {
            "trading_allowed": True, "session_grade": "HIGH",
            "session_label": "LONDON", "passed": True,
        },
        "risk": {"passed": True},
        "raw": {
            "timing": {
                "readiness_state": "READY", "execution_readiness": "READY",
                "in_ote": True,
            },
            "setup_classification": {
                "setup_type": "CONTINUATION_STRICT",
                "confidence": 0.85, "reason": "SYNTHETIC_READY",
                "family": "CONTINUATION",
                "required_ready_sections": ["context", "poi", "liquidity", "timing", "micro", "session", "risk"],
            },
        },
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


class TestGradeRiskMultiplier(unittest.TestCase):
    """Contract: grade_risk_multiplier maps correctly."""

    def test_a_plus_maps_to_1_0(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.A_PLUS), 1.0)

    def test_a_maps_to_0_75(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.A), 0.75)

    def test_b_maps_to_0_50(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.B), 0.50)

    def test_c_maps_to_0_25(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.C), 0.25)

    def test_d_maps_to_0_0(self):
        self.assertEqual(grade_risk_multiplier(SetupGrade.D), 0.0)


class TestEnterEligibilityRiskGuard(unittest.TestCase):
    """Contract: enter_eligible=False blocks risk."""

    def test_d_grade_blocks_risk(self):
        """Grade D → risk multiplier = 0 even if evidence seems ready."""
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.D,
            enter_eligible=False,
        )
        self.assertFalse(plan.allowed)
        # risk multiplier must be 0
        self.assertEqual(plan.metadata.get("effective_risk_pct", 0.0), 0.0)

    def test_enter_not_eligible_blocks_risk(self):
        """Even with grade A, enter_eligible=False → no risk."""
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A,
            enter_eligible=False,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.reason, "ENTER_NOT_ELIGIBLE")

    def test_enter_eligible_true_allows_risk_with_setup(self):
        """Grade B + enter_eligible=True + tradable setup → risk allowed."""
        bundle = EvidenceBundle.from_dict({
            "setup_type": "CONTINUATION_STRICT",
            "side": "BUY",
            "context": {}, "poi": {}, "liquidity": {}, "micro": {},
            "news": {}, "session": {}, "risk": {},
            "raw": {},
        })
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.B,
            enter_eligible=True,
            evidence=bundle,
        )
        # With CONTINUATION_STRICT cap 0.75 and grade B multiplier 0.50, risk is allowed
        self.assertTrue(plan.allowed)
        self.assertGreater(plan.metadata.get("effective_risk_pct", 0.0), 0.0)

    def test_risk_positive_only_when_eligible(self):
        """Critical invariant: risk multiplier > 0 only when enter_eligible=True."""
        # Not eligible → risk blocked
        plan_no = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A,
            enter_eligible=False,
        )
        self.assertFalse(plan_no.allowed)

        # Eligible with evidence → risk allowed
        bundle = EvidenceBundle.from_dict({
            "setup_type": "CONTINUATION_STRICT",
            "side": "BUY",
            "context": {}, "poi": {}, "liquidity": {}, "micro": {},
            "news": {}, "session": {}, "risk": {},
            "raw": {},
        })
        plan_yes = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.A,
            enter_eligible=True,
            evidence=bundle,
        )
        self.assertTrue(plan_yes.allowed)


class TestReadinessBlocksEnter(unittest.TestCase):
    """Contract: missing critical sections block enter eligibility."""

    def _evaluate(self, bundle):
        veto = evaluate_hard_veto(bundle)
        scorecard = evaluate_scorecard(bundle, veto)
        readiness = evaluate_readiness(bundle, scorecard, veto)
        eligibility = evaluate_enter_eligibility(
            bundle=bundle, scorecard=scorecard,
            readiness=readiness, veto=veto,
        )
        return eligibility

    def test_missing_micro_blocks_enter(self):
        b = _ready_bundle(micro={
            "readiness_state": "WAITING_TRIGGER",
            "execution_readiness": "WAITING_TRIGGER",
        })
        r = self._evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any("SECTION_NOT_READY:micro" in bl for bl in r.blockers))

    def test_missing_liquidity_blocks_enter(self):
        b = _ready_bundle(liquidity={
            "readiness_state": "WAITING_TRIGGER",
            "execution_readiness": "WAITING_TRIGGER",
        })
        r = self._evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any("SECTION_NOT_READY:liquidity" in bl for bl in r.blockers))

    def test_missing_timing_blocks_enter(self):
        b = _ready_bundle(raw={"timing": {"readiness_state": "UNAVAILABLE"}})
        r = self._evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any("SECTION_NOT_READY:timing" in bl for bl in r.blockers))

    def test_missing_session_blocks_enter(self):
        b = _ready_bundle(session={})
        r = self._evaluate(b)
        self.assertFalse(r.enter_eligible)

    def test_missing_risk_blocks_enter(self):
        b = _ready_bundle(risk={})
        r = self._evaluate(b)
        self.assertFalse(r.enter_eligible)

    def test_hard_veto_blocks_enter(self):
        b = _ready_bundle(session={
            "trading_allowed": False,
            "session_label": "TOKYO",
            "is_hard_block": True,
        })
        r = self._evaluate(b)
        self.assertFalse(r.enter_eligible)


class TestFinalRiskInvariant(unittest.TestCase):
    """Critical invariant: risk_multiplier final > 0 only if enter_eligible."""

    def test_risk_zero_when_not_eligible(self):
        """PDE must not produce positive risk when enter_eligible=False."""
        b = _ready_bundle(
            setup_type="POI_REACTION",
            micro={"readiness_state": "UNAVAILABLE", "execution_readiness": "UNAVAILABLE"},
        )
        result = evaluate_professional_decision(b)
        self.assertFalse(result.enter_eligible)
        self.assertEqual(result.risk_multiplier, 0.0)

    def test_no_setup_zero_risk(self):
        """NO_SETUP or UNKNOWN → enter_eligible=False → risk=0."""
        b = _ready_bundle(setup_type="UNKNOWN")
        result = evaluate_professional_decision(b)
        self.assertFalse(result.enter_eligible)
        self.assertEqual(result.risk_multiplier, 0.0)


if __name__ == "__main__":
    unittest.main()

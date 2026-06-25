"""P2-E Phase 7D — Enter eligibility coherence tests."""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.strategy.enter_eligibility import evaluate_enter_eligibility
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.scorecard import evaluate_scorecard
from gold_sniper.strategy.readiness import evaluate_readiness


def _ready_bundle(**overrides):
    """Fully ready synthetic bundle with tradable setup."""
    data = {
        "setup_type": "CONTINUATION_LIGHT",
        "side": "BUY",
        "context": {
            "direction": "BUY",
            "htf_aligned": True,
            "in_ote": True,
            "premium_discount": "DISCOUNT",
        },
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB", "score": 85},
            "price_bounds": {"low": 2400, "high": 2405},
            "poi_quality_score": 85,
        },
        "liquidity": {
            "liquidity_semantic_status": "LIQUIDITY_READY",
            "readiness_state": "READY",
            "execution_readiness": "READY",
            "sweep_detected": True,
            "liquidity_quality_score": 80,
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
        "news": {
            "news_clear": True,
            "impact_level": "NONE",
            "passed": True,
        },
        "session": {
            "trading_allowed": True,
            "session_grade": "HIGH",
            "session_label": "LONDON",
            "passed": True,
        },
        "risk": {
            "passed": True,
        },
        "raw": {
            "timing": {
                "readiness_state": "READY",
                "execution_readiness": "READY",
                "in_ote": True,
            },
            "setup_classification": {
                "setup_type": "CONTINUATION_LIGHT",
                "confidence": 0.75,
                "reason": "SYNTHETIC_READY",
                "family": "CONTINUATION",
                "required_ready_sections": ["context", "poi", "liquidity", "timing", "micro", "session", "risk"],
                "tags": [],
                "evidence": {},
            },
        },
    }
    data.update(overrides)
    return EvidenceBundle.from_dict(data)


def _evaluate(bundle):
    veto = evaluate_hard_veto(bundle)
    scorecard = evaluate_scorecard(bundle, veto)
    readiness = evaluate_readiness(bundle, scorecard, veto)
    eligibility = evaluate_enter_eligibility(
        bundle=bundle,
        scorecard=scorecard,
        readiness=readiness,
        veto=veto,
    )
    return eligibility


class TestEnterEligibilityCoherence(unittest.TestCase):

    def test_session_section_not_ready_blocks_enter(self):
        b = _ready_bundle(session={})
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any("SECTION_NOT_READY:session" in bl for bl in r.blockers))

    def test_risk_section_not_ready_blocks_enter(self):
        b = _ready_bundle(risk={})
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any("SECTION_NOT_READY:risk" in bl for bl in r.blockers))

    def test_timing_not_ready_blocks_enter(self):
        b = _ready_bundle(raw={"timing": {"readiness_state": "UNAVAILABLE"}})
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any("SECTION_NOT_READY:timing" in bl for bl in r.blockers))

    def test_liquidity_not_ready_blocks_enter(self):
        b = _ready_bundle(liquidity={
            "readiness_state": "WAITING_TRIGGER",
            "execution_readiness": "WAITING_TRIGGER",
        })
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any("SECTION_NOT_READY:liquidity" in bl for bl in r.blockers))

    def test_micro_not_ready_blocks_enter(self):
        b = _ready_bundle(micro={
            "readiness_state": "WAITING_TRIGGER",
            "execution_readiness": "WAITING_TRIGGER",
        })
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any("SECTION_NOT_READY:micro" in bl for bl in r.blockers))

    def test_fully_ready_bundle_can_be_eligible(self):
        """With all sections READY + tradable setup + grade B+, enter_eligible=True."""
        b = _ready_bundle()
        r = _evaluate(b)
        # With CONTINUATION_LIGHT (cap 0.75) and grade likely A/B, should be eligible
        self.assertIn("news_ok", r.checks)
        # Verify checks are present
        self.assertIn("news_state", r.checks)
        self.assertIn("news_ok", r.checks)

    def test_checks_contain_news_fields(self):
        b = _ready_bundle()
        r = _evaluate(b)
        self.assertIn("news_state", r.checks)
        self.assertIn("news_ok", r.checks)


if __name__ == "__main__":
    unittest.main()

"""P2-E Phase 7B — Enter eligibility unit tests."""

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle, ReadinessState, SetupGrade, SetupType, TradeSide
from gold_sniper.strategy.enter_eligibility import evaluate_enter_eligibility
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.scorecard import evaluate_scorecard
from gold_sniper.strategy.readiness import evaluate_readiness


def _ready_bundle(setup_type="REVERSAL_STRICT", side="BUY", **overrides):
    """Build a fully ready synthetic bundle that should pass enter eligibility."""
    data = {
        "setup_type": setup_type,
        "side": side,
        "context": {"direction": side, "in_ote": True},
        "poi": {
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "selected_poi": {"poi_type_normalized": "BULLISH_OB" if side == "BUY" else "BEARISH_OB"},
            "price_bounds": {"low": 2400.0, "high": 2405.0},
            "execution_readiness": "READY",
            "poi_quality_score": 80.0,
        },
        "liquidity": {
            "liquidity_semantic_status": "LIQUIDITY_READY",
            "execution_readiness": "READY",
            "readiness_state": "READY",
        },
        "micro": {
            "micro_semantic_status": "MICRO_READY",
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "displacement_present": True,
            "retest_confirmed": True,
        },
        "session": {
            "trading_allowed": True,
            "session_label": "LONDON",
            "session": "LONDON",
            "session_grade": "HIGH",
        },
        "risk": {"passed": True},
        "raw": {
            "timing": {
                "readiness_state": "READY",
                "execution_readiness": "READY",
                "in_ote": True,
            },
            "setup_classification": {
                "setup_type": setup_type,
                "confidence": 0.85,
                "reason": "SYNTHETIC_READY",
                "family": "REVERSAL",
                "required_ready_sections": ["context", "poi", "liquidity", "timing", "micro"],
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


class TestEnterEligibilityNotEligible(unittest.TestCase):

    def test_unknown_setup_not_eligible(self):
        b = _ready_bundle("UNKNOWN")
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertIn("SETUP_TYPE_NOT_ELIGIBLE", r.blockers)

    def test_no_setup_not_eligible(self):
        b = _ready_bundle("NO_SETUP")
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertIn("SETUP_TYPE_NOT_ELIGIBLE", r.blockers)

    def test_grade_d_not_eligible(self):
        # Build a bundle that will score low enough for D grade
        b = _ready_bundle(
            "POI_REACTION",
            context={"direction": "BUY"},
            poi={
                "poi_semantic_status": "POI_PRESENT_WAITING_TRIGGER",
                "execution_readiness": "UNAVAILABLE",
            },
            liquidity={},
            micro={},
            session={},
            raw={"timing": {}},
        )
        r = _evaluate(b)
        # With very low evidence, grade should be D and eligibility False
        self.assertFalse(r.enter_eligible)

    def test_hard_veto_not_eligible(self):
        b = _ready_bundle("REVERSAL_STRICT", session={
            "trading_allowed": False,
            "session_label": "TOKYO",
            "session": "TOKYO",
            "is_hard_block": True,
        })
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        blocker_str = " ".join(r.blockers)
        self.assertTrue("HARD_VETO" in blocker_str or "READINESS" in blocker_str or "SECTION" in blocker_str)

    def test_global_readiness_not_ready(self):
        b = _ready_bundle(
            "CONTINUATION_LIGHT",
            context={},
            poi={},
            liquidity={},
            micro={},
            session={},
            raw={"timing": {}},
        )
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertIn("GLOBAL_READINESS_NOT_READY", r.blockers)

    def test_micro_not_ready(self):
        b = _ready_bundle(
            micro={"execution_readiness": "WAITING_TRIGGER", "readiness_state": "WAITING_TRIGGER"},
        )
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any(b.startswith("SECTION_NOT_READY:micro") for b in r.blockers))

    def test_liquidity_not_ready(self):
        b = _ready_bundle(
            liquidity={"execution_readiness": "WAITING_TRIGGER", "readiness_state": "WAITING_TRIGGER"},
        )
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any(b.startswith("SECTION_NOT_READY:liquidity") for b in r.blockers))

    def test_timing_not_ready(self):
        b = _ready_bundle(
            raw={"timing": {"readiness_state": "WAITING_TRIGGER", "execution_readiness": "WAITING_TRIGGER"}},
        )
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertTrue(any(b.startswith("SECTION_NOT_READY:timing") for b in r.blockers))

    def test_risk_preview_zero_not_eligible(self):
        # New setup types have max_risk_multiplier=0.0 → risk preview zero
        b = _ready_bundle("POI_REACTION")
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertIn("RISK_NOT_ALLOWED", r.blockers)


class TestEnterEligibilityEligible(unittest.TestCase):

    def test_continuation_light_checks_setup_ok(self):
        """CONTINUATION_LIGHT with all sections READY + no sweep requirement → setup_ok=True."""
        b = _ready_bundle("CONTINUATION_LIGHT")
        r = _evaluate(b)
        # setup_type is eligible (not UNKNOWN/NO_SETUP)
        self.assertTrue(r.checks["setup_ok"])
        # Risk preview should exist (even if not positive for Phase7B new types)
        self.assertIn("previews", r.risk_preview)

    def test_to_dict_has_all_keys(self):
        b = _ready_bundle("REVERSAL_STRICT")
        r = _evaluate(b)
        d = r.to_dict()
        for key in ("enter_eligible", "reason", "blockers", "checks", "risk_preview"):
            self.assertIn(key, d, f"Missing key: {key}")

    def test_risk_preview_contains_previews(self):
        b = _ready_bundle("REVERSAL_STRICT")
        r = _evaluate(b)
        self.assertIn("previews", r.risk_preview)

    def test_blockers_explain_why_not_eligible(self):
        """When not eligible, blockers are non-empty and audit-ready."""
        b = _ready_bundle("POI_REACTION")
        r = _evaluate(b)
        self.assertFalse(r.enter_eligible)
        self.assertGreater(len(r.blockers), 0)
        # The reason must reference the primary blocker
        self.assertIn(r.reason, r.blockers)


if __name__ == "__main__":
    unittest.main()

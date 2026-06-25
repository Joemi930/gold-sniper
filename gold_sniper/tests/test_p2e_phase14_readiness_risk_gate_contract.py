import unittest

from gold_sniper.strategy.readiness_risk_gate_contract import (
    GateBlockerCode,
    evaluate_readiness_risk_gate,
)


def _decision(**overrides):
    payload = {
        "decision": "WATCH_ONLY",
        "setup_type": "SWEEP_REVERSAL",
        "setup_grade": "C",
        "poi_micro_synergy": True,
        "readiness_state": "READY",
        "readiness_reason": "CORE_EVIDENCE_READY",
        "readiness_by_section": {
            "context": "READY",
            "poi": "READY",
            "liquidity": "READY",
            "timing": "READY",
            "micro": "READY",
            "news": "READY",
            "session": "READY",
            "risk": "READY",
        },
        "enter_eligible": True,
        "enter_eligibility_blockers": [],
        "risk_allowed": True,
        "risk_reason": "SHADOW_RISK_ALLOCATED",
        "risk_multiplier": 0.5,
        "risk_preview": {
            "allowed": True,
            "risk_pct": 0.25,
            "risk_multiplier": 0.5,
            "reason": "SHADOW_RISK_ALLOCATED",
            "metadata": {"setup_max_risk_multiplier": 0.75},
        },
        "setup_candidates": [{"candidate_type": "SWEEP_REVERSAL", "confidence": 0.9}],
        "best_setup_candidate": {"candidate_type": "SWEEP_REVERSAL", "confidence": 0.9},
    }
    payload.update(overrides)
    return payload


class TestP2EPhase14ReadinessRiskGateContract(unittest.TestCase):
    def test_synergy_true_poi_reaction_is_not_tradable(self):
        result = evaluate_readiness_risk_gate(_decision(
            setup_type="POI_REACTION",
            setup_candidates=[{"candidate_type": "POI_REACTION"}],
            best_setup_candidate={"candidate_type": "POI_REACTION"},
            risk_allowed=False,
            risk_multiplier=0.0,
            risk_preview={"metadata": {"setup_max_risk_multiplier": 0.0}, "reason": "ZERO_RISK_AFTER_MULTIPLIERS"},
        ))
        self.assertEqual(result.primary_blocker, GateBlockerCode.SETUP_TYPE_POI_REACTION_NOT_TRADABLE.value)

    def test_synergy_true_no_setup_candidate(self):
        result = evaluate_readiness_risk_gate(_decision(setup_candidates=[], best_setup_candidate={}))
        self.assertEqual(result.primary_blocker, GateBlockerCode.NO_SETUP_CANDIDATE.value)

    def test_synergy_true_liquidity_non_ready(self):
        result = evaluate_readiness_risk_gate(_decision(
            readiness_by_section={"liquidity": "WAITING_TRIGGER", "poi": "READY", "micro": "READY"},
        ))
        self.assertIn(GateBlockerCode.LIQUIDITY_NOT_READY.value, result.blockers)

    def test_synergy_true_timing_non_ready(self):
        result = evaluate_readiness_risk_gate(_decision(
            readiness_by_section={"timing": "WAITING_TRIGGER", "poi": "READY", "micro": "READY"},
        ))
        self.assertIn(GateBlockerCode.TIMING_NOT_READY.value, result.blockers)

    def test_synergy_true_enter_eligible_false(self):
        result = evaluate_readiness_risk_gate(_decision(enter_eligible=False, enter_eligibility_blockers=["GLOBAL_READINESS_NOT_READY"]))
        self.assertIn(GateBlockerCode.ENTER_ELIGIBILITY_FALSE.value, result.blockers)

    def test_synergy_true_risk_multiplier_zero(self):
        result = evaluate_readiness_risk_gate(_decision(risk_allowed=False, risk_multiplier=0.0))
        self.assertIn(GateBlockerCode.RISK_MULTIPLIER_ZERO.value, result.blockers)

    def test_synergy_true_risk_not_allowed(self):
        result = evaluate_readiness_risk_gate(_decision(risk_allowed=False, risk_multiplier=0.0))
        self.assertIn(GateBlockerCode.RISK_NOT_ALLOWED.value, result.blockers)

    def test_everything_ready_risk_positive_has_no_primary_blocker(self):
        result = evaluate_readiness_risk_gate(_decision(decision="ENTER_FULL"))
        self.assertEqual(result.primary_blocker, GateBlockerCode.NONE.value)


if __name__ == "__main__":
    unittest.main()

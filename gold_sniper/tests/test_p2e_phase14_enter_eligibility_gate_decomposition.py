import unittest
from types import SimpleNamespace

from gold_sniper.strategy.contracts import EvidenceBundle, ReadinessState, SetupGrade, SetupType
from gold_sniper.strategy.enter_eligibility import evaluate_enter_eligibility
from gold_sniper.strategy.readiness_risk_gate_contract import evaluate_readiness_risk_gate


def _bundle(setup_type=SetupType.SWEEP_REVERSAL, *, synergy=True, liquidity_ready=True):
    return EvidenceBundle(
        setup_type=setup_type,
        context={"direction": "BUY", "htf_aligned": True},
        poi={
            "selected_poi_present": True,
            "price_bounds": {"low": 100.0, "high": 110.0},
            "poi_micro_synergy_enabled": synergy,
            "poi_micro_synergy": {"synergy": synergy, "micro_inside_poi": True},
        },
        liquidity={
            "sweep_detected": True,
            "readiness_state": "READY" if liquidity_ready else "WAITING_TRIGGER",
        },
        micro={
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": True,
            "price_in_agent2_poi": True,
            "candles_1m_count": 20,
        },
        session={"trading_allowed": True, "readiness_state": "READY"},
        risk={"max_daily_loss_hit": False, "max_weekly_loss_hit": False, "max_drawdown_hit": False, "kill_switch": False},
        raw={"timing": {"readiness_state": "READY"}},
    )


def _readiness(*, liquidity_ready=True):
    return SimpleNamespace(
        state=ReadinessState.READY if liquidity_ready else ReadinessState.WATCH_ONLY,
        section_states={
            "context": "READY",
            "poi": "READY",
            "liquidity": "READY" if liquidity_ready else "WAITING_TRIGGER",
            "timing": "READY",
            "micro": "READY",
            "session": "READY",
            "risk": "READY",
            "news": "READY",
        },
    )


def _scorecard():
    return SimpleNamespace(grade=SetupGrade.C)


def _veto():
    return SimpleNamespace(hard_veto=False, replay_invalid=False)


class TestP2EPhase14EnterEligibilityGateDecomposition(unittest.TestCase):
    def test_poi_reaction_synergy_true_remains_non_tradable_with_explicit_blocker(self):
        result = evaluate_enter_eligibility(
            bundle=_bundle(SetupType.POI_REACTION, synergy=True),
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=_veto(),
        )
        self.assertFalse(result.enter_eligible)
        self.assertIn("SETUP_TYPE_POI_REACTION_NOT_TRADABLE", result.blockers)

    def test_sweep_reversal_synergy_true_all_ready_can_be_enter_eligible(self):
        result = evaluate_enter_eligibility(
            bundle=_bundle(SetupType.SWEEP_REVERSAL, synergy=True),
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=_veto(),
        )
        self.assertTrue(result.enter_eligible, result.blockers)

    def test_sweep_reversal_synergy_true_liquidity_missing_stays_blocked(self):
        result = evaluate_enter_eligibility(
            bundle=_bundle(SetupType.SWEEP_REVERSAL, synergy=True, liquidity_ready=False),
            scorecard=_scorecard(),
            readiness=_readiness(liquidity_ready=False),
            veto=_veto(),
        )
        self.assertFalse(result.enter_eligible)
        self.assertIn("GLOBAL_READINESS_NOT_READY", result.blockers)

    def test_tradable_candidate_detected_but_setup_type_not_used_is_diagnosed(self):
        gate = evaluate_readiness_risk_gate({
            "decision": "WATCH_ONLY",
            "setup_type": "POI_REACTION",
            "setup_grade": "C",
            "poi_micro_synergy": True,
            "setup_candidates": [{"candidate_type": "SWEEP_REVERSAL", "confidence": 0.9}],
            "best_setup_candidate": {"candidate_type": "SWEEP_REVERSAL", "confidence": 0.9},
            "enter_eligible": False,
            "risk_multiplier": 0.0,
            "risk_allowed": False,
        })
        self.assertTrue(gate.has_tradable_setup_candidate)
        self.assertEqual(gate.primary_blocker, "SETUP_TYPE_POI_REACTION_NOT_TRADABLE")


if __name__ == "__main__":
    unittest.main()

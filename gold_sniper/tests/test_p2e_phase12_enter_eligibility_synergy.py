import unittest
from types import SimpleNamespace

from gold_sniper.strategy.contracts import EvidenceBundle, ReadinessState, SetupGrade, SetupType
from gold_sniper.strategy.enter_eligibility import evaluate_enter_eligibility


def _bundle(*, synergy=True, outside=False, too_weak=False):
    return EvidenceBundle(
        setup_type=SetupType.SWEEP_REVERSAL,
        context={"direction": "BUY", "htf_aligned": True},
        poi={
            "selected_poi_present": True,
            "selected_poi": {"price_bounds": {"low": 100.0, "high": 110.0}, "score": 55.0},
            "price_bounds": {"low": 100.0, "high": 110.0},
            "poi_quality_score": 0.0 if too_weak else 55.0,
            "poi_micro_synergy_enabled": synergy,
            "poi_micro_synergy": {
                "synergy": synergy,
                "micro_inside_poi": not outside,
                "micro_outside_poi": outside,
                "reason": "TEST" if synergy else "POI_QUALITY_ZERO" if too_weak else "MICRO_OUTSIDE_POI",
            },
        },
        liquidity={"sweep_detected": True, "readiness_state": "READY"},
        micro={
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": not outside,
            "price_in_agent2_poi": not outside,
            "trigger_outside_poi": outside,
            "candles_1m_count": 20,
        },
        session={"trading_allowed": True, "readiness_state": "READY"},
        risk={"max_daily_loss_hit": False, "max_weekly_loss_hit": False, "max_drawdown_hit": False, "kill_switch": False},
        raw={"timing": {"readiness_state": "READY"}},
    )


def _readiness():
    return SimpleNamespace(
        state=ReadinessState.READY,
        section_states={
            "context": "READY",
            "poi": "READY",
            "liquidity": "READY",
            "timing": "READY",
            "micro": "READY",
            "session": "READY",
            "risk": "READY",
            "news": "READY",
        },
    )


def _scorecard():
    return SimpleNamespace(grade=SetupGrade.C)


def _veto(*, hard=False):
    return SimpleNamespace(hard_veto=hard, replay_invalid=False)


class TestP2EPhase12EnterEligibilitySynergy(unittest.TestCase):
    def test_sweep_reversal_ready_with_synergy_can_be_eligible(self):
        result = evaluate_enter_eligibility(
            bundle=_bundle(synergy=True),
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=_veto(),
        )
        self.assertTrue(result.enter_eligible, result.blockers)

    def test_same_without_synergy_is_not_eligible(self):
        result = evaluate_enter_eligibility(
            bundle=_bundle(synergy=False),
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=_veto(),
        )
        self.assertFalse(result.enter_eligible)
        self.assertIn("POI_MICRO_SYNERGY_MISSING", result.blockers)

    def test_micro_outside_is_not_eligible(self):
        result = evaluate_enter_eligibility(
            bundle=_bundle(synergy=False, outside=True),
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=_veto(),
        )
        self.assertFalse(result.enter_eligible)

    def test_poi_too_weak_is_not_eligible(self):
        result = evaluate_enter_eligibility(
            bundle=_bundle(synergy=False, too_weak=True),
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=_veto(),
        )
        self.assertFalse(result.enter_eligible)

    def test_hard_veto_remains_blocking(self):
        result = evaluate_enter_eligibility(
            bundle=_bundle(synergy=True),
            scorecard=_scorecard(),
            readiness=_readiness(),
            veto=_veto(hard=True),
        )
        self.assertFalse(result.enter_eligible)
        self.assertIn("HARD_VETO_OR_REPLAY_INVALID", result.blockers)


if __name__ == "__main__":
    unittest.main()

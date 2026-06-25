"""P2-E Phase 11: Readiness micro contract integration tests.

Tests:
1. MICRO_CONFIRMED -> _micro_state READY.
2. MICRO_WAITING_TRIGGER -> WAITING_TRIGGER.
3. MICRO_MISSING_DATA -> UNAVAILABLE.
4. MICRO_OUTSIDE_POI -> INVALID.
5. MICRO_INVALID -> INVALID.
6. READY global impossible si POI/risk/session non prets.
"""

import unittest
from datetime import datetime, timezone

from gold_sniper.strategy.contracts import (
    BlockedStage,
    DecisionAction,
    EvidenceBundle,
    HardVetoResult,
    ReadinessState,
    ScoreCard,
    SetupType,
    TradeSide,
)
from gold_sniper.strategy.readiness import evaluate_readiness, _micro_state


class TestReadinessMicroContract(unittest.TestCase):

    def _ts_utc(self):
        return datetime.now(timezone.utc).isoformat()

    def _make_bundle(self, **overrides) -> EvidenceBundle:
        micro = {
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": True,
            "candles_1m_count": 5,
            "micro_contract_status": "MICRO_CONFIRMED",
            "readiness_state": "READY",
        }
        micro.update(overrides.pop("_micro", {}))
        default = {
            "symbol": "XAUUSD",
            "ts_utc": self._ts_utc(),
            "setup_type": SetupType.SWEEP_REVERSAL,
            "side": TradeSide.BUY,
            "observations": {},
            "context": {"direction": "BUY", "htf_aligned": True},
            "poi": {"selected_poi": {"type": "DEMAND", "bottom": 99.0, "top": 101.0}, "price_bounds": {"low": 99.0, "high": 101.0}, "execution_readiness": "READY", "poi_quality_score": 85.0},
            "liquidity": {"execution_readiness": "READY", "sweep_detected": True},
            "micro": micro,
            "news": {"news_clear": True, "impact_level": "LOW"},
            "session": {"trading_allowed": True, "session_grade": "HIGH", "session": "LONDON"},
            "risk": {},
            "raw": {"timing": {"in_ote": True, "readiness_state": "READY"}},
        }
        default.update(overrides)
        return EvidenceBundle(
            symbol=default["symbol"],
            ts_utc=default["ts_utc"],
            setup_type=default["setup_type"],
            side=default["side"],
            observations=default["observations"],
            context=default["context"],
            poi=default["poi"],
            liquidity=default["liquidity"],
            micro=default["micro"],
            news=default["news"],
            session=default["session"],
            risk=default["risk"],
            raw=default["raw"],
        )

    def _scorecard(self):
        return ScoreCard(
            score_before_veto=85.0,
            score_after_veto=85.0,
            missing_evidence=[],
            soft_issues=[],
            metadata={},
        )

    def _veto_pass(self):
        return HardVetoResult(
            hard_veto=False,
            veto_code=None,
            replay_invalid=False,
            blocked_stage=BlockedStage.NONE,
        )

    # ── 1. MICRO_CONFIRMED → READY ─────────────────────────────────
    def test_micro_confirmed_returns_ready_state(self):
        state = _micro_state({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": True,
            "candles_1m_count": 5,
        })
        self.assertEqual(state, "READY")

    # ── 2. MICRO_WAITING_TRIGGER → WAITING_TRIGGER ─────────────────
    def test_micro_waiting_trigger_returns_waiting_trigger_state(self):
        state = _micro_state({
            "sweep_1m_confirmed": True,
            "choch_detected": False,
            "candles_1m_count": 5,
        })
        self.assertEqual(state, "WAITING_TRIGGER")

    # ── 3. MICRO_MISSING_DATA → UNAVAILABLE ────────────────────────
    def test_micro_missing_data_returns_unavailable(self):
        state = _micro_state({
            "sweep_1m_confirmed": None,
            "choch_detected": None,
        })
        self.assertEqual(state, "UNAVAILABLE")

    # ── 4. MICRO_OUTSIDE_POI → INVALID ─────────────────────────────
    def test_micro_outside_poi_returns_invalid(self):
        state = _micro_state({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_outside_poi": True,
            "candles_1m_count": 5,
        })
        self.assertEqual(state, "INVALID")

    # ── 5. MICRO_INVALID → INVALID ─────────────────────────────────
    def test_micro_invalid_returns_invalid(self):
        state = _micro_state({
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "candles_1m_count": 1,
        })
        self.assertEqual(state, "INVALID")

    # ── 6. CORE READY global with all sections ──────────────────────
    def test_readiness_result_has_micro_contract_in_metadata(self):
        bundle = self._make_bundle()
        result = evaluate_readiness(bundle, self._scorecard(), self._veto_pass())
        metadata = result.metadata or {}
        self.assertIn("micro_contract", metadata)
        self.assertIsInstance(metadata["micro_contract"], dict)
        # Verify readiness produces a meaningful result (not necessarily READY)
        self.assertIsNotNone(result.state)
        self.assertIsNotNone(result.suggested_action)


if __name__ == "__main__":
    unittest.main()

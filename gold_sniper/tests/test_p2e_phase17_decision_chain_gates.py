import asyncio
import unittest

from agents.base_agent import AgentResult
from gold_sniper.replay.evidence_builder import build_evidence_bundle
from gold_sniper.replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager
from gold_sniper.strategy.contracts import EvidenceBundle, SetupType, TradeSide
from gold_sniper.strategy.professional_decision_engine import evaluate_professional_decision
from gold_sniper.strategy.scorecard import evaluate_scorecard


def _valid_sweep_bundle(*, timing_ready: bool = True) -> EvidenceBundle:
    timing = {
        "readiness_state": "READY" if timing_ready else "WAIT_FOR_TRIGGER",
        "execution_readiness": "READY" if timing_ready else "WAIT_FOR_TRIGGER",
        "premium_discount": "DISCOUNT",
        "timing_reconciled": not timing_ready,
        "timing_evidence_source": "AGENT5_MICRO_CONTRACT" if not timing_ready else "AGENT4",
    }
    return EvidenceBundle(
        symbol="XAUUSD",
        ts_utc="2026-06-04T10:41:00+00:00",
        setup_type=SetupType.SWEEP_REVERSAL,
        side=TradeSide.SELL,
        context={
            "direction": "SELL",
            "draw_on_liquidity": "SELL_SIDE",
            "premium_discount": "DISCOUNT",
            "htf_aligned": False,
        },
        poi={
            "poi_available": True,
            "selected_poi_present": True,
            "selected_poi": {"price_bounds": {"low": 4457.96, "high": 4466.01}, "score": 77.9},
            "price_bounds": {"low": 4457.96, "high": 4466.01},
            "poi_quality_score": 77.9,
            "lifecycle_state": "FRESH",
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "poi_micro_synergy_enabled": True,
            "poi_micro_synergy": {
                "synergy": True,
                "effective_poi_status": "POI_READY",
                "upgraded_poi_status": "POI_READY",
                "micro_confirmed": True,
                "micro_inside_poi": True,
                "micro_outside_poi": False,
                "remaining_blockers": [],
            },
        },
        liquidity={
            "readiness_state": "READY",
            "execution_readiness": "READY",
            "liquidity_state": "MICRO_SWEEP_CHOCH",
            "micro_liquidity_confirmed": True,
            "poi_micro_synergy": True,
            "promoted_by_reconciliation": True,
            "liquidity_evidence_source": "AGENT5_MICRO_CONTRACT",
            "liquidity_reconciliation_blockers": [],
            "passed": True,
        },
        micro={
            "readiness_state": "READY",
            "execution_readiness": "READY",
            "micro_is_confirmed": True,
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": True,
            "price_in_agent2_poi": True,
            "trigger_outside_poi": False,
            "displacement_present": True,
            "reclaim_confirmed": True,
            "retest_confirmed": False,
            "trigger_strength": 95.0,
            "passed": True,
        },
        news={
            "news_clear": True,
            "impact_level": "NONE",
            "feed_alive": True,
            "passed": True,
            "reason": "NEWS_CLEAR",
        },
        session={
            "session": "LONDON",
            "session_grade": "MEDIUM",
            "trading_allowed": False,
            "is_hard_block": False,
            "risk_multiplier": 0.7,
            "passed": False,
            "reason": "LONDON",
        },
        risk={
            "atr_risk_multiplier": 0.7,
            "session_risk_multiplier": 0.7,
            "max_daily_loss_hit": False,
            "max_weekly_loss_hit": False,
            "max_drawdown_hit": False,
            "kill_switch": False,
            "passed": False,
            "reason": "LONDON",
        },
        raw={"timing": timing},
    )


class _Blackboard:
    def __init__(self):
        self.data = {}

    def read_sync(self, key):
        return self.data.get(key)

    async def write(self, key, value):
        self.data[key] = value


class TestP2EPhase17DecisionChainGates(unittest.TestCase):
    def test_session_and_risk_present_are_not_missing_when_agent7_passed_false(self):
        scorecard = evaluate_scorecard(_valid_sweep_bundle())
        self.assertNotIn("SESSION_CONTEXT_MISSING", scorecard.missing_evidence)
        self.assertNotIn("RISK_CONTEXT_MISSING", scorecard.missing_evidence)

    def test_valid_micro_sweep_chain_produces_enter_reduced_and_positive_risk(self):
        result = evaluate_professional_decision(_valid_sweep_bundle(timing_ready=False))
        self.assertEqual(result.decision, "ENTER_REDUCED")
        self.assertTrue(result.enter_eligible)
        self.assertGreater(result.risk_multiplier, 0.0)
        self.assertEqual(result.readiness_state, "READY")
        self.assertNotIn("SESSION_CONTEXT_MISSING", result.missing_evidence)
        self.assertNotIn("RISK_CONTEXT_MISSING", result.missing_evidence)

    def test_default_none_side_infers_from_context_direction(self):
        bundle = build_evidence_bundle(
            {
                "agent_1": AgentResult(
                    agent_id="agent_1",
                    score=80,
                    hard_filter_pass=True,
                    direction="SHORT",
                    reason="HTF",
                    payload={
                        "structure_4h": "BEARISH",
                        "structure_15m": "BEARISH",
                        "shadow_ict_contract": {
                            "contextual_notes": {"htf_draw_on_liquidity": "SELL_SIDE"}
                        },
                    },
                )
            }
        )
        self.assertEqual(bundle.side, TradeSide.SELL)

    def test_p1_enter_decision_creates_shadow_open_event(self):
        """P1.1: legacy PDE ENTER without Kasper authority is blocked.

        The Kasper authority gate requires kasper_decision_recommendation == "ENTER_ELIGIBLE"
        plus scenario_id, market_story, and sequence_pass_fail.  A bare PDE ENTER
        without these fields is treated as a legacy path and rejected.
        """
        bundle = _valid_sweep_bundle(timing_ready=False)
        decision = evaluate_professional_decision(bundle).to_dict()
        decision.update(
            {
                "side": "SELL",
                "setup_type": "SWEEP_REVERSAL",
                "p1_evidence_bundle": bundle.to_dict(),
            }
        )
        manager = SimulatedTradeManager(_Blackboard(), SimulatedTradeConfig(write_blackboard_positions=False))
        events = asyncio.run(
            manager.on_p1_decision(
                {"time": "2026-06-04T10:42:00+00:00", "open": 4460.0, "high": 4462.0, "low": 4458.0, "close": 4460.0},
                decision,
            )
        )
        self.assertEqual(events[0]["event"], "p1_decision")
        # P1.1: legacy PDE ENTER without Kasper authority → blocked
        self.assertFalse(any(event["event"] == "open" for event in events),
                         "Legacy ENTER without Kasper authority should be blocked")
        self.assertEqual(manager.summary()["trades"], 0)
        self.assertEqual(manager.summary().get("legacy_enter_blocked_count", 0), 1)

    def test_p1_enter_with_kasper_authority_creates_shadow_open_event(self):
        """P1.1: PDE ENTER with full Kasper authority still works."""
        bundle = _valid_sweep_bundle(timing_ready=False)
        decision = evaluate_professional_decision(bundle).to_dict()
        decision.update(
            {
                "side": "SELL",
                "setup_type": "SWEEP_REVERSAL",
                "p1_evidence_bundle": bundle.to_dict(),
                # Full Kasper authority
                "kasper_decision_recommendation": "ENTER_ELIGIBLE",
                "scenario_id": "KASPER_REVERSAL_SELL_test123",
                "scenario_key": "KASPER_REVERSAL_SELL_test123",
                "decision_id": "KASPER_DEC_test123",
                "kasper_side": "SELL",
                "kasper_grade": "A",
                "market_story": "HTF bias is bearish. Liquidity event: buyside_sweep...",
                "sequence_pass_fail": {
                    "htf_bias": "PASS", "liquidity_sweep": "PASS", "reintegrated": "PASS",
                    "displacement": "PASS", "structure_shift": "PASS", "poi": "PASS",
                    "micro_confirmation": "PASS", "risk_precheck": "PASS",
                },
            }
        )
        manager = SimulatedTradeManager(_Blackboard(), SimulatedTradeConfig(write_blackboard_positions=False))
        events = asyncio.run(
            manager.on_p1_decision(
                {"time": "2026-06-04T10:42:00+00:00", "open": 4460.0, "high": 4462.0, "low": 4458.0, "close": 4460.0},
                decision,
            )
        )
        self.assertEqual(events[0]["event"], "p1_decision")
        self.assertTrue(any(event["event"] == "open" for event in events),
                        "ENTER with full Kasper authority should create trade")
        self.assertEqual(manager.summary()["trades"], 1)

    def test_watch_only_decision_does_not_create_shadow_open_event(self):
        bundle = _valid_sweep_bundle()
        decision = evaluate_professional_decision(bundle).to_dict()
        decision["decision"] = "WATCH_ONLY"
        decision["enter_eligible"] = False
        decision["p1_evidence_bundle"] = bundle.to_dict()
        manager = SimulatedTradeManager(_Blackboard(), SimulatedTradeConfig(write_blackboard_positions=False))
        events = asyncio.run(
            manager.on_p1_decision(
                {"time": "2026-06-04T10:42:00+00:00", "open": 4460.0, "high": 4462.0, "low": 4458.0, "close": 4460.0},
                decision,
            )
        )
        self.assertEqual([event["event"] for event in events], ["p1_decision"])
        self.assertEqual(manager.summary()["trades"], 0)


if __name__ == "__main__":
    unittest.main()

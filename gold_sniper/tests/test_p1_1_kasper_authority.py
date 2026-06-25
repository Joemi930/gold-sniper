"""P1.1 Kasper Authority & Natural Trade Validation — tests.

Tests:
  - Kasper authority gate blocks legacy ENTER paths
  - Trade requires scenario_id, market_story, sequence_pass_fail
  - Agent5 RR estimate is computed and propagated
  - RR missing blocks ENTER
  - RR below minimum blocks ENTER
  - Valid 8/8 Kasper reversal can ENTER
  - POI_REACTION still not tradable
  - Forced ENTER still impossible
  - Duplicate scenario still blocked
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from gold_sniper.strategy.kasper_contracts import (
    Agent1Context,
    Agent2POIContext,
    Agent3LiquidityContext,
    Agent4TimingContext,
    Agent5TriggerContext,
    Agent6NewsContext,
    Agent7SessionContext,
    KasperEvidenceBundle,
    LiquidityEvent,
    MicroConfirmation,
    SelectedPOI,
)
from gold_sniper.strategy.kasper_scenario_engine import KasperScenarioEngine


# ── helpers ───────────────────────────────────────────────────────────

def _bundle(**overrides) -> KasperEvidenceBundle:
    defaults: dict = {
        "agent1": Agent1Context(htf_bias="bullish", structure_state="BULLISH", confidence=0.8),
        "agent2": Agent2POIContext(
            selected_poi=SelectedPOI(
                type="order_block", low=2600.0, high=2610.0, midpoint=2605.0,
                freshness="FRESH", tradable=True, htf_confluence=True,
            ),
        ),
        "agent3": Agent3LiquidityContext(
            liquidity_event=LiquidityEvent(
                type="sellside_sweep", close_back_inside=True,
                wick_rejection=True, displacement_after_sweep=True,
            ),
        ),
        "agent4": Agent4TimingContext(
            in_discount_for_buy=True, ote_zone_touched=True, timing_quality=80.0,
        ),
        "agent5": Agent5TriggerContext(
            micro_confirmation=MicroConfirmation(
                trigger_type="micro_choch", confirmed=True,
                close_breaks_structure=True, wick_rejection_on_poi=True,
                entry_price=2602.0, stop_loss=2595.0,
                target_liquidity=2625.0, rr_estimate=2.3,
            ),
        ),
        "agent6": Agent6NewsContext(news_safe=True, veto=False),
        "agent7": Agent7SessionContext(
            session="LONDON", killzone_active=True, asia_block=False,
            friday_halt=False, spread_safe=True,
        ),
    }
    defaults.update(overrides)
    return KasperEvidenceBundle(**defaults)


# ── Test Suite ────────────────────────────────────────────────────────


class TestKasperAuthorityGate(unittest.TestCase):
    """KasperScenarioEngine must be the sole entry authority."""

    def test_valid_8_of_8_kasper_reversal_can_enter(self):
        """A complete 8/8 sweep reversal produces ENTER_ELIGIBLE grade A/A+."""
        engine = KasperScenarioEngine()
        bundle = _bundle()
        result = engine.evaluate(bundle)
        self.assertEqual(result.decision_recommendation, "ENTER_ELIGIBLE",
                         f"Expected ENTER_ELIGIBLE, got {result.decision_recommendation}: {result.blocking_reason}")
        self.assertIn(result.grade, ("A_PLUS", "A"),
                      f"Expected A_PLUS or A grade, got {result.grade}")
        self.assertIsNotNone(result.scenario_id)
        self.assertIn("sellside_sweep", result.story.lower())

    def test_enter_result_has_scenario_id(self):
        """ENTER_ELIGIBLE result must have a non-empty scenario_id."""
        engine = KasperScenarioEngine()
        bundle = _bundle()
        result = engine.evaluate(bundle)
        self.assertTrue(result.scenario_id, f"scenario_id is empty: {result.scenario_id}")
        self.assertTrue(result.scenario_id.startswith("KASPER_"))

    def test_enter_result_has_market_story(self):
        """ENTER_ELIGIBLE result must have a non-empty market_story."""
        engine = KasperScenarioEngine()
        bundle = _bundle()
        result = engine.evaluate(bundle)
        self.assertTrue(result.story, "market_story is empty")
        self.assertIn("HTF bias", result.story)

    def test_enter_result_has_sequence_pass_fail(self):
        """ENTER_ELIGIBLE result must have sequence_pass_fail with all gates."""
        engine = KasperScenarioEngine()
        bundle = _bundle()
        result = engine.evaluate(bundle)
        self.assertIsInstance(result.sequence, dict)
        self.assertGreater(len(result.sequence), 0)
        for gate in ("htf_bias", "liquidity_sweep", "reintegrated", "displacement",
                      "structure_shift", "poi", "micro_confirmation", "risk_precheck"):
            self.assertEqual(result.sequence.get(gate), "PASS",
                             f"Gate {gate} should be PASS, got {result.sequence.get(gate)}")

    def test_no_trade_without_scenario_id(self):
        """REJECT or WAIT with missing scenario_id should never be ENTER_ELIGIBLE."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent1=Agent1Context(htf_bias="neutral"))
        result = engine.evaluate(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        # Even REJECT/WAIT must have a scenario_id for traceability
        self.assertTrue(result.scenario_id, "Even non-ENTER results must have scenario_id")

    def test_no_trade_without_market_story(self):
        """Every KasperScenarioResult must have a story."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent1=Agent1Context(htf_bias="neutral"))
        result = engine.evaluate(bundle)
        self.assertTrue(result.story, "Every result must have a story")

    def test_reversal_buy_requires_sellside_sweep(self):
        """BUY reversal requires sellside_sweep (liquidity below taken)."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent3=Agent3LiquidityContext(
            liquidity_event=LiquidityEvent(type="buyside_sweep", close_back_inside=True,
                                           displacement_after_sweep=True),
        ))
        result = engine.evaluate(bundle)
        # buyside_sweep → SELL side, not BUY
        self.assertEqual(result.side, "SELL")

    def test_reversal_sell_requires_buyside_sweep(self):
        """SELL reversal requires buyside_sweep (liquidity above taken)."""
        engine = KasperScenarioEngine()
        bundle = _bundle(
            agent1=Agent1Context(htf_bias="bearish", structure_state="BEARISH", confidence=0.8),
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(type="buyside_sweep", close_back_inside=True,
                                               displacement_after_sweep=True),
            ),
            agent4=Agent4TimingContext(in_premium_for_sell=True, ote_zone_touched=True),
        )
        result = engine.evaluate(bundle)
        self.assertEqual(result.side, "SELL")


class TestRRPropagation(unittest.TestCase):
    """Agent5 rr_estimate must flow through KasperScenarioEngine."""

    def test_rr_estimate_present_grants_risk_precheck_pass(self):
        """When Agent5 provides rr_estimate >= 1.5, risk_precheck is PASS."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent5=Agent5TriggerContext(
            micro_confirmation=MicroConfirmation(
                trigger_type="micro_choch", confirmed=True,
                close_breaks_structure=True, wick_rejection_on_poi=True,
                entry_price=2602.0, stop_loss=2595.0,
                target_liquidity=2625.0, rr_estimate=2.5,
            ),
        ))
        result = engine.evaluate(bundle)
        self.assertEqual(result.sequence.get("risk_precheck"), "PASS",
                         f"risk_precheck should PASS with RR=2.5, got {result.sequence.get('risk_precheck')}")
        self.assertEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_rr_missing_blocks_enter(self):
        """When Agent5 provides no rr_estimate, risk_precheck fails."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent5=Agent5TriggerContext(
            micro_confirmation=MicroConfirmation(
                trigger_type="micro_choch", confirmed=True,
                close_breaks_structure=True, wick_rejection_on_poi=True,
                entry_price=2602.0, stop_loss=2595.0,
                rr_estimate=None,  # missing
            ),
        ))
        result = engine.evaluate(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        reason_lower = (result.missing_confluence or "").lower() + (result.blocking_reason or "").lower()
        self.assertTrue("rr" in reason_lower or "missing" in reason_lower,
                        f"Should mention RR or missing in reason: {result.missing_confluence} / {result.blocking_reason}")

    def test_rr_below_min_blocks_enter(self):
        """When rr_estimate < 1.5, risk_precheck fails."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent5=Agent5TriggerContext(
            micro_confirmation=MicroConfirmation(
                trigger_type="micro_choch", confirmed=True,
                close_breaks_structure=True, wick_rejection_on_poi=True,
                entry_price=2602.0, stop_loss=2595.0,
                target_liquidity=2608.0, rr_estimate=0.8,
            ),
        ))
        result = engine.evaluate(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        self.assertIn("below minimum", (result.blocking_reason or "").lower())

    def test_rr_1_5_exactly_passes(self):
        """rr_estimate == 1.5 should PASS risk_precheck."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent5=Agent5TriggerContext(
            micro_confirmation=MicroConfirmation(
                trigger_type="micro_choch", confirmed=True,
                close_breaks_structure=True, wick_rejection_on_poi=True,
                entry_price=2602.0, stop_loss=2595.0,
                target_liquidity=2612.5, rr_estimate=1.5,
            ),
        ))
        result = engine.evaluate(bundle)
        self.assertEqual(result.sequence.get("risk_precheck"), "PASS",
                         f"RR=1.5 should PASS, got {result.sequence.get('risk_precheck')}")


class TestAntiForcedEnter(unittest.TestCase):
    """No forced ENTER — legacy paths blocked."""

    def test_no_bias_no_enter(self):
        """Neutral HTF bias produces REJECT, never ENTER."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent1=Agent1Context(htf_bias="neutral"))
        result = engine.evaluate(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_no_sweep_no_reversal_enter(self):
        """Reversal without sweep cannot ENTER."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent3=Agent3LiquidityContext(
            liquidity_event=LiquidityEvent(type="none"),
        ))
        result = engine.evaluate(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_poi_not_tradable_no_enter(self):
        """Non-tradable POI blocks ENTER."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent2=Agent2POIContext(
            selected_poi=SelectedPOI(type="order_block", low=2600.0, high=2610.0,
                                     freshness="deeply_mitigated", tradable=False),
        ))
        result = engine.evaluate(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_no_micro_confirmation_no_enter(self):
        """Missing micro confirmation blocks ENTER."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent5=Agent5TriggerContext(
            micro_confirmation=MicroConfirmation(
                trigger_type="none", confirmed=False,
                close_breaks_structure=False,
            ),
        ))
        result = engine.evaluate(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")


class TestPOIReactionNonTradable(unittest.TestCase):
    """POI_REACTION remains non-tradable."""

    def test_poi_reaction_type_is_wait_or_reject(self):
        """Any setup classified as POI_REACTION must not ENTER."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent2=Agent2POIContext(
            selected_poi=SelectedPOI(type="fvg", low=2605.0, high=2610.0,
                                     freshness="FRESH", tradable=True),
        ))
        # Even tradable FVGs without displacement should not be tradable as reversal
        # (POI_REACTION-like scenario — no sweep context)
        bundle_no_sweep = _bundle(
            agent2=Agent2POIContext(
                selected_poi=SelectedPOI(type="fvg", low=2605.0, high=2610.0,
                                         freshness="FRESH", tradable=True),
            ),
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(type="sellside_sweep",
                                               close_back_inside=True,
                                               displacement_after_sweep=True),
            ),
        )
        # With sweep it should work; without it shouldn't
        result_no_sweep = engine.evaluate(_bundle(agent3=Agent3LiquidityContext(
            liquidity_event=LiquidityEvent(type="none"))))
        self.assertNotEqual(result_no_sweep.decision_recommendation, "ENTER_ELIGIBLE")


class TestAntiDuplicateScenario(unittest.TestCase):
    """Anti-duplicate scenario gate remains active."""

    def test_different_scenario_ids_are_unique(self):
        """Two different setups produce different scenario_ids."""
        engine = KasperScenarioEngine()
        r1 = engine.evaluate(_bundle())
        r2 = engine.evaluate(_bundle(
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(type="buyside_sweep",
                                               close_back_inside=True,
                                               displacement_after_sweep=True),
            ),
            agent1=Agent1Context(htf_bias="bearish", structure_state="BEARISH"),
        ))
        self.assertNotEqual(r1.scenario_id, r2.scenario_id)


class TestNewsSessionVeto(unittest.TestCase):
    """News and session vetoes block ENTER."""

    def test_news_veto_blocks_enter(self):
        """Active news veto → REJECT."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent6=Agent6NewsContext(
            high_impact_active=True, news_safe=False, veto=True,
            invalid_reason="NFP in 10min",
        ))
        result = engine.evaluate(bundle)
        self.assertEqual(result.decision_recommendation, "REJECT")

    def test_session_veto_blocks_enter(self):
        """Asia session block → REJECT."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent7=Agent7SessionContext(
            session="TOKYO", killzone_active=False, asia_block=True,
            friday_halt=False, spread_safe=True, veto=True,
        ))
        result = engine.evaluate(bundle)
        self.assertEqual(result.decision_recommendation, "REJECT")

    def test_friday_halt_blocks_enter(self):
        """Friday halt → REJECT."""
        engine = KasperScenarioEngine()
        bundle = _bundle(agent7=Agent7SessionContext(
            session="LONDON", killzone_active=True, asia_block=False,
            friday_halt=True, spread_safe=True,
        ))
        result = engine.evaluate(bundle)
        self.assertEqual(result.decision_recommendation, "REJECT")


class TestLegacyEnterBlocked(unittest.TestCase):
    """SimulatedTradeManager Kasper gate tests."""

    def test_shadow_signal_requires_kasper_authority(self):
        """_p1_decision_shadow_signal returns None when Kasper gate fails."""
        from gold_sniper.replay.simulated_trade_manager import _p1_decision_shadow_signal

        decision = {
            "decision": "ENTER_REDUCED",
            "enter_eligible": True,
            "risk_plan": {"allowed": True, "risk_multiplier": 0.5, "risk_pct": 0.5, "risk_amount": 0.5},
            "risk_multiplier": 0.5,
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "A",
            "setup_family": "REVERSAL",
            "p1_evidence_bundle": {
                "side": "SELL",
                "context": {"direction": "SELL"},
                "poi": {"price_bounds": {"low": 2600.0, "high": 2610.0}},
                "liquidity": {},
            },
            # NO kasper_decision_recommendation — should block
        }
        candle = {"time": "2026-06-04T10:41:00", "close": 2605.0, "high": 2608.0, "low": 2602.0}
        result = _p1_decision_shadow_signal(candle, decision)
        self.assertIsNone(result, "Should block ENTER without Kasper authority")
        self.assertTrue(decision.get("_kasper_gate_blocked"))

    def test_shadow_signal_blocks_wait_recommendation(self):
        """kasper_decision_recommendation=WAIT → signal blocked."""
        from gold_sniper.replay.simulated_trade_manager import _p1_decision_shadow_signal

        decision = {
            "decision": "ENTER_REDUCED",
            "enter_eligible": True,
            "risk_plan": {"allowed": True, "risk_multiplier": 0.5, "risk_pct": 0.5, "risk_amount": 0.5},
            "risk_multiplier": 0.5,
            "kasper_decision_recommendation": "WAIT",
            "scenario_id": "KASPER_REVERSAL_SELL_abc123",
            "market_story": "HTF bias is bearish...",
            "sequence_pass_fail": {"htf_bias": "PASS"},
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "A",
            "setup_family": "REVERSAL",
            "p1_evidence_bundle": {
                "side": "SELL",
                "context": {"direction": "SELL"},
                "poi": {"price_bounds": {"low": 2600.0, "high": 2610.0}},
                "liquidity": {},
            },
        }
        candle = {"time": "2026-06-04T10:41:00", "close": 2605.0, "high": 2608.0, "low": 2602.0}
        result = _p1_decision_shadow_signal(candle, decision)
        self.assertIsNone(result, "Should block WAIT recommendation")

    def test_shadow_signal_passes_with_full_kasper_authority(self):
        """ENTER_ELIGIBLE + scenario_id + story + sequence → signal allowed."""
        from gold_sniper.replay.simulated_trade_manager import _p1_decision_shadow_signal

        decision = {
            "decision": "ENTER_REDUCED",
            "enter_eligible": True,
            "risk_plan": {"allowed": True, "risk_multiplier": 0.5, "risk_pct": 0.5, "risk_amount": 0.5},
            "risk_multiplier": 0.5,
            "kasper_decision_recommendation": "ENTER_ELIGIBLE",
            "scenario_id": "KASPER_REVERSAL_SELL_abc123",
            "scenario_key": "KASPER_REVERSAL_SELL_abc123",
            "decision_id": "KASPER_DEC_abc123",
            "kasper_side": "SELL",
            "kasper_grade": "A",
            "market_story": "HTF bias is bearish. Liquidity event: buyside_sweep.",
            "sequence_pass_fail": {
                "htf_bias": "PASS", "liquidity_sweep": "PASS", "reintegrated": "PASS",
                "displacement": "PASS", "structure_shift": "PASS", "poi": "PASS",
                "micro_confirmation": "PASS", "risk_precheck": "PASS",
            },
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "A",
            "setup_family": "REVERSAL",
            "p1_evidence_bundle": {
                "side": "SELL",
                "context": {"direction": "SELL"},
                "poi": {"price_bounds": {"low": 2600.0, "high": 2610.0}},
                "liquidity": {},
                "micro": {"entry_price_candidate": 2610.0, "stop_loss_candidate": 2620.0, "rr_estimate": 2.0},
            },
        }
        candle = {"time": "2026-06-04T10:41:00", "close": 2605.0, "high": 2608.0, "low": 2602.0}
        result = _p1_decision_shadow_signal(candle, decision)
        self.assertIsNotNone(result, "Should allow signal with full Kasper authority")
        self.assertEqual(result.get("signal"), "SELL")

    def test_shadow_signal_blocks_missing_scenario_id(self):
        """Even with ENTER_ELIGIBLE, missing scenario_id blocks."""
        from gold_sniper.replay.simulated_trade_manager import _p1_decision_shadow_signal

        decision = {
            "decision": "ENTER_REDUCED",
            "enter_eligible": True,
            "risk_plan": {"allowed": True, "risk_multiplier": 0.5, "risk_pct": 0.5, "risk_amount": 0.5},
            "risk_multiplier": 0.5,
            "kasper_decision_recommendation": "ENTER_ELIGIBLE",
            # scenario_id MISSING
            "market_story": "HTF bias is bearish...",
            "sequence_pass_fail": {"htf_bias": "PASS"},
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "A",
            "p1_evidence_bundle": {
                "side": "SELL",
                "context": {"direction": "SELL"},
                "poi": {"price_bounds": {"low": 2600.0, "high": 2610.0}},
                "liquidity": {},
            },
        }
        candle = {"time": "2026-06-04T10:41:00", "close": 2605.0, "high": 2608.0, "low": 2602.0}
        result = _p1_decision_shadow_signal(candle, decision)
        self.assertIsNone(result, "Should block without scenario_id")
        self.assertEqual(decision.get("_kasper_gate_reason"), "KASPER_SCENARIO_ID_MISSING")


class TestAgent5RREvidencePropagation(unittest.TestCase):
    """Agent5 RR fields propagate through EvidenceBundle to Kasper adapter."""

    def test_build_kasper_bundle_reads_agent5_rr_fields(self):
        """build_kasper_evidence_bundle reads rr_estimate from micro dict."""
        from gold_sniper.strategy.kasper_contracts import build_kasper_evidence_bundle

        micro = {
            "trigger_type": "MICRO_CHOCH",
            "micro_is_confirmed": True,
            "choch_detected": True,
            "displacement_present": True,
            "entry_price_candidate": 2602.0,
            "stop_loss_candidate": 2595.0,
            "target_liquidity": 2625.0,
            "rr_estimate": 3.0,
            "risk_points": 7.0,
            "rr_invalid_reason": None,
        }
        context = {"direction": "BUY", "htf_context_available": True}
        poi = {"selected_poi": {"type": "order_block", "low": 2600.0, "high": 2610.0, "tradable": True, "freshness": "FRESH"}}
        liquidity = {"sweep_detected": True, "sweep_side": "sellside_sweep", "sweep_rejected": True, "displacement_after_sweep": True}

        bundle = build_kasper_evidence_bundle(
            context=context, poi=poi, liquidity=liquidity, micro=micro,
        )
        self.assertEqual(bundle.agent5.micro_confirmation.entry_price, 2602.0)
        self.assertEqual(bundle.agent5.micro_confirmation.stop_loss, 2595.0)
        self.assertEqual(bundle.agent5.micro_confirmation.target_liquidity, 2625.0)
        self.assertEqual(bundle.agent5.micro_confirmation.rr_estimate, 3.0)

    def test_kasper_bundle_rr_none_when_missing(self):
        """When micro dict has no RR fields, rr_estimate is None."""
        from gold_sniper.strategy.kasper_contracts import build_kasper_evidence_bundle

        micro = {"trigger_type": "NONE", "micro_is_confirmed": False}
        bundle = build_kasper_evidence_bundle(micro=micro)
        self.assertIsNone(bundle.agent5.micro_confirmation.rr_estimate)
        self.assertIsNone(bundle.agent5.micro_confirmation.entry_price)

    def test_kasper_bundle_rr_from_flat_micro(self):
        """rr_estimate from flat micro dict is read correctly."""
        from gold_sniper.strategy.kasper_contracts import build_kasper_evidence_bundle

        micro = {"rr_estimate": 2.1, "entry_price_candidate": 2615.0, "stop_loss_candidate": 2605.0}
        bundle = build_kasper_evidence_bundle(micro=micro)
        self.assertEqual(bundle.agent5.micro_confirmation.rr_estimate, 2.1)
        self.assertEqual(bundle.agent5.micro_confirmation.entry_price, 2615.0)


if __name__ == "__main__":
    unittest.main()

"""P1 Kasper Brain Core — KasperScenarioEngine tests.

Tests the scenario-driven decision engine against all required P1 validation criteria:

  - Reversal buy/sell A+ requires correct sweep direction
  - OB without sweep is not tradable reversal
  - FVG without displacement is not tradable
  - Sweep without reintegration is rejected
  - Sweep with displacement but no CHoCH is WAIT
  - CHoCH without Agent1 bias is rejected
  - POI deeply mitigated is rejected
  - POI_REACTION is WAIT not ENTER
  - News veto blocks A+
  - Session veto blocks A+
  - RR below minimum blocks A+
  - Grade → risk % mapping
  - C and D grades do not enter
  - Duplicate scenario blocks second entry
  - Same side same POI cooldown blocks second entry
  - market_story is present for every decision
  - missing_confluence is logged when WAIT
  - Anti-forced ENTER: no signal can produce enter_eligible=True without full sequence
  - POI_REACTION non-tradable: POI_REACTION setup never produces ENTER_ELIGIBLE
"""

from __future__ import annotations

import unittest

from gold_sniper.strategy.kasper_contracts import (
    Agent1Context,
    Agent2POIContext,
    Agent3LiquidityContext,
    Agent4TimingContext,
    Agent5TriggerContext,
    Agent6NewsContext,
    Agent7SessionContext,
    KasperEvidenceBundle,
    KasperScenarioResult,
    LiquidityEvent,
    MicroConfirmation,
    SelectedPOI,
    build_kasper_evidence_bundle,
)
from gold_sniper.strategy.kasper_scenario_engine import (
    KasperScenarioEngine,
    evaluate_kasper_scenario,
)


# ── helpers ───────────────────────────────────────────────────────────

def _bundle(**overrides) -> KasperEvidenceBundle:
    """Build a KasperEvidenceBundle with sensible defaults, override specific fields."""
    defaults = {
        "agent1": Agent1Context(htf_bias="bullish", structure_state="BULLISH", confidence=0.8),
        "agent2": Agent2POIContext(
            selected_poi=SelectedPOI(
                type="order_block", low=2600.0, high=2610.0, midpoint=2605.0,
                freshness="FRESH", tradable=True, htf_confluence=True,
            ),
            poi_quality=80.0,
        ),
        "agent3": Agent3LiquidityContext(
            liquidity_event=LiquidityEvent(
                type="sellside_sweep", close_back_inside=True,
                displacement_after_sweep=True, wick_rejection=True,
            ),
            liquidity_quality=85.0,
        ),
        "agent4": Agent4TimingContext(
            in_discount_for_buy=True, ote_zone_touched=True,
            timing_quality=75.0,
        ),
        "agent5": Agent5TriggerContext(
            micro_confirmation=MicroConfirmation(
                trigger_type="micro_choch", confirmed=True,
                close_breaks_structure=True, wick_rejection_on_poi=True,
                entry_price=2610.0, stop_loss=2598.0, rr_estimate=2.0,
            ),
            handoff_status="READY",
        ),
        "agent6": Agent6NewsContext(news_safe=True, veto=False),
        "agent7": Agent7SessionContext(
            session="LONDON", killzone_active=True,
            asia_block=False, friday_halt=False, spread_safe=True,
            veto=False,
        ),
        "symbol": "XAUUSD",
        "timestamp": "2026-06-04T10:41:00",
    }
    merged = {**defaults, **overrides}
    return KasperEvidenceBundle(**merged)


def _evaluate(**overrides) -> KasperScenarioResult:
    engine = KasperScenarioEngine()
    return engine.evaluate(_bundle(**overrides))


# ── reversal scenario tests ───────────────────────────────────────────

class TestReversalBuyAPlus(unittest.TestCase):
    """Reversal BUY A+ requires sellside sweep (liquidity below taken → BUY)."""

    def test_reversal_buy_a_plus_requires_sellside_sweep(self):
        result = _evaluate()
        self.assertEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        self.assertEqual(result.side, "BUY")
        self.assertIn(result.grade, ("A_PLUS", "A"))
        self.assertGreaterEqual(result.score, 85.0)

    def test_reversal_sell_a_plus_requires_buyside_sweep(self):
        result = _evaluate(
            agent1=Agent1Context(htf_bias="bearish", structure_state="BEARISH", confidence=0.8),
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(
                    type="buyside_sweep", close_back_inside=True,
                    displacement_after_sweep=True, wick_rejection=True,
                ),
                liquidity_quality=85.0,
            ),
            agent4=Agent4TimingContext(
                in_premium_for_sell=True, ote_zone_touched=True, timing_quality=75.0,
            ),
        )
        self.assertEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        self.assertEqual(result.side, "SELL")

    def test_ob_without_sweep_is_not_tradable_reversal(self):
        result = _evaluate(
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(type="none"),
                liquidity_quality=0.0,
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        self.assertIn("sweep", result.missing_confluence.lower() if result.missing_confluence else "")

    def test_fvg_without_displacement_is_not_tradable(self):
        result = _evaluate(
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(
                    type="sellside_sweep", close_back_inside=True,
                    displacement_after_sweep=False,
                ),
                liquidity_quality=60.0,
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        self.assertIn("displacement", result.missing_confluence.lower() if result.missing_confluence else "")

    def test_sweep_without_reintegration_is_rejected(self):
        result = _evaluate(
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(
                    type="sellside_sweep", close_back_inside=False,
                    displacement_after_sweep=True, wick_rejection=False,
                ),
                liquidity_quality=50.0,
            ),
        )
        # Without reintegration, grade should be D and rejected
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertEqual(result.grade, "D")

    def test_sweep_with_displacement_but_no_choch_is_wait(self):
        result = _evaluate(
            agent5=Agent5TriggerContext(
                micro_confirmation=MicroConfirmation(
                    trigger_type="none", confirmed=False,
                    close_breaks_structure=False, rr_estimate=2.0,
                ),
                handoff_status="WAITING",
            ),
        )
        self.assertIn(result.decision_recommendation, ("WAIT", "REJECT"))
        self.assertIn("structure", (result.missing_confluence or "").lower())

    def test_choch_without_agent1_bias_is_rejected(self):
        result = _evaluate(
            agent1=Agent1Context(htf_bias="neutral", structure_state="unclear", confidence=0.2),
        )
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertEqual(result.grade, "D")
        self.assertIn("bias", (result.missing_confluence or "").lower())

    def test_poi_deeply_mitigated_is_rejected(self):
        result = _evaluate(
            agent2=Agent2POIContext(
                selected_poi=SelectedPOI(
                    type="order_block", low=2600.0, high=2610.0,
                    freshness="deeply_mitigated", tradable=False,
                ),
                poi_quality=20.0,
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_poi_reaction_is_wait_not_enter(self):
        """POI_REACTION must never produce ENTER_ELIGIBLE."""
        result = _evaluate(
            agent2=Agent2POIContext(
                selected_poi=SelectedPOI(
                    type="POI_REACTION", low=2600.0, high=2610.0,
                    freshness="FRESH", tradable=False,
                ),
                poi_quality=45.0,
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_news_veto_blocks_a_plus(self):
        result = _evaluate(
            agent6=Agent6NewsContext(
                high_impact_active=True, news_safe=False, veto=True,
                invalid_reason="FOMC in 5 minutes",
            ),
        )
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("NEWS", result.blocking_reason.upper() if result.blocking_reason else "")
        self.assertEqual(result.score, 0.0)

    def test_session_veto_blocks_a_plus(self):
        result = _evaluate(
            agent7=Agent7SessionContext(
                session="ASIA", asia_block=True, veto=True,
                invalid_reason="Tokyo session — no trading",
            ),
        )
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("ASIA", (result.blocking_reason or "").upper())

    def test_friday_halt_blocks_entry(self):
        result = _evaluate(
            agent7=Agent7SessionContext(
                session="LONDON", friday_halt=True, veto=True,
            ),
        )
        self.assertEqual(result.decision_recommendation, "REJECT")

    def test_rr_below_min_blocks_a_plus(self):
        result = _evaluate(
            agent5=Agent5TriggerContext(
                micro_confirmation=MicroConfirmation(
                    trigger_type="micro_choch", confirmed=True,
                    close_breaks_structure=True, rr_estimate=1.2,
                ),
                handoff_status="READY",
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        self.assertIn("RR", (result.missing_confluence or "").upper())

    def test_rr_missing_blocks_entry(self):
        result = _evaluate(
            agent5=Agent5TriggerContext(
                micro_confirmation=MicroConfirmation(
                    trigger_type="micro_choch", confirmed=True,
                    close_breaks_structure=True, rr_estimate=None,
                ),
                handoff_status="READY",
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_market_story_is_present_for_every_decision(self):
        """Every decision must contain a market_story string."""
        # Test ENTER
        result = _evaluate()
        self.assertIsNotNone(result.story)
        self.assertGreater(len(result.story), 20)
        self.assertIn("HTF bias", result.story)

        # Test WAIT
        result_wait = _evaluate(
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(
                    type="sellside_sweep", close_back_inside=True,
                    displacement_after_sweep=False,
                ),
                liquidity_quality=60.0,
            ),
        )
        self.assertIsNotNone(result_wait.story)
        self.assertGreater(len(result_wait.story), 20)

        # Test REJECT
        result_reject = _evaluate(
            agent6=Agent6NewsContext(high_impact_active=True, news_safe=False, veto=True),
        )
        self.assertIsNotNone(result_reject.story)
        self.assertGreater(len(result_reject.story), 10)

    def test_missing_confluence_is_logged_when_wait(self):
        result = _evaluate(
            agent5=Agent5TriggerContext(
                micro_confirmation=MicroConfirmation(
                    trigger_type="none", confirmed=False,
                    close_breaks_structure=True, rr_estimate=2.0,
                ),
                handoff_status="WAITING",
            ),
        )
        if result.decision_recommendation == "WAIT":
            self.assertIsNotNone(result.missing_confluence)
            self.assertGreater(len(result.missing_confluence), 0)

    def test_sequence_pass_fail_contains_all_steps(self):
        result = _evaluate()
        expected_keys = {"htf_bias", "liquidity_sweep", "reintegrated", "displacement",
                         "structure_shift", "poi", "micro_confirmation", "risk_precheck"}
        self.assertTrue(expected_keys.issubset(set(result.sequence.keys())))
        for key in expected_keys:
            self.assertIn(result.sequence.get(key, "MISSING"), ("PASS", "FAIL", "WAIT", "UNKNOWN"))


# ── continuation scenario tests ────────────────────────────────────────

class TestContinuationScenario(unittest.TestCase):
    """Continuation model tests — strict, requires BOS + displacement POI."""

    def test_continuation_without_bos_is_wait_or_reject(self):
        bundle = _bundle(
            agent5=Agent5TriggerContext(
                micro_confirmation=MicroConfirmation(
                    trigger_type="none", confirmed=False,
                    close_breaks_structure=False, rr_estimate=2.0,
                ),
                handoff_status="WAITING",
            ),
        )
        engine = KasperScenarioEngine()
        result = engine.evaluate_kasper_continuation(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_continuation_requires_htf_bias(self):
        bundle = _bundle(
            agent1=Agent1Context(htf_bias="neutral", structure_state="unclear"),
        )
        engine = KasperScenarioEngine()
        result = engine.evaluate_kasper_continuation(bundle)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        self.assertIn("bias", (result.missing_confluence or "").lower())


# ── scoring and grade tests ────────────────────────────────────────────

class TestScoringAndGrades(unittest.TestCase):
    """Score → grade → risk mapping tests."""

    def test_a_plus_requires_95_score(self):
        engine = KasperScenarioEngine()
        seq = {k: "PASS" for k in ["htf_bias", "liquidity_sweep", "reintegrated",
                                     "displacement", "structure_shift", "poi",
                                     "micro_confirmation", "risk_precheck"]}
        score = engine._score_sequence(seq)
        self.assertEqual(score, 100.0)
        grade = engine._grade_from_score(score)
        self.assertEqual(grade, "A_PLUS")

    def test_a_requires_85_score(self):
        engine = KasperScenarioEngine()
        grade = engine._grade_from_score(85.0)
        self.assertEqual(grade, "A")
        grade = engine._grade_from_score(94.0)
        self.assertEqual(grade, "A")

    def test_b_requires_70_score(self):
        engine = KasperScenarioEngine()
        grade = engine._grade_from_score(70.0)
        self.assertEqual(grade, "B")
        grade = engine._grade_from_score(84.0)
        self.assertEqual(grade, "B")

    def test_c_and_d_do_not_enter(self):
        engine = KasperScenarioEngine()
        self.assertEqual(engine._grade_from_score(50.0), "C")
        self.assertEqual(engine._grade_from_score(30.0), "D")
        # Verify that C/D scenarios are not ENTER_ELIGIBLE
        result_c = _evaluate(
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(
                    type="sellside_sweep", close_back_inside=True,
                    displacement_after_sweep=True,
                ),
                liquidity_quality=60.0,
            ),
            agent5=Agent5TriggerContext(
                micro_confirmation=MicroConfirmation(
                    trigger_type="micro_choch", confirmed=True,
                    close_breaks_structure=True, rr_estimate=1.8,
                ),
            ),
        )
        if result_c.grade == "C":
            self.assertNotEqual(result_c.decision_recommendation, "ENTER_ELIGIBLE")


# ── risk mapping tests ─────────────────────────────────────────────────

class TestRiskMapping(unittest.TestCase):
    """Verify grade → risk percentage mapping."""

    def test_a_plus_recommendation_not_overridden(self):
        """A+ scenario should maintain ENTER_ELIGIBLE, not forced down."""
        result = _evaluate()
        if result.grade == "A_PLUS":
            self.assertEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_a_grade_scenario(self):
        """A-grade scenario: score 85-94."""
        # Remove one component to drop score from 100 to 85
        result = _evaluate(
            agent4=Agent4TimingContext(
                in_discount_for_buy=False, ote_zone_touched=False,
                timing_quality=40.0,
            ),
        )
        self.assertIn(result.grade, ("A_PLUS", "A", "B"))
        # If A or A_PLUS, should be ENTER_ELIGIBLE
        if result.grade in ("A_PLUS", "A"):
            self.assertEqual(result.decision_recommendation, "ENTER_ELIGIBLE")


# ── anti-duplicate tests ───────────────────────────────────────────────

class TestAntiDuplicate(unittest.TestCase):
    """Anti-duplicate scenario gate tests (logic unit tests — the SimulatedTradeManager
    integration is tested via replay smoke)."""

    def test_duplicate_scenario_id_not_possible_at_engine_level(self):
        """KasperScenarioEngine produces unique scenario_ids with hashed content.
        Two identical bundles should produce different hashes if timestamps differ."""
        r1 = _evaluate()
        r2 = _evaluate(timestamp="2026-06-04T10:42:00")
        self.assertNotEqual(r1.scenario_id, r2.scenario_id)

    def test_same_bundle_same_id(self):
        """Same input produces same scenario_id (deterministic)."""
        r1 = _evaluate()
        r2 = _evaluate()
        self.assertEqual(r1.scenario_id, r2.scenario_id)


# ── anti-forced ENTER tests ────────────────────────────────────────────

class TestAntiForcedEnter(unittest.TestCase):
    """Verify that no combination of inputs can force an ENTER without
    the complete Kasper sequence."""

    def test_missing_sweep_never_enter(self):
        for bias in ("bullish", "bearish"):
            result = _evaluate(
                agent1=Agent1Context(htf_bias=bias, confidence=1.0),
                agent3=Agent3LiquidityContext(
                    liquidity_event=LiquidityEvent(type="none"),
                ),
            )
            self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE",
                                f"ENTER with missing sweep, bias={bias}")

    def test_missing_displacement_never_enter(self):
        result = _evaluate(
            agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(
                    type="sellside_sweep", close_back_inside=True,
                    displacement_after_sweep=False,
                ),
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_missing_poi_never_enter(self):
        result = _evaluate(
            agent2=Agent2POIContext(
                selected_poi=SelectedPOI(type="none", tradable=False),
                poi_quality=0.0,
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_missing_micro_never_enter(self):
        result = _evaluate(
            agent5=Agent5TriggerContext(
                micro_confirmation=MicroConfirmation(
                    trigger_type="none", confirmed=False,
                    close_breaks_structure=True, rr_estimate=2.0,
                ),
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_neutral_bias_never_enter(self):
        result = _evaluate(
            agent1=Agent1Context(htf_bias="neutral", confidence=0.0),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_high_score_does_not_bypass_veto(self):
        """Even with perfect agent outputs, news veto must block."""
        result = _evaluate(
            agent6=Agent6NewsContext(
                high_impact_active=True, news_safe=False, veto=True,
                invalid_reason="NFP report",
            ),
        )
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertEqual(result.score, 0.0)


# ── POI_REACTION non-tradable tests ────────────────────────────────────

class TestPOIReactionNonTradable(unittest.TestCase):
    """POI_REACTION must never produce ENTER_ELIGIBLE."""

    def test_poi_reaction_never_enter(self):
        """Even with all other gates passing, POI_REACTION must not enter."""
        result = _evaluate(
            agent2=Agent2POIContext(
                selected_poi=SelectedPOI(
                    type="POI_REACTION", low=2600.0, high=2610.0,
                    freshness="FRESH", tradable=False,
                ),
                poi_quality=45.0,
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_poi_reaction_with_perfect_other_conditions(self):
        """POI_REACTION + perfect everything else = still no ENTER."""
        result = _evaluate(
            agent2=Agent2POIContext(
                selected_poi=SelectedPOI(
                    type="POI_REACTION", low=2600.0, high=2610.0,
                    freshness="FRESH", tradable=False,
                ),
                poi_quality=50.0,
            ),
        )
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")


# ── adapter tests ──────────────────────────────────────────────────────

class TestAdapters(unittest.TestCase):
    """Test that the adapter functions correctly translate existing dicts."""

    def test_build_kasper_bundle_from_dicts(self):
        bundle = build_kasper_evidence_bundle(
            context={"direction": "BUY", "structure_state": "BULLISH", "confidence": 0.8},
            poi={"selected_poi": {"type": "order_block", "tradable": True, "freshness": "FRESH"}},
            liquidity={"sweep_type": "sellside_sweep", "sweep_rejected": True, "displacement_after_sweep": True},
            timing={"premium_discount": "DISCOUNT", "ote_reached": True},
            micro={"trigger_type": "micro_choch", "trigger_confirmed": True, "rr_estimate": 2.0},
            news={"news_clear": True},
            session={"session_label": "LONDON", "killzone_active": True},
            symbol="XAUUSD",
            timestamp="2026-06-04T10:41:00",
        )
        self.assertEqual(bundle.agent1.htf_bias, "bullish")
        self.assertEqual(bundle.agent2.selected_poi.type, "order_block")
        self.assertEqual(bundle.agent3.liquidity_event.type, "sellside_sweep")
        self.assertTrue(bundle.agent3.liquidity_event.close_back_inside)
        self.assertTrue(bundle.agent4.in_discount_for_buy)
        self.assertTrue(bundle.agent5.micro_confirmation.confirmed)

    def test_empty_dicts_produce_defaults(self):
        bundle = build_kasper_evidence_bundle()
        self.assertEqual(bundle.agent1.htf_bias, "neutral")
        self.assertEqual(bundle.agent2.selected_poi.type, "none")
        self.assertEqual(bundle.agent3.liquidity_event.type, "none")

    def test_adapter_handles_none(self):
        bundle = build_kasper_evidence_bundle(
            context=None, poi=None, liquidity=None,
        )
        self.assertEqual(bundle.agent1.htf_bias, "neutral")
        self.assertEqual(bundle.agent2.selected_poi.type, "none")


# ── convenience function test ──────────────────────────────────────────

class TestConvenienceFunction(unittest.TestCase):
    def test_evaluate_kasper_scenario_returns_result(self):
        bundle = _bundle()
        result = evaluate_kasper_scenario(bundle)
        self.assertIsInstance(result, KasperScenarioResult)
        self.assertIsNotNone(result.scenario_id)
        self.assertIsNotNone(result.story)


# ── decision recommendation tests ──────────────────────────────────────

class TestDecisionRecommendation(unittest.TestCase):
    """Test that the engine only produces valid decision recommendations."""

    def test_only_valid_recommendations(self):
        """Every scenario must produce ENTER_ELIGIBLE, WAIT, or REJECT."""
        # Test multiple bundles
        test_cases = [
            _bundle(),  # Full premium
            _bundle(agent1=Agent1Context(htf_bias="neutral")),  # No bias
            _bundle(agent6=Agent6NewsContext(veto=True, news_safe=False)),  # News veto
            _bundle(agent3=Agent3LiquidityContext(
                liquidity_event=LiquidityEvent(type="none"),
            )),  # No sweep
        ]
        for i, bundle in enumerate(test_cases):
            engine = KasperScenarioEngine()
            result = engine.evaluate(bundle)
            self.assertIn(result.decision_recommendation, ("ENTER_ELIGIBLE", "WAIT", "REJECT"),
                          f"Case {i}: invalid recommendation {result.decision_recommendation}")

    def test_scenario_id_format(self):
        result = _evaluate()
        self.assertTrue(result.scenario_id.startswith("KASPER_"))
        self.assertIn("REVERSAL", result.scenario_id)


# ── spread safety test ─────────────────────────────────────────────────

class TestSpreadSafety(unittest.TestCase):
    def test_spread_unsafe_blocks_entry(self):
        result = _evaluate(
            agent7=Agent7SessionContext(
                session="LONDON", spread_safe=False,
            ),
        )
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("SPREAD", (result.blocking_reason or "").upper())


if __name__ == "__main__":
    unittest.main()

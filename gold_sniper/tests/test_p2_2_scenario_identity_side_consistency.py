"""P2.2 Scenario Identity & Side Consistency Audit — tests.

Tests:
  - Scenario identity: scenario_key, decision_id uniqueness
  - Side consistency: kasper_side → signal → trade propagation
  - Side mismatch blocks trade
  - Anti-duplicate uses scenario_key (not generic sweep type)
  - Grade→risk_pct mapping
  - Kasper error never opens trade
  - Continuation is WAIT_ONLY for P2.2
  - Session veto blocks promotion
  - Trade journal has identity + side fields
  - Bridge gates enforce required fields
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
    KasperScenarioResult,
    LiquidityEvent,
    MicroConfirmation,
    SelectedPOI,
    build_kasper_evidence_bundle,
)
from gold_sniper.strategy.kasper_scenario_engine import (
    KasperScenarioEngine,
    build_kasper_scenario_identity,
    evaluate_kasper_scenario,
    reset_kasper_error_counter,
    get_kasper_error_count,
)
from gold_sniper.strategy.risk_allocator import allocate_risk, grade_risk_multiplier
from gold_sniper.strategy.contracts import (
    DecisionAction,
    EvidenceBundle,
    SetupGrade,
    SetupType,
    TradeSide,
)


# ── helpers ───────────────────────────────────────────────────────

def _make_default_bundle(**overrides) -> KasperEvidenceBundle:
    """Build a valid reversal-ready bundle with all 8 gates viable."""
    a1 = Agent1Context(htf_bias="bearish", structure_state="BEARISH",
                       last_htf_bos=True, draw_on_liquidity="sellside", confidence=0.85)
    a2 = Agent2POIContext(selected_poi=SelectedPOI(
        type="order_block", low=2850.0, high=2855.0, midpoint=2852.5,
        freshness="fresh", created_by_displacement=True,
        created_by_bos_or_choch=True, tradable=True,
    ), poi_quality=85.0)
    a3 = Agent3LiquidityContext(liquidity_event=LiquidityEvent(
        type="buyside_sweep", swept_level=2860.0, sweep_time="2026-05-15T14:30:00",
        close_back_inside=True, wick_rejection=True,
        displacement_after_sweep=True, target_after_sweep="sellside",
    ), liquidity_quality=80.0)
    a4 = Agent4TimingContext(in_premium_for_sell=True, ote_zone_touched=True,
                             timing_quality=75.0)
    a5 = Agent5TriggerContext(micro_confirmation=MicroConfirmation(
        trigger_type="micro_choch", confirmed=True,
        close_breaks_structure=True, wick_rejection_on_poi=True,
        entry_price=2853.0, stop_loss=2862.0, rr_estimate=2.0,
    ))
    a6 = Agent6NewsContext(news_safe=True, veto=False)
    a7 = Agent7SessionContext(session="LONDON", spread_safe=True,
                              session_quality=80.0, veto=False)

    kwargs = dict(
        agent1=a1, agent2=a2, agent3=a3, agent4=a4, agent5=a5,
        agent6=a6, agent7=a7, symbol="XAUUSD",
        timestamp="2026-05-15T14:30:00",
    )
    kwargs.update(overrides)
    return KasperEvidenceBundle(**kwargs)


# ── P2.2 Scenario Identity tests ──────────────────────────────────

class TestScenarioIdentity(unittest.TestCase):
    """scenario_key and decision_id uniqueness."""

    def test_scenario_key_differs_for_different_swept_levels(self):
        """Different swept levels produce different scenario_keys."""
        b1 = _make_default_bundle()
        b2 = _make_default_bundle(agent3=Agent3LiquidityContext(
            liquidity_event=LiquidityEvent(
                type="buyside_sweep", swept_level=2870.0,  # different level
                sweep_time="2026-05-15T14:30:00",
                close_back_inside=True, displacement_after_sweep=True,
            ),
            liquidity_quality=80.0,
        ))
        id1 = build_kasper_scenario_identity(b1, "REVERSAL", "SELL")
        id2 = build_kasper_scenario_identity(b2, "REVERSAL", "SELL")
        self.assertNotEqual(id1.scenario_key, id2.scenario_key,
                            "Different swept levels must produce different scenario_keys")

    def test_scenario_key_differs_for_different_poi_bounds(self):
        """Different POI bounds produce different scenario_keys."""
        b1 = _make_default_bundle()
        b2 = _make_default_bundle(agent2=Agent2POIContext(
            selected_poi=SelectedPOI(
                type="order_block", low=2840.0, high=2845.0,  # different bounds
                freshness="fresh", created_by_displacement=True, tradable=True,
            ),
            poi_quality=85.0,
        ))
        id1 = build_kasper_scenario_identity(b1, "REVERSAL", "SELL")
        id2 = build_kasper_scenario_identity(b2, "REVERSAL", "SELL")
        self.assertNotEqual(id1.scenario_key, id2.scenario_key,
                            "Different POI bounds must produce different scenario_keys")

    def test_decision_id_differs_for_consecutive_candles(self):
        """Same opportunity at different candle timestamps → different decision_ids."""
        b = _make_default_bundle()
        id1 = build_kasper_scenario_identity(b, "REVERSAL", "SELL",
                                             candle_timestamp="2026-05-15T14:30:00")
        id2 = build_kasper_scenario_identity(b, "REVERSAL", "SELL",
                                             candle_timestamp="2026-05-15T14:31:00")
        self.assertEqual(id1.scenario_key, id2.scenario_key,
                         "Same opportunity must have same scenario_key")
        self.assertNotEqual(id1.decision_id, id2.decision_id,
                            "Different candles must produce different decision_ids")

    def test_scenario_key_stable_across_same_opportunity(self):
        """Same swept_level, poi, trigger → same scenario_key."""
        b1 = _make_default_bundle()
        b2 = _make_default_bundle()
        id1 = build_kasper_scenario_identity(b1, "REVERSAL", "SELL")
        id2 = build_kasper_scenario_identity(b2, "REVERSAL", "SELL")
        self.assertEqual(id1.scenario_key, id2.scenario_key,
                         "Same components must produce identical scenario_key")

    def test_identity_components_present(self):
        """Identity components dict has all required fields."""
        b = _make_default_bundle()
        identity = build_kasper_scenario_identity(b, "REVERSAL", "SELL")
        comps = identity.identity_components
        for field in ("symbol", "family", "side", "sweep_type", "swept_level",
                      "sweep_time", "poi_type", "poi_low", "poi_high",
                      "trigger_type", "candle_timestamp"):
            self.assertIn(field, comps, f"identity_components missing '{field}'")


# ── P2.2 Side consistency tests ───────────────────────────────────

class TestSideConsistency(unittest.TestCase):
    """kasper_side propagation and mismatch rejection."""

    def test_kasper_side_propagates_to_result(self):
        """Buyside sweep → Kasper side SELL (reversal)."""
        b = _make_default_bundle()
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.side, "SELL",
                         f"buyside_sweep reversal should be SELL, got {result.side}")

    def test_sellside_sweep_gives_buy_side(self):
        """Sellside sweep → Kasper side BUY."""
        a3 = Agent3LiquidityContext(liquidity_event=LiquidityEvent(
            type="sellside_sweep", swept_level=2840.0, sweep_time="2026-05-15T14:30:00",
            close_back_inside=True, wick_rejection=True,
            displacement_after_sweep=True,
        ), liquidity_quality=80.0)
        a1 = Agent1Context(htf_bias="bullish", structure_state="BULLISH",
                           draw_on_liquidity="buyside", confidence=0.85)
        b = _make_default_bundle(agent1=a1, agent3=a3)
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.side, "BUY",
                         f"sellside_sweep reversal should be BUY, got {result.side}")

    def test_buy_story_cannot_open_sell_trade(self):
        """When Kasper says BUY, the side must be BUY — not SELL."""
        a3 = Agent3LiquidityContext(liquidity_event=LiquidityEvent(
            type="sellside_sweep", swept_level=2840.0, sweep_time="2026-05-15T14:30:00",
            close_back_inside=True, wick_rejection=True, displacement_after_sweep=True,
        ), liquidity_quality=80.0)
        a1 = Agent1Context(htf_bias="bullish", structure_state="BULLISH",
                           draw_on_liquidity="buyside", confidence=0.85)
        b = _make_default_bundle(agent1=a1, agent3=a3)
        result = evaluate_kasper_scenario(b)
        # Even if someone tried to force SELL, the scenario_key embeds BUY
        self.assertEqual(result.side, "BUY")
        self.assertIn("BUY", result.scenario_key if result.scenario_key else "",
                      "scenario_key should embed BUY side")

    def test_sell_story_cannot_open_buy_trade(self):
        """When Kasper says SELL, the side must be SELL — not BUY."""
        b = _make_default_bundle()  # buyside_sweep → SELL reversal
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.side, "SELL")
        self.assertIn("SELL", result.scenario_key if result.scenario_key else "",
                      "scenario_key should embed SELL side")


# ── P2.2 Anti-duplicate tests ─────────────────────────────────────

class TestAntiDuplicateP22(unittest.TestCase):
    """Anti-duplicate uses scenario_key, not generic sweep type."""

    def test_same_scenario_key_blocks_duplicate(self):
        """Two signals with same scenario_key → duplicate blocked."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig
        tm = SimulatedTradeManager(MagicMock(), SimulatedTradeConfig(equity_initial=100.0))
        # Inject an active trade with a scenario_key
        tm.active_positions[1] = {
            "ticket": 1, "type": "SELL", "scenario_key": "KASPER_REVERSAL_SELL_abc123",
            "poi_type": "order_block", "poi_low": 2850.0, "poi_high": 2855.0,
            "swept_level": 2860.0, "sweep_time": "2026-05-15T14:30:00",
        }
        signal = {"signal": "SELL", "scenario_key": "KASPER_REVERSAL_SELL_abc123"}
        result = tm._check_duplicate_scenario(signal)
        self.assertEqual(result, "DUPLICATE_SCENARIO_KEY_ACTIVE")

    def test_different_scenario_key_not_blocked(self):
        """Different scenario_key → NOT blocked."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig
        tm = SimulatedTradeManager(MagicMock(), SimulatedTradeConfig(equity_initial=100.0))
        tm.active_positions[1] = {
            "ticket": 1, "type": "SELL", "scenario_key": "KASPER_REVERSAL_SELL_abc123",
            "poi_type": "order_block", "poi_low": 2850.0, "poi_high": 2855.0,
            "swept_level": 2860.0, "sweep_time": "2026-05-15T14:30:00",
        }
        signal = {"signal": "SELL", "scenario_key": "KASPER_REVERSAL_SELL_xyz789"}
        result = tm._check_duplicate_scenario(signal)
        self.assertIsNone(result, "Different scenario_key should not be blocked")

    def test_generic_sweep_type_alone_does_not_block_different_setup(self):
        """Two different setups sharing generic sweep type → NOT blocked."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig
        tm = SimulatedTradeManager(MagicMock(), SimulatedTradeConfig(equity_initial=100.0))
        # Active trade with a specific swept_level
        tm.active_positions[1] = {
            "ticket": 1, "type": "SELL",
            "scenario_key": "KASPER_REVERSAL_SELL_abc123",
            "poi_type": "order_block", "poi_low": 2850.0, "poi_high": 2855.0,
            "swept_level": 2860.0, "sweep_time": "2026-05-15T14:30:00",
            "liquidity_event_type": "buyside_sweep",
        }
        # New signal — same generic sweep type but DIFFERENT swept_level + poi
        signal = {
            "signal": "SELL",
            "scenario_key": "KASPER_REVERSAL_SELL_def456",
            "liquidity_event_type": "buyside_sweep",  # same generic type
            "swept_level": 2875.0,  # different level
            "sweep_time": "2026-05-16T10:00:00",  # different time
            "poi_type": "fvg", "poi_low": 2865.0, "poi_high": 2870.0,
        }
        result = tm._check_duplicate_scenario(signal)
        self.assertIsNone(result,
                          "Generic sweep type alone must not block different setups")

    def test_same_side_same_poi_blocks_duplicate(self):
        """Same side + same POI bounds → blocked."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig
        tm = SimulatedTradeManager(MagicMock(), SimulatedTradeConfig(equity_initial=100.0))
        tm.active_positions[1] = {
            "ticket": 1, "type": "SELL",
            "scenario_key": "KASPER_REVERSAL_SELL_abc123",
            "poi_type": "order_block", "poi_low": 2850.0, "poi_high": 2855.0,
        }
        signal = {
            "signal": "SELL",
            "scenario_key": "KASPER_REVERSAL_SELL_other999",  # different key
            "poi_type": "order_block", "poi_low": 2850.0, "poi_high": 2855.0,
        }
        result = tm._check_duplicate_scenario(signal)
        self.assertEqual(result, "SAME_SIDE_SAME_POI_ACTIVE")

    def test_same_side_same_sweep_level_blocks_duplicate(self):
        """Same side + same swept_level + sweep_time → blocked."""
        from gold_sniper.replay.simulated_trade_manager import SimulatedTradeManager, SimulatedTradeConfig
        tm = SimulatedTradeManager(MagicMock(), SimulatedTradeConfig(equity_initial=100.0))
        tm.active_positions[1] = {
            "ticket": 1, "type": "BUY",
            "scenario_key": "KASPER_REVERSAL_BUY_abc123",
            "swept_level": 2840.0, "sweep_time": "2026-05-15T14:30:00",
        }
        signal = {
            "signal": "BUY",
            "scenario_key": "KASPER_REVERSAL_BUY_other999",
            "swept_level": 2840.0, "sweep_time": "2026-05-15T14:30:00",
        }
        result = tm._check_duplicate_scenario(signal)
        self.assertEqual(result, "SAME_SIDE_SAME_SWEEP_LEVEL_ACTIVE")


# ── P2.2 Grade/risk mapping tests ─────────────────────────────────

class TestGradeRiskMapping(unittest.TestCase):
    """Grade → risk_pct mapping from Kasper grade."""

    def test_a_plus_uses_one_percent_when_no_cap(self):
        """A_PLUS → 1.00% risk."""
        risk = grade_risk_multiplier(SetupGrade.A_PLUS)
        self.assertEqual(risk, 1.00, f"A_PLUS should be 1.00%, got {risk}")

    def test_a_maps_to_075_percent(self):
        """A → 0.75% risk."""
        risk = grade_risk_multiplier(SetupGrade.A)
        self.assertEqual(risk, 0.75, f"A should be 0.75%, got {risk}")

    def test_b_maps_to_050_percent(self):
        """B → 0.50% risk."""
        risk = grade_risk_multiplier(SetupGrade.B)
        self.assertEqual(risk, 0.50, f"B should be 0.50%, got {risk}")

    def test_c_has_no_trade_risk(self):
        """C → 0.25% (WATCH_ONLY — not tradable by default)."""
        risk = grade_risk_multiplier(SetupGrade.C)
        self.assertEqual(risk, 0.25)

    def test_d_has_zero_risk(self):
        """D → 0.00% (REJECT)."""
        risk = grade_risk_multiplier(SetupGrade.D)
        self.assertEqual(risk, 0.0)

    def test_c_grade_not_tradable_in_engine(self):
        """C grade in Kasper engine → WAIT or REJECT, not ENTER_ELIGIBLE."""
        # Build a bundle that passes structural gates but has weak RR → low score → C
        a5_weak = Agent5TriggerContext(micro_confirmation=MicroConfirmation(
            trigger_type="micro_choch", confirmed=False,  # weak micro
            close_breaks_structure=True, rr_estimate=1.0,  # weak RR
        ))
        b = _make_default_bundle(agent5=a5_weak)
        result = evaluate_kasper_scenario(b)
        # With a weak micro confirmation, should be WAIT/REJECT, not ENTER_ELIGIBLE
        self.assertIn(result.decision_recommendation, ("WAIT", "REJECT"),
                      f"C-grade should not be ENTER_ELIGIBLE, got {result.decision_recommendation}")

    def test_d_grade_not_tradable_in_engine(self):
        """D grade in Kasper engine → REJECT."""
        # Remove everything → D grade
        empty = KasperEvidenceBundle()
        result = evaluate_kasper_scenario(empty)
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertEqual(result.grade, "D")


# ── P2.2 Kasper error tests ───────────────────────────────────────

class TestKasperErrorNeverOpensTrade(unittest.TestCase):
    """Kasper engine errors never produce ENTER_ELIGIBLE."""

    def setUp(self):
        reset_kasper_error_counter()

    def test_kasper_error_never_opens_trade(self):
        """Engine error → REJECT with kasper_error set."""
        with patch.object(KasperScenarioEngine, '_hard_veto',
                          side_effect=RuntimeError("Simulated engine crash")):
            b = _make_default_bundle()
            result = evaluate_kasper_scenario(b)
            self.assertEqual(result.decision_recommendation, "REJECT")
            self.assertIsNotNone(result.kasper_error)
            self.assertIn("Simulated engine crash", result.kasper_error or "")
            self.assertGreater(get_kasper_error_count(), 0)

    def test_kasper_error_result_has_d_grade(self):
        """Error result always has grade D."""
        with patch.object(KasperScenarioEngine, '_hard_veto',
                          side_effect=ValueError("Boom")):
            b = _make_default_bundle()
            result = evaluate_kasper_scenario(b)
            self.assertEqual(result.grade, "D")
            self.assertEqual(result.score, 0.0)

    def test_kasper_error_result_has_no_scenario_key(self):
        """Error result has KASPER_ERROR identity."""
        with patch.object(KasperScenarioEngine, '_hard_veto',
                          side_effect=Exception("Fail")):
            b = _make_default_bundle()
            result = evaluate_kasper_scenario(b)
            self.assertEqual(result.scenario_key, "KASPER_ERROR")
            self.assertEqual(result.decision_id, "KASPER_ERROR")

    def test_normal_evaluation_does_not_increment_error_counter(self):
        """Normal evaluation leaves error counter at 0."""
        reset_kasper_error_counter()
        b = _make_default_bundle()
        evaluate_kasper_scenario(b)
        self.assertEqual(get_kasper_error_count(), 0)


# ── P2.2 Continuation WAIT_ONLY tests ─────────────────────────────

class TestContinuationWaitOnly(unittest.TestCase):
    """P2.2: Continuation is WAIT_ONLY — KASPER_WEIGHTS don't cover it."""

    def test_continuation_never_returns_enter_eligible(self):
        """Even with perfect continuation evidence, it must return WAIT."""
        # Build a bundle where reversal fails structurally but continuation
        # would pass (if it were allowed)
        a3 = Agent3LiquidityContext(liquidity_event=LiquidityEvent(
            type="buyside_sweep", swept_level=2860.0,
            close_back_inside=True,  # reintegration passes
            displacement_after_sweep=True,  # displacement passes
        ), liquidity_quality=80.0)
        mc = MicroConfirmation(
            trigger_type="micro_bos", confirmed=True,
            close_breaks_structure=True,
            entry_price=2853.0, stop_loss=2862.0, rr_estimate=2.0,
        )
        a5 = Agent5TriggerContext(micro_confirmation=mc)
        # POI is tradable AND created by displacement (continuation POI gate)
        a2 = Agent2POIContext(selected_poi=SelectedPOI(
            type="order_block", low=2850.0, high=2855.0,
            freshness="fresh", created_by_displacement=True,
            tradable=True,
        ), poi_quality=85.0)
        b = _make_default_bundle(agent3=a3, agent5=a5, agent2=a2)
        result = evaluate_kasper_scenario(b)
        # Reversal should pass all 8 gates → ENTER_ELIGIBLE
        # If reversal passes, continuation is never reached
        # To test continuation directly: make reversal fail at POI gate
        a2_bad = Agent2POIContext(selected_poi=SelectedPOI(
            type="order_block", low=2850.0, high=2855.0,
            freshness="deeply_mitigated",  # reversal fails POI
            created_by_displacement=True, tradable=False,
        ), poi_quality=20.0)
        b_bad = _make_default_bundle(agent3=a3, agent5=a5, agent2=a2_bad)
        result2 = evaluate_kasper_scenario(b_bad)
        # Reversal fails at POI → structural gates passed → tries continuation
        # Continuation must be WAIT, not ENTER_ELIGIBLE
        self.assertNotEqual(result2.decision_recommendation, "ENTER_ELIGIBLE",
                            "Continuation must not be ENTER_ELIGIBLE in P2.2")

    def test_kasper_weights_do_not_include_continuation_keys(self):
        """KASPER_WEIGHTS only covers reversal keys."""
        from gold_sniper.strategy.kasper_scenario_engine import KASPER_WEIGHTS
        continuation_keys = {"continuation_bos", "continuation_poi"}
        for key in continuation_keys:
            self.assertNotIn(key, KASPER_WEIGHTS,
                             f"KASPER_WEIGHTS must not contain {key} — continuation is WAIT_ONLY")


# ── P2.2 Session veto tests ───────────────────────────────────────

class TestSessionVetoBlocksPromotion(unittest.TestCase):
    """Session/Asia/Friday/spread veto blocks Kasper promotion."""

    def test_session_veto_blocks_entry(self):
        """Session veto → REJECT even if all gates pass."""
        a7 = Agent7SessionContext(session="TOKYO", asia_block=True,
                                  spread_safe=True, veto=True,
                                  invalid_reason="Tokyo/Asia session block")
        b = _make_default_bundle(agent7=a7)
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("ASIA_BLOCK", result.blocking_reason or "")

    def test_friday_halt_blocks_entry(self):
        """Friday halt → REJECT."""
        a7 = Agent7SessionContext(session="LONDON", friday_halt=True,
                                  spread_safe=True, veto=False)
        b = _make_default_bundle(agent7=a7)
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("FRIDAY_HALT", result.blocking_reason or "")

    def test_spread_unsafe_blocks_entry(self):
        """Spread unsafe → REJECT."""
        a7 = Agent7SessionContext(session="LONDON", spread_safe=False,
                                  veto=False)
        b = _make_default_bundle(agent7=a7)
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("SPREAD_UNSAFE", result.blocking_reason or "")

    def test_news_veto_blocks_entry(self):
        """News veto → REJECT."""
        a6 = Agent6NewsContext(high_impact_active=True, news_safe=False,
                               veto=True, invalid_reason="NFP pending")
        b = _make_default_bundle(agent6=a6)
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("NEWS_VETO", result.blocking_reason or "")

    def test_cooldown_active_blocks_entry(self):
        """Cooldown active → REJECT."""
        a7 = Agent7SessionContext(session="LONDON", spread_safe=True,
                                  cooldown_active=True, veto=False)
        b = _make_default_bundle(agent7=a7)
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("COOLDOWN_ACTIVE", result.blocking_reason or "")

    def test_daily_limit_exceeded_blocks_entry(self):
        """Daily limiter exceeded → REJECT."""
        a7 = Agent7SessionContext(session="LONDON", spread_safe=True,
                                  daily_trade_count=2, veto=False)
        b = _make_default_bundle(agent7=a7)
        result = evaluate_kasper_scenario(b)
        self.assertEqual(result.decision_recommendation, "REJECT")
        self.assertIn("DAILY_LIMIT_EXCEEDED", result.blocking_reason or "")


# ── P2.2 Trade journal identity tests ─────────────────────────────

class TestTradeJournalIdentityFields(unittest.TestCase):
    """Trade journal must include scenario identity and side fields."""

    def test_kasper_result_has_all_identity_fields(self):
        """KasperScenarioResult includes scenario_key, decision_id."""
        b = _make_default_bundle()
        result = evaluate_kasper_scenario(b)
        self.assertIsNotNone(result.scenario_key)
        self.assertNotEqual(result.scenario_key, "")
        self.assertIsNotNone(result.decision_id)
        self.assertNotEqual(result.decision_id, "")

    def test_kasper_result_has_scenario_id(self):
        """scenario_id still present for backward compatibility."""
        b = _make_default_bundle()
        result = evaluate_kasper_scenario(b)
        self.assertIsNotNone(result.scenario_id)
        self.assertNotEqual(result.scenario_id, "")

    def test_enter_eligible_result_has_side(self):
        """ENTER_ELIGIBLE result has kasper_side = BUY or SELL."""
        b = _make_default_bundle()
        result = evaluate_kasper_scenario(b)
        if result.decision_recommendation == "ENTER_ELIGIBLE":
            self.assertIn(result.side, ("BUY", "SELL"))

    def test_reject_result_has_blocking_reason(self):
        """REJECT result has a blocking reason."""
        empty = KasperEvidenceBundle()
        result = evaluate_kasper_scenario(empty)
        self.assertIsNotNone(result.blocking_reason)

    def test_kasper_error_field_is_none_on_success(self):
        """Normal evaluation → kasper_error is None."""
        b = _make_default_bundle()
        result = evaluate_kasper_scenario(b)
        self.assertIsNone(result.kasper_error,
                          "kasper_error should be None on successful evaluation")


# ── P2.2 Bridge gate tests ────────────────────────────────────────

class TestBridgeGates(unittest.TestCase):
    """PDE/Kasper bridge gate enforcement."""

    def test_a_plus_enter_eligible_has_valid_rr(self):
        """A_PLUS ENTER_ELIGIBLE result has RR >= 1.5."""
        b = _make_default_bundle()
        result = evaluate_kasper_scenario(b)
        if result.decision_recommendation == "ENTER_ELIGIBLE" and result.grade == "A_PLUS":
            self.assertIn("risk_precheck", result.sequence)
            self.assertEqual(result.sequence.get("risk_precheck"), "PASS")

    def test_weak_rr_not_enter_eligible(self):
        """RR < 1.5 → not ENTER_ELIGIBLE."""
        a5 = Agent5TriggerContext(micro_confirmation=MicroConfirmation(
            trigger_type="micro_choch", confirmed=True,
            close_breaks_structure=True, wick_rejection_on_poi=True,
            entry_price=2853.0, stop_loss=2862.0, rr_estimate=1.2,  # below 1.5
        ))
        b = _make_default_bundle(agent5=a5)
        result = evaluate_kasper_scenario(b)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE",
                            "RR < 1.5 must not be ENTER_ELIGIBLE")

    def test_no_micro_confirmation_not_enter_eligible(self):
        """Missing micro confirmation → not ENTER_ELIGIBLE."""
        a5 = Agent5TriggerContext(micro_confirmation=MicroConfirmation(
            confirmed=False,
        ))
        b = _make_default_bundle(agent5=a5)
        result = evaluate_kasper_scenario(b)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_deeply_mitigated_poi_not_enter_eligible(self):
        """Deeply mitigated POI → not ENTER_ELIGIBLE."""
        a2 = Agent2POIContext(selected_poi=SelectedPOI(
            type="order_block", low=2850.0, high=2855.0,
            freshness="deeply_mitigated", created_by_displacement=True,
            tradable=False,
        ), poi_quality=10.0)
        b = _make_default_bundle(agent2=a2)
        result = evaluate_kasper_scenario(b)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")


if __name__ == "__main__":
    unittest.main()

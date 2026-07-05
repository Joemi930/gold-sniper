"""P1 Kasper Brain Core — scenario-driven decision engine.

KasperScenarioEngine reads the KasperEvidenceBundle (normalized agent outputs)
and reconstructs a complete trading story before recommending ENTER / WAIT / REJECT.

It does NOT replace the existing ProfessionalDecisionEngine.
It sits *before* the PDE, providing:

    - market_story (narrative)
    - scenario_id (unique, hashable)
    - sequence_pass_fail (per-step gate status)
    - missing_confluence (for WAIT decisions)
    - hard_veto_reason (for REJECT decisions)
    - grade / score / decision_recommendation

Architecture:
    Agents 1..7 → EvidenceBundle → KasperEvidenceBundle
    → KasperScenarioEngine.evaluate()
    → KasperScenarioResult
    → ProfessionalDecisionEngine (enriched with scenario context)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from .kasper_contracts import (
    Agent1Context,
    Agent2POIContext,
    Agent3LiquidityContext,
    Agent4TimingContext,
    Agent5TriggerContext,
    Agent6NewsContext,
    Agent7SessionContext,
    DecisionRecommendation,
    Grade,
    KasperEvidenceBundle,
    KasperScenarioIdentity,
    KasperScenarioResult,
    LiquidityEvent,
    MicroConfirmation,
    PassFail,
    SelectedPOI,
    Side,
)


# ── sequence step weights (Kasper scoring model) ─────────────────────
KASPER_WEIGHTS: Dict[str, float] = {
    "htf_bias": 15.0,
    "liquidity_sweep": 20.0,
    "reintegrated": 5.0,
    "displacement": 15.0,
    "structure_shift": 15.0,
    "poi": 20.0,
    "micro_confirmation": 5.0,
    "risk_precheck": 5.0,
}

# V2: dedicated weights for the continuation model (5 gates → 100).
# P2.2 locked continuation to WAIT because KASPER_WEIGHTS lacked these keys;
# V2 unlocks it with its own weights so grading is meaningful.
KASPER_CONTINUATION_WEIGHTS: Dict[str, float] = {
    "htf_bias": 25.0,
    "continuation_bos": 25.0,
    "continuation_poi": 20.0,
    "micro_confirmation": 20.0,
    "risk_precheck": 10.0,
}


def _v2_enabled() -> bool:
    """STRATEGY V2 (regime-selected dual edge). Default OFF."""
    try:
        from config import STRATEGY_V2
        return bool(STRATEGY_V2)
    except Exception:
        return False


# Grade thresholds
GRADE_A_PLUS_MIN = 95.0
GRADE_A_MIN = 85.0
GRADE_B_MIN = 70.0
GRADE_C_MIN = 50.0


# ── Graded-entry feature flag (pure; no env reads in the strategy layer) ──
# Default OFF preserves the legacy binary 8/8 behaviour and the test-suite.
# The replay layer toggles it via set_graded_entry() based on its own config.
#
# IMPORTANT: the flag is stored on `builtins`, NOT as a module global. The
# replay's PYTHONPATH has two roots, so this file is imported under two names
# ("gold_sniper.strategy.kasper_scenario_engine" and "strategy.kasper_scenario_engine")
# = two distinct module objects with independent globals. A module-global flag
# set via one import is invisible to the other, which is exactly why graded mode
# silently failed to activate during replay. `builtins` is a single shared
# namespace across every import path, so the flag is process-global and correct.
import builtins as _builtins

_GRADED_FLAG_ATTR = "_GOLD_SNIPER_KASPER_GRADED_ENTRY"


def set_graded_entry(enabled: bool) -> None:
    """Enable/disable graded-entry mode (A+/A/B). Called by the replay layer."""
    setattr(_builtins, _GRADED_FLAG_ATTR, bool(enabled))


def graded_entry_enabled() -> bool:
    """Return whether graded-entry mode is currently enabled."""
    return bool(getattr(_builtins, _GRADED_FLAG_ATTR, False))


class KasperScenarioEngine:
    """Scenario-driven decision engine — Kasper/ICT premium model.

    Evaluates a KasperEvidenceBundle through:
        1. Hard veto (news, session, asia, friday, spread)
        2. Reversal scenario (primary premium model)
        3. Continuation scenario (strict fallback)
        4. Returns the best valid result or the best explanation for WAIT/REJECT
    """

    # ── public API ─────────────────────────────────────────────────

    @property
    def graded_entry(self) -> bool:
        """Graded-entry mode (A+/A/B tiers) vs legacy binary 8/8.

        When enabled, the four *confirmation* gates (reintegration,
        displacement, structure_shift, micro_confirmation) are SCORED instead
        of acting as hard early-exit vetoes. The four *validity* gates
        (htf_bias, liquidity_sweep, poi, risk_precheck) remain hard vetoes.
        A setup enters at grade >= B (score >= 70), which still requires real
        confirmation on top of the 60-pt validity base — it is graded, not
        diluted. Disabled by default so the binary behaviour and the existing
        test-suite are preserved. The replay layer flips it on via
        ``set_graded_entry(True)`` (the strategy layer stays pure — no env reads).
        """
        return graded_entry_enabled()

    def evaluate(
        self,
        evidence: KasperEvidenceBundle,
        *,
        candle_timestamp: Optional[str] = None,
    ) -> KasperScenarioResult:
        """Evaluate a complete evidence bundle and produce a Kasper scenario result.

        Args:
            evidence: Normalized agent outputs.
            candle_timestamp: Current candle timestamp for decision_id uniqueness.
        """
        try:
            return self._evaluate_impl(evidence, candle_timestamp=candle_timestamp)
        except Exception as exc:
            global _KASPER_ERROR_COUNTER, _KASPER_LAST_ERROR
            import traceback
            from datetime import datetime, timezone
            _KASPER_ERROR_COUNTER += 1
            _KASPER_LAST_ERROR = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "exception_class": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
            side = self._side_from_bias_and_sweep(evidence)
            return KasperScenarioResult(
                scenario_id="KASPER_ERROR",
                scenario_key="KASPER_ERROR",
                decision_id="KASPER_ERROR",
                side=side,
                scenario_type="engine_error",
                story=f"Kasper engine error: {type(exc).__name__}: {exc}",
                sequence={"engine_error": "FAIL"},
                grade="D",
                score=0.0,
                decision_recommendation="REJECT",
                blocking_reason=f"ENGINE_ERROR: {type(exc).__name__}",
                missing_confluence=f"Kasper engine raised {type(exc).__name__}",
                kasper_error=str(exc),
            )

    def _evaluate_impl(
        self,
        evidence: KasperEvidenceBundle,
        *,
        candle_timestamp: Optional[str] = None,
    ) -> KasperScenarioResult:
        """Internal implementation — all exceptions caught by evaluate()."""
        # 1. Hard veto always wins
        veto = self._hard_veto(evidence)
        if veto:
            return self._reject(evidence, veto, candle_timestamp=candle_timestamp)

        # ── V2 regime dispatch: the regime selects the edge ──
        # STRONG trend → continuation ONLY (fading strong trends is the proven
        # account-killer: Feb 0/5 straight-to-SL). RANGE/WEAK → reversal ONLY
        # (the premium mean-reversion edge, in its natural habitat).
        if _v2_enabled():
            _regime = str(getattr(evidence.agent1, "primary_regime", "UNKNOWN") or "UNKNOWN").upper()
            if _regime in ("STRONG_UP", "STRONG_DOWN"):
                return self.evaluate_kasper_continuation(evidence, candle_timestamp=candle_timestamp)
            reversal = self.evaluate_kasper_reversal(evidence, candle_timestamp=candle_timestamp)
            return reversal

        # 2. Reversal first — the premium model
        reversal = self.evaluate_kasper_reversal(evidence, candle_timestamp=candle_timestamp)
        if reversal.decision_recommendation == "ENTER_ELIGIBLE":
            return reversal

        # 3. Continuation — strict only, but ONLY if reversal passed the
        #    structural gates. If reversal failed before POI/micro, the
        #    structural foundation is missing — don't paper over with
        #    a continuation model.
        if self._reversal_passed_structural_gates(reversal.sequence):
            continuation = self.evaluate_kasper_continuation(evidence, candle_timestamp=candle_timestamp)
            if continuation.decision_recommendation == "ENTER_ELIGIBLE":
                return continuation
            return self._best_explanation(reversal, continuation)

        # 4. Reversal failed structurally — return reversal explanation directly
        return reversal

    def evaluate_kasper_reversal(
        self,
        evidence: KasperEvidenceBundle,
        *,
        candle_timestamp: Optional[str] = None,
    ) -> KasperScenarioResult:
        """Evaluate a liquidity-sweep reversal setup (premium Kasper model).

        Required sequence:
            HTF bias → liquidity sweep → reintegration → displacement
            → structure shift (BOS/CHoCH) → tradable POI → micro confirmation
            → risk precheck (RR ≥ 1.5)
        """
        sequence: Dict[str, PassFail] = {
            "htf_bias": "UNKNOWN",
            "liquidity_sweep": "UNKNOWN",
            "reintegrated": "UNKNOWN",
            "displacement": "UNKNOWN",
            "structure_shift": "UNKNOWN",
            "poi": "UNKNOWN",
            "micro_confirmation": "UNKNOWN",
            "risk_precheck": "UNKNOWN",
        }

        side = self._side_from_bias_and_sweep(evidence)
        a1 = evidence.agent1
        a2 = evidence.agent2
        a3 = evidence.agent3
        a5 = evidence.agent5
        le = a3.liquidity_event
        mc = a5.micro_confirmation
        poi = a2.selected_poi

        # ── STEP 1: HTF bias ──────────────────────────────────
        if a1.htf_bias in ("bullish", "bearish"):
            sequence["htf_bias"] = "PASS"
        else:
            return self._wait_or_reject(
                evidence, sequence, "D", 0.0,
                "HTF bias neutral or unclear — cannot build directional scenario",
                side=side,
            )

        # ── STEP 2: Liquidity sweep (MANDATORY for reversal) ──
        # P2.3: Accept both Kasper format (buyside_sweep/sellside_sweep)
        # and Agent 3 legacy format (SWEEP_BSL/SWEEP_SSL/SWEEP)
        le_upper = le.type.upper().strip() if le.type else ""
        if le_upper in ("BUYSIDE_SWEEP", "SELLSIDE_SWEEP", "SWEEP", "SWEEP_BSL", "SWEEP_SSL", "BSL", "SSL"):
            sequence["liquidity_sweep"] = "PASS"
        else:
            return self._wait_or_reject(
                evidence, sequence, "C", 50.0,
                "Reversal setup requires liquidity sweep — none detected",
                side=side,
            )

        # ── STEP 3: Reintegration (close back inside range) ───
        if le.close_back_inside or le.wick_rejection:
            sequence["reintegrated"] = "PASS"
        elif self.graded_entry:
            sequence["reintegrated"] = "FAIL"
        else:
            return self._wait_or_reject(
                evidence, sequence, "D", 35.0,
                "Sweep detected but price did not reintegrate (close back inside)",
                side=side,
            )

        # ── STEP 4: Displacement after sweep ──────────────────
        if le.displacement_after_sweep:
            sequence["displacement"] = "PASS"
        elif self.graded_entry:
            sequence["displacement"] = "FAIL"
        else:
            return self._wait_or_reject(
                evidence, sequence, "C", 60.0,
                "Sweep valid but no displacement after sweep — wait for momentum confirmation",
                side=side,
            )

        # ── STEP 5: Structure shift (BOS / CHoCH) ─────────────
        # Primary: Agent 5 micro confirmation (close_breaks_structure, trigger_type)
        # P2.3 fallback: Agent 1 HTF context (last_htf_bos, last_htf_choch)
        # — if HTF already shows BOS/CHoCH, the structure shift is confirmed
        # Note: Agent 5 produces UPPERCASE trigger types (MICRO_CHOCH, MICRO_BOS)
        _tt_upper = mc.trigger_type.upper().strip() if mc.trigger_type else ""
        if mc.close_breaks_structure or _tt_upper in ("MICRO_CHOCH", "MICRO_BOS", "CHOCH", "BOS"):
            sequence["structure_shift"] = "PASS"
        elif a1.last_htf_bos or a1.last_htf_choch:
            sequence["structure_shift"] = "PASS"
        elif self.graded_entry:
            sequence["structure_shift"] = "FAIL"
        else:
            return self._wait_or_reject(
                evidence, sequence, "C", 65.0,
                "No BOS/CHoCH confirming structure shift after sweep",
                side=side,
            )

        # ── STEP 6: Tradable POI ──────────────────────────────
        if poi.tradable and poi.freshness not in ("deeply_mitigated", "MITIGATED"):
            sequence["poi"] = "PASS"
        else:
            reason = "No tradable POI"
            if not poi.tradable:
                reason = "POI present but not tradable (quality/freshness insufficient)"
            elif poi.freshness in ("deeply_mitigated", "MITIGATED"):
                reason = f"POI is {poi.freshness} — deeply mitigated, no entry"
            return self._wait_or_reject(evidence, sequence, "C", 65.0, reason, side=side)

        # ── STEP 7: Micro confirmation ────────────────────────
        # Primary: Agent 5 explicitly confirms (mc.confirmed)
        # P3 improvement (doctrine-safe): CHoCH/BOS fallback now requires
        #   close_breaks_structure=True — a structure break that doesn't
        #   close beyond the prior swing point is a trap, not confirmation.
        #   This ADDDS a quality requirement without lowering any threshold.
        # P2.3 fallback: Agent 5 detected a CHoCH/BOS trigger on M1
        #   (trigger_type = micro_choch/micro_bos/choch/bos) even if
        #   Agent 5 rejected it due to "no sweep" — because Kasper
        #   Steps 1-2 already validated the sweep exists. A CHoCH on
        #   M1 that aligns with the already-proven sweep + HTF bias
        #   IS valid micro confirmation of the reversal.
        if mc.confirmed:
            sequence["micro_confirmation"] = "PASS"
        elif (
            mc.trigger_type.upper() in ("MICRO_CHOCH", "MICRO_BOS", "CHOCH", "BOS")
            and sequence.get("liquidity_sweep") == "PASS"
            and mc.close_breaks_structure
        ):
            sequence["micro_confirmation"] = "PASS"
        elif self.graded_entry:
            sequence["micro_confirmation"] = "FAIL"
        else:
            return self._wait_or_reject(
                evidence, sequence, "C", 70.0,
                "No micro confirmation on entry timeframe — wait for trigger",
                side=side,
            )

        # ── STEP 8: Risk precheck ─────────────────────────────
        if mc.rr_estimate is not None and mc.rr_estimate >= 1.5:
            sequence["risk_precheck"] = "PASS"
        else:
            rr_str = f"{mc.rr_estimate:.1f}" if mc.rr_estimate else "missing"
            return self._wait_or_reject(
                evidence, sequence, "C", 70.0,
                f"RR estimate {rr_str} below minimum 1.5",
                side=side,
            )

        # ── All gates passed ──────────────────────────────────
        score = self._score_sequence(sequence)
        grade = self._grade_from_score(score)

        # P3: C_CONFIRMED upgrade — when all 8 gates pass but score
        # lands in C range (50-70), the setup still has valid structure.
        # Upgrade to C_CONFIRMED (ENTER_REDUCED at 0.25% total risk).
        # This is doctrine-safe because:
        #   1. All 8 Kasper gates passed (structure is valid)
        #   2. Reduced risk (0.25% vs 0.50% for B)
        #   3. Generic C with failed gates still WAIT/REJECT
        #   4. Risk per leg = 0.125% — extremely conservative
        decision: DecisionRecommendation
        if self.graded_entry:
            # Graded model. Validity gates (htf, sweep, poi, RR) already passed
            # as hard vetoes above (60-pt base). Confirmation gates are scored.
            # Enter at grade >= B (score >= 70): that requires real confirmation
            # — one 15-pt gate (displacement OR structure_shift) or two 5-pt
            # gates — never a bare sweep-into-POI. C/D -> WAIT (the trade manager
            # rejects C/D anyway, so this keeps the contract consistent).
            if grade in ("A_PLUS", "A", "B"):
                decision = "ENTER_ELIGIBLE"
            else:
                decision = "WAIT"
        elif grade in ("A_PLUS", "A"):
            decision = "ENTER_ELIGIBLE"
        elif grade == "B":
            decision = "WAIT"
        elif grade == "C" and self._all_gates_pass(sequence):
            # P3: all 8 gates passed → C_CONFIRMED
            grade = "C_CONFIRMED"
            decision = "ENTER_ELIGIBLE"
        else:
            decision = "REJECT"

        # Build scenario identity
        identity = build_kasper_scenario_identity(
            evidence, "REVERSAL", side,
            candle_timestamp=candle_timestamp,
            action="EVALUATE",
        )

        return KasperScenarioResult(
            scenario_id=identity.scenario_id,
            scenario_key=identity.scenario_key,
            decision_id=identity.decision_id,
            side=side,
            scenario_type="liquidity_sweep_reversal",
            story=self.build_trade_story(evidence, sequence, side),
            sequence=sequence,
            grade=grade,
            score=score,
            decision_recommendation=decision,
            blocking_reason=None if decision == "ENTER_ELIGIBLE" else "Scenario incomplete – see missing_confluence",
            missing_confluence=None if decision == "ENTER_ELIGIBLE" else self._missing_from_sequence(sequence),
            entry_reason="Micro confirmation after sweep + displacement + POI retest with valid structure shift",
            invalidation_reason=f"Invalid beyond sweep extreme or POI {poi.type} boundary",
            target_reason="Target next opposing liquidity pool",
        )

    def evaluate_kasper_continuation(
        self,
        evidence: KasperEvidenceBundle,
        *,
        candle_timestamp: Optional[str] = None,
    ) -> KasperScenarioResult:
        """Evaluate a BOS-continuation setup (strict fallback model).

        P2.2: Continuation is WAIT_ONLY — KASPER_WEIGHTS do not cover
        continuation-specific keys (continuation_bos, continuation_poi).
        This prevents continuation from being scored as ENTER_ELIGIBLE
        without dedicated weights and tests.

        Continuation requires:
            HTF bias → BOS/displacement → POI created by that impulse
            → micro confirmation → RR ≥ 1.5
        """
        sequence: Dict[str, PassFail] = {
            "htf_bias": "UNKNOWN",
            "continuation_bos": "UNKNOWN",
            "continuation_poi": "UNKNOWN",
            "micro_confirmation": "UNKNOWN",
            "risk_precheck": "UNKNOWN",
        }

        side = self._side_from_bias_and_sweep(evidence)
        a1 = evidence.agent1
        a2 = evidence.agent2
        mc = evidence.agent5.micro_confirmation
        poi = a2.selected_poi

        # HTF bias is mandatory
        if a1.htf_bias not in ("bullish", "bearish"):
            return self._wait_or_reject(
                evidence, sequence, "D", 0.0,
                "Continuation requires clear HTF bias",
                scenario_type="bos_continuation", side=side,
            )

        sequence["htf_bias"] = "PASS"

        # Continuation needs BOS or displacement in the direction of bias
        if mc.close_breaks_structure or mc.trigger_type in ("micro_choch", "micro_bos", "choch", "bos"):
            sequence["continuation_bos"] = "PASS"
        else:
            return self._wait_or_reject(
                evidence, sequence, "C", 60.0,
                "Continuation requires BOS or displacement in bias direction",
                scenario_type="bos_continuation", side=side,
            )

        # POI created by the impulse
        if poi.tradable and poi.created_by_displacement:
            sequence["continuation_poi"] = "PASS"
        elif poi.tradable and poi.created_by_bos_or_choch:
            sequence["continuation_poi"] = "PASS"
        else:
            return self._wait_or_reject(
                evidence, sequence, "C", 65.0,
                "No tradable POI created by the continuation impulse",
                scenario_type="bos_continuation", side=side,
            )

        # Micro confirmation
        if mc.confirmed:
            sequence["micro_confirmation"] = "PASS"
        else:
            return self._wait_or_reject(
                evidence, sequence, "C", 70.0,
                "No micro confirmation for continuation entry",
                scenario_type="bos_continuation", side=side,
            )

        # Risk precheck
        if mc.rr_estimate is not None and mc.rr_estimate >= 1.5:
            sequence["risk_precheck"] = "PASS"
        else:
            rr_str = f"{mc.rr_estimate:.1f}" if mc.rr_estimate else "missing"
            return self._wait_or_reject(
                evidence, sequence, "C", 70.0,
                f"RR {rr_str} below minimum 1.5 for continuation",
                scenario_type="bos_continuation", side=side,
            )

        score = self._score_sequence(sequence)
        grade = self._grade_from_score(score)

        if _v2_enabled():
            # V2: continuation trades WITH the HTF bias (not the fade side),
            # and is decidable by grade like the premium model.
            side = "BUY" if a1.htf_bias == "bullish" else "SELL"
            if grade in ("A_PLUS", "A"):
                decision: DecisionRecommendation = "ENTER_ELIGIBLE"
            else:
                decision = "WAIT"
        else:
            # P2.2 (legacy): Continuation is WAIT_ONLY — KASPER_WEIGHTS do not
            # cover continuation-specific keys.
            decision = "WAIT"

        # Build scenario identity
        identity = build_kasper_scenario_identity(
            evidence, "CONTINUATION", side,
            candle_timestamp=candle_timestamp,
            action="EVALUATE",
        )

        return KasperScenarioResult(
            scenario_id=identity.scenario_id,
            scenario_key=identity.scenario_key,
            decision_id=identity.decision_id,
            side=side,
            scenario_type="bos_continuation",
            story=self.build_trade_story(evidence, sequence, side),
            sequence=sequence,
            grade=grade,
            score=score,
            decision_recommendation=decision,
            blocking_reason="P2.2: Continuation is WAIT_ONLY — dedicated weights and tests required before tradable",
            missing_confluence=self._missing_from_sequence(sequence),
            entry_reason="Continuation BOS + POI created by displacement + micro confirmation",
            invalidation_reason="Invalid beyond structure break extreme",
            target_reason="Next opposing liquidity in trend direction",
        )

    # ── story builder ──────────────────────────────────────────────

    def build_trade_story(
        self,
        evidence: KasperEvidenceBundle,
        sequence: Dict[str, str],
        side: str,
    ) -> str:
        """Build a human-readable market story from the evidence and sequence."""
        a1 = evidence.agent1
        a3 = evidence.agent3
        a2 = evidence.agent2
        a5 = evidence.agent5
        le = a3.liquidity_event
        mc = a5.micro_confirmation
        poi = a2.selected_poi

        parts: List[str] = []

        # HTF context
        parts.append(
            f"HTF bias is {a1.htf_bias} (structure: {a1.structure_state}, "
            f"draw on {a1.draw_on_liquidity})."
        )

        # Liquidity event
        if le.type != "none":
            parts.append(
                f"Liquidity event: {le.type}"
                + (f" at {le.swept_level}" if le.swept_level else "")
                + (", reintegrated" if le.close_back_inside else "")
                + (", displacement confirmed" if le.displacement_after_sweep else "")
                + "."
            )
        else:
            parts.append("No liquidity sweep detected.")

        # POI
        if poi.type != "none":
            parts.append(
                f"POI: {poi.type}"
                + (f" [{poi.low}-{poi.high}]" if poi.low and poi.high else "")
                + f", freshness={poi.freshness}"
                + (", tradable" if poi.tradable else ", not tradable")
                + "."
            )
        else:
            parts.append("No tradable POI identified.")

        # Micro confirmation
        parts.append(
            f"Micro: trigger={mc.trigger_type}"
            + (", confirmed" if mc.confirmed else ", not confirmed")
            + (f", RR={mc.rr_estimate:.1f}" if mc.rr_estimate else "")
            + "."
        )

        # Sequence summary
        passed = [k for k, v in sequence.items() if v == "PASS"]
        failed = [k for k, v in sequence.items() if v not in ("PASS", "UNKNOWN")]
        parts.append(
            f"Sequence passed {len(passed)}/{len(sequence)} gates"
            + (f", failed: {', '.join(failed)}" if failed else "")
            + "."
        )

        # Decision side
        parts.append(f"Recommended side: {side}.")

        return " ".join(parts)

    # ── veto logic ─────────────────────────────────────────────────

    def _hard_veto(self, evidence: KasperEvidenceBundle) -> Optional[str]:
        """Return the hard veto reason, or None if all gates pass.

        Checks (in order): news veto, specific session blocks (asia, friday,
        spread), generic session veto, cooldown, daily limiter.

        Specific checks come before the generic _is_hard_block to produce
        precise veto reasons for debugging and audit.
        """
        a6 = evidence.agent6
        a7 = evidence.agent7

        # ── News veto ──────────────────────────────────────────
        if a6.veto or a6.high_impact_active or not a6.news_safe:
            return f"NEWS_VETO: {a6.invalid_reason or 'high-impact news active'}"

        # ── Specific session blocks (precise reasons first) ────
        if a7.asia_block:
            return "ASIA_BLOCK: Tokyo/Asia session — no trading"

        if a7.friday_halt:
            return "FRIDAY_HALT: No new positions on Friday"

        if not a7.spread_safe:
            return "SPREAD_UNSAFE: Spread regime unsafe for entry"

        # ── Generic session veto (catch-all after specifics) ───
        if a7.veto:
            return f"SESSION_VETO: {a7.invalid_reason or 'session blocked'}"

        # ── Cooldown active ────────────────────────────────────
        if a7.cooldown_active:
            return "COOLDOWN_ACTIVE: Mandatory pause between trades"

        # ── Daily limiter exceeded ─────────────────────────────
        if a7.daily_trade_count >= 2:
            return f"DAILY_LIMIT_EXCEEDED: {a7.daily_trade_count} trades already today"

        return None

    def _is_hard_block(self, a7: Agent7SessionContext) -> bool:
        """Check if the session is hard-blocked (structural veto)."""
        return a7.asia_block or a7.friday_halt or a7.veto or not a7.spread_safe

    # ── side logic ─────────────────────────────────────────────────

    def _side_from_bias_and_sweep(self, evidence: KasperEvidenceBundle) -> str:
        """Determine trade side from sweep direction and HTF bias."""
        le_type = evidence.agent3.liquidity_event.type
        le_upper = le_type.upper().strip() if le_type else ""

        # Sellside sweep = liquidity below taken = reversal BUY
        if le_upper in ("SELLSIDE_SWEEP", "SELLSIDE", "SWEEP_SSL", "SSL"):
            return "BUY"

        # Buyside sweep = liquidity above taken = reversal SELL
        if le_upper in ("BUYSIDE_SWEEP", "BUYSIDE", "SWEEP_BSL", "BSL"):
            return "SELL"

        # Fallback to HTF bias
        if evidence.agent1.htf_bias == "bullish":
            return "BUY"
        if evidence.agent1.htf_bias == "bearish":
            return "SELL"

        return "NONE"

    # ── scoring ────────────────────────────────────────────────────

    def _score_sequence(self, sequence: Dict[str, str]) -> float:
        """Score a sequence using Kasper weights. Only PASS counts."""
        total = 0.0
        weights = (
            KASPER_CONTINUATION_WEIGHTS
            if "continuation_bos" in sequence
            else KASPER_WEIGHTS
        )
        for key, weight in weights.items():
            if sequence.get(key) == "PASS":
                total += weight
        # If the sequence has extra keys not in weights, count them at 0 weight
        return min(total, 100.0)

    def _grade_from_score(self, score: float) -> Grade:
        """Map a numeric score to a Kasper grade."""
        if score >= GRADE_A_PLUS_MIN:
            return "A_PLUS"
        if score >= GRADE_A_MIN:
            return "A"
        if score >= GRADE_B_MIN:
            return "B"
        if score >= GRADE_C_MIN:
            return "C"
        return "D"

    @staticmethod
    def _all_gates_pass(sequence: dict) -> bool:
        """P3: Check if all 8 Kasper gates passed.

        C_CONFIRMED upgrade requires all gates to pass before granting
        the reduced-risk (0.25%) executable grade.
        """
        required_gates = (
            "htf_bias", "liquidity_sweep", "reintegrated",
            "displacement", "structure_shift", "poi",
            "micro_confirmation", "risk_precheck",
        )
        return all(sequence.get(gate) == "PASS" for gate in required_gates)

    # ── scenario id ────────────────────────────────────────────────

    def _build_scenario_id(self, evidence: KasperEvidenceBundle, family: str, side: str) -> str:
        """Build a unique, hashable scenario id."""
        ts = evidence.timestamp or "NO_TS"
        poi_type = evidence.agent2.selected_poi.type
        le_type = evidence.agent3.liquidity_event.type

        raw = f"KASPER_{family}_{side}_{ts}_{poi_type}_{le_type}"
        short_hash = hashlib.sha256(raw.encode()).hexdigest()[:12]
        return f"KASPER_{family}_{side}_{short_hash}"

    # ── result constructors ────────────────────────────────────────

    def _reject(
        self,
        evidence: KasperEvidenceBundle,
        reason: str,
        *,
        candle_timestamp: Optional[str] = None,
    ) -> KasperScenarioResult:
        """Build a hard-veto REJECT result."""
        side = self._side_from_bias_and_sweep(evidence)
        effective_side = side if side != "NONE" else "NONE"
        identity = build_kasper_scenario_identity(
            evidence, "VETO", effective_side,
            candle_timestamp=candle_timestamp,
            action="REJECT",
        )
        return KasperScenarioResult(
            scenario_id=identity.scenario_id,
            scenario_key=identity.scenario_key,
            decision_id=identity.decision_id,
            side=effective_side,
            scenario_type="hard_veto",
            story=f"Hard veto blocked scenario: {reason}",
            sequence={"hard_veto": "FAIL"},
            grade="D",
            score=0.0,
            decision_recommendation="REJECT",
            blocking_reason=reason,
            missing_confluence=reason,
        )

    def _wait_or_reject(
        self,
        evidence: KasperEvidenceBundle,
        sequence: Dict[str, str],
        grade: Grade,
        score: float,
        reason: str,
        scenario_type: str = "liquidity_sweep_reversal",
        side: Optional[str] = None,
        *,
        candle_timestamp: Optional[str] = None,
    ) -> KasperScenarioResult:
        """Build a WAIT or REJECT result for an incomplete scenario."""
        decision: DecisionRecommendation = "WAIT" if grade in ("B", "C") else "REJECT"
        effective_side = side or self._side_from_bias_and_sweep(evidence)
        identity = build_kasper_scenario_identity(
            evidence, "INCOMPLETE", effective_side,
            candle_timestamp=candle_timestamp,
            action="WAIT_OR_REJECT",
        )
        return KasperScenarioResult(
            scenario_id=identity.scenario_id,
            scenario_key=identity.scenario_key,
            decision_id=identity.decision_id,
            side=effective_side,
            scenario_type=scenario_type,
            story=self.build_trade_story(evidence, sequence, effective_side)
            + f" Missing: {reason}.",
            sequence=sequence,
            grade=grade,
            score=score,
            decision_recommendation=decision,
            blocking_reason=reason,
            missing_confluence=reason,
        )

    def _best_explanation(
        self,
        reversal: KasperScenarioResult,
        continuation: KasperScenarioResult,
    ) -> KasperScenarioResult:
        """Return the best explanation when neither model is ENTER_ELIGIBLE."""
        # Prefer higher score; if tie, prefer reversal (premium model)
        if reversal.score >= continuation.score:
            return reversal
        return continuation

    def _reversal_passed_structural_gates(self, sequence: Dict[str, str]) -> bool:
        """Return True if the reversal sequence passed the structural gates
        (sweep, reintegration, displacement, structure_shift). If these fail,
        the structural foundation is missing — continuation shouldn't paper over it."""
        structural_keys = ("liquidity_sweep", "reintegrated", "displacement", "structure_shift")
        return all(sequence.get(k) == "PASS" for k in structural_keys)

    def _missing_from_sequence(self, sequence: Dict[str, str]) -> str:
        """Extract a human-readable missing-confluence string from sequence."""
        missing = [k for k, v in sequence.items() if v not in ("PASS",)]
        if missing:
            return f"Missing: {', '.join(missing)}"
        return "Unknown missing confluence"


# ── scenario identity builder ───────────────────────────────────────

def build_kasper_scenario_identity(
    evidence: KasperEvidenceBundle,
    family: str,
    side: str,
    candle_timestamp: Optional[str] = None,
    action: str = "EVALUATE",
) -> KasperScenarioIdentity:
    """Build a unique scenario identity for a single trading opportunity.

    scenario_key: stable across candles for the same real opportunity.
        Components: symbol + family + side + sweep_type + swept_level_rounded
        + sweep_time + poi_type + poi_low_rounded + poi_high_rounded + trigger_type.
    decision_id: unique per candle/decision.
        Components: scenario_key + candle_timestamp + action.
    scenario_id: backward-compatible hash for legacy consumers.
    """
    a3 = evidence.agent3
    a2 = evidence.agent2
    a5 = evidence.agent5
    le = a3.liquidity_event
    poi = a2.selected_poi
    mc = a5.micro_confirmation
    symbol = evidence.symbol or "XAUUSD"
    # decision_id uses candle_timestamp (current candle) for per-candle uniqueness;
    # scenario_key uses evidence timestamp (opportunity anchor) for stability
    decision_ts = candle_timestamp or evidence.timestamp or "NO_TS"
    scenario_ts = evidence.timestamp or candle_timestamp or "NO_TS"

    # Round price components for stability (0.1 XAUUSD granularity)
    swept_level = (
        round(le.swept_level, 1) if le.swept_level is not None else "NO_SWEPT"
    )
    poi_low = round(poi.low, 1) if poi.low is not None else "NO_POI_LOW"
    poi_high = round(poi.high, 1) if poi.high is not None else "NO_POI_HIGH"
    sweep_time = le.sweep_time or "NO_SWEEP_TIME"

    # Build scenario_key components (stable identity)
    key_raw = (
        f"{symbol}|{family}|{side}|{le.type}|{swept_level}|{sweep_time}"
        f"|{poi.type}|{poi_low}|{poi_high}|{mc.trigger_type}"
    )
    scenario_key = (
        f"KASPER_{family}_{side}_"
        + hashlib.sha256(key_raw.encode()).hexdigest()[:16]
    )

    # Build decision_id (unique per candle — uses decision_ts)
    dec_raw = f"{scenario_key}|{decision_ts}|{action}"
    decision_id = "KASPER_DEC_" + hashlib.sha256(dec_raw.encode()).hexdigest()[:16]

    # Backward-compatible scenario_id
    raw_id = f"KASPER_{family}_{side}_{scenario_ts}_{poi.type}_{le.type}"
    short_hash = hashlib.sha256(raw_id.encode()).hexdigest()[:12]
    scenario_id = f"KASPER_{family}_{side}_{short_hash}"

    return KasperScenarioIdentity(
        scenario_key=scenario_key,
        decision_id=decision_id,
        scenario_id=scenario_id,
        identity_components={
            "symbol": symbol,
            "family": family,
            "side": side,
            "sweep_type": le.type,
            "swept_level": str(swept_level),
            "sweep_time": str(sweep_time),
            "poi_type": poi.type,
            "poi_low": str(poi_low),
            "poi_high": str(poi_high),
            "trigger_type": mc.trigger_type,
            "candle_timestamp": decision_ts,
        },
    )


# ── error tracking ──────────────────────────────────────────────────

_KASPER_ERROR_COUNTER: int = 0
_KASPER_LAST_ERROR: Optional[Dict[str, Any]] = None


def reset_kasper_error_counter() -> None:
    """Reset the global Kasper error counter (for replay/test isolation)."""
    global _KASPER_ERROR_COUNTER, _KASPER_LAST_ERROR
    _KASPER_ERROR_COUNTER = 0
    _KASPER_LAST_ERROR = None


def get_kasper_error_count() -> int:
    return _KASPER_ERROR_COUNTER


def get_kasper_last_error() -> Optional[Dict[str, Any]]:
    return _KASPER_LAST_ERROR


# ── convenience function ──────────────────────────────────────────────

def evaluate_kasper_scenario(evidence: KasperEvidenceBundle) -> KasperScenarioResult:
    """Convenience wrapper: evaluate a KasperEvidenceBundle through the engine.

    Catches all exceptions and returns a REJECT result with kasper_error set.
    Never allows a trade to open on an engine error.
    """
    global _KASPER_ERROR_COUNTER, _KASPER_LAST_ERROR
    engine = KasperScenarioEngine()
    try:
        return engine.evaluate(evidence)
    except Exception as exc:
        _KASPER_ERROR_COUNTER += 1
        import traceback
        from datetime import datetime, timezone
        error_info = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exception_class": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        }
        _KASPER_LAST_ERROR = error_info
        side = engine._side_from_bias_and_sweep(evidence)
        return KasperScenarioResult(
            scenario_id="KASPER_ERROR",
            scenario_key="KASPER_ERROR",
            decision_id="KASPER_ERROR",
            side=side,
            scenario_type="engine_error",
            story=f"Kasper engine error: {type(exc).__name__}: {exc}",
            sequence={"engine_error": "FAIL"},
            grade="D",
            score=0.0,
            decision_recommendation="REJECT",
            blocking_reason=f"ENGINE_ERROR: {type(exc).__name__}",
            missing_confluence=f"Kasper engine raised {type(exc).__name__}",
            kasper_error=str(exc),
        )

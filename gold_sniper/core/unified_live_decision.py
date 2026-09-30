"""Unified live decision adapter — Voie B (encapsulation).

Reads agent results from the live BlackBoard, builds an EvidenceBundle using
the SAME modules as replay/decision_pipeline.py, calls the SAME KasperScenarioEngine
+ ProfessionalDecisionEngine, and applies the SAME ENTER_FULL-by-grade promotion.

Activated via env var GS_UNIFIED_PIPELINE=1. When off, the legacy orchestrator
vote path runs unchanged (rollback safety).

Architecture rule:
    This module is PURE decision logic. It never sends orders, never touches
    the broker, and never modifies blackboard state except for diagnostic fields.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from replay.evidence_builder import (
    build_evidence_bundle_from_blackboard,
    validate_evidence_bundle,
    bundle_to_json_dict,
)
from strategy.professional_decision_engine import evaluate_professional_decision
from strategy.readiness_risk_gate_contract import evaluate_readiness_risk_gate
from strategy.kasper_contracts import build_kasper_evidence_bundle
from strategy.kasper_scenario_engine import evaluate_kasper_scenario, set_graded_entry
from strategy.risk_allocator import allocate_risk
from strategy.contracts import DecisionAction, SetupGrade, SetupType


# ── Graded-entry activation ──────────────────────────────────────────────
# Must be called once at module load to ensure Kasper uses graded mode.
# The replay does this in ReplayDecisionPipeline.__init__() — we mirror it here.
_GRADED_ENV = os.environ.get("GOLD_SNIPER_KASPER_GRADED", "").strip().lower()
if _GRADED_ENV in ("1", "true", "yes", "on"):
    set_graded_entry(True)

# Force graded-entry ON by default for unified pipeline (the validated config uses it).
# This is safe: graded mode is what was validated in the 8 replays.
if not _GRADED_ENV:
    set_graded_entry(True)

def unified_pipeline_enabled() -> bool:
    """Check if the unified pipeline is active. Reads from env."""
    return os.environ.get("GS_UNIFIED_PIPELINE", "0").strip().lower() in (
        "1", "true", "yes", "on"
    )


def grade_scaled_risk_pct(raw_grade: Any) -> float:
    """Return live risk percent for a Kasper grade: GRADE_RISK_PCT * GS_RISK_SCALE."""
    try:
        scale = float(os.environ.get("GS_RISK_SCALE", "1.0") or "1.0")
    except (TypeError, ValueError):
        scale = 1.0
    grade = str(raw_grade or "D").upper().replace("+", "_PLUS")
    grade_risk = {
        "A_PLUS": 1.00,
        "A": 0.75,
        "B": 0.50,
        "C_CONFIRMED": 0.25,
        "C": 0.0,
        "D": 0.0,
    }
    return round(float(grade_risk.get(grade, 0.0)) * scale, 6)


def _ensure_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def unified_live_decision(
    blackboard: Any,
    *,
    candle: dict[str, Any] | None = None,
    symbol: str = "XAUUSD",
) -> dict[str, Any]:
    """Produce a live trading decision using the validated Kasper/PDE pipeline.

    This function mirrors ReplayDecisionPipeline.__call__() but reads agent
    results from the live blackboard instead of running replay agent runners.

    Args:
        blackboard: The live BlackBoard instance.
        candle: Current candle dict (optional, for timestamp/price context).
        symbol: Trading symbol.

    Returns:
        A decision dict with the same shape as the legacy orchestrator output,
        enriched with Kasper/PDE fields for downstream consumers.
    """
    # ── Resolve timestamp ──────────────────────────────────────────────
    ts_utc: str | None = None
    if candle and "time" in candle:
        ct = candle["time"]
        ts_utc = ct.isoformat() if hasattr(ct, "isoformat") else str(ct)
    if not ts_utc:
        ts_utc = datetime.now(timezone.utc).isoformat()

    # ── 1. Build EvidenceBundle from live agent_results ─────────────────
    # This is the SAME function called by ReplayDecisionPipeline.
    bundle = build_evidence_bundle_from_blackboard(
        blackboard,
        symbol=symbol,
        ts_utc=ts_utc,
    )
    validation_errors = validate_evidence_bundle(bundle)

    # ── 2. Run PDE ──────────────────────────────────────────────────────
    decision_result = evaluate_professional_decision(bundle)
    p1_payload = _p1_decision_payload(bundle, decision_result, validation_errors)

    # ── 3. Run Kasper scenario engine ───────────────────────────────────
    _bundle_dict = bundle_to_json_dict(bundle)
    _bundle_ctx = _bundle_dict.get("context") if isinstance(_bundle_dict.get("context"), dict) else {}
    _bundle_poi = _bundle_dict.get("poi") if isinstance(_bundle_dict.get("poi"), dict) else {}
    _bundle_liq = _bundle_dict.get("liquidity") if isinstance(_bundle_dict.get("liquidity"), dict) else {}
    _bundle_micro = _bundle_dict.get("micro") if isinstance(_bundle_dict.get("micro"), dict) else {}
    _bundle_news = _bundle_dict.get("news") if isinstance(_bundle_dict.get("news"), dict) else {}
    _bundle_sess = _bundle_dict.get("session") if isinstance(_bundle_dict.get("session"), dict) else {}

    # Inject primary_regime from agent_1
    try:
        agent1_result = blackboard.read_sync("agent_results.agent_1")
        if agent1_result and hasattr(agent1_result, "payload") and agent1_result.payload:
            regime = agent1_result.payload.get("primary_regime")
            if regime:
                _bundle_ctx = dict(_bundle_ctx)
                _bundle_ctx.setdefault("primary_regime", regime)
    except Exception:
        pass

    try:
        kasper_bundle = build_kasper_evidence_bundle(
            context=_bundle_ctx,
            poi=_bundle_poi,
            liquidity=_bundle_liq,
            timing=_bundle_dict.get("timing") if isinstance(_bundle_dict.get("timing"), dict) else None,
            micro=_bundle_micro,
            news=_bundle_news,
            session=_bundle_sess,
            symbol=symbol,
            timestamp=p1_payload.get("timestamp"),
            extra_sweep_evidence=True,
        )
        kasper_result = evaluate_kasper_scenario(kasper_bundle)

        p1_payload.update({
            "scenario_id": kasper_result.scenario_id,
            "scenario_key": kasper_result.scenario_key,
            "decision_id": kasper_result.decision_id,
            "scenario_type": kasper_result.scenario_type,
            "market_story": kasper_result.story,
            "sequence_pass_fail": kasper_result.sequence,
            "missing_confluence": kasper_result.missing_confluence,
            "entry_reason": kasper_result.entry_reason,
            "invalidation_reason": kasper_result.invalidation_reason,
            "target_reason": kasper_result.target_reason,
            "kasper_grade": kasper_result.grade,
            "kasper_score": kasper_result.score,
            "kasper_side": kasper_result.side,
            "kasper_error": kasper_result.kasper_error,
            "kasper_rr_estimate": kasper_bundle.agent5.micro_confirmation.rr_estimate,
            "kasper_decision_recommendation": kasper_result.decision_recommendation,
            "hard_veto_reason": kasper_result.blocking_reason,
        })
    except Exception as _kexc:
        import traceback as _tb
        _exc_name = type(_kexc).__name__
        _exc_msg = str(_kexc)
        p1_payload.update({
            "scenario_id": None,
            "scenario_key": None,
            "decision_id": None,
            "scenario_type": f"kasper_error:{_exc_name}",
            "market_story": "Kasper scenario engine error — fallback to PDE score-only decision.",
            "sequence_pass_fail": {},
            "missing_confluence": f"Kasper engine exception: {_exc_name}: {_exc_msg}"[:200],
            "entry_reason": None,
            "invalidation_reason": None,
            "target_reason": None,
            "kasper_grade": "D",
            "kasper_score": 0.0,
            "kasper_side": "NONE",
            "kasper_error": f"{_exc_name}: {_exc_msg}"[:200],
            "kasper_rr_estimate": None,
            "kasper_decision_recommendation": "REJECT",
            "hard_veto_reason": None,
        })

    # ── 4. P2.1 Alignment Bridge — promote ENTER_FULL by grade ──────────
    # Same logic as in ReplayDecisionPipeline._p1_decision_payload()
    _kasper_rec = str(p1_payload.get("kasper_decision_recommendation") or "").upper()
    _kasper_grade = str(p1_payload.get("kasper_grade") or "D").upper()
    _pde_decision = str(p1_payload.get("decision") or "REJECT").upper()
    _hard_veto = bool(p1_payload.get("hard_veto", False))
    _replay_invalid = bool(p1_payload.get("replay_invalid", False))
    _kasper_seq = p1_payload.get("sequence_pass_fail") if isinstance(p1_payload.get("sequence_pass_fail"), dict) else {}
    _risk_precheck_pass = _kasper_seq.get("risk_precheck") == "PASS"

    _kasper_enters = _kasper_rec == "ENTER_ELIGIBLE"
    _pde_blocking = _pde_decision in {"WATCH_ONLY", "WAIT_FOR_TRIGGER", "WAIT_FOR_BETTER_PRICE"}
    _pde_not_entering = _pde_decision not in {"ENTER_FULL", "ENTER_REDUCED"}
    _grade_executable = _kasper_grade in {"A_PLUS", "A", "B"}

    # P2.3: overridable PDE veto codes
    _kasper_overridable_vetos = {
        "TRIGGER_OUTSIDE_POI", "POI_MISSING", "POI_INVALID_SHAPE",
        "POI_QUALITY_WATCH_C", "POI_QUALITY_WATCH_D",
        "MICRO_TRIGGER_TYPE_MISSING", "MICRO_PAYLOAD_MISSING",
        "MICRO_CONFIRMATION_WATCH_C", "MICRO_CONFIRMATION_WATCH_D",
        "DISPLACEMENT_MISSING", "RECLAIM_OR_ACCEPTANCE_MISSING",
        "RETEST_MISSING", "MICRO_MISSING",
    }
    _pde_veto_code = str(p1_payload.get("veto_code") or "").upper()
    _pde_veto_overridable = _pde_veto_code in _kasper_overridable_vetos

    _should_promote = (
        _kasper_enters and _grade_executable and _risk_precheck_pass
        and not _replay_invalid
        and _pde_not_entering
        and (not _hard_veto or _pde_veto_overridable)
    )

    if _should_promote:
        p1_payload["decision"] = "ENTER_FULL"
        p1_payload["kasper_pde_alignment_status"] = "PROMOTED"
        p1_payload["kasper_pde_alignment_reason"] = (
            f"Kasper {_kasper_grade} ENTER_ELIGIBLE with valid RR overrides "
            f"legacy PDE {_pde_decision} — scorecard threshold bypassed by Kasper authority"
        )
        p1_payload["setup_grade"] = _kasper_grade
        p1_payload["trade_open_source"] = "KASPER_AUTHORITY"

        # Re-allocate risk for the promoted grade
        try:
            _grade_enum = SetupGrade(_kasper_grade)
            _new_risk = allocate_risk(
                action=DecisionAction.ENTER_FULL,
                grade=_grade_enum,
                evidence=bundle,
                capital=100.0,
                enter_eligible=True,
            )
            _rp_dict = _new_risk.to_dict()
            # Fix POI_REACTION classification
            _rp_meta = _rp_dict.get("metadata") or {}
            if _rp_meta.get("setup_type") == "POI_REACTION" and not _new_risk.allowed:
                from dataclasses import replace
                _fixed_bundle = replace(bundle, setup_type=SetupType.SWEEP_REVERSAL)
                _new_risk = allocate_risk(
                    action=DecisionAction.ENTER_FULL,
                    grade=_grade_enum,
                    evidence=_fixed_bundle,
                    capital=100.0,
                    enter_eligible=True,
                )
                _rp_dict = _new_risk.to_dict()
            p1_payload["risk_plan"] = _rp_dict
            p1_payload["risk_multiplier"] = _new_risk.risk_multiplier
            p1_payload["risk_allowed"] = _new_risk.allowed
            p1_payload["risk_reason"] = _new_risk.reason
            p1_payload["enter_eligible"] = True
            p1_payload["enter_eligibility_reason"] = "KASPER_AUTHORITY_PROMOTED"
            p1_payload["enter_eligibility_blockers"] = []
            p1_payload["grade_risk_multiplier"] = _new_risk.metadata.get("grade_risk_multiplier")
            p1_payload["effective_risk_pct"] = _new_risk.metadata.get("effective_risk_pct")
            p1_payload["risk_grade_pct"] = _new_risk.metadata.get("grade_risk_multiplier")
        except Exception:
            # Risk allocation failed — revert promotion
            p1_payload["decision"] = _pde_decision
            p1_payload["kasper_pde_alignment_status"] = "RISK_ALLOCATION_FAILED"
            p1_payload["kasper_pde_alignment_reason"] = (
                f"Kasper ENTER_ELIGIBLE but risk allocation failed — kept PDE {_pde_decision}"
            )

    # ── 5. Build orchestrator-compatible output ──────────────────────────
    # Extract levels from agent_5 payload
    a5_data = {}
    try:
        a5_raw = blackboard.read_sync("agents.agent_5") or {}
    except Exception:
        a5_raw = {}
    try:
        a5_result = blackboard.read_sync("agent_results.agent_5")
        if a5_result and hasattr(a5_result, "payload") and a5_result.payload:
            a5_payload = a5_result.payload
            a5_data = {
                "entry_price": a5_payload.get("entry_price"),
                "sl_price": a5_payload.get("sl_price") or a5_payload.get("stop_loss"),
                "tp1_price": a5_payload.get("tp1_price") or a5_payload.get("tp1"),
                "tp2_price": a5_payload.get("tp2_price") or a5_payload.get("tp2"),
            }
    except Exception:
        pass

    # Determine direction from Kasper side or PDE
    direction = (p1_payload.get("kasper_side") or p1_payload.get("side") or "NONE").upper()
    if direction == "NONE":
        bundle_side = _bundle_dict.get("side", "NONE")
        direction = str(bundle_side).upper() if bundle_side else "NONE"

    # Build legacy-compatible output shape
    decision_value = p1_payload.get("decision", "REJECT")
    setup_grade = p1_payload.get("setup_grade", "D")

    # Map PDE/Kasper decision to legacy orchestrator decision codes
    _legacy_decision = decision_value
    if decision_value in ("ENTER_FULL", "ENTER_REDUCED"):
        _legacy_decision = "EXECUTE"
    elif decision_value == "WAIT_FOR_TRIGGER":
        _legacy_decision = "WAIT"
    elif decision_value == "WAIT_FOR_BETTER_PRICE":
        _legacy_decision = "WAIT"
    elif decision_value == "WATCH_ONLY":
        _legacy_decision = "WAIT"
    elif decision_value == "REJECT":
        _legacy_decision = "REJECT"

    # Determine stars (legacy UI compatibility)
    _stars = 0
    if _legacy_decision == "EXECUTE":
        if setup_grade == "A_PLUS":
            _stars = 5
        elif setup_grade == "A":
            _stars = 4
        elif setup_grade == "B":
            _stars = 3
        elif setup_grade == "C_CONFIRMED":
            _stars = 2
    elif _legacy_decision == "WAIT":
        _stars = 3

    score = p1_payload.get("confidence_score", 0.0) * 100.0

    # Extract risk_pct from grade mapping, scaled by GS_RISK_SCALE
    _base_risk = grade_scaled_risk_pct(setup_grade)
    # Live sizing uses RiskCalculator(base 1%) * risk_modifier. The validated
    # replay sizing is grade percent scaled by GS_RISK_SCALE, not the PDE
    # modulator alone (which is usually 1.0/0.75/0.5 and would under-risk live).
    risk_modifier = _base_risk if _legacy_decision == "EXECUTE" else 0.0

    # Build signal data if executable
    entry = a5_data.get("entry_price")
    sl = a5_data.get("sl_price")
    tp1 = a5_data.get("tp1_price")
    tp2 = a5_data.get("tp2_price")

    # Fallback: use current tick
    if not entry:
        try:
            tick = blackboard.read_sync("market_data.current_tick") or {}
            entry = tick.get("ask") if direction == "BUY" else tick.get("bid")
        except Exception:
            pass

    result: dict[str, Any] = {
        # Legacy orchestrator fields
        "decision": _legacy_decision,
        "score": round(score, 1),
        "raw_score": round(score, 1),
        "stars": _stars,
        "direction": direction,
        "risk_modifier": round(risk_modifier, 3),
        "regime": _bundle_ctx.get("primary_regime", "UNKNOWN"),
        "session": _bundle_sess.get("session_name") or _bundle_sess.get("session", "UNKNOWN"),
        "strategy": "UNIFIED_KASPER_PDE",
        "strategy_min_score": 0.0,
        "strategy_exceptional_score": 92.0,
        "strategy_risk_pct": round(_base_risk, 2),
        "strategy_weight_overrides": {},
        "adaptive_weights_applied": False,
        "effective_weights": {},
        "diamond_evaluation": None,
        "reason": (
            f"UNIFIED_PIPELINE | decision={decision_value} grade={setup_grade} "
            f"kasper_rec={_kasper_rec} score={score:.1f}"
        ),
        "agent_breakdown": _build_agent_breakdown(blackboard),
        "timestamp": datetime.now(timezone.utc).isoformat(),

        # PDE/Kasper fields (for downstream consumers)
        "pde_decision": decision_value,
        "setup_grade": setup_grade,
        "setup_type": p1_payload.get("setup_type", "UNKNOWN"),
        "confidence_score": p1_payload.get("confidence_score", 0.0),
        "score_before_veto": p1_payload.get("score_before_veto", 0.0),
        "score_after_veto": p1_payload.get("score_after_veto", 0.0),
        "hard_veto": p1_payload.get("hard_veto", False),
        "veto_code": p1_payload.get("veto_code"),
        "blocked_stage": p1_payload.get("blocked_stage"),
        "replay_invalid": p1_payload.get("replay_invalid", False),
        "readiness_state": p1_payload.get("readiness_state", "UNAVAILABLE"),
        "readiness_reason": p1_payload.get("readiness_reason", ""),
        "missing_evidence": p1_payload.get("missing_evidence", []),
        "soft_issues": p1_payload.get("soft_issues", []),
        "risk_plan": p1_payload.get("risk_plan", {}),
        "risk_multiplier_kasper": p1_payload.get("risk_multiplier", 0.0),
        "required_execution_mode": p1_payload.get("required_execution_mode", "shadow_only"),
        "enter_eligible": p1_payload.get("enter_eligible", False),
        "enter_eligibility_reason": p1_payload.get("enter_eligibility_reason", ""),
        "enter_eligibility_blockers": p1_payload.get("enter_eligibility_blockers", []),
        "grade_risk_multiplier": p1_payload.get("grade_risk_multiplier"),
        "effective_risk_pct": p1_payload.get("effective_risk_pct"),
        "live_effective_risk_pct": round(_base_risk, 6),
        "risk_allowed": p1_payload.get("risk_allowed", False),
        "risk_reason": p1_payload.get("risk_reason", "UNKNOWN"),

        # Kasper fields
        "scenario_id": p1_payload.get("scenario_id"),
        "scenario_key": p1_payload.get("scenario_key"),
        "decision_id": p1_payload.get("decision_id"),
        "scenario_type": p1_payload.get("scenario_type"),
        "market_story": (p1_payload.get("market_story") or "")[:200],
        "sequence_pass_fail": p1_payload.get("sequence_pass_fail", {}),
        "missing_confluence": p1_payload.get("missing_confluence"),
        "entry_reason": p1_payload.get("entry_reason"),
        "invalidation_reason": p1_payload.get("invalidation_reason"),
        "target_reason": p1_payload.get("target_reason"),
        "kasper_grade": p1_payload.get("kasper_grade"),
        "kasper_score": p1_payload.get("kasper_score"),
        "kasper_side": p1_payload.get("kasper_side"),
        "kasper_error": p1_payload.get("kasper_error"),
        "kasper_rr_estimate": p1_payload.get("kasper_rr_estimate"),
        "kasper_decision_recommendation": p1_payload.get("kasper_decision_recommendation"),
        "hard_veto_reason": p1_payload.get("hard_veto_reason"),
        "kasper_pde_alignment_status": p1_payload.get("kasper_pde_alignment_status", "NO_ALIGNMENT_NEEDED"),
        "kasper_pde_alignment_reason": p1_payload.get("kasper_pde_alignment_reason", ""),
        "trade_open_source": p1_payload.get("trade_open_source"),

        # Agent micro contract fields
        "micro_contract_status": _bundle_micro.get("micro_contract_status") if isinstance(_bundle_micro, dict) else None,
        "micro_contract_readiness": (_bundle_micro.get("readiness_state") if isinstance(_bundle_micro, dict) else None),
        "micro_contract_reason": (_bundle_micro.get("readiness_reason") if isinstance(_bundle_micro, dict) else None),

        # Entry levels (from agent_5)
        "entry_price": entry,
        "stop_loss": sl,
        "tp1_price": tp1,
        "tp2_price": tp2 or tp1,

        # P1 evidence (for diagnostics)
        "p1_evidence_bundle": _bundle_dict,
        "p1_evidence_validation_errors": list(validation_errors),

        # Signal fields for trade_manager compatibility
        "signal": ("BUY" if direction == "BUY" else ("SELL" if direction == "SELL" else None)),
        "take_profit": tp2 or tp1,
    }

    # Add readiness/risk gate
    try:
        gate_result = evaluate_readiness_risk_gate(result)
        result["gate_primary_blocker"] = gate_result.primary_blocker
        result["gate_blockers"] = gate_result.blockers
        result["gate_decomposition"] = gate_result.to_dict()
        result["setup_tradable"] = gate_result.setup_tradable
    except Exception:
        pass

    return result


def _p1_decision_payload(
    bundle: Any,
    decision_result: Any,
    validation_errors: list[str],
) -> dict[str, Any]:
    """Extract the P1 decision payload from PDE result — mirrors replay/decision_pipeline.py."""
    from gold_sniper.replay.evidence_builder import bundle_to_json_dict

    decision_dict = decision_result.to_dict() if hasattr(decision_result, "to_dict") else dict(decision_result.__dict__)
    bundle_dict = bundle_to_json_dict(bundle)
    score_breakdown = decision_dict.get("score_breakdown") or {}
    classification = score_breakdown.get("setup_classification") or {}
    classification_evidence = classification.get("evidence") or {}
    setup_signal_inventory = classification_evidence.get("signals", {})
    setup_candidates = classification_evidence.get("candidates", [])
    enter_eligibility = score_breakdown.get("enter_eligibility") or {}
    risk_plan = decision_dict.get("risk_plan") or {}

    return {
        "decision": decision_dict.get("decision", "REJECT"),
        "setup_grade": decision_dict.get("setup_grade", "D"),
        "side": bundle_dict.get("side"),
        "confidence_score": decision_dict.get("confidence_score", 0.0),
        "score_before_veto": decision_dict.get("score_before_veto", 0.0),
        "score_after_veto": decision_dict.get("score_after_veto", 0.0),
        "hard_veto": decision_dict.get("hard_veto", False),
        "veto_code": decision_dict.get("veto_code"),
        "blocked_stage": decision_dict.get("blocked_stage"),
        "replay_invalid": decision_dict.get("replay_invalid", False),
        "readiness_state": decision_dict.get("readiness_state", "UNAVAILABLE"),
        "readiness_reason": decision_dict.get("readiness_reason", ""),
        "readiness_by_section": decision_dict.get("readiness_by_section", {}),
        "missing_evidence": decision_dict.get("missing_evidence", []),
        "soft_issues": decision_dict.get("soft_issues", []),
        "risk_plan": decision_dict.get("risk_plan", {}),
        "risk_multiplier": decision_dict.get("risk_multiplier", 0.0),
        "required_execution_mode": decision_dict.get("required_execution_mode", "shadow_only"),
        "setup_type": score_breakdown.get("setup_type") or classification.get("setup_type") or "UNKNOWN",
        "setup_classification": classification,
        "setup_family": classification.get("family", "UNKNOWN"),
        "setup_classification_reason": classification.get("reason", "UNKNOWN"),
        "setup_classification_confidence": classification.get("confidence", 0.0),
        "enter_eligible": bool(decision_dict.get("enter_eligible", False)),
        "enter_eligibility_reason": decision_dict.get("enter_eligibility_reason") or enter_eligibility.get("reason", "UNKNOWN"),
        "enter_eligibility_blockers": decision_dict.get("enter_eligibility_blockers") or enter_eligibility.get("blockers", []),
        "enter_eligibility_checks": decision_dict.get("enter_eligibility_checks") or enter_eligibility.get("checks", {}),
        "risk_preview": decision_dict.get("risk_preview") or enter_eligibility.get("risk_preview", {}),
        "grade_risk_multiplier": (risk_plan.get("metadata") or {}).get("grade_risk_multiplier"),
        "effective_risk_pct": (risk_plan.get("metadata") or {}).get("effective_risk_pct"),
        "setup_max_risk_multiplier": (risk_plan.get("metadata") or {}).get("setup_max_risk_multiplier"),
        "risk_allowed": risk_plan.get("allowed", False),
        "risk_reason": risk_plan.get("reason", "UNKNOWN"),
        "setup_signal_inventory": setup_signal_inventory,
        "setup_candidates": setup_candidates if isinstance(setup_candidates, list) else [],
        "best_setup_candidate": _best_candidate(setup_candidates),
        "p1_evidence_bundle": bundle_dict,
        "p1_evidence_validation_errors": list(validation_errors),
        "timestamp": bundle_dict.get("ts_utc"),
        # Micro contract fields
        "micro_contract_status": (bundle_dict.get("micro") or {}).get("micro_contract_status"),
        "micro_contract_readiness": (bundle_dict.get("micro") or {}).get("readiness_state"),
        "micro_contract_reason": (bundle_dict.get("micro") or {}).get("readiness_reason"),
        "micro_contract_confirmed": (bundle_dict.get("micro") or {}).get("micro_is_confirmed"),
        "micro_contract_waiting_trigger": (bundle_dict.get("micro") or {}).get("micro_is_waiting_trigger"),
        "micro_contract_invalid": (bundle_dict.get("micro") or {}).get("micro_is_invalid"),
        "micro_contract_missing_data": (bundle_dict.get("micro") or {}).get("micro_is_missing_data"),
        "micro_contract_outside_poi": (bundle_dict.get("micro") or {}).get("micro_is_outside_poi"),
        "micro_contract_missing_fields": (bundle_dict.get("micro") or {}).get("micro_missing_fields", []),
        "micro_contract_present_fields": (bundle_dict.get("micro") or {}).get("micro_present_fields", []),
        "micro_contract_contradictions": (bundle_dict.get("micro") or {}).get("micro_contradictions", []),
        "micro_evidence": (bundle_dict.get("micro") or {}).get("micro_evidence", {}),
        "sweep_1m_confirmed": (bundle_dict.get("micro") or {}).get("sweep_1m_confirmed"),
        "choch_detected": (bundle_dict.get("micro") or {}).get("choch_detected"),
        "trigger_inside_poi": (bundle_dict.get("micro") or {}).get("trigger_inside_poi"),
        "price_in_agent2_poi": (bundle_dict.get("micro") or {}).get("price_in_agent2_poi"),
        "trigger_outside_poi": (bundle_dict.get("micro") or {}).get("trigger_outside_poi"),
        "retest_confirmed": (bundle_dict.get("micro") or {}).get("retest_confirmed"),
        "trigger_confirmed": (bundle_dict.get("micro") or {}).get("trigger_confirmed"),
        "candles_1m_count": (bundle_dict.get("micro") or {}).get("candles_1m_count"),
    }


def _best_candidate(candidates: Any) -> dict[str, Any]:
    if not isinstance(candidates, list) or not candidates:
        return {}
    try:
        return max(
            (c for c in candidates if isinstance(c, dict)),
            key=lambda c: float(c.get("confidence", 0.0)),
            default={},
        )
    except (TypeError, ValueError):
        return {}


def _build_agent_breakdown(blackboard: Any) -> dict[str, Any]:
    """Build agent breakdown for UI/logging (legacy compatibility)."""
    breakdown = {}
    for agent_id in ("agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"):
        try:
            result = blackboard.read_sync(f"agent_results.{agent_id}")
            if result:
                breakdown[agent_id] = {
                    "score": round(getattr(result, "score", 0.0), 1),
                    "hf": getattr(result, "hard_filter_pass", None),
                    "dir": getattr(result, "direction", None),
                    "reason": getattr(result, "reason", ""),
                }
        except Exception:
            pass
    return breakdown

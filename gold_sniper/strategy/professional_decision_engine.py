from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from gold_sniper.strategy.contracts import (
    BlockedStage,
    DecisionAction,
    EvidenceBundle,
    ExecutionMode,
    ReadinessState,
    SetupGrade,
    SetupType,
    TradeSide,
)
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.scorecard import evaluate_scorecard
from gold_sniper.strategy.readiness import evaluate_readiness
from gold_sniper.strategy.risk_allocator import allocate_risk
from gold_sniper.strategy.setup_taxonomy import get_setup_requirement
from gold_sniper.strategy.enter_eligibility import evaluate_enter_eligibility


SHADOW_ONLY = "SHADOW_ONLY"

DECISION_ENTER_FULL = "ENTER_FULL"
DECISION_ENTER_REDUCED = "ENTER_REDUCED"
DECISION_WAIT_FOR_TRIGGER = "WAIT_FOR_TRIGGER"
DECISION_WAIT_FOR_BETTER_PRICE = "WAIT_FOR_BETTER_PRICE"
DECISION_WATCH_ONLY = "WATCH_ONLY"
DECISION_REJECT = "REJECT"

GRADE_A_PLUS = "A_PLUS"
GRADE_A = "A"
GRADE_B = "B"
GRADE_C = "C"
GRADE_D = "D"

_ACTION_MAP: dict[DecisionAction, str] = {
    DecisionAction.ENTER_FULL: DECISION_ENTER_FULL,
    DecisionAction.ENTER_REDUCED: DECISION_ENTER_REDUCED,
    DecisionAction.WAIT_FOR_TRIGGER: DECISION_WAIT_FOR_TRIGGER,
    DecisionAction.WAIT_FOR_BETTER_PRICE: DECISION_WAIT_FOR_BETTER_PRICE,
    DecisionAction.WATCH_ONLY: DECISION_WATCH_ONLY,
    DecisionAction.REJECT: DECISION_REJECT,
}

_GRADE_MAP: dict[SetupGrade, str] = {
    SetupGrade.A_PLUS: GRADE_A_PLUS,
    SetupGrade.A: GRADE_A,
    SetupGrade.B: GRADE_B,
    SetupGrade.C: GRADE_C,
    SetupGrade.D: GRADE_D,
}


@dataclass(frozen=True)
class ProfessionalDecisionResult:
    decision: str
    setup_grade: str
    confidence_score: float
    risk_multiplier: float
    hard_veto: bool
    hard_veto_reason: str | None = None
    soft_issues: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    required_execution_mode: str = SHADOW_ONLY
    explanation: str = ""
    score_breakdown: dict[str, Any] = field(default_factory=dict)
    evidence_roles: dict[str, Any] = field(default_factory=dict)
    score_before_veto: float = 0.0
    score_after_veto: float = 0.0
    blocked_stage: str | None = None
    veto_code: str | None = None
    replay_invalid: bool = False
    readiness_state: str = "UNAVAILABLE"
    readiness_reason: str = "READINESS_UNAVAILABLE"
    readiness_by_section: dict[str, Any] = field(default_factory=dict)
    risk_plan: dict[str, Any] = field(default_factory=dict)
    # P2-E Phase 7B: enter eligibility
    enter_eligible: bool = False
    enter_eligibility_reason: str = "NOT_EVALUATED"
    enter_eligibility_blockers: list[str] = field(default_factory=list)
    enter_eligibility_checks: dict[str, Any] = field(default_factory=dict)
    risk_preview: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_professional_decision(
    evidence: dict[str, Any] | EvidenceBundle | None,
    *,
    setup_type: str = "UNKNOWN",
) -> ProfessionalDecisionResult:
    """Evaluate evidence through the P1 decision engine — shadow-only, broker-free.

    Supports both legacy dict evidence and new EvidenceBundle contracts.
    """
    if isinstance(evidence, EvidenceBundle):
        return _evaluate_from_bundle(evidence)

    if isinstance(evidence, dict):
        if _looks_like_contract_dict(evidence):
            return _evaluate_from_bundle(EvidenceBundle.from_dict(evidence))
        return _evaluate_legacy(evidence, setup_type)

    return _evaluate_legacy({}, setup_type)


def _looks_like_contract_dict(evidence: dict[str, Any]) -> bool:
    contract_keys = {
        "observations",
        "context",
        "poi",
        "liquidity",
        "micro",
        "news",
        "session",
        "risk",
        "raw",
        "setup_type",
        "side",
    }
    legacy_keys = {
        "news_permission",
        "session_permission",
        "htf_context_placeholder",
        "dol_placeholder",
        "liquidity_state",
        "poi_placeholder",
        "session_premium_ote_gate",
        "micro_confirmation",
        "risk_placeholder",
        "xauusd_killzone",
        "kasper_ict_scenarios",
    }
    return bool(contract_keys.intersection(evidence)) and not bool(legacy_keys.intersection(evidence))


def _evaluate_from_bundle(bundle: EvidenceBundle) -> ProfessionalDecisionResult:
    veto = evaluate_hard_veto(bundle)
    scorecard = evaluate_scorecard(bundle, veto)
    readiness = evaluate_readiness(bundle, scorecard, veto)
    enter_eligibility = evaluate_enter_eligibility(
        bundle=bundle,
        scorecard=scorecard,
        readiness=readiness,
        veto=veto,
    )

    if veto.replay_invalid:
        action = DECISION_REJECT
        grade = GRADE_D
        hard_veto = False
        reason = None  # replay_invalid is not a hard veto
    elif veto.hard_veto:
        action = DECISION_REJECT
        grade = GRADE_D
        hard_veto = True
        reason = veto.veto_code or veto.veto_reason
    else:
        action = _action_from_scorecard(scorecard, bundle.setup_type, readiness, enter_eligibility)
        grade = _GRADE_MAP.get(scorecard.grade, GRADE_D)
        hard_veto = False
        reason = None

    risk_plan = allocate_risk(
        action=action, grade=grade, evidence=bundle, capital=100.0,
        enter_eligible=enter_eligibility.enter_eligible,
    )

    classification = (bundle.raw or {}).get("setup_classification") or {}
    return ProfessionalDecisionResult(
        decision=action,
        setup_grade=grade,
        confidence_score=round(max(0.0, min(scorecard.score_before_veto / 100.0, 1.0)), 3),
        risk_multiplier=risk_plan.risk_multiplier,
        hard_veto=hard_veto,
        hard_veto_reason=reason,
        soft_issues=scorecard.soft_issues,
        missing_evidence=scorecard.missing_evidence,
        required_execution_mode=SHADOW_ONLY,
        explanation=_build_explanation(action, grade, scorecard, veto, risk_plan),
        score_breakdown={
            **scorecard.metadata,
            "setup_type": bundle.setup_type.value if hasattr(bundle.setup_type, "value") else str(bundle.setup_type),
            "setup_classification": classification,
            "readiness": readiness.to_dict(),
            "enter_eligibility": enter_eligibility.to_dict(),
            "grade_risk_multiplier": risk_plan.metadata.get("grade_risk_multiplier"),
            "effective_risk_pct": risk_plan.metadata.get("effective_risk_pct"),
            "readiness_coherence": readiness.metadata.get("readiness_coherence", {}),
        },
        evidence_roles=_classify_evidence_roles(bundle),
        score_before_veto=scorecard.score_before_veto,
        score_after_veto=scorecard.score_after_veto,
        blocked_stage=veto.blocked_stage.value,
        veto_code=veto.veto_code,
        replay_invalid=veto.replay_invalid,
        readiness_state=readiness.state.value,
        readiness_reason=readiness.reason,
        readiness_by_section=dict(readiness.section_states),
        risk_plan=risk_plan.to_dict(),
        enter_eligible=enter_eligibility.enter_eligible,
        enter_eligibility_reason=enter_eligibility.reason,
        enter_eligibility_blockers=list(enter_eligibility.blockers),
        enter_eligibility_checks=dict(enter_eligibility.checks),
        risk_preview=dict(enter_eligibility.risk_preview),
    )


def _evaluate_legacy(ev: dict[str, Any], setup_type: str) -> ProfessionalDecisionResult:
    """Legacy path preserving original PDE behavior for backward compatibility."""
    setup = str(setup_type or "UNKNOWN").upper()

    # Try old hard veto detection first, then supplement with new registry
    hard_veto_reason = _detect_hard_veto(ev)
    if not hard_veto_reason:
        bundle = _bundle_from_legacy_evidence(ev, setup_type)
        new_veto = evaluate_hard_veto(bundle)
        if new_veto.hard_veto or new_veto.replay_invalid:
            hard_veto_reason = bundle.raw.get("legacy_veto_code") or new_veto.veto_code or new_veto.veto_reason

    if hard_veto_reason:
        # Compute new-contract fields for enrichment
        bundle_v = _bundle_from_legacy_evidence(ev, setup_type)
        new_v = evaluate_hard_veto(bundle_v)
        return ProfessionalDecisionResult(
            decision=DECISION_REJECT,
            setup_grade=GRADE_D,
            confidence_score=0.0,
            risk_multiplier=0.0,
            hard_veto=True,
            hard_veto_reason=hard_veto_reason,
            soft_issues=[],
            missing_evidence=[],
            required_execution_mode=SHADOW_ONLY,
            explanation=f"Rejected by non-negotiable hard veto: {hard_veto_reason}.",
            score_breakdown={"hard_veto_reason": hard_veto_reason},
            evidence_roles=_classify_evidence_roles_legacy(ev),
            score_before_veto=0.0,
            score_after_veto=0.0,
            blocked_stage=new_v.blocked_stage.value if new_v.blocked_stage else BlockedStage.NONE.value,
            veto_code=new_v.veto_code or hard_veto_reason,
            replay_invalid=new_v.replay_invalid,
            risk_plan={},
        )

    soft_issues = _collect_soft_issues(ev)
    missing = _collect_missing_evidence(ev)
    score, breakdown = _score_evidence(ev, setup)
    grade = _grade_from_score(score, missing)
    decision = _decision_from_grade_and_missing(grade, missing)
    risk = _risk_from_grade_decision_and_modulators(grade, decision, ev)
    breakdown["risk_modulators"] = _risk_modulators(ev)
    confidence = round(max(0.0, min(score / 100.0, 1.0)), 3)

    # Also compute new-contract fields for enrichment
    bundle = _bundle_from_legacy_evidence(ev, setup_type)
    new_veto = evaluate_hard_veto(bundle)
    new_scorecard = evaluate_scorecard(bundle, new_veto)

    return ProfessionalDecisionResult(
        decision=decision,
        setup_grade=grade,
        confidence_score=round(confidence, 3),
        risk_multiplier=round(risk, 3),
        hard_veto=False,
        hard_veto_reason=None,
        soft_issues=list(dict.fromkeys(soft_issues)),
        missing_evidence=list(dict.fromkeys(missing)),
        required_execution_mode=SHADOW_ONLY,
        explanation=_explain(decision, grade, score, risk, missing, soft_issues),
        score_breakdown=breakdown,
        evidence_roles=_classify_evidence_roles_legacy(ev),
        score_before_veto=new_scorecard.score_before_veto,
        score_after_veto=new_scorecard.score_after_veto,
        blocked_stage=new_veto.blocked_stage.value,
        veto_code=new_veto.veto_code,
        replay_invalid=new_veto.replay_invalid,
        risk_plan=allocate_risk(action=decision, grade=grade, evidence=bundle, capital=100.0).to_dict(),
    )


def _action_from_scorecard(scorecard: Any, setup_type: SetupType | str = SetupType.UNKNOWN, readiness: Any | None = None, enter_eligibility: Any | None = None) -> str:
    grade = scorecard.grade
    missing = set(scorecard.missing_evidence)
    requirement = get_setup_requirement(setup_type)
    score = float(scorecard.score_after_veto or 0.0)

    if readiness is not None:
        state = readiness.state
        if state in {ReadinessState.INVALID, ReadinessState.REJECT}:
            return DECISION_REJECT
        if state == ReadinessState.WAITING_POI:
            return DECISION_WAIT_FOR_BETTER_PRICE
        if state == ReadinessState.WAITING_TRIGGER:
            return DECISION_WAIT_FOR_TRIGGER
        if state == ReadinessState.UNAVAILABLE:
            return DECISION_WATCH_ONLY
        if state == ReadinessState.WATCH_ONLY:
            return DECISION_WATCH_ONLY
        if state == ReadinessState.READY:
            if missing:
                return _waiting_decision_from_missing(missing)
            if score >= requirement.min_score_enter_full:
                if enter_eligibility is not None and not enter_eligibility.enter_eligible:
                    return getattr(enter_eligibility, "suggested_action_when_blocked", DECISION_WATCH_ONLY)
                return DECISION_ENTER_FULL
            if score >= requirement.min_score_enter_reduced:
                if enter_eligibility is not None and not enter_eligibility.enter_eligible:
                    return getattr(enter_eligibility, "suggested_action_when_blocked", DECISION_WATCH_ONLY)
                return DECISION_ENTER_REDUCED
            if score >= requirement.min_score_watch:
                return DECISION_WATCH_ONLY
            return DECISION_WATCH_ONLY

    # Legacy fallback.
    if missing:
        if grade in {SetupGrade.A_PLUS, SetupGrade.A, SetupGrade.B}:
            return _waiting_decision_from_missing(missing)
        if grade == SetupGrade.C:
            return DECISION_WATCH_ONLY
        return DECISION_REJECT

    # Use setup taxonomy thresholds for clean evidence
    if score >= requirement.min_score_enter_full:
        return DECISION_ENTER_FULL

    if score >= requirement.min_score_enter_reduced:
        return DECISION_ENTER_REDUCED

    if score >= requirement.min_score_watch:
        return DECISION_WATCH_ONLY

    return DECISION_REJECT


def _waiting_decision_from_missing(missing: set[str]) -> str:
    if _missing_price_or_poi(missing):
        return DECISION_WAIT_FOR_BETTER_PRICE
    if _missing_micro(missing):
        return DECISION_WAIT_FOR_TRIGGER
    return DECISION_WATCH_ONLY


def _missing_micro(missing: set[str]) -> bool:
    return bool(
        missing
        & {
            "MICRO_TRIGGER_TYPE_MISSING",
            "MICRO_PAYLOAD_MISSING",
            "DISPLACEMENT_MISSING",
            "RECLAIM_OR_ACCEPTANCE_MISSING",
            "RETEST_MISSING",
            "MICRO_CONFIRMATION_WATCH_C",
            "MICRO_CONFIRMATION_WATCH_D",
            "MICRO_MISSING",
        }
    )


def _missing_price_or_poi(missing: set[str]) -> bool:
    return bool(
        missing
        & {
            "POI_MISSING",
            "POI_INVALID_SHAPE",
            "POI_QUALITY_WATCH_C",
            "POI_QUALITY_WATCH_D",
            "FIBONACCI_OTE_CONTEXT_MISSING",
            "OTE_CONTEXT_MISSING",
            "PREMIUM_DISCOUNT_CONTEXT_MISSING",
        }
    )


def _build_explanation(
    action: str,
    grade: str,
    scorecard: Any,
    veto: Any,
    risk_plan: Any,
) -> str:
    if veto.replay_invalid:
        return f"Replay data invalid: {veto.veto_reason or 'data integrity check failed'}."
    if veto.hard_veto:
        return f"Rejected by non-negotiable hard veto [{veto.veto_code}]: {veto.veto_reason}."
    if action == DECISION_REJECT:
        return f"Setup rejected as grade {grade} with score {scorecard.score_after_veto:.1f}."
    if action == DECISION_ENTER_FULL:
        return f"Grade {grade} setup accepted for full shadow entry with risk multiplier {risk_plan.risk_multiplier:.2f}."
    if action == DECISION_ENTER_REDUCED:
        return f"Grade {grade} setup accepted for reduced shadow entry with risk multiplier {risk_plan.risk_multiplier:.2f}."
    if action == DECISION_WAIT_FOR_TRIGGER:
        return f"Grade {grade} setup waits for trigger confirmation: {', '.join(scorecard.missing_evidence[:3])}."
    if action == DECISION_WAIT_FOR_BETTER_PRICE:
        return f"Grade {grade} setup waits for better price or POI readiness: {', '.join(scorecard.missing_evidence[:3])}."
    return f"Grade {grade} setup remains watch-only due to soft issues: {', '.join(scorecard.soft_issues[:3])}."


def _classify_evidence_roles(bundle: EvidenceBundle) -> dict[str, Any]:
    roles: dict[str, Any] = {}
    if bundle.news:
        roles["news"] = "hard_veto_or_context"
    if bundle.session:
        roles["session"] = "hard_veto_or_context"
    if bundle.context:
        roles["context"] = "score_provider"
    if bundle.poi:
        roles["poi"] = "score_provider"
    if bundle.liquidity:
        roles["liquidity"] = "score_provider"
    if bundle.micro:
        roles["micro"] = "score_provider"
    if bundle.risk:
        roles["risk"] = "risk_adjuster"
    return roles


def _bundle_from_legacy_evidence(evidence: dict[str, Any] | None, setup_type: str) -> EvidenceBundle:
    """Adapt legacy dict evidence into an EvidenceBundle."""
    ev = evidence if isinstance(evidence, dict) else {}
    setup = str(setup_type or "UNKNOWN").upper()

    resolved_setup = SetupType.UNKNOWN
    try:
        resolved_setup = SetupType(setup)
    except ValueError:
        pass

    # --- news ---
    news_raw = ev.get("news_permission", {})
    news: dict[str, Any] = {}
    if isinstance(news_raw, dict):
        news["passed"] = news_raw.get("passed")
        news["reason"] = news_raw.get("reason")
        news["high_impact_window"] = news_raw.get("high_impact_window")
        news["news_blocked"] = news_raw.get("news_blocked")
        news["news_clear"] = news_raw.get("news_clear")
        news["post_news_stealth"] = news_raw.get("post_news_stealth")
        news["medium_impact_nearby"] = news_raw.get("medium_impact_nearby")
        val = news_raw.get("value")
        if isinstance(val, dict):
            for k in ("high_impact_window", "news_blocked", "news_clear", "post_news_stealth", "medium_impact_nearby"):
                if k in val:
                    news[k] = val[k]

    # --- session ---
    session_raw = ev.get("session_permission", {})
    session: dict[str, Any] = {}
    if isinstance(session_raw, dict):
        session["passed"] = session_raw.get("passed")
        session["reason"] = session_raw.get("reason")
        session["session"] = session_raw.get("session")
        session["session_label"] = session_raw.get("session_label")
        session["is_hard_block"] = session_raw.get("is_hard_block")
        session["friday_halt"] = session_raw.get("friday_halt")
        session["trading_allowed"] = session_raw.get("trading_allowed")
        session["session_grade"] = session_raw.get("session_grade")
        session["session_score"] = session_raw.get("session_score")
        val = session_raw.get("value")
        if isinstance(val, dict):
            for k in ("session", "session_label", "is_hard_block", "friday_halt", "trading_allowed", "session_grade", "session_score"):
                if k in val:
                    session[k] = val[k]

    # --- context ---
    ctx: dict[str, Any] = {}
    for src_key in ("htf_context_placeholder", "dol_placeholder"):
        src = ev.get(src_key, {})
        if isinstance(src, dict):
            if src.get("passed") is True:
                ctx["htf_context_available"] = True
            val = src.get("value")
            if isinstance(val, dict):
                if val.get("direction"):
                    ctx["direction"] = val["direction"]
                if val.get("draw_on_liquidity"):
                    ctx["draw_on_liquidity"] = val["draw_on_liquidity"]

    # --- poi ---
    poi_raw = ev.get("poi_placeholder", {})
    poi: dict[str, Any] = {}
    if isinstance(poi_raw, dict):
        poi["reason"] = poi_raw.get("reason")
        poi["mitigation_pct"] = poi_raw.get("mitigation_pct")
        poi["opposes_htf_dol"] = poi_raw.get("opposes_htf_dol")
        val = poi_raw.get("value")
        if isinstance(val, dict):
            for k in ("score", "quality_score", "poi_quality_score", "lifecycle_state", "selected_poi",
                       "poi_available", "mitigation_pct", "deepest_penetration_pct",
                       "opposes_htf_dol", "aligned_with_context"):
                if k in val:
                    poi[k] = val[k]

    # --- liquidity ---
    liq_raw = ev.get("liquidity_state", {})
    liquidity: dict[str, Any] = {}
    if isinstance(liq_raw, dict):
        liquidity["reason"] = liq_raw.get("reason")
        val = liq_raw.get("value")
        if isinstance(val, dict):
            for k in ("sweep_detected", "sweep_rejected", "liquidity_state", "liquidity_quality_score", "score"):
                if k in val:
                    liquidity[k] = val[k]

    # --- micro ---
    micro_raw = ev.get("micro_confirmation", {})
    micro: dict[str, Any] = {}
    if isinstance(micro_raw, dict):
        micro["reason"] = micro_raw.get("reason")
        val = micro_raw.get("value")
        if isinstance(val, dict):
            for k in ("score", "trigger_strength", "displacement_present", "reclaim_confirmed",
                       "retest_confirmed", "trigger_inside_poi"):
                if k in val:
                    micro[k] = val[k]

    # --- risk ---
    risk_raw = ev.get("risk_placeholder", {})
    risk: dict[str, Any] = {}
    if isinstance(risk_raw, dict):
        risk["passed"] = risk_raw.get("passed")
        val = risk_raw.get("value")
        if isinstance(val, dict):
            for k in ("max_daily_loss_hit", "max_weekly_loss_hit", "max_drawdown_hit",
                       "kill_switch", "atr_risk_multiplier"):
                if k in val:
                    risk[k] = val[k]

    # --- timing / OTE ---
    ote_raw = ev.get("session_premium_ote_gate", {})
    if isinstance(ote_raw, dict):
        val = ote_raw.get("value")
        if isinstance(val, dict):
            if val.get("in_ote") is True:
                ctx["in_ote"] = True
            if val.get("premium_discount"):
                ctx["premium_discount"] = val["premium_discount"]
            if "timing_quality_score" in val:
                ctx["timing_quality_score"] = val["timing_quality_score"]

    # --- killzone / session multipliers ---
    kz = ev.get("xauusd_killzone", {})
    if isinstance(kz, dict):
        val = kz.get("value")
        if isinstance(val, dict):
            if "risk_multiplier" in val:
                session["risk_multiplier"] = val["risk_multiplier"]

    # --- raw ---
    raw: dict[str, Any] = {}
    scenarios = ev.get("kasper_ict_scenarios", {})
    if isinstance(scenarios, dict):
        raw["kasper_ict_scenarios"] = scenarios

    # --- legacy hard veto token detection ---
    legacy_veto_code = _apply_legacy_hard_veto_markers(ev, news, session, poi, liquidity, micro, risk)
    if legacy_veto_code:
        raw["legacy_veto_code"] = legacy_veto_code

    return EvidenceBundle(
        symbol="XAUUSD",
        setup_type=resolved_setup,
        news=news,
        session=session,
        context=ctx,
        poi=poi,
        liquidity=liquidity,
        micro=micro,
        risk=risk,
        raw=raw,
    )


_LEGACY_HARD_VETO_TOKENS: set[str] = {
    "NEWS_HARD_VETO",
    "NEWS_CONTEXT_BLOCKED",
    "POST_NEWS_STEALTH_NOT_NORMALIZED",
    "SESSION_VETO_TOKYO_ASIA",
    "SESSION_TOKYO_ASIA_BLOCKED",
    "SESSION_EXPLICITLY_BLOCKED",
    "SESSION_OFF_SESSION_NON_TRADABLE",
    "OFF_SESSION_NON_TRADABLE",
    "TRIGGER_OUTSIDE_POI",
    "RISK_INVALID",
    "SL_IMPOSSIBLE",
}

_LEGACY_TOKEN_TO_STAGE: dict[str, tuple[str, str, str]] = {
    "NEWS_HARD_VETO": ("news", "high_impact_window", "NEWS_HIGH_IMPACT_WINDOW"),
    "NEWS_CONTEXT_BLOCKED": ("news", "high_impact_window", "NEWS_HIGH_IMPACT_WINDOW"),
    "POST_NEWS_STEALTH_NOT_NORMALIZED": ("news", "post_news_stealth", "NEWS_POST_EVENT_STEALTH"),
    "SESSION_VETO_TOKYO_ASIA": ("session", "session", "SESSION_TOKYO_ASIA_BLOCK"),
    "SESSION_TOKYO_ASIA_BLOCKED": ("session", "session", "SESSION_TOKYO_ASIA_BLOCK"),
    "SESSION_EXPLICITLY_BLOCKED": ("session", "is_hard_block", "SESSION_EXPLICIT_HARD_BLOCK"),
    "SESSION_OFF_SESSION_NON_TRADABLE": ("session", "is_hard_block", "SESSION_EXPLICIT_HARD_BLOCK"),
    "OFF_SESSION_NON_TRADABLE": ("session", "is_hard_block", "SESSION_EXPLICIT_HARD_BLOCK"),
    "TRIGGER_OUTSIDE_POI": ("micro", "trigger_outside_poi", None),
    "RISK_INVALID": ("risk", "max_daily_loss_hit", "MAX_DAILY_LOSS_GUARD"),
    "SL_IMPOSSIBLE": ("risk", "max_daily_loss_hit", "MAX_DAILY_LOSS_GUARD"),
}


def _apply_legacy_hard_veto_markers(
    ev: dict[str, Any],
    news: dict[str, Any],
    session: dict[str, Any],
    poi: dict[str, Any],
    liquidity: dict[str, Any],
    micro: dict[str, Any],
    risk: dict[str, Any],
) -> str | None:
    """Scan legacy evidence for old hard veto tokens and apply them as markers.
    Returns the legacy veto code if one was found.
    """
    all_reasons: list[str] = []
    found_legacy_veto: str | None = None

    for stage_key, stage_data in ev.items():
        if not isinstance(stage_data, dict):
            continue
        reason = str(stage_data.get("reason") or "").upper()
        if reason:
            all_reasons.append(reason)
        value = stage_data.get("value")
        if isinstance(value, dict):
            reasons_list = value.get("reasons") or []
            for r in reasons_list:
                all_reasons.append(str(r).upper())
            if value.get("hard_reject") is True or value.get("hard_block") is True:
                r_list = value.get("reasons") or []
                if r_list:
                    all_reasons.append(str(r_list[0]).upper())
                else:
                    all_reasons.append(f"{stage_key.upper()}_HARD_REJECT")

    for reason in all_reasons:
        if reason in _LEGACY_HARD_VETO_TOKENS:
            mapping = _LEGACY_TOKEN_TO_STAGE.get(reason)
            if mapping:
                target_dict_key, flag_key, _veto_code = mapping
                target = {"news": news, "session": session, "poi": poi,
                          "liquidity": liquidity, "micro": micro, "risk": risk}.get(target_dict_key)
                if target is not None:
                    target[flag_key] = True
                    if reason.startswith("SESSION_"):
                        if "TOKYO" in reason or "ASIA" in reason:
                            session["session"] = "TOKYO"
                    if reason == "TRIGGER_OUTSIDE_POI":
                        micro["outside_poi"] = True
            if found_legacy_veto is None:
                found_legacy_veto = reason

    return found_legacy_veto


# ── Legacy scoring functions (preserved for backward compatibility) ──


def _result(
    *, decision: str, grade: str, confidence: float, risk: float,
    hard_veto: bool, hard_veto_reason: str | None,
    soft_issues: list[str], missing: list[str],
    breakdown: dict[str, Any], roles: dict[str, Any], explanation: str,
) -> ProfessionalDecisionResult:
    return ProfessionalDecisionResult(
        decision=decision, setup_grade=grade,
        confidence_score=round(confidence, 3), risk_multiplier=round(risk, 3),
        hard_veto=hard_veto, hard_veto_reason=hard_veto_reason,
        soft_issues=list(dict.fromkeys(soft_issues)),
        missing_evidence=list(dict.fromkeys(missing)),
        required_execution_mode=SHADOW_ONLY, explanation=explanation,
        score_breakdown=breakdown, evidence_roles=roles,
        risk_plan={},
    )


def _classify_evidence_roles_legacy(evidence: dict[str, Any]) -> dict[str, Any]:
    roles: dict[str, Any] = {}
    for key in evidence:
        if key in {"news_permission"}:
            roles[key] = "hard_veto"
        elif key in {"session_permission"}:
            roles[key] = "hard_veto_or_context"
        elif key in {"poi_placeholder", "micro_confirmation", "session_premium_ote_gate", "liquidity_state"}:
            roles[key] = "score_provider"
        elif key in {"risk_placeholder", "spread_risk_placeholder"}:
            roles[key] = "risk_adjuster"
        elif key in {"kasper_ict_scenarios", "htf_context_placeholder", "dol_placeholder"}:
            roles[key] = "score_provider"
        else:
            roles[key] = "informational"
    return roles


def _detect_hard_veto(evidence: dict[str, Any]) -> str | None:
    reasons = _all_reasons(evidence)
    hard_tokens = {
        "NEWS_HARD_VETO", "NEWS_CONTEXT_BLOCKED",
        "POST_NEWS_STEALTH_NOT_NORMALIZED",
        "SESSION_VETO_TOKYO_ASIA", "SESSION_TOKYO_ASIA_BLOCKED",
        "SESSION_EXPLICITLY_BLOCKED", "SESSION_OFF_SESSION_NON_TRADABLE",
        "OFF_SESSION_NON_TRADABLE", "TRIGGER_OUTSIDE_POI",
        "RISK_INVALID", "SL_IMPOSSIBLE",
    }
    for reason in reasons:
        if reason in hard_tokens:
            return reason
    for key in ("poi_placeholder", "micro_confirmation", "session_premium_ote_gate", "liquidity_state"):
        stage = evidence.get(key, {})
        if not isinstance(stage, dict):
            continue
        value = stage.get("value", {})
        if not isinstance(value, dict):
            continue
        if value.get("hard_reject") is True or value.get("hard_block") is True:
            reasons_list = value.get("reasons") or []
            if reasons_list:
                return str(reasons_list[0]).upper()
            return f"{key.upper()}_HARD_REJECT"
    return None


def _score_evidence(evidence: dict[str, Any], setup_type: str) -> tuple[float, dict[str, Any]]:
    breakdown: dict[str, Any] = {}
    score = 0.0
    for name, value in (
        ("scenario", _scenario_score(evidence)),
        ("context", _context_score(evidence)),
        ("liquidity", _liquidity_score(evidence)),
        ("poi", _poi_score(evidence)),
        ("premium_ote", _premium_ote_score(evidence)),
        ("micro", _micro_score(evidence, setup_type)),
        ("risk", _risk_score(evidence)),
    ):
        breakdown[name] = value
        score += value
    final_score = round(max(0.0, min(score, 100.0)), 2)
    breakdown["session_timing_quality"] = _stage_score(evidence, "session_premium_ote_gate", "timing_quality_score", 0.0)
    breakdown["poi_quality_score"] = _stage_score(evidence, "poi_placeholder", "quality_score", 0.0)
    breakdown["micro_template"] = _micro_template_name(evidence)
    breakdown["total"] = final_score
    return final_score, breakdown


def _scenario_score(evidence: dict[str, Any]) -> float:
    scenario = evidence.get("kasper_ict_scenarios", {})
    best = scenario.get("best_scenario", {}) if isinstance(scenario, dict) else {}
    if not isinstance(best, dict):
        return 0.0
    confidence = _num(best.get("confidence"), 0.0) or 0.0
    if best.get("tradable"):
        return 18.0 + min(confidence * 7.0, 7.0)
    if best.get("near_miss"):
        return 12.0 + min(confidence * 5.0, 5.0)
    if best.get("status") == "SCENARIO_WAIT":
        return 5.0
    return 0.0


def _context_score(evidence: dict[str, Any]) -> float:
    score = 0.0
    if _stage_passed(evidence, "news_permission"):
        score += 8.0
    if _stage_passed(evidence, "session_permission"):
        score += 8.0
    if _stage_passed(evidence, "htf_context_placeholder"):
        score += 7.0
    if _stage_passed(evidence, "dol_placeholder"):
        score += 7.0
    if _stage_passed(evidence, "spread_risk_placeholder"):
        score += 5.0
    return score


def _liquidity_score(evidence: dict[str, Any]) -> float:
    stage = evidence.get("liquidity_state", {})
    reason = str(stage.get("reason", "")).upper() if isinstance(stage, dict) else ""
    value = stage.get("value", {}) if isinstance(stage, dict) else {}
    if "SUPPORTS" in reason:
        return 10.0
    if "WATCH" in reason:
        return 5.0
    if isinstance(value, dict) and value.get("decision") == "SUPPORTS_SETUP":
        return 10.0
    if isinstance(value, dict) and value.get("decision") == "WATCH":
        return 5.0
    return 0.0


def _poi_score(evidence: dict[str, Any]) -> float:
    stage = evidence.get("poi_placeholder", {})
    reason = str(stage.get("reason", "")).upper() if isinstance(stage, dict) else ""
    value = stage.get("value", {}) if isinstance(stage, dict) else {}
    raw_score = _num(value.get("score") if isinstance(value, dict) else None, None)
    if "ACCEPT" in reason:
        return 18.0 + min((raw_score or 0.0) / 100.0 * 7.0, 7.0)
    if "WATCH" in reason:
        return 10.0 + min((raw_score or 0.0) / 100.0 * 5.0, 5.0)
    return 0.0


def _premium_ote_score(evidence: dict[str, Any]) -> float:
    stage = evidence.get("session_premium_ote_gate", {})
    reason = str(stage.get("reason", "")).upper() if isinstance(stage, dict) else ""
    value = stage.get("value", {}) if isinstance(stage, dict) else {}
    raw_score = _num(value.get("score") if isinstance(value, dict) else None, None)
    if "PASS" in reason:
        return 10.0 + min((raw_score or 0.0) / 100.0 * 5.0, 5.0)
    if "WATCH" in reason:
        return 5.0
    return 0.0


def _micro_score(evidence: dict[str, Any], setup_type: str) -> float:
    stage = evidence.get("micro_confirmation", {})
    reason = str(stage.get("reason", "")).upper() if isinstance(stage, dict) else ""
    value = stage.get("value", {}) if isinstance(stage, dict) else {}
    raw_score = _num(value.get("score") if isinstance(value, dict) else None, None)
    if "CONFIRMED" in reason:
        return 15.0 + min((raw_score or 0.0) / 100.0 * 5.0, 5.0)
    if "WATCH" in reason:
        if setup_type in {"REVERSAL", "REVERSAL_AFTER_SWEEP"}:
            return 3.0
        return 8.0
    return 0.0


def _risk_score(evidence: dict[str, Any]) -> float:
    stage = evidence.get("risk_placeholder", {})
    if isinstance(stage, dict) and stage.get("passed") is True:
        return 10.0
    return 0.0


def _grade_from_score(score: float, missing: list[str] | None = None) -> str:
    missing_set = {str(item).upper() for item in (missing or [])}
    if any("REJECT" in item or "BLOCKS_SETUP" in item or "BLOCK_" in item for item in missing_set):
        return GRADE_D
    if any("WATCH_C" in item or "WATCH_D" in item or item == "RISK_CONTEXT_MISSING" for item in missing_set):
        return GRADE_C if score >= 45.0 else GRADE_D
    if any("WATCH_B" in item for item in missing_set):
        return GRADE_B if score >= 62.0 else GRADE_C
    if missing_set and score >= 88.0:
        return GRADE_A
    if score >= 88.0:
        return GRADE_A_PLUS
    if score >= 76.0:
        return GRADE_A
    if score >= 62.0:
        return GRADE_B
    if score >= 45.0:
        return GRADE_C
    return GRADE_D


def _decision_from_grade_and_missing(grade: str, missing: list[str]) -> str:
    missing_set = set(missing)
    if grade == GRADE_A_PLUS:
        return DECISION_ENTER_FULL
    if grade == GRADE_A:
        if _missing_price_or_poi(missing_set):
            return DECISION_WAIT_FOR_BETTER_PRICE
        if _missing_micro(missing_set) or missing_set:
            return DECISION_WAIT_FOR_TRIGGER
        return DECISION_ENTER_FULL
    if grade == GRADE_B:
        if _missing_price_or_poi(missing_set):
            return DECISION_WAIT_FOR_BETTER_PRICE
        if _missing_micro(missing_set):
            return DECISION_WAIT_FOR_TRIGGER
        return DECISION_ENTER_REDUCED
    if grade == GRADE_C:
        return DECISION_WATCH_ONLY
    return DECISION_REJECT


def _risk_from_grade_decision_and_modulators(grade: str, decision: str, evidence: dict[str, Any]) -> float:
    if decision not in {DECISION_ENTER_FULL, DECISION_ENTER_REDUCED}:
        return 0.0
    base = {GRADE_A_PLUS: 1.0, GRADE_A: 0.75, GRADE_B: 0.4, GRADE_C: 0.0, GRADE_D: 0.0}.get(grade, 0.0)
    modulators = _risk_modulators(evidence)
    risk = base
    for value in modulators.values():
        risk *= value
    if grade == GRADE_B:
        risk = min(risk, 0.5)
    return round(max(0.0, min(risk, 1.0)), 3)


def _risk_modulators(evidence: dict[str, Any]) -> dict[str, float]:
    return {
        "session": _stage_risk_multiplier(evidence, "xauusd_killzone", 1.0),
        "timing": _stage_risk_multiplier(evidence, "session_premium_ote_gate", 1.0),
        "liquidity": _stage_risk_multiplier(evidence, "liquidity_state", 1.0),
        "atr": _stage_risk_multiplier(evidence, "risk_placeholder", 1.0),
    }


def _stage_risk_multiplier(evidence: dict[str, Any], key: str, default: float = 1.0) -> float:
    stage = evidence.get(key, {})
    value = stage.get("value", {}) if isinstance(stage, dict) else {}
    if isinstance(value, dict):
        for field in ("risk_multiplier", "session_risk_multiplier", "timing_risk_multiplier", "liquidity_risk_multiplier"):
            if field in value:
                return _bounded_multiplier(value.get(field), default)
    return default


def _stage_score(evidence: dict[str, Any], key: str, field: str, default: float = 0.0) -> float:
    stage = evidence.get(key, {})
    value = stage.get("value", {}) if isinstance(stage, dict) else {}
    if isinstance(value, dict):
        try:
            return float(value.get(field, default) or default)
        except (TypeError, ValueError):
            return default
    return default


def _bounded_multiplier(value: Any, default: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(max(0.0, min(number, 1.0)), 4)


def _micro_template_name(evidence: dict[str, Any]) -> str:
    stage = evidence.get("micro_confirmation", {})
    value = stage.get("value", {}) if isinstance(stage, dict) else {}
    if isinstance(value, dict):
        return str(value.get("template_name") or "unknown")
    return "unknown"


def _collect_soft_issues(evidence: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for stage in evidence.values():
        if not isinstance(stage, dict):
            continue
        reason = str(stage.get("reason", "")).upper()
        value = stage.get("value")
        if "WATCH" in reason:
            issues.append(reason)
        if isinstance(value, dict):
            for warning in value.get("warnings", []) or []:
                issues.append(str(warning))
            for reason_item in value.get("reasons", []) or []:
                normalized = str(reason_item).upper()
                if "WATCH" in normalized or "MISSING" in normalized:
                    issues.append(str(reason_item))
    return list(dict.fromkeys(issues))


def _collect_missing_evidence(evidence: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for stage in evidence.values():
        if not isinstance(stage, dict):
            continue
        reason = str(stage.get("reason", "")).upper()
        if stage.get("passed") is False and not _is_hard_reason(reason):
            missing.append(reason)
        value = stage.get("value")
        if isinstance(value, dict):
            for item in value.get("missing_evidence", []) or []:
                missing.append(str(item))
    return list(dict.fromkeys(missing))


def _explain(decision: str, grade: str, score: float, risk: float,
             missing: list[str], soft_issues: list[str]) -> str:
    if decision == DECISION_REJECT:
        return f"Setup rejected as grade {grade} with score {score:.1f}."
    if decision == DECISION_ENTER_FULL:
        return f"Grade {grade} setup accepted for full shadow entry with risk multiplier {risk:.2f}."
    if decision == DECISION_ENTER_REDUCED:
        return f"Grade {grade} setup accepted for reduced shadow entry with risk multiplier {risk:.2f}."
    if decision == DECISION_WAIT_FOR_TRIGGER:
        return f"Grade {grade} setup waits for trigger confirmation: {', '.join(missing[:3])}."
    if decision == DECISION_WAIT_FOR_BETTER_PRICE:
        return f"Grade {grade} setup waits for better price or POI readiness: {', '.join(missing[:3])}."
    return f"Grade {grade} setup remains watch-only due to soft issues: {', '.join(soft_issues[:3])}."


def _all_reasons(evidence: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for stage in evidence.values():
        if isinstance(stage, dict):
            reason = stage.get("reason")
            if reason:
                reasons.append(str(reason).upper())
            value = stage.get("value")
            if isinstance(value, dict):
                for key in ("reasons", "warnings"):
                    for item in value.get(key, []) or []:
                        reasons.append(str(item).upper())
    return list(dict.fromkeys(reasons))


def _stage_passed(evidence: dict[str, Any], key: str) -> bool:
    stage = evidence.get(key, {})
    return bool(isinstance(stage, dict) and stage.get("passed") is True)


def _is_hard_reason(reason: str) -> bool:
    return reason in {
        "NEWS_HARD_VETO", "NEWS_CONTEXT_BLOCKED",
        "POST_NEWS_STEALTH_NOT_NORMALIZED",
        "SESSION_VETO_TOKYO_ASIA", "SESSION_TOKYO_ASIA_BLOCKED",
        "SESSION_EXPLICITLY_BLOCKED", "SESSION_OFF_SESSION_NON_TRADABLE",
        "OFF_SESSION_NON_TRADABLE", "TRIGGER_OUTSIDE_POI",
        "RISK_INVALID", "SL_IMPOSSIBLE",
    }


def _num(value: Any, default: float | None = 0.0) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default

from __future__ import annotations

from typing import Any

from gold_sniper.strategy.contracts import (
    BlockedStage,
    DecisionAction,
    EvidenceBundle,
    HardVetoResult,
    ReadinessResult,
    ReadinessState,
    ScoreCard,
)
from gold_sniper.strategy.micro_readiness_contract import (
    evaluate_micro_readiness,
    micro_status_to_readiness,
)
from gold_sniper.strategy.poi_readiness_contract import (
    POIContractStatus,
    evaluate_poi_contract,
    poi_status_to_readiness,
)


def evaluate_readiness(
    bundle: EvidenceBundle,
    scorecard: ScoreCard,
    veto: HardVetoResult,
) -> ReadinessResult:
    if veto.replay_invalid:
        return ReadinessResult(
            state=ReadinessState.INVALID,
            suggested_action=DecisionAction.REJECT,
            reason=veto.veto_code or "REPLAY_INVALID",
            blocked_stage=veto.blocked_stage,
            section_states=_section_states(bundle),
            missing_evidence=list(scorecard.missing_evidence),
            soft_issues=list(scorecard.soft_issues),
            metadata={"veto_code": veto.veto_code, "replay_invalid": True},
        )
    if veto.hard_veto:
        return ReadinessResult(
            state=ReadinessState.REJECT,
            suggested_action=DecisionAction.REJECT,
            reason=veto.veto_code or "HARD_VETO",
            blocked_stage=veto.blocked_stage,
            section_states=_section_states(bundle),
            missing_evidence=list(scorecard.missing_evidence),
            soft_issues=list(scorecard.soft_issues),
            metadata={"veto_code": veto.veto_code, "hard_veto": True},
        )

    poi_state = _poi_state(bundle.poi)
    micro_state = _micro_state(bundle.micro)
    context_state = _context_state(bundle.context)
    liquidity_state = _liquidity_state(bundle.liquidity)
    session_state = _session_state(bundle.session)
    news_state = _news_state(bundle.news)
    risk_state = _risk_state(bundle.risk)
    timing_state = _timing_state(bundle)
    section_states = {
        "context": context_state,
        "poi": poi_state,
        "liquidity": liquidity_state,
        "timing": timing_state,
        "micro": micro_state,
        "news": news_state,
        "session": session_state,
        "risk": risk_state,
    }

    # ── P2-E Phase 7D: Strict readiness coherence blockers ─────────
    strict_block = _strict_readiness_blocker(section_states, scorecard)
    if strict_block:
        state, action, reason, stage = strict_block
        return _result(
            state,
            action,
            reason,
            stage,
            section_states,
            scorecard,
            bundle,
        )

    if poi_state == ReadinessState.INVALID.value:
        return _result(
            ReadinessState.REJECT,
            DecisionAction.REJECT,
            "POI_INVALID_OR_DEAD",
            BlockedStage.POI,
            section_states,
            scorecard,
            bundle,
        )
    if context_state in {ReadinessState.UNAVAILABLE.value, ReadinessState.INVALID.value}:
        return _result(
            ReadinessState.UNAVAILABLE,
            DecisionAction.WATCH_ONLY,
            "CONTEXT_UNAVAILABLE",
            BlockedStage.HTF_CONTEXT,
            section_states,
            scorecard,
            bundle,
        )
    if poi_state in {ReadinessState.UNAVAILABLE.value, ReadinessState.WAITING_POI.value}:
        if _context_interesting(bundle, scorecard):
            return _result(
                ReadinessState.WAITING_POI,
                DecisionAction.WAIT_FOR_BETTER_PRICE,
                "CONTEXT_INTERESTING_WAITING_POI",
                BlockedStage.POI,
                section_states,
                scorecard,
                bundle,
            )
        return _result(
            ReadinessState.WATCH_ONLY,
            DecisionAction.WATCH_ONLY,
            "POI_UNAVAILABLE_OBSERVATION_ONLY",
            BlockedStage.POI,
            section_states,
            scorecard,
            bundle,
        )
    if poi_state == ReadinessState.WATCH_ONLY.value:
        return _result(
            ReadinessState.WATCH_ONLY,
            DecisionAction.WATCH_ONLY,
            "POI_MEDIUM_CONTEXT_INTERESTING",
            BlockedStage.POI,
            section_states,
            scorecard,
            bundle,
        )
    if poi_state in {ReadinessState.READY.value, ReadinessState.WAITING_TRIGGER.value}:
        if micro_state in {ReadinessState.UNAVAILABLE.value, ReadinessState.WAITING_TRIGGER.value}:
            return _result(
                ReadinessState.WAITING_TRIGGER,
                DecisionAction.WAIT_FOR_TRIGGER,
                "POI_USABLE_WAITING_MICRO_TRIGGER",
                BlockedStage.MICRO,
                section_states,
                scorecard,
                bundle,
            )
    if _core_ready(section_states):
        return _result(
            ReadinessState.READY,
            DecisionAction.WATCH_ONLY,
            "CORE_EVIDENCE_READY",
            BlockedStage.NONE,
            section_states,
            scorecard,
            bundle,
        )
    return _result(
        ReadinessState.WATCH_ONLY,
        DecisionAction.WATCH_ONLY,
        "PARTIAL_EVIDENCE_OBSERVATION_ONLY",
        BlockedStage.NONE,
        section_states,
        scorecard,
        bundle,
    )


# ── P2-E Phase 7D: Strict readiness blockers ──────────────────────

def _strict_readiness_blocker(
    section_states: dict[str, str],
    scorecard: ScoreCard,
) -> tuple[ReadinessState, DecisionAction, str, BlockedStage] | None:
    """Block global READY when critical evidence is missing or invalid."""
    missing = set(scorecard.missing_evidence or [])

    # ── Missing context evidence — never READY ────────────────────
    if "CONTEXT_MISSING" in missing:
        return (
            ReadinessState.UNAVAILABLE,
            DecisionAction.WATCH_ONLY,
            "CONTEXT_MISSING_NOT_READY",
            BlockedStage.HTF_CONTEXT,
        )
    if "SESSION_CONTEXT_MISSING" in missing:
        return (
            ReadinessState.UNAVAILABLE,
            DecisionAction.WATCH_ONLY,
            "SESSION_CONTEXT_MISSING_NOT_READY",
            BlockedStage.SESSION,
        )
    if "RISK_CONTEXT_MISSING" in missing:
        return (
            ReadinessState.UNAVAILABLE,
            DecisionAction.WATCH_ONLY,
            "RISK_CONTEXT_MISSING_NOT_READY",
            BlockedStage.RISK,
        )
    if "NEWS_CONTEXT_MISSING" in missing:
        return (
            ReadinessState.WATCH_ONLY,
            DecisionAction.WATCH_ONLY,
            "NEWS_CONTEXT_MISSING_WATCH_ONLY",
            BlockedStage.NEWS,
        )
    if "POI_MISSING" in missing:
        return (
            ReadinessState.WAITING_POI,
            DecisionAction.WAIT_FOR_BETTER_PRICE,
            "POI_MISSING_NOT_READY",
            BlockedStage.POI,
        )
    if "LIQUIDITY_MISSING" in missing:
        return (
            ReadinessState.WAITING_TRIGGER,
            DecisionAction.WAIT_FOR_TRIGGER,
            "LIQUIDITY_MISSING_NOT_READY",
            BlockedStage.LIQUIDITY,
        )
    if "LIQUIDITY_UNAVAILABLE" in missing:
        return (
            ReadinessState.WAITING_TRIGGER,
            DecisionAction.WAIT_FOR_TRIGGER,
            "LIQUIDITY_UNAVAILABLE_NOT_READY",
            BlockedStage.LIQUIDITY,
        )
    if "MICRO_MISSING" in missing:
        return (
            ReadinessState.WAITING_TRIGGER,
            DecisionAction.WAIT_FOR_TRIGGER,
            "MICRO_MISSING_NOT_READY",
            BlockedStage.MICRO,
        )
    if "MICRO_UNAVAILABLE" in missing:
        return (
            ReadinessState.WAITING_TRIGGER,
            DecisionAction.WAIT_FOR_TRIGGER,
            "MICRO_UNAVAILABLE_NOT_READY",
            BlockedStage.MICRO,
        )

    # ── Hard section rejects / invalids — never READY ─────────────
    reject_sections: dict[str, BlockedStage] = {
        "session": BlockedStage.SESSION,
        "risk": BlockedStage.RISK,
        "news": BlockedStage.NEWS,
        "liquidity": BlockedStage.LIQUIDITY,
        "poi": BlockedStage.POI,
        "micro": BlockedStage.MICRO,
        "timing": BlockedStage.ENGINE,
    }
    for section, stage in reject_sections.items():
        state = section_states.get(section, "UNAVAILABLE")
        if state in {ReadinessState.REJECT.value, ReadinessState.INVALID.value}:
            return (
                ReadinessState.REJECT,
                DecisionAction.REJECT,
                f"{section.upper()}_{state}_NOT_READY",
                stage,
            )

    return None


# ── Result factory ──────────────────────────────────────────────────

def _result(
    state: ReadinessState,
    action: DecisionAction,
    reason: str,
    stage: BlockedStage,
    section_states: dict[str, str],
    scorecard: ScoreCard,
    bundle: EvidenceBundle,
) -> ReadinessResult:
    poi_contract_result = evaluate_poi_contract(bundle.poi)
    poi_contract = poi_contract_result.to_dict()
    micro_contract = evaluate_micro_readiness(bundle.micro).to_dict()
    poi_micro_synergy = _poi_micro_synergy_payload(bundle.poi)
    effective_poi_status = _effective_poi_status(bundle.poi, poi_contract_result)
    return ReadinessResult(
        state=state,
        suggested_action=action,
        reason=reason,
        blocked_stage=stage,
        section_states=section_states,
        missing_evidence=list(scorecard.missing_evidence),
        soft_issues=list(scorecard.soft_issues),
        metadata={
            "score_before_veto": scorecard.score_before_veto,
            "score_after_veto": scorecard.score_after_veto,
            "setup_type": bundle.setup_type.value,
            "side": bundle.side.value,
            "readiness_coherence": _readiness_coherence_metadata(section_states, scorecard),
            "poi_contract": poi_contract,
            "micro_contract": micro_contract,
            "poi_micro_synergy": poi_micro_synergy,
            "effective_poi_status": effective_poi_status.value,
        },
    )


# ── Section state extraction ───────────────────────────────────────

def _section_states(bundle: EvidenceBundle) -> dict[str, str]:
    return {
        "context": _context_state(bundle.context),
        "poi": _poi_state(bundle.poi),
        "liquidity": _liquidity_state(bundle.liquidity),
        "timing": _timing_state(bundle),
        "micro": _micro_state(bundle.micro),
        "news": _news_state(bundle.news),
        "session": _session_state(bundle.session),
        "risk": _risk_state(bundle.risk),
    }


# ── P2-E Phase 7D: Timing state ─────────────────────────────────────

def _timing_state(bundle: EvidenceBundle) -> str:
    timing = bundle.raw.get("timing") if isinstance(bundle.raw.get("timing"), dict) else {}
    readiness = str(
        timing.get("readiness_state")
        or timing.get("execution_readiness")
        or ""
    ).upper()
    if readiness == "READY":
        return ReadinessState.READY.value
    if (
        timing.get("timing_reconciled") is True
        and timing.get("timing_evidence_source") == "AGENT5_MICRO_CONTRACT"
    ):
        return ReadinessState.READY.value
    if readiness in {"WAIT_FOR_TRIGGER", "WAITING_TRIGGER"}:
        return ReadinessState.WAITING_TRIGGER.value
    if readiness == "REJECT":
        return ReadinessState.REJECT.value
    if readiness == "INVALID":
        return ReadinessState.INVALID.value
    if readiness == "UNAVAILABLE":
        return ReadinessState.UNAVAILABLE.value
    # Fallback: in_ote is a READY proxy
    if bundle.context.get("in_ote") is True or timing.get("in_ote") is True:
        return ReadinessState.READY.value
    if bundle.context.get("premium_discount") in {"PREMIUM", "DISCOUNT"} or timing.get("premium_discount") in {"PREMIUM", "DISCOUNT"}:
        return ReadinessState.WATCH_ONLY.value
    return ReadinessState.UNAVAILABLE.value


def _poi_state(poi: dict[str, Any]) -> str:
    result = evaluate_poi_contract(poi)
    return poi_status_to_readiness(_effective_poi_status(poi, result))


def _poi_micro_synergy_payload(poi: dict[str, Any]) -> dict[str, Any]:
    payload = poi.get("poi_micro_synergy") if isinstance(poi, dict) else {}
    return payload if isinstance(payload, dict) else {}


def _effective_poi_status(
    poi: dict[str, Any],
    result: Any,
) -> POIContractStatus:
    synergy = _poi_micro_synergy_payload(poi)
    if synergy.get("synergy") is True:
        raw_status = (
            synergy.get("upgraded_poi_status")
            or synergy.get("effective_poi_status")
            or poi.get("effective_poi_status")
        )
        try:
            return POIContractStatus(str(raw_status))
        except (TypeError, ValueError):
            return result.status
    return result.status


def _micro_state(micro: dict[str, Any]) -> str:
    result = evaluate_micro_readiness(micro)
    # ── Legacy fallback: when contract has no structured evidence ──
    if result.status.value == "MICRO_MISSING_DATA" and micro:
        if micro.get("outside_poi") is True or micro.get("trigger_outside_poi") is True:
            return ReadinessState.INVALID.value
        readiness = str(micro.get("execution_readiness") or micro.get("readiness_state") or "").upper()
        if readiness in {"READY", "WAITING_TRIGGER", "INVALID", "UNAVAILABLE"}:
            return ReadinessState(readiness).value if readiness in ReadinessState._value2member_map_ else readiness
        if micro.get("displacement_present") or micro.get("reclaim_confirmed") or micro.get("trigger_inside_poi"):
            if micro.get("retest_confirmed") is True:
                return ReadinessState.READY.value
            return ReadinessState.WAITING_TRIGGER.value
        # No legacy signals either → truly unavailable
        return ReadinessState.UNAVAILABLE.value
    return micro_status_to_readiness(result.status)


def _context_state(context: dict[str, Any]) -> str:
    if not context:
        return ReadinessState.UNAVAILABLE.value
    if context.get("direction") in {"BUY", "SELL", "LONG", "SHORT"} or context.get("htf_aligned") is True:
        return ReadinessState.READY.value
    if context.get("draw_on_liquidity") not in {None, "", "UNKNOWN"}:
        return ReadinessState.WATCH_ONLY.value
    return ReadinessState.UNAVAILABLE.value


def _liquidity_state(liq: dict[str, Any]) -> str:
    if not liq:
        return ReadinessState.UNAVAILABLE.value
    if liq.get("macro_break_detected") is True:
        return ReadinessState.REJECT.value
    if liq.get("promoted_by_reconciliation") is True:
        blockers = liq.get("liquidity_reconciliation_blockers") or liq.get("blockers") or []
        if (
            liq.get("liquidity_evidence_source") == "AGENT5_MICRO_CONTRACT"
            and liq.get("micro_liquidity_confirmed") is True
            and not blockers
        ):
            return ReadinessState.READY.value
        return ReadinessState.WAITING_TRIGGER.value
    readiness = str(liq.get("execution_readiness") or liq.get("readiness_state") or "").upper()
    if readiness in {"READY", "WAIT_FOR_TRIGGER", "WAITING_TRIGGER", "REJECT", "INVALID", "UNAVAILABLE"}:
        if readiness in {"WAIT_FOR_TRIGGER", "WAITING_TRIGGER"}:
            return ReadinessState.WAITING_TRIGGER.value
        if readiness == "READY":
            return ReadinessState.READY.value
        if readiness == "REJECT":
            return ReadinessState.REJECT.value
        if readiness == "INVALID":
            return ReadinessState.INVALID.value
        if readiness == "UNAVAILABLE":
            return ReadinessState.UNAVAILABLE.value
    if liq.get("sweep_rejected") is True or liq.get("sweep_detected") is True:
        return ReadinessState.READY.value
    if liq.get("liquidity_state") not in {None, "", "UNKNOWN"}:
        return ReadinessState.WATCH_ONLY.value
    return ReadinessState.UNAVAILABLE.value


def _news_state(news: dict[str, Any]) -> str:
    if not news:
        return ReadinessState.UNAVAILABLE.value
    if news.get("high_impact_window") is True or news.get("post_news_stealth") is True:
        return ReadinessState.REJECT.value
    if news.get("news_clear") is True or news.get("impact_level") in {"NONE", "LOW"}:
        return ReadinessState.READY.value
    if news.get("medium_impact_nearby") is True:
        return ReadinessState.WATCH_ONLY.value
    return ReadinessState.WATCH_ONLY.value


def _session_state(session: dict[str, Any]) -> str:
    if not session:
        return ReadinessState.UNAVAILABLE.value
    label = str(session.get("session") or session.get("session_label") or "").upper()
    if session.get("is_hard_block") is True or label in {"TOKYO", "ASIA", "ASIAN", "TOKYO_ASIA"}:
        return ReadinessState.REJECT.value
    if session.get("trading_allowed") is True or session.get("session_grade") in {"HIGH", "MEDIUM"}:
        return ReadinessState.READY.value
    return ReadinessState.WATCH_ONLY.value


def _risk_state(risk: dict[str, Any]) -> str:
    if not risk:
        return ReadinessState.UNAVAILABLE.value
    if risk.get("max_daily_loss_hit") or risk.get("max_weekly_loss_hit") or risk.get("max_drawdown_hit") or risk.get("kill_switch"):
        return ReadinessState.REJECT.value
    return ReadinessState.READY.value


def _context_interesting(bundle: EvidenceBundle, scorecard: ScoreCard) -> bool:
    component_values = scorecard.metadata.get("component_values", {}) if isinstance(scorecard.metadata, dict) else {}
    context_score = _float(component_values.get("context"), 0.0)
    liquidity_score = _float(component_values.get("liquidity"), 0.0)
    return (
        context_score >= 40.0
        or liquidity_score >= 60.0
        or bundle.context.get("htf_aligned") is True
        or bundle.context.get("direction") in {"BUY", "SELL", "LONG", "SHORT"}
    )


# ── P2-E Phase 7D: Revised core ready ──────────────────────────────

def _core_ready(section_states: dict[str, str]) -> bool:
    """Global READY requires all critical sections READY.

    news can be READY or WATCH_ONLY (soft news is not a hard block).
    All other sections must be READY.
    """
    required_ready = (
        "context", "poi", "liquidity", "timing", "micro", "session", "risk",
    )
    return all(
        section_states.get(section) == ReadinessState.READY.value
        for section in required_ready
    ) and section_states.get("news") in {
        ReadinessState.READY.value,
        ReadinessState.WATCH_ONLY.value,
    }


# ── P2-E Phase 7D: Readiness coherence metadata ────────────────────

def _section_is_non_ready_for_coherence(section: str, state: str) -> bool:
    """news=WATCH_ONLY is explicitly allowed by _core_ready() — do not flag it."""
    if section == "news" and state == ReadinessState.WATCH_ONLY.value:
        return False
    return state != ReadinessState.READY.value


def _readiness_coherence_metadata(
    section_states: dict[str, str],
    scorecard: ScoreCard,
) -> dict[str, Any]:
    missing = list(scorecard.missing_evidence or [])
    ready_blockers = [
        item for item in missing
        if item in {
            "CONTEXT_MISSING",
            "SESSION_CONTEXT_MISSING",
            "RISK_CONTEXT_MISSING",
            "NEWS_CONTEXT_MISSING",
            "POI_MISSING",
            "LIQUIDITY_MISSING",
            "LIQUIDITY_UNAVAILABLE",
            "MICRO_MISSING",
            "MICRO_UNAVAILABLE",
            "MICRO_INVALID",
        }
    ]
    non_ready_sections = {
        section: state
        for section, state in section_states.items()
        if _section_is_non_ready_for_coherence(section, state)
    }
    return {
        "missing_ready_blockers": ready_blockers,
        "non_ready_sections": non_ready_sections,
        "can_be_global_ready": not ready_blockers and _core_ready(section_states),
    }


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

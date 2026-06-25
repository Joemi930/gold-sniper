from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.strategy.micro_readiness_contract import evaluate_micro_readiness
from gold_sniper.strategy.poi_readiness_contract import POIContractStatus
from gold_sniper.strategy.poi_readiness_contract import evaluate_poi_contract


@dataclass(frozen=True)
class SetupSignalInventory:
    direction: str
    side: str
    context_present: bool
    direction_known: bool
    htf_aligned: bool
    trend_aligned_poi: bool
    counter_trend_poi: bool
    poi_present: bool
    poi_executable: bool
    poi_contract_status: str
    poi_contract_reason: str
    poi_ready_for_trigger: bool
    poi_ready: bool
    poi_too_weak: bool
    poi_invalid: bool
    poi_contract_contradictions: list[str]
    poi_quality_breakdown: dict[str, Any]
    poi_score_source: str
    poi_score_is_computed: bool
    poi_type: str
    price_bounds_present: bool
    poi_quality_score: float
    liquidity_ready: bool
    liquidity_waiting: bool
    sweep_detected: bool
    sweep_rejected: bool
    liquidity_state: str
    liquidity_quality_score: float
    micro_ready: bool
    micro_waiting: bool
    micro_partial: bool
    micro_contract_status: str
    micro_contract_reason: str
    micro_contract_missing_fields: list[str]
    micro_contract_present_fields: list[str]
    micro_contract_contradictions: list[str]
    micro_confirmed: bool
    micro_missing_data: bool
    micro_invalid: bool
    micro_outside_poi: bool
    reclaim_confirmed: bool
    retest_confirmed: bool
    displacement_present: bool
    trigger_inside_poi: bool
    micro_state: str
    timing_ready: bool
    timing_waiting: bool
    in_ote: bool
    premium_discount: str
    timing_state: str
    session_ready: bool
    session_label: str
    session_grade: str
    trading_allowed: bool
    risk_ready: bool
    news_safe: bool
    missing_core: list[str]
    present_signals: list[str]
    poi_micro_synergy: bool = False
    poi_micro_synergy_status: str = "UNKNOWN"
    poi_micro_reason: str = "UNKNOWN"
    micro_inside_poi: bool = False
    effective_poi_status: str = "UNKNOWN"
    poi_rejection_code: str = "POI_REJECTION_NONE"
    poi_rejection_severity: str = "NONE"
    poi_rejection_fatal: bool = False
    poi_rejection_recoverable: bool = False
    poi_rejection_reason: str = "NO_REJECTION"
    micro_sweep_confirmed: bool = False
    setup_sweep_evidence: bool = False
    setup_sweep_evidence_source: str = "NONE"
    liquidity_evidence_source: str = "NONE"
    micro_liquidity_confirmed: bool = False
    liquidity_reconciled: bool = False
    liquidity_reconciliation_reason: str = "NONE"
    liquidity_reconciliation_blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_setup_signal_inventory(bundle: EvidenceBundle) -> SetupSignalInventory:
    context = _safe_dict(bundle.context)
    poi = _safe_dict(bundle.poi)
    liquidity = _safe_dict(bundle.liquidity)
    micro = _safe_dict(bundle.micro)
    news = _safe_dict(bundle.news)
    session = _safe_dict(bundle.session)
    risk = _safe_dict(bundle.risk)
    timing = _safe_dict(_safe_dict(bundle.raw).get("timing"))
    selected_poi = _safe_dict(poi.get("selected_poi"))
    poi_contract = evaluate_poi_contract(poi, selected_poi=selected_poi)
    micro_contract = evaluate_micro_readiness(micro)
    synergy = _safe_dict(poi.get("poi_micro_synergy") or _safe_dict(bundle.raw).get("poi_micro_synergy"))
    rejection = _safe_dict(poi.get("poi_rejection") or (poi_contract.audit or {}).get("rejection"))
    poi_rejection_code = str(
        poi.get("poi_rejection_code")
        or rejection.get("code")
        or "POI_REJECTION_NONE"
    )
    poi_rejection_severity = str(
        poi.get("poi_rejection_severity")
        or rejection.get("severity")
        or "NONE"
    )
    poi_rejection_fatal = bool(poi.get("poi_rejection_fatal") or rejection.get("fatal"))
    poi_rejection_recoverable = bool(
        poi.get("poi_rejection_recoverable")
        or rejection.get("recoverable")
    )
    poi_rejection_reason = str(
        poi.get("poi_rejection_reason")
        or rejection.get("reason")
        or "NO_REJECTION"
    )
    poi_micro_synergy = bool(synergy.get("synergy") or poi.get("poi_micro_synergy_enabled"))
    poi_micro_synergy_status = str(
        synergy.get("status")
        or poi.get("poi_micro_synergy_status")
        or "UNKNOWN"
    )
    poi_micro_reason = str(
        synergy.get("reason")
        or poi.get("poi_micro_reason")
        or "UNKNOWN"
    )
    micro_inside_poi = bool(
        synergy.get("micro_inside_poi")
        or micro_contract.evidence.price_in_agent2_poi
        or micro_contract.evidence.trigger_inside_poi
        or poi.get("micro_inside_poi")
    )
    effective_poi_status = str(
        synergy.get("upgraded_poi_status")
        or synergy.get("effective_poi_status")
        or poi.get("effective_poi_status")
        or poi_contract.status.value
    )

    side = _upper(_enum_value(bundle.side), "NONE")
    direction = _upper(context.get("direction") or side, "UNKNOWN")
    direction_known = direction not in {"", "NONE", "UNKNOWN", "NEUTRAL"}
    context_present = bool(context)
    htf_aligned = bool(context.get("htf_aligned") or context.get("htf_context_available"))

    poi_type = _upper(
        poi.get("poi_type")
        or selected_poi.get("poi_type")
        or selected_poi.get("poi_type_normalized")
        or selected_poi.get("type")
    )
    price_bounds_present = poi_contract.has_price_bounds
    poi_present = (
        poi_contract.is_observable
        or poi_contract.is_executable
        or poi_contract.is_ready_for_trigger
        or poi_contract.is_ready
    )
    poi_executable = (
        poi_contract.is_executable
        or poi_contract.is_ready_for_trigger
        or poi_contract.is_ready
    )
    poi_ready_for_trigger = poi_contract.is_ready_for_trigger
    poi_ready = poi_contract.is_ready
    poi_too_weak = poi_contract.is_too_weak
    poi_invalid = poi_contract.is_invalid
    poi_quality_score = poi_contract.quality.final_poi_quality_score or 0.0

    liquidity_status = _upper(
        liquidity.get("liquidity_semantic_status")
        or liquidity.get("readiness_state")
        or liquidity.get("execution_readiness")
    )
    liquidity_ready = liquidity_status in {"READY", "LIQUIDITY_READY"}
    liquidity_waiting = "WAIT" in liquidity_status or liquidity_status in {
        "WAIT_FOR_TRIGGER",
        "WAITING_TRIGGER",
    }
    sweep_detected = bool(
        liquidity.get("sweep_detected")
        or liquidity.get("sweep")
        or _upper(liquidity.get("liquidity_state")) == "SWEEP"
        or _upper(liquidity.get("event")) == "SWEEP"
    )
    sweep_rejected = bool(
        liquidity.get("sweep_rejected")
        or liquidity.get("rejection_after_sweep")
        or liquidity.get("rejection_confirmed")
    )
    liquidity_state = _upper(
        liquidity.get("liquidity_state")
        or liquidity.get("event")
        or liquidity_status
    )
    liquidity_quality_score = _float(
        liquidity.get("liquidity_quality_score")
        or liquidity.get("score")
        or liquidity.get("sweep_depth_ratio")
    )
    liquidity_evidence_source = str(liquidity.get("liquidity_evidence_source") or "NONE")
    micro_liquidity_confirmed = bool(liquidity.get("micro_liquidity_confirmed"))
    liquidity_reconciled = bool(liquidity.get("liquidity_reconciled"))
    liquidity_reconciliation_reason = str(
        liquidity.get("liquidity_reconciliation_reason")
        or liquidity.get("readiness_reason")
        or "NONE"
    )
    liquidity_reconciliation_blockers = [
        str(item)
        for item in (
            liquidity.get("liquidity_reconciliation_blockers")
            or liquidity.get("blockers")
            or []
        )
        if item
    ]

    # ── P2-E Phase 11: contract-driven micro readiness ────────────
    micro_ready = micro_contract.is_confirmed
    micro_waiting = micro_contract.is_waiting_trigger
    reclaim_confirmed = bool(micro_contract.evidence.reclaim_confirmed)
    retest_confirmed = bool(micro_contract.evidence.retest_confirmed)
    displacement_present = bool(micro_contract.evidence.displacement_present)
    trigger_inside_poi = bool(micro_contract.evidence.trigger_inside_poi)
    trigger_confirmed = bool(micro_contract.evidence.trigger_confirmed)
    micro_sweep_confirmed = bool(micro_contract.evidence.sweep_1m_confirmed)
    micro_choch_detected = bool(micro_contract.evidence.choch_detected)
    # ── Legacy fallback: when contract has no structured evidence ──
    if micro_contract.is_missing_data:
        micro_status_legacy = _upper(
            micro.get("micro_semantic_status")
            or micro.get("readiness_state")
            or micro.get("execution_readiness")
        )
        micro_ready = micro_status_legacy in {"READY", "MICRO_READY"}
        micro_waiting = "WAIT" in micro_status_legacy or micro_status_legacy in {
            "WAIT_FOR_TRIGGER",
            "WAITING_TRIGGER",
        }
        reclaim_confirmed = bool(
            micro.get("reclaim_confirmed")
            or micro.get("acceptance_confirmed")
            or micro.get("vwap_reclaim")
        )
        retest_confirmed = bool(micro.get("retest_confirmed"))
        displacement_present = bool(micro.get("displacement_present"))
        trigger_inside_poi = bool(micro.get("trigger_inside_poi"))
        trigger_confirmed = bool(micro.get("trigger_confirmed"))
        micro_sweep_confirmed = bool(micro.get("sweep_1m_confirmed") or micro.get("sweep_detected"))
        micro_choch_detected = bool(micro.get("choch_detected"))
    micro_partial = bool(
        reclaim_confirmed
        or trigger_inside_poi
        or displacement_present
        or retest_confirmed
    )
    micro_trigger_location_ok = bool(trigger_inside_poi or retest_confirmed or trigger_confirmed)
    micro_contract_sweep_evidence = bool(
        poi_micro_synergy
        and micro_ready
        and micro_contract.is_confirmed
        and micro_inside_poi
        and micro_sweep_confirmed
        and micro_choch_detected
        and micro_trigger_location_ok
    )
    setup_sweep_evidence = bool(sweep_detected or micro_contract_sweep_evidence)
    if sweep_detected:
        setup_sweep_evidence_source = "AGENT3"
    elif micro_contract_sweep_evidence:
        setup_sweep_evidence_source = "MICRO_CONTRACT"
    else:
        setup_sweep_evidence_source = "NONE"

    timing_state = _upper(
        timing.get("readiness_state")
        or timing.get("execution_readiness")
    )
    in_ote = bool(context.get("in_ote") or timing.get("in_ote"))
    premium_discount = _upper(
        context.get("premium_discount")
        or timing.get("premium_discount")
    )
    timing_ready = timing_state == "READY" or in_ote
    timing_waiting = "WAIT" in timing_state

    trend_aligned_poi = _poi_aligns_with_direction(poi_type, direction)
    counter_trend_poi = _poi_opposes_direction(poi_type, direction)

    session_state = _upper(session.get("readiness_state") or session.get("execution_readiness"))
    session_label = _upper(session.get("session_label") or session.get("session"))
    session_grade = _upper(session.get("session_grade"), "UNKNOWN")
    trading_allowed = bool(session.get("trading_allowed"))
    session_ready = (
        session_state == "READY"
        or trading_allowed
        or session_grade in {"HIGH", "MEDIUM"}
    )
    risk_ready = bool(risk) and not bool(
        risk.get("max_daily_loss_hit")
        or risk.get("max_weekly_loss_hit")
        or risk.get("max_drawdown_hit")
        or risk.get("kill_switch")
    )
    news_safe = not bool(
        news.get("high_impact_window")
        or news.get("post_news_stealth")
        or news.get("news_blocked")
    )

    missing_core: list[str] = []
    if not direction_known and not poi_present:
        missing_core.extend(["DIRECTION_MISSING", "POI_MISSING"])

    present_signals: list[str] = []
    if poi_present:
        present_signals.append("POI_PRESENT")
    if poi_executable:
        present_signals.append("POI_EXECUTABLE")
    if poi_ready_for_trigger:
        present_signals.append("POI_READY_FOR_TRIGGER")
    if poi_ready:
        present_signals.append("POI_READY")
    if poi_too_weak:
        present_signals.append("POI_TOO_WEAK")
    if poi_invalid:
        present_signals.append("POI_INVALID")
    if poi_rejection_fatal:
        present_signals.append("POI_REJECTION_FATAL")
    if poi_rejection_recoverable:
        present_signals.append("POI_REJECTION_RECOVERABLE")
    if poi_contract.status == POIContractStatus.RECOVERABLE_REJECTED:
        present_signals.append("POI_RECOVERABLE_REJECTED")
    if liquidity_ready:
        present_signals.append("LIQUIDITY_READY")
    if liquidity_waiting:
        present_signals.append("LIQUIDITY_WAITING")
    if sweep_detected:
        present_signals.append("SWEEP_DETECTED")
    if sweep_rejected:
        present_signals.append("SWEEP_REJECTED")
    if micro_sweep_confirmed:
        present_signals.append("MICRO_SWEEP_CONFIRMED")
    if setup_sweep_evidence:
        present_signals.append("SETUP_SWEEP_EVIDENCE")
    if setup_sweep_evidence_source == "MICRO_CONTRACT":
        present_signals.append("SETUP_SWEEP_EVIDENCE_MICRO_CONTRACT")
    if micro_liquidity_confirmed:
        present_signals.append("MICRO_LIQUIDITY_CONFIRMED")
    if liquidity_reconciled:
        present_signals.append("LIQUIDITY_RECONCILED")
    if liquidity_evidence_source == "AGENT5_MICRO_CONTRACT":
        present_signals.append("LIQUIDITY_SOURCE_AGENT5_MICRO_CONTRACT")
    if liquidity_evidence_source == "AGENT3_MACRO":
        present_signals.append("LIQUIDITY_SOURCE_AGENT3_MACRO")
    if micro_ready:
        present_signals.append("MICRO_READY")
    if micro_waiting:
        present_signals.append("MICRO_WAITING")
    if micro_contract.is_confirmed:
        present_signals.append("MICRO_CONFIRMED")
    if micro_contract.is_waiting_trigger:
        present_signals.append("MICRO_WAITING_TRIGGER")
    if micro_contract.is_missing_data:
        present_signals.append("MICRO_MISSING_DATA")
    if micro_contract.is_invalid:
        present_signals.append("MICRO_INVALID")
    if micro_contract.is_outside_poi:
        present_signals.append("MICRO_OUTSIDE_POI")
    if micro_inside_poi:
        present_signals.append("MICRO_INSIDE_POI")
    if poi_micro_synergy:
        present_signals.append("POI_MICRO_SYNERGY")
    if effective_poi_status in {
        POIContractStatus.READY_FOR_TRIGGER.value,
        POIContractStatus.READY.value,
    }:
        present_signals.append("EFFECTIVE_POI_READY")
    if micro_partial:
        present_signals.append("MICRO_PARTIAL")
    if reclaim_confirmed:
        present_signals.append("RECLAIM_CONFIRMED")
    if retest_confirmed:
        present_signals.append("RETEST_CONFIRMED")
    if displacement_present:
        present_signals.append("DISPLACEMENT_PRESENT")
    if trigger_inside_poi:
        present_signals.append("TRIGGER_INSIDE_POI")
    if in_ote:
        present_signals.append("IN_OTE")
    if timing_ready:
        present_signals.append("TIMING_READY")
    if trend_aligned_poi:
        present_signals.append("TREND_ALIGNED_POI")
    if counter_trend_poi:
        present_signals.append("COUNTER_TREND_POI")

    return SetupSignalInventory(
        direction=direction,
        side=side,
        context_present=context_present,
        direction_known=direction_known,
        htf_aligned=htf_aligned,
        trend_aligned_poi=trend_aligned_poi,
        counter_trend_poi=counter_trend_poi,
        poi_present=poi_present,
        poi_executable=poi_executable,
        poi_contract_status=poi_contract.status.value,
        poi_contract_reason=poi_contract.reason,
        poi_ready_for_trigger=poi_ready_for_trigger,
        poi_ready=poi_ready,
        poi_too_weak=poi_too_weak,
        poi_invalid=poi_invalid,
        poi_contract_contradictions=list(poi_contract.contradictions),
        poi_quality_breakdown=poi_contract.quality.to_dict(),
        poi_score_source=poi_contract.quality.score_source,
        poi_score_is_computed=poi_contract.quality.score_is_computed,
        poi_type=poi_type,
        price_bounds_present=price_bounds_present,
        poi_quality_score=poi_quality_score,
        liquidity_ready=liquidity_ready,
        liquidity_waiting=liquidity_waiting,
        sweep_detected=sweep_detected,
        sweep_rejected=sweep_rejected,
        liquidity_state=liquidity_state,
        liquidity_quality_score=liquidity_quality_score,
        micro_sweep_confirmed=micro_sweep_confirmed,
        setup_sweep_evidence=setup_sweep_evidence,
        setup_sweep_evidence_source=setup_sweep_evidence_source,
        liquidity_evidence_source=liquidity_evidence_source,
        micro_liquidity_confirmed=micro_liquidity_confirmed,
        liquidity_reconciled=liquidity_reconciled,
        liquidity_reconciliation_reason=liquidity_reconciliation_reason,
        liquidity_reconciliation_blockers=liquidity_reconciliation_blockers,
        micro_ready=micro_ready,
        micro_waiting=micro_waiting,
        micro_partial=micro_partial,
        micro_contract_status=micro_contract.status.value,
        micro_contract_reason=micro_contract.reason,
        micro_contract_missing_fields=list(micro_contract.missing_fields),
        micro_contract_present_fields=list(micro_contract.present_fields),
        micro_contract_contradictions=list(micro_contract.contradictions),
        micro_confirmed=micro_contract.is_confirmed,
        micro_missing_data=micro_contract.is_missing_data,
        micro_invalid=micro_contract.is_invalid,
        micro_outside_poi=micro_contract.is_outside_poi,
        reclaim_confirmed=reclaim_confirmed,
        retest_confirmed=retest_confirmed,
        displacement_present=displacement_present,
        trigger_inside_poi=trigger_inside_poi,
        micro_state=micro_contract.readiness_state,
        timing_ready=timing_ready,
        timing_waiting=timing_waiting,
        in_ote=in_ote,
        premium_discount=premium_discount,
        timing_state=timing_state,
        session_ready=session_ready,
        session_label=session_label,
        session_grade=session_grade,
        trading_allowed=trading_allowed,
        risk_ready=risk_ready,
        news_safe=news_safe,
        missing_core=missing_core,
        present_signals=present_signals,
        poi_micro_synergy=poi_micro_synergy,
        poi_micro_synergy_status=poi_micro_synergy_status,
        poi_micro_reason=poi_micro_reason,
        micro_inside_poi=micro_inside_poi,
        effective_poi_status=effective_poi_status,
        poi_rejection_code=poi_rejection_code,
        poi_rejection_severity=poi_rejection_severity,
        poi_rejection_fatal=poi_rejection_fatal,
        poi_rejection_recoverable=poi_rejection_recoverable,
        poi_rejection_reason=poi_rejection_reason,
    )


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _upper(value: Any, default: str = "UNKNOWN") -> str:
    return str(value or default).upper()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _poi_aligns_with_direction(poi_type: str, direction: str) -> bool:
    poi_type = str(poi_type or "").upper()
    direction = str(direction or "").upper()
    if direction in {"BUY", "LONG", "BULLISH"}:
        return any(token in poi_type for token in ("BULL", "DEMAND", "BUY", "DISCOUNT"))
    if direction in {"SELL", "SHORT", "BEARISH"}:
        return any(token in poi_type for token in ("BEAR", "SUPPLY", "SELL", "PREMIUM"))
    return False


def _poi_opposes_direction(poi_type: str, direction: str) -> bool:
    poi_type = str(poi_type or "").upper()
    direction = str(direction or "").upper()
    if direction in {"BUY", "LONG", "BULLISH"}:
        return any(token in poi_type for token in ("BEAR", "SUPPLY", "SELL", "PREMIUM"))
    if direction in {"SELL", "SHORT", "BEARISH"}:
        return any(token in poi_type for token in ("BULL", "DEMAND", "BUY", "DISCOUNT"))
    return False

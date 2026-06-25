"""P1 replay evidence builder.

This module converts raw AgentResult outputs into the P1 EvidenceBundle contract.

Rules:
- broker-free
- replay-safe
- no live/paper execution
- no strategy decision
- no order/signal/entry/sl/tp/lot fields in P1 agent observations
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from agents.base_agent import AgentResult
from agents.agent_1_meteo import build_agent_1_observation
from agents.agent_2_cartographe import build_agent_2_observation
from agents.agent_3_liquidite import build_agent_3_observation
from agents.agent_4_fibonacci import build_agent_4_observation
from agents.agent_5_microscope import build_agent_5_observation
from agents.agent_6_sentinelle import build_agent_6_observation
from agents.agent_7_chronos import build_agent_7_observation
from gold_sniper.strategy.contracts import (
    AgentObservation,
    EvidenceBundle,
    SetupType,
    TradeSide,
)
from gold_sniper.strategy.micro_readiness_contract import (
    MicroContractStatus,
    build_micro_evidence,
    evaluate_micro_readiness,
)
from gold_sniper.strategy.liquidity_reconciliation import reconcile_liquidity_readiness
from gold_sniper.strategy.poi_micro_synergy_contract import (
    evaluate_poi_micro_synergy,
    resolve_effective_poi_status,
)
from gold_sniper.strategy.poi_readiness_contract import evaluate_poi_contract
from gold_sniper.strategy.setup_taxonomy import classify_setup


AGENT_ORDER = (
    "agent_1",
    "agent_2",
    "agent_3",
    "agent_4",
    "agent_5",
    "agent_6",
    "agent_7",
)

FORBIDDEN_EVIDENCE_KEYS = {
    "decision",
    "action",
    "order",
    "order" "_send",
    "execute",
    "execution",
    "broker",
    "trade_signal",
    "signal",
    "entry",
    "entry_price",
    "sl",
    "stop_loss",
    "tp",
    "take_profit",
    "lot",
    "lots",
    "volume",
    "position_size",
    "permission",
    "recommendation",
    "open_order",
    "close_order",
    "modify_order",
}

FORBIDDEN_EVIDENCE_KEY_PATTERNS = {
    "decision",
    "action",
    "order",
    "ordersend",
    "execute",
    "execution",
    "broker",
    "tradesignal",
    "signal",
    "entry",
    "entryprice",
    "entryzone",
    "sl",
    "stoploss",
    "tp",
    "takeprofit",
    "lot",
    "lots",
    "volume",
    "positionsize",
    "permission",
    "recommendation",
    "openorder",
    "closeorder",
    "modifyorder",
    "ordertype",
    "brokerroute",
}

# P1.1 Kasper: analytical RR fields from Agent5 that must pass through
# the forbidden-key sanitizer. These are ANALYSIS (not trade execution):
# Agent5 computes risk metrics; it does NOT send orders or decide entry.
ANALYTICAL_AGENT5_RR_FIELDS: set[str] = {
    "entry_price_candidate",
    "stop_loss_candidate",
    "target_liquidity",
    "tp1_candidate",
    "tp2_candidate",
    "rr_estimate",
    "rr_effective_estimate",
    "risk_points",
    "rr_invalid_reason",
}


def _normalized_key(value: Any) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def _is_forbidden_key(key: Any) -> bool:
    # P1.1 Kasper: analytical RR fields from Agent5 are NOT trade execution
    # fields — they are risk metrics computed for scenario evaluation.
    key_str = str(key)
    if key_str in ANALYTICAL_AGENT5_RR_FIELDS:
        return False
    normalized = _normalized_key(key)
    if normalized in FORBIDDEN_EVIDENCE_KEY_PATTERNS:
        return True
    return any(pattern in normalized for pattern in {
        "ordersend",
        "tradesignal",
        "entry",
        "stoploss",
        "takeprofit",
        "positionsize",
        "broker",
    })


def build_evidence_bundle(
    agent_results: dict[str, AgentResult | None] | Iterable[AgentResult],
    *,
    symbol: str = "XAUUSD",
    ts_utc: str | None = None,
    setup_type: SetupType | str = SetupType.UNKNOWN,
    side: TradeSide | str = TradeSide.NONE,
) -> EvidenceBundle:
    """Convert agent results into a stable P1 EvidenceBundle."""
    normalized_results = _normalize_results(agent_results)

    observations = {
        "agent_1": build_agent_1_observation(normalized_results.get("agent_1")),
        "agent_2": build_agent_2_observation(normalized_results.get("agent_2")),
        "agent_3": build_agent_3_observation(normalized_results.get("agent_3")),
        "agent_4": build_agent_4_observation(normalized_results.get("agent_4")),
        "agent_5": build_agent_5_observation(normalized_results.get("agent_5")),
        "agent_6": build_agent_6_observation(normalized_results.get("agent_6")),
        "agent_7": build_agent_7_observation(normalized_results.get("agent_7")),
    }

    sanitized_observations = {
        agent_id: sanitize_observation(obs)
        for agent_id, obs in observations.items()
    }

    context = _context_from_agent_1(sanitized_observations["agent_1"])
    poi = _poi_from_agent_2(sanitized_observations["agent_2"])
    liquidity = _liquidity_from_agent_3(sanitized_observations["agent_3"])
    micro = _micro_from_agent_5(sanitized_observations["agent_5"])
    news = _news_from_agent_6(sanitized_observations["agent_6"])
    session = _session_from_agent_7(sanitized_observations["agent_7"])
    poi, poi_micro_synergy = _with_poi_micro_synergy(poi, micro)
    risk = _risk_from_agent_7(sanitized_observations["agent_7"])
    timing = _timing_from_agent_4(sanitized_observations["agent_4"])

    resolved_ts_utc = ts_utc or _utc_now_iso()
    resolved_side = _resolve_side(side, context)
    resolved_setup_type = _safe_setup_type(setup_type)
    base_raw = {
        "schema_version": "p1.evidence_bundle.v1",
        "source": "replay.evidence_builder",
        "agent_order": list(AGENT_ORDER),
        "timing": timing,
        "poi_micro_synergy": poi_micro_synergy,
    }

    preliminary_bundle = EvidenceBundle(
        symbol=str(symbol or "XAUUSD"),
        ts_utc=resolved_ts_utc,
        setup_type=resolved_setup_type,
        side=resolved_side,
        observations=sanitized_observations,
        context=context,
        poi=poi,
        liquidity=liquidity,
        micro=micro,
        news=news,
        session=session,
        risk=risk,
        raw=base_raw,
    )
    liquidity = reconcile_liquidity_readiness(preliminary_bundle)
    timing = _reconcile_timing_from_agent_4(
        timing,
        poi=poi,
        micro=micro,
        liquidity=liquidity,
    )
    context.update({
        "in_ote": timing.get("in_ote", False),
        "premium_discount": timing.get("premium_discount", "UNKNOWN"),
        "timing_quality_score": timing.get("timing_quality_score", 0.0),
    })
    base_raw = {**base_raw, "timing": timing}

    initial_bundle = EvidenceBundle(
        symbol=str(symbol or "XAUUSD"),
        ts_utc=resolved_ts_utc,
        setup_type=resolved_setup_type,
        side=resolved_side,
        observations=sanitized_observations,
        context=context,
        poi=poi,
        liquidity=liquidity,
        micro=micro,
        news=news,
        session=session,
        risk=risk,
        raw=base_raw,
    )

    # P2-E Phase 7A: classify setup when UNKNOWN
    if initial_bundle.setup_type == SetupType.UNKNOWN:
        classification = classify_setup(initial_bundle)
        raw = dict(initial_bundle.raw or {})
        raw["setup_classification"] = {
            "setup_type": classification.setup_type.value,
            "confidence": classification.confidence,
            "reason": classification.reason,
            "family": classification.family,
            "required_ready_sections": list(classification.required_ready_sections),
            "tags": list(classification.tags),
            "evidence": classification.evidence,
        }
        return EvidenceBundle(
            symbol=initial_bundle.symbol,
            ts_utc=initial_bundle.ts_utc,
            setup_type=classification.setup_type,
            side=initial_bundle.side,
            observations=initial_bundle.observations,
            context=initial_bundle.context,
            poi=initial_bundle.poi,
            liquidity=initial_bundle.liquidity,
            micro=initial_bundle.micro,
            news=initial_bundle.news,
            session=initial_bundle.session,
            risk=initial_bundle.risk,
            raw=raw,
        )

    return initial_bundle


def build_evidence_bundle_from_blackboard(
    blackboard: Any,
    *,
    symbol: str = "XAUUSD",
    ts_utc: str | None = None,
    setup_type: SetupType | str = SetupType.UNKNOWN,
) -> EvidenceBundle:
    """Read AgentResult values from blackboard and build EvidenceBundle.

    This reads only replay-safe agent_results. It does not call broker, strategy engine,
    orchestrator, MT5, network, Discord, or any execution path.
    """
    raw = {}
    try:
        raw = blackboard.get_all().get("agent_results", {}) or {}
    except Exception:
        raw = {}

    return build_evidence_bundle(
        raw,
        symbol=symbol,
        ts_utc=ts_utc,
        setup_type=setup_type,
    )


def sanitize_observation(observation: AgentObservation) -> AgentObservation:
    payload = _strip_forbidden_keys(dict(observation.payload or {}))
    return AgentObservation(
        agent_id=observation.agent_id,
        source=observation.source,
        passed=observation.passed,
        score=_safe_float(observation.score),
        confidence=max(0.0, min(_safe_float(observation.confidence), 1.0)),
        reason=str(observation.reason or "UNKNOWN"),
        hard_filter_pass=observation.hard_filter_pass,
        payload=payload,
        missing_evidence=list(observation.missing_evidence or []),
        warnings=list(observation.warnings or []),
    )


def validate_observation_json(observation: AgentObservation) -> list[str]:
    errors: list[str] = []
    payload = observation.payload or {}

    for required in ("schema_version", "agent_id", "status", "unknown_fields"):
        if required not in payload:
            errors.append(f"{observation.agent_id}:MISSING_{required.upper()}")

    found = _find_forbidden_keys(payload)
    for key in found:
        errors.append(f"{observation.agent_id}:FORBIDDEN_KEY:{key}")

    if payload.get("status") not in {"OK", "PARTIAL", "UNKNOWN", "ERROR", "NOT_APPLICABLE"}:
        errors.append(f"{observation.agent_id}:INVALID_STATUS")

    return errors


def validate_evidence_bundle(bundle: EvidenceBundle) -> list[str]:
    errors: list[str] = []
    for agent_id in AGENT_ORDER:
        obs = bundle.observations.get(agent_id)
        if not isinstance(obs, AgentObservation):
            errors.append(f"{agent_id}:OBSERVATION_MISSING")
            continue
        errors.extend(validate_observation_json(obs))

    for section_name in ("context", "poi", "liquidity", "micro", "news", "session", "risk", "raw"):
        section = getattr(bundle, section_name)
        found = _find_forbidden_keys(section)
        for key in found:
            errors.append(f"{section_name}:FORBIDDEN_KEY:{key}")

    return errors


def bundle_to_json_dict(bundle: EvidenceBundle) -> dict[str, Any]:
    payload = bundle.to_dict()
    errors = validate_evidence_bundle(bundle)
    if errors:
        payload.setdefault("raw", {})
        payload["raw"]["validation_errors"] = errors
    return payload


def _normalize_results(agent_results: dict[str, AgentResult | None] | Iterable[AgentResult]) -> dict[str, AgentResult | None]:
    if isinstance(agent_results, dict):
        return {str(k): v for k, v in agent_results.items()}
    normalized: dict[str, AgentResult | None] = {}
    for result in agent_results or []:
        if isinstance(result, AgentResult):
            normalized[result.agent_id] = result
    return normalized


def _context_from_agent_1(obs: AgentObservation) -> dict[str, Any]:
    p = obs.payload or {}
    bias = str(p.get("directional_bias") or "UNKNOWN").upper()
    # P2.3: propagate BOS/CHoCH evidence from Agent 1 for Kasper structure shift gate
    bars_since_bos_4h = p.get("bars_since_bos_4h")
    bars_since_bos_15m = p.get("bars_since_bos_15m")
    # A recent BOS (within ~20 bars on 4H) indicates valid structure shift
    last_htf_bos = (
        isinstance(bars_since_bos_4h, (int, float)) and bars_since_bos_4h is not None
        and bars_since_bos_4h >= 0 and bars_since_bos_4h <= 20
    ) or (
        isinstance(bars_since_bos_15m, (int, float)) and bars_since_bos_15m is not None
        and bars_since_bos_15m >= 0 and bars_since_bos_15m <= 80  # 15m scale
    )
    return {
        "htf_aligned": bool(p.get("htf_aligned", False)),
        "direction": "BUY" if bias == "LONG" else "SELL" if bias == "SHORT" else "UNKNOWN",
        "primary_regime": p.get("primary_regime", "UNKNOWN"),
        "draw_on_liquidity": p.get("draw_on_liquidity", "UNKNOWN"),
        "institutional_order_flow": p.get("institutional_order_flow", "UNKNOWN"),
        "last_htf_bos": last_htf_bos,
        "last_htf_choch": False,  # Agent 1 doesn't explicitly track CHoCH
        "bars_since_bos_4h": bars_since_bos_4h,
        "bars_since_bos_15m": bars_since_bos_15m,
        "structure_4h": p.get("structure_4h", "UNKNOWN"),
        "structure_15m": p.get("structure_15m", "UNKNOWN"),
        "passed": obs.passed,
        "reason": obs.reason,
    }


def _poi_from_agent_2(obs: AgentObservation) -> dict[str, Any]:
    p = obs.payload or {}
    p2a = _p2a_connectivity_from_payload(p)
    selected = p.get("selected_poi")
    if not isinstance(selected, dict):
        selected = p2a.get("selected_poi") if isinstance(p2a.get("selected_poi"), dict) else None
    candidates = p.get("poi_candidates")
    if not isinstance(candidates, list):
        candidates = p2a.get("poi_candidates") if isinstance(p2a.get("poi_candidates"), list) else []
    price_bounds = p.get("price_bounds")
    if not isinstance(price_bounds, dict) and selected:
        price_bounds = selected.get("price_bounds")
    audit = p.get("p2a_connectivity_audit")
    if not isinstance(audit, dict):
        audit = p2a.get("audit") if isinstance(p2a.get("audit"), dict) else {}
    reference_poi = selected or (candidates[0] if candidates else {})
    if not isinstance(price_bounds, dict) and reference_poi:
        price_bounds = reference_poi.get("price_bounds")
    poi_type = (
        p.get("poi_type_normalized")
        or p.get("poi_type")
        or reference_poi.get("poi_type_normalized")
        or "UNKNOWN"
    )
    lifecycle = (
        p.get("lifecycle_normalized")
        or p.get("lifecycle_state")
        or reference_poi.get("lifecycle_normalized")
        or "UNKNOWN"
    )
    readiness = p.get("execution_readiness") or reference_poi.get("execution_readiness") or "UNAVAILABLE"
    missing = list(p.get("unknown_fields") or [])
    agent2_has_any_zone = bool(audit.get("agent2_has_any_zone") or selected or candidates)
    if not selected and not agent2_has_any_zone:
        missing.append("POI_UNAVAILABLE")
    if not price_bounds:
        missing.append("INVALID_OR_MISSING_PRICE_BOUNDS")
    failure_class = _poi_failure_class(
        selected=selected,
        candidates=candidates,
        price_bounds=price_bounds,
        readiness=str(readiness),
        obs_passed=bool(obs.passed),
        obs_reason=str(obs.reason or ""),
    )
    semantic_status = _evidence_poi_semantic_status(
        selected=selected,
        candidates=candidates,
        price_bounds=price_bounds,
        readiness=str(readiness),
        obs_reason=str(obs.reason or ""),
    )
    return {
        "schema_version": "p2a.evidence_bundle.poi.v1",
        # Backwards-compatible fields used by scorecard/PDE
        "poi_available": bool(p.get("poi_available", False) or agent2_has_any_zone),
        "selected_poi": selected,
        "selected_poi_present": bool(selected),
        "poi_type": poi_type,
        "lifecycle_state": lifecycle,
        "poi_quality_score": _safe_float(p.get("poi_quality_score") or reference_poi.get("score")),
        "mitigation_pct": _safe_float(p.get("mitigation_pct") or reference_poi.get("mitigation_pct")),
        "aligned_with_context": p.get("aligned_with_context", reference_poi.get("aligned_with_context")),
        "has_price_bounds": bool(price_bounds),
        # P2-A rich fields
        "poi_candidates": candidates,
        "price_bounds": price_bounds,
        "poi_type_normalized": poi_type,
        "lifecycle_normalized": lifecycle,
        "session_created": p.get("session_created") or reference_poi.get("session_created"),
        "execution_readiness": readiness,
        "readiness_state": readiness,
        "readiness_reason": _poi_readiness_reason(str(readiness), str(lifecycle), price_bounds, selected),
        "poi_semantic_available": bool(selected or candidates or agent2_has_any_zone),
        "poi_semantic_selected": bool(selected),
        "poi_semantic_bounds": bool(price_bounds),
        "poi_semantic_status": semantic_status,
        "poi_failure_class": failure_class,
        "missing_evidence": sorted(set(missing)),
        "warnings": list(p.get("warnings") or []),
        "connectivity_audit": {
            **audit,
            "agent2_has_any_zone": agent2_has_any_zone,
            "agent2_has_selected_ob": bool(audit.get("agent2_has_selected_ob")),
            "poi_bounds_present": bool(price_bounds),
            "selected_poi_present": bool(selected),
        },
        "passed": obs.passed,
        "reason": obs.reason,
    }


def _p2a_connectivity_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    p2a = payload.get("p2a_poi_connectivity")
    return p2a if isinstance(p2a, dict) else {}


def _poi_failure_class(
    *,
    selected: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    price_bounds: dict[str, Any] | None,
    readiness: str,
    obs_passed: bool,
    obs_reason: str,
) -> str:
    readiness_upper = str(readiness or "").upper()
    reason_upper = str(obs_reason or "").upper()
    has_selected = bool(selected)
    has_candidate = has_selected or bool(candidates)
    has_bounds = bool(price_bounds)
    if not has_candidate:
        return "NO_POI_DETECTED"
    if not has_selected and candidates:
        return "POI_CANDIDATES_ONLY"
    if not has_bounds:
        return "POI_PRESENT_NO_BOUNDS"
    if readiness_upper == "READY":
        return "POI_SELECTED_READY" if obs_passed else "POI_PRESENT_LEGACY_REJECTED"
    if readiness_upper in {"WAITING_TRIGGER", "WAIT_FOR_TRIGGER"}:
        return "POI_SELECTED_WAITING_TRIGGER"
    if "LOW" in reason_upper:
        return "POI_PRESENT_LOW_CONFIDENCE"
    if readiness_upper in {"UNAVAILABLE", "REJECT", "INVALID"}:
        return "POI_PRESENT_UNAVAILABLE"
    return "POI_PRESENT_UNEXECUTABLE"


def _evidence_poi_semantic_status(
    *,
    selected: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    price_bounds: dict[str, Any] | None,
    readiness: str,
    obs_reason: str,
) -> str:
    if not selected and not candidates:
        return "POI_ABSENT"
    if not price_bounds:
        return "POI_PRESENT_INVALID_BOUNDS"
    readiness_upper = str(readiness or "").upper()
    reason_upper = str(obs_reason or "").upper()
    if readiness_upper == "READY":
        return "POI_PRESENT_EXECUTABLE"
    if readiness_upper in {"WAITING_TRIGGER", "WAIT_FOR_TRIGGER"}:
        return "POI_PRESENT_WAITING_TRIGGER"
    if "LOW" in reason_upper:
        return "POI_PRESENT_LOW_CONFIDENCE"
    return "POI_PRESENT_UNEXECUTABLE"


def _liquidity_from_agent_3(obs: AgentObservation) -> dict[str, Any]:
    p = obs.payload or {}
    readiness = p.get("readiness_state") or p.get("execution_readiness")
    sweep_detected = bool(p.get("sweep_detected", False))
    sweep_depth = _safe_float(p.get("sweep_depth_ratio"))
    # P2.3: displacement_after_sweep is implied by a confirmed sweep with
    # depth > 0 — price moved beyond the swept level before reversing.
    # Agent 3 detects this mechanically; the field was simply not propagated.
    displacement_after_sweep = sweep_detected and sweep_depth > 0
    return {
        "liquidity_state": p.get("liquidity_state", "UNKNOWN"),
        "sweep_detected": sweep_detected,
        "sweep_rejected": bool(p.get("sweep_rejected", False)),
        "sweep_side": p.get("sweep_side", "UNKNOWN"),
        "sweep_depth_ratio": sweep_depth,
        "displacement_after_sweep": displacement_after_sweep,
        "idm_detected": bool(p.get("idm_detected", False)),
        "idm_swept": bool(p.get("idm_swept", False)),
        "draw_on_liquidity": p.get("draw_on_liquidity", "UNKNOWN"),
        "execution_readiness": readiness,
        "readiness_state": readiness,
        "readiness_reason": p.get("readiness_reason"),
        "agent3_poi_handoff": p.get("agent3_poi_handoff"),
        "agent3_consumed_poi": p.get("agent3_consumed_poi"),
        "liquidity_handoff_status": p.get("liquidity_handoff_status"),
        "passed": obs.passed,
        "reason": obs.reason,
    }


def _timing_from_agent_4(obs: AgentObservation) -> dict[str, Any]:
    p = obs.payload or {}
    readiness = p.get("execution_readiness") or p.get("readiness_state")
    return {
        "in_ote": bool(p.get("in_ote", False)),
        "premium_discount": p.get("premium_discount", "UNKNOWN"),
        "timing_quality_score": _safe_float(p.get("timing_quality_score")),
        "retracement_depth_class": p.get("retracement_depth_class", "UNKNOWN"),
        "execution_readiness": readiness,
        "readiness_state": readiness,
        "readiness_reason": p.get("readiness_reason"),
        "agent4_poi_handoff": p.get("agent4_poi_handoff"),
        "agent4_consumed_poi": p.get("agent4_consumed_poi"),
        "ote_handoff_status": p.get("ote_handoff_status"),
        "passed": obs.passed,
        "reason": obs.reason,
    }


def _reconcile_timing_from_agent_4(
    timing: dict[str, Any],
    *,
    poi: dict[str, Any],
    micro: dict[str, Any],
    liquidity: dict[str, Any],
) -> dict[str, Any]:
    """Promote timing only when post-agent micro evidence supplies the trigger.

    Agent4 stays the OTE/timing producer. This post-bundle reconciliation only
    resolves the Phase17 case where a POI-anchored micro sweep + CHoCH is already
    confirmed, while Agent4 is still waiting for an OTE retest.
    """
    enriched = dict(timing or {})
    readiness = str(
        enriched.get("readiness_state")
        or enriched.get("execution_readiness")
        or ""
    ).upper()
    if readiness in {"REJECT", "INVALID"}:
        enriched.setdefault("timing_reconciled", False)
        enriched.setdefault("timing_evidence_source", "AGENT4")
        return enriched
    if readiness == "READY":
        enriched.setdefault("timing_reconciled", False)
        enriched.setdefault("timing_evidence_source", "AGENT4")
        return enriched

    synergy = poi.get("poi_micro_synergy") if isinstance(poi.get("poi_micro_synergy"), dict) else {}
    poi_micro_synergy = bool(synergy.get("synergy") or poi.get("poi_micro_synergy_enabled"))
    micro_confirmed = bool(micro.get("micro_is_confirmed") or micro.get("micro_confirmed"))
    micro_inside_poi = bool(micro.get("price_in_agent2_poi") or micro.get("trigger_inside_poi"))
    micro_outside_poi = bool(micro.get("trigger_outside_poi") or micro.get("outside_poi"))
    micro_sweep_choch = bool(micro.get("sweep_1m_confirmed") and micro.get("choch_detected"))
    liquidity_ready = (
        str(liquidity.get("readiness_state") or liquidity.get("execution_readiness") or "").upper()
        == "READY"
    )

    should_promote = (
        poi_micro_synergy
        and micro_confirmed
        and micro_inside_poi
        and not micro_outside_poi
        and micro_sweep_choch
        and liquidity_ready
    )
    reconciliation = {
        "timing_reconciled": should_promote,
        "timing_evidence_source": "AGENT5_MICRO_CONTRACT" if should_promote else "AGENT4",
        "poi_micro_synergy": poi_micro_synergy,
        "micro_confirmed": micro_confirmed,
        "micro_inside_poi": micro_inside_poi,
        "micro_outside_poi": micro_outside_poi,
        "micro_sweep_choch": micro_sweep_choch,
        "liquidity_ready": liquidity_ready,
        "original_readiness_state": readiness or "UNKNOWN",
        "original_readiness_reason": enriched.get("readiness_reason") or enriched.get("reason"),
    }
    if not should_promote:
        enriched.setdefault("timing_reconciled", False)
        enriched.setdefault("timing_evidence_source", "AGENT4")
        enriched["timing_reconciliation"] = reconciliation
        return enriched

    enriched.update(
        {
            "execution_readiness": "READY",
            "readiness_state": "READY",
            "readiness_reason": "TIMING_POI_ANCHORED_MICRO_SWEEP_READY",
            "timing_reconciled": True,
            "timing_evidence_source": "AGENT5_MICRO_CONTRACT",
            "timing_reconciliation": reconciliation,
        }
    )
    return enriched


def _micro_from_agent_5(obs: AgentObservation) -> dict[str, Any]:
    p = obs.payload or {}

    # Build contract-grade micro evidence from raw payload
    micro_evidence = build_micro_evidence(p)
    contract = evaluate_micro_readiness(micro_evidence)

    # Legacy readiness fallback (used when contract status is MISSING_DATA)
    legacy_readiness = p.get("execution_readiness") or p.get("readiness_state") or _micro_readiness_from_payload(p)
    effective_readiness = (
        contract.readiness_state
        if contract.status != MicroContractStatus.MISSING_DATA
        else legacy_readiness
    )
    effective_reason = (
        contract.reason
        if contract.status != MicroContractStatus.MISSING_DATA
        else p.get("readiness_reason") or _micro_readiness_reason(p)
    )

    return {
        "trigger_type": p.get("trigger_type", "UNKNOWN"),
        "displacement_present": bool(p.get("displacement_present", False)),
        "reclaim_confirmed": bool(p.get("reclaim_confirmed", False)),
        "retest_confirmed": bool(p.get("retest_confirmed", False)),
        "trigger_inside_poi": bool(p.get("trigger_inside_poi", False)),
        "outside_poi": bool(p.get("outside_poi", False)),
        "trigger_outside_poi": bool(p.get("trigger_outside_poi", False)),
        "trigger_strength": _safe_float(p.get("trigger_strength")),
        "amd_phase": p.get("amd_phase", "UNKNOWN"),
        "execution_readiness": effective_readiness,
        "readiness_state": effective_readiness,
        "readiness_reason": effective_reason,
        "micro_handoff_status": p.get("micro_handoff_status"),
        "agent5_poi_handoff": p.get("agent5_poi_handoff"),
        "agent5_consumed_poi": p.get("agent5_consumed_poi"),
        "passed": obs.passed,
        "reason": obs.reason,
        # Micro evidence contract fields
        "micro_evidence": micro_evidence.to_dict(),
        "micro_contract_status": contract.status.value,
        "micro_is_confirmed": contract.is_confirmed,
        "micro_is_waiting_trigger": contract.is_waiting_trigger,
        "micro_is_invalid": contract.is_invalid,
        "micro_is_missing_data": contract.is_missing_data,
        "micro_is_outside_poi": contract.is_outside_poi,
        "micro_missing_fields": list(contract.missing_fields),
        "micro_present_fields": list(contract.present_fields),
        "micro_contradictions": list(contract.contradictions),
        "sweep_1m_confirmed": micro_evidence.sweep_1m_confirmed,
        "choch_detected": micro_evidence.choch_detected,
        "candles_1m_count": micro_evidence.candles_1m_count,
        "price_in_agent2_poi": micro_evidence.price_in_agent2_poi,
        "trigger_confirmed": micro_evidence.trigger_confirmed,
        # P1.1 Kasper: RR fields propagated from Agent5
        "entry_price_candidate": p.get("entry_price_candidate"),
        "stop_loss_candidate": p.get("stop_loss_candidate"),
        "target_liquidity": p.get("target_liquidity"),
        "tp1_candidate": p.get("tp1_candidate"),
        "tp2_candidate": p.get("tp2_candidate"),
        "rr_estimate": p.get("rr_estimate"),
        "risk_points": p.get("risk_points"),
        "rr_invalid_reason": p.get("rr_invalid_reason"),
    }


def _with_poi_micro_synergy(
    poi: dict[str, Any],
    micro: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    selected_poi = poi.get("selected_poi") if isinstance(poi.get("selected_poi"), dict) else None
    poi_contract = evaluate_poi_contract(poi, selected_poi=selected_poi)
    rejection = (poi_contract.audit or {}).get("rejection")
    if not isinstance(rejection, dict):
        rejection = {}
    micro_contract = evaluate_micro_readiness(micro)
    micro_inside_poi = bool(
        micro_contract.evidence.price_in_agent2_poi
        or micro_contract.evidence.trigger_inside_poi
    )
    synergy = evaluate_poi_micro_synergy(
        poi_contract,
        micro_contract,
        micro_inside_poi=micro_inside_poi,
    )
    effective_poi_status = resolve_effective_poi_status(poi_contract, synergy).value
    synergy_payload = {
        **synergy.to_dict(),
        "effective_poi_status": effective_poi_status,
        "poi_rejection": rejection,
    }
    enriched_poi = dict(poi)
    enriched_poi.update(
        {
            "poi_rejection": rejection,
            "poi_rejection_code": rejection.get("code"),
            "poi_rejection_source": rejection.get("source"),
            "poi_rejection_severity": rejection.get("severity"),
            "poi_rejection_fatal": bool(rejection.get("fatal")),
            "poi_rejection_recoverable": bool(rejection.get("recoverable")),
            "poi_rejection_reason": rejection.get("reason"),
            "poi_micro_synergy": synergy_payload,
            "poi_micro_synergy_enabled": synergy.synergy,
            "poi_micro_synergy_status": synergy.status.value,
            "poi_micro_reason": synergy.reason,
            "micro_confirmed": synergy.micro_confirmed,
            "micro_inside_poi": synergy.micro_inside_poi,
            "micro_outside_poi": synergy.micro_outside_poi,
            "effective_poi_status": effective_poi_status,
            "poi_micro_upgraded_poi_status": synergy.upgraded_poi_status,
            "poi_micro_remaining_blockers": list(synergy.remaining_blockers),
        }
    )
    return enriched_poi, synergy_payload


def _news_from_agent_6(obs: AgentObservation) -> dict[str, Any]:
    p = obs.payload or {}
    readiness = "REJECT" if p.get("high_impact_window") or p.get("post_news_stealth") else "READY" if p.get("news_clear") else "WATCH_ONLY"
    return {
        "news_clear": bool(p.get("news_clear", False)),
        "high_impact_window": bool(p.get("high_impact_window", False)),
        "post_news_stealth": bool(p.get("post_news_stealth", False)),
        "medium_impact_nearby": bool(p.get("medium_impact_nearby", False)),
        "impact_level": p.get("impact_level", "UNKNOWN"),
        "feed_alive": bool(p.get("feed_alive", True)),
        "calendar_source": p.get("calendar_source", "UNKNOWN"),
        "resume_at": p.get("resume_at"),
        "readiness_state": readiness,
        "passed": obs.passed,
        "reason": obs.reason,
    }


def _session_from_agent_7(obs: AgentObservation) -> dict[str, Any]:
    p = obs.payload or {}
    session_label = str(p.get("session") or "UNKNOWN").upper()
    is_hard_block = bool(p.get("is_hard_block", False))
    readiness_blocked = is_hard_block or session_label in {"TOKYO", "ASIA", "ASIAN", "TOKYO_ASIA"}
    readiness = "REJECT" if readiness_blocked else "READY" if p.get("trading_allowed") else "WATCH_ONLY"
    return {
        "session": p.get("session", "UNKNOWN"),
        "session_grade": p.get("session_grade", "LOW"),
        "trading_allowed": bool(p.get("trading_allowed", False)),
        "in_kill_zone": bool(p.get("in_kill_zone", False)),
        "kill_zone_name": p.get("kill_zone_name"),
        "risk_multiplier": _safe_float(p.get("risk_multiplier"), 1.0),
        "friday_halt": bool(p.get("friday_halt", False)),
        "friday_mode": p.get("friday_mode", "NORMAL"),
        "is_hard_block": is_hard_block,
        "readiness_state": readiness,
        "passed": obs.passed,
        "reason": obs.reason,
    }


def _risk_from_agent_7(obs: AgentObservation) -> dict[str, Any]:
    p = obs.payload or {}
    return {
        "atr_risk_multiplier": _safe_float(p.get("risk_multiplier"), 1.0),
        "session_risk_multiplier": _safe_float(p.get("risk_multiplier"), 1.0),
        "max_daily_loss_hit": False,
        "max_weekly_loss_hit": False,
        "max_drawdown_hit": False,
        "kill_switch": False,
        "passed": obs.passed,
        "reason": obs.reason,
    }


def _resolve_side(side: TradeSide | str, context: dict[str, Any]) -> TradeSide:
    if isinstance(side, TradeSide) and side != TradeSide.NONE:
        return side
    raw = str(side or "").upper()
    if raw in {"BUY", "SELL"}:
        return TradeSide(raw)
    direction = str(context.get("direction") or "UNKNOWN").upper()
    if direction == "BUY":
        return TradeSide.BUY
    if direction == "SELL":
        return TradeSide.SELL
    return TradeSide.NONE


def _safe_setup_type(setup_type: SetupType | str) -> SetupType:
    if isinstance(setup_type, SetupType):
        return setup_type
    try:
        return SetupType(str(setup_type))
    except ValueError:
        return SetupType.UNKNOWN


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _poi_readiness_reason(readiness: str, lifecycle: str, price_bounds: Any, selected: Any) -> str:
    if not selected:
        return "POI_UNAVAILABLE"
    if not price_bounds:
        return "POI_BOUNDS_MISSING"
    normalized_lifecycle = str(lifecycle or "").upper()
    if normalized_lifecycle in {"INVALIDATED", "CONSUMED"}:
        return f"POI_{normalized_lifecycle}"
    return f"POI_{str(readiness or 'UNKNOWN').upper()}"


def _micro_readiness_from_payload(p: dict[str, Any]) -> str:
    if p.get("outside_poi") is True or p.get("trigger_outside_poi") is True:
        return "INVALID"
    if p.get("displacement_present") or p.get("reclaim_confirmed") or p.get("trigger_inside_poi"):
        if p.get("retest_confirmed") is True:
            return "READY"
        return "WAITING_TRIGGER"
    return "WAITING_TRIGGER"


def _micro_readiness_reason(p: dict[str, Any]) -> str:
    if p.get("outside_poi") is True or p.get("trigger_outside_poi") is True:
        return "TRIGGER_OUTSIDE_POI"
    if p.get("retest_confirmed") is True:
        return "MICRO_READY"
    if p.get("displacement_present") or p.get("reclaim_confirmed") or p.get("trigger_inside_poi"):
        return "MICRO_WAITING_RETEST"
    return "MICRO_TRIGGER_MISSING"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _strip_forbidden_keys(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_forbidden_key(key_str):
                continue
            clean[key_str] = _strip_forbidden_keys(item)
        return clean
    if isinstance(value, list):
        return [_strip_forbidden_keys(item) for item in value]
    return value


def _find_forbidden_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            key_str = str(key)
            path = f"{prefix}.{key_str}" if prefix else key_str
            if _is_forbidden_key(key_str):
                found.append(path)
            found.extend(_find_forbidden_keys(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_forbidden_keys(item, f"{prefix}[{index}]"))
    return found

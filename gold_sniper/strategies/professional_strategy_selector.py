# LEGACY SHADOW SELECTOR - frozen after clean repo restart.
# Do not add new strategy modules here.
# Future decision authority: unified_xauusd_strategy.py, Phase 1.
# Phase 0 preserves behavior; no selector logic is changed here.

"""Shadow selector for professional ICT/SMC strategy modules."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from strategies.base_strategy import empty_context
from strategies.contextual_drawdown_guard import ContextualDrawdownGuard
from strategies.fvg_near_only import FvgNearOnly
from strategies.fvg_ny_london import FvgNyLondonOnly
from strategies.fvg_sweep_displacement_retest import FvgSweepDisplacementRetest
from strategies.no_trade_tokyo import NoTradeTokyo
from strategies.ob_five_star_strict import ObFiveStarStrict
from strategies.ob_partial_mitigation_watch import ObPartialMitigationWatch
from strategies.ob_wick_tagged_retest import ObWickTaggedRetest
from strategies.premium_strict import PremiumStrict

ENTRY_MODELS = {
    "FVG_NEAR_ONLY",
    "FVG_NY_LONDON_ONLY",
    "FVG_SWEEP_DISPLACEMENT_RETEST",
    "OB_WICK_TAGGED_RETEST",
    "OB_FIVE_STAR_STRICT",
}
WATCH_MODELS = {"OB_PARTIAL_MITIGATION_WATCH"}
PERMISSION_GATES = {"NO_TRADE_TOKYO"}
TIER_GATES = {"PREMIUM_STRICT"}
RISK_GATES = {"CONTEXTUAL_DRAWDOWN_GUARD"}
OB_STRATEGY_IDS = {"OB_WICK_TAGGED_RETEST", "OB_PARTIAL_MITIGATION_WATCH", "OB_FIVE_STAR_STRICT"}


def _get_contract(agent: dict[str, Any]) -> dict[str, Any]:
    return ((agent.get("payload") or {}).get("shadow_ict_contract") or {})


def _get_notes(agent: dict[str, Any]) -> dict[str, Any]:
    return _get_contract(agent).get("contextual_notes", {}) or {}


def _session_bucket(session: str) -> str:
    value = str(session or "UNKNOWN").upper()
    if "TOKYO" in value or "ASIA" in value:
        return "TOKYO"
    if "LONDON" in value:
        return "LONDON"
    if value.startswith("NY") or "NEW_YORK" in value:
        return "NY"
    return "OTHER"


def _score_bucket(score: float) -> str:
    value = float(score or 0.0)
    if value < 60:
        return "0-59"
    if value < 70:
        return "60-69"
    if value < 80:
        return "70-79"
    if value < 90:
        return "80-89"
    return "90-100"


def normalize_ob_lifecycle(raw: Any) -> str:
    value = str(raw or "UNKNOWN").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "FRESH": "FRESH",
        "WICK": "WICK_TAGGED",
        "WICK_TAG": "WICK_TAGGED",
        "WICK_TAGGED": "WICK_TAGGED",
        "WICKTAGGED": "WICK_TAGGED",
        "PARTIAL": "PARTIALLY_MITIGATED",
        "PARTIAL_MITIGATION": "PARTIALLY_MITIGATED",
        "PARTIALLY_MITIGATED": "PARTIALLY_MITIGATED",
        "MITIGATED": "MITIGATED",
        "CONSUMED": "CONSUMED",
        "INVALIDATED": "INVALIDATED",
        "STALE": "STALE",
    }
    return aliases.get(value, "UNKNOWN")


def _build_poi_context(raw_poi: dict[str, Any], fallback_type: str = "UNKNOWN") -> dict[str, Any]:
    evidence = raw_poi.get("five_star_evidence") or {}

    def evidence_value(raw_key: str, evidence_key: str | None = None) -> Any:
        value = raw_poi.get(raw_key)
        if value is not None:
            return value
        return evidence.get(evidence_key or raw_key)

    poi_type = str(raw_poi.get("priority_label") or raw_poi.get("type") or fallback_type or "UNKNOWN")
    raw_state = (
        raw_poi.get("human_zone_state_shadow")
        or raw_poi.get("state_shadow")
        or raw_poi.get("zone_lifecycle_state")
        or raw_poi.get("lifecycle")
        or raw_poi.get("state")
        or ("FRESH" if raw_poi.get("fresh") is True else None)
        or ("MITIGATED" if raw_poi.get("mitigated") is True else None)
        or "UNKNOWN"
    )
    normalized = normalize_ob_lifecycle(raw_state)
    distance_bucket = str(raw_poi.get("fvg_distance_bucket") or _distance_bucket(raw_poi.get("distance_to_price")))
    is_ob = poi_type.startswith("OB") or str(raw_poi.get("zone_type", "")).startswith("OB") or str(raw_poi.get("source_field", "")) == "active_ob"
    high = raw_poi.get("high", raw_poi.get("top", raw_poi.get("entry_zone_top")))
    low = raw_poi.get("low", raw_poi.get("bottom", raw_poi.get("entry_zone_bottom")))
    return {
        "source_field": raw_poi.get("source_field", "UNKNOWN"),
        "poi_type": poi_type,
        "poi_state": normalized if is_ob else str(raw_state),
        "poi_state_raw": str(raw_state),
        "lifecycle_normalized": normalized,
        "lifecycle_mapped_successfully": normalized != "UNKNOWN",
        "distance_bucket": distance_bucket,
        "score": float(raw_poi.get("score", raw_poi.get("score_shadow")) or 0.0),
        "filled_pct": float(raw_poi.get("filled_pct") or 0.0),
        "is_fvg": poi_type == "FVG_CONTINUATION_ALIGNED" or str(raw_poi.get("type", "")).startswith("FVG"),
        "is_ob": is_ob,
        "aligned_with_order_flow": bool(raw_poi.get("aligned_with_order_flow", True)),
        "liquidity_target_still_open": bool(raw_poi.get("liquidity_target_still_open", True)),
        "deepest_penetration_pct": float(raw_poi.get("deepest_penetration_pct") or 0.0),
        "close_inside_count": int(raw_poi.get("close_inside_count") or 0),
        "direction": raw_poi.get("direction") or raw_poi.get("ob_type") or "unknown",
        "high": high,
        "low": low,
        "open": raw_poi.get("open"),
        "close": raw_poi.get("close"),
        "created_at": raw_poi.get("created_at") or raw_poi.get("time"),
        "timeframe": raw_poi.get("timeframe"),
        "session_created": raw_poi.get("session_created") or raw_poi.get("created_session") or raw_poi.get("session") or evidence.get("session_created"),
        "mitigation_state": raw_poi.get("mitigation_state"),
        "is_strategy_input_candidate": bool(is_ob or poi_type.startswith("FVG")),
        "has_price_bounds": high is not None and low is not None,
        "five_star_evidence": evidence,
        "imbalance_created": evidence_value("imbalance_created"),
        "fvg_created_after_ob": evidence_value("fvg_created_after_ob", "imbalance_created"),
        "fvg_created": raw_poi.get("fvg_created"),
        "has_fvg": raw_poi.get("has_fvg"),
        "has_imbalance": raw_poi.get("has_imbalance"),
        "displacement_created": raw_poi.get("displacement_created"),
        "impulse_after_ob": raw_poi.get("impulse_after_ob"),
        "liquidity_sweep_before": evidence_value("liquidity_sweep_before"),
        "sweep_before_ob": raw_poi.get("sweep_before_ob"),
        "has_sweep_before_ob": raw_poi.get("has_sweep_before_ob"),
        "sweep_detected": raw_poi.get("sweep_detected"),
        "is_extreme_ob": evidence_value("is_extreme_ob"),
        "structural_extreme": raw_poi.get("structural_extreme"),
        "extreme_ob": raw_poi.get("extreme_ob"),
        "at_structure_extreme": raw_poi.get("at_structure_extreme"),
        "golden_hour_return": evidence_value("golden_hour_return"),
        "return_in_golden_hour": raw_poi.get("return_in_golden_hour"),
        "return_time": raw_poi.get("return_time"),
        "touched_at": raw_poi.get("touched_at"),
    }


def _extract_shadow_poi_candidates(a2_diag: dict[str, Any], primary: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items: list[dict[str, Any]] = []
    best = a2_diag.get("best_shadow_poi") or {}
    if best:
        raw_items.append(best)
    stack = a2_diag.get("shadow_agent2_poi_stack") or a2_diag.get("poi_stack") or []
    if isinstance(stack, dict):
        stack = stack.get("items") or stack.get("poi_stack") or []
    if isinstance(stack, list):
        raw_items.extend(item for item in stack if isinstance(item, dict))
    active_ob = a2_diag.get("active_ob")
    if isinstance(active_ob, dict):
        raw_active_ob = dict(active_ob)
        raw_active_ob.setdefault("source_field", "active_ob")
        raw_active_ob.setdefault("priority_label", raw_active_ob.get("priority_label") or raw_active_ob.get("zone_type") or "OB_ACTIVE")
        raw_items.append(raw_active_ob)

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, float, str]] = set()
    for item in raw_items:
        poi_type = str(item.get("priority_label") or item.get("type") or a2_diag.get("best_shadow_poi_type") or "UNKNOWN")
        poi = _build_poi_context(item, poi_type)
        key = (poi["poi_type"], poi["poi_state_raw"], poi["score"], str(poi.get("source_field") or "UNKNOWN"))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(poi)
    return candidates or [primary]


def build_strategy_context(event: dict[str, Any]) -> dict[str, Any]:
    agents = event.get("agents", {}) or {}
    a1 = agents.get("agent_1", {}) or {}
    a2 = agents.get("agent_2", {}) or {}
    a5 = agents.get("agent_5", {}) or {}
    a6 = agents.get("agent_6", {}) or {}
    a7 = agents.get("agent_7", {}) or {}
    a1_notes = _get_notes(a1)
    a5_notes = _get_notes(a5)
    a7_notes = _get_notes(a7)
    a2_payload = a2.get("payload") or {}
    a2_diag = dict(a2_payload.get("diagnostic") or {})
    if "active_ob" not in a2_diag:
        active_ob = a2_payload.get("active_ob") or a2.get("active_ob")
        if isinstance(active_ob, dict):
            a2_diag["active_ob"] = active_ob
    best = a2_diag.get("best_shadow_poi") or {}
    trigger_context = ((a5.get("payload") or {}).get("shadow_trigger_context") or {})
    session = str(a7_notes.get("session_label") or (a7.get("payload") or {}).get("session_name") or "UNKNOWN")
    poi_type = str(a2_diag.get("best_shadow_poi_type") or best.get("priority_label") or best.get("type") or "UNKNOWN")
    trigger_kind = str(a5_notes.get("trigger_kind") or trigger_context.get("trigger_kind") or "UNKNOWN")
    displacement = float(a5.get("payload", {}).get("displacement_ratio") or trigger_context.get("micro_shift_strength") or 0.0)
    primary_poi = _build_poi_context(best, poi_type)
    return {
        "context": {
            **empty_context(),
            "session": session,
            "session_bucket": _session_bucket(session),
            "primary_regime": str(a1_notes.get("primary_regime") or "UNKNOWN"),
            "delivery_phase": str(a5_notes.get("delivery_phase") or trigger_context.get("setup_family_hint") or "UNKNOWN"),
            "draw_on_liquidity": str(a1_notes.get("htf_draw_on_liquidity") or "UNKNOWN"),
            "order_flow": str(a1_notes.get("institutional_order_flow") or "UNKNOWN"),
            "news_clear": bool(a6.get("hard_filter_pass", True)),
            "news_reason": str(a6.get("reason") or "UNKNOWN"),
            "trading_allowed": bool((a7.get("payload") or {}).get("trading_allowed", True)),
        },
        "poi": primary_poi,
        "poi_candidates": _extract_shadow_poi_candidates(a2_diag, primary_poi),
        "trigger": {
            "trigger_kind": trigger_kind,
            "trigger_strength": float(a5.get("score") or 0.0),
            "has_retest": bool(trigger_context.get("retest_detected", False)),
            "has_displacement": displacement > 0,
            "has_sweep": bool((a5.get("payload") or {}).get("sweep_1m_confirmed", False)),
            "inside_poi": bool(a5_notes.get("trigger_inside_poi", trigger_context.get("poi_context_valid", False))),
            "agent5_score_bucket": _score_bucket(float(a5.get("score") or 0.0)),
            "agent5_pass": bool(a5.get("hard_filter_pass")),
        },
        "risk": {"risk_flag": "LOW", "drawdown_context_flag": False},
    }


def _distance_bucket(distance: Any) -> str:
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return "NA"
    if value <= 5:
        return "0-5"
    if value <= 10:
        return "5-10"
    if value <= 20:
        return "10-20"
    return "20+"


def modules() -> list[Any]:
    return [
        NoTradeTokyo(),
        FvgNearOnly(),
        FvgNyLondonOnly(),
        FvgSweepDisplacementRetest(),
        ObWickTaggedRetest(),
        ObPartialMitigationWatch(),
        ObFiveStarStrict(),
        PremiumStrict(),
        ContextualDrawdownGuard(),
    ]


def evaluate_professional_strategies(event: dict[str, Any]) -> dict[str, Any]:
    agents = event.get("agents", {}) or {}
    data = build_strategy_context(event)
    context = data["context"]
    trigger = data["trigger"]
    base_modules = modules()
    results = [module.evaluate(data, agents) for module in base_modules]
    primary_key = (data["poi"].get("poi_type"), data["poi"].get("poi_state_raw"), data["poi"].get("score"))
    for poi in data.get("poi_candidates", []):
        poi_key = (poi.get("poi_type"), poi.get("poi_state_raw"), poi.get("score"))
        if poi_key == primary_key or not poi.get("is_ob"):
            continue
        ob_data = deepcopy(data)
        ob_data["poi"] = poi
        for module in base_modules:
            if module.strategy_id in OB_STRATEGY_IDS:
                results.append(module.evaluate(ob_data, agents))
    candidates = [
        result for result in results
        if result["permission"] in {"CANDIDATE", "STANDARD_SHADOW", "PREMIUM_SHADOW"} and result["is_applicable"]
    ]
    entry_candidates = [result for result in candidates if result["strategy_id"] in ENTRY_MODELS]
    vetoes = [
        result for result in results
        if result.get("hard_veto") and result["strategy_id"] in (PERMISSION_GATES | RISK_GATES)
    ]
    gates = {
        "no_trade_tokyo": next((item for item in results if item["strategy_id"] == "NO_TRADE_TOKYO"), None),
        "premium_strict": next((item for item in results if item["strategy_id"] == "PREMIUM_STRICT"), None),
        "drawdown_guard": next((item for item in results if item["strategy_id"] == "CONTEXTUAL_DRAWDOWN_GUARD"), None),
    }
    selected = None
    selected_gate_ids: list[str] = []
    if not context.get("news_clear", True):
        decision = "REJECT"
        reason = "NEWS_VETO"
        blocking_layer = "NEWS"
        block_reason = "GATE_REJECT"
    elif vetoes:
        selected_gate = vetoes[0]
        decision = "WAIT"
        reason = selected_gate["reason"]
        blocking_layer = selected_gate["blocking_layer"]
        block_reason = "SESSION_VETO" if selected_gate["strategy_id"] == "NO_TRADE_TOKYO" else "GATE_REJECT"
        selected_gate_ids.append(selected_gate["strategy_id"])
    elif entry_candidates:
        selected = sorted(entry_candidates, key=lambda item: (item["score"], item["confidence"]), reverse=True)[0]
        decision = selected["permission"]
        reason = selected["reason"]
        blocking_layer = selected["blocking_layer"]
        block_reason = "NONE"
    else:
        decision = "WAIT"
        reason = "NO_PROFESSIONAL_STRATEGY_READY"
        blocking_layer = "POI"
        block_reason = "NO_POI"

    trigger_kind = str(trigger.get("trigger_kind") or "NONE").upper()
    has_trigger = trigger_kind not in {"", "NONE", "UNKNOWN", "NA"}
    is_executable = bool(selected and decision in {"STANDARD_SHADOW", "PREMIUM_SHADOW"} and has_trigger and block_reason == "NONE")
    if selected and not has_trigger:
        decision = "CANDIDATE"
        reason = "SELECTED_ENTRY_BLOCKED_TRIGGER_NONE"
        block_reason = "TRIGGER_NONE"
        is_executable = False

    premium_gate = gates["premium_strict"] or {}
    if selected and is_executable:
        if premium_gate.get("permission") == "PREMIUM_SHADOW":
            decision = "PREMIUM_SHADOW"
            selected_gate_ids.append("PREMIUM_STRICT")
        elif premium_gate:
            decision = "STANDARD_SHADOW"
            selected_gate_ids.append("PREMIUM_STRICT")

    ob_results = [result for result in results if result["strategy_id"] in OB_STRATEGY_IDS]
    ob_candidates = [
        result for result in ob_results
        if result["permission"] in {"CANDIDATE", "STANDARD_SHADOW", "PREMIUM_SHADOW"} and result["is_applicable"]
    ]
    selector_competition = {
        "ob_candidate_strategy_ids": [result["strategy_id"] for result in ob_candidates],
        "fvg_candidate_strategy_ids": [result["strategy_id"] for result in entry_candidates if str(result["strategy_id"]).startswith("FVG")],
        "ob_lost_to_fvg": bool(ob_candidates and selected and str(selected["strategy_id"]).startswith("FVG")),
        "ob_five_star_lost_to_fvg": bool(
            any(result["strategy_id"] == "OB_FIVE_STAR_STRICT" for result in ob_candidates)
            and selected
            and str(selected["strategy_id"]).startswith("FVG")
        ),
    }
    ob_diagnostics = {
        "poi_seen": any(bool(poi.get("is_ob")) for poi in data.get("poi_candidates", [])),
        "poi_candidates_seen": [
            {
                "poi_type": poi.get("poi_type"),
                "source_field": poi.get("source_field"),
                "raw_lifecycle": poi.get("poi_state_raw"),
                "normalized_lifecycle": poi.get("lifecycle_normalized"),
                "mapped_successfully": poi.get("lifecycle_mapped_successfully"),
                "is_strategy_input_candidate": poi.get("is_strategy_input_candidate"),
                "five_star_evidence": poi.get("five_star_evidence"),
            }
            for poi in data.get("poi_candidates", []) if poi.get("is_ob")
        ],
        "results": [
            {
                "strategy_id": result.get("strategy_id"),
                "permission": result.get("permission"),
                "is_applicable": result.get("is_applicable"),
                "reason": result.get("reason"),
                "blocking_layer": result.get("blocking_layer"),
                "poi_type": (result.get("poi") or {}).get("poi_type"),
                "lifecycle_normalized": (result.get("poi") or {}).get("lifecycle_normalized"),
                "ob_five_star": result.get("ob_five_star") or (result.get("poi") or {}).get("ob_five_star"),
            }
            for result in ob_results
        ],
    }

    semantics = {
        "evaluation_count": len(results),
        "candidate_count": len(candidates),
        "selected_count": 1 if selected else 0,
        "executable_shadow_entry_count": 1 if is_executable else 0,
        "non_executable_signal_count": 0 if is_executable else 1,
        "trigger_none_block_count": 1 if block_reason == "TRIGGER_NONE" else 0,
        "premium_strict_standalone_entries": 0,
    }
    final_decision = {
        "decision": decision,
        "is_executable_entry": is_executable,
        "entry_block_reason": block_reason,
        "selected_entry_strategy_id": selected["strategy_id"] if selected else None,
        "selected_gate_ids": selected_gate_ids,
    }
    return {
        "human_orchestrator_strategy_shadow": {
            "selected_strategy_id": selected["strategy_id"] if selected else None,
            "selected_entry_strategy_id": selected["strategy_id"] if selected else None,
            "decision": decision,
            "score": selected["score"] if selected else 0.0,
            "confidence": selected["confidence"] if selected else 0.0,
            "reason": reason,
            "blocking_layer": blocking_layer,
            "candidate_strategies": [result["strategy_id"] for result in candidates],
            "candidate_entry_strategies": [result["strategy_id"] for result in entry_candidates],
            "rejected_strategies": [
                {"strategy_id": result["strategy_id"], "reason": result["reason"]}
                for result in results if result["permission"] in {"REJECT", "WAIT"}
            ],
            "strategy_signal_semantics": semantics,
            "selected_entry_strategy": selected or {
                "strategy_id": None,
                "permission": "WAIT",
                "reason": "No executable entry strategy selected",
            },
            "gates": gates,
            "final_shadow_decision": final_decision,
            "ob_strategy_diagnostics": ob_diagnostics,
            "selector_competition": selector_competition,
        },
        "strategy_results": results,
        "strategy_signal_semantics": semantics,
        "selected_entry_strategy": selected,
        "gates": gates,
        "final_shadow_decision": final_decision,
        "context": context,
        "poi": data["poi"],
        "trigger": data["trigger"],
    }

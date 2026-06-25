import asyncio
import time as _time
from typing import Any

import numpy as np

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from core.visual_layers import VISUAL_LAYERS, VisualFibLevel, VisualRectangle
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger

# Shadow mode — diagnostique uniquement, aucune décision modifiée
try:
    from context.zone_lifecycle import classify_zone_pool_shadow, zone_lifecycle_pool_summary
    _ZONE_LIFECYCLE_AVAILABLE = True
except ImportError:
    _ZONE_LIFECYCLE_AVAILABLE = False



OB_MIN_SCORE = 60.0
OB_FACTOR_WEIGHTS = {
    "freshness": 20.0,
    "impulse": 20.0,
    "htf_alignment": 20.0,
    "fvg_in_zone": 20.0,
    "liquidity_confluence": 20.0,
}
OB_INSTITUTIONAL_CANDLE_BONUS = 10.0
OB_SELECTION_POOL_LIMIT = 12


def _candle_time_unix(
    candles: list,
    candle_index: int | None,
    current_time_unix: int,
    timeframe_seconds: int,
) -> int:
    if candle_index is not None and 0 <= candle_index < len(candles):
        raw_time = candles[candle_index].get("time") if isinstance(candles[candle_index], dict) else None
        if hasattr(raw_time, "timestamp"):
            return int(raw_time.timestamp())
        if isinstance(raw_time, (int, float)):
            return int(raw_time)
    age = len(candles) - 1 - int(candle_index or len(candles) - 1)
    return current_time_unix - max(age, 0) * timeframe_seconds


def _enrich_visual_times(zones: list, candles: list, timeframe_seconds: int, current_time_unix: int) -> list:
    enriched = []
    for zone in zones:
        item = dict(zone)
        item.setdefault(
            "time_start_unix",
            _candle_time_unix(candles, item.get("candle_index"), current_time_unix, timeframe_seconds),
        )
        enriched.append(item)
    return enriched


def build_shadow_agent2_poi_stack(
    obs: list[dict[str, Any]],
    fvgs: list[dict[str, Any]],
    lifecycles: list[dict[str, Any]],
    *,
    direction: str | None,
    current_price: float | None = None,
    atr_14: float | None = None,
    agent1_score: float = 0.0,
) -> dict[str, Any]:
    """Build a replay-only ICT POI stack without changing Agent 2 decisions."""
    lifecycle_by_id = {str(item.get("zone_id")): item for item in lifecycles or []}
    stack: list[dict[str, Any]] = []
    dol_aligned_default = bool(direction and agent1_score >= 65)
    order_flow_aligned_default = bool(direction)

    for zone in obs:
        if not _is_valid_order_block(zone):
            continue
        zone_dir = _result_direction(zone.get("type"))
        lifecycle = lifecycle_by_id.get(str(zone.get("candle_index")), {})
        state = _human_zone_state(zone, lifecycle)
        score = float(zone.get("ob_score", zone.get("score", 0.0)) or 0.0)
        zone_type = "OB_CONTINUATION" if zone_dir == direction else "OB_REVERSAL"
        dol_aligned = dol_aligned_default and zone_type == "OB_CONTINUATION"
        order_flow_aligned = order_flow_aligned_default and zone_type == "OB_CONTINUATION"
        usable = (
            zone_type == "OB_CONTINUATION"
            and state in ("FRESH", "WICK_TAGGED", "PARTIALLY_MITIGATED")
            and dol_aligned
            and order_flow_aligned
        )
        top, bottom = _zone_top_bottom(zone)
        created_at = lifecycle.get("created_at") or zone.get("created_at") or "UNKNOWN"
        item = {
            "zone_id": str(zone.get("candle_index", "")),
            "zone_type": zone_type,
            "direction": zone_dir,
            "timeframe": "15m",
            "created_at": created_at,
            "score": round(score, 4),
            "legacy_fresh": bool(zone.get("fresh", False)),
            "legacy_mitigated": not bool(zone.get("fresh", False)),
            "human_zone_state_shadow": state,
            "deepest_penetration_pct": _penetration_pct(lifecycle.get("deepest_penetration_pct", 0.0)),
            "mean_threshold_reached": bool(lifecycle.get("mean_threshold_reached", False)),
            "close_inside_count": int(lifecycle.get("close_inside_count", 0) or 0),
            "touch_count": int(lifecycle.get("touch_count", 0) or 0),
            "reaction_displacement_score": _round_float(lifecycle.get("reaction_displacement_score", 0.0)),
            "dol_aligned": dol_aligned,
            "order_flow_aligned": order_flow_aligned,
            "zone_still_contextually_usable": usable,
            "zone_rejection_context_reason": _zone_context_reason(state, usable),
            "top": top,
            "bottom": bottom,
        }
        item["priority_label"] = _poi_priority_label(item)
        stack.append(item)

    stack.sort(key=_shadow_poi_sort_key)
    ob_candidate = next(
        (
            item
            for item in stack
            if item["zone_type"] == "OB_CONTINUATION"
            and item["zone_still_contextually_usable"]
            and item["priority_label"] in (
                "OB_CONTINUATION_FRESH",
                "OB_CONTINUATION_WICK_TAGGED",
                "OB_CONTINUATION_PARTIALLY_MITIGATED",
            )
        ),
        None,
    )

    fvg_candidates = [
        _shadow_fvg_to_poi(fvg, direction=direction, current_price=current_price, atr_14=atr_14)
        for fvg in fvgs
    ]
    fvg_candidates = [item for item in fvg_candidates if item is not None]
    fvg_candidates.sort(key=lambda item: item.get("score_shadow", 0), reverse=True)
    best_fvg = next((item for item in fvg_candidates if item.get("usable_as_alternative_poi_shadow")), None)

    if ob_candidate:
        best = ob_candidate
        best_type = ob_candidate["priority_label"]
        reason = "OB_CONTINUATION_AVAILABLE"
    elif best_fvg:
        best = best_fvg
        best_type = "FVG_CONTINUATION_ALIGNED"
        reason = "FVG_ALTERNATIVE_NO_MATURE_OB"
        stack.append(best_fvg)
    else:
        best = None
        best_type = "WAIT_FOR_POI_DEVELOPMENT"
        reason = "WAIT_FOR_POI_DEVELOPMENT"

    return {
        "shadow_agent2_poi_stack": stack[:10],
        "shadow_fvg_continuation_alternatives": fvg_candidates[:5],
        "best_shadow_poi": best,
        "best_shadow_poi_type": best_type,
        "best_shadow_poi_reason": reason,
    }


def _human_zone_state(zone: dict[str, Any], lifecycle: dict[str, Any]) -> str:
    state = str(lifecycle.get("state") or ("FRESH" if zone.get("fresh", False) else "MITIGATED"))
    penetration = _penetration_pct(lifecycle.get("deepest_penetration_pct", 0.0))
    close_inside = int(lifecycle.get("close_inside_count", 0) or 0)
    mean_reached = bool(lifecycle.get("mean_threshold_reached", False))
    touch_count = int(lifecycle.get("touch_count", 0) or 0)
    if state in ("CONSUMED", "INVALIDATED", "STALE", "FLIPPED_BREAKER"):
        return state
    if touch_count > 0 and penetration <= 30.0 and close_inside == 0:
        return "WICK_TAGGED"
    if 30.0 < penetration <= 50.0 and not (mean_reached and close_inside > 0):
        return "PARTIALLY_MITIGATED"
    if mean_reached and close_inside > 0:
        return "MITIGATED"
    return state


def _penetration_pct(value: Any) -> float:
    try:
        pct = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if 0.0 <= pct <= 1.5:
        pct *= 100.0
    return round(pct, 4)


def _zone_top_bottom(zone: dict[str, Any]) -> tuple[float | None, float | None]:
    top = _round_float(zone.get("top", zone.get("high")))
    bottom = _round_float(zone.get("bottom", zone.get("low")))
    if top is not None and bottom is not None and bottom > top:
        top, bottom = bottom, top
    return top, bottom


def _zone_context_reason(state: str, usable: bool) -> str:
    if usable:
        return f"{state}_CONTEXTUALLY_USABLE"
    if state in ("CONSUMED", "INVALIDATED", "STALE", "FLIPPED_BREAKER"):
        return state
    return "CONTEXT_NOT_ALIGNED"


def _poi_priority_label(item: dict[str, Any]) -> str:
    if item.get("zone_type") != "OB_CONTINUATION":
        return "OTHER_POI"
    state = item.get("human_zone_state_shadow")
    if state == "FRESH":
        return "OB_CONTINUATION_FRESH"
    if state == "WICK_TAGGED":
        return "OB_CONTINUATION_WICK_TAGGED"
    if state == "PARTIALLY_MITIGATED":
        return "OB_CONTINUATION_PARTIALLY_MITIGATED"
    return f"OB_CONTINUATION_{state}"


def _shadow_poi_sort_key(item: dict[str, Any]) -> tuple[int, float]:
    priority = {
        "OB_CONTINUATION_FRESH": 0,
        "OB_CONTINUATION_WICK_TAGGED": 1,
        "OB_CONTINUATION_PARTIALLY_MITIGATED": 2,
        "FVG_CONTINUATION_ALIGNED": 3,
    }.get(item.get("priority_label"), 9)
    return (priority, -float(item.get("score", item.get("score_shadow", 0.0)) or 0.0))


def _shadow_fvg_to_poi(
    fvg: dict[str, Any],
    *,
    direction: str | None,
    current_price: float | None,
    atr_14: float | None,
) -> dict[str, Any] | None:
    fvg_direction = fvg.get("direction")
    if not fvg_direction:
        fvg_type = fvg.get("type")
        fvg_direction = "LONG" if fvg_type == "BULLISH" else ("SHORT" if fvg_type == "BEARISH" else None)
    if fvg_direction not in ("LONG", "SHORT"):
        return None
    high = _round_float(fvg.get("high", fvg.get("top")))
    low = _round_float(fvg.get("low", fvg.get("bottom")))
    if high is None or low is None:
        return None
    if low > high:
        high, low = low, high
    mid = (high + low) / 2.0
    distance = abs(float(current_price) - mid) if current_price is not None else 0.0
    filled_pct = float(fvg.get("filled_pct", 100.0 if fvg.get("is_filled") else 0.0) or 0.0)
    age_minutes = int(fvg.get("age_minutes", int(fvg.get("age", 0) or 0) * 15) or 0)
    aligned = fvg_direction == direction
    stale = age_minutes > 24 * 60
    too_far = bool(atr_14 and atr_14 > 0 and distance > 3.0 * float(atr_14))
    state = "FILLED" if filled_pct >= 100.0 else ("STALE" if stale else ("PARTIALLY_FILLED" if filled_pct > 0 else "FRESH"))
    usable = bool(aligned and state in ("FRESH", "PARTIALLY_FILLED") and not too_far)
    score = min(100.0, max(0.0, float(fvg.get("score_shadow", fvg.get("score", 0.0)) or 0.0)))
    return {
        "type": "FVG_CONTINUATION",
        "direction": fvg_direction,
        "timeframe": fvg.get("timeframe", "15m"),
        "score_shadow": round(score, 4),
        "created_at": fvg.get("created_at", "UNKNOWN"),
        "age_minutes": age_minutes,
        "filled_pct": round(filled_pct, 4),
        "distance_to_price": round(distance, 4),
        "aligned_with_dol": aligned,
        "aligned_with_order_flow": aligned,
        "liquidity_target_still_open": not stale,
        "state_shadow": state,
        "usable_as_alternative_poi_shadow": usable,
        "reason": "FVG_CONTINUATION_ALIGNED" if usable else "FVG_NOT_USABLE_AS_ALTERNATIVE",
        "priority_label": "FVG_CONTINUATION_ALIGNED" if usable else "FVG_CONTINUATION_REJECTED",
        "top": high,
        "bottom": low,
    }


def build_replay_agent_2_diagnostic(
    *,
    candle: dict[str, Any],
    blackboard: BlackBoard,
    candles_15m: list,
    candles_4h: list,
    obs: list,
    fvgs: list,
    selected_ob: dict | None,
    atr_14: float | None,
    direction: str | None,
    final_reason: str,
    hard_filter_pass: bool,
    score: float,
) -> dict[str, Any]:
    replay_meta = _safe_read(blackboard, "meta.replay") or {}
    if replay_meta.get("eval_active") is False:
        return {
            "time": _iso_time(candle.get("time")),
            "phase": replay_meta.get("phase"),
            "eval_active": False,
            "final_reason": final_reason,
            "hard_filter_pass": hard_filter_pass,
            "score": float(score),
            "direction": direction,
            "shadow_agent2_poi_stack": [],
            "best_shadow_poi": None,
            "best_shadow_poi_type": "WARMUP_DIAGNOSTIC_COMPACT",
            "best_shadow_poi_reason": "WARMUP_DIAGNOSTIC_COMPACT",
        }
    valid_zones = [zone for zone in obs if _is_valid_order_block(zone)]
    fresh_zones = [zone for zone in valid_zones if zone.get("fresh", False)]
    mitigated_zones = [zone for zone in valid_zones if not zone.get("fresh", False)]
    best_raw_score_ob = max(valid_zones, key=_order_block_score) if valid_zones else None
    best_fresh_ob = max(fresh_zones, key=_order_block_score) if fresh_zones else None
    selected = _zone_summary(selected_ob, candles_15m) if selected_ob else None
    mitigation = _mitigation_diagnostic(selected_ob, candles_15m)
    selected_age_bars = int(selected_ob.get("age", 0)) if selected_ob else None
    selection_meta = _selection_metadata(selected_ob)

    # ── Shadow ZoneLifecycle (P1.27) — diagnostique uniquement ─────────────
    shadow_lifecycle_summary: dict = {}
    shadow_selected_lifecycle: dict | None = None
    lifecycles: list[dict[str, Any]] = []
    if _ZONE_LIFECYCLE_AVAILABLE and obs:
        try:
            lifecycles = classify_zone_pool_shadow(obs, candles_15m, atr_14=atr_14)
            shadow_lifecycle_summary = zone_lifecycle_pool_summary(lifecycles)
            if selected_ob is not None:
                selected_idx = selected_ob.get("candle_index")
                for lc in lifecycles:
                    if lc["zone_id"] == str(selected_idx):
                        shadow_selected_lifecycle = dict(lc)
                        break
        except Exception:
            pass
    # ──────────────────────────────────────────────────────────────────────

    # ── Shadow P1.35 — Exposition des FVG et micro-POI ─────────────
    shadow_fvg_candidates = []
    for fvg in fvgs:
        f_type = fvg.get("type")
        f_dir = "LONG" if f_type == "BULLISH" else ("SHORT" if f_type == "BEARISH" else "UNKNOWN")
        f_top = _round_float(fvg.get("top"))
        f_bot = _round_float(fvg.get("bottom"))
        f_mid = _round_float((f_top + f_bot) / 2) if f_top is not None and f_bot is not None else None
        f_age = int(fvg.get("age", 0)) * 15 if fvg.get("age") is not None else 0
        
        # Determine filled status roughly (real check requires iterating past candles, we'll keep it basic)
        # Agent 2 current FVG detection logic handles basic validity.
        is_filled = not bool(fvg.get("valid", True))
        
        shadow_fvg_candidates.append({
            "timeframe": "15m",
            "direction": f_dir,
            "high": f_top,
            "low": f_bot,
            "mid": f_mid,
            "created_at": _iso_time(candles_15m[fvg.get("candle_index")].get("time")) if isinstance(fvg.get("candle_index"), int) and 0 <= fvg.get("candle_index") < len(candles_15m) else "UNKNOWN",
            "age_minutes": f_age,
            "filled_pct": 100.0 if is_filled else 0.0,
            "is_filled": is_filled,
            "aligned_with_agent1": f_dir == direction,
            "distance_to_price": 0.0, # requires current price which we don't have easily here
            "score_shadow": _order_block_score(fvg),
            "reason_shadow": "SHADOW_FVG_CANDIDATE"
        })

    shadow_ltf_micro_poi_candidates = ["5m unavailable in current data pipeline"]
    
    # ── Shadow P1.36 — FVG Continuation POI ─────────────
    shadow_fvg_continuation_pois = []
    best_shadow_fvg_continuation_poi = None
    best_shadow_fvg_score = 0
    best_shadow_fvg_reason = "NO_FVG"

    if len(candles_15m) > 5 and atr_14:
        high_arr = np.array([c.get("high", 0) for c in candles_15m], dtype=float)
        low_arr = np.array([c.get("low", 0) for c in candles_15m], dtype=float)
        length = len(high_arr)
        min_size = 0.1 * atr_14
        
        a1_score = float(_safe_read(blackboard, "agents.agent_1.score") or 0.0)
        a7_data = _safe_read(blackboard, "agents.agent_7") or {}
        a7_pass = bool(a7_data.get("hard_filter_pass", False))
        
        c_last = candles_15m[-1]
        current_price = float(c_last.get("close", 0.0))
        
        for i in range(2, length):
            fvg_top = 0.0
            fvg_bottom = 0.0
            fvg_type = "UNKNOWN"
            mitigated = False
            
            if direction == "LONG":
                fvg_size = low_arr[i] - high_arr[i - 2]
                if fvg_size >= min_size:
                    fvg_top = float(low_arr[i])
                    fvg_bottom = float(high_arr[i - 2])
                    fvg_type = "BULLISH"
                    equilibrium = (fvg_top + fvg_bottom) / 2
                    mitigated = any(low_arr[j] <= equilibrium for j in range(i + 1, length))
            elif direction == "SHORT":
                fvg_size = low_arr[i - 2] - high_arr[i]
                if fvg_size >= min_size:
                    fvg_top = float(low_arr[i - 2])
                    fvg_bottom = float(high_arr[i])
                    fvg_type = "BEARISH"
                    equilibrium = (fvg_top + fvg_bottom) / 2
                    mitigated = any(high_arr[j] >= equilibrium for j in range(i + 1, length))
                    
            if fvg_type != "UNKNOWN":
                age_bars = length - 1 - i
                age_minutes = age_bars * 15
                f_mid = (fvg_top + fvg_bottom) / 2
                
                score_fvg = 0
                f_dir = "LONG" if fvg_type == "BULLISH" else "SHORT"
                if f_dir == direction: score_fvg += 25
                if a1_score >= 80: score_fvg += 20
                if not mitigated: score_fvg += 20
                if age_minutes <= 180: score_fvg += 10
                if abs(current_price - f_mid) <= 2.0 * atr_14: score_fvg += 10
                
                if f_dir == "LONG" and current_price > fvg_top: score_fvg += 10
                elif f_dir == "SHORT" and current_price < fvg_bottom: score_fvg += 10
                
                if a7_pass: score_fvg += 5
                
                score_fvg = min(100, max(0, score_fvg))
                
                created_at_iso = _iso_time(candles_15m[i].get("time")) or "UNKNOWN"
                current_time_iso = _iso_time(candle.get("time")) or "UNKNOWN"
                no_lookahead = {
                    "created_before_decision": created_at_iso <= current_time_iso,
                    "no_future_data_used": True,
                    "is_valid": created_at_iso <= current_time_iso
                }
                
                poi = {
                    "type": "FVG_CONTINUATION_POI",
                    "timeframe": "15m",
                    "direction": f_dir,
                    "high": round(fvg_top, 2),
                    "low": round(fvg_bottom, 2),
                    "mid": round(f_mid, 2),
                    "created_at": created_at_iso,
                    "age_minutes": age_minutes,
                    "filled_pct": 100.0 if mitigated else 0.0,
                    "is_filled": mitigated,
                    "aligned_with_htf": f_dir == direction,
                    "distance_to_price": round(abs(current_price - f_mid), 2),
                    "score_shadow": score_fvg,
                    "state_shadow": "FILLED" if mitigated else "FRESH",
                    "reason_shadow": "FVG_CONTINUATION_POI",
                    "shadow_fvg_no_lookahead_validation": no_lookahead
                }
                shadow_fvg_continuation_pois.append(poi)
                
        shadow_fvg_continuation_pois.sort(key=lambda x: x["score_shadow"], reverse=True)
        if shadow_fvg_continuation_pois:
            best_shadow_fvg_continuation_poi = shadow_fvg_continuation_pois[0]
            best_shadow_fvg_score = best_shadow_fvg_continuation_poi["score_shadow"]
            best_shadow_fvg_reason = f"SCORE={best_shadow_fvg_score}"

    current_price_for_stack = _round_float(candle.get("close"))
    if current_price_for_stack is None and candles_15m:
        current_price_for_stack = _round_float(candles_15m[-1].get("close"))
    try:
        agent1_score = float(_safe_read(blackboard, "agents.agent_1.score") or 0.0)
    except (TypeError, ValueError):
        agent1_score = 0.0
    shadow_poi_stack = build_shadow_agent2_poi_stack(
        obs,
        shadow_fvg_continuation_pois,
        lifecycles,
        direction=direction,
        current_price=current_price_for_stack,
        atr_14=atr_14,
        agent1_score=agent1_score,
    )
    # ──────────────────────────────────────────────────────────────────────

    return {
        "time": _iso_time(candle.get("time")),
        "phase": replay_meta.get("phase"),
        "eval_active": replay_meta.get("eval_active"),
        "candles_15m_count": len(candles_15m),
        "candles_4H_count": len(candles_4h),
        "detected_ob_count": len(obs),
        "detected_fvg_count": len(fvgs),
        "candidate_poi_count": len(obs) + len(fvgs),
        "fresh_zone_count": len(fresh_zones),
        "mitigated_zone_count": len(mitigated_zones),
        "rejected_zone_count": len(mitigated_zones),
        "selection_policy": selection_meta.get("selection_policy", "fresh_first_score_desc"),
        "ote_confluence_available": selection_meta.get("ote_confluence_available", False),
        "selected_ob_has_ote_overlap": selection_meta.get("selected_ob_has_ote_overlap", False),
        "selected_ob_overlap_size_points": selection_meta.get("selected_ob_overlap_size_points", 0.0),
        "best_raw_score_ob": _zone_summary(best_raw_score_ob, candles_15m),
        "best_fresh_ob": _zone_summary(best_fresh_ob, candles_15m),
        "selected_ob_was_fresh": bool(selected_ob.get("fresh", False)) if selected_ob else None,
        "selected_zone": selected,
        "selected_zone_time": selected.get("time") if selected else None,
        "selected_zone_age_bars": selected_age_bars,
        "selected_zone_age_minutes": selected_age_bars * 15 if selected_age_bars is not None else None,
        "mitigation_check_window": mitigation["window"],
        "mitigation_touch_count": mitigation["touch_count"],
        "mitigation_reason": mitigation["reason"],
        "candidate_samples": [_zone_summary(zone, candles_15m) for zone in obs[:3]],
        "atr_14": round(float(atr_14 or 0.0), 6),
        "final_reason": final_reason,
        "hard_filter_pass": hard_filter_pass,   # NON MODIFIÉ
        "score": float(score),                   # NON MODIFIÉ
        "direction": direction,                  # NON MODIFIÉ
        # ── Shadow diagnostique P1.27 — ne compte pas dans les décisions ──
        "shadow_zone_lifecycle_summary": shadow_lifecycle_summary,
        "shadow_selected_zone_lifecycle": shadow_selected_lifecycle,
        # ── Shadow P1.35 — FVG et LTF ──
        "shadow_fvg_candidates": shadow_fvg_candidates,
        "shadow_ltf_micro_poi_candidates": shadow_ltf_micro_poi_candidates,
        # ── Shadow P1.36 — FVG Continuation POI ──
        "shadow_fvg_continuation_pois": shadow_fvg_continuation_pois,
        "best_shadow_fvg_continuation_poi": best_shadow_fvg_continuation_poi,
        "best_shadow_fvg_score": best_shadow_fvg_score,
        "best_shadow_fvg_reason": best_shadow_fvg_reason,
        "shadow_agent2_poi_stack": shadow_poi_stack["shadow_agent2_poi_stack"],
        "shadow_fvg_continuation_alternatives": shadow_poi_stack["shadow_fvg_continuation_alternatives"],
        "best_shadow_poi": shadow_poi_stack["best_shadow_poi"],
        "best_shadow_poi_type": shadow_poi_stack["best_shadow_poi_type"],
        "best_shadow_poi_reason": shadow_poi_stack["best_shadow_poi_reason"],
        
        # ── Shadow P1.41 — Human Zone Lifecycle ──
        "human_zone_state_shadow": shadow_selected_lifecycle.get("state") if shadow_selected_lifecycle else None,
        "deepest_penetration_pct": shadow_selected_lifecycle.get("deepest_penetration_pct") if shadow_selected_lifecycle else None,
        "mean_threshold_reached": shadow_selected_lifecycle.get("mean_threshold_reached") if shadow_selected_lifecycle else None,
        "touch_count": shadow_selected_lifecycle.get("touch_count") if shadow_selected_lifecycle else None,
        "close_inside_count": shadow_selected_lifecycle.get("close_inside_count") if shadow_selected_lifecycle else None,
        "reaction_displacement_score": shadow_selected_lifecycle.get("reaction_displacement_score") if shadow_selected_lifecycle else None,
        "zone_still_contextually_usable": (
            True if shadow_selected_lifecycle and shadow_selected_lifecycle.get("state") in ("FRESH", "WICK_TAGGED", "PARTIALLY_MITIGATED") else False
        ) if shadow_selected_lifecycle else False,
        "zone_rejection_context_reason": shadow_selected_lifecycle.get("invalidation_reason") or shadow_selected_lifecycle.get("state") if shadow_selected_lifecycle else None,
    }


def _safe_read(blackboard: BlackBoard, path: str) -> Any:
    try:
        return blackboard.read_sync(path)
    except KeyError:
        return None


def _zone_summary(zone: dict | None, candles: list) -> dict[str, Any] | None:
    if not zone:
        return None
    index = zone.get("candle_index")
    raw_time = candles[index].get("time") if isinstance(index, int) and 0 <= index < len(candles) else None
    return {
        "type": zone.get("type"),
        "top": _round_float(zone.get("top")),
        "bottom": _round_float(zone.get("bottom")),
        "score": _round_float(zone.get("score", zone.get("ob_score"))),
        "fresh": bool(zone.get("fresh", False)),
        "valid": bool(zone.get("valid", False)),
        "candle_index": index,
        "time": _iso_time(raw_time),
        "age_bars": zone.get("age"),
        "age_minutes": int(zone.get("age", 0)) * 15 if zone.get("age") is not None else None,
        "grade": zone.get("grade"),
        "score_factors": zone.get("score_factors", {}),
    }


def _order_block_score(zone: dict) -> float:
    try:
        return float(zone.get("ob_score", zone.get("score", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_valid_order_block(zone: dict) -> bool:
    return bool(zone.get("valid", False)) and _order_block_score(zone) >= OB_MIN_SCORE


def rank_order_blocks_fresh_first(obs: list[dict]) -> list[dict]:
    """Rank valid fresh zones first, then mitigated zones, score-desc within each group."""
    valid_obs = [zone for zone in obs if _is_valid_order_block(zone)]
    return sorted(
        valid_obs,
        key=lambda zone: (not bool(zone.get("fresh", False)), -_order_block_score(zone)),
    )


def select_best_order_block(obs: list[dict]) -> dict | None:
    """Select fresh valid zones before mitigated zones, preserving score order."""
    valid_obs = rank_order_blocks_fresh_first(obs)
    if not valid_obs:
        return None
    return valid_obs[0]


def select_best_order_block_with_ote_confluence(
    obs: list[dict],
    candles_15m: list,
    swings: dict,
    direction: str,
) -> dict | None:
    """Select fresh valid OB with Agent 4 OTE overlap before plain score order."""
    ranked = rank_order_blocks_fresh_first(obs)
    if not ranked:
        return None
    fresh_obs = [zone for zone in ranked if bool(zone.get("fresh", False))]
    if not fresh_obs:
        return _with_selection_metadata(ranked[0], _selection_meta(False, False, 0.0, "fresh_first_score_desc"))

    confluence: list[tuple[dict, dict]] = []
    for zone in fresh_obs:
        meta = _order_block_ote_confluence(zone, candles_15m, swings, direction)
        if meta["selected_ob_has_ote_overlap"]:
            confluence.append((zone, meta))

    if confluence:
        selected, meta = max(confluence, key=lambda item: _order_block_score(item[0]))
        meta["selection_policy"] = "fresh_ote_confluence_score_desc"
        meta["ote_confluence_available"] = True
        return _with_selection_metadata(selected, meta)

    return _with_selection_metadata(
        fresh_obs[0],
        _selection_meta(False, False, 0.0, "fresh_first_score_desc"),
    )


def _order_block_ote_confluence(zone: dict, candles_15m: list, swings: dict, direction: str) -> dict[str, Any]:
    from agents.agent_4_fibonacci import calculate_ote_levels_anchored_to_poi

    selection = calculate_ote_levels_anchored_to_poi(candles_15m, swings, direction, zone)
    levels = selection.get("levels") if selection else None
    if not levels:
        return _selection_meta(False, False, 0.0, "fresh_first_score_desc")
    zone_bottom, zone_top = _order_block_entry_bounds(zone)
    overlap_low, overlap_high, overlap_size = _zone_overlap(zone_bottom, zone_top, levels.get("ote_low"), levels.get("ote_high"))
    has_overlap = overlap_size > 0.0
    meta = _selection_meta(True, has_overlap, overlap_size, "fresh_first_score_desc")
    meta.update(
        {
            "ote_low": _round_float(levels.get("ote_low")),
            "ote_high": _round_float(levels.get("ote_high")),
            "overlap_low": _round_float(overlap_low),
            "overlap_high": _round_float(overlap_high),
        }
    )
    return meta


def _with_selection_metadata(zone: dict, meta: dict[str, Any]) -> dict:
    selected = dict(zone)
    selected["ote_confluence_selection"] = meta
    return selected


def _selection_metadata(zone: dict | None) -> dict[str, Any]:
    if not isinstance(zone, dict):
        return {}
    meta = zone.get("ote_confluence_selection")
    return meta if isinstance(meta, dict) else {}


def _selection_meta(
    available: bool,
    has_overlap: bool,
    overlap_size: float,
    policy: str,
) -> dict[str, Any]:
    return {
        "selection_policy": policy,
        "ote_confluence_available": bool(available),
        "selected_ob_has_ote_overlap": bool(has_overlap),
        "selected_ob_overlap_size_points": round(float(overlap_size or 0.0), 6),
    }


def _order_block_entry_bounds(zone: dict) -> tuple[float | None, float | None]:
    bottom = zone.get("entry_zone_bottom", zone.get("bottom"))
    top = zone.get("entry_zone_top", zone.get("top"))
    return _to_float(bottom), _to_float(top)


def _zone_overlap(
    bottom_a: float | None,
    top_a: float | None,
    bottom_b: float | None,
    top_b: float | None,
) -> tuple[float | None, float | None, float]:
    if None in (bottom_a, top_a, bottom_b, top_b):
        return None, None, 0.0
    assert bottom_a is not None and top_a is not None and bottom_b is not None and top_b is not None
    low = max(bottom_a, bottom_b)
    high = min(top_a, top_b)
    if low > high:
        return None, None, 0.0
    return low, high, round(high - low, 6)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mitigation_diagnostic(zone: dict | None, candles: list) -> dict[str, Any]:
    if not zone:
        return {"touch_count": 0, "reason": "NO_SELECTED_ZONE", "window": None}
    index = zone.get("candle_index")
    if not isinstance(index, int) or index < 0 or index >= len(candles):
        return {"touch_count": 0, "reason": "INVALID_ZONE_INDEX", "window": None}

    bottom = float(zone["bottom"])
    top = float(zone["top"])
    start_index = index + 2
    end_index = len(candles) - 1
    touches = []
    for touch_index in range(start_index, len(candles)):
        current = candles[touch_index]
        high = float(current["high"])
        low = float(current["low"])
        if high >= bottom and low <= top:
            touches.append(
                {
                    "index": touch_index,
                    "time": _iso_time(current.get("time")),
                    "high": _round_float(high),
                    "low": _round_float(low),
                }
            )

    first_touch = touches[0] if touches else None
    return {
        "touch_count": len(touches),
        "reason": "TOUCHED_AFTER_CREATION" if touches else "NO_TOUCH_AFTER_CREATION",
        "first_touch": first_touch,
        "window": {
            "start_index": start_index,
            "start_time": _iso_time(candles[start_index].get("time")) if start_index < len(candles) else None,
            "end_index": end_index,
            "end_time": _iso_time(candles[end_index].get("time")) if candles else None,
            "bars": max(0, len(candles) - start_index),
            "includes_current_15m_candle": end_index >= start_index,
        },
    }


def _iso_time(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _round_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _estimate_atr_14(candles: list) -> float | None:
    if len(candles) < 15:
        return None
    ranges = []
    for candle in candles[-15:]:
        try:
            ranges.append(float(candle["high"]) - float(candle["low"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def _publish_visual_layers(ob_zones: list, fvg_zones: list, current_time_unix: int) -> None:
    """
    Publie les Order Blocks et FVG actifs dans le store de calques visuels.
    Appellee a la fin de chaque cycle de calcul de l'Agent 2.
    """
    layers = []

    for ob in ob_zones:
        if not ob.get("fresh", True):
            continue

        is_bullish = ob.get("type") == "BULLISH"
        score = ob.get("score", ob.get("ob_score", 0))

        if is_bullish:
            if score >= 75:
                color = "rgba(16, 185, 129, 0.20)"
                border_color = "rgba(16, 185, 129, 0.80)"
            elif score >= 55:
                color = "rgba(16, 185, 129, 0.12)"
                border_color = "rgba(16, 185, 129, 0.50)"
            else:
                color = "rgba(16, 185, 129, 0.06)"
                border_color = "rgba(16, 185, 129, 0.25)"
        else:
            if score >= 75:
                color = "rgba(239, 68, 68, 0.20)"
                border_color = "rgba(239, 68, 68, 0.80)"
            elif score >= 55:
                color = "rgba(239, 68, 68, 0.12)"
                border_color = "rgba(239, 68, 68, 0.50)"
            else:
                color = "rgba(239, 68, 68, 0.06)"
                border_color = "rgba(239, 68, 68, 0.25)"

        direction_label = "Bull" if is_bullish else "Bear"
        layers.append(
            VisualRectangle(
                time_start=ob.get("time_start_unix", current_time_unix - 3600),
                time_end=ob.get("time_end_unix", None),
                price_top=ob["top"],
                price_bottom=ob["bottom"],
                color=color,
                border_color=border_color,
                label=f"OB {direction_label} {score:.0f}pts",
            )
        )

    for fvg in fvg_zones:
        if not fvg.get("fresh", True):
            continue

        is_bullish = fvg.get("type") == "BULLISH"
        color = "rgba(250, 204, 21, 0.08)" if is_bullish else "rgba(168, 85, 247, 0.08)"
        border_color = "rgba(250, 204, 21, 0.30)" if is_bullish else "rgba(168, 85, 247, 0.30)"
        fvg_size = abs(fvg["top"] - fvg["bottom"])
        layers.append(
            VisualRectangle(
                time_start=fvg.get("time_start_unix", current_time_unix - 1800),
                time_end=None,
                price_top=fvg["top"],
                price_bottom=fvg["bottom"],
                color=color,
                border_color=border_color,
                label=f"FVG {'+' if is_bullish else '-'} {fvg_size:.1f}",
            )
        )

    VISUAL_LAYERS.set_layers("agent_2", layers)


def _arrays_to_ohlcv(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray | None = None,
) -> np.ndarray:
    """Assemble les arrays OHLC en matrice numpy."""
    if volume is None:
        return np.column_stack([open_, high, low, close])
    return np.column_stack([open_, high, low, close, volume])


def _grade(score: float) -> str:
    """Convertit un score OB en grade lisible."""
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "E"


def _bias_matches_direction(htf_bias: str | None, direction: str) -> bool:
    """Verifie que le biais HTF confirme la direction du trade."""
    if not htf_bias:
        return False
    normalized = str(htf_bias).upper()
    if direction == "LONG":
        return normalized in {"LONG", "BULLISH", "BUY"}
    return normalized in {"SHORT", "BEARISH", "SELL"}


def _extract_liquidity_levels(liquidity_pools: dict | None, direction: str) -> list[float]:
    """Extrait les niveaux de liquidite pertinents pour un OB."""
    if not liquidity_pools:
        return []

    keys = ["eql", "ssl", "swing_lows"] if direction == "LONG" else ["eqh", "bsl", "swing_highs"]
    levels: list[float] = []
    for key in keys:
        for item in liquidity_pools.get(key, []) or []:
            if isinstance(item, dict):
                level = item.get("level", item.get("price"))
            else:
                level = item
            if level is not None:
                levels.append(float(level))
    return levels


def _zone_retouched_after_creation(ohlcv: np.ndarray, ob_idx: int, bottom: float, top: float) -> bool:
    """Detecte si le prix est revenu mitiguer la zone apres sa creation."""
    for candle in ohlcv[ob_idx + 2 :]:
        high = float(candle[1])
        low = float(candle[2])
        if high >= bottom and low <= top:
            return True
    return False


def _fvg_created_from_ob(ohlcv: np.ndarray, ob_idx: int, direction: str) -> bool:
    """Detecte une FVG creee par l'impulsion sortie de l'OB."""
    if ob_idx + 2 >= len(ohlcv):
        return False
    high = ohlcv[:, 1]
    low = ohlcv[:, 2]
    if direction == "LONG":
        return bool(low[ob_idx + 2] > high[ob_idx])
    return bool(high[ob_idx + 2] < low[ob_idx])


def _liquidity_confluence(
    ohlcv: np.ndarray,
    ob_idx: int,
    bottom: float,
    top: float,
    atr_14: float,
    direction: str,
    liquidity_pools: dict | None,
) -> bool:
    """Verifie si l'OB est colle a un niveau de liquidite."""
    tolerance = max(0.25 * atr_14, 0.0001)
    levels = _extract_liquidity_levels(liquidity_pools, direction)
    if levels:
        if direction == "LONG":
            return any(bottom - tolerance <= level <= top + tolerance for level in levels)
        return any(bottom - tolerance <= level <= top + tolerance for level in levels)

    lookback = ohlcv[max(0, ob_idx - 8) : ob_idx]
    if len(lookback) == 0:
        return False
    if direction == "LONG":
        return float(ohlcv[ob_idx][2]) <= float(np.min(lookback[:, 2])) + tolerance
    return float(ohlcv[ob_idx][1]) >= float(np.max(lookback[:, 1])) - tolerance


def _average_volume_before(ohlcv: np.ndarray, candle_idx: int, window: int = 20) -> float:
    """Retourne le volume moyen disponible avant une bougie."""
    if ohlcv.shape[1] < 5 or candle_idx <= 0:
        return 0.0
    volumes = ohlcv[max(0, candle_idx - window) : candle_idx, 4]
    volumes = volumes[volumes > 0]
    if len(volumes) == 0:
        return 0.0
    return float(np.mean(volumes))


def detect_institutional_candles(ohlcv: np.ndarray, atr_14: float, candle_idx: int | None = None) -> dict:
    """
    Detecte engulfing, rejection candle et institutional candle.

    La matrice peut contenir OHLC ou OHLCV. Le pattern institutional candle
    exige un volume disponible pour verifier le seuil 2x la moyenne 20 bougies.
    """
    if len(ohlcv) < 2:
        return {"detected": False, "patterns": [], "bonus_pts": 0.0, "has_institutional_candle": False}

    idx = len(ohlcv) - 1 if candle_idx is None else candle_idx
    if idx <= 0 or idx >= len(ohlcv):
        return {"detected": False, "patterns": [], "bonus_pts": 0.0, "has_institutional_candle": False}

    o, h, l, c = 0, 1, 2, 3
    curr = ohlcv[idx]
    prev = ohlcv[idx - 1]
    curr_open = float(curr[o])
    curr_high = float(curr[h])
    curr_low = float(curr[l])
    curr_close = float(curr[c])
    prev_open = float(prev[o])
    prev_close = float(prev[c])
    patterns: list[str] = []

    curr_body_low = min(curr_open, curr_close)
    curr_body_high = max(curr_open, curr_close)
    prev_body_low = min(prev_open, prev_close)
    prev_body_high = max(prev_open, prev_close)
    body_engulfs_previous = curr_body_low <= prev_body_low and curr_body_high >= prev_body_high
    if body_engulfs_previous and curr_close > curr_open and prev_close < prev_open:
        patterns.append("BULLISH_ENGULFING")
    elif body_engulfs_previous and curr_close < curr_open and prev_close > prev_open:
        patterns.append("BEARISH_ENGULFING")

    total_range = curr_high - curr_low
    if total_range > 0:
        lower_wick = curr_body_low - curr_low
        upper_wick = curr_high - curr_body_high
        if lower_wick / total_range > 0.70:
            patterns.append("BULLISH_REJECTION_CANDLE")
        if upper_wick / total_range > 0.70:
            patterns.append("BEARISH_REJECTION_CANDLE")

    body_size = abs(curr_close - curr_open)
    avg_volume = _average_volume_before(ohlcv, idx)
    volume_ratio = float(curr[4]) / avg_volume if ohlcv.shape[1] >= 5 and avg_volume > 0 else 0.0
    has_institutional_candle = body_size > 1.5 * max(float(atr_14 or 0.0), 0.0001) and volume_ratio > 2.0
    if has_institutional_candle:
        candle_direction = "BULLISH" if curr_close > curr_open else "BEARISH"
        patterns.append(f"INSTITUTIONAL_CANDLE_{candle_direction}")

    return {
        "detected": bool(patterns),
        "patterns": patterns,
        "bonus_pts": OB_INSTITUTIONAL_CANDLE_BONUS if has_institutional_candle else 0.0,
        "has_institutional_candle": has_institutional_candle,
        "volume_ratio": round(volume_ratio, 2),
    }


def score_order_block(
    ohlcv: np.ndarray,
    ob_idx: int,
    atr_14: float,
    direction: str,
    htf_bias: str | None = None,
    liquidity_pools: dict | None = None,
) -> dict:
    """
    Score un Order Block sur 5 facteurs independants.

    Facteurs: fraicheur, impulsion, alignement HTF, FVG dans la zone,
    confluence liquidite. Un score < 60 rend l'OB inutilisable.
    """
    i = ob_idx
    if i + 2 >= len(ohlcv) or direction not in {"LONG", "SHORT"}:
        return {"score": 0.0, "factors": {}, "valid": False, "grade": "E"}

    atr = max(float(atr_14 or 0.0), 0.0001)
    o, h, l, c = 0, 1, 2, 3
    bottom = float(ohlcv[i][l])
    top = float(ohlcv[i][h])
    factors: dict[str, float] = {}

    is_fresh = not _zone_retouched_after_creation(ohlcv, i, bottom, top)
    factors["freshness"] = OB_FACTOR_WEIGHTS["freshness"] if is_fresh else 0.0

    impulse_body = abs(float(ohlcv[i + 1][c] - ohlcv[i + 1][o]))
    impulse_direction_ok = (
        (direction == "LONG" and ohlcv[i + 1][c] > ohlcv[i + 1][o])
        or (direction == "SHORT" and ohlcv[i + 1][c] < ohlcv[i + 1][o])
    )
    impulse_ratio = impulse_body / atr if impulse_direction_ok else 0.0
    factors["impulse"] = min(impulse_ratio / 2.0, 1.0) * OB_FACTOR_WEIGHTS["impulse"]

    factors["htf_alignment"] = OB_FACTOR_WEIGHTS["htf_alignment"] if _bias_matches_direction(htf_bias, direction) else 0.0
    factors["fvg_in_zone"] = OB_FACTOR_WEIGHTS["fvg_in_zone"] if _fvg_created_from_ob(ohlcv, i, direction) else 0.0
    factors["liquidity_confluence"] = (
        OB_FACTOR_WEIGHTS["liquidity_confluence"]
        if _liquidity_confluence(ohlcv, i, bottom, top, atr, direction, liquidity_pools)
        else 0.0
    )
    candle_patterns = detect_institutional_candles(ohlcv, atr, candle_idx=i + 1)
    factors["institutional_candle"] = (
        OB_INSTITUTIONAL_CANDLE_BONUS
        if candle_patterns["has_institutional_candle"]
        else 0.0
    )

    score = round(min(sum(factors.values()), 100.0), 1)
    return {
        "score": score,
        "factors": {key: round(value, 1) for key, value in factors.items()},
        "valid": score >= OB_MIN_SCORE,
        "grade": _grade(score),
        "fresh": is_fresh,
        "impulse_ratio": round(impulse_ratio, 2),
        "institutional_patterns": candle_patterns["patterns"],
        "institutional_volume_ratio": candle_patterns["volume_ratio"],
    }


def detect_order_blocks(
    high: np.ndarray,
    low: np.ndarray,
    open_: np.ndarray,
    close: np.ndarray,
    swing_highs: list,
    swing_lows: list,
    atr_14: float,
    direction: str,
    htf_bias: str | None = None,
    liquidity_pools: dict | None = None,
    volume: np.ndarray | None = None,
) -> list:
    """Detecte et garde uniquement les OB dont le score institutionnel passe."""
    obs = []
    length = len(close)
    ohlcv = _arrays_to_ohlcv(open_, high, low, close, volume)
    liquidity_context = dict(liquidity_pools or {})
    liquidity_context.setdefault("swing_highs", swing_highs)
    liquidity_context.setdefault("swing_lows", swing_lows)

    for i in range(2, length - 2):
        if direction == "LONG":
            if close[i] >= open_[i]:
                continue

            post_body = abs(close[i + 1] - open_[i + 1])
            if not (close[i + 1] > open_[i + 1] and post_body >= 1.5 * atr_14):
                continue

            recent_shs = [s for s in swing_highs if s["index"] < i]
            if not recent_shs:
                continue
            last_sh_price = recent_shs[-1]["price"]
            if not (close[i + 1] > last_sh_price or (len(close) > i + 2 and close[i + 2] > last_sh_price)):
                continue

            ob_zone = {
                "type": "BULLISH",
                "top": float(high[i]),
                "bottom": float(low[i]),
                "entry_zone_top": float(open_[i]),
                "entry_zone_bottom": float(low[i]),
                "candle_index": i,
                "age": length - 1 - i,
            }

        elif direction == "SHORT":
            if close[i] <= open_[i]:
                continue

            post_body = abs(close[i + 1] - open_[i + 1])
            if not (close[i + 1] < open_[i + 1] and post_body >= 1.5 * atr_14):
                continue

            recent_sls = [s for s in swing_lows if s["index"] < i]
            if not recent_sls:
                continue
            last_sl_price = recent_sls[-1]["price"]
            if not (close[i + 1] < last_sl_price or (len(close) > i + 2 and close[i + 2] < last_sl_price)):
                continue

            ob_zone = {
                "type": "BEARISH",
                "top": float(high[i]),
                "bottom": float(low[i]),
                "entry_zone_top": float(high[i]),
                "entry_zone_bottom": float(close[i]),
                "candle_index": i,
                "age": length - 1 - i,
            }
        else:
            continue

        score_details = score_order_block(ohlcv, i, atr_14, direction, htf_bias, liquidity_context)
        ob_zone.update(
            {
                "ob_score": score_details["score"],
                "score": score_details["score"],
                "score_factors": score_details["factors"],
                "institutional_patterns": score_details.get("institutional_patterns", []),
                "institutional_volume_ratio": score_details.get("institutional_volume_ratio", 0.0),
                "grade": score_details["grade"],
                "fresh": score_details.get("fresh", False),
                "valid": score_details["valid"],
            }
        )
        if score_details["valid"]:
            obs.append(ob_zone)

    return rank_order_blocks_fresh_first(obs)[:OB_SELECTION_POOL_LIMIT]


def detect_fvg(high: np.ndarray, low: np.ndarray, atr_14: float, direction: str) -> list:
    """Detecte les FVG fraiches dans la direction du biais."""
    fvgs = []
    length = len(high)
    min_size = 0.1 * atr_14

    for i in range(2, length):
        if direction == "LONG":
            fvg_size = low[i] - high[i - 2]
            if fvg_size >= min_size:
                fvg_top = float(low[i])
                fvg_bottom = float(high[i - 2])
                equilibrium = (fvg_top + fvg_bottom) / 2
                mitigated = any(low[j] <= equilibrium for j in range(i + 1, length))
                fvgs.append(
                    {
                        "type": "BULLISH",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "equilibrium": equilibrium,
                        "size": float(fvg_size),
                        "size_ratio": float(fvg_size / atr_14),
                        "fresh": not mitigated,
                        "candle_index": i,
                    }
                )

        elif direction == "SHORT":
            fvg_size = low[i - 2] - high[i]
            if fvg_size >= min_size:
                fvg_top = float(low[i - 2])
                fvg_bottom = float(high[i])
                equilibrium = (fvg_top + fvg_bottom) / 2
                mitigated = any(high[j] >= equilibrium for j in range(i + 1, length))
                fvgs.append(
                    {
                        "type": "BEARISH",
                        "top": fvg_top,
                        "bottom": fvg_bottom,
                        "equilibrium": equilibrium,
                        "size": float(fvg_size),
                        "size_ratio": float(fvg_size / atr_14),
                        "fresh": not mitigated,
                        "candle_index": i,
                    }
                )

    fresh_fvgs = [f for f in fvgs if f["fresh"]]
    fresh_fvgs.sort(key=lambda x: x["candle_index"], reverse=True)
    return fresh_fvgs[:3]


def detect_breaker_blocks(ob_zones: list, current_ohlcv: np.ndarray) -> list:
    """Transforme les OB invalides en breaker blocks."""
    breakers = []
    if len(current_ohlcv) == 0:
        return breakers
    last_close = float(current_ohlcv[-1][3])

    for ob in ob_zones:
        invalidated = (
            ob.get("type") == "BULLISH" and last_close < float(ob["bottom"])
        ) or (
            ob.get("type") == "BEARISH" and last_close > float(ob["top"])
        )
        if invalidated:
            breakers.append(
                {
                    "level": ob["top"] if ob["type"] == "BULLISH" else ob["bottom"],
                    "type": "BEARISH_BREAKER" if ob["type"] == "BULLISH" else "BULLISH_BREAKER",
                    "strength": ob.get("ob_score", ob.get("score", 0)),
                    "origin_ob": ob,
                }
            )
    return breakers


def _result_direction(zone_type: str | None) -> str | None:
    """Mappe le type de zone vers LONG/SHORT."""
    if zone_type == "BULLISH":
        return "LONG"
    if zone_type == "BEARISH":
        return "SHORT"
    return None


def score_agent_2(
    best_ob: dict | None,
    best_fvg: dict | None,
    current_price: float,
    atr_14: float,
    blackboard: BlackBoard,
) -> AgentResult:
    """Construit le resultat Agent 2 depuis le meilleur OB score."""
    if not best_ob:
        contract = {
            "agent_id": "agent_2",
            "observations": ["No OB with score >= 60 found"],
            "score": 0,
            "confidence": 0.0,
            "hard_veto": True,
            "reason": "NO_VALID_OB_SCORE_GE_60",
            "uncertainty": "HIGH",
            "alternative_scenario": {"scenario": "NONE", "condition": "NONE"},
            "contextual_notes": {
                "zone_lifecycle_state": "NONE",
                "touch_count": 0,
                "mean_threshold_reached": False,
                "deepest_penetration_pct": 0.0,
                "zone_context_reason": "NO_ACTIVE_POI",
                "not_applicable_reason": "NO_ACTIVE_POI"
            },
            "diagnostic_present": False,
            "not_applicable_reason": "NO_ACTIVE_POI"
        }
        return AgentResult(
            agent_id="agent_2",
            score=0,
            reason="NO_VALID_OB_SCORE_GE_60",
            direction=None,
            hard_filter_pass=False,
            payload={"shadow_ict_contract": contract}
        )

    ob_score = float(best_ob.get("ob_score", 0.0))
    if ob_score < OB_MIN_SCORE or not best_ob.get("valid", False):
        return AgentResult(
            agent_id="agent_2",
            score=0,
            reason=f"WEAK_OB_REJECTED score={ob_score:.1f}<60 factors={best_ob.get('score_factors', {})}",
            direction=_result_direction(best_ob.get("type")),
            hard_filter_pass=False,
            payload={"ob_score": ob_score, "score_factors": best_ob.get("score_factors", {})},
        )

    zone_is_fresh = bool(best_ob.get("fresh", False))
    if not zone_is_fresh:
        # ── Shadow P1.44 — ZoneLifecycle classificateur (doctrine ICT) ─────────────────
        # Le hard_filter_pass reste False (décision réelle inchangée).
        # On enrichit shadow_ict_contract pour distinguer les zones ICT-récupérables.
        _diag = best_ob.get("diagnostic", {}) or {}
        _pen_pct = float(_diag.get("deepest_penetration_pct") or 0.0)
        _mean_reached = bool(_diag.get("mean_threshold_reached", False))
        _close_inside = int(_diag.get("close_inside_count") or 0)
        _touch_count = int(_diag.get("touch_count") or 1)

        # ICT ZoneLifecycle classification (shadow only)
        if _pen_pct <= 30.0 and _close_inside == 0:
            _zone_lifecycle = "WICK_TAGGED"           # 1er test léger, récupérable
            _shadow_hard_veto = False                  # shadow: zone ICT-usable
            _shadow_reason = "ZONE_WICK_TAGGED_RECOVERABLE"
            _shadow_score = max(int(ob_score * 0.80), 40)
            _shadow_uncertainty = "MEDIUM"
            _shadow_alt = {"scenario": "RETEST_ENTRY", "condition": "PRICE_RETURNS_ABOVE_POI_MID"}
        elif _pen_pct <= 50.0 and not (_mean_reached and _close_inside >= 1):
            _zone_lifecycle = "PARTIALLY_MITIGATED"   # pénétration partielle, attendre confirmation
            _shadow_hard_veto = False                  # shadow: watch and wait
            _shadow_reason = "ZONE_PARTIALLY_MITIGATED_WATCH"
            _shadow_score = max(int(ob_score * 0.50), 20)
            _shadow_uncertainty = "HIGH"
            _shadow_alt = {"scenario": "WAIT_FOR_REACTION", "condition": "NEW_MSS_FROM_ZONE"}
        else:
            _zone_lifecycle = "INVALIDATED"            # mean violé + close(s) inside = zone morte
            _shadow_hard_veto = True
            _shadow_reason = "ZONE_ALREADY_MITIGATED"
            _shadow_score = 0
            _shadow_uncertainty = "LOW"
            _shadow_alt = {"scenario": "NONE", "condition": "NONE"}

        reason_str = "ZONE_ALREADY_MITIGATED"  # décision réelle inchangée
        contract = {
            "agent_id": "agent_2",
            "observations": [
                f"zone_lifecycle={_zone_lifecycle}",
                f"pen={_pen_pct:.1f}% mean_reached={_mean_reached} close_inside={_close_inside} touches={_touch_count}",
                f"ob_score={ob_score:.1f}"
            ],
            "score": _shadow_score,
            "confidence": _shadow_score / 100.0,
            "hard_veto": _shadow_hard_veto,
            "reason": _shadow_reason,
            "uncertainty": _shadow_uncertainty,
            "alternative_scenario": _shadow_alt,
            "contextual_notes": {
                "zone_lifecycle": _zone_lifecycle,
                "zone_lifecycle_state": _zone_lifecycle,
                "touch_count": _touch_count,
                "mean_threshold_reached": _mean_reached,
                "deepest_penetration_pct": _pen_pct,
                "close_inside_count": _close_inside,
                "zone_context_reason": _diag.get("zone_rejection_context_reason", reason_str),
                "not_applicable_reason": ""
            },
            "diagnostic_present": True,
            "not_applicable_reason": ""
        }
        return AgentResult(
            agent_id="agent_2",
            score=0,
            reason=reason_str,
            direction=_result_direction(best_ob.get("type")),
            hard_filter_pass=False,  # INCHANGÉ — décision réelle protégée
            payload={"ob_score": ob_score, "score_factors": best_ob.get("score_factors", {}), "shadow_ict_contract": contract, "diagnostic": best_ob.get("diagnostic", {})},
        )

    price_in_zone = float(best_ob["bottom"]) <= current_price <= float(best_ob["top"])
    fvg_confluence = bool(
        best_fvg
        and float(best_ob["bottom"]) <= float(best_fvg["top"])
        and float(best_fvg["bottom"]) <= float(best_ob["top"])
    )
    selection_meta = _selection_metadata(best_ob)

    if price_in_zone:
        asyncio.create_task(
            blackboard.notify_price_in_poi(
                {
                    "zone": best_ob,
                    "score_agent2": ob_score,
                    "current_price": current_price,
                }
            )
        )

    return AgentResult(
        agent_id="agent_2",
        score=ob_score,
        reason=f"OB_5_FACTORS score={ob_score:.1f} grade={best_ob.get('grade')} factors={best_ob.get('score_factors')}",
        direction=_result_direction(best_ob.get("type")),
        hard_filter_pass=True,
        payload={
            "zone_type": best_ob.get("type"),
            "zone_top": best_ob.get("top"),
            "zone_bottom": best_ob.get("bottom"),
            "poi_zone": best_ob,
            "active_ob": best_ob,
            "active_fvg": best_fvg,
            "ob_score": ob_score,
            "score_factors": best_ob.get("score_factors", {}),
            "institutional_patterns": best_ob.get("institutional_patterns", []),
            "institutional_volume_ratio": best_ob.get("institutional_volume_ratio", 0.0),
            "grade": best_ob.get("grade"),
            "fvg_confluence": fvg_confluence,
            "price_in_zone": price_in_zone,
            "zone_is_fresh": zone_is_fresh,
            "zone_age_15m_candles": best_ob.get("age", 0),
            "selection_policy": selection_meta.get("selection_policy", "fresh_first_score_desc"),
            "ote_confluence_available": selection_meta.get("ote_confluence_available", False),
            "selected_ob_has_ote_overlap": selection_meta.get("selected_ob_has_ote_overlap", False),
            "selected_ob_overlap_size_points": selection_meta.get("selected_ob_overlap_size_points", 0.0),
            "diagnostic": best_ob.get("diagnostic", {}),
            "shadow_ict_contract": {
                "agent_id": "agent_2",
                "observations": [f"Active POI score={ob_score}"],
                "score": ob_score,
                "confidence": ob_score / 100.0,
                "hard_veto": False,
                "reason": f"OB_5_FACTORS score={ob_score:.1f}",
                "uncertainty": "LOW",
                "alternative_scenario": {"scenario": "NONE", "condition": "NONE"},
                "contextual_notes": {
                    "zone_lifecycle_state": best_ob.get("diagnostic", {}).get("human_zone_state_shadow", "FRESH"),
                    "touch_count": best_ob.get("diagnostic", {}).get("touch_count", 0),
                    "mean_threshold_reached": best_ob.get("diagnostic", {}).get("mean_threshold_reached", False),
                    "deepest_penetration_pct": best_ob.get("diagnostic", {}).get("deepest_penetration_pct", 0.0),
                    "zone_context_reason": best_ob.get("diagnostic", {}).get("zone_rejection_context_reason", "FRESH_POI"),
                    "not_applicable_reason": ""
                },
                "diagnostic_present": True,
                "not_applicable_reason": ""
            }
        },
    )


class AgentCartographe:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_2"
        self._known_zones: list[dict[str, Any]] = []

    async def run(self):
        """Boucle principale Agent 2."""
        self.logger.info("Agent 2 (Cartographe V2 OB 5 facteurs) demarre")
        while not self.bb.kill_event.is_set():
            try:
                try:
                    await asyncio.wait_for(self.bb._events["new_candle_15m"].wait(), timeout=15.0)
                    self.bb._events["new_candle_15m"].clear()
                except asyncio.TimeoutError:
                    pass

                agent1_result = await self.bb.wait_for_agent("agent_1", timeout=2.0)
                if not agent1_result or agent1_result.score == 0 or not agent1_result.direction:
                    result = AgentResult(
                        agent_id="agent_2",
                        score=0,
                        reason="WAITING_ON_AGENT1_FAIL",
                        direction=None,
                        hard_filter_pass=False,
                    )
                    await self.bb.publish_agent_dashboard(
                        "agent_2", result, min_interval_sec=0, trigger_orchestrator=False
                    )
                    VISUAL_LAYERS.clear_agent("agent_2")
                    await self.bb.update_dict(f"agents.{self.name}", {"order_blocks": []})
                    continue

                direction = agent1_result.direction
                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14") or _estimate_atr_14(candles_15m)
                current_tick = self.bb.read_sync("market_data.current_tick")

                if len(candles_15m) < 10 or not atr_14 or not current_tick:
                    await self.bb.publish_agent_dashboard(
                        "agent_2",
                        idle_result("agent_2", reason="WAITING_INSUFFICIENT_DATA"),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_2")
                    await asyncio.sleep(2)
                    continue

                high = np.array([c["high"] for c in candles_15m], dtype=float)
                low = np.array([c["low"] for c in candles_15m], dtype=float)
                open_ = np.array([c["open"] for c in candles_15m], dtype=float)
                close = np.array([c["close"] for c in candles_15m], dtype=float)
                volume_values = [
                    float(c.get("volume") or c.get("tick_volume") or c.get("real_volume") or 0.0)
                    for c in candles_15m
                ]
                volume = np.array(volume_values, dtype=float) if any(volume_values) else None

                from agents.agent_1_meteo import detect_swings

                loop = asyncio.get_running_loop()
                swings = await loop.run_in_executor(
                    None,
                    lambda: detect_swings(high, low, close, n=3, atr_14=atr_14),
                )
                agent1_meta = agent1_result.payload or {}
                htf_bias = agent1_meta.get("structure_4h") or direction
                liquidity_pools = dict(self.bb.get_all().get("market_analysis", {}).get("liquidity_pools", {}) or {})

                fvgs = detect_fvg(high, low, atr_14, direction)
                obs = await loop.run_in_executor(
                    None,
                    lambda: detect_order_blocks(
                        high,
                        low,
                        open_,
                        close,
                        swings["swing_highs"],
                        swings["swing_lows"],
                        atr_14,
                        direction,
                        htf_bias=htf_bias,
                        liquidity_pools=liquidity_pools,
                        volume=volume,
                    ),
                )

                current_time_unix = int(_time.time())
                obs = _enrich_visual_times(obs, candles_15m, 900, current_time_unix)
                fvgs = _enrich_visual_times(fvgs, candles_15m, 900, current_time_unix)

                ohlcv = _arrays_to_ohlcv(open_, high, low, close, volume)
                breakers = detect_breaker_blocks(self._known_zones, ohlcv)
                self._known_zones = [*obs, *[z for z in self._known_zones if z not in obs]][:20]

                best_ob = select_best_order_block_with_ote_confluence(obs, candles_15m, swings, direction)
                best_fvg = fvgs[0] if fvgs else None

                bid = float(current_tick.get("bid", 0.0))
                ask = float(current_tick.get("ask", 0.0))
                current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else float(close[-1])

                result = score_agent_2(best_ob, best_fvg, current_price, atr_14, self.bb)
                await self.bb.publish_agent_dashboard(
                    "agent_2", result, min_interval_sec=0
                )

                payload = result.payload or {}
                await self.bb.update_agent(
                    self.name,
                    {
                        "score": result.score,
                        "direction": result.direction,
                        "active_ob": payload.get("active_ob"),
                        "active_fvg": payload.get("active_fvg"),
                        "breaker_blocks": breakers,
                        "poi_zone": payload.get("poi_zone"),
                        "ob_score": payload.get("ob_score", 0),
                        "zone_is_fresh": payload.get("zone_is_fresh", False),
                        "reason": result.reason,
                        "hard_filter_pass": result.hard_filter_pass,
                    },
                )
                _publish_visual_layers(obs, fvgs, current_time_unix)

                ui_zones = [*obs]
                if best_fvg:
                    ui_zones.append(best_fvg)

                await self.bb.update_dict(f"agents.{self.name}", {"order_blocks": ui_zones, "fvgs": fvgs})
                await self.bb.update_dict("market_analysis.zones", {"order_blocks": ui_zones, "fvgs": fvgs, "breaker_blocks": breakers})

            except Exception as exc:
                self.logger.error(f"Erreur dans Agent 2 (Cartographe V2): {exc}")
                from config import AGENT_DASHBOARD_PULSE_SEC

                await self.bb.publish_agent_dashboard(
                    "agent_2",
                    idle_result("agent_2", reason=f"ERROR: {exc}", hard_filter_pass=False),
                    min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                    trigger_orchestrator=False,
                )
                VISUAL_LAYERS.clear_agent("agent_2")
                await asyncio.sleep(5)


AgentCartographer = AgentCartographe


def _p1_safe_dict(value):
    return value if isinstance(value, dict) else {}


def _p1_safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════════════════════════════════════════
# P2-A — Connectivity Repair : normalisation POI replay-safe
# ═══════════════════════════════════════════════════════════════════════════════

P2A_ALLOWED_POI_LIFECYCLES = {
    "FRESH",
    "WICK_TAGGED",
    "PARTIAL",
    "PARTIALLY_MITIGATED",
    "MITIGATED",
    "CONSUMED",
    "INVALIDATED",
    "STALE",
    "FILLED",
    "PARTIALLY_FILLED",
    "UNKNOWN",
}
P2A_LIFECYCLE_ALIASES = {
    "PARTIALLY_MITIGATED": "PARTIAL",
    "PARTIALLY_FILLED": "PARTIAL",
    "FILLED": "MITIGATED",
    "STALE": "CONSUMED",
    "FLIPPED_BREAKER": "INVALIDATED",
}
P2A_ALLOWED_READINESS = {
    "READY",
    "WAITING_TRIGGER",
    "UNAVAILABLE",
    "INVALID",
    "UNKNOWN",
}


def _p2a_safe_dict(value):
    return value if isinstance(value, dict) else {}


def _p2a_safe_list(value):
    return value if isinstance(value, list) else []


def _p2a_float_or_none(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _p2a_normalize_poi_type(value):
    raw = str(value or "").upper()
    if "FVG" in raw:
        return "FVG"
    if "OB" in raw or "ORDER_BLOCK" in raw or raw in {"BULLISH", "BEARISH", "OB_CONTINUATION", "OB_REVERSAL"}:
        return "OB"
    return "UNKNOWN"


def _p2a_normalize_lifecycle(value):
    raw = str(value or "UNKNOWN").upper()
    raw = raw.replace(" ", "_").replace("-", "_")
    if raw in P2A_LIFECYCLE_ALIASES:
        return P2A_LIFECYCLE_ALIASES[raw]
    if raw in P2A_ALLOWED_POI_LIFECYCLES:
        return raw
    if "WICK" in raw:
        return "WICK_TAGGED"
    if "PARTIAL" in raw:
        return "PARTIAL"
    if "MITIGATED" in raw:
        return "MITIGATED"
    if "CONSUMED" in raw:
        return "CONSUMED"
    if "INVALID" in raw:
        return "INVALIDATED"
    if "FRESH" in raw:
        return "FRESH"
    return "UNKNOWN"


def _p2a_price_bounds(raw):
    item = _p2a_safe_dict(raw)
    high = (
        _p2a_float_or_none(item.get("high"))
        or _p2a_float_or_none(item.get("top"))
        or _p2a_float_or_none(item.get("zone_top"))
    )
    low = (
        _p2a_float_or_none(item.get("low"))
        or _p2a_float_or_none(item.get("bottom"))
        or _p2a_float_or_none(item.get("zone_bottom"))
    )
    if high is None or low is None:
        return None
    if low > high:
        low, high = high, low
    return {"low": round(low, 6), "high": round(high, 6)}


def _p2a_clean_poi(raw, *, source="UNKNOWN"):
    item = _p2a_safe_dict(raw)
    bounds = _p2a_price_bounds(item)
    poi_type = _p2a_normalize_poi_type(
        item.get("poi_type_normalized")
        or item.get("zone_type")
        or item.get("type")
        or item.get("priority_label")
    )
    lifecycle = _p2a_normalize_lifecycle(
        item.get("lifecycle_normalized")
        or item.get("human_zone_state_shadow")
        or item.get("state_shadow")
        or item.get("state")
        or item.get("lifecycle_state")
    )
    score = (
        _p2a_float_or_none(item.get("score"))
        or _p2a_float_or_none(item.get("score_shadow"))
        or _p2a_float_or_none(item.get("ob_score"))
        or 0.0
    )
    mitigation_pct = (
        _p2a_float_or_none(item.get("mitigation_pct"))
        or _p2a_float_or_none(item.get("deepest_penetration_pct"))
        or _p2a_float_or_none(item.get("filled_pct"))
    )
    aligned = item.get("aligned_with_context")
    if aligned is None:
        aligned = item.get("dol_aligned")
    if aligned is None:
        aligned = item.get("order_flow_aligned")
    if aligned is None:
        aligned = item.get("aligned_with_dol")
    if aligned is None:
        aligned = item.get("aligned_with_order_flow")
    missing = []
    if bounds is None:
        missing.append("INVALID_OR_MISSING_PRICE_BOUNDS")
    if poi_type == "UNKNOWN":
        missing.append("POI_TYPE_UNKNOWN")
    if lifecycle == "UNKNOWN":
        missing.append("POI_LIFECYCLE_UNKNOWN")
    readiness = _p2a_execution_readiness(
        has_poi=bool(item),
        has_bounds=bounds is not None,
        lifecycle=lifecycle,
        aligned_with_context=bool(aligned) if aligned is not None else None,
    )
    return {
        "schema_version": "p2a.poi.v1",
        "source": source,
        "poi_type_normalized": poi_type,
        "lifecycle_normalized": lifecycle,
        "price_bounds": bounds,
        "score": round(float(score or 0.0), 4),
        "mitigation_pct": round(float(mitigation_pct), 4) if mitigation_pct is not None else None,
        "session_created": item.get("session_created") or item.get("created_at") or "UNKNOWN",
        "aligned_with_context": bool(aligned) if aligned is not None else None,
        "execution_readiness": readiness,
        "missing_evidence": missing,
        "warnings": [],
        "raw_priority_label": item.get("priority_label"),
        "raw_reason": item.get("reason") or item.get("reason_shadow") or item.get("zone_rejection_context_reason"),
    }


def _p2a_execution_readiness(*, has_poi, has_bounds, lifecycle, aligned_with_context):
    if not has_poi:
        return "UNAVAILABLE"
    if not has_bounds:
        return "INVALID"
    if lifecycle in {"CONSUMED", "INVALIDATED", "MITIGATED"}:
        return "INVALID"
    if aligned_with_context is False:
        return "WAITING_TRIGGER"
    if lifecycle in {"FRESH", "WICK_TAGGED", "PARTIAL"}:
        return "READY"
    return "WAITING_TRIGGER"


def build_p2a_poi_connectivity_payload(
    *,
    obs,
    fvgs,
    best_ob,
    best_fvg,
    direction,
    current_price,
    atr_14,
    blackboard=None,
):
    """Construit le payload P2-A POI connectivity toujours présent, même sans diagnose."""
    lifecycles = []
    try:
        if _ZONE_LIFECYCLE_AVAILABLE and obs:
            lifecycles = classify_zone_pool_shadow(obs, [], atr_14=atr_14)
    except Exception:
        lifecycles = []
    agent1_score = 0.0
    if blackboard is not None:
        try:
            agent1_score = float(_safe_read(blackboard, "agents.agent_1.score") or 0.0)
        except Exception:
            agent1_score = 0.0
    shadow_stack = build_shadow_agent2_poi_stack(
        obs or [],
        fvgs or [],
        lifecycles or [],
        direction=direction,
        current_price=current_price,
        atr_14=atr_14,
        agent1_score=agent1_score,
    )
    candidates = []
    for raw in shadow_stack.get("shadow_agent2_poi_stack") or []:
        candidates.append(_p2a_clean_poi(raw, source="shadow_agent2_poi_stack"))
    for raw in shadow_stack.get("shadow_fvg_continuation_alternatives") or []:
        candidates.append(_p2a_clean_poi(raw, source="shadow_fvg_continuation_alternatives"))
    selected_raw = (
        shadow_stack.get("best_shadow_poi")
        or best_ob
        or best_fvg
    )
    selected = _p2a_clean_poi(selected_raw, source="best_shadow_poi") if selected_raw else None
    audit = {
        "agent2_has_any_zone": bool(obs or fvgs),
        "agent2_has_selected_ob": best_ob is not None,
        "agent2_has_selected_fvg": best_fvg is not None,
        "candidate_count": len(candidates),
        "selected_poi_present": selected is not None,
        "poi_bounds_present": bool(selected and selected.get("price_bounds")),
        "best_shadow_poi_type": shadow_stack.get("best_shadow_poi_type"),
        "best_shadow_poi_reason": shadow_stack.get("best_shadow_poi_reason"),
    }
    return {
        "schema_version": "p2a.poi_connectivity.v1",
        "poi_candidates": candidates[:10],
        "selected_poi": selected,
        "active_ob": _p2a_clean_poi(best_ob, source="active_ob") if best_ob else None,
        "active_fvg": _p2a_clean_poi(best_fvg, source="active_fvg") if best_fvg else None,
        "audit": audit,
    }


def build_agent_2_observation(result):
    from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource

    if result is None:
        return AgentObservation(
            agent_id="agent_2",
            source=EvidenceSource.POI,
            passed=None,
            score=0.0,
            confidence=0.0,
            reason="UNKNOWN",
            payload={
                "schema_version": "p1.agent_observation.v1",
                "agent_id": "agent_2",
                "status": "UNKNOWN",
                "poi_available": False,
                "selected_poi": None,
                "selected_poi_present": False,
                "poi_candidates": [],
                "price_bounds": None,
                "poi_type": "UNKNOWN",
                "poi_type_normalized": "UNKNOWN",
                "lifecycle_state": "UNKNOWN",
                "lifecycle_normalized": "UNKNOWN",
                "poi_quality_score": 0.0,
                "mitigation_pct": None,
                "session_created": "UNKNOWN",
                "aligned_with_context": None,
                "execution_readiness": "UNAVAILABLE",
                "poi_semantic_available": False,
                "poi_semantic_selected": False,
                "poi_semantic_bounds": False,
                "poi_semantic_status": "POI_ABSENT",
                "p2a_connectivity_audit": {},
                "unknown_fields": ["AGENT_2_RESULT_MISSING"],
            },
            missing_evidence=["AGENT_2_RESULT_MISSING"],
        )

    payload = _p1_safe_dict(result.payload)
    p2a = _p2a_safe_dict(payload.get("p2a_poi_connectivity"))
    selected_poi = _p2a_safe_dict(p2a.get("selected_poi"))
    candidates = _p2a_safe_list(p2a.get("poi_candidates"))

    if not selected_poi:
        # Fallback legacy: try active_ob / active_fvg / poi_zone from payload
        active_ob = _p1_safe_dict(payload.get("active_ob"))
        active_fvg = _p1_safe_dict(payload.get("active_fvg"))
        poi_zone = _p1_safe_dict(payload.get("poi_zone")) or active_ob or active_fvg
        selected_poi = _p2a_clean_poi(poi_zone, source="legacy_payload") if poi_zone else {}

    audit = _p2a_safe_dict(p2a.get("audit"))
    poi_available = bool(
        selected_poi
        or candidates
        or audit.get("agent2_has_any_zone")
    )
    reference_poi = selected_poi or (candidates[0] if candidates else {})
    price_bounds = reference_poi.get("price_bounds") if reference_poi else None
    lifecycle = reference_poi.get("lifecycle_normalized", "UNKNOWN") if reference_poi else "UNKNOWN"
    poi_type = reference_poi.get("poi_type_normalized", "UNKNOWN") if reference_poi else "UNKNOWN"
    readiness = reference_poi.get("execution_readiness", "UNAVAILABLE") if reference_poi else "UNAVAILABLE"
    poi_semantic_status = _p2a_poi_semantic_status(
        selected=selected_poi,
        candidates=candidates,
        price_bounds=price_bounds,
        readiness=readiness,
        reason=str(result.reason or ""),
    )

    missing = []
    if not poi_available:
        missing.append("POI_UNAVAILABLE")
    if price_bounds is None:
        missing.append("INVALID_OR_MISSING_PRICE_BOUNDS")
    if poi_type == "UNKNOWN":
        missing.append("POI_TYPE_UNKNOWN")
    if lifecycle == "UNKNOWN":
        missing.append("POI_LIFECYCLE_UNKNOWN")

    audit.setdefault("agent2_has_any_zone", bool(candidates or selected_poi))
    audit.setdefault("selected_poi_present", bool(selected_poi))
    audit.setdefault("poi_bounds_present", price_bounds is not None)

    return AgentObservation(
        agent_id="agent_2",
        source=EvidenceSource.POI,
        passed=bool(result.hard_filter_pass),
        score=_p1_safe_float(result.score),
        confidence=min(max(_p1_safe_float(result.score) / 100.0, 0.0), 1.0),
        reason=str(result.reason or "UNKNOWN"),
        hard_filter_pass=bool(result.hard_filter_pass),
        payload={
            "schema_version": "p1.agent_observation.v1",
            "agent_id": "agent_2",
            "status": "OK" if selected_poi else "PARTIAL" if poi_available else "UNKNOWN",
            "poi_available": poi_available,
            "selected_poi_present": bool(selected_poi),
            "selected_poi": selected_poi or None,
            "poi_candidates": candidates,
            "price_bounds": price_bounds,
            "poi_type": poi_type,
            "poi_type_normalized": poi_type,
            "lifecycle_state": lifecycle,
            "lifecycle_normalized": lifecycle,
            "poi_quality_score": _p1_safe_float(
                reference_poi.get("score") if reference_poi else result.score
            ),
            "mitigation_pct": reference_poi.get("mitigation_pct") if reference_poi else None,
            "session_created": reference_poi.get("session_created") if reference_poi else "UNKNOWN",
            "aligned_with_context": reference_poi.get("aligned_with_context") if reference_poi else None,
            "execution_readiness": readiness,
            "poi_semantic_available": bool(selected_poi or candidates),
            "poi_semantic_selected": bool(selected_poi),
            "poi_semantic_bounds": bool(price_bounds),
            "poi_semantic_status": poi_semantic_status,
            "p2a_connectivity_audit": audit,
            "unknown_fields": missing,
        },
        missing_evidence=missing,
        warnings=[],
    )


def _p2a_poi_semantic_status(
    *,
    selected: dict,
    candidates: list,
    price_bounds,
    readiness: str,
    reason: str,
) -> str:
    if not selected and not candidates:
        return "POI_ABSENT"
    if not price_bounds:
        return "POI_PRESENT_INVALID_BOUNDS"
    readiness_upper = str(readiness or "").upper()
    reason_upper = str(reason or "").upper()
    if readiness_upper == "READY":
        return "POI_PRESENT_EXECUTABLE"
    if readiness_upper in {"WAITING_TRIGGER", "WAIT_FOR_TRIGGER"}:
        return "POI_PRESENT_WAITING_TRIGGER"
    if "LOW" in reason_upper:
        return "POI_PRESENT_LOW_CONFIDENCE"
    return "POI_PRESENT_UNEXECUTABLE"

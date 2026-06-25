import asyncio
import time as _time
from typing import Any
import numpy as np

from core.blackboard import BlackBoard
from core.visual_layers import VISUAL_LAYERS, VisualFibLevel, VisualFibonacci, VisualHLine, VisualRectangle
from agents.base_agent import AgentResult
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _bounds_from_p2a_poi(poi: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(poi, dict) or not poi:
        return None, None
    bounds = poi.get("price_bounds")
    if isinstance(bounds, dict):
        low = bounds.get("low", bounds.get("bottom", bounds.get("entry_zone_bottom")))
        high = bounds.get("high", bounds.get("top", bounds.get("entry_zone_top")))
        if low is not None and high is not None:
            low_f = float(low)
            high_f = float(high)
            return min(low_f, high_f), max(low_f, high_f)
    low = poi.get("low", poi.get("bottom", poi.get("entry_zone_bottom")))
    high = poi.get("high", poi.get("top", poi.get("entry_zone_top")))
    if low is not None and high is not None:
        low_f = float(low)
        high_f = float(high)
        return min(low_f, high_f), max(low_f, high_f)
    return None, None


def extract_agent2_p2a_ote_anchor(blackboard) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Extract P2-A POI anchor for Agent4 OTE/Fibonacci.

    Delegates to the centralized poi_contract module (Phase 7E).
    """
    from gold_sniper.agents.poi_contract import extract_p2a_selected_poi

    anchor, diagnostics = extract_p2a_selected_poi(blackboard)
    if anchor:
        anchor["agent4_handoff_source"] = diagnostics["source"]
    return anchor, diagnostics


def enrich_agent4_result_with_handoff(
    result: AgentResult,
    ote_anchor: dict[str, Any] | None,
    ote_handoff: dict[str, Any],
) -> AgentResult:
    from gold_sniper.agents.poi_contract import consumed_poi_snapshot

    payload = dict(result.payload or {})
    payload["agent4_poi_handoff"] = ote_handoff
    payload["agent4_consumed_poi"] = consumed_poi_snapshot(ote_anchor)
    payload["ote_handoff_status"] = "P2A_POI_CONSUMED" if ote_anchor else "P2A_POI_MISSING"
    if result.hard_filter_pass or payload.get("in_ote") is True:
        payload["execution_readiness"] = "READY"
        payload["readiness_state"] = "READY"
        payload["readiness_reason"] = "OTE_READY"
    elif ote_anchor:
        payload["execution_readiness"] = "WAIT_FOR_TRIGGER"
        payload["readiness_state"] = "WAIT_FOR_TRIGGER"
        payload["readiness_reason"] = "OTE_WAITING_PRICE"
    else:
        payload["execution_readiness"] = "UNAVAILABLE"
        payload["readiness_state"] = "UNAVAILABLE"
        payload["readiness_reason"] = "OTE_POI_MISSING"
    return AgentResult(
        agent_id=result.agent_id,
        score=result.score,
        hard_filter_pass=result.hard_filter_pass,
        direction=result.direction,
        reason=result.reason,
        payload=payload,
        timestamp=result.timestamp,
        veto=result.veto,
        risk_modifier=result.risk_modifier,
    )


def diagnose_contextual_ote(
    agent4_payload: dict,
    current_price: float,
    direction: str,
    agent1_score: float,
    legacy_veto: bool,
) -> dict:
    levels = agent4_payload.get("levels", {})
    equilibrium = agent4_payload.get("equilibrium") or levels.get("equilibrium")
    if equilibrium is None:
        range_position = "UNKNOWN"
    else:
        if current_price == equilibrium:
            range_position = "EQUILIBRIUM"
        elif direction == "LONG":
            range_position = "DISCOUNT" if current_price < equilibrium else "PREMIUM"
        else:
            range_position = "PREMIUM" if current_price > equilibrium else "DISCOUNT"

    ote_low = agent4_payload.get("ote_low") or levels.get("ote_low")
    ote_high = agent4_payload.get("ote_high") or levels.get("ote_high")
    ote_classic = False
    depth_class = "UNKNOWN"
    if ote_low is not None and ote_high is not None:
        if (direction == "LONG" and current_price > ote_high) or (direction == "SHORT" and current_price < ote_low):
            depth_class = "SHALLOW_PULLBACK"
        elif (direction == "LONG" and current_price < ote_low) or (direction == "SHORT" and current_price > ote_high):
            depth_class = "DEEP_RETRACEMENT"
        else:
            depth_class = "CLASSIC_OTE"
            ote_classic = True

    if agent1_score >= 80:
        setup_family_hint = "TREND_CONTINUATION"
    elif agent1_score > 0:
        setup_family_hint = "SNIPER_PULLBACK"
    else:
        setup_family_hint = "REVERSAL"

    premium_forbidden = legacy_veto
    conflict_mode = "NONE"
    reason = "NO_CONFLICT"

    if premium_forbidden:
        if setup_family_hint == "TREND_CONTINUATION":
            conflict_mode = "SOFT_WARNING"
            reason = "TREND_CONTINUATION tolerates shallow pullback in premium"
        else:
            conflict_mode = "HARD_VETO"
            if direction == "SHORT":
                reason = "SHORT entry forbidden in DISCOUNT; waiting for PREMIUM retracement."
            else:
                reason = "LONG entry forbidden in PREMIUM; waiting for DISCOUNT retracement."

    return {
        "range_position": range_position,
        "retracement_depth_class": depth_class,
        "ote_classic": ote_classic,
        "setup_family_hint": setup_family_hint,
        "premium_forbidden": premium_forbidden,
        "premium_conflict_mode": conflict_mode,
        "reason_contextual": reason,
    }


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


def _publish_visual_layers_agent4(
    swing_low_price: float,
    swing_high_price: float,
    swing_low_time: int,
    swing_high_time: int,
    direction: str,
    levels_data: dict,
) -> None:
    if not levels_data or not all(k in levels_data for k in ("ote_low", "ote_high", "ote_sweet", "equilibrium")):
        VISUAL_LAYERS.clear_agent("agent_4")
        return

    layers = []

    layers.append(
        VisualRectangle(
            time_start=swing_low_time if direction == "LONG" else swing_high_time,
            time_end=None,
            price_top=levels_data["ote_high"],
            price_bottom=levels_data["ote_low"],
            color="rgba(245, 158, 11, 0.15)",
            border_color="rgba(245, 158, 11, 0.60)",
            label="OTE Zone",
        )
    )

    layers.append(
        VisualHLine(
            time_start=swing_low_time,
            price=levels_data["ote_sweet"],
            color="rgba(245, 158, 11, 1.0)",
            style="dashed",
            width=2,
            label="70.5% Sweet Spot",
            label_side="right",
        )
    )

    layers.append(
        VisualHLine(
            time_start=swing_low_time,
            price=levels_data["equilibrium"],
            color="rgba(148, 163, 184, 0.80)",
            style="dashed",
            width=1,
            label="EQ 50%",
            label_side="left",
        )
    )

    total_range = swing_high_price - swing_low_price
    fib_0382 = 0
    if total_range > 0:
        fib_0382 = (
            swing_high_price - 0.382 * total_range
            if direction == "LONG"
            else swing_low_price + 0.382 * total_range
        )

    fib_levels = [
        (0.382, levels_data.get("fib_0382", fib_0382), "38.2%", "rgba(100,116,139,0.5)", False),
        (0.618, levels_data["ote_high"], "61.8%", "rgba(245,158,11,0.7)", True),
        (0.705, levels_data["ote_sweet"], "70.5%", "rgba(245,158,11,1.0)", True),
        (0.786, levels_data["ote_low"], "78.6%", "rgba(245,158,11,0.7)", True),
    ]

    for _ratio, price, label_text, color, is_ote in fib_levels:
        if not price:
            continue
        layers.append(
            VisualHLine(
                time_start=swing_low_time,
                price=price,
                color=color,
                style="dotted" if not is_ote else "solid",
                width=1,
                label=f"Fib {label_text}",
                label_side="right",
            )
        )

    tp1_price = levels_data.get("tp1")
    if tp1_price:
        layers.append(
            VisualHLine(
                time_start=swing_low_time,
                price=tp1_price,
                color="rgba(16, 185, 129, 0.60)",
                style="dashed",
                width=1,
                label="TP1 -27.2%",
                label_side="right",
            )
        )

    VISUAL_LAYERS.set_layers("agent_4", layers)


def calculate_ote_zones(swing_low_price: float, swing_high_price: float, direction: str) -> dict:
    """Calcule equilibrium, premium/discount, OTE 61.8-78.6 et sweet spot 70.5."""
    total_range = swing_high_price - swing_low_price
    if total_range <= 0:
        return {}
    
    if direction == "LONG":
        equilibrium   = swing_high_price - 0.500 * total_range
        ote_high      = swing_high_price - 0.618 * total_range
        ote_sweet     = swing_high_price - 0.705 * total_range
        ote_low       = swing_high_price - 0.786 * total_range
        
        tp1 = swing_high_price + 0.272 * total_range
        tp2 = swing_high_price + 0.618 * total_range
        tp3 = swing_high_price + 1.000 * total_range
        
        discount_zone = (swing_low_price, equilibrium)
        premium_zone  = (equilibrium, swing_high_price)
    
    elif direction == "SHORT":
        equilibrium   = swing_low_price + 0.500 * total_range
        ote_low       = swing_low_price + 0.618 * total_range
        ote_sweet     = swing_low_price + 0.705 * total_range
        ote_high      = swing_low_price + 0.786 * total_range
        
        tp1 = swing_low_price - 0.272 * total_range
        tp2 = swing_low_price - 0.618 * total_range
        tp3 = swing_low_price - 1.000 * total_range
        
        discount_zone = (swing_low_price, equilibrium)
        premium_zone  = (equilibrium, swing_high_price)
    else:
        return {}
    
    return {
        "direction": direction,
        "swing_low": swing_low_price,
        "swing_high": swing_high_price,
        "equilibrium": equilibrium,
        "ote_high": ote_high,
        "ote_sweet": ote_sweet,
        "ote_low": ote_low,
        "ote_zone": (ote_low, ote_high),
        "discount_zone": discount_zone,
        "premium_zone": premium_zone,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
    }


def calculate_ote_levels(swing_low: float, swing_high: float, direction: str) -> dict:
    """Alias Script 06 pour calculate_ote_zones."""
    return calculate_ote_zones(swing_low, swing_high, direction)


def calculate_ote_levels_anchored_to_poi(
    candles_15m: list,
    swings: dict,
    direction: str,
    poi_zone: dict | None,
) -> dict | None:
    """Calculate unchanged OTE levels from a swing anchored to Agent 2 POI."""
    if not isinstance(poi_zone, dict):
        return None
    if direction not in {"LONG", "SHORT"}:
        return None
    zone_type = str(poi_zone.get("type") or "").upper()
    if (direction == "LONG" and zone_type != "BULLISH") or (direction == "SHORT" and zone_type != "BEARISH"):
        return None

    poi_index = _to_int(poi_zone.get("candle_index"))
    if poi_index is None or poi_index < 0 or poi_index >= len(candles_15m):
        return None

    zone_bottom = _to_float(poi_zone.get("bottom"))
    zone_top = _to_float(poi_zone.get("top"))
    if zone_bottom is None or zone_top is None or zone_bottom >= zone_top:
        return None

    candle = candles_15m[poi_index] if isinstance(candles_15m[poi_index], dict) else {}
    if direction == "LONG":
        structural_low = _latest_structural_anchor(
            swings.get("swing_lows", []),
            poi_index,
            max_price=zone_bottom,
        )
        if structural_low:
            anchor_low = structural_low["price"]
            swing_low_index = structural_low["index"]
        else:
            anchor_low = min(
                value
                for value in (
                    zone_bottom,
                    _to_float(poi_zone.get("entry_zone_bottom")),
                    _to_float(candle.get("low")),
                )
                if value is not None
            )
            swing_low_index = poi_index
        post_highs = [
            item for item in swings.get("swing_highs", [])
            if _to_int(item.get("index")) is not None
            and _to_int(item.get("index")) >= poi_index
            and _to_float(item.get("price")) is not None
            and _to_float(item.get("price")) > anchor_low
        ]
        if not post_highs:
            return None
        swing_high_item = max(post_highs, key=lambda item: _to_int(item.get("index")) or -1)
        swing_low = float(anchor_low)
        swing_high = float(swing_high_item["price"])
        swing_high_index = _to_int(swing_high_item.get("index"))
    else:
        structural_high = _latest_structural_anchor(
            swings.get("swing_highs", []),
            poi_index,
            min_price=zone_top,
        )
        if structural_high:
            anchor_high = structural_high["price"]
            swing_high_index = structural_high["index"]
        else:
            anchor_high = max(
                value
                for value in (
                    zone_top,
                    _to_float(poi_zone.get("entry_zone_top")),
                    _to_float(candle.get("high")),
                )
                if value is not None
            )
            swing_high_index = poi_index
        post_lows = [
            item for item in swings.get("swing_lows", [])
            if _to_int(item.get("index")) is not None
            and _to_int(item.get("index")) >= poi_index
            and _to_float(item.get("price")) is not None
            and _to_float(item.get("price")) < anchor_high
        ]
        if not post_lows:
            return None
        swing_low_item = max(post_lows, key=lambda item: _to_int(item.get("index")) or -1)
        swing_low = float(swing_low_item["price"])
        swing_high = float(anchor_high)
        swing_low_index = _to_int(swing_low_item.get("index"))

    if swing_high <= swing_low:
        return None

    levels = calculate_ote_levels(swing_low, swing_high, direction)
    if not levels:
        return None
    return {
        "swing_low": swing_low,
        "swing_high": swing_high,
        "swing_low_index": swing_low_index,
        "swing_high_index": swing_high_index,
        "swing_low_time": _candle_time_value(candles_15m, swing_low_index),
        "swing_high_time": _candle_time_value(candles_15m, swing_high_index),
        "anchor_mode": "AGENT2_POI_ANCHORED",
        "poi_zone_used": _compact_poi_zone(poi_zone),
        "agent4_swing_contains_agent2_zone": _swing_contains_zone(swing_low, swing_high, zone_bottom, zone_top),
        "levels": levels,
    }


def select_ote_swing_with_agent2_anchor(
    candles_15m: list,
    swings: dict,
    direction: str,
    poi_zone: dict | None = None,
) -> dict | None:
    """Prefer Agent 2 anchored OTE, then fall back to the existing recent-swing choice."""
    anchored = calculate_ote_levels_anchored_to_poi(candles_15m, swings, direction, poi_zone)
    if anchored:
        return anchored

    fallback = _calculate_recent_swing_ote(candles_15m, swings, direction)
    if not fallback:
        return None
    fallback["anchor_mode"] = "NO_VALID_ANCHOR" if isinstance(poi_zone, dict) else "FALLBACK_RECENT_SWING"
    fallback["poi_zone_used"] = _compact_poi_zone(poi_zone) if isinstance(poi_zone, dict) else None
    if isinstance(poi_zone, dict):
        zone_bottom = _to_float(poi_zone.get("bottom"))
        zone_top = _to_float(poi_zone.get("top"))
        fallback["agent4_swing_contains_agent2_zone"] = _swing_contains_zone(
            fallback["swing_low"],
            fallback["swing_high"],
            zone_bottom,
            zone_top,
        )
    else:
        fallback["agent4_swing_contains_agent2_zone"] = False
    return fallback


def enrich_agent4_result_with_swing(result: AgentResult, swing_selection: dict) -> AgentResult:
    payload = dict(result.payload or {})
    levels = swing_selection.get("levels") or payload.get("levels")
    if levels:
        payload["levels"] = levels
    payload.update(
        {
            "swing_used": {
                "low_price": swing_selection.get("swing_low"),
                "high_price": swing_selection.get("swing_high"),
                "low_index": swing_selection.get("swing_low_index"),
                "high_index": swing_selection.get("swing_high_index"),
                "low_time": swing_selection.get("swing_low_time"),
                "high_time": swing_selection.get("swing_high_time"),
            },
            "ote_anchor_mode": swing_selection.get("anchor_mode"),
            "poi_zone_used": swing_selection.get("poi_zone_used"),
            "agent4_swing_contains_agent2_zone": swing_selection.get("agent4_swing_contains_agent2_zone", False),
        }
    )
    return AgentResult(
        agent_id=result.agent_id,
        score=result.score,
        hard_filter_pass=result.hard_filter_pass,
        direction=result.direction,
        reason=result.reason,
        payload=payload,
        timestamp=result.timestamp,
        veto=result.veto,
        risk_modifier=result.risk_modifier,
    )


def _calculate_recent_swing_ote(candles_15m: list, swings: dict, direction: str) -> dict | None:
    if not swings.get("swing_highs") or not swings.get("swing_lows"):
        return None
    last_high_item = swings["swing_highs"][-1]
    last_low_item = swings["swing_lows"][-1]
    last_high = _to_float(last_high_item.get("price"))
    last_low = _to_float(last_low_item.get("price"))
    last_high_idx = _to_int(last_high_item.get("index"))
    last_low_idx = _to_int(last_low_item.get("index"))
    if last_high is None or last_low is None:
        return None
    if last_high <= last_low:
        high_values = [_to_float(candle.get("high")) if isinstance(candle, dict) else None for candle in candles_15m]
        low_values = [_to_float(candle.get("low")) if isinstance(candle, dict) else None for candle in candles_15m]
        high_candidates = [(idx, value) for idx, value in enumerate(high_values) if value is not None]
        low_candidates = [(idx, value) for idx, value in enumerate(low_values) if value is not None]
        if not high_candidates or not low_candidates:
            return None
        last_high_idx, last_high = max(high_candidates, key=lambda item: item[1])
        last_low_idx, last_low = min(low_candidates, key=lambda item: item[1])
    levels = calculate_ote_levels(last_low, last_high, direction)
    if not levels:
        return None
    return {
        "swing_low": float(last_low),
        "swing_high": float(last_high),
        "swing_low_index": last_low_idx,
        "swing_high_index": last_high_idx,
        "swing_low_time": _candle_time_value(candles_15m, last_low_idx),
        "swing_high_time": _candle_time_value(candles_15m, last_high_idx),
        "anchor_mode": "FALLBACK_RECENT_SWING",
        "poi_zone_used": None,
        "agent4_swing_contains_agent2_zone": False,
        "levels": levels,
    }


def _compact_poi_zone(zone: dict | None) -> dict | None:
    if not isinstance(zone, dict):
        return None
    keys = (
        "type",
        "top",
        "bottom",
        "entry_zone_top",
        "entry_zone_bottom",
        "candle_index",
        "fresh",
        "ob_score",
        "score",
    )
    return {key: zone.get(key) for key in keys if key in zone}


def _latest_structural_anchor(
    swing_items: list,
    poi_index: int,
    *,
    min_price: float | None = None,
    max_price: float | None = None,
) -> dict | None:
    candidates = []
    for item in swing_items:
        index = _to_int(item.get("index"))
        price = _to_float(item.get("price"))
        if index is None or price is None or index > poi_index:
            continue
        if min_price is not None and price < min_price:
            continue
        if max_price is not None and price > max_price:
            continue
        candidates.append({"index": index, "price": price})
    return max(candidates, key=lambda item: item["index"]) if candidates else None


def _candle_time_value(candles: list, candle_index: int | None) -> str | None:
    if candle_index is None or candle_index < 0 or candle_index >= len(candles):
        return None
    candle = candles[candle_index] if isinstance(candles[candle_index], dict) else {}
    value = candle.get("time")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value is not None else None


def _swing_contains_zone(
    swing_low: float | None,
    swing_high: float | None,
    zone_bottom: float | None,
    zone_top: float | None,
) -> bool:
    if None in (swing_low, swing_high, zone_bottom, zone_top):
        return False
    return float(swing_low) <= float(zone_bottom) <= float(zone_top) <= float(swing_high)


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def score_fibonacci_ote(current_price: float, fib_levels: dict, direction: str, dxy_bias: str = "NEUTRAL") -> AgentResult:
    """Score la matrice Premium/Discount et la precision OTE."""
    if not fib_levels:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="INVALID_FIB_LEVELS",
            direction=direction, hard_filter_pass=False
        )

    equilibrium = fib_levels["equilibrium"]
    ote_low = fib_levels["ote_low"]
    ote_high = fib_levels["ote_high"]
    ote_sweet = fib_levels["ote_sweet"]
    is_discount = current_price <= equilibrium
    is_premium = current_price >= equilibrium
    
    if direction == "LONG" and current_price > equilibrium:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="PREMIUM_ZONE_LONG_FORBIDDEN",
            direction=direction, hard_filter_pass=False,
            payload={
                "levels": fib_levels,
                "current_price": current_price,
                "equilibrium": equilibrium,
                "in_discount": False,
                "in_premium": True,
                "in_ote": False,
                "price_in_ote": False,
                "premium_discount_ok": False,
                "forbidden": True,
            },
        )
    
    if direction == "SHORT" and current_price < equilibrium:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="DISCOUNT_ZONE_SHORT_FORBIDDEN",
            direction=direction, hard_filter_pass=False,
            payload={
                "levels": fib_levels,
                "current_price": current_price,
                "equilibrium": equilibrium,
                "in_discount": True,
                "in_premium": False,
                "in_ote": False,
                "price_in_ote": False,
                "premium_discount_ok": False,
                "forbidden": True,
            },
        )
    
    in_ote = ote_low <= current_price <= ote_high
    macro_adjustment = 0
    if direction == "LONG" and dxy_bias == "BEARISH":
        macro_adjustment = 10
    elif direction == "LONG" and dxy_bias == "BULLISH":
        macro_adjustment = -10
    elif direction == "SHORT" and dxy_bias == "BULLISH":
        macro_adjustment = 10
    elif direction == "SHORT" and dxy_bias == "BEARISH":
        macro_adjustment = -10
    
    if not in_ote:
        return AgentResult(
            agent_id="agent_4", score=max(0, min(25 + macro_adjustment, 100)),
            reason="IN_CORRECT_ZONE_BUT_NOT_YET_IN_OTE - Attendre",
            direction=direction, hard_filter_pass=True,
            payload={
                "levels": fib_levels,
                "current_price": current_price,
                "equilibrium": equilibrium,
                "ote_low": ote_low,
                "ote_high": ote_high,
                "ote_sweet": ote_sweet,
                "in_discount": is_discount,
                "in_premium": is_premium,
                "in_ote": False,
                "price_in_ote": False,
                "precision": 0.0,
                "precision_pct": 0.0,
                "premium_discount_ok": True,
                "forbidden": False,
                "dxy_bias": dxy_bias,
                "macro_adjustment": macro_adjustment,
            },
        )
    
    ote_half_width = (ote_high - ote_low) / 2
    if ote_half_width == 0:
        precision = 0.0
    else:
        distance_to_sweet = abs(current_price - ote_sweet)
        precision = max(0.0, 1.0 - (distance_to_sweet / ote_half_width))
    
    score = max(0, min(60 + precision * 30 + macro_adjustment, 100))
    
    return AgentResult(
        agent_id="agent_4",
        score=round(score, 1),
        reason=f"IN_OTE_PRECISION={precision:.0%}_SWEET_70_5={ote_sweet:.2f}_DXY={macro_adjustment}",
        direction=direction,
        hard_filter_pass=True,
        payload={
            "ote_zone": fib_levels["ote_zone"],
            "ote_low": ote_low,
            "ote_high": ote_high,
            "ote_sweet": ote_sweet,
            "precision": precision,
            "precision_pct": precision,
            "current_price": current_price,
            "equilibrium": equilibrium,
            "in_discount": is_discount,
            "in_premium": is_premium,
            "in_ote": True,
            "premium_discount_ok": True,
            "forbidden": False,
            "dxy_bias": dxy_bias,
            "macro_adjustment": macro_adjustment,
            "tp1": fib_levels["tp1"],
            "tp2": fib_levels["tp2"],
            "tp3": fib_levels["tp3"],
            "price_in_ote": True,
            "levels": fib_levels,
        }
    )


def score_fibonacci(current_price: float, levels: dict, direction: str, dxy_bias: str = "NEUTRAL") -> dict:
    """Interface Script 06 retournant un dict de score."""
    result = score_fibonacci_ote(current_price, levels, direction, dxy_bias)
    payload = result.payload or {}
    return {
        "score": result.score,
        "hard_filter_pass": result.hard_filter_pass,
        "in_ote": payload.get("in_ote", False),
        "in_discount": payload.get("in_discount", False),
        "in_premium": payload.get("in_premium", False),
        "precision_pct": payload.get("precision_pct", 0.0),
        "dxy_bias": payload.get("dxy_bias", dxy_bias),
        "macro_adjustment": payload.get("macro_adjustment", 0),
        "reason": result.reason,
        "levels": payload.get("levels", levels),
    }

class AgentFibonacci:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_4"
    
    async def run(self):
        self.logger.info("▶️  Agent 4 (Fibonacci V2) démarré")
        while not self.bb.kill_event.is_set():
            try:
                try:
                    await asyncio.wait_for(self.bb._events["new_candle_15m"].wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    pass
                
                # Attendre Agent 2 (qui attend Agent 1)
                agent2_result = await self.bb.wait_for_agent("agent_2", timeout=2.0)
                agent1_result = self.bb.read_sync("agent_results.agent_1")
                ote_anchor, ote_handoff = extract_agent2_p2a_ote_anchor(self.bb)

                if not agent1_result or not agent2_result:
                    waiting = AgentResult(
                        agent_id="agent_4",
                        score=25,
                        reason="WAITING_ON_AGENT2_RESULT",
                        direction=None,
                        hard_filter_pass=False,
                        payload={
                            "execution_readiness": "UNAVAILABLE",
                            "readiness_state": "UNAVAILABLE",
                            "readiness_reason": "OTE_WAITING_AGENT2_RESULT",
                            "agent4_poi_handoff": ote_handoff,
                            "ote_handoff_status": "P2A_POI_MISSING",
                        },
                    )
                    await self.bb.publish_agent_dashboard(
                        "agent_4", waiting, min_interval_sec=0, trigger_orchestrator=False
                    )
                    VISUAL_LAYERS.clear_agent("agent_4")
                    await self.bb.update_dict(f"agents.{self.name}", {"price_in_ote": False})
                    continue

                if not ote_anchor:
                    waiting = AgentResult(
                        agent_id="agent_4",
                        score=25,
                        reason="WAITING_ON_AGENT2_POI",
                        direction=None,
                        hard_filter_pass=False,
                        payload={
                            "execution_readiness": "UNAVAILABLE",
                            "readiness_state": "UNAVAILABLE",
                            "readiness_reason": "OTE_POI_MISSING",
                            "agent4_poi_handoff": ote_handoff,
                            "ote_handoff_status": "P2A_POI_MISSING",
                        },
                    )
                    await self.bb.publish_agent_dashboard(
                        "agent_4", waiting, min_interval_sec=0, trigger_orchestrator=False
                    )
                    VISUAL_LAYERS.clear_agent("agent_4")
                    await self.bb.update_dict(f"agents.{self.name}", {"price_in_ote": False})
                    continue

                direction = agent1_result.direction
                if not direction:
                    await self.bb.publish_agent_dashboard(
                        "agent_4",
                        idle_result("agent_4", reason="WAITING_NO_DIRECTION", score=25),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_4")
                    continue

                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14") or _estimate_atr_14(candles_15m)
                current_tick = self.bb.read_sync("market_data.current_tick")
                
                if len(candles_15m) < 10 or not atr_14 or not current_tick:
                    await self.bb.publish_agent_dashboard(
                        "agent_4",
                        idle_result("agent_4", reason="WAITING_INSUFFICIENT_DATA", score=25),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_4")
                    await asyncio.sleep(2)
                    continue
                
                high = np.array([c["high"] for c in candles_15m])
                low = np.array([c["low"] for c in candles_15m])
                close = np.array([c["close"] for c in candles_15m])
                
                from agents.agent_1_meteo import detect_swings
                swings = detect_swings(high, low, close, n=3, atr_14=atr_14)
                
                if not swings["swing_highs"] or not swings["swing_lows"]:
                    await self.bb.publish_agent_dashboard(
                        "agent_4",
                        idle_result("agent_4", reason="WAITING_NO_SWINGS", score=25),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_4")
                    continue
                
                swing_selection = select_ote_swing_with_agent2_anchor(candles_15m, swings, direction, ote_anchor)
                if not swing_selection:
                    await self.bb.publish_agent_dashboard(
                        "agent_4",
                        idle_result("agent_4", reason="WAITING_NO_SWINGS", score=25),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_4")
                    continue

                fib_levels = swing_selection["levels"]
                
                bid = current_tick.get("bid", 0.0)
                ask = current_tick.get("ask", 0.0)
                current_price = (bid + ask) / 2 if bid > 0 else close[-1]
                
                market = self.bb.get_market()
                result = score_fibonacci_ote(current_price, fib_levels, direction, market.get("dxy_bias", "NEUTRAL"))
                result = enrich_agent4_result_with_swing(result, swing_selection)
                result = enrich_agent4_result_with_handoff(result, ote_anchor, ote_handoff)
                
                payload = result.payload or {}
                legacy_veto = payload.get("forbidden", False)
                agent1_score = agent1_result.score
                shadow_diag = diagnose_contextual_ote(
                    payload, current_price, direction, agent1_score, legacy_veto
                )
                payload["shadow_ote_context"] = shadow_diag
                payload["setup_family_match"] = shadow_diag.get("setup_family_hint")
                payload["retracement_depth_class"] = shadow_diag.get("retracement_depth_class")
                payload["premium_discount_conflict_mode"] = shadow_diag.get("premium_conflict_mode")
                _a4_contextual_verdict = "SOFT_WARNING_PASSED" if shadow_diag.get("premium_conflict_mode") == "SOFT_WARNING" else ("HARD_VETO" if shadow_diag.get("premium_conflict_mode") == "HARD_VETO" else "CLEAN")
                payload["agent4_contextual_verdict_shadow"] = _a4_contextual_verdict
                payload["shadow_ict_contract"] = {
                    "agent_id": "agent_4",
                    "observations": [f"OTE in_ote={payload.get('in_ote', False)} verdict={_a4_contextual_verdict}"],
                    "score": result.score,
                    "confidence": result.score / 100.0,
                    "hard_veto": _a4_contextual_verdict == "HARD_VETO",
                    "reason": result.reason,
                    "uncertainty": "LOW" if payload.get("in_ote") else "MEDIUM",
                    "alternative_scenario": {"scenario": "WAIT_FOR_OTE" if not payload.get("in_ote") else "NONE", "condition": "PRICE_RETRACES_TO_0618"},
                    "contextual_notes": {
                        "setup_family_match": payload.get("setup_family_match", "UNKNOWN"),
                        "retracement_depth_class": payload.get("retracement_depth_class", "UNKNOWN"),
                        "premium_discount_conflict_mode": payload.get("premium_discount_conflict_mode", "CLEAN"),
                        "agent4_contextual_verdict_shadow": _a4_contextual_verdict
                    },
                    "diagnostic_present": True,
                    "not_applicable_reason": "" if payload.get("in_ote") is not None else "NO_SWING_FOUND"
                }
                result.payload = payload
                
                # Retrocompat UI
                swing_used = payload.get("swing_used") or {}
                await self.bb.update_agent(
                    self.name,
                    {
                        "score": result.score,
                        "direction": result.direction,
                        "swing_used": swing_used,
                        "ote_anchor_mode": payload.get("ote_anchor_mode"),
                        "agent4_swing_contains_agent2_zone": payload.get("agent4_swing_contains_agent2_zone", False),
                        "poi_zone_used": payload.get("poi_zone_used"),
                        "ote_low": payload.get("ote_low"),
                        "ote_high": payload.get("ote_high"),
                        "ote_sweet": payload.get("ote_sweet"),
                        "equilibrium": payload.get("equilibrium"),
                        "in_ote": payload.get("in_ote", False),
                        "price_in_ote": payload.get("price_in_ote", False),
                        "in_discount": payload.get("in_discount", False),
                        "in_premium": payload.get("in_premium", False),
                        "precision_pct": payload.get("precision_pct", 0.0),
                        "execution_readiness": payload.get("execution_readiness"),
                        "readiness_state": payload.get("readiness_state"),
                        "readiness_reason": payload.get("readiness_reason"),
                        "agent4_poi_handoff": payload.get("agent4_poi_handoff"),
                        "agent4_consumed_poi": payload.get("agent4_consumed_poi"),
                        "ote_handoff_status": payload.get("ote_handoff_status"),
                        "reason": result.reason,
                        "hard_filter_pass": result.hard_filter_pass,
                        "payload": payload,
                    },
                )
                await self.bb.publish_agent_dashboard(
                    "agent_4", result, min_interval_sec=0
                )
                current_time_unix = int(_time.time())
                _publish_visual_layers_agent4(
                    swing_low_price=swing_selection["swing_low"],
                    swing_high_price=swing_selection["swing_high"],
                    swing_low_time=_candle_time_unix(candles_15m, swing_selection.get("swing_low_index"), current_time_unix, 900),
                    swing_high_time=_candle_time_unix(candles_15m, swing_selection.get("swing_high_index"), current_time_unix, 900),
                    direction=direction,
                    levels_data=payload.get("levels", fib_levels) or fib_levels,
                )

            except Exception as e:
                self.logger.error(f"❌ Erreur dans Agent 4 (Fibonacci V2) : {e}")
                from config import AGENT_DASHBOARD_PULSE_SEC

                await self.bb.publish_agent_dashboard(
                    "agent_4",
                    idle_result(
                        "agent_4",
                        reason=f"ERROR: {e}",
                        score=25,
                        hard_filter_pass=False,
                    ),
                    min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                    trigger_orchestrator=False,
                )
                VISUAL_LAYERS.clear_agent("agent_4")
                await asyncio.sleep(5)


def _p1_safe_dict(value):    return value if isinstance(value, dict) else {}
def _p1_safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_agent_4_observation(result):
    from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource

    if result is None:
        return AgentObservation(
            agent_id="agent_4",
            source=EvidenceSource.TIMING,
            passed=None,
            score=0.0,
            confidence=0.0,
            reason="UNKNOWN",
            payload={
                "schema_version": "p1.agent_observation.v1",
                "agent_id": "agent_4",
                "status": "UNKNOWN",
                "in_ote": False,
                "premium_discount": "UNKNOWN",
                "execution_readiness": "UNAVAILABLE",
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "AGENT_4_RESULT_MISSING",
                "unknown_fields": ["agent_result"],
            },
            missing_evidence=["AGENT_4_RESULT_MISSING"],
        )

    payload = _p1_safe_dict(result.payload)
    shadow = _p1_safe_dict(payload.get("shadow_ote_context"))

    in_ote = bool(payload.get("in_ote") or payload.get("price_in_ote") or shadow.get("ote_classic"))
    premium_discount = (
        shadow.get("range_position")
        or ("DISCOUNT" if payload.get("in_discount") else "PREMIUM" if payload.get("in_premium") else "UNKNOWN")
    )

    missing = []
    if premium_discount == "UNKNOWN":
        missing.append("PREMIUM_DISCOUNT_UNKNOWN")
    if not in_ote:
        missing.append("OTE_CONTEXT_MISSING")

    return AgentObservation(
        agent_id="agent_4",
        source=EvidenceSource.TIMING,
        passed=bool(result.hard_filter_pass),
        score=_p1_safe_float(result.score),
        confidence=min(max(_p1_safe_float(result.score) / 100.0, 0.0), 1.0),
        reason=str(result.reason or "UNKNOWN"),
        hard_filter_pass=bool(result.hard_filter_pass),
        payload={
            "schema_version": "p1.agent_observation.v1",
            "agent_id": "agent_4",
            "status": "OK" if in_ote else "PARTIAL",
            "in_ote": in_ote,
            "premium_discount": premium_discount,
            "retracement_depth_class": shadow.get("retracement_depth_class", "UNKNOWN"),
            "timing_quality_score": _p1_safe_float(result.score),
            "precision_pct": _p1_safe_float(payload.get("precision_pct")),
            "swing_available": bool(payload.get("swing_used") or payload.get("swing_start_index")),
            "execution_readiness": payload.get("execution_readiness") or payload.get("readiness_state"),
            "readiness_state": payload.get("readiness_state") or payload.get("execution_readiness"),
            "readiness_reason": payload.get("readiness_reason"),
            "agent4_poi_handoff": payload.get("agent4_poi_handoff"),
            "agent4_consumed_poi": payload.get("agent4_consumed_poi"),
            "ote_handoff_status": payload.get("ote_handoff_status"),
            "unknown_fields": missing,
        },
        missing_evidence=missing,
        warnings=[],
    )

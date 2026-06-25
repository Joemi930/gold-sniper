"""Replay-only POI/OTE alignment diagnostics."""
from __future__ import annotations

from typing import Any

from agents.base_agent import AgentResult


def build_poi_ote_alignment_diagnostic(
    *,
    candle: dict[str, Any],
    blackboard: Any,
) -> dict[str, Any]:
    """Build a compact JSON-safe diagnostic without changing agent decisions."""
    a1 = _safe_agent_result(blackboard, "agent_1")
    a2 = _safe_agent_result(blackboard, "agent_2")
    a4 = _safe_agent_result(blackboard, "agent_4")
    a5 = _safe_agent_result(blackboard, "agent_5")
    a2_data = _safe_agent_data(blackboard, "agent_2")
    a4_data = _safe_agent_data(blackboard, "agent_4")
    a5_data = _safe_agent_data(blackboard, "agent_5")
    a4_payload = _payload(a4)
    market = _safe_market(blackboard)
    replay_meta = _safe_read(blackboard, "meta.replay") or {}
    tick = _safe_read(blackboard, "market_data.current_tick") or {}
    candles_1m = _safe_read(blackboard, "market_data.candles.1m") or []

    current_close = _last_candle_close(candles_1m)
    current_bid = _to_float(tick.get("bid"))
    current_ask = _to_float(tick.get("ask"))
    current_price = _mid_or_close(current_bid, current_ask, current_close, candle)
    atr = _first_float(
        market.get("atr_14_1m"),
        market.get("atr_14"),
        market.get("atr_14_15m"),
    )

    poi_zone = _agent2_poi_zone(a2_data, a2)
    poi_bottom, poi_top = zone_bounds(poi_zone, prefer_entry=True)
    poi_mid = _midpoint(poi_bottom, poi_top)
    price_in_poi = is_price_in_zone(current_price, poi_bottom, poi_top)
    distance_poi = distance_to_zone(current_price, poi_bottom, poi_top)

    levels = _agent4_levels(a4_data, a4)
    ote_low = _first_float(a4_data.get("ote_low"), _payload(a4).get("ote_low"), levels.get("ote_low"))
    ote_high = _first_float(a4_data.get("ote_high"), _payload(a4).get("ote_high"), levels.get("ote_high"))
    ote_mid = _midpoint(ote_low, ote_high)
    equilibrium = _first_float(a4_data.get("equilibrium"), _payload(a4).get("equilibrium"), levels.get("equilibrium"))
    price_in_ote = bool(a4_data.get("in_ote", False)) or is_price_in_zone(current_price, ote_low, ote_high)
    distance_ote = distance_to_zone(current_price, ote_low, ote_high)

    swing = a4_data.get("swing_used") if isinstance(a4_data.get("swing_used"), dict) else {}
    if not swing and isinstance(a4_payload.get("swing_used"), dict):
        swing = a4_payload.get("swing_used")
    swing_low = _first_float(swing.get("low_price"), levels.get("swing_low"))
    swing_high = _first_float(swing.get("high_price"), levels.get("swing_high"))
    ote_anchor_mode = a4_data.get("ote_anchor_mode") or a4_payload.get("ote_anchor_mode")

    overlap_low, overlap_high, overlap_size = zone_overlap(poi_bottom, poi_top, ote_low, ote_high)
    poi_ote_overlap = overlap_size is not None and overlap_size > 0
    zone_gap = distance_between_zones(poi_bottom, poi_top, ote_low, ote_high)
    center_gap = (
        round(abs(poi_mid - ote_mid), 6)
        if poi_mid is not None and ote_mid is not None
        else None
    )
    swing_contains_poi = bool(
        a4_data.get("agent4_swing_contains_agent2_zone", a4_payload.get("agent4_swing_contains_agent2_zone", False))
    ) or _swing_contains_zone(swing_low, swing_high, poi_bottom, poi_top)
    a2_dir = a2.direction if a2 else a2_data.get("direction")
    a4_dir = a4.direction if a4 else a4_data.get("direction")
    direction_match = bool(a2_dir and a4_dir and a2_dir == a4_dir)
    price_in_both = price_in_poi and price_in_ote
    suspected_issue = classify_alignment_issue(
        has_poi=poi_bottom is not None and poi_top is not None,
        has_ote=ote_low is not None and ote_high is not None,
        direction_match=direction_match,
        swing_contains_poi=swing_contains_poi,
        poi_ote_overlap=poi_ote_overlap,
        price_in_poi=price_in_poi,
        price_in_ote=price_in_ote,
        distance_to_poi=distance_poi,
        distance_to_ote=distance_ote,
        atr=atr,
        ote_anchor_mode=ote_anchor_mode,
    )

    return {
        "time": _iso_time(candle.get("time")),
        "phase": replay_meta.get("phase"),
        "eval_active": bool(replay_meta.get("eval_active", False)),
        "direction_agent1": a1.direction if a1 else _safe_agent_data(blackboard, "agent_1").get("direction"),
        "current_price": _round(current_price),
        "current_bid": _round(current_bid),
        "current_ask": _round(current_ask),
        "current_close_1m": _round(current_close),
        "agent2": {
            "hard_filter_pass": bool(a2.hard_filter_pass if a2 else a2_data.get("hard_filter_pass", False)),
            "reason": a2.reason if a2 else a2_data.get("reason"),
            "direction": a2_dir,
            "zone_type": _zone_get(poi_zone, "type"),
            "zone_top": _round(_zone_get(poi_zone, "top")),
            "zone_bottom": _round(_zone_get(poi_zone, "bottom")),
            "entry_zone_top": _round(_zone_get(poi_zone, "entry_zone_top")),
            "entry_zone_bottom": _round(_zone_get(poi_zone, "entry_zone_bottom")),
            "zone_mid": _round(poi_mid),
            "ob_score": _round(_first_float(a2_data.get("ob_score"), _payload(a2).get("ob_score"), _zone_get(poi_zone, "ob_score"), _zone_get(poi_zone, "score"))),
            "fresh": bool(_zone_get(poi_zone, "fresh")) if poi_zone else None,
            "zone_time": _zone_get(poi_zone, "time"),
            "age_bars": _zone_get(poi_zone, "age"),
            "price_in_poi": price_in_poi,
            "distance_to_poi_points": _round(distance_poi),
            "distance_to_poi_pct_atr": pct_atr(distance_poi, atr),
            "side_relative_to_poi": side_relative_to_zone(current_price, poi_bottom, poi_top),
        },
        "agent4": {
            "hard_filter_pass": bool(a4.hard_filter_pass if a4 else a4_data.get("hard_filter_pass", False)),
            "reason": a4.reason if a4 else a4_data.get("reason"),
            "direction": a4_dir,
            "swing_low": _round(swing_low),
            "swing_high": _round(swing_high),
            "swing_used_time_low": swing.get("low_time"),
            "swing_used_time_high": swing.get("high_time"),
            "ote_anchor_mode": ote_anchor_mode,
            "ote_low": _round(ote_low),
            "ote_high": _round(ote_high),
            "ote_mid": _round(ote_mid),
            "equilibrium": _round(equilibrium),
            "in_ote": bool(_payload(a4).get("in_ote", a4_data.get("in_ote", False))),
            "in_discount": bool(_payload(a4).get("in_discount", a4_data.get("in_discount", False))),
            "in_premium": bool(_payload(a4).get("in_premium", a4_data.get("in_premium", False))),
            "premium_discount_ok": bool(_payload(a4).get("premium_discount_ok", False)),
            "price_in_ote": price_in_ote,
            "distance_to_ote_points": _round(distance_ote),
            "distance_to_ote_pct_atr": pct_atr(distance_ote, atr),
            "side_relative_to_ote": side_relative_to_zone(current_price, ote_low, ote_high),
        },
        "alignment": {
            "poi_overlaps_ote": poi_ote_overlap,
            "overlap_low": _round(overlap_low),
            "overlap_high": _round(overlap_high),
            "overlap_size_points": _round(overlap_size),
            "distance_between_poi_and_ote_points": _round(zone_gap),
            "poi_center_vs_ote_center_distance": _round(center_gap),
            "agent2_direction_matches_agent4_direction": direction_match,
            "agent4_swing_contains_agent2_zone": swing_contains_poi,
            "ote_anchor_mode": ote_anchor_mode,
            "price_in_both": price_in_both,
            "price_in_poi_only": price_in_poi and not price_in_ote,
            "price_in_ote_only": price_in_ote and not price_in_poi,
            "price_in_none": not price_in_poi and not price_in_ote,
            "suspected_issue": suspected_issue,
        },
        "agent5": {
            "hard_filter_pass": bool(a5.hard_filter_pass if a5 else a5_data.get("hard_filter_pass", False)),
            "reason": a5.reason if a5 else a5_data.get("reason"),
            "score": _round(a5.score if a5 else a5_data.get("score")),
        },
    }


def classify_alignment_issue(
    *,
    has_poi: bool,
    has_ote: bool,
    direction_match: bool,
    swing_contains_poi: bool,
    poi_ote_overlap: bool,
    price_in_poi: bool,
    price_in_ote: bool,
    distance_to_poi: float | None,
    distance_to_ote: float | None,
    atr: float | None,
    ote_anchor_mode: str | None = None,
) -> str:
    if not has_poi:
        return "NO_AGENT2_POI"
    if not has_ote:
        return "NO_AGENT4_OTE"
    if not direction_match:
        return "DIRECTION_MISMATCH"
    if ote_anchor_mode == "NO_VALID_ANCHOR":
        return "AGENT4_POI_ANCHOR_UNAVAILABLE_FALLBACK_USED"
    if not swing_contains_poi:
        return "AGENT4_SWING_NOT_ANCHORED_TO_AGENT2_POI"
    if not poi_ote_overlap:
        return "POI_OTE_NO_OVERLAP"
    if price_in_poi and price_in_ote:
        return "POI_OTE_ALIGNED_NO_TRIGGER" if ote_anchor_mode == "AGENT2_POI_ANCHORED" else "VALID_ALIGNMENT_NO_TRIGGER"
    if ote_anchor_mode == "AGENT2_POI_ANCHORED" and not price_in_poi and not price_in_ote:
        return "AGENT4_POI_ANCHORED_PRICE_NOT_RETURNED"
    if _is_far(distance_to_poi, atr) and _is_far(distance_to_ote, atr):
        return "PRICE_FAR_FROM_POI_AND_OTE"
    if _is_far(distance_to_poi, atr):
        return "PRICE_FAR_FROM_POI"
    if _is_far(distance_to_ote, atr):
        return "PRICE_FAR_FROM_OTE"
    return "PRICE_NEAR_ZONE_NO_TRIGGER"


def zone_bounds(zone: dict[str, Any] | None, *, prefer_entry: bool = False) -> tuple[float | None, float | None]:
    if not zone:
        return None, None
    bottom = zone.get("entry_zone_bottom") if prefer_entry else None
    top = zone.get("entry_zone_top") if prefer_entry else None
    bottom = zone.get("bottom") if bottom is None else bottom
    top = zone.get("top") if top is None else top
    return _to_float(bottom), _to_float(top)


def is_price_in_zone(price: float | None, bottom: float | None, top: float | None) -> bool:
    return price is not None and bottom is not None and top is not None and bottom <= price <= top


def distance_to_zone(price: float | None, bottom: float | None, top: float | None) -> float | None:
    if price is None or bottom is None or top is None:
        return None
    if bottom <= price <= top:
        return 0.0
    if price < bottom:
        return round(bottom - price, 6)
    return round(price - top, 6)


def distance_between_zones(
    bottom_a: float | None,
    top_a: float | None,
    bottom_b: float | None,
    top_b: float | None,
) -> float | None:
    if None in (bottom_a, top_a, bottom_b, top_b):
        return None
    assert bottom_a is not None and top_a is not None and bottom_b is not None and top_b is not None
    if max(bottom_a, bottom_b) <= min(top_a, top_b):
        return 0.0
    if top_a < bottom_b:
        return round(bottom_b - top_a, 6)
    return round(bottom_a - top_b, 6)


def zone_overlap(
    bottom_a: float | None,
    top_a: float | None,
    bottom_b: float | None,
    top_b: float | None,
) -> tuple[float | None, float | None, float | None]:
    if None in (bottom_a, top_a, bottom_b, top_b):
        return None, None, None
    assert bottom_a is not None and top_a is not None and bottom_b is not None and top_b is not None
    low = max(bottom_a, bottom_b)
    high = min(top_a, top_b)
    if low > high:
        return None, None, 0.0
    return round(low, 6), round(high, 6), round(high - low, 6)


def pct_atr(distance: float | None, atr: float | None) -> float | None:
    if distance is None or not atr or atr <= 0:
        return None
    return round(distance / atr, 6)


def side_relative_to_zone(price: float | None, bottom: float | None, top: float | None) -> str | None:
    if price is None or bottom is None or top is None:
        return None
    if price < bottom:
        return "BELOW"
    if price > top:
        return "ABOVE"
    return "INSIDE"


def _safe_agent_result(blackboard: Any, agent_id: str) -> AgentResult | None:
    result = _safe_read(blackboard, f"agent_results.{agent_id}")
    return result if isinstance(result, AgentResult) else None


def _safe_agent_data(blackboard: Any, agent_id: str) -> dict[str, Any]:
    try:
        data = blackboard.get_agent(agent_id)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_market(blackboard: Any) -> dict[str, Any]:
    try:
        data = blackboard.get_market()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_read(blackboard: Any, path: str) -> Any:
    try:
        return blackboard.read_sync(path)
    except Exception:
        return None


def _payload(result: AgentResult | None) -> dict[str, Any]:
    return result.payload if result and isinstance(result.payload, dict) else {}


def _agent2_poi_zone(a2_data: dict[str, Any], a2_result: AgentResult | None) -> dict[str, Any] | None:
    payload = _payload(a2_result)
    zone = a2_data.get("poi_zone") or a2_data.get("active_ob") or payload.get("poi_zone") or payload.get("active_ob")
    return zone if isinstance(zone, dict) else None


def _agent4_levels(a4_data: dict[str, Any], a4_result: AgentResult | None) -> dict[str, Any]:
    payload = _payload(a4_result)
    levels = payload.get("levels")
    if isinstance(levels, dict):
        return levels
    return {
        "swing_low": (a4_data.get("swing_used") or {}).get("low_price") if isinstance(a4_data.get("swing_used"), dict) else None,
        "swing_high": (a4_data.get("swing_used") or {}).get("high_price") if isinstance(a4_data.get("swing_used"), dict) else None,
        "ote_low": a4_data.get("ote_low"),
        "ote_high": a4_data.get("ote_high"),
        "equilibrium": a4_data.get("equilibrium"),
    }


def _first_float(*values: Any) -> float | None:
    for value in values:
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: Any) -> float | None:
    number = _to_float(value)
    return round(number, 6) if number is not None else None


def _midpoint(bottom: float | None, top: float | None) -> float | None:
    if bottom is None or top is None:
        return None
    return round((bottom + top) / 2.0, 6)


def _last_candle_close(candles: list[dict[str, Any]]) -> float | None:
    if not candles:
        return None
    return _to_float(candles[-1].get("close"))


def _mid_or_close(
    bid: float | None,
    ask: float | None,
    close: float | None,
    candle: dict[str, Any],
) -> float | None:
    if bid and ask and bid > 0 and ask > 0:
        return round((bid + ask) / 2.0, 6)
    return close if close is not None else _to_float(candle.get("close"))


def _zone_get(zone: dict[str, Any] | None, key: str) -> Any:
    return zone.get(key) if zone else None


def _swing_contains_zone(
    swing_low: float | None,
    swing_high: float | None,
    zone_bottom: float | None,
    zone_top: float | None,
) -> bool:
    if None in (swing_low, swing_high, zone_bottom, zone_top):
        return False
    assert swing_low is not None and swing_high is not None and zone_bottom is not None and zone_top is not None
    return swing_low <= zone_bottom <= zone_top <= swing_high


def _is_far(distance: float | None, atr: float | None) -> bool:
    return distance is not None and atr is not None and atr > 0 and distance > atr


def _iso_time(value: Any) -> str | None:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value) if value is not None else None

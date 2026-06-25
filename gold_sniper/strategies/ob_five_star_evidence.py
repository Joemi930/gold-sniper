from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo


def enrich_active_ob_with_five_star_evidence(
    ob: dict[str, Any],
    candles: Sequence[dict[str, Any]],
    context: dict[str, Any] | None,
    current_time: Any,
) -> dict[str, Any]:
    enriched = deepcopy(ob)
    safe_candles, guard = _candles_until(candles, current_time)
    created_at = _first_present(ob, "created_at", "time", "timestamp", "ob_created_at", "candle_time")
    created_time = _parse_time(created_at) or _parse_time(current_time)
    direction = _direction(ob)
    created_index = _nearest_index_at_or_before(safe_candles, created_time)

    imbalance = detect_imbalance_after_ob(ob, safe_candles, created_index, direction)
    sweep = detect_liquidity_sweep_before_ob(ob, safe_candles, created_index, direction)
    extreme = detect_structural_extreme_ob(ob, safe_candles, created_index, direction)
    unmitigated = detect_unmitigated_ob(ob)
    session = infer_ob_creation_session(ob, current_time)
    golden = detect_golden_hour_return(ob, current_time)

    evidence_items = [imbalance, sweep, extreme, unmitigated, session, golden]
    missing = [item["reason"] for item in evidence_items if not item["passed"]]
    confidence_values = [float(item.get("confidence") or 0.0) for item in evidence_items[:5]]
    evidence = {
        "imbalance_created": imbalance["passed"],
        "imbalance_reason": imbalance["reason"],
        "imbalance_source": imbalance["source"],
        "imbalance_confidence": imbalance["confidence"],
        "liquidity_sweep_before": sweep["passed"],
        "liquidity_sweep_reason": sweep["reason"],
        "liquidity_sweep_source": sweep["source"],
        "liquidity_sweep_confidence": sweep["confidence"],
        "is_extreme_ob": extreme["passed"],
        "extreme_ob_reason": extreme["reason"],
        "extreme_ob_source": extreme["source"],
        "extreme_ob_confidence": extreme["confidence"],
        "unmitigated": unmitigated["passed"],
        "unmitigated_reason": unmitigated["reason"],
        "unmitigated_source": unmitigated["source"],
        "unmitigated_confidence": unmitigated["confidence"],
        "session_created": session["session_created"],
        "london_or_ny_creation": session["passed"],
        "session_created_reason": session["reason"],
        "session_created_source": session["source"],
        "session_created_confidence": session["confidence"],
        "golden_hour_return": golden["passed"],
        "golden_hour_reason": golden["reason"],
        "golden_hour_source": golden["source"],
        "golden_hour_confidence": golden["confidence"],
        "missing_evidence": missing,
        "evidence_confidence": round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.0,
        "no_lookahead_guard": guard,
        "before_enrichment_star_count": _explicit_star_count(ob),
    }
    evidence["after_enrichment_star_count"] = sum(
        1 for key in ("imbalance_created", "liquidity_sweep_before", "is_extreme_ob", "unmitigated", "london_or_ny_creation")
        if evidence.get(key) is True
    )
    enriched["five_star_evidence"] = evidence
    enriched.setdefault("created_at", created_time.isoformat() if created_time else None)
    enriched["imbalance_created"] = evidence["imbalance_created"]
    enriched["liquidity_sweep_before"] = evidence["liquidity_sweep_before"]
    enriched["is_extreme_ob"] = evidence["is_extreme_ob"]
    enriched["session_created"] = evidence["session_created"]
    enriched["golden_hour_return"] = evidence["golden_hour_return"]
    return enriched


def detect_imbalance_after_ob(ob: dict[str, Any], candles: Sequence[dict[str, Any]], ob_index: int | None, direction: str) -> dict[str, Any]:
    explicit = _explicit_bool(ob, "imbalance_created", "fvg_created_after_ob", "fvg_created", "has_fvg", "has_imbalance", "displacement_created", "impulse_after_ob")
    if explicit is not None:
        return _evidence(explicit, "EXPLICIT_IMBALANCE_FIELD" if explicit else "NO_IMBALANCE_DETECTED", "field", 1.0 if explicit else 0.0)
    if ob_index is None or len(candles) < ob_index + 3:
        return _evidence(False, "INSUFFICIENT_CANDLE_DATA", "missing", 0.0)
    end = min(len(candles) - 2, ob_index + 8)
    for idx in range(ob_index, end):
        c0, c2 = candles[idx], candles[idx + 2]
        if direction == "BULLISH" and _num(c0.get("high")) < _num(c2.get("low")):
            return _evidence(True, "FVG_AFTER_OB_DETECTED", "computed_from_candles", 0.8)
        if direction == "BEARISH" and _num(c0.get("low")) > _num(c2.get("high")):
            return _evidence(True, "FVG_AFTER_OB_DETECTED", "computed_from_candles", 0.8)
    if _displacement_after(candles, ob_index, direction):
        return _evidence(True, "DISPLACEMENT_AFTER_OB_DETECTED", "computed_from_candles", 0.55)
    return _evidence(False, "NO_IMBALANCE_DETECTED", "computed", 0.0)


def detect_liquidity_sweep_before_ob(ob: dict[str, Any], candles: Sequence[dict[str, Any]], ob_index: int | None, direction: str) -> dict[str, Any]:
    explicit = _explicit_bool(ob, "liquidity_sweep_before", "sweep_before_ob", "has_sweep_before_ob", "sweep_detected")
    if explicit is not None:
        return _evidence(explicit, "EXPLICIT_SWEEP_FIELD" if explicit else "NO_SWEEP_DETECTED", "field", 1.0 if explicit else 0.0)
    if ob_index is None or ob_index < 4:
        return _evidence(False, "INSUFFICIENT_CANDLE_DATA", "missing", 0.0)
    start = max(0, ob_index - 20)
    prior = candles[start:ob_index]
    if len(prior) < 4:
        return _evidence(False, "INSUFFICIENT_CANDLE_DATA", "missing", 0.0)
    recent = prior[:-1]
    sweep_candle = prior[-1]
    if direction == "BULLISH":
        swing_low = min(_num(c.get("low")) for c in recent)
        if _num(sweep_candle.get("low")) < swing_low and _num(sweep_candle.get("close")) > swing_low:
            return _evidence(True, "SELL_SIDE_SWEEP_BEFORE_BULLISH_OB", "computed_from_swing_low", 0.7)
    if direction == "BEARISH":
        swing_high = max(_num(c.get("high")) for c in recent)
        if _num(sweep_candle.get("high")) > swing_high and _num(sweep_candle.get("close")) < swing_high:
            return _evidence(True, "BUY_SIDE_SWEEP_BEFORE_BEARISH_OB", "computed_from_swing_high", 0.7)
    return _evidence(False, "NO_SWEEP_DETECTED", "computed", 0.0)


def detect_structural_extreme_ob(ob: dict[str, Any], candles: Sequence[dict[str, Any]], ob_index: int | None, direction: str) -> dict[str, Any]:
    explicit = _explicit_bool(ob, "is_extreme_ob", "structural_extreme", "extreme_ob", "at_structure_extreme")
    if explicit is not None:
        return _evidence(explicit, "EXPLICIT_EXTREME_FIELD" if explicit else "NOT_STRUCTURAL_EXTREME", "field", 1.0 if explicit else 0.0)
    if ob_index is None or not candles:
        return _evidence(False, "NOT_STRUCTURAL_EXTREME_OR_INSUFFICIENT_DATA", "missing", 0.0)
    start = max(0, ob_index - 50)
    window = candles[start:ob_index + 1]
    if len(window) < 5:
        return _evidence(False, "NOT_STRUCTURAL_EXTREME_OR_INSUFFICIENT_DATA", "missing", 0.0)
    avg_range = sum(abs(_num(c.get("high")) - _num(c.get("low"))) for c in window) / len(window)
    tolerance = max(avg_range * 0.25, 0.01)
    if direction == "BULLISH":
        ob_low = _num(_first_present(ob, "low", "bottom", "entry_zone_bottom"))
        if ob_low <= min(_num(c.get("low")) for c in window) + tolerance:
            return _evidence(True, "LOWEST_OB_IN_LOCAL_LEG", "computed_from_swing_window", 0.75)
    if direction == "BEARISH":
        ob_high = _num(_first_present(ob, "high", "top", "entry_zone_top"))
        if ob_high >= max(_num(c.get("high")) for c in window) - tolerance:
            return _evidence(True, "HIGHEST_OB_IN_LOCAL_LEG", "computed_from_swing_window", 0.75)
    return _evidence(False, "NOT_STRUCTURAL_EXTREME", "computed", 0.0)


def infer_ob_creation_session(ob: dict[str, Any], current_time: Any) -> dict[str, Any]:
    raw_session = _first_present(ob, "session_created", "created_session", "session")
    if raw_session:
        bucket = _session_bucket(raw_session)
        return {"passed": bucket in {"LONDON", "NY"}, "session_created": bucket, "reason": f"OB_CREATED_DURING_{bucket}" if bucket in {"LONDON", "NY"} else "ASIAN_OR_LOW_LIQUIDITY_OB_NOT_FIVE_STAR", "source": "field", "confidence": 1.0}
    created_at = _first_present(ob, "created_at", "time", "timestamp", "ob_created_at", "candle_time")
    dt = _parse_time(created_at)
    source = "created_at"
    confidence = 0.8
    if dt is None:
        dt = _parse_time(current_time)
        source = "fallback_current_time"
        confidence = 0.4
    if dt is None:
        return {"passed": False, "session_created": "UNKNOWN", "reason": "CREATED_AT_MISSING", "source": "missing", "confidence": 0.0}
    bucket = _session_bucket_from_time(dt)
    return {
        "passed": bucket in {"LONDON", "NY"},
        "session_created": bucket,
        "reason": f"OB_CREATED_DURING_{bucket}" if bucket in {"LONDON", "NY"} else "ASIAN_OR_LOW_LIQUIDITY_OB_NOT_FIVE_STAR",
        "source": source,
        "confidence": confidence,
    }


def detect_golden_hour_return(ob: dict[str, Any], current_time: Any) -> dict[str, Any]:
    explicit = _explicit_bool(ob, "golden_hour_return", "return_in_golden_hour")
    if explicit is not None:
        return _evidence(explicit, "EXPLICIT_GOLDEN_HOUR_FIELD" if explicit else "NO_GOLDEN_HOUR_RETURN", "field", 1.0 if explicit else 0.0)
    dt = _parse_time(_first_present(ob, "return_time", "touched_at")) or _parse_time(current_time)
    if dt is None:
        return _evidence(False, "NO_GOLDEN_HOUR_RETURN", "missing", 0.0)
    paris = dt.astimezone(ZoneInfo("Europe/Paris"))
    if paris.hour == 16:
        return _evidence(True, "RETURN_DURING_16_17_PARIS", "current_record_timestamp", 0.6)
    return _evidence(False, "NO_GOLDEN_HOUR_RETURN", "current_record_timestamp", 0.0)


def detect_unmitigated_ob(ob: dict[str, Any]) -> dict[str, Any]:
    state = str(_first_present(ob, "human_zone_state_shadow", "state_shadow", "zone_lifecycle_state", "lifecycle", "state") or "").upper()
    if ob.get("fresh") is True or state in {"FRESH", "UNMITIGATED"}:
        return _evidence(True, "ACTIVE_OB_FRESH", "lifecycle", 1.0)
    if ob.get("mitigated") is True or state:
        return _evidence(False, f"OB_LIFECYCLE_{state or 'MITIGATED'}", "lifecycle", 1.0)
    return _evidence(False, "FIELD_MISSING", "missing", 0.0)


def _candles_until(candles: Sequence[dict[str, Any]], current_time: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    current = _parse_time(current_time)
    safe = []
    max_time = None
    for candle in candles:
        candle_time = _parse_time(candle.get("time"))
        if current is not None and candle_time is not None and candle_time > current:
            continue
        safe.append(candle)
        if candle_time is not None and (max_time is None or candle_time > max_time):
            max_time = candle_time
    return safe, {
        "status": "OK",
        "current_time": current.isoformat() if current else None,
        "max_candle_time_used": max_time.isoformat() if max_time else None,
        "future_candles_used": False,
    }


def _nearest_index_at_or_before(candles: Sequence[dict[str, Any]], timestamp: datetime | None) -> int | None:
    if timestamp is None:
        return len(candles) - 1 if candles else None
    best = None
    for index, candle in enumerate(candles):
        candle_time = _parse_time(candle.get("time"))
        if candle_time is not None and candle_time <= timestamp:
            best = index
    return best


def _displacement_after(candles: Sequence[dict[str, Any]], ob_index: int, direction: str) -> bool:
    end = min(len(candles), ob_index + 6)
    sample = candles[ob_index:end]
    if len(sample) < 3:
        return False
    ranges = [abs(_num(c.get("high")) - _num(c.get("low"))) for c in sample]
    avg_range = sum(ranges) / len(ranges)
    for candle in sample[1:]:
        body = abs(_num(candle.get("close")) - _num(candle.get("open")))
        if body < avg_range * 0.7:
            continue
        if direction == "BULLISH" and _num(candle.get("close")) > _num(candle.get("open")):
            return True
        if direction == "BEARISH" and _num(candle.get("close")) < _num(candle.get("open")):
            return True
    return False


def _explicit_star_count(ob: dict[str, Any]) -> int:
    return sum(
        1 for value in (
            _explicit_bool(ob, "imbalance_created", "fvg_created_after_ob", "fvg_created", "has_fvg", "has_imbalance", "displacement_created", "impulse_after_ob"),
            _explicit_bool(ob, "liquidity_sweep_before", "sweep_before_ob", "has_sweep_before_ob", "sweep_detected"),
            _explicit_bool(ob, "is_extreme_ob", "structural_extreme", "extreme_ob", "at_structure_extreme"),
            detect_unmitigated_ob(ob)["passed"],
            _session_bucket(_first_present(ob, "session_created", "created_session", "session")) in {"LONDON", "NY"},
        )
        if value is True
    )


def _explicit_bool(ob: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key in ob and ob.get(key) is not None:
            return bool(ob.get(key))
    return None


def _evidence(passed: bool, reason: str, source: str, confidence: float) -> dict[str, Any]:
    return {"passed": bool(passed), "reason": reason, "source": source, "confidence": round(float(confidence), 4)}


def _direction(ob: dict[str, Any]) -> str:
    raw = str(_first_present(ob, "direction", "ob_type", "type") or "").upper()
    if any(token in raw for token in ("BULL", "BUY", "LONG")):
        return "BULLISH"
    if any(token in raw for token in ("BEAR", "SELL", "SHORT")):
        return "BEARISH"
    return "UNKNOWN"


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _num(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


def _session_bucket(value: Any) -> str:
    text = str(value or "UNKNOWN").upper()
    if "TOKYO" in text or "ASIA" in text:
        return "TOKYO"
    if "LONDON" in text:
        return "LONDON"
    if text.startswith("NY") or "NEW_YORK" in text:
        return "NY"
    return "UNKNOWN" if text == "UNKNOWN" else "OTHER"


def _session_bucket_from_time(dt: datetime) -> str:
    utc_hour = dt.astimezone(ZoneInfo("UTC")).hour
    if 7 <= utc_hour < 12:
        return "LONDON"
    if 12 <= utc_hour < 21:
        return "NY"
    if utc_hour >= 22 or utc_hour < 7:
        return "TOKYO"
    return "OTHER"

import asyncio
import time as _time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Sequence

from agents.base_agent import AgentResult
from config import AGENT_DASHBOARD_PULSE_SEC, BE_PLUS_RR, SL_BUFFER_POINTS, TP1_RR, TP2_RR
from core.blackboard import BLACKBOARD, BlackBoard
from core.visual_layers import VISUAL_LAYERS, VisualHLine, VisualMarker
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger


AMD_ACCUMULATION_WINDOW = 10
AMD_SWEEP_ATR_MIN = 0.05
AMD_CHOCH_BODY_MIN = 0.5
AMD_MAX_CHOCH_DELAY = 5


def _candle_time_unix(
    candles: Sequence,
    candle_index: int | None,
    current_time_unix: int,
    timeframe_seconds: int,
) -> int:
    if candle_index is not None and 0 <= candle_index < len(candles):
        candle = candles[candle_index]
        raw_time = candle.get("time") if isinstance(candle, dict) else None
        if hasattr(raw_time, "timestamp"):
            return int(raw_time.timestamp())
        if isinstance(raw_time, (int, float)):
            return int(raw_time)
    age = len(candles) - 1 - int(candle_index or len(candles) - 1)
    return current_time_unix - max(age, 0) * timeframe_seconds


def _publish_visual_layers_agent5(
    choch_confirmed: bool,
    choch_price: float,
    choch_time_unix: int,
    direction: str,
    sweep_price: float,
    sweep_time_unix: int,
    amd_phase: int,
) -> None:
    layers = []

    if amd_phase >= 2 and sweep_time_unix and sweep_price:
        layers.append(
            VisualMarker(
                time=sweep_time_unix,
                price=sweep_price,
                color="rgba(59, 130, 246, 1.0)",
                shape="circle",
                size=1,
                label="Sweep 1M",
                position="belowBar" if direction == "LONG" else "aboveBar",
            )
        )

    if choch_confirmed and choch_price and choch_time_unix:
        is_long = direction == "LONG"
        marker_color = "rgba(16, 185, 129, 1.0)" if is_long else "rgba(239, 68, 68, 1.0)"
        line_color = "rgba(16, 185, 129, 0.80)" if is_long else "rgba(239, 68, 68, 0.80)"
        layers.append(
            VisualMarker(
                time=choch_time_unix,
                price=choch_price,
                color=marker_color,
                shape="arrowUp" if is_long else "arrowDown",
                size=3,
                label=f"CHoCH {'UP' if is_long else 'DOWN'}",
                position="belowBar" if is_long else "aboveBar",
            )
        )
        layers.append(
            VisualHLine(
                time_start=choch_time_unix,
                price=choch_price,
                color=line_color,
                style="solid",
                width=2,
                label=f"CHoCH Entry @ {choch_price:.2f}",
                label_side="right",
            )
        )

    VISUAL_LAYERS.set_layers("agent_5", layers)


class AMDPhase(Enum):
    IDLE = "IDLE"
    ACCUMULATION_DETECTED = "ACCUMULATION"
    MANIPULATION_DETECTED = "MANIPULATION"
    DISTRIBUTION_CONFIRMED = "DISTRIBUTION"


@dataclass
class AMDState:
    phase: AMDPhase = AMDPhase.IDLE
    accumulation_high: float = 0.0
    accumulation_low: float = 0.0
    accumulation_start_index: int = 0
    sweep_index: int = -1
    sweep_price: float = 0.0
    choch_index: int = -1
    last_swing_high_1m: float = 0.0
    last_swing_low_1m: float = 0.0


def _candle_value(candle, key: str, index: int) -> float:
    """Lit une valeur OHLC depuis un dict ou une sequence."""
    if isinstance(candle, dict):
        return float(candle[key])
    return float(candle[index])


def _atr_14_from_candles(candles: Sequence, period: int = 14) -> float:
    """ATR-14 estimate from a candle stream (used to size the EXECUTION_TF stop).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|), averaged over
    the last `period` bars. Safe fallback to last range, then 1.0.
    """
    try:
        cs = list(candles or [])
        if len(cs) < 2:
            return 1.0
        trs: list[float] = []
        for i in range(1, len(cs)):
            hi = float(cs[i].get("high", 0.0))
            lo = float(cs[i].get("low", 0.0))
            pc = float(cs[i - 1].get("close", cs[i - 1].get("open", hi)))
            trs.append(max(hi - lo, abs(hi - pc), abs(lo - pc)))
        window = trs[-period:] if len(trs) >= period else trs
        atr = sum(window) / len(window) if window else 1.0
        return float(atr) if atr > 0 else 1.0
    except Exception:
        return 1.0


def _swing_levels(candles: Sequence, k: int = 2) -> tuple[list[float], list[float]]:
    """Fractal swing highs/lows: bar i is a swing if its high(low) strictly
    exceeds the highs(lows) of k bars on EACH side. Causal by construction —
    only fully closed bars in the provided history are examined."""
    highs: list[float] = []
    lows: list[float] = []
    cs = list(candles or [])
    n = len(cs)
    for i in range(k, n - k):
        hi = float(cs[i].get("high", 0.0)); lo = float(cs[i].get("low", 0.0))
        if all(hi > float(cs[j].get("high", 0.0)) for j in range(i - k, i + k + 1) if j != i):
            highs.append(hi)
        if all(lo < float(cs[j].get("low", 0.0)) for j in range(i - k, i + k + 1) if j != i):
            lows.append(lo)
    return highs, lows


def _normalize_candles(candles: Sequence) -> list[dict]:
    """Normalise les bougies 1M au format dict open/high/low/close/volume."""
    normalized = []
    for candle in candles:
        if isinstance(candle, dict):
            volume = float(candle.get("volume", 0.0))
        elif len(candle) > 4:
            volume = _candle_value(candle, "volume", 4)
        else:
            volume = 0.0

        normalized.append(
            {
                "open": _candle_value(candle, "open", 0),
                "high": _candle_value(candle, "high", 1),
                "low": _candle_value(candle, "low", 2),
                "close": _candle_value(candle, "close", 3),
                "volume": volume,
            }
        )
    return normalized


def _price_in_zone(price: float, zone: dict | None) -> bool:
    """Verifie si le prix est dans la zone POI."""
    if not zone:
        return False
    bottom = zone.get("entry_zone_bottom", zone.get("bottom"))
    top = zone.get("entry_zone_top", zone.get("top"))
    if bottom is None or top is None:
        return False
    return float(bottom) <= price <= float(top)


def _zone_bounds(zone: dict | None) -> tuple[float | None, float | None]:
    if not zone:
        return None, None
    bottom = zone.get("entry_zone_bottom", zone.get("bottom"))
    top = zone.get("entry_zone_top", zone.get("top"))
    if bottom is None or top is None:
        return None, None
    return float(bottom), float(top)


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _bounds_from_poi(poi: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(poi, dict) or not poi:
        return None, None
    bounds = poi.get("price_bounds")
    if isinstance(bounds, dict):
        low = bounds.get("low", bounds.get("bottom", bounds.get("entry_zone_bottom")))
        high = bounds.get("high", bounds.get("top", bounds.get("entry_zone_top")))
        if low is not None and high is not None:
            try:
                low_f = float(low)
                high_f = float(high)
                return min(low_f, high_f), max(low_f, high_f)
            except (TypeError, ValueError):
                return None, None
    low = poi.get("low", poi.get("bottom", poi.get("entry_zone_bottom")))
    high = poi.get("high", poi.get("top", poi.get("entry_zone_top")))
    if low is not None and high is not None:
        try:
            low_f = float(low)
            high_f = float(high)
            return min(low_f, high_f), max(low_f, high_f)
        except (TypeError, ValueError):
            return None, None
    return None, None


def _normalize_p2a_poi_for_agent5(poi: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(poi, dict) or not poi:
        return None
    bottom, top = _bounds_from_poi(poi)
    if bottom is None or top is None:
        return None
    normalized = dict(poi)
    normalized["bottom"] = bottom
    normalized["top"] = top
    normalized["entry_zone_bottom"] = bottom
    normalized["entry_zone_top"] = top
    normalized.setdefault("type", poi.get("type") or poi.get("poi_type") or poi.get("poi_type_normalized") or "UNKNOWN")
    normalized.setdefault("poi_type", poi.get("poi_type") or poi.get("poi_type_normalized") or normalized.get("type"))
    normalized.setdefault("execution_readiness", poi.get("execution_readiness") or poi.get("readiness_state") or "UNKNOWN")
    normalized.setdefault("source", "P2A_SELECTED_POI")
    return normalized


def _distance_to_zone(price: float, bottom: float | None, top: float | None) -> float | None:
    if bottom is None or top is None:
        return None
    if bottom <= price <= top:
        return 0.0
    if price < bottom:
        return round(bottom - price, 6)
    return round(price - top, 6)


def _safe_agent_result(blackboard: BlackBoard, agent_id: str) -> AgentResult | None:
    try:
        result = blackboard.read_sync(f"agent_results.{agent_id}")
    except KeyError:
        return None
    return result if isinstance(result, AgentResult) else None


def _extract_agent2_p2a_poi_for_agent5(blackboard: BlackBoard) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """
    Returns a normalized POI zone usable by Agent5 and a handoff diagnostic.

    Delegates to the centralized poi_contract module (Phase 7E).
    P2-A selected_poi is the priority source. Legacy Agent2 fields are only
    compatibility fallbacks.
    """
    from gold_sniper.agents.poi_contract import extract_p2a_selected_poi

    anchor, diagnostics = extract_p2a_selected_poi(blackboard)
    if not anchor:
        if diagnostics.get("failure_reason") == "NO_P2A_POI_OR_BOUNDS":
            diagnostics = dict(diagnostics)
            diagnostics["failure_reason"] = "NO_SELECTED_POI_OR_BOUNDS"
        return None, diagnostics

    normalized = dict(anchor)
    normalized["agent5_handoff_source"] = diagnostics["source"]
    diagnostics["readiness"] = str(
        normalized.get("execution_readiness")
        or normalized.get("readiness_state")
        or "UNKNOWN"
    )
    return normalized, diagnostics


def _agent4_ote_bounds(a4_data: dict, a4_result: AgentResult | None) -> tuple[float | None, float | None]:
    payload = a4_result.payload if a4_result and a4_result.payload else {}
    levels = payload.get("levels") or {}
    low = a4_data.get("ote_low", payload.get("ote_low", levels.get("ote_low")))
    high = a4_data.get("ote_high", payload.get("ote_high", levels.get("ote_high")))
    if low is None or high is None:
        return None, None
    return float(low), float(high)


def _price_in_bounds(price: float, bottom: float | None, top: float | None) -> bool:
    return bottom is not None and top is not None and bottom <= price <= top


def build_replay_agent_5_diagnostic(
    *,
    candle: dict[str, Any] | None,
    blackboard: BlackBoard,
    candles_1m: Sequence,
    result: AgentResult,
) -> dict[str, Any]:
    a1_data = blackboard.get_agent("agent_1")
    a2_data = blackboard.get_agent("agent_2")
    a4_data = blackboard.get_agent("agent_4")
    market_data = blackboard.get_market()
    agent2_result = _safe_agent_result(blackboard, "agent_2")
    agent4_result = _safe_agent_result(blackboard, "agent_4")
    replay_meta = {}
    try:
        replay_meta = blackboard.read_sync("meta.replay") or {}
    except KeyError:
        pass

    tick = None
    try:
        tick = blackboard.read_sync("market_data.current_tick")
    except KeyError:
        pass
    normalized = _normalize_candles(candles_1m)
    if tick:
        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))
        current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else normalized[-1]["close"]
    else:
        current_price = normalized[-1]["close"] if normalized else 0.0

    poi_zone, poi_handoff = _extract_agent2_p2a_poi_for_agent5(blackboard)
    poi_bottom, poi_top = _zone_bounds(poi_zone)
    ote_bottom, ote_top = _agent4_ote_bounds(a4_data, agent4_result)
    price_in_poi = _price_in_zone(current_price, poi_zone)
    price_in_ote = bool(a4_data.get("in_ote", False)) or _price_in_bounds(current_price, ote_bottom, ote_top)

    payload = result.payload or {}
    atr = float(market_data.get("atr_14_1m") or market_data.get("atr_14") or 1.0)
    accumulation = _detect_accumulation(normalized, poi_zone, atr) if poi_zone else None
    agent4_payload = agent4_result.payload if agent4_result and agent4_result.payload else {}

    candles_15m = []
    try:
        candles_15m = list(blackboard.read_sync("market_data.candles.15m") or [])
    except KeyError:
        pass

    return {
        "time": candle.get("time").isoformat() if candle and hasattr(candle.get("time"), "isoformat") else str(candle.get("time")) if candle else None,
        "phase": replay_meta.get("phase"),
        "eval_active": replay_meta.get("eval_active"),
        "direction": result.direction or a1_data.get("direction"),
        "current_price": round(float(current_price), 6),
        "agent2_hard_filter_pass": bool(a2_data.get("hard_filter_pass", agent2_result.hard_filter_pass if agent2_result else False)),
        "agent2_reason": a2_data.get("reason") or (agent2_result.reason if agent2_result else None),
        "agent2_zone_top": poi_top,
        "agent2_zone_bottom": poi_bottom,
        "agent2_zone_type": poi_zone.get("type") if poi_zone else None,
        "agent2_zone_fresh": bool(poi_zone.get("fresh", False)) if poi_zone else None,
        "agent5_poi_handoff_source": poi_handoff.get("source"),
        "agent5_poi_handoff_selected": poi_handoff.get("selected_poi_present"),
        "agent5_poi_handoff_candidate_count": poi_handoff.get("candidate_count"),
        "agent5_poi_handoff_bounds_present": poi_handoff.get("bounds_present"),
        "agent5_poi_handoff_readiness": poi_handoff.get("readiness"),
        "agent5_poi_handoff_failure_reason": poi_handoff.get("failure_reason"),
        "agent5_consumed_poi_top": poi_top,
        "agent5_consumed_poi_bottom": poi_bottom,
        "agent5_consumed_poi_type": poi_zone.get("type") if poi_zone else None,
        "agent2_ob_score": a2_data.get("ob_score") or (agent2_result.payload or {}).get("ob_score") if agent2_result else a2_data.get("ob_score"),
        "agent4_hard_filter_pass": bool(a4_data.get("hard_filter_pass", agent4_result.hard_filter_pass if agent4_result else False)),
        "agent4_reason": a4_data.get("reason") or (agent4_result.reason if agent4_result else None),
        "agent4_ote_top": ote_top,
        "agent4_ote_bottom": ote_bottom,
        "agent4_premium_discount": {
            "in_discount": bool(a4_data.get("in_discount", agent4_payload.get("in_discount", False))),
            "in_premium": bool(a4_data.get("in_premium", agent4_payload.get("in_premium", False))),
            "premium_discount_ok": bool(agent4_payload.get("premium_discount_ok", False)),
        },
        "price_in_agent2_poi": price_in_poi,
        "price_in_agent4_ote": price_in_ote,
        "distance_to_poi_points": _distance_to_zone(current_price, poi_bottom, poi_top),
        "distance_to_ote_points": _distance_to_zone(current_price, ote_bottom, ote_top),
        "candles_1m_count": len(normalized),
        "candles_15m_count": len(candles_15m),
        "amd_state": payload.get("phase") or payload.get("amd_phase"),
        "accumulation_detected": bool(accumulation),
        "manipulation_detected": bool(payload.get("sweep_1m_confirmed") or payload.get("sweep_detected")),
        "choch_detected": bool(payload.get("choch_detected", False)),
        "trigger_found": bool(result.hard_filter_pass),
        "final_reason": result.reason,
        "hard_filter_pass": result.hard_filter_pass,
        "score": float(result.score),
    }


def diagnose_contextual_trigger(
    payload: dict,
    agent1_score: float,
    legacy_hard_filter_pass: bool,
) -> dict:
    """
    P1.29 - Analyse contextuelle d'Agent 5 selon le setup ICT.
    Ne modifie pas la decision reelle, genere uniquement un diagnostic shadow.
    """
    setup_family = "UNKNOWN"
    if agent1_score >= 80:
        setup_family = "TREND_CONTINUATION"
    elif agent1_score >= 65:
        setup_family = "SNIPER_PULLBACK"
    elif agent1_score > 0:
        setup_family = "REVERSAL"
    else:
        setup_family = "OBSERVATION"

    agent5_required = (setup_family == "REVERSAL")

    missing_trigger_conflict = "NONE"
    if not legacy_hard_filter_pass:
        if setup_family == "REVERSAL":
            missing_trigger_conflict = "HARD_VETO"
        elif setup_family in ["TREND_CONTINUATION", "SNIPER_PULLBACK"]:
            missing_trigger_conflict = "SOFT_WARNING"
        else:
            missing_trigger_conflict = "NONE"

    return {
        "trigger_kind": "AMD_CHOCH" if payload.get("choch_detected") else "NONE",
        "micro_shift_strength": payload.get("displacement_ratio", 0.0),
        "compression_type": "AMD_ACCUMULATION" if payload.get("amd_phase", 0) > 0 else "NONE",
        "time_window_valid": True,
        "poi_context_valid": True,
        "setup_family_hint": setup_family,
        "agent5_required": agent5_required,
        "missing_trigger_conflict_mode": missing_trigger_conflict,
        "reason_contextual": f"{setup_family} - required={agent5_required} conflict={missing_trigger_conflict}"
    }


def _find_last_swing_high(candles: Sequence, n: int = 2) -> float | None:
    """Retourne le dernier micro swing high confirme."""
    ohlcv = _normalize_candles(candles)
    if len(ohlcv) < 2 * n + 1:
        return None
    for i in range(len(ohlcv) - n - 1, n - 1, -1):
        high = ohlcv[i]["high"]
        if all(high > ohlcv[i - k]["high"] for k in range(1, n + 1)) and all(
            high > ohlcv[i + k]["high"] for k in range(1, n + 1)
        ):
            return high
    return None


def _find_last_swing_low(candles: Sequence, n: int = 2) -> float | None:
    """Retourne le dernier micro swing low confirme."""
    ohlcv = _normalize_candles(candles)
    if len(ohlcv) < 2 * n + 1:
        return None
    for i in range(len(ohlcv) - n - 1, n - 1, -1):
        low = ohlcv[i]["low"]
        if all(low < ohlcv[i - k]["low"] for k in range(1, n + 1)) and all(
            low < ohlcv[i + k]["low"] for k in range(1, n + 1)
        ):
            return low
    return None


def _price_buffer_from_points(points: float = SL_BUFFER_POINTS) -> float:
    return max(float(points), 0.0) * 0.01


def _calculate_level_payload(
    direction: str,
    entry: float,
    atr: float,
    *,
    sweep_price: float | None = None,
) -> dict | None:
    """Calcule les niveaux officiels 1R/2R; fallback ATR uniquement sans sweep."""
    buffer = _price_buffer_from_points()
    basis = "SWEEP_STRUCTURE"
    if direction == "LONG":
        if sweep_price is not None:
            sl = float(sweep_price) - buffer
        else:
            basis = "SL_FALLBACK_ATR"
            sl = float(entry) - max(float(atr or 0.0), 0.0)
        risk = float(entry) - sl
        if risk <= 0:
            return None
        tp1 = float(entry) + risk * TP1_RR
        tp2 = float(entry) + risk * TP2_RR
    elif direction == "SHORT":
        if sweep_price is not None:
            sl = float(sweep_price) + buffer
        else:
            basis = "SL_FALLBACK_ATR"
            sl = float(entry) + max(float(atr or 0.0), 0.0)
        risk = sl - float(entry)
        if risk <= 0:
            return None
        tp1 = float(entry) - risk * TP1_RR
        tp2 = float(entry) - risk * TP2_RR
    else:
        return None
    return {
        "entry": float(entry),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "risk_points": risk,
        "sl_basis": basis,
        "tp1_rr": TP1_RR,
        "tp2_rr": TP2_RR,
        "be_plus_rr": BE_PLUS_RR,
        "sweep_price": sweep_price,
        "choch_price": float(entry),
    }


def _calculate_levels(direction: str, entry: float, atr: float, a4_data: dict | None = None) -> tuple[float, float, float, float]:
    """Compat: retourne entry, SL, TP1 et TP2 aux RR officiels."""
    a4_data = a4_data or {}
    levels = _calculate_level_payload(direction, entry, atr, sweep_price=a4_data.get("sweep_price"))
    if levels is None:
        raise ValueError("INVALID_AGENT5_LEVELS")
    return levels["entry"], levels["sl"], levels["tp1"], levels["tp2"]


def _reject(agent_id: str, reason: str, direction: str | None = None, payload: dict | None = None) -> AgentResult:
    """Construit un rejet standard AgentResult."""
    return AgentResult(
        agent_id=agent_id,
        score=0,
        hard_filter_pass=False,
        direction=direction,
        reason=reason,
        payload=payload or {},
    )


def _detect_accumulation(candles_1m: list[dict], poi_zone: dict, atr_1m: float) -> Optional[dict]:
    """Detecte une accumulation recente dans le POI."""
    if len(candles_1m) < AMD_ACCUMULATION_WINDOW:
        return None

    recent = candles_1m[-AMD_ACCUMULATION_WINDOW:]
    acc_high = max(c["high"] for c in recent)
    acc_low = min(c["low"] for c in recent)
    acc_range = acc_high - acc_low
    range_ok = acc_range <= 2.0 * atr_1m
    zone_ok = all(_price_in_zone(c["close"], poi_zone) for c in recent)
    if not range_ok or not zone_ok:
        return None
    return {
        "high": acc_high,
        "low": acc_low,
        "range": acc_range,
        "start_index": max(0, len(candles_1m) - AMD_ACCUMULATION_WINDOW),
    }


def detect_amd_sequence(
    candles_1m: Sequence,
    direction: str,
    poi_zone: dict,
    atr_14: float,
    amd_state: AMDState,
) -> tuple[AMDState, dict]:
    """Avance la sequence AMD stateful: accumulation, sweep, puis CHoCH post-sweep."""
    candles = _normalize_candles(candles_1m)
    if not candles or direction not in {"LONG", "SHORT"}:
        return amd_state, {"phase": amd_state.phase.value, "choch_detected": False, "sweep_detected": False, "amd_complete": False}

    atr = max(float(atr_14 or 0.0), 0.01)
    current_index = len(candles) - 1
    current = candles[-1]
    min_sweep_depth = AMD_SWEEP_ATR_MIN * atr

    if amd_state.phase == AMDPhase.IDLE:
        accum = _detect_accumulation(candles, poi_zone, atr)
        if accum:
            amd_state.phase = AMDPhase.ACCUMULATION_DETECTED
            amd_state.accumulation_high = accum["high"]
            amd_state.accumulation_low = accum["low"]
            amd_state.accumulation_start_index = accum["start_index"]

    if amd_state.phase == AMDPhase.ACCUMULATION_DETECTED:
        if direction == "LONG":
            sweep_depth = amd_state.accumulation_low - current["low"]
            is_sweep = current["low"] < amd_state.accumulation_low and current["close"] > amd_state.accumulation_low and sweep_depth >= min_sweep_depth
        else:
            sweep_depth = current["high"] - amd_state.accumulation_high
            is_sweep = current["high"] > amd_state.accumulation_high and current["close"] < amd_state.accumulation_high and sweep_depth >= min_sweep_depth

        if is_sweep:
            history_before_sweep = candles[:current_index]
            amd_state.phase = AMDPhase.MANIPULATION_DETECTED
            amd_state.sweep_price = current["low"] if direction == "LONG" else current["high"]
            amd_state.sweep_index = current_index
            amd_state.last_swing_high_1m = _find_last_swing_high(history_before_sweep) or amd_state.accumulation_high
            amd_state.last_swing_low_1m = _find_last_swing_low(history_before_sweep) or amd_state.accumulation_low

    if amd_state.phase == AMDPhase.MANIPULATION_DETECTED:
        candles_after_sweep = current_index - amd_state.sweep_index
        body = abs(current["close"] - current["open"])
        delay_ok = 0 < candles_after_sweep <= AMD_MAX_CHOCH_DELAY

        if candles_after_sweep > AMD_MAX_CHOCH_DELAY:
            amd_state = AMDState()
        elif delay_ok:
            if direction == "LONG":
                choch_confirmed = current["close"] > amd_state.last_swing_high_1m and body >= AMD_CHOCH_BODY_MIN * atr
            else:
                choch_confirmed = current["close"] < amd_state.last_swing_low_1m and body >= AMD_CHOCH_BODY_MIN * atr

            if choch_confirmed:
                amd_state.phase = AMDPhase.DISTRIBUTION_CONFIRMED
                amd_state.choch_index = current_index

    sweep_detected = amd_state.sweep_index >= 0 and amd_state.phase in {
        AMDPhase.MANIPULATION_DETECTED,
        AMDPhase.DISTRIBUTION_CONFIRMED,
    }
    return amd_state, {
        "phase": amd_state.phase.value,
        "choch_detected": sweep_detected and amd_state.phase == AMDPhase.DISTRIBUTION_CONFIRMED,
        "sweep_detected": sweep_detected,
        "amd_complete": sweep_detected and amd_state.phase == AMDPhase.DISTRIBUTION_CONFIRMED,
        "sweep_index": amd_state.sweep_index,
        "choch_index": amd_state.choch_index,
        "candles_since_sweep": current_index - amd_state.sweep_index if amd_state.sweep_index >= 0 else 0,
    }


def analyze_amd_sequence(
    candles_1m: Sequence,
    direction: str | None,
    poi_zone: dict | None,
    atr_1m: float,
    in_ote: bool = False,
    a4_data: dict | None = None,
) -> AgentResult:
    """Analyse complete AMD en mode batch pour validation et orchestration."""
    agent_id = "agent_5"
    candles = _normalize_candles(candles_1m)
    atr = max(float(atr_1m or 0.0), 0.01)

    legacy_hard_filter_pass = False
    legacy_reason = ""
    legacy_score = 0.0
    payload = {}

    current_price = candles[-1]["close"] if candles else 0.0
    price_in_poi = _price_in_zone(current_price, poi_zone) or in_ote
    
    noise_risk = "LOW"
    if candles and len(candles) > 3:
        avg_range = sum((c["high"] - c["low"]) for c in candles[-3:]) / 3
        if avg_range > 1.5 * atr:
            noise_risk = "HIGH"
        elif avg_range > 1.0 * atr:
            noise_risk = "MEDIUM"

    trigger_kind = "NONE"
    displacement_ratio = 0.0
    compression_detected = False
    retest_detected = False
    sweep_detected = False
    choch_detected = False
    sweep_price = 0.0
    choch_price = 0.0

    score_shadow = 0

    if len(candles) < AMD_ACCUMULATION_WINDOW + AMD_MAX_CHOCH_DELAY:
        legacy_reason = "NOT_ENOUGH_1M_CANDLES"
    elif direction not in {"LONG", "SHORT"}:
        legacy_reason = "NO_DIRECTION_FROM_AGENT_1"
    else:
        acc_window = candles[-AMD_ACCUMULATION_WINDOW - AMD_MAX_CHOCH_DELAY : -AMD_MAX_CHOCH_DELAY]
        acc_high = max(c["high"] for c in acc_window)
        acc_low = min(c["low"] for c in acc_window)
        acc_range = acc_high - acc_low
        
        if acc_range <= 2.0 * atr:
            compression_detected = True

        recent_start = max(0, len(candles) - 10)
        recent = candles[recent_start:]
        sweep_idx = None

        for offset, candle in enumerate(recent):
            if direction == "LONG":
                sweep_depth = acc_low - candle["low"]
                is_sweep = candle["low"] < acc_low and candle["close"] > acc_low and sweep_depth >= AMD_SWEEP_ATR_MIN * atr
            else:
                sweep_depth = candle["high"] - acc_high
                is_sweep = candle["high"] > acc_high and candle["close"] < acc_high and sweep_depth >= AMD_SWEEP_ATR_MIN * atr
            
            if is_sweep:
                sweep_detected = True
                sweep_idx = recent_start + offset
                break

        sweep_price = 0.0
        choch_price = 0.0

        if sweep_detected and sweep_idx is not None:
            sweep_candle = candles[sweep_idx]
            sweep_price = float(sweep_candle["low"]) if direction == "LONG" else float(sweep_candle["high"])
            last_swing_high = _find_last_swing_high(candles[:sweep_idx]) or acc_high
            last_swing_low = _find_last_swing_low(candles[:sweep_idx]) or acc_low
            post_sweep = candles[sweep_idx + 1 : sweep_idx + AMD_MAX_CHOCH_DELAY + 1]

            for offset, candle in enumerate(post_sweep, start=sweep_idx + 1):
                body = abs(candle["close"] - candle["open"])
                if direction == "LONG" and candle["close"] > last_swing_high and body >= AMD_CHOCH_BODY_MIN * atr:
                    choch_detected = True
                    trigger_kind = "MICRO_CHOCH"
                    choch_price = float(candle["close"])
                    break
                if direction == "SHORT" and candle["close"] < last_swing_low and body >= AMD_CHOCH_BODY_MIN * atr:
                    choch_detected = True
                    trigger_kind = "MICRO_CHOCH"
                    choch_price = float(candle["close"])
                    break

        if choch_detected:
            displacement_ratio = body / atr if atr > 0 else 0
            quality_bonus = min(displacement_ratio / 0.5, 1.0) * 10
            legacy_score = min(85 + quality_bonus, 100)
            legacy_reason = f"AMD_COMPLET - Accumulation -> Sweep -> CHoCH DISP={displacement_ratio:.2f}"
            if price_in_poi:
                legacy_hard_filter_pass = True
            else:
                legacy_reason = "PRICE_OUTSIDE_POI_OTE - CHoCH ignore"
        elif sweep_detected:
            legacy_reason = "CHoCH_SANS_SWEEP - risque de fausse cassure"
            legacy_score = 30
        elif not compression_detected:
            legacy_reason = f"NO_ACCUMULATION - range={acc_range:.2f} > 2xATR"
        else:
            legacy_reason = "NO_TRIGGER"

        if trigger_kind != "NONE" or sweep_detected or choch_detected:
            if price_in_poi: score_shadow += 25
            if choch_detected: score_shadow += 20
            if displacement_ratio >= 1.0: score_shadow += 15
            if compression_detected: score_shadow += 15
            if noise_risk == "LOW": score_shadow += 10
            score_shadow += 5

            if choch_detected and len(candles) > sweep_idx + 2:
                retest_detected = True
                score_shadow += 10

    human_trigger_confidence = "LOW"
    if score_shadow >= 75: human_trigger_confidence = "HIGH"
    elif score_shadow >= 50: human_trigger_confidence = "MEDIUM"

    payload = {
        "amd_complete": choch_detected,
        "sweep_1m_confirmed": sweep_detected,
        "choch_detected": choch_detected,
        "sweep_price": sweep_price,
        "choch_price": choch_price,
        "displacement_ratio": displacement_ratio,
        "shadow_ict_contract": {
            "agent_id": "agent_5",
            "observations": [f"Legacy Reason: {legacy_reason}", f"Shadow Score: {score_shadow}"],
            "score": score_shadow,
            "confidence": human_trigger_confidence,
            "hard_veto": legacy_hard_filter_pass is False,
            "reason": legacy_reason,
            "uncertainty": noise_risk,
            "alternative_scenario": "NONE",
            "contextual_notes": {
                "trigger_kind": trigger_kind,
                "micro_shift_strength": displacement_ratio,
                "compression_detected": compression_detected,
                "retest_detected": retest_detected,
                "trigger_inside_poi": price_in_poi,
                "trigger_timing_valid": True,
                "noise_risk": noise_risk
            },
            "diagnostic_present": True,
            "not_applicable_reason": "NO_DIRECTION" if not direction else ""
        }
    }

    return AgentResult(
        agent_id=agent_id,
        score=legacy_score,
        reason=legacy_reason,
        direction=direction,
        hard_filter_pass=legacy_hard_filter_pass,
        payload=payload
    )


def _micro_readiness_from_agent5_result(
    result: AgentResult,
    poi_zone: dict[str, Any] | None,
    candles_1m: Sequence,
) -> tuple[str, str]:
    payload = result.payload or {}
    if not poi_zone:
        return "UNAVAILABLE", "MICRO_POI_MISSING"
    if len(candles_1m or []) < AMD_ACCUMULATION_WINDOW + AMD_MAX_CHOCH_DELAY:
        return "UNAVAILABLE", "MICRO_INSUFFICIENT_1M_CANDLES"
    if payload.get("choch_detected") and payload.get("sweep_1m_confirmed") and result.hard_filter_pass:
        return "READY", "MICRO_READY"
    if payload.get("sweep_1m_confirmed") and not payload.get("choch_detected"):
        return "WAIT_FOR_TRIGGER", "MICRO_SWEEP_WAITING_CHOCH"
    if result.reason == "PRICE_OUTSIDE_POI_OTE - CHoCH ignore":
        return "REJECT", "MICRO_PRICE_OUTSIDE_POI"
    if result.reason == "NO_TRIGGER":
        return "WAIT_FOR_TRIGGER", "MICRO_NO_TRIGGER_YET"
    if result.reason == "NO_DIRECTION_FROM_AGENT_1":
        return "UNAVAILABLE", "MICRO_NO_DIRECTION"
    return "WAIT_FOR_TRIGGER", str(result.reason or "MICRO_WAITING")


async def run_agent_5(
    ohlcv_1m: Sequence,
    blackboard: BlackBoard = BLACKBOARD,
    *,
    diagnose: bool = False,
    replay_candle: dict[str, Any] | None = None,
) -> AgentResult:
    """Execution ponctuelle Agent 5 conforme au Script 03."""
    a1_data = blackboard.get_agent("agent_1")
    a2_data = blackboard.get_agent("agent_2")
    a4_data = blackboard.get_agent("agent_4")
    market_data = blackboard.get_market()
    
    agent1_result = blackboard.read_sync("agent_results.agent_1")
    a1_direction = a1_data.get("direction")
    if not a1_direction and agent1_result and hasattr(agent1_result, "direction"):
        a1_direction = agent1_result.direction

    # ── Regime filter: mean-reversion (liquidity_sweep_reversal) dies fading
    # strong trends (Feb: 0/5 straight-to-SL). Block entries in the configured
    # regimes by disabling the direction → the AMD analysis yields no trigger.
    # Default OFF → zero regression.
    try:
        from config import REGIME_FILTER_ENABLED, REGIME_BLOCKED_SET
        if REGIME_FILTER_ENABLED and a1_direction:
            _regime = str(
                a1_data.get("primary_regime")
                or (a1_data.get("notes") or {}).get("primary_regime")
                or ""
            ).upper()
            if _regime in REGIME_BLOCKED_SET:
                a1_direction = None
    except Exception:
        pass

    poi_zone, poi_handoff = _extract_agent2_p2a_poi_for_agent5(blackboard)

    # ── Étape M15: source the micro-trigger structure from EXECUTION_TF ──
    # The AMD/sweep detector sizes the stop from the candle stream + ATR it is
    # given (sweep_price = a swing low/high of that stream → 1R = entry−sweep).
    # Feeding it the EXECUTION_TF (e.g. 15m) stream + ATR makes 1R ~4× bigger,
    # and since tp1/tp2 = risk×RR, the targets scale with it → RR preserved.
    # Default EXECUTION_TF="1m" → keep the 1m stream passed in → ZERO regression.
    exec_atr_override: float | None = None
    try:
        from config import EXECUTION_TF, execution_ladder
        if EXECUTION_TF != "1m":
            _exec_tf = execution_ladder().get("exec", "1m")
            _exec_candles = list(blackboard.read_sync(f"market_data.candles.{_exec_tf}") or [])
            if len(_exec_candles) >= AMD_ACCUMULATION_WINDOW + AMD_MAX_CHOCH_DELAY:
                ohlcv_1m = _exec_candles  # the AMD detector is TF-agnostic
                _md_atr = market_data.get(f"atr_14_{_exec_tf}")
                exec_atr_override = float(_md_atr) if _md_atr else _atr_14_from_candles(_exec_candles)
    except Exception:
        exec_atr_override = None

    _atr_for_amd = exec_atr_override or market_data.get("atr_14_1m") or market_data.get("atr_14") or 1.0
    result = analyze_amd_sequence(
        ohlcv_1m,
        direction=a1_direction,
        poi_zone=poi_zone,
        atr_1m=_atr_for_amd,
        in_ote=bool(a4_data.get("in_ote", False)),
        a4_data=a4_data,
    )
    payload = dict(result.payload or {})
    atr_1m_val = float(exec_atr_override or market_data.get("atr_14_1m") or market_data.get("atr_14") or 1.0)
    poi_bottom, poi_top = _zone_bounds(poi_zone)
    readiness_state, readiness_reason = _micro_readiness_from_agent5_result(result, poi_zone, ohlcv_1m)
    normalized: list[dict] = []
    trigger_inside_poi = False
    if poi_zone and ohlcv_1m:
        normalized = _normalize_candles(ohlcv_1m)
        trigger_inside_poi = bool(normalized and _price_in_zone(normalized[-1]["close"], poi_zone))
    price_in_agent2_poi = trigger_inside_poi
    trigger_outside_poi = bool(poi_zone and ohlcv_1m) and not trigger_inside_poi
    candles_1m_count = len(ohlcv_1m) if ohlcv_1m else 0

    # ── P1.1 Kasper: RR computation ──────────────────────────────────
    rr_invalid_reason: str | None = None
    entry_price_candidate: float | None = None
    stop_loss_candidate: float | None = None
    target_liquidity: float | None = None
    tp1_candidate: float | None = None
    tp2_candidate: float | None = None
    rr_estimate: float | None = None
    risk_points_val: float | None = None

    if poi_zone and normalized and a1_direction in ("LONG", "SHORT"):
        atr = max(float(atr_1m_val or 0.0), 0.01)
        current_close = float(normalized[-1]["close"])
        sweep_price = float(payload.get("sweep_price") or 0.0)
        choch_price = float(payload.get("choch_price") or 0.0)

        # Entry: at POI edge aligned with direction
        if a1_direction == "LONG":
            entry_price_candidate = float(poi_bottom) if poi_bottom > 0 else current_close
            # SL: below sweep low with ATR buffer
            sl_ref = sweep_price if sweep_price > 0 else float(poi_bottom or current_close)
            stop_loss_candidate = round(sl_ref - atr * 0.5, 5)
            # Target: opposing liquidity (buyside = recent swing high or acc high)
            if normalized:
                target_liquidity = round(max(c["high"] for c in normalized[-20:]), 5)
            if target_liquidity and target_liquidity <= entry_price_candidate:
                target_liquidity = round(entry_price_candidate + atr * 3.0, 5)
        else:  # SHORT
            entry_price_candidate = float(poi_top) if poi_top > 0 else current_close
            # SL: above sweep high with ATR buffer
            sl_ref = sweep_price if sweep_price > 0 else float(poi_top or current_close)
            stop_loss_candidate = round(sl_ref + atr * 0.5, 5)
            # Target: opposing liquidity (sellside = recent swing low or acc low)
            if normalized:
                target_liquidity = round(min(c["low"] for c in normalized[-20:]), 5)
            if target_liquidity and target_liquidity >= entry_price_candidate:
                target_liquidity = round(entry_price_candidate - atr * 3.0, 5)

        # Validate prices and ensure SL is on the correct side
        if entry_price_candidate and stop_loss_candidate and entry_price_candidate > 0 and stop_loss_candidate > 0:
            # ── P2.1 fix: force SL to correct side of entry ─────────
            if a1_direction == "LONG" and stop_loss_candidate >= entry_price_candidate:
                stop_loss_candidate = round(entry_price_candidate - atr * 1.0, 5)
            elif a1_direction == "SHORT" and stop_loss_candidate <= entry_price_candidate:
                stop_loss_candidate = round(entry_price_candidate + atr * 1.0, 5)
            # ─────────────────────────────────────────────────────────
            risk_points_val = round(abs(entry_price_candidate - stop_loss_candidate), 5)
            # ── Étape M15: structural stop floor ──
            # The micro-sweep keeps 1R tight (~120 pts) on every TF. Force the
            # risk to at least STOP_ATR_FLOOR_MULT × ATR(exec) so 1R reflects the
            # higher-TF structure. tp1/tp2 below recompute from risk_points_val →
            # RR stays constant → grading gate (rr_estimate≥1.5) preserved.
            # Default mult 0.0 → no-op → zero regression.
            try:
                from config import STOP_ATR_FLOOR_MULT as _SL_FLOOR
            except Exception:
                _SL_FLOOR = 0.0
            if _SL_FLOOR and atr and risk_points_val < _SL_FLOOR * atr:
                risk_points_val = round(_SL_FLOOR * atr, 5)
                if a1_direction == "LONG":
                    stop_loss_candidate = round(entry_price_candidate - risk_points_val, 5)
                else:
                    stop_loss_candidate = round(entry_price_candidate + risk_points_val, 5)
            if risk_points_val > 0:
                # ── Cibles structurelles: TP1 = dernier swing pertinent au-delà
                # de l'entrée, TP2 = le swing suivant après TP1. Fallback aux
                # multiples de R si aucun swing exploitable. rr_estimate devient
                # structurel (target_liquidity=TP1) → sélectivité naturelle.
                _struct_done = False
                try:
                    from config import (STRUCT_TP, STRUCT_TP_MIN_DIST_ATR,
                                        STRUCT_TP_SEP_ATR, STRUCT_TP_LOOKBACK,
                                        STRUCT_TP_SWING_K)
                    if STRUCT_TP and normalized:
                        sh, slw = _swing_levels(normalized[-STRUCT_TP_LOOKBACK:], k=STRUCT_TP_SWING_K)
                        _min_d = STRUCT_TP_MIN_DIST_ATR * atr
                        _sep = STRUCT_TP_SEP_ATR * atr
                        if a1_direction == "LONG":
                            above = sorted(h for h in sh if h > entry_price_candidate + _min_d)
                            tp1_candidate = round(above[0], 5) if above else round(
                                entry_price_candidate + max(1.5 * risk_points_val, 2.0 * atr), 5)
                            beyond = [h for h in above if h > tp1_candidate + _sep]
                            tp2_candidate = round(beyond[0], 5) if beyond else round(
                                tp1_candidate + (tp1_candidate - entry_price_candidate), 5)
                        else:
                            below = sorted((l for l in slw if l < entry_price_candidate - _min_d), reverse=True)
                            tp1_candidate = round(below[0], 5) if below else round(
                                entry_price_candidate - max(1.5 * risk_points_val, 2.0 * atr), 5)
                            beyond = [l for l in below if l < tp1_candidate - _sep]
                            tp2_candidate = round(beyond[0], 5) if beyond else round(
                                tp1_candidate - (entry_price_candidate - tp1_candidate), 5)
                        target_liquidity = tp1_candidate
                        _struct_done = True
                except Exception:
                    _struct_done = False
                if not _struct_done:
                    tp1_candidate = round(entry_price_candidate + risk_points_val, 5) if a1_direction == "LONG" else round(entry_price_candidate - risk_points_val, 5)
                    tp2_candidate = round(entry_price_candidate + 2 * risk_points_val, 5) if a1_direction == "LONG" else round(entry_price_candidate - 2 * risk_points_val, 5)

                if target_liquidity and target_liquidity > 0:
                    reward_points = abs(target_liquidity - entry_price_candidate)
                    rr_estimate = round(reward_points / risk_points_val, 2) if risk_points_val > 0 else None
                else:
                    rr_estimate = 1.0  # default 1R when no structural target

                if a1_direction == "LONG" and stop_loss_candidate >= entry_price_candidate:
                    rr_invalid_reason = "SL must be below entry for BUY"
                    rr_estimate = None
                elif a1_direction == "SHORT" and stop_loss_candidate <= entry_price_candidate:
                    rr_invalid_reason = "SL must be above entry for SELL"
                    rr_estimate = None
            else:
                rr_invalid_reason = "Zero risk points — SL equals entry"
        else:
            rr_invalid_reason = "Cannot determine entry or stop loss from POI zone"
    else:
        if not poi_zone:
            rr_invalid_reason = "No POI zone available for RR computation"
        elif not normalized:
            rr_invalid_reason = "No 1M candles available for RR computation"
        elif a1_direction not in ("LONG", "SHORT"):
            rr_invalid_reason = "No direction from Agent1"

    # ── Store RR fields in payload ──────────────────────────────────
    payload["entry_price_candidate"] = entry_price_candidate
    payload["stop_loss_candidate"] = stop_loss_candidate
    payload["target_liquidity"] = target_liquidity
    payload["tp1_candidate"] = tp1_candidate
    payload["tp2_candidate"] = tp2_candidate
    payload["rr_estimate"] = rr_estimate
    payload["risk_points"] = risk_points_val
    payload["rr_invalid_reason"] = rr_invalid_reason
    # ── end P1.1 RR ─────────────────────────────────────────────────

    from gold_sniper.agents.poi_contract import consumed_poi_snapshot

    payload["agent5_poi_handoff"] = poi_handoff
    payload["agent5_consumed_poi"] = consumed_poi_snapshot(poi_zone)
    payload["execution_readiness"] = readiness_state
    payload["readiness_state"] = readiness_state
    payload["readiness_reason"] = readiness_reason
    payload["micro_handoff_status"] = "P2A_POI_CONSUMED" if poi_zone else "P2A_POI_MISSING"
    payload["trigger_type"] = "MICRO_CHOCH" if payload.get("choch_detected") else "NONE"
    payload["displacement_present"] = bool(payload.get("choch_detected"))
    payload["reclaim_confirmed"] = bool(payload.get("sweep_1m_confirmed"))
    payload["trigger_inside_poi"] = trigger_inside_poi
    payload["price_in_agent2_poi"] = price_in_agent2_poi
    payload["trigger_outside_poi"] = trigger_outside_poi
    payload["candles_1m_count"] = candles_1m_count
    result = AgentResult(
        agent_id=result.agent_id,
        score=result.score,
        hard_filter_pass=result.hard_filter_pass,
        direction=result.direction,
        reason=result.reason,
        payload=payload,
        veto=result.veto,
        risk_modifier=result.risk_modifier,
    )
    if diagnose:
        payload = dict(result.payload or {})
        payload["diagnostic"] = build_replay_agent_5_diagnostic(
            candle=replay_candle,
            blackboard=blackboard,
            candles_1m=ohlcv_1m,
            result=result,
        )
        result = AgentResult(
            agent_id=result.agent_id,
            score=result.score,
            hard_filter_pass=result.hard_filter_pass,
            direction=result.direction,
            reason=result.reason,
            payload=payload,
            veto=result.veto,
            risk_modifier=result.risk_modifier,
        )
    await _publish_agent_5_result(blackboard, result)
    payload = result.payload or {}
    if payload.get("sweep_1m_confirmed") or payload.get("choch_detected"):
        current_time_unix = int(_time.time())
        _publish_visual_layers_agent5(
            choch_confirmed=bool(payload.get("choch_detected", False)),
            choch_price=float(payload.get("choch_price") or payload.get("entry") or 0.0),
            choch_time_unix=_candle_time_unix(ohlcv_1m, payload.get("choch_index"), current_time_unix, 60),
            direction=result.direction,
            sweep_price=float(payload.get("sweep_price") or 0.0),
            sweep_time_unix=_candle_time_unix(ohlcv_1m, payload.get("sweep_index"), current_time_unix, 60),
            amd_phase=int(payload.get("amd_phase", 0) or 0),
        )
    else:
        VISUAL_LAYERS.clear_agent("agent_5")
    return result


async def _publish_agent_5_result(blackboard: BlackBoard, result: AgentResult) -> None:
    """Publie le resultat Agent 5 dans les slots Blackboard attendus."""
    payload = result.payload or {}
    await blackboard.update_agent(
        "agent_5",
        {
            "score": result.score,
            "direction": result.direction,
            "choch_detected": bool(payload.get("choch_detected", result.score >= 85)),
            "choch_price": payload.get("choch_price"),
            "price_in_poi": result.reason != "PRICE_OUTSIDE_POI_OTE - CHoCH ignore",
            "sweep_1m_confirmed": bool(payload.get("sweep_1m_confirmed", False)),
            "amd_phase": payload.get("amd_phase", 0),
            "entry_price": payload.get("entry"),
            "sl_price": payload.get("sl"),
            "tp1_price": payload.get("tp1"),
            "tp2_price": payload.get("tp2"),
            "risk_points": payload.get("risk_points"),
            "sl_basis": payload.get("sl_basis"),
            "tp1_rr": payload.get("tp1_rr"),
            "tp2_rr": payload.get("tp2_rr"),
            "be_plus_rr": payload.get("be_plus_rr"),
            "reason": result.reason,
            "hard_filter_pass": result.hard_filter_pass,
            "execution_readiness": payload.get("execution_readiness"),
            "readiness_state": payload.get("readiness_state"),
            "readiness_reason": payload.get("readiness_reason"),
            "micro_handoff_status": payload.get("micro_handoff_status"),
            "agent5_poi_handoff": payload.get("agent5_poi_handoff"),
            "agent5_consumed_poi": payload.get("agent5_consumed_poi"),
            # P1.1 Kasper: RR fields
            "entry_price_candidate": payload.get("entry_price_candidate"),
            "stop_loss_candidate": payload.get("stop_loss_candidate"),
            "target_liquidity": payload.get("target_liquidity"),
            "tp1_candidate": payload.get("tp1_candidate"),
            "tp2_candidate": payload.get("tp2_candidate"),
            "rr_estimate": payload.get("rr_estimate"),
            "risk_points": payload.get("risk_points"),
            "rr_invalid_reason": payload.get("rr_invalid_reason"),
        },
    )
    await blackboard.publish_agent_dashboard("agent_5", result, min_interval_sec=0)


class AgentMicroscope:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_5"
        self.amd_state = AMDState()
        self.active = False
        self.current_poi = None

    async def run(self):
        """Demarre les boucles Agent 5."""
        self.logger.info("Agent 5 (Microscope V2 AMD) demarre")
        await asyncio.gather(
            self._wait_for_poi_activation(),
            self._tick_monitoring_loop(),
        )

    async def _wait_for_poi_activation(self):
        """Reveille l'Agent 5 quand le prix entre dans un POI."""
        while not self.bb.kill_event.is_set():
            try:
                await asyncio.wait_for(self.bb._events["price_in_poi"].wait(), timeout=1.0)
                self.bb._events["price_in_poi"].clear()
                await self.bb.update_dict(f"agents.{self.name}", {"state": "AWAKE"})

                poi_data = self.bb.read_sync("meta.active_poi")
                if poi_data:
                    self.current_poi = poi_data.get("zone", poi_data)
                    self.amd_state = AMDState()
                    self.active = True
            except asyncio.TimeoutError:
                pass
            except Exception as exc:
                self.logger.error(f"Erreur wait_for_poi_activation Agent 5: {exc}")
                await asyncio.sleep(2)

    async def _tick_monitoring_loop(self):
        """Surveille les bougies 1M et publie uniquement une AMD complete."""
        while not self.bb.kill_event.is_set():
            await asyncio.sleep(0.05)

            if not self.active or not self.current_poi:
                await self.bb.update_dict(f"agents.{self.name}", {"state": "SLEEPING"})
                await self.bb.publish_agent_dashboard(
                    "agent_5",
                    idle_result(
                        "agent_5",
                        reason="IDLE_SLEEPING",
                        payload={"phase": AMDPhase.IDLE.value},
                    ),
                    min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                    trigger_orchestrator=False,
                )
                VISUAL_LAYERS.clear_agent("agent_5")
                continue

            try:
                candles_1m = list(self.bb.read_sync("market_data.candles.1m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14") or self.bb.read_sync("market.atr_14_1m") or 1.0
                tick = self.bb.read_sync("market_data.current_tick")
                agent1_result = self.bb.read_sync("agent_results.agent_1")
                direction = agent1_result.direction if agent1_result else self.bb.get_agent("agent_1").get("direction")

                if not candles_1m or not tick or not direction:
                    await self.bb.publish_agent_dashboard(
                        "agent_5",
                        idle_result("agent_5", reason="WAITING_TICK_DATA"),
                        min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_5")
                    continue

                current_price = (float(tick["bid"]) + float(tick["ask"])) / 2
                if not _price_in_zone(current_price, self.current_poi):
                    self.active = False
                    await self.bb.update_dict(f"agents.{self.name}", {"state": "SLEEPING"})
                    await self.bb.publish_agent_dashboard(
                        "agent_5",
                        idle_result(
                            "agent_5",
                            reason="IDLE_PRICE_OUTSIDE_POI",
                            payload={"phase": AMDPhase.IDLE.value},
                        ),
                        min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_5")
                    continue

                self.amd_state, signal = detect_amd_sequence(candles_1m, direction, self.current_poi, atr_14, self.amd_state)
                await self.bb.update_dict(f"agents.{self.name}", {"state": f"ACTIVE ({self.amd_state.phase.value})"})

                if signal["choch_detected"]:
                    closes = [_candle_value(c, "close", 3) for c in candles_1m]
                    choch_displacement = abs(closes[-1] - closes[-2]) if len(closes) >= 2 else 0.0
                    result = score_agent_5(signal, current_price, choch_displacement, atr_14, direction)
                    await _publish_agent_5_result(self.bb, result)
                    current_time_unix = int(_time.time())
                    sweep_idx = signal.get("sweep_index")
                    choch_idx = signal.get("choch_index")
                    sweep_price = self.amd_state.sweep_price
                    if not sweep_price and sweep_idx is not None and 0 <= sweep_idx < len(candles_1m):
                        sweep_price = (
                            _candle_value(candles_1m[sweep_idx], "low", 2)
                            if direction == "LONG"
                            else _candle_value(candles_1m[sweep_idx], "high", 1)
                        )
                    choch_price = current_price
                    if choch_idx is not None and 0 <= choch_idx < len(candles_1m):
                        choch_price = _candle_value(candles_1m[choch_idx], "close", 3)
                    _publish_visual_layers_agent5(
                        choch_confirmed=bool(signal.get("choch_detected", False)),
                        choch_price=float(choch_price or 0.0),
                        choch_time_unix=_candle_time_unix(candles_1m, choch_idx, current_time_unix, 60),
                        direction=direction,
                        sweep_price=float(sweep_price or 0.0),
                        sweep_time_unix=_candle_time_unix(candles_1m, sweep_idx, current_time_unix, 60),
                        amd_phase=3 if signal.get("choch_detected") else 2,
                    )

                    if result.hard_filter_pass:
                        self.active = False
                        await self.bb.update_dict(f"agents.{self.name}", {"state": "SIGNAL_SENT"})
                else:
                    prev = self.bb.read_sync("agent_results.agent_5")
                    pulse_score = float(prev.score) if prev else 0.0
                    await self.bb.publish_agent_dashboard(
                        "agent_5",
                        idle_result(
                            "agent_5",
                            reason="ACTIVE_MONITORING",
                            score=pulse_score,
                            direction=direction,
                            payload={"phase": self.amd_state.phase.value},
                        ),
                        min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                        trigger_orchestrator=False,
                    )
                    current_time_unix = int(_time.time())
                    sweep_idx = signal.get("sweep_index")
                    if signal.get("sweep_detected") and sweep_idx is not None:
                        _publish_visual_layers_agent5(
                            choch_confirmed=False,
                            choch_price=0.0,
                            choch_time_unix=0,
                            direction=direction,
                            sweep_price=float(self.amd_state.sweep_price or 0.0),
                            sweep_time_unix=_candle_time_unix(candles_1m, sweep_idx, current_time_unix, 60),
                            amd_phase=2,
                        )
                    elif self.amd_state.phase == AMDPhase.IDLE:
                        VISUAL_LAYERS.clear_agent("agent_5")
            except Exception as exc:
                self.logger.error(f"Erreur _tick_monitoring_loop Agent 5: {exc}")
                VISUAL_LAYERS.clear_agent("agent_5")
                await asyncio.sleep(2)


def _p1_safe_dict(value):
    return value if isinstance(value, dict) else {}


def _p1_safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_agent_5_observation(result):
    from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource

    if result is None:
        return AgentObservation(
            agent_id="agent_5",
            source=EvidenceSource.MICRO_CONFIRMATION,
            passed=None,
            score=0.0,
            confidence=0.0,
            reason="UNKNOWN",
            payload={
                "schema_version": "p1.agent_observation.v1",
                "agent_id": "agent_5",
                "status": "UNKNOWN",
                "displacement_present": False,
                "reclaim_confirmed": False,
                "retest_confirmed": False,
                "trigger_inside_poi": False,
                "execution_readiness": "UNAVAILABLE",
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "MICRO_AGENT5_RESULT_MISSING",
                "micro_handoff_status": "P2A_POI_MISSING",
                "unknown_fields": ["agent_result"],
            },
            missing_evidence=["AGENT_5_RESULT_MISSING"],
        )

    payload = _p1_safe_dict(result.payload)
    shadow = _p1_safe_dict(payload.get("shadow_trigger_context"))
    consumed_poi = _p1_safe_dict(payload.get("agent5_consumed_poi"))
    handoff = _p1_safe_dict(payload.get("agent5_poi_handoff"))

    displacement = bool(
        payload.get("displacement_present")
        or payload.get("has_displacement")
        or shadow.get("displacement_present")
    )
    reclaim = bool(payload.get("reclaim_confirmed") or shadow.get("reclaim_confirmed"))
    retest = bool(payload.get("retest_confirmed") or payload.get("has_retest") or shadow.get("retest_confirmed"))
    inside_poi = bool(payload.get("trigger_inside_poi") or payload.get("inside_poi") or shadow.get("trigger_inside_poi"))

    sweep = payload.get("sweep_1m_confirmed")
    choch = payload.get("choch_detected")

    missing = []
    if not displacement:
        missing.append("DISPLACEMENT_MISSING")
    if not retest:
        missing.append("RETEST_MISSING")
    if not inside_poi:
        missing.append("TRIGGER_INSIDE_POI_UNKNOWN_OR_FALSE")
    if not sweep:
        missing.append("SWEEP_1M_NOT_CONFIRMED")
    if not choch:
        missing.append("CHOCH_NOT_DETECTED")

    return AgentObservation(
        agent_id="agent_5",
        source=EvidenceSource.MICRO_CONFIRMATION,
        passed=bool(result.hard_filter_pass),
        score=_p1_safe_float(result.score),
        confidence=min(max(_p1_safe_float(result.score) / 100.0, 0.0), 1.0),
        reason=str(result.reason or "UNKNOWN"),
        hard_filter_pass=bool(result.hard_filter_pass),
        payload={
            "schema_version": "p1.agent_observation.v1",
            "agent_id": "agent_5",
            "status": "OK" if displacement and inside_poi else "PARTIAL",
            "trigger_type": payload.get("trigger_type") or payload.get("trigger_kind") or "UNKNOWN",
            "displacement_present": displacement,
            "reclaim_confirmed": reclaim,
            "retest_confirmed": retest,
            "trigger_inside_poi": inside_poi,
            "amd_phase": str(payload.get("amd_phase") or payload.get("phase") or "UNKNOWN"),
            "trigger_strength": _p1_safe_float(payload.get("trigger_strength") or result.score),
            "execution_readiness": payload.get("execution_readiness") or payload.get("readiness_state") or "UNAVAILABLE",
            "readiness_state": payload.get("readiness_state") or payload.get("execution_readiness") or "UNAVAILABLE",
            "readiness_reason": payload.get("readiness_reason") or "MICRO_READINESS_UNAVAILABLE",
            "micro_handoff_status": payload.get("micro_handoff_status"),
            "agent5_poi_handoff": handoff,
            "sweep_1m_confirmed": payload.get("sweep_1m_confirmed"),
            "choch_detected": payload.get("choch_detected"),
            "candles_1m_count": payload.get("candles_1m_count"),
            "price_in_agent2_poi": payload.get("price_in_agent2_poi", inside_poi),
            "trigger_outside_poi": payload.get("trigger_outside_poi"),
            "agent5_consumed_poi": consumed_poi,
            # P1.1 Kasper: RR fields
            "entry_price_candidate": payload.get("entry_price_candidate"),
            "stop_loss_candidate": payload.get("stop_loss_candidate"),
            "target_liquidity": payload.get("target_liquidity"),
            "tp1_candidate": payload.get("tp1_candidate"),
            "tp2_candidate": payload.get("tp2_candidate"),
            "rr_estimate": payload.get("rr_estimate"),
            "risk_points": payload.get("risk_points"),
            "rr_invalid_reason": payload.get("rr_invalid_reason"),
            "unknown_fields": missing,
        },
        missing_evidence=missing,
        warnings=[],
    )

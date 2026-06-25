import asyncio
import time as _time
from datetime import datetime
from typing import Any, Sequence

import numpy as np

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from core.visual_layers import VISUAL_LAYERS, VisualHLine, VisualMarker, VisualRectangle
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger


MIN_SWEEP_DEPTH_ATR = 0.05
EQ_TOLERANCE_ATR = 0.15


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


def _extract_agent2_p2a_liquidity_anchor(blackboard: BlackBoard) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """
    Extract P2-A POI anchor for Agent3 liquidity checks.

    Delegates to the centralized poi_contract module (Phase 7E).
    P2-A selected_poi is priority. Candidates are fallback. Legacy Agent2 fields
    are used only when P2-A is absent.
    """
    from gold_sniper.agents.poi_contract import extract_p2a_selected_poi

    anchor, diagnostics = extract_p2a_selected_poi(blackboard)
    if anchor:
        anchor["agent3_handoff_source"] = diagnostics["source"]
    return anchor, diagnostics


def _liquidity_readiness_from_result(result: AgentResult, liquidity_anchor: dict[str, Any] | None) -> str:
    if not liquidity_anchor:
        return "UNAVAILABLE"
    payload = result.payload or {}
    event = str(payload.get("event") or "UNKNOWN").upper()
    if event == "SWEEP" and result.hard_filter_pass:
        return "READY"
    if event in {"NONE", "APPROACH"}:
        return "WAIT_FOR_TRIGGER"
    if event == "BREAK":
        return "REJECT"
    return "WATCH_ONLY"


def _liquidity_readiness_reason_from_result(result: AgentResult, liquidity_anchor: dict[str, Any] | None) -> str:
    if not liquidity_anchor:
        return "LIQUIDITY_POI_MISSING"
    payload = result.payload or {}
    event = str(payload.get("event") or "UNKNOWN").upper()
    if event == "SWEEP" and result.hard_filter_pass:
        return "LIQUIDITY_SWEEP_READY"
    if event in {"NONE", "APPROACH"}:
        return "LIQUIDITY_WAITING_SWEEP"
    if event == "BREAK":
        return "LIQUIDITY_BREAK_REJECT"
    return str(result.reason or "LIQUIDITY_WATCH_ONLY")


def _enrich_agent3_result_with_handoff(
    result: AgentResult,
    liquidity_anchor: dict[str, Any] | None,
    liquidity_handoff: dict[str, Any],
) -> AgentResult:
    from gold_sniper.agents.poi_contract import consumed_poi_snapshot

    payload = dict(result.payload or {})
    payload["agent3_poi_handoff"] = liquidity_handoff
    payload["agent3_consumed_poi"] = consumed_poi_snapshot(liquidity_anchor)
    payload["liquidity_handoff_status"] = "P2A_POI_CONSUMED" if liquidity_anchor else "P2A_POI_MISSING"
    payload["execution_readiness"] = _liquidity_readiness_from_result(result, liquidity_anchor)
    payload["readiness_state"] = payload["execution_readiness"]
    payload["readiness_reason"] = _liquidity_readiness_reason_from_result(result, liquidity_anchor)
    return AgentResult(
        agent_id=result.agent_id,
        score=result.score,
        hard_filter_pass=result.hard_filter_pass,
        direction=result.direction,
        reason=result.reason,
        payload=payload,
        veto=result.veto,
        risk_modifier=result.risk_modifier,
    )


def _raw_time_unix(raw_time) -> int | None:
    if hasattr(raw_time, "timestamp"):
        return int(raw_time.timestamp())
    if isinstance(raw_time, (int, float)):
        return int(raw_time)
    return None


def _candle_time_unix(
    candles: list,
    candle_index: int | None,
    current_time_unix: int,
    timeframe_seconds: int,
) -> int:
    if candle_index is not None and 0 <= candle_index < len(candles):
        raw_time = candles[candle_index].get("time") if isinstance(candles[candle_index], dict) else None
        parsed = _raw_time_unix(raw_time)
        if parsed is not None:
            return parsed
    age = len(candles) - 1 - int(candle_index or len(candles) - 1)
    return current_time_unix - max(age, 0) * timeframe_seconds


def _enrich_equal_level_times(levels: list, candles: list, current_time_unix: int) -> list:
    enriched = []
    for level in levels:
        item = dict(level)
        idx = item.get("idx_1", item.get("idx_2"))
        item.setdefault("time_start_unix", _candle_time_unix(candles, idx, current_time_unix, 900))
        enriched.append(item)
    return enriched


def _enrich_asian_range_time(asian_range: dict, candles_1m: list, current_time_unix: int) -> dict:
    if not asian_range or not asian_range.get("valid"):
        return asian_range

    session_times = []
    for candle in candles_1m:
        if not isinstance(candle, dict) or "time" not in candle:
            continue
        raw_time = candle["time"]
        if isinstance(raw_time, datetime):
            hour = raw_time.hour
        else:
            hour = datetime.fromtimestamp(raw_time).hour
        if hour >= 22 or hour < 7:
            parsed = _raw_time_unix(raw_time)
            if parsed is not None:
                session_times.append(parsed)

    enriched = dict(asian_range)
    if session_times:
        enriched.setdefault("time_start_unix", min(session_times))
        enriched.setdefault("time_end_unix", max(session_times))
    else:
        count = int(enriched.get("count", 0) or 0)
        enriched.setdefault("time_start_unix", current_time_unix - max(count, 1) * 60)
        enriched.setdefault("time_end_unix", current_time_unix)
    return enriched


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


def _publish_visual_layers_agent3(
    eqh_levels: list,
    eql_levels: list,
    sweep_events: list,
    asian_range: dict,
    current_time_unix: int,
) -> None:
    layers = []

    for eqh in eqh_levels:
        layers.append(
            VisualHLine(
                time_start=eqh.get("time_start_unix", current_time_unix - 7200),
                price=eqh["level"],
                color="rgba(250, 204, 21, 0.85)",
                style="dashed",
                width=1,
                label=f"BSL {eqh['level']:.2f}",
                label_side="right",
            )
        )

    for eql in eql_levels:
        layers.append(
            VisualHLine(
                time_start=eql.get("time_start_unix", current_time_unix - 7200),
                price=eql["level"],
                color="rgba(250, 204, 21, 0.85)",
                style="dashed",
                width=1,
                label=f"SSL {eql['level']:.2f}",
                label_side="right",
            )
        )

    for sweep in sweep_events:
        side = sweep.get("side", "BSL")
        is_bsl_sweep = side == "BSL"
        layers.append(
            VisualMarker(
                time=sweep["time_unix"],
                price=sweep["price"],
                color="rgba(59, 130, 246, 1.0)",
                shape="arrowDown" if is_bsl_sweep else "arrowUp",
                size=2,
                label=f"SWEEP {side}",
                position="aboveBar" if is_bsl_sweep else "belowBar",
            )
        )

    if asian_range and asian_range.get("valid"):
        layers.append(
            VisualRectangle(
                time_start=asian_range.get("time_start_unix", current_time_unix - 28800),
                time_end=asian_range.get("time_end_unix", current_time_unix),
                price_top=asian_range["high"],
                price_bottom=asian_range["low"],
                color="rgba(148, 163, 184, 0.05)",
                border_color="rgba(148, 163, 184, 0.40)",
                label=f"Asian Range +/-{asian_range['high'] - asian_range['low']:.1f}",
            )
        )

        mid = (asian_range["high"] + asian_range["low"]) / 2
        layers.append(
            VisualHLine(
                time_start=asian_range.get("time_start_unix", current_time_unix - 28800),
                price=mid,
                color="rgba(148, 163, 184, 0.60)",
                style="dotted",
                width=1,
                label="Asian Mid",
            )
        )

    VISUAL_LAYERS.set_layers("agent_3", layers)


def _to_ohlcv(open_: np.ndarray | None, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> np.ndarray:
    """Construit une matrice OHLC minimale."""
    if open_ is None:
        open_ = closes.copy()
    return np.column_stack([open_, highs, lows, closes])


def detect_equal_levels(
    swing_highs: list,
    swing_lows: list,
    highs: np.ndarray,
    lows: np.ndarray,
    atr_14: float,
    tolerance_k: float = EQ_TOLERANCE_ATR,
) -> dict:
    """Detecte les clusters EQH/EQL par tolerance ATR."""
    tolerance = tolerance_k * atr_14
    eqh_clusters = []
    eql_clusters = []

    for i, sh1 in enumerate(swing_highs):
        for sh2 in swing_highs[i + 1 :]:
            if abs(sh1["price"] - sh2["price"]) <= tolerance:
                level = max(float(sh1["price"]), float(sh2["price"]))
                eqh_clusters.append(
                    {
                        "level": level,
                        "bsl_zone_top": level + 0.1 * atr_14,
                        "bsl_zone_bottom": level,
                        "strength": abs(sh2["index"] - sh1["index"]),
                        "idx_1": sh1["index"],
                        "idx_2": sh2["index"],
                        "swept": False,
                        "broken": False,
                    }
                )

    for i, sl1 in enumerate(swing_lows):
        for sl2 in swing_lows[i + 1 :]:
            if abs(sl1["price"] - sl2["price"]) <= tolerance:
                level = min(float(sl1["price"]), float(sl2["price"]))
                eql_clusters.append(
                    {
                        "level": level,
                        "ssl_zone_bottom": level - 0.1 * atr_14,
                        "ssl_zone_top": level,
                        "strength": abs(sl2["index"] - sl1["index"]),
                        "idx_1": sl1["index"],
                        "idx_2": sl2["index"],
                        "swept": False,
                        "broken": False,
                    }
                )

    eqh_clusters.sort(key=lambda x: x["strength"], reverse=True)
    eql_clusters.sort(key=lambda x: x["strength"], reverse=True)
    return {"eqh": eqh_clusters[:5], "eql": eql_clusters[:5]}


def detect_eqh_eql(
    highs: Sequence[float],
    lows: Sequence[float],
    swing_highs_idx: list[int],
    swing_lows_idx: list[int],
    atr_14: float,
    tolerance_k: float = EQ_TOLERANCE_ATR,
) -> dict:
    """Interface conforme au rapport Script 05."""
    swing_highs = [{"index": idx, "price": float(highs[idx])} for idx in swing_highs_idx]
    swing_lows = [{"index": idx, "price": float(lows[idx])} for idx in swing_lows_idx]
    return detect_equal_levels(swing_highs, swing_lows, np.array(highs), np.array(lows), atr_14, tolerance_k)


def classify_liquidity_event(
    candle_high: float,
    candle_low: float,
    candle_close: float,
    eqh_level: float | None = None,
    eql_level: float | None = None,
    atr_14: float = 1.0,
) -> dict:
    """Classe l'evenement en SWEEP, BREAK, APPROACH ou NONE."""
    min_sweep_depth = MIN_SWEEP_DEPTH_ATR * max(float(atr_14 or 0.0), 0.0001)
    result = {
        "event": "NONE",
        "side": None,
        "sweep_depth": 0.0,
        "sweep_depth_ratio": 0.0,
        "tradeable": False,
        "direction": None,
    }

    if eqh_level:
        eqh = float(eqh_level)
        if candle_high > eqh and candle_close < eqh:
            sweep_depth = candle_high - eqh
            if sweep_depth >= min_sweep_depth:
                result.update(
                    {
                        "event": "SWEEP",
                        "side": "BSL",
                        "sweep_depth": sweep_depth,
                        "sweep_depth_ratio": sweep_depth / atr_14 if atr_14 > 0 else 0.0,
                        "tradeable": True,
                        "direction": "SHORT",
                        "level": eqh,
                    }
                )
        elif candle_close > eqh:
            result.update({"event": "BREAK", "side": "BSL", "tradeable": False, "direction": "LONG", "level": eqh})
        elif eqh - min_sweep_depth <= candle_high <= eqh:
            result.update({"event": "APPROACH", "side": "BSL", "level": eqh})

    if eql_level:
        eql = float(eql_level)
        if candle_low < eql and candle_close > eql:
            sweep_depth = eql - candle_low
            if sweep_depth >= min_sweep_depth:
                result.update(
                    {
                        "event": "SWEEP",
                        "side": "SSL",
                        "sweep_depth": sweep_depth,
                        "sweep_depth_ratio": sweep_depth / atr_14 if atr_14 > 0 else 0.0,
                        "tradeable": True,
                        "direction": "LONG",
                        "level": eql,
                    }
                )
        elif candle_close < eql:
            result.update({"event": "BREAK", "side": "SSL", "tradeable": False, "direction": "SHORT", "level": eql})
        elif eql <= candle_low <= eql + min_sweep_depth and result["event"] == "NONE":
            result.update({"event": "APPROACH", "side": "SSL", "level": eql})

    return result


def detect_liquidity_event(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    eqh_level: float,
    eql_level: float,
    atr_14: float,
    expected_direction: str,
) -> dict:
    """Cherche le dernier sweep ou break pertinent sur les 10 dernieres bougies."""
    length = len(closes)
    lookback = min(10, length)
    relevant_eqh = eqh_level if expected_direction == "SHORT" else None
    relevant_eql = eql_level if expected_direction == "LONG" else None

    for i in range(length - 1, length - lookback - 1, -1):
        event = classify_liquidity_event(
            float(highs[i]),
            float(lows[i]),
            float(closes[i]),
            eqh_level=relevant_eqh,
            eql_level=relevant_eql,
            atr_14=atr_14,
        )
        if event["event"] in {"SWEEP", "BREAK"}:
            event["candle_index"] = i
            event["age"] = length - 1 - i
            return event

    return {"event": "NONE", "detected": False, "tradeable": False, "direction": expected_direction}


def detect_sweep(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    eqh_level: float,
    eql_level: float,
    atr_14: float,
    direction: str,
) -> dict:
    """Compatibilite: retourne seulement les sweeps, jamais les breaks."""
    event = detect_liquidity_event(highs, lows, closes, eqh_level, eql_level, atr_14, direction)
    if event.get("event") != "SWEEP":
        return {"detected": False, "event": event.get("event", "NONE")}
    return {
        "detected": True,
        "event": "SWEEP",
        "type": f"SWEEP_{event['side']}",
        "level_swept": event.get("level"),
        "sweep_depth": event.get("sweep_depth", 0.0),
        "sweep_depth_ratio": event.get("sweep_depth_ratio", 0.0),
        "candle_index": event.get("candle_index"),
        "age": event.get("age", 0),
        "direction": event.get("direction"),
    }


def check_asian_range(candles_1m: list, atr_14: float) -> dict:
    """Detecte la range asiatique sur les bougies 1M."""
    asian_candles = []
    for candle in candles_1m:
        if "time" not in candle:
            continue
        raw_time = candle["time"]
        if isinstance(raw_time, datetime):
            hour = raw_time.hour
        else:
            hour = datetime.fromtimestamp(raw_time).hour
        if hour >= 22 or hour < 7:
            asian_candles.append(candle)

    if len(asian_candles) < 30:
        return {"valid": False, "reason": "NOT_ENOUGH_ASIAN_CANDLES"}

    asian_high = max(float(c["high"]) for c in asian_candles)
    asian_low = min(float(c["low"]) for c in asian_candles)
    asian_range = asian_high - asian_low
    range_valid = asian_range >= 0.3 * atr_14
    return {
        "valid": range_valid,
        "high": asian_high,
        "low": asian_low,
        "range": asian_range,
        "mid": (asian_high + asian_low) / 2,
        "count": len(asian_candles),
    }


def detect_inducement(
    ohlcv: np.ndarray,
    swing_lows_idx: list[int],
    major_swing_low: float | None,
    direction: str,
    atr_14: float,
    swing_highs_idx: list[int] | None = None,
    major_swing_high: float | None = None,
) -> dict:
    """Detecte l'IDM et si cet inducement a ete sweepe."""
    if len(ohlcv) == 0:
        return {"detected": False, "swept": False}

    if direction == "LONG":
        if major_swing_low is None:
            return {"detected": False, "swept": False}
        candidates = [(idx, float(ohlcv[idx][2])) for idx in swing_lows_idx if float(ohlcv[idx][2]) > major_swing_low]
        if not candidates:
            return {"detected": False, "swept": False}
        idm_idx, idm_level = max(candidates, key=lambda item: item[0])
        post_idm = ohlcv[idm_idx + 1 :]
        swept = any(float(candle[2]) < idm_level and float(candle[3]) > idm_level for candle in post_idm)
        return {"detected": True, "level": idm_level, "swept": swept, "idx": idm_idx, "type": "SSL_IDM"}

    if major_swing_high is None or not swing_highs_idx:
        return {"detected": False, "swept": False}
    candidates = [(idx, float(ohlcv[idx][1])) for idx in swing_highs_idx if float(ohlcv[idx][1]) < major_swing_high]
    if not candidates:
        return {"detected": False, "swept": False}
    idm_idx, idm_level = max(candidates, key=lambda item: item[0])
    post_idm = ohlcv[idm_idx + 1 :]
    swept = any(float(candle[1]) > idm_level and float(candle[3]) < idm_level for candle in post_idm)
    return {"detected": True, "level": idm_level, "swept": swept, "idx": idm_idx, "type": "BSL_IDM"}


def score_agent_3(liquidity_event: dict, asian_range: dict, direction: str, idm: dict | None = None) -> AgentResult:
    """Score Agent 3 en distinguant sweep tradeable et break invalidant."""
    event = liquidity_event.get("event", "NONE")
    idm = idm or {"detected": False, "swept": False}

    if event == "BREAK":
        return AgentResult(
            agent_id="agent_3",
            score=0,
            reason=f"BREAK_{liquidity_event.get('side')} - niveau casse, signal annule",
            direction=liquidity_event.get("direction"),
            hard_filter_pass=False,
            payload={
                "event": "BREAK",
                "side": liquidity_event.get("side"),
                "level": liquidity_event.get("level"),
                "asian_range": asian_range,
                "idm": idm,
                "shadow_ict_contract": {
                    "agent_id": "agent_3",
                    "observations": [f"Liquidity level broken: {liquidity_event.get('side')}"],
                    "score": 0, "confidence": 0.0, "hard_veto": True,
                    "reason": f"BREAK_{liquidity_event.get('side')}",
                    "uncertainty": "LOW",
                    "alternative_scenario": {"scenario": "NONE", "condition": "NONE"},
                    "contextual_notes": {
                        "event_type": "PURGE",
                        "acceptance_state": "BREAK_ACCEPTED",
                        "reentry_state": "NONE",
                        "displacement_score": liquidity_event.get("sweep_depth_ratio", 0.0)
                    },
                    "diagnostic_present": True,
                    "not_applicable_reason": ""
                }
            },
        )

    if event != "SWEEP" or not liquidity_event.get("tradeable"):
        return AgentResult(
            agent_id="agent_3",
            score=30,
            reason=f"NO_SWEEP_DETECTED event={event}",
            direction=direction,
            hard_filter_pass=True,
            payload={
                "event": event, "asian_range": asian_range, "idm": idm,
                "shadow_ict_contract": {
                    "agent_id": "agent_3",
                    "observations": [f"No tradeable sweep. event={event}"],
                    "score": 30, "confidence": 0.3, "hard_veto": False,
                    "reason": f"NO_SWEEP_DETECTED event={event}",
                    "uncertainty": "MEDIUM",
                    "alternative_scenario": {"scenario": "WAIT_FOR_PURGE", "condition": "PRICE_REACHES_EQUAL_LEVEL"},
                    "contextual_notes": {
                        "event_type": event if event != "NONE" else "NONE",
                        "acceptance_state": "NONE",
                        "reentry_state": "NONE",
                        "displacement_score": 0.0
                    },
                    "diagnostic_present": True,
                    "not_applicable_reason": "NO_SWEEP"
                }
            },
        )

    confirmed_direction = liquidity_event.get("direction")
    if confirmed_direction != direction:
        return AgentResult(
            agent_id="agent_3",
            score=20,
            reason=f"SWEEP_DIRECTION_CONFLICT {confirmed_direction} vs {direction}",
            direction=confirmed_direction,
            hard_filter_pass=False,
            payload={
                "event": "SWEEP", "asian_range": asian_range, "idm": idm,
                "shadow_ict_contract": {
                    "agent_id": "agent_3",
                    "observations": [f"Direction conflict: sweep={confirmed_direction} bias={direction}"],
                    "score": 20, "confidence": 0.2, "hard_veto": True,
                    "reason": f"SWEEP_DIRECTION_CONFLICT",
                    "uncertainty": "HIGH",
                    "alternative_scenario": {"scenario": "REVERT", "condition": "COUNTER_TREND_SWEEP"},
                    "contextual_notes": {
                        "event_type": "REVERT",
                        "acceptance_state": "CONFLICT",
                        "reentry_state": "NONE",
                        "displacement_score": liquidity_event.get("sweep_depth_ratio", 0.0)
                    },
                    "diagnostic_present": True,
                    "not_applicable_reason": ""
                }
            },
        )

    sweep_depth_ratio = liquidity_event.get("sweep_depth_ratio", 0.0)
    sweep_quality = min(sweep_depth_ratio / 0.3, 1.0) * 55
    sweep_age = liquidity_event.get("age", 99)
    freshness_bonus = 15 if sweep_age <= 3 else (8 if sweep_age <= 6 else 0)
    asian_bonus = 15 if asian_range.get("valid") else 0
    idm_bonus = 15 if idm.get("swept") else 0
    total = min(sweep_quality + freshness_bonus + asian_bonus + idm_bonus, 100)

    return AgentResult(
        agent_id="agent_3",
        score=round(total, 1),
        reason=f"SWEEP_{liquidity_event.get('side')}_CONFIRMED depth={sweep_depth_ratio:.2f} age={sweep_age}",
        direction=confirmed_direction,
        hard_filter_pass=True,
        payload={
            "event": "SWEEP",
            "sweep_type": f"SWEEP_{liquidity_event.get('side')}",
            "level_swept": liquidity_event.get("level"),
            "sweep_depth_ratio": sweep_depth_ratio,
            "sweep_age_candles": sweep_age,
            "asian_range": asian_range,
            "asian_range_valid": asian_range.get("valid", False),
            "idm": idm,
            "idm_detected": idm.get("detected", False),
            "idm_swept": idm.get("swept", False),
            "shadow_ict_contract": {
                "agent_id": "agent_3",
                "observations": [f"SWEEP_{liquidity_event.get('side')} confirmed depth={sweep_depth_ratio:.2f}"],
                "score": round(total, 1), "confidence": round(total / 100, 2), "hard_veto": False,
                "reason": f"SWEEP_{liquidity_event.get('side')}_CONFIRMED",
                "uncertainty": "LOW",
                "alternative_scenario": {"scenario": "NONE", "condition": "NONE"},
                "contextual_notes": {
                    "event_type": "PURGE",
                    "acceptance_state": "SWEEP_ACCEPTED" if sweep_age <= 3 else "SWEEP_AGED",
                    "reentry_state": "REENTRY_PENDING",
                    "displacement_score": sweep_depth_ratio
                },
                "diagnostic_present": True,
                "not_applicable_reason": ""
            }
        },
    )


class AgentLiquidite:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_3"

    async def run(self):
        """Boucle principale Agent 3."""
        self.logger.info("Agent 3 (Liquidite V2 Sweep vs Break) demarre")
        while not self.bb.kill_event.is_set():
            try:
                try:
                    await asyncio.wait_for(self.bb._events["new_candle_15m"].wait(), timeout=15.0)
                    self.bb._events["new_candle_15m"].clear()
                except asyncio.TimeoutError:
                    pass

                agent2_result = await self.bb.wait_for_agent("agent_2", timeout=2.0)
                agent1_result = self.bb.read_sync("agent_results.agent_1")
                liquidity_anchor, liquidity_handoff = _extract_agent2_p2a_liquidity_anchor(self.bb)
                if not agent1_result or not agent2_result:
                    result = AgentResult(
                        agent_id="agent_3",
                        score=30,
                        reason="WAITING_ON_AGENT2_RESULT",
                        direction=None,
                        hard_filter_pass=True,
                        payload={
                            "execution_readiness": "UNAVAILABLE",
                            "readiness_state": "UNAVAILABLE",
                            "readiness_reason": "LIQUIDITY_WAITING_AGENT2_RESULT",
                            "agent3_poi_handoff": liquidity_handoff,
                            "liquidity_handoff_status": "P2A_POI_MISSING",
                        },
                    )
                    await self.bb.publish_agent_dashboard(
                        "agent_3", result, min_interval_sec=0, trigger_orchestrator=False
                    )
                    VISUAL_LAYERS.clear_agent("agent_3")
                    await self.bb.update_dict(f"agents.{self.name}", {"equal_highs": [], "equal_lows": []})
                    continue

                if not liquidity_anchor:
                    result = AgentResult(
                        agent_id="agent_3",
                        score=30,
                        reason="WAITING_ON_AGENT2_POI",
                        direction=None,
                        hard_filter_pass=True,
                        payload={
                            "execution_readiness": "UNAVAILABLE",
                            "readiness_state": "UNAVAILABLE",
                            "readiness_reason": "LIQUIDITY_POI_MISSING",
                            "agent3_poi_handoff": liquidity_handoff,
                            "liquidity_handoff_status": "P2A_POI_MISSING",
                        },
                    )
                    await self.bb.publish_agent_dashboard(
                        "agent_3", result, min_interval_sec=0, trigger_orchestrator=False
                    )
                    VISUAL_LAYERS.clear_agent("agent_3")
                    await self.bb.update_dict(f"agents.{self.name}", {"equal_highs": [], "equal_lows": []})
                    continue

                direction = agent1_result.direction
                if not direction:
                    await self.bb.publish_agent_dashboard(
                        "agent_3",
                        idle_result("agent_3", reason="WAITING_NO_DIRECTION", score=30),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_3")
                    continue

                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                candles_1m = list(self.bb.read_sync("market_data.candles.1m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14") or _estimate_atr_14(candles_15m)
                if len(candles_15m) < 10 or not atr_14:
                    await self.bb.publish_agent_dashboard(
                        "agent_3",
                        idle_result("agent_3", reason="WAITING_INSUFFICIENT_DATA", score=30),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    VISUAL_LAYERS.clear_agent("agent_3")
                    await asyncio.sleep(2)
                    continue

                high = np.array([c["high"] for c in candles_15m], dtype=float)
                low = np.array([c["low"] for c in candles_15m], dtype=float)
                open_ = np.array([c["open"] for c in candles_15m], dtype=float)
                close = np.array([c["close"] for c in candles_15m], dtype=float)

                from agents.agent_1_meteo import detect_swings

                loop = asyncio.get_running_loop()
                swings = await loop.run_in_executor(
                    None,
                    lambda: detect_swings(high, low, close, n=3, atr_14=atr_14),
                )
                eq_levels = detect_equal_levels(swings["swing_highs"], swings["swing_lows"], high, low, atr_14)
                current_time_unix = int(_time.time())
                eq_levels = {
                    "eqh": _enrich_equal_level_times(eq_levels["eqh"], candles_15m, current_time_unix),
                    "eql": _enrich_equal_level_times(eq_levels["eql"], candles_15m, current_time_unix),
                }
                eqh_level = eq_levels["eqh"][0]["level"] if eq_levels["eqh"] else 0.0
                eql_level = eq_levels["eql"][0]["level"] if eq_levels["eql"] else 0.0

                event = detect_liquidity_event(high, low, close, eqh_level, eql_level, atr_14, direction)
                asian_range = check_asian_range(candles_1m, atr_14)
                asian_range = _enrich_asian_range_time(asian_range, candles_1m, current_time_unix)

                ohlcv = _to_ohlcv(open_, high, low, close)
                swing_lows_idx = [item["index"] for item in swings["swing_lows"]]
                swing_highs_idx = [item["index"] for item in swings["swing_highs"]]
                major_low = min((item["price"] for item in swings["swing_lows"]), default=None)
                major_high = max((item["price"] for item in swings["swing_highs"]), default=None)
                idm = detect_inducement(ohlcv, swing_lows_idx, major_low, direction, atr_14, swing_highs_idx, major_high)

                result = score_agent_3(event, asian_range, direction, idm)
                result = _enrich_agent3_result_with_handoff(result, liquidity_anchor, liquidity_handoff)
                payload = result.payload or {}

                await self.bb.update_agent(
                    self.name,
                    {
                        "score": result.score,
                        "direction": result.direction,
                        "eqh_levels": eq_levels["eqh"],
                        "eql_levels": eq_levels["eql"],
                        "sweep_detected": payload.get("event") == "SWEEP",
                        "break_detected": payload.get("event") == "BREAK",
                        "sweep_side": payload.get("sweep_type"),
                        "sweep_depth_ratio": payload.get("sweep_depth_ratio", 0.0),
                        "asian_range": asian_range,
                        "idm_detected": payload.get("idm_detected", False),
                        "idm_swept": payload.get("idm_swept", False),
                        "reason": result.reason,
                        "hard_filter_pass": result.hard_filter_pass,
                        "execution_readiness": payload.get("execution_readiness"),
                        "readiness_state": payload.get("readiness_state"),
                        "readiness_reason": payload.get("readiness_reason"),
                        "agent3_poi_handoff": payload.get("agent3_poi_handoff"),
                        "agent3_consumed_poi": payload.get("agent3_consumed_poi"),
                        "liquidity_handoff_status": payload.get("liquidity_handoff_status"),
                    },
                )
                sweep_events = []
                if event.get("event") == "SWEEP" and event.get("candle_index") is not None:
                    sweep_idx = int(event["candle_index"])
                    side = event.get("side", "BSL")
                    sweep_events.append(
                        {
                            "time_unix": _candle_time_unix(candles_15m, sweep_idx, current_time_unix, 900),
                            "price": float(high[sweep_idx] if side == "BSL" else low[sweep_idx]),
                            "side": side,
                            "depth_ratio": event.get("sweep_depth_ratio", 0.0),
                        }
                    )
                await self.bb.publish_agent_dashboard(
                    "agent_3", result, min_interval_sec=0
                )
                _publish_visual_layers_agent3(
                    eqh_levels=eq_levels["eqh"],
                    eql_levels=eq_levels["eql"],
                    sweep_events=sweep_events,
                    asian_range=asian_range,
                    current_time_unix=current_time_unix,
                )

            except Exception as exc:
                self.logger.error(f"Erreur dans Agent 3 (Liquidite V2): {exc}")
                from config import AGENT_DASHBOARD_PULSE_SEC

                await self.bb.publish_agent_dashboard(
                    "agent_3",
                    idle_result(
                        "agent_3",
                        reason=f"ERROR: {exc}",
                        score=30,
                        hard_filter_pass=False,
                    ),
                    min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                    trigger_orchestrator=False,
                )
                VISUAL_LAYERS.clear_agent("agent_3")
                await asyncio.sleep(5)


AgentLiquidity = AgentLiquidite


def _p1_safe_dict(value):    return value if isinstance(value, dict) else {}
def _p1_safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_agent_3_observation(result):
    from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource

    if result is None:
        return AgentObservation(
            agent_id="agent_3",
            source=EvidenceSource.LIQUIDITY,
            passed=None,
            score=0.0,
            confidence=0.0,
            reason="UNKNOWN",
            payload={
                "schema_version": "p1.agent_observation.v1",
                "agent_id": "agent_3",
                "status": "UNKNOWN",
                "sweep_detected": False,
                "sweep_rejected": False,
                "draw_on_liquidity": "UNKNOWN",
                "execution_readiness": "UNAVAILABLE",
                "readiness_state": "UNAVAILABLE",
                "readiness_reason": "AGENT_3_RESULT_MISSING",
                "unknown_fields": ["agent_result"],
            },
            missing_evidence=["AGENT_3_RESULT_MISSING"],
        )

    payload = _p1_safe_dict(result.payload)
    event = str(payload.get("event") or payload.get("liquidity_event") or "UNKNOWN").upper()
    sweep_detected = event == "SWEEP" or bool(payload.get("sweep_detected"))
    sweep_rejected = bool(payload.get("sweep_rejected") or payload.get("rejection_confirmed"))

    missing = []
    if event == "UNKNOWN":
        missing.append("LIQUIDITY_EVENT_UNKNOWN")

    return AgentObservation(
        agent_id="agent_3",
        source=EvidenceSource.LIQUIDITY,
        passed=bool(result.hard_filter_pass),
        score=_p1_safe_float(result.score),
        confidence=min(max(_p1_safe_float(result.score) / 100.0, 0.0), 1.0),
        reason=str(result.reason or "UNKNOWN"),
        hard_filter_pass=bool(result.hard_filter_pass),
        payload={
            "schema_version": "p1.agent_observation.v1",
            "agent_id": "agent_3",
            "status": "OK" if event != "UNKNOWN" else "UNKNOWN",
            "liquidity_state": event,
            "sweep_detected": sweep_detected,
            "sweep_rejected": sweep_rejected,
            "sweep_side": payload.get("sweep_type") or payload.get("sweep_side") or "UNKNOWN",
            "sweep_depth_ratio": _p1_safe_float(payload.get("sweep_depth_ratio")),
            "idm_detected": bool(payload.get("idm_detected", False)),
            "idm_swept": bool(payload.get("idm_swept", False)),
            "draw_on_liquidity": payload.get("draw_on_liquidity", "UNKNOWN"),
            "execution_readiness": payload.get("execution_readiness") or payload.get("readiness_state"),
            "readiness_state": payload.get("readiness_state") or payload.get("execution_readiness"),
            "readiness_reason": payload.get("readiness_reason"),
            "agent3_poi_handoff": payload.get("agent3_poi_handoff"),
            "agent3_consumed_poi": payload.get("agent3_consumed_poi"),
            "liquidity_handoff_status": payload.get("liquidity_handoff_status"),
            "unknown_fields": missing,
        },
        missing_evidence=missing,
        warnings=[],
    )

"""Offline evidence reconstruction for the Phase 7 historical replay."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gold_sniper.replay.offline_market_structure import (
    Candle,
    average_true_range,
    candles_until,
    infer_bias_from_swings,
    load_candles_from_csv,
    parse_time,
)
from gold_sniper.strategy.atr_risk_model import evaluate_atr_risk_plan
from gold_sniper.strategy.kasper_ict_scenario_engine import evaluate_kasper_ict_scenarios
from gold_sniper.strategy.market_structure_engine import evaluate_market_structure
from gold_sniper.strategy.ote_confluence_engine import evaluate_ote_confluence
from gold_sniper.strategy.poi_star_rating import evaluate_poi_star_rating
from gold_sniper.strategy.vwap_m1_engine import evaluate_vwap_m1_scalp
from gold_sniper.strategy.xauusd_killzone_model import evaluate_xauusd_killzone
from gold_sniper.strategy.xauusd_market_weather import evaluate_xauusd_market_weather


@dataclass
class OfflineEvidenceBuilder:
    candles_1m: list[Candle]
    candles_15m: list[Candle]
    candles_4h: list[Candle]

    @classmethod
    def from_data_root(cls, data_root: Path) -> "OfflineEvidenceBuilder":
        return cls(
            candles_1m=_load_first_csv(data_root / "1m"),
            candles_15m=_load_first_csv(data_root / "15m"),
            candles_4h=_load_first_csv(data_root / "4H"),
        )

    def build(self, row: dict[str, Any]) -> dict[str, Any]:
        timestamp = parse_time(row.get("time"))
        if timestamp is None:
            return _empty_evidence()
        past_4h = candles_until(self.candles_4h, timestamp, limit=80)
        past_15m = candles_until(self.candles_15m, timestamp, limit=240)
        past_1m = candles_until(self.candles_1m, timestamp, limit=180)
        agent1 = build_agent1(past_4h, past_15m)
        agent3 = build_agent3(past_15m, agent1)
        agent2 = build_agent2(past_15m, row, agent1, agent3)
        agent4 = build_agent4(past_15m, past_4h, row, agent1)
        setup_type = infer_setup_type(agent1, agent2, agent3, agent4)
        agent5 = build_agent5(past_1m, row, agent2, setup_type)
        rows_15m = [_candle_to_dict(item) for item in past_15m]
        rows_1m = [_candle_to_dict(item) for item in past_1m]
        killzone = evaluate_xauusd_killzone(row.get("time")).to_dict()
        weather = evaluate_xauusd_market_weather(rows_15m).to_dict()
        structure = evaluate_market_structure(rows_15m).to_dict()
        poi_rating = evaluate_poi_star_rating(agent2.get("poi") if isinstance(agent2.get("poi"), dict) else None, {**agent3, **killzone}).to_dict()
        ote_confluence = evaluate_ote_confluence(agent4.get("range_low"), agent4.get("range_high"), row.get("close"), _direction_from_bias(agent1.get("bias")), poi_confluence=poi_rating.get("stars", 0) >= 3).to_dict()
        vwap_scalp = evaluate_vwap_m1_scalp(rows_1m, ema_200_m15_bias=weather.get("ema_200_m15_bias", "NEUTRAL")).to_dict()
        atr = average_true_range(past_15m)
        risk_plan = evaluate_atr_risk_plan(row.get("close"), _direction_from_bias(agent1.get("bias")), atr, structural_stop=agent2.get("poi_low") if _direction_from_bias(agent1.get("bias")) == "LONG" else agent2.get("poi_high")).to_dict()
        scenario_context = {
            **killzone,
            **weather,
            **agent1,
            **agent2,
            **agent3,
            **agent4,
            **agent5,
            **structure,
            **ote_confluence,
            **vwap_scalp,
            "poi_rating": poi_rating,
            "poi_stars": poi_rating.get("stars"),
            "poi_grade": poi_rating.get("grade"),
            "risk_plan": risk_plan,
            "risk_valid": risk_plan.get("risk_valid"),
        }
        scenarios = evaluate_kasper_ict_scenarios(scenario_context)
        best = scenarios.get("best_scenario", {}) if isinstance(scenarios, dict) else {}
        return {
            "agents": {
                "agent_1": agent1,
                "agent_2": {**agent2, "poi_rating": poi_rating},
                "agent_3": agent3,
                "agent_4": {**agent4, "ote_confluence": ote_confluence},
                "agent_5": agent5,
            },
            "context": {
                "setup_type": setup_type,
                "scenario_type": best.get("scenario_type"),
                "direction": _direction_from_bias(agent1.get("bias")),
                "htf_context": agent1.get("bias"),
                "draw_on_liquidity": agent1.get("draw_on_liquidity"),
                "order_flow": agent1.get("order_flow"),
                "killzone": killzone,
                "session": killzone.get("session"),
                "session_allowed": killzone.get("session_allowed"),
                "session_quality": killzone.get("session_quality"),
                "market_weather": weather,
                "market_structure": structure,
                "poi_rating": poi_rating,
                "ote_confluence": ote_confluence,
                "vwap_scalp": vwap_scalp,
                "risk_plan": risk_plan,
                "kasper_scenarios": scenarios,
            },
            "evidence_flags": {
                "agent_1_quality": agent1.get("evidence_quality"),
                "agent_2_quality": agent2.get("evidence_quality"),
                "agent_3_quality": agent3.get("evidence_quality"),
                "agent_4_quality": agent4.get("evidence_quality"),
                "agent_5_quality": agent5.get("evidence_quality"),
            },
        }


def build_agent1(past_4h: list[Candle], past_15m: list[Candle]) -> dict[str, Any]:
    if len(past_4h) < 6:
        return {
            "htf_context_available": False,
            "htf_context": "UNKNOWN",
            "bias": "UNKNOWN",
            "dol_available": False,
            "draw_on_liquidity": "UNKNOWN",
            "order_flow": "UNKNOWN",
            "alternative_scenario": "H4 history insufficient",
            "evidence_quality": "MISSING",
        }
    structure = infer_bias_from_swings(past_4h)
    bias = structure["bias"]
    recent_high = max(item.high for item in past_4h[-12:])
    recent_low = min(item.low for item in past_4h[-12:])
    close = past_15m[-1].close if past_15m else past_4h[-1].close
    if bias == "BULLISH":
        dol = "BUY_SIDE"
        dol_price = recent_high
    elif bias == "BEARISH":
        dol = "SELL_SIDE"
        dol_price = recent_low
    elif bias == "RANGE":
        dol = "BOTH"
        dol_price = recent_high if abs(recent_high - close) <= abs(close - recent_low) else recent_low
    else:
        dol = "UNKNOWN"
        dol_price = None
    return {
        "htf_context_available": bias != "UNKNOWN",
        "htf_context": bias,
        "bias": bias,
        "htf_bias": bias,
        "dol_available": dol != "UNKNOWN",
        "draw_on_liquidity": dol,
        "dol": dol,
        "dol_price": dol_price,
        "liquidity_target_open": dol != "UNKNOWN",
        "order_flow": structure["order_flow"],
        "alternative_scenario": "Range continuation or sweep required" if bias == "RANGE" else None,
        "evidence_quality": "HIGH" if bias in {"BULLISH", "BEARISH"} else "MEDIUM" if bias == "RANGE" else "MISSING",
    }


def build_agent3(past_15m: list[Candle], agent1: dict[str, Any]) -> dict[str, Any]:
    if len(past_15m) < 12:
        return {"liquidity_story_available": False, "state": "NO_LIQUIDITY_STORY", "evidence_quality": "MISSING"}
    previous = past_15m[-13:-1]
    current = past_15m[-1]
    prev_high = max(item.high for item in previous)
    prev_low = min(item.low for item in previous)
    swept_buy = current.high > prev_high
    swept_sell = current.low < prev_low
    rejected_buy = swept_buy and current.close < prev_high
    rejected_sell = swept_sell and current.close > prev_low
    accepted_buy = swept_buy and current.close > prev_high
    accepted_sell = swept_sell and current.close < prev_low
    dol = agent1.get("draw_on_liquidity", "UNKNOWN")
    payload: dict[str, Any] = {
        "draw_on_liquidity": dol,
        "dol": dol,
        "dol_status": "OPEN" if agent1.get("dol_available") else "UNKNOWN",
        "liquidity_target_open": bool(agent1.get("dol_available")),
        "buy_side_liquidity": True,
        "sell_side_liquidity": True,
        "equal_highs": _near_equal_highs(previous),
        "equal_lows": _near_equal_lows(previous),
        "external_liquidity_taken": swept_buy or swept_sell,
        "internal_liquidity_taken": bool(_near_equal_highs(previous) or _near_equal_lows(previous)),
        "acceptance_after_sweep": accepted_buy or accepted_sell,
        "rejection_after_sweep": rejected_buy or rejected_sell,
        "swept_side": "BUY_SIDE" if swept_buy else "SELL_SIDE" if swept_sell else "NONE",
    }
    if rejected_buy or rejected_sell:
        payload.update({"liquidity_story_available": True, "state": "SWEEP_CONFIRMED", "sweep_detected": True, "sweep_rejected": True, "rejection_confirmed": True, "sweep_side": payload["swept_side"], "evidence_quality": "HIGH"})
    elif accepted_buy or accepted_sell:
        payload.update({"liquidity_story_available": True, "state": "BREAKOUT_ACCEPTANCE", "break_detected": True, "break_accepted": True, "breakout_acceptance": True, "evidence_quality": "HIGH"})
    elif agent1.get("dol_available"):
        payload.update({"liquidity_story_available": True, "state": "LIQUIDITY_RUN", "run_detected": True, "evidence_quality": "MEDIUM"})
    elif payload["internal_liquidity_taken"]:
        payload.update({"liquidity_story_available": True, "state": "CLEANUP", "internal_cleanup": True, "idm_swept": True, "evidence_quality": "LOW"})
    else:
        payload.update({"liquidity_story_available": False, "state": "NO_LIQUIDITY_STORY", "evidence_quality": "MISSING"})
    return payload


def build_agent2(past_15m: list[Candle], row: dict[str, Any], agent1: dict[str, Any], agent3: dict[str, Any]) -> dict[str, Any]:
    if len(past_15m) < 8:
        return {"poi_available": False, "poi_type": "NONE", "evidence_quality": "MISSING"}
    atr = average_true_range(past_15m) or 1.0
    current_close = _float_or_none(row.get("close")) or past_15m[-1].close
    poi = _find_recent_ob_or_fvg(past_15m, atr, current_close)
    if not poi:
        return {"poi_available": False, "poi_type": "NONE", "evidence_quality": "MISSING"}
    direction = poi["direction"]
    touch_count, close_inside_count = _poi_touch_stats(past_15m[poi["index"] + 1 :], poi["low"], poi["high"])
    mitigation = _mitigation_pct(current_close, poi["low"], poi["high"], direction)
    poi_payload = {
        "type": poi["type"],
        "poi_type": poi["type"],
        "direction": direction,
        "low": poi["low"],
        "high": poi["high"],
        "poi_low": poi["low"],
        "poi_high": poi["high"],
        "lifecycle": "FRESH" if touch_count == 0 else "WICK_TAGGED" if touch_count <= 1 else "PARTIALLY_MITIGATED",
        "touch_count": touch_count,
        "close_inside_count": close_inside_count,
        "mitigation_pct": mitigation,
        "distance_atr": abs(current_close - ((poi["low"] + poi["high"]) / 2.0)) / atr,
        "age_sessions": max((len(past_15m) - poi["index"]) / 32.0, 0.0),
        "displacement_score": poi["displacement_score"],
        "has_displacement": True,
        "has_fvg": poi.get("has_fvg", False),
        "imbalance_attached": poi.get("has_fvg", False),
        "linked_to_ob": poi["type"] in {"OB", "OB_FVG_STACK"},
        "liquidity_sweep": bool(agent3.get("sweep_detected") or agent3.get("liquidity_story_available")),
        "extreme_of_range": _is_extreme_of_range(past_15m, poi["low"], poi["high"], direction),
    }
    return {
        "poi_available": True,
        "poi": poi_payload,
        "poi_type": poi["type"],
        "poi_direction": "LONG" if direction == "LONG" else "SHORT",
        "poi_low": poi["low"],
        "poi_high": poi["high"],
        "freshness": poi_payload["lifecycle"],
        "touch_count": touch_count,
        "close_inside_count": close_inside_count,
        "mitigation_pct": mitigation,
        "distance_atr": poi_payload["distance_atr"],
        "age_sessions": poi_payload["age_sessions"],
        "evidence_quality": "HIGH" if poi_payload["has_fvg"] and agent1.get("dol_available") else "MEDIUM",
    }


def build_agent4(past_15m: list[Candle], past_4h: list[Candle], row: dict[str, Any], agent1: dict[str, Any]) -> dict[str, Any]:
    base = past_15m[-96:] if len(past_15m) >= 24 else past_4h[-24:]
    if len(base) < 8:
        return {"range_available": False, "premium_discount": "UNKNOWN", "ote": {}, "evidence_quality": "MISSING"}
    low = min(item.low for item in base)
    high = max(item.high for item in base)
    close = _float_or_none(row.get("close")) or base[-1].close
    if high <= low:
        return {"range_available": False, "premium_discount": "UNKNOWN", "ote": {}, "evidence_quality": "MISSING"}
    midpoint = low + (high - low) * 0.5
    zone = "PREMIUM" if close > midpoint else "DISCOUNT" if close < midpoint else "EQUILIBRIUM"
    direction = _direction_from_bias(agent1.get("bias"))
    if direction == "LONG":
        ote_low, ote_high = high - (high - low) * 0.79, high - (high - low) * 0.62
        aligned = zone == "DISCOUNT"
    elif direction == "SHORT":
        ote_low, ote_high = low + (high - low) * 0.62, low + (high - low) * 0.79
        aligned = zone == "PREMIUM"
    else:
        ote_low, ote_high = None, None
        aligned = False
    inside_ote = bool(ote_low is not None and ote_low <= close <= ote_high)
    ote = {
        "in_ote": inside_ote,
        "ote_aligned": inside_ote,
        "ote_conflict": direction in {"LONG", "SHORT"} and not aligned,
        "fibonacci_anchor_valid": True,
        "fib_anchor_valid": True,
        "dealing_range_valid": True,
        "premium_discount": zone,
        "dealing_range_position": zone,
    }
    return {
        "range_available": True,
        "range_low": low,
        "range_high": high,
        "current_zone": zone,
        "premium_discount": zone,
        "pd_zone": zone,
        "in_premium": zone == "PREMIUM",
        "in_discount": zone == "DISCOUNT",
        "ote_available": True,
        "ote_low": ote_low,
        "ote_high": ote_high,
        "inside_ote": inside_ote,
        "in_ote": inside_ote,
        "ote": ote,
        "direction_alignment": "ALIGNED" if aligned else "CONFLICT" if direction in {"LONG", "SHORT"} else "UNKNOWN",
        "evidence_quality": "HIGH" if inside_ote else "MEDIUM",
    }


def build_agent5(past_1m: list[Candle], row: dict[str, Any], agent2: dict[str, Any], setup_type: str) -> dict[str, Any]:
    if len(past_1m) < 8 or not agent2.get("poi_available"):
        return {"micro_available": False, "micro_trigger": False, "evidence_quality": "MISSING"}
    poi_low = agent2.get("poi_low")
    poi_high = agent2.get("poi_high")
    if poi_low is None or poi_high is None:
        return {"micro_available": False, "micro_trigger": False, "inside_poi": False, "evidence_quality": "MISSING"}
    current = past_1m[-1]
    previous = past_1m[-8:-1]
    prev_high = max(item.high for item in previous)
    prev_low = min(item.low for item in previous)
    direction = agent2.get("poi_direction", "UNKNOWN")
    inside = current.low <= float(poi_high) and current.high >= float(poi_low)
    sweep = current.low < prev_low if direction == "LONG" else current.high > prev_high if direction == "SHORT" else False
    displacement = current.range > (average_true_range(past_1m, period=14) or current.range) * 0.8 and current.body > current.range * 0.45
    reclaim = current.close > prev_low if direction == "LONG" else current.close < prev_high if direction == "SHORT" else False
    retest = any(float(poi_low) <= item.low <= float(poi_high) or float(poi_low) <= item.high <= float(poi_high) for item in previous[-4:])
    trigger = bool(inside and displacement and reclaim and retest and (sweep or setup_type != "REVERSAL"))
    return {
        "micro_available": True,
        "micro_trigger": trigger,
        "trigger_type": "SWEEP_RECLAIM_RETEST" if sweep else "BOS" if displacement else "UNKNOWN",
        "micro_direction": direction,
        "sweep": sweep,
        "micro_sweep_present": sweep,
        "displacement": displacement,
        "displacement_present": displacement,
        "displacement_score": 0.75 if displacement else 0.25,
        "reclaim": reclaim,
        "reclaim_confirmed": reclaim,
        "retest": retest,
        "retest_confirmed": retest,
        "choch": sweep and displacement,
        "bos": displacement and not sweep,
        "inside_poi": inside,
        "trigger_inside_poi": inside,
        "poi_context_valid": inside,
        "trigger_price": current.close,
        "evidence_quality": "HIGH" if trigger else "LOW",
    }


def infer_setup_type(agent1: dict[str, Any], agent2: dict[str, Any], agent3: dict[str, Any], agent4: dict[str, Any]) -> str:
    if not agent1.get("htf_context_available") or not agent2.get("poi_available"):
        return "OBSERVATION"
    if agent3.get("sweep_detected") and agent4.get("direction_alignment") == "ALIGNED":
        return "REVERSAL"
    if agent4.get("inside_ote") and agent4.get("direction_alignment") == "ALIGNED":
        return "OTE_CONFLUENCE"
    if agent1.get("bias") in {"BULLISH", "BEARISH"} and agent3.get("liquidity_story_available"):
        return "TREND_CONTINUATION"
    return "OBSERVATION"


def _candle_to_dict(candle: Candle) -> dict[str, Any]:
    return {
        "time": candle.raw_time,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "tick_volume": candle.tick_volume,
    }


def _find_recent_ob_or_fvg(candles: list[Candle], atr: float, current_close: float) -> dict[str, Any] | None:
    start = max(2, len(candles) - 80)
    candidates: list[dict[str, Any]] = []
    for idx in range(start, len(candles)):
        candle = candles[idx]
        prev = candles[idx - 1]
        displacement = candle.body / atr if atr else 0.0
        if displacement < 0.6:
            continue
        if candle.bullish and prev.bearish:
            fvg = idx >= 2 and candles[idx - 2].high < candle.low
            candidates.append({"type": "OB_FVG_STACK" if fvg else "OB", "direction": "LONG", "low": prev.low, "high": prev.high, "index": idx - 1, "has_fvg": fvg, "displacement_score": min(displacement, 1.0)})
        elif candle.bearish and prev.bullish:
            fvg = idx >= 2 and candles[idx - 2].low > candle.high
            candidates.append({"type": "OB_FVG_STACK" if fvg else "OB", "direction": "SHORT", "low": prev.low, "high": prev.high, "index": idx - 1, "has_fvg": fvg, "displacement_score": min(displacement, 1.0)})
    if not candidates:
        return None
    return min(candidates[-12:], key=lambda item: abs(current_close - ((item["low"] + item["high"]) / 2.0)))


def _poi_touch_stats(candles: list[Candle], low: float, high: float) -> tuple[int, int]:
    touches = 0
    closes = 0
    for candle in candles:
        intersects = candle.low <= high and candle.high >= low
        if intersects:
            touches += 1
        if low <= candle.close <= high:
            closes += 1
    return touches, closes


def _mitigation_pct(price: float, low: float, high: float, direction: str) -> float:
    width = max(high - low, 0.0001)
    if direction == "LONG":
        return max(0.0, min((high - price) / width, 1.0))
    return max(0.0, min((price - low) / width, 1.0))


def _is_extreme_of_range(candles: list[Candle], low: float, high: float, direction: str) -> bool:
    recent = candles[-96:] if len(candles) >= 8 else candles
    r_low = min(item.low for item in recent)
    r_high = max(item.high for item in recent)
    midpoint = r_low + (r_high - r_low) * 0.5
    return high <= midpoint if direction == "LONG" else low >= midpoint


def _near_equal_highs(candles: list[Candle]) -> bool:
    highs = sorted((item.high for item in candles), reverse=True)[:2]
    return len(highs) == 2 and abs(highs[0] - highs[1]) <= max(highs[0] * 0.0008, 0.5)


def _near_equal_lows(candles: list[Candle]) -> bool:
    lows = sorted(item.low for item in candles)[:2]
    return len(lows) == 2 and abs(lows[0] - lows[1]) <= max(lows[0] * 0.0008, 0.5)


def _load_first_csv(folder: Path) -> list[Candle]:
    if not folder.exists():
        return []
    files = sorted(folder.glob("*.csv"))
    return load_candles_from_csv(files[0]) if files else []


def _direction_from_bias(bias: Any) -> str:
    value = str(bias or "").upper()
    if value == "BULLISH":
        return "LONG"
    if value == "BEARISH":
        return "SHORT"
    return "UNKNOWN"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_evidence() -> dict[str, Any]:
    return {
        "agents": {},
        "context": {"setup_type": "UNKNOWN"},
        "evidence_flags": {
            "agent_1_quality": "MISSING",
            "agent_2_quality": "MISSING",
            "agent_3_quality": "MISSING",
            "agent_4_quality": "MISSING",
            "agent_5_quality": "MISSING",
        },
    }

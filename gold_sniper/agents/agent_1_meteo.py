import asyncio
import numpy as np

from config import AGENT_DASHBOARD_PULSE_SEC
from core.blackboard import BlackBoard
from agents.base_agent import AgentResult
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger

FRESHNESS_SCORE_MAP = {
    "historical_only": (65, True, "HTF_BIAS_{direction}_ESTABLISHED"),
    "recent_20_bars": (82, True, "MTF_ALIGNED_{direction}_BOS_RECENT"),
    "fresh_5_bars": (95, True, "MTF_ALIGNED_{direction}_BOS_FRESH"),
    "choch_live": (90, True, "CHOCH_CONFIRMED_{direction}_LIVE"),
    "no_structure": (0, False, "STRUCTURE_NEUTRAL_MTF"),
}


def detect_swings(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                  n: int, atr_14: float) -> dict:
    length = len(high)
    swing_highs = []
    swing_lows = []
    
    for i in range(n, length - n):
        body = abs(close[i] - (high[i] - (high[i] - low[i]) / 2))
        body_size = abs(close[i] - (high[i] + low[i]) / 2)
        quality_ratio = body_size / atr_14 if atr_14 > 0 else 0
        
        is_sh = all(high[i] > high[i - k] for k in range(1, n + 1)) and \
                all(high[i] > high[i + k] for k in range(1, n + 1))
        
        is_sl = all(low[i] < low[i - k] for k in range(1, n + 1)) and \
                all(low[i] < low[i + k] for k in range(1, n + 1))
        
        if is_sh:
            quality = "HIGH" if quality_ratio >= 0.6 else ("MID" if quality_ratio >= 0.4 else "LOW")
            if quality != "LOW":
                swing_highs.append({"index": i, "price": high[i], "quality": quality})
        
        if is_sl:
            quality = "HIGH" if quality_ratio >= 0.6 else ("MID" if quality_ratio >= 0.4 else "LOW")
            if quality != "LOW":
                swing_lows.append({"index": i, "price": low[i], "quality": quality})
    
    return {"swing_highs": swing_highs, "swing_lows": swing_lows}


def _fallback_structure_from_close(close: np.ndarray) -> dict:
    if len(close) < 20:
        return {"state": "NEUTRAL", "last_event": None}

    recent = close[-20:]
    total_move = float(recent[-1] - recent[0])
    recent_range = max(float(np.max(recent) - np.min(recent)), 0.0001)
    lower_break = float(recent[-1]) <= float(np.min(recent[:-1]))
    upper_break = float(recent[-1]) >= float(np.max(recent[:-1]))

    if lower_break and total_move < -0.35 * recent_range:
        return {
            "state": "BEARISH",
            "last_event": "BOS",
            "last_event_index": len(close) - 1,
            "reference_sh": float(np.max(recent[:-1])),
            "reference_sl": float(np.min(recent[:-1])),
            "bos_freshness": 0,
            "last_sh_quality": "MID",
        }
    if upper_break and total_move > 0.35 * recent_range:
        return {
            "state": "BULLISH",
            "last_event": "BOS",
            "last_event_index": len(close) - 1,
            "reference_sh": float(np.max(recent[:-1])),
            "reference_sl": float(np.min(recent[:-1])),
            "bos_freshness": 0,
            "last_sh_quality": "MID",
        }
    return {"state": "NEUTRAL", "last_event": None}


def classify_market_structure(swings: dict, close: np.ndarray) -> dict:
    if not swings["swing_highs"] or not swings["swing_lows"]:
        return _fallback_structure_from_close(close)

    state = "NEUTRAL"
    swing_highs = sorted(swings["swing_highs"], key=lambda item: item["index"])
    swing_lows = sorted(swings["swing_lows"], key=lambda item: item["index"])
    last_sh = swing_highs[-1]
    last_sl = swing_lows[-1]
    last_event = None
    last_event_index = 0

    for i in range(len(close)):
        prior_highs = [s for s in swing_highs if s["index"] < i]
        prior_lows = [s for s in swing_lows if s["index"] < i]
        if prior_highs:
            last_sh = prior_highs[-1]
        if prior_lows:
            last_sl = prior_lows[-1]

        if state in ("BULLISH", "NEUTRAL"):
            if close[i] > last_sh["price"]:
                if state == "BULLISH":
                    last_event = "BOS"
                else:
                    last_event = "CHoCH"
                state = "BULLISH"
                last_event_index = i

        if state in ("BEARISH", "NEUTRAL"):
            if close[i] < last_sl["price"]:
                if state == "BEARISH":
                    last_event = "BOS"
                else:
                    last_event = "CHoCH"
                state = "BEARISH"
                last_event_index = i
    if last_event is None and len(swing_highs) >= 2 and len(swing_lows) >= 2:
        prev_sh, curr_sh = swing_highs[-2], swing_highs[-1]
        prev_sl, curr_sl = swing_lows[-2], swing_lows[-1]
        if curr_sh["price"] < prev_sh["price"] and curr_sl["price"] < prev_sl["price"]:
            state = "BEARISH"
            last_event = "BOS"
            last_event_index = curr_sl["index"]
        elif curr_sh["price"] > prev_sh["price"] and curr_sl["price"] > prev_sl["price"]:
            state = "BULLISH"
            last_event = "BOS"
            last_event_index = curr_sh["index"]

    bos_freshness = len(close) - 1 - last_event_index

    return {
        "state": state,
        "last_event": last_event,
        "last_event_index": last_event_index,
        "reference_sh": last_sh["price"],
        "reference_sl": last_sl["price"],
        "bos_freshness": bos_freshness,
        "last_sh_quality": last_sh.get("quality", "MID"),
    }

def score_agent_1(structure_4h: dict, structure_15m: dict) -> AgentResult:
    dir_4h = structure_4h["state"]
    dir_15m = structure_15m["state"]
    
    # Shadow fields mapping
    htf_draw_on_liquidity = "SELL_SIDE" if dir_4h == "BEARISH" else "BUY_SIDE" if dir_4h == "BULLISH" else "NEUTRAL"
    institutional_order_flow = "BEARISH" if dir_15m == "BEARISH" else "BULLISH" if dir_15m == "BULLISH" else "NEUTRAL"
    
    if dir_4h == "NEUTRAL":
        score, hard_filter_pass, reason_template = FRESHNESS_SCORE_MAP["no_structure"]
        contract = {
            "agent_id": "agent_1",
            "observations": ["No 4H structure detected"],
            "score": score,
            "confidence": 0.0,
            "hard_veto": not hard_filter_pass,
            "reason": "STRUCTURE_NEUTRAL_MTF",
            "uncertainty": "HIGH",
            "alternative_scenario": {"scenario": "WAIT_FOR_BREAK", "condition": "PRICE_BREAKS_RANGE"},
            "contextual_notes": {
                "htf_draw_on_liquidity": "NEUTRAL",
                "institutional_order_flow": "NEUTRAL",
                "primary_regime": "RANGE",
                "alternative_scenario": "WAIT_FOR_BREAK",
                "uncertainty": "HIGH"
            },
            "diagnostic_present": True,
            "not_applicable_reason": "NO_TECHNICAL_OPPORTUNITY"
        }
        return AgentResult(
            agent_id="agent_1",
            score=score,
            reason=reason_template,
            direction=None,
            hard_filter_pass=hard_filter_pass,
            payload={"shadow_ict_contract": contract}
        )

    direction = "LONG" if dir_4h == "BULLISH" else "SHORT"

    if dir_15m == "NEUTRAL" or dir_4h != dir_15m:
        score, hard_filter_pass, reason_template = FRESHNESS_SCORE_MAP["historical_only"]
        return AgentResult(
            agent_id="agent_1",
            score=score,
            reason=reason_template.format(direction=direction),
            direction=direction,
            hard_filter_pass=hard_filter_pass,
            payload={
                "structure_4h": dir_4h,
                "structure_15m": dir_15m,
                "bars_since_bos_4h": structure_4h.get("bos_freshness"),
                "bars_since_bos_15m": structure_15m.get("bos_freshness"),
                "freshness": "historical_only",
                "last_event_4h": structure_4h.get("last_event"),
                "last_event_15m": structure_15m.get("last_event"),
                "mtf_alignment": "MISMATCH" if dir_15m != "NEUTRAL" else "15M_NEUTRAL",
                "shadow_ict_contract": {
                    "agent_id": "agent_1",
                    "observations": [f"4H is {dir_4h}", f"15M is {dir_15m}"],
                    "score": score,
                    "confidence": 0.3,
                    "hard_veto": not hard_filter_pass,
                    "reason": reason_template.format(direction=direction),
                    "uncertainty": "HIGH",
                    "alternative_scenario": {"scenario": "EXPECT_REVERSAL", "condition": "MTF_MISMATCH"},
                    "contextual_notes": {
                        "htf_draw_on_liquidity": htf_draw_on_liquidity,
                        "institutional_order_flow": institutional_order_flow,
                        "primary_regime": "WEAK_" + ("UP" if direction == "LONG" else "DOWN"),
                        "alternative_scenario": "EXPECT_REVERSAL",
                        "uncertainty": "HIGH"
                    },
                    "diagnostic_present": True,
                    "not_applicable_reason": ""
                }
            },
        )

    bars_since_bos = structure_15m.get("bos_freshness")
    event_15m = structure_15m.get("last_event")
    if event_15m is None or bars_since_bos is None:
        freshness = "no_structure"
    elif event_15m == "CHoCH" and bars_since_bos <= 20:
        freshness = "choch_live"
    elif bars_since_bos <= 5:
        freshness = "fresh_5_bars"
    elif bars_since_bos <= 20:
        freshness = "recent_20_bars"
    else:
        freshness = "historical_only"

    score, hard_filter_pass, reason_template = FRESHNESS_SCORE_MAP[freshness]
    reason = reason_template.format(direction=direction)
    
    return AgentResult(
        agent_id="agent_1",
        score=score,
        reason=reason,
        direction=direction,
        hard_filter_pass=hard_filter_pass,
        payload={
            "structure_4h": dir_4h,
            "structure_15m": dir_15m,
            "bars_since_bos_15m": bars_since_bos,
            "freshness": freshness,
            "last_event_4h": structure_4h.get("last_event"),
            "last_event_15m": structure_15m.get("last_event"),
            "shadow_ict_contract": {
                "agent_id": "agent_1",
                "observations": [f"4H {dir_4h} confirmed by 15M {dir_15m}", f"Event: {event_15m} {bars_since_bos} bars ago"],
                "score": score,
                "confidence": score / 100.0,
                "hard_veto": not hard_filter_pass,
                "reason": reason,
                "uncertainty": "LOW",
                "alternative_scenario": {"scenario": "NONE", "condition": "NONE"},
                "contextual_notes": {
                    "htf_draw_on_liquidity": htf_draw_on_liquidity,
                    "institutional_order_flow": institutional_order_flow,
                    "primary_regime": "STRONG_" + ("UP" if direction == "LONG" else "DOWN"),
                    "alternative_scenario": "NONE",
                    "uncertainty": "LOW"
                },
                "diagnostic_present": True,
                "not_applicable_reason": ""
            }
        }
    )


def _calculate_agent_1_structures(
    candles_4h: list,
    candles_15m: list,
    atr_14: float,
) -> tuple[dict, dict]:
    high_4h = np.array([c["high"] for c in candles_4h], dtype=float)
    low_4h = np.array([c["low"] for c in candles_4h], dtype=float)
    close_4h = np.array([c["close"] for c in candles_4h], dtype=float)

    high_15m = np.array([c["high"] for c in candles_15m], dtype=float)
    low_15m = np.array([c["low"] for c in candles_15m], dtype=float)
    close_15m = np.array([c["close"] for c in candles_15m], dtype=float)

    swings_4h = detect_swings(high_4h, low_4h, close_4h, n=5, atr_14=atr_14)
    swings_15m = detect_swings(high_15m, low_15m, close_15m, n=3, atr_14=atr_14)

    return (
        classify_market_structure(swings_4h, close_4h),
        classify_market_structure(swings_15m, close_15m),
    )


async def calculate_agent_1_result(
    candles_4h: list,
    candles_15m: list,
    atr_14: float,
) -> tuple[AgentResult, dict, dict]:
    loop = asyncio.get_running_loop()
    structures = await loop.run_in_executor(
        None,
        lambda: _calculate_agent_1_structures(candles_4h, candles_15m, atr_14),
    )
    if structures is None:
        raise RuntimeError("Agent 1 executor returned None")
    structure_4h, structure_15m = structures
    if structure_4h is None or structure_15m is None:
        raise RuntimeError("Agent 1 executor returned incomplete structures")
    return score_agent_1(structure_4h, structure_15m), structure_4h, structure_15m


class AgentMeteo:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_1"
    
    async def run(self):
        self.logger.info("▶️  Agent 1 (Météo V2) démarré")
        while not self.bb.kill_event.is_set():
            try:
                # Dans la V2 finale, on attendra self.bb._events["new_candle_15m"].wait()
                # Pour l'instant, s'il n'est pas encore émis par le builder, on met un timeout
                try:
                    await asyncio.wait_for(self.bb._events["new_candle_15m"].wait(), timeout=15.0)
                    self.bb._events["new_candle_15m"].clear()
                except asyncio.TimeoutError:
                    pass # Fallback sur polling si le builder n'est pas encore V2

                candles_4h = list(self.bb.read_sync("market_data.candles.4H") or [])
                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                market_candles = self.bb.get_market().get("candles", {})
                self.logger.info(
                    "AGENT1_DEBUG: 4H=%s | 15M=%s | market.4h=%s | market.15m=%s",
                    len(candles_4h),
                    len(candles_15m),
                    len(market_candles.get("4h", market_candles.get("4H", [])) or []),
                    len(market_candles.get("15m", []) or []),
                )
                
                if len(candles_4h) < 10 or len(candles_15m) < 10:
                    await self.bb.publish_agent_dashboard(
                        "agent_1",
                        idle_result("agent_1", reason="WAITING_INSUFFICIENT_CANDLES"),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    await asyncio.sleep(2)
                    continue
                
                # Fetch ATR or calculate roughly if missing
                atr_14 = self.bb.read_sync("market_data.atr_14")
                if not atr_14:
                    # Approximation de l'ATR sur les 14 dernières bougies 15m
                    tr_list = [c["high"] - c["low"] for c in candles_15m[-14:]]
                    atr_14 = sum(tr_list) / len(tr_list) if tr_list else 0.001
                
                result, structure_4h, structure_15m = await calculate_agent_1_result(
                    candles_4h,
                    candles_15m,
                    atr_14,
                )
                self.logger.info(
                    "AGENT1_DEBUG: executor_ok=True | 4H_state=%s event=%s | 15M_state=%s event=%s | bars_since_bos=%s | score=%s | result=%s",
                    structure_4h.get("state"),
                    structure_4h.get("last_event"),
                    structure_15m.get("state"),
                    structure_15m.get("last_event"),
                    result.payload.get("bars_since_bos_15m"),
                    result.score,
                    result.reason,
                )
                
                # Mettre à jour l'ancien format pour l'UI et Agent 5
                await self.bb.update_dict(f"agents.{self.name}", {
                    "bias": result.direction if result.direction else "NEUTRAL",
                    "market_phase": "EXPANSION" if result.score > 0 else "PULLBACK",
                })
                
                await self.bb.update_dict("market_analysis.market_structure", {
                    "trend_4h": structure_4h["state"],
                    "trend_15m": structure_15m["state"],
                    "overall_bias": result.direction if result.direction else "NEUTRAL"
                })
                
                await self.bb.publish_agent_dashboard(
                    "agent_1", result, min_interval_sec=0
                )

            except Exception as e:
                self.logger.error(f"❌ Erreur dans Agent 1 (Météo V2) : {e}")
                await self.bb.publish_agent_dashboard(
                    "agent_1",
                    idle_result("agent_1", reason=f"ERROR: {e}", hard_filter_pass=False),
                    min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                    trigger_orchestrator=False,
                )
                await asyncio.sleep(5)


def _p1_safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _p1_safe_dict(value):
    return value if isinstance(value, dict) else {}


def _p1_structure_matches_direction(structure: str, direction: str) -> bool:
    direction = str(direction or "UNKNOWN").upper()
    structure = str(structure or "UNKNOWN").upper()
    if direction == "LONG":
        return structure == "BULLISH"
    if direction == "SHORT":
        return structure == "BEARISH"
    return False


def _p1_status(result) -> str:
    if result is None:
        return "UNKNOWN"
    if getattr(result, "hard_filter_pass", None) is False:
        return "PARTIAL"
    if getattr(result, "score", 0.0) <= 0:
        return "UNKNOWN"
    return "OK"


def build_agent_1_observation(result):
    from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource

    if result is None:
        return AgentObservation(
            agent_id="agent_1",
            source=EvidenceSource.MARKET_STRUCTURE,
            passed=None,
            score=0.0,
            confidence=0.0,
            reason="UNKNOWN",
            hard_filter_pass=None,
            payload={
                "schema_version": "p1.agent_observation.v1",
                "agent_id": "agent_1",
                "status": "UNKNOWN",
                "htf_aligned": False,
                "directional_bias": "UNKNOWN",
                "primary_regime": "UNKNOWN",
                "draw_on_liquidity": "UNKNOWN",
                "institutional_order_flow": "UNKNOWN",
                "unknown_fields": ["agent_result"],
            },
            missing_evidence=["AGENT_1_RESULT_MISSING"],
            warnings=[],
        )

    payload = _p1_safe_dict(result.payload)
    contract = _p1_safe_dict(payload.get("shadow_ict_contract"))
    notes = _p1_safe_dict(contract.get("contextual_notes"))

    direction = result.direction or "UNKNOWN"
    structure_4h = payload.get("structure_4h", "UNKNOWN")
    structure_15m = payload.get("structure_15m", "UNKNOWN")
    htf_aligned = (
        _p1_structure_matches_direction(structure_4h, direction)
        and _p1_structure_matches_direction(structure_15m, direction)
    )

    missing = []
    if direction == "UNKNOWN" or direction is None:
        missing.append("HTF_DIRECTION_UNKNOWN")
    if structure_4h == "UNKNOWN":
        missing.append("STRUCTURE_4H_UNKNOWN")
    if structure_15m == "UNKNOWN":
        missing.append("STRUCTURE_15M_UNKNOWN")
    if direction != "UNKNOWN" and not htf_aligned:
        missing.append("HTF_MTF_ALIGNMENT_MISMATCH")

    return AgentObservation(
        agent_id="agent_1",
        source=EvidenceSource.MARKET_STRUCTURE,
        passed=bool(result.hard_filter_pass),
        score=_p1_safe_float(result.score),
        confidence=min(max(_p1_safe_float(result.score) / 100.0, 0.0), 1.0),
        reason=str(result.reason or "UNKNOWN"),
        hard_filter_pass=bool(result.hard_filter_pass),
        payload={
            "schema_version": "p1.agent_observation.v1",
            "agent_id": "agent_1",
            "status": _p1_status(result),
            "htf_aligned": htf_aligned,
            "directional_bias": direction,
            "structure_4h": structure_4h,
            "structure_15m": structure_15m,
            "primary_regime": notes.get("primary_regime", "UNKNOWN"),
            "draw_on_liquidity": notes.get("htf_draw_on_liquidity", "UNKNOWN"),
            "institutional_order_flow": notes.get("institutional_order_flow", "UNKNOWN"),
            "bars_since_bos_4h": payload.get("bars_since_bos_4h"),
            "bars_since_bos_15m": payload.get("bars_since_bos_15m"),
            "unknown_fields": missing,
        },
        missing_evidence=missing,
        warnings=[],
    )

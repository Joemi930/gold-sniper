import asyncio
import numpy as np

from config import AGENT_DASHBOARD_PULSE_SEC
from core.blackboard import BlackBoard
from agents.base_agent import AgentResult
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger

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
    
    if dir_4h == "NEUTRAL" or dir_15m == "NEUTRAL":
        return AgentResult(
            agent_id="agent_1", score=0,
            reason="STRUCTURE_NEUTRAL_MTF",
            direction=None, hard_filter_pass=True
        )
    
    if dir_4h != dir_15m:
        return AgentResult(
            agent_id="agent_1", score=0,
            reason=f"MTF_MISALIGNMENT_{dir_4h}_vs_{dir_15m}",
            direction=None, hard_filter_pass=True
        )
    
    direction = "LONG" if dir_4h == "BULLISH" else "SHORT"
    
    base_score = 75
    
    freshness_15m = structure_15m.get("bos_freshness", 99)
    if freshness_15m <= 5:
        freshness_bonus = 15
    elif freshness_15m <= 15:
        freshness_bonus = 8
    else:
        freshness_bonus = 0
    
    quality_bonus = 10 if structure_15m.get("last_sh_quality") == "HIGH" else 5
    choch_penalty = -5 if structure_15m.get("last_event") == "CHoCH" else 0
    
    final_score = min(base_score + freshness_bonus + quality_bonus + choch_penalty, 100)
    
    return AgentResult(
        agent_id="agent_1",
        score=final_score,
        reason=f"MTF_ALIGNED_{direction}_BOS_FRESH={freshness_15m}",
        direction=direction,
        hard_filter_pass=True,
        payload={
            "structure_4h": dir_4h,
            "structure_15m": dir_15m,
            "bos_freshness_15m": freshness_15m,
            "last_event_4h": structure_4h.get("last_event"),
            "last_event_15m": structure_15m.get("last_event"),
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
                    "AGENT1_DEBUG: executor_ok=True | 4H_state=%s event=%s | 15M_state=%s event=%s | result=%s",
                    structure_4h.get("state"),
                    structure_4h.get("last_event"),
                    structure_15m.get("state"),
                    structure_15m.get("last_event"),
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

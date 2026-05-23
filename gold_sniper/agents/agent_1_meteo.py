import asyncio
import numpy as np

from core.blackboard import BlackBoard
from core.agent_result import AgentResult
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

def classify_market_structure(swings: dict, close: np.ndarray) -> dict:
    if not swings["swing_highs"] or not swings["swing_lows"]:
        return {"state": "NEUTRAL", "last_event": None}
    
    state = "NEUTRAL"
    last_sh = swings["swing_highs"][-1]
    last_sl = swings["swing_lows"][-1]
    last_event = None
    last_event_index = 0
    
    for i in range(max(0, len(close) - 20), len(close)):
        if state in ("BULLISH", "NEUTRAL"):
            if close[i] > last_sh["price"]:
                if state == "BULLISH":
                    last_event = "BOS"
                else:
                    last_event = "CHoCH"
                state = "BULLISH"
                last_event_index = i
                new_sh = [s for s in swings["swing_highs"] if s["index"] <= i]
                if new_sh:
                    last_sh = new_sh[-1]
        
        if state in ("BEARISH", "NEUTRAL"):
            if close[i] < last_sl["price"]:
                if state == "BEARISH":
                    last_event = "BOS"
                else:
                    last_event = "CHoCH"
                state = "BEARISH"
                last_event_index = i
                new_sl = [s for s in swings["swing_lows"] if s["index"] <= i]
                if new_sl:
                    last_sl = new_sl[-1]
    
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
            direction=None, is_hard_filter=True
        )
    
    if dir_4h != dir_15m:
        return AgentResult(
            agent_id="agent_1", score=0,
            reason=f"MTF_MISALIGNMENT_{dir_4h}_vs_{dir_15m}",
            direction=None, is_hard_filter=True
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
        is_hard_filter=True,
        metadata={
            "structure_4h": dir_4h,
            "structure_15m": dir_15m,
            "bos_freshness_15m": freshness_15m,
            "last_event_4h": structure_4h.get("last_event"),
            "last_event_15m": structure_15m.get("last_event"),
        }
    )

class AgentMeteo:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_1_meteo"
    
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
                
                if len(candles_4h) < 10 or len(candles_15m) < 10:
                    await asyncio.sleep(2)
                    continue
                
                # Fetch ATR or calculate roughly if missing
                atr_14 = self.bb.read_sync("market_data.atr_14")
                if not atr_14:
                    # Approximation de l'ATR sur les 14 dernières bougies 15m
                    tr_list = [c["high"] - c["low"] for c in candles_15m[-14:]]
                    atr_14 = sum(tr_list) / len(tr_list) if tr_list else 0.001
                
                high_4h = np.array([c["high"] for c in candles_4h])
                low_4h = np.array([c["low"] for c in candles_4h])
                close_4h = np.array([c["close"] for c in candles_4h])
                
                high_15m = np.array([c["high"] for c in candles_15m])
                low_15m = np.array([c["low"] for c in candles_15m])
                close_15m = np.array([c["close"] for c in candles_15m])
                
                swings_4h = detect_swings(high_4h, low_4h, close_4h, n=5, atr_14=atr_14)
                swings_15m = detect_swings(high_15m, low_15m, close_15m, n=3, atr_14=atr_14)
                
                structure_4h = classify_market_structure(swings_4h, close_4h)
                structure_15m = classify_market_structure(swings_15m, close_15m)
                
                result = score_agent_1(structure_4h, structure_15m)
                
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
                
                await self.bb.write_agent_result("agent_1", result)

            except Exception as e:
                self.logger.error(f"❌ Erreur dans Agent 1 (Météo V2) : {e}")
                await asyncio.sleep(5)

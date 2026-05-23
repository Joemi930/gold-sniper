import asyncio
import numpy as np
from datetime import datetime

from core.blackboard import BlackBoard
from core.agent_result import AgentResult
from utils.logger import get_logger

def detect_equal_levels(swing_highs: list, swing_lows: list,
                        highs: np.ndarray, lows: np.ndarray,
                        atr_14: float, tolerance_k: float = 0.15) -> dict:
    tolerance = tolerance_k * atr_14
    
    eqh_clusters = []
    eql_clusters = []
    
    for i, sh1 in enumerate(swing_highs):
        for sh2 in swing_highs[i+1:]:
            if abs(sh1["price"] - sh2["price"]) <= tolerance:
                level = max(sh1["price"], sh2["price"])
                eqh_clusters.append({
                    "level": level,
                    "bsl_zone_top": level + 0.1 * atr_14,
                    "bsl_zone_bottom": level,
                    "strength": abs(sh2["index"] - sh1["index"]),
                    "idx_1": sh1["index"],
                    "idx_2": sh2["index"],
                })
    
    for i, sl1 in enumerate(swing_lows):
        for sl2 in swing_lows[i+1:]:
            if abs(sl1["price"] - sl2["price"]) <= tolerance:
                level = min(sl1["price"], sl2["price"])
                eql_clusters.append({
                    "level": level,
                    "ssl_zone_bottom": level - 0.1 * atr_14,
                    "ssl_zone_top": level,
                    "strength": abs(sl2["index"] - sl1["index"]),
                    "idx_1": sl1["index"],
                    "idx_2": sl2["index"],
                })
    
    eqh_clusters.sort(key=lambda x: x["strength"], reverse=True)
    eql_clusters.sort(key=lambda x: x["strength"], reverse=True)
    
    return {"eqh": eqh_clusters[:5], "eql": eql_clusters[:5]}

def detect_sweep(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 eqh_level: float, eql_level: float,
                 atr_14: float, direction: str) -> dict:
    min_sweep_depth = 0.05 * atr_14
    
    length = len(closes)
    lookback = min(10, length)
    
    for i in range(length - 1, length - lookback - 1, -1):
        if direction == "LONG" and eqh_level > 0:
            wick_above = highs[i] - eqh_level
            if highs[i] > eqh_level and closes[i] < eqh_level and wick_above >= min_sweep_depth:
                return {
                    "detected": True,
                    "type": "SWEEP_BSL",
                    "level_swept": eqh_level,
                    "sweep_depth": wick_above,
                    "sweep_depth_ratio": wick_above / atr_14,
                    "candle_index": i,
                    "age": length - 1 - i,
                }
        
        elif direction == "SHORT" and eql_level > 0:
            wick_below = eql_level - lows[i]
            if lows[i] < eql_level and closes[i] > eql_level and wick_below >= min_sweep_depth:
                return {
                    "detected": True,
                    "type": "SWEEP_SSL",
                    "level_swept": eql_level,
                    "sweep_depth": wick_below,
                    "sweep_depth_ratio": wick_below / atr_14,
                    "candle_index": i,
                    "age": length - 1 - i,
                }
    
    return {"detected": False}

def check_asian_range(candles_1m: list, atr_14: float) -> dict:
    # Requires candle time. We assume "hour_utc" can be derived from "time"
    asian_candles = []
    for c in candles_1m:
        if "time" in c:
            hour = c["time"].hour if isinstance(c["time"], datetime) else datetime.fromtimestamp(c["time"]).hour
            if hour >= 22 or hour < 7:
                asian_candles.append(c)
    
    if len(asian_candles) < 30:
        return {"valid": False}
    
    asian_high = max(c["high"] for c in asian_candles)
    asian_low = min(c["low"] for c in asian_candles)
    asian_range = asian_high - asian_low
    asian_mid = (asian_high + asian_low) / 2
    
    range_valid = asian_range >= 0.3 * atr_14
    
    return {
        "valid": range_valid,
        "high": asian_high,
        "low": asian_low,
        "range": asian_range,
        "mid": asian_mid,
    }

def score_agent_3(sweep_data: dict, asian_range: dict, direction: str) -> AgentResult:
    if not sweep_data.get("detected"):
        return AgentResult(
            agent_id="agent_3", score=30,
            reason="NO_SWEEP_DETECTED - signal non confirmé",
            direction=direction, is_hard_filter=False
        )
    
    sweep_depth_ratio = sweep_data.get("sweep_depth_ratio", 0)
    sweep_quality = min(sweep_depth_ratio / 0.3, 1.0) * 60
    
    sweep_age = sweep_data.get("age", 99)
    freshness_bonus = 20 if sweep_age <= 3 else (10 if sweep_age <= 6 else 0)
    
    asian_bonus = 20 if asian_range.get("valid") else 0
    
    total = min(sweep_quality + freshness_bonus + asian_bonus, 100)
    
    return AgentResult(
        agent_id="agent_3",
        score=round(total, 1),
        reason=f"SWEEP_{sweep_data['type']}_DEPTH={sweep_depth_ratio:.2f}_AGE={sweep_age}",
        direction=direction,
        is_hard_filter=False,
        metadata={
            "sweep_type": sweep_data.get("type"),
            "sweep_depth_ratio": sweep_depth_ratio,
            "sweep_age_candles": sweep_age,
            "asian_range_valid": asian_range.get("valid"),
        }
    )

class AgentLiquidite:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_3_liquidite"
    
    async def run(self):
        self.logger.info("▶️  Agent 3 (Liquidité V2) démarré")
        while not self.bb.kill_event.is_set():
            try:
                try:
                    await asyncio.wait_for(self.bb._events["new_candle_15m"].wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    pass
                
                # Attendre Agent 2 (qui attend Agent 1)
                agent2_result = await self.bb.wait_for_agent("agent_2", timeout=2.0)
                agent1_result = self.bb.read_sync("agent_results.agent_1")
                
                if not agent1_result or not agent2_result or agent2_result.score == 0:
                    await self.bb.write_agent_result("agent_3", AgentResult(
                        agent_id="agent_3", score=30,
                        reason="WAITING_ON_AGENT2_FAIL",
                        direction=None, is_hard_filter=False
                    ))
                    # Retrocompat UI
                    await self.bb.update_dict(f"agents.{self.name}", {"equal_highs": [], "equal_lows": []})
                    continue
                
                direction = agent1_result.direction
                if not direction:
                    continue
                
                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                candles_1m = list(self.bb.read_sync("market_data.candles.1m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14")
                
                if len(candles_15m) < 10 or not atr_14:
                    await asyncio.sleep(2)
                    continue
                
                high = np.array([c["high"] for c in candles_15m])
                low = np.array([c["low"] for c in candles_15m])
                close = np.array([c["close"] for c in candles_15m])
                
                from agents.agent_1_meteo import detect_swings
                swings = detect_swings(high, low, close, n=3, atr_14=atr_14)
                
                eq_levels = detect_equal_levels(swings["swing_highs"], swings["swing_lows"], high, low, atr_14)
                
                # Par défaut on check le meilleur EQH et le meilleur EQL
                eqh_level = eq_levels["eqh"][0]["level"] if eq_levels["eqh"] else 0.0
                eql_level = eq_levels["eql"][0]["level"] if eq_levels["eql"] else 0.0
                
                sweep = detect_sweep(high, low, close, eqh_level, eql_level, atr_14, direction)
                asian_range = check_asian_range(candles_1m, atr_14)
                
                result = score_agent_3(sweep, asian_range, direction)
                
                # Retrocompat UI
                await self.bb.update_dict(f"agents.{self.name}", {
                    "equal_highs": eq_levels["eqh"],
                    "equal_lows": eq_levels["eql"]
                })
                
                await self.bb.write_agent_result("agent_3", result)
                
            except Exception as e:
                self.logger.error(f"❌ Erreur dans Agent 3 (Liquidité V2) : {e}")
                await asyncio.sleep(5)

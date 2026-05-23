import asyncio
from enum import Enum
from dataclasses import dataclass
from typing import Optional

from core.blackboard import BlackBoard
from core.agent_result import AgentResult
from utils.logger import get_logger

class AMDPhase(Enum):
    IDLE = "IDLE"
    ACCUMULATION_DETECTED = "ACCUMULATION"
    MANIPULATION_DETECTED = "MANIPULATION"   # = Sweep validé
    DISTRIBUTION_CONFIRMED = "DISTRIBUTION"  # = CHoCH post-sweep

@dataclass
class AMDState:
    phase: AMDPhase = AMDPhase.IDLE
    accumulation_high: float = 0.0
    accumulation_low: float = 0.0
    accumulation_start_index: int = 0
    sweep_index: int = 0
    sweep_price: float = 0.0
    choch_index: int = 0
    last_swing_high_1m: float = 0.0
    last_swing_low_1m: float = 0.0

def detect_accumulation(candles_1m: list, poi_zone: dict, atr_14: float) -> Optional[dict]:
    N_MIN_ACCUMULATION = 5
    MAX_RANGE_RATIO = 0.5
    
    if len(candles_1m) < N_MIN_ACCUMULATION:
        return None
    
    recent = candles_1m[-15:]
    
    for start in range(len(recent) - N_MIN_ACCUMULATION + 1):
        window = recent[start:]
        window_high = max(c["high"] for c in window)
        window_low = min(c["low"] for c in window)
        window_range = window_high - window_low
        
        if window_range < MAX_RANGE_RATIO * atr_14:
            poi_bottom = poi_zone.get("entry_zone_bottom", poi_zone.get("bottom", 0))
            poi_top = poi_zone.get("entry_zone_top", poi_zone.get("top", float('inf')))
            
            all_in_poi = all(poi_bottom <= c["close"] <= poi_top for c in window)
            
            if all_in_poi:
                return {
                    "detected": True,
                    "high": window_high,
                    "low": window_low,
                    "range": window_range,
                    "start_index": start,
                }
    
    return None

def detect_amd_sequence(candles_1m: list, direction: str,
                        poi_zone: dict, atr_14: float,
                        amd_state: AMDState) -> tuple[AMDState, dict]:
    closes_1m = [c["close"] for c in candles_1m]
    highs_1m = [c["high"] for c in candles_1m]
    lows_1m = [c["low"] for c in candles_1m]
    
    current_close = closes_1m[-1]
    current_high = highs_1m[-1]
    current_low = lows_1m[-1]
    min_sweep_depth = 0.05 * atr_14
    
    if amd_state.phase == AMDPhase.IDLE:
        accum = detect_accumulation(candles_1m, poi_zone, atr_14)
        if accum:
            amd_state.phase = AMDPhase.ACCUMULATION_DETECTED
            amd_state.accumulation_high = accum["high"]
            amd_state.accumulation_low = accum["low"]
    
    if amd_state.phase == AMDPhase.ACCUMULATION_DETECTED:
        if direction == "LONG":
            wick_below = amd_state.accumulation_low - current_low
            is_sweep = (
                current_low < amd_state.accumulation_low and
                current_close > amd_state.accumulation_low and
                wick_below >= min_sweep_depth
            )
        else:
            wick_above = current_high - amd_state.accumulation_high
            is_sweep = (
                current_high > amd_state.accumulation_high and
                current_close < amd_state.accumulation_high and
                wick_above >= min_sweep_depth
            )
        
        if is_sweep:
            amd_state.phase = AMDPhase.MANIPULATION_DETECTED
            amd_state.sweep_price = current_low if direction == "LONG" else current_high
            amd_state.sweep_index = len(candles_1m) - 1
            
            amd_state.last_swing_high_1m = max(highs_1m[-5:]) if len(highs_1m) >= 5 else current_high
            amd_state.last_swing_low_1m = min(lows_1m[-5:]) if len(lows_1m) >= 5 else current_low
    
    if amd_state.phase == AMDPhase.MANIPULATION_DETECTED:
        if direction == "LONG":
            choch_confirmed = current_close > amd_state.last_swing_high_1m
        else:
            choch_confirmed = current_close < amd_state.last_swing_low_1m
        
        if choch_confirmed:
            amd_state.phase = AMDPhase.DISTRIBUTION_CONFIRMED
            amd_state.choch_index = len(candles_1m) - 1
    
    signal = {
        "phase": amd_state.phase.value,
        "choch_detected": amd_state.phase == AMDPhase.DISTRIBUTION_CONFIRMED,
        "sweep_detected": amd_state.phase in [AMDPhase.MANIPULATION_DETECTED, AMDPhase.DISTRIBUTION_CONFIRMED],
        "amd_complete": amd_state.phase == AMDPhase.DISTRIBUTION_CONFIRMED,
        "candles_since_sweep": len(candles_1m) - 1 - amd_state.sweep_index if amd_state.sweep_index > 0 else 0,
    }
    
    return amd_state, signal

def score_agent_5(signal: dict, current_close: float,
                  choch_displacement: float, atr_14: float,
                  direction: str) -> AgentResult:
    if not signal.get("choch_detected"):
        phase = signal.get("phase", "IDLE")
        return AgentResult(
            agent_id="agent_5", score=0,
            reason=f"NO_CHOCH_YET_PHASE={phase}",
            direction=direction, is_hard_filter=False
        )
    
    base_score = 60
    
    amd_bonus = 30 if signal.get("sweep_detected") else 0
    
    displacement_ratio = choch_displacement / atr_14 if atr_14 > 0 else 0
    quality_bonus = min(displacement_ratio / 0.5, 1.0) * 10
    
    total = min(base_score + amd_bonus + quality_bonus, 100)
    
    return AgentResult(
        agent_id="agent_5",
        score=round(total, 1),
        reason=f"CHOCH_CONFIRMED_AMD={signal.get('amd_complete')}_DISP={displacement_ratio:.2f}",
        direction=direction,
        is_hard_filter=False,
        metadata={
            "amd_complete": signal.get("amd_complete"),
            "sweep_detected": signal.get("sweep_detected"),
            "displacement_ratio": displacement_ratio,
            "phase": signal.get("phase"),
        }
    )

class AgentMicroscope:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_5_microscope"
        self.amd_state = AMDState()
        self.active = False
        self.current_poi = None
    
    async def run(self):
        self.logger.info("▶️  Agent 5 (Microscope V2) démarré")
        await asyncio.gather(
            self._wait_for_poi_activation(),
            self._tick_monitoring_loop(),
        )
    
    async def _wait_for_poi_activation(self):
        while not self.bb.kill_event.is_set():
            try:
                await asyncio.wait_for(self.bb._events["price_in_poi"].wait(), timeout=1.0)
                self.bb._events["price_in_poi"].clear()
                
                # Check if UI wants 'state'
                await self.bb.update_dict(f"agents.{self.name}", {"state": "AWAKE"})
                
                poi_data = self.bb.read_sync("meta.active_poi")
                if poi_data:
                    self.current_poi = poi_data["zone"]
                    self.amd_state = AMDState()
                    self.active = True
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                self.logger.error(f"❌ Erreur wait_for_poi_activation: {e}")
                await asyncio.sleep(2)
    
    async def _tick_monitoring_loop(self):
        while not self.bb.kill_event.is_set():
            await asyncio.sleep(0.05)
            
            if not self.active or not self.current_poi:
                await self.bb.update_dict(f"agents.{self.name}", {"state": "SLEEPING"})
                continue
            
            try:
                candles_1m = list(self.bb.read_sync("market_data.candles.1m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14")
                tick = self.bb.read_sync("market_data.current_tick")
                
                # direction from agent 1
                agent1_result = self.bb.read_sync("agent_results.agent_1")
                direction = agent1_result.direction if agent1_result else None
                
                if not candles_1m or not atr_14 or not tick or not direction:
                    continue
                
                current_price = (tick["bid"] + tick["ask"]) / 2
                poi_bottom = self.current_poi.get("bottom", 0)
                poi_top = self.current_poi.get("top", float("inf"))
                
                if not (poi_bottom <= current_price <= poi_top):
                    self.active = False
                    await self.bb.update_dict(f"agents.{self.name}", {"state": "SLEEPING"})
                    continue
                
                # We are active, let's update UI to show AMD phase
                await self.bb.update_dict(f"agents.{self.name}", {"state": f"ACTIVE ({self.amd_state.phase.value})"})
                
                self.amd_state, signal = detect_amd_sequence(
                    candles_1m, direction, self.current_poi, atr_14, self.amd_state
                )
                
                if signal["choch_detected"]:
                    closes = [c["close"] for c in candles_1m]
                    choch_displacement = abs(closes[-1] - closes[-2]) if len(closes) >= 2 else 0
                    
                    result = score_agent_5(signal, current_price, choch_displacement, atr_14, direction)
                    await self.bb.write_agent_result("agent_5", result)
                    
                    if result.score >= 60:
                        self.active = False
                        await self.bb.update_dict(f"agents.{self.name}", {"state": "SIGNAL_SENT"})
            except Exception as e:
                self.logger.error(f"❌ Erreur _tick_monitoring_loop: {e}")
                await asyncio.sleep(2)

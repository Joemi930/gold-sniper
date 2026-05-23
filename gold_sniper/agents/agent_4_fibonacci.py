import asyncio
import numpy as np

from core.blackboard import BlackBoard
from core.agent_result import AgentResult
from utils.logger import get_logger

def calculate_ote_zones(swing_low_price: float, swing_high_price: float, direction: str) -> dict:
    total_range = swing_high_price - swing_low_price
    
    if direction == "LONG":
        equilibrium   = swing_high_price - 0.500 * total_range
        ote_high      = swing_high_price - 0.618 * total_range
        ote_sweet     = swing_high_price - 0.705 * total_range
        ote_low       = swing_high_price - 0.786 * total_range
        
        tp1 = swing_high_price + 0.272 * total_range
        tp2 = swing_high_price + 0.618 * total_range
        tp3 = swing_high_price + 1.000 * total_range
        
        discount_zone = (swing_low_price, equilibrium)
        premium_zone  = (equilibrium, swing_high_price)
    
    elif direction == "SHORT":
        equilibrium   = swing_low_price + 0.500 * total_range
        ote_low       = swing_low_price + 0.618 * total_range
        ote_sweet     = swing_low_price + 0.705 * total_range
        ote_high      = swing_low_price + 0.786 * total_range
        
        tp1 = swing_low_price - 0.272 * total_range
        tp2 = swing_low_price - 0.618 * total_range
        tp3 = swing_low_price - 1.000 * total_range
        
        discount_zone = (swing_low_price, equilibrium)
        premium_zone  = (equilibrium, swing_high_price)
    else:
        return {}
    
    return {
        "direction": direction,
        "swing_low": swing_low_price,
        "swing_high": swing_high_price,
        "equilibrium": equilibrium,
        "ote_high": ote_high,
        "ote_sweet": ote_sweet,
        "ote_low": ote_low,
        "ote_zone": (ote_low, ote_high),
        "discount_zone": discount_zone,
        "premium_zone": premium_zone,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
    }

def score_fibonacci_ote(current_price: float, fib_levels: dict, direction: str) -> AgentResult:
    if not fib_levels:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="INVALID_FIB_LEVELS",
            direction=direction, is_hard_filter=False
        )

    equilibrium = fib_levels["equilibrium"]
    ote_low = fib_levels["ote_low"]
    ote_high = fib_levels["ote_high"]
    ote_sweet = fib_levels["ote_sweet"]
    
    if direction == "LONG" and current_price > equilibrium:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="PREMIUM_ZONE_LONG_FORBIDDEN",
            direction=direction, is_hard_filter=False
        )
    
    if direction == "SHORT" and current_price < equilibrium:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="DISCOUNT_ZONE_SHORT_FORBIDDEN",
            direction=direction, is_hard_filter=False
        )
    
    in_ote = ote_low <= current_price <= ote_high
    
    if not in_ote:
        return AgentResult(
            agent_id="agent_4", score=25,
            reason="IN_CORRECT_ZONE_BUT_NOT_YET_IN_OTE - Attendre",
            direction=direction, is_hard_filter=False
        )
    
    ote_half_width = (ote_high - ote_low) / 2
    if ote_half_width == 0:
        precision = 0.0
    else:
        distance_to_sweet = abs(current_price - ote_sweet)
        precision = max(0.0, 1.0 - (distance_to_sweet / ote_half_width))
    
    score = 60 + precision * 40
    
    return AgentResult(
        agent_id="agent_4",
        score=round(score, 1),
        reason=f"IN_OTE_PRECISION={precision:.0%}_SWEET={ote_sweet:.2f}",
        direction=direction,
        is_hard_filter=False,
        metadata={
            "ote_zone": fib_levels["ote_zone"],
            "ote_sweet": ote_sweet,
            "precision": precision,
            "current_price": current_price,
            "equilibrium": equilibrium,
            "tp1": fib_levels["tp1"],
            "tp2": fib_levels["tp2"],
            "tp3": fib_levels["tp3"],
            "price_in_ote": True,
        }
    )

class AgentFibonacci:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_4_fibonacci"
    
    async def run(self):
        self.logger.info("▶️  Agent 4 (Fibonacci V2) démarré")
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
                    await self.bb.write_agent_result("agent_4", AgentResult(
                        agent_id="agent_4", score=25,
                        reason="WAITING_ON_AGENT2_FAIL",
                        direction=None, is_hard_filter=False
                    ))
                    # Retrocompat UI
                    await self.bb.update_dict(f"agents.{self.name}", {"price_in_ote": False})
                    continue
                
                direction = agent1_result.direction
                if not direction:
                    continue
                
                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14")
                current_tick = self.bb.read_sync("market_data.current_tick")
                
                if len(candles_15m) < 10 or not atr_14 or not current_tick:
                    await asyncio.sleep(2)
                    continue
                
                high = np.array([c["high"] for c in candles_15m])
                low = np.array([c["low"] for c in candles_15m])
                close = np.array([c["close"] for c in candles_15m])
                
                from agents.agent_1_meteo import detect_swings
                swings = detect_swings(high, low, close, n=3, atr_14=atr_14)
                
                if not swings["swing_highs"] or not swings["swing_lows"]:
                    continue
                
                last_high = swings["swing_highs"][-1]["price"]
                last_low = swings["swing_lows"][-1]["price"]
                
                # S'assurer que le high est bien > au low pour le range
                if last_high <= last_low:
                    last_high, last_low = max(high), min(low)
                
                fib_levels = calculate_ote_zones(last_low, last_high, direction)
                
                bid = current_tick.get("bid", 0.0)
                ask = current_tick.get("ask", 0.0)
                current_price = (bid + ask) / 2 if bid > 0 else close[-1]
                
                result = score_fibonacci_ote(current_price, fib_levels, direction)
                
                # Retrocompat UI
                in_ote = result.metadata.get("price_in_ote", False) if result.metadata else False
                await self.bb.update_dict(f"agents.{self.name}", {
                    "price_in_ote": in_ote
                })
                
                await self.bb.write_agent_result("agent_4", result)
                
            except Exception as e:
                self.logger.error(f"❌ Erreur dans Agent 4 (Fibonacci V2) : {e}")
                await asyncio.sleep(5)

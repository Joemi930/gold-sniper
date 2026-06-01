import asyncio
import numpy as np

from core.blackboard import BlackBoard
from agents.base_agent import AgentResult
from utils.agent_dashboard_helpers import idle_result
from utils.logger import get_logger

def calculate_ote_zones(swing_low_price: float, swing_high_price: float, direction: str) -> dict:
    """Calcule equilibrium, premium/discount, OTE 61.8-78.6 et sweet spot 70.5."""
    total_range = swing_high_price - swing_low_price
    if total_range <= 0:
        return {}
    
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


def calculate_ote_levels(swing_low: float, swing_high: float, direction: str) -> dict:
    """Alias Script 06 pour calculate_ote_zones."""
    return calculate_ote_zones(swing_low, swing_high, direction)


def score_fibonacci_ote(current_price: float, fib_levels: dict, direction: str, dxy_bias: str = "NEUTRAL") -> AgentResult:
    """Score la matrice Premium/Discount et la precision OTE."""
    if not fib_levels:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="INVALID_FIB_LEVELS",
            direction=direction, hard_filter_pass=False
        )

    equilibrium = fib_levels["equilibrium"]
    ote_low = fib_levels["ote_low"]
    ote_high = fib_levels["ote_high"]
    ote_sweet = fib_levels["ote_sweet"]
    is_discount = current_price <= equilibrium
    is_premium = current_price >= equilibrium
    
    if direction == "LONG" and current_price > equilibrium:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="PREMIUM_ZONE_LONG_FORBIDDEN",
            direction=direction, hard_filter_pass=False,
            payload={
                "levels": fib_levels,
                "current_price": current_price,
                "equilibrium": equilibrium,
                "in_discount": False,
                "in_premium": True,
                "in_ote": False,
                "price_in_ote": False,
                "premium_discount_ok": False,
                "forbidden": True,
            },
        )
    
    if direction == "SHORT" and current_price < equilibrium:
        return AgentResult(
            agent_id="agent_4", score=0,
            reason="DISCOUNT_ZONE_SHORT_FORBIDDEN",
            direction=direction, hard_filter_pass=False,
            payload={
                "levels": fib_levels,
                "current_price": current_price,
                "equilibrium": equilibrium,
                "in_discount": True,
                "in_premium": False,
                "in_ote": False,
                "price_in_ote": False,
                "premium_discount_ok": False,
                "forbidden": True,
            },
        )
    
    in_ote = ote_low <= current_price <= ote_high
    macro_adjustment = 0
    if direction == "LONG" and dxy_bias == "BEARISH":
        macro_adjustment = 10
    elif direction == "LONG" and dxy_bias == "BULLISH":
        macro_adjustment = -10
    elif direction == "SHORT" and dxy_bias == "BULLISH":
        macro_adjustment = 10
    elif direction == "SHORT" and dxy_bias == "BEARISH":
        macro_adjustment = -10
    
    if not in_ote:
        return AgentResult(
            agent_id="agent_4", score=max(0, min(25 + macro_adjustment, 100)),
            reason="IN_CORRECT_ZONE_BUT_NOT_YET_IN_OTE - Attendre",
            direction=direction, hard_filter_pass=True,
            payload={
                "levels": fib_levels,
                "current_price": current_price,
                "equilibrium": equilibrium,
                "ote_low": ote_low,
                "ote_high": ote_high,
                "ote_sweet": ote_sweet,
                "in_discount": is_discount,
                "in_premium": is_premium,
                "in_ote": False,
                "price_in_ote": False,
                "precision": 0.0,
                "precision_pct": 0.0,
                "premium_discount_ok": True,
                "forbidden": False,
                "dxy_bias": dxy_bias,
                "macro_adjustment": macro_adjustment,
            },
        )
    
    ote_half_width = (ote_high - ote_low) / 2
    if ote_half_width == 0:
        precision = 0.0
    else:
        distance_to_sweet = abs(current_price - ote_sweet)
        precision = max(0.0, 1.0 - (distance_to_sweet / ote_half_width))
    
    score = max(0, min(60 + precision * 30 + macro_adjustment, 100))
    
    return AgentResult(
        agent_id="agent_4",
        score=round(score, 1),
        reason=f"IN_OTE_PRECISION={precision:.0%}_SWEET_70_5={ote_sweet:.2f}_DXY={macro_adjustment}",
        direction=direction,
        hard_filter_pass=True,
        payload={
            "ote_zone": fib_levels["ote_zone"],
            "ote_low": ote_low,
            "ote_high": ote_high,
            "ote_sweet": ote_sweet,
            "precision": precision,
            "precision_pct": precision,
            "current_price": current_price,
            "equilibrium": equilibrium,
            "in_discount": is_discount,
            "in_premium": is_premium,
            "in_ote": True,
            "premium_discount_ok": True,
            "forbidden": False,
            "dxy_bias": dxy_bias,
            "macro_adjustment": macro_adjustment,
            "tp1": fib_levels["tp1"],
            "tp2": fib_levels["tp2"],
            "tp3": fib_levels["tp3"],
            "price_in_ote": True,
            "levels": fib_levels,
        }
    )


def score_fibonacci(current_price: float, levels: dict, direction: str, dxy_bias: str = "NEUTRAL") -> dict:
    """Interface Script 06 retournant un dict de score."""
    result = score_fibonacci_ote(current_price, levels, direction, dxy_bias)
    payload = result.payload or {}
    return {
        "score": result.score,
        "hard_filter_pass": result.hard_filter_pass,
        "in_ote": payload.get("in_ote", False),
        "in_discount": payload.get("in_discount", False),
        "in_premium": payload.get("in_premium", False),
        "precision_pct": payload.get("precision_pct", 0.0),
        "dxy_bias": payload.get("dxy_bias", dxy_bias),
        "macro_adjustment": payload.get("macro_adjustment", 0),
        "reason": result.reason,
        "levels": payload.get("levels", levels),
    }

class AgentFibonacci:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_4"
    
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
                    waiting = AgentResult(
                        agent_id="agent_4",
                        score=25,
                        reason="WAITING_ON_AGENT2_FAIL",
                        direction=None,
                        hard_filter_pass=False,
                    )
                    await self.bb.publish_agent_dashboard(
                        "agent_4", waiting, min_interval_sec=0, trigger_orchestrator=False
                    )
                    await self.bb.update_dict(f"agents.{self.name}", {"price_in_ote": False})
                    continue

                direction = agent1_result.direction
                if not direction:
                    await self.bb.publish_agent_dashboard(
                        "agent_4",
                        idle_result("agent_4", reason="WAITING_NO_DIRECTION", score=25),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    continue

                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14")
                current_tick = self.bb.read_sync("market_data.current_tick")
                
                if len(candles_15m) < 10 or not atr_14 or not current_tick:
                    await self.bb.publish_agent_dashboard(
                        "agent_4",
                        idle_result("agent_4", reason="WAITING_INSUFFICIENT_DATA", score=25),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    await asyncio.sleep(2)
                    continue
                
                high = np.array([c["high"] for c in candles_15m])
                low = np.array([c["low"] for c in candles_15m])
                close = np.array([c["close"] for c in candles_15m])
                
                from agents.agent_1_meteo import detect_swings
                swings = detect_swings(high, low, close, n=3, atr_14=atr_14)
                
                if not swings["swing_highs"] or not swings["swing_lows"]:
                    await self.bb.publish_agent_dashboard(
                        "agent_4",
                        idle_result("agent_4", reason="WAITING_NO_SWINGS", score=25),
                        min_interval_sec=0,
                        trigger_orchestrator=False,
                    )
                    continue
                
                last_high = swings["swing_highs"][-1]["price"]
                last_low = swings["swing_lows"][-1]["price"]
                
                # S'assurer que le high est bien > au low pour le range
                if last_high <= last_low:
                    last_high, last_low = max(high), min(low)
                
                fib_levels = calculate_ote_levels(last_low, last_high, direction)
                
                bid = current_tick.get("bid", 0.0)
                ask = current_tick.get("ask", 0.0)
                current_price = (bid + ask) / 2 if bid > 0 else close[-1]
                
                market = self.bb.get_market()
                result = score_fibonacci_ote(current_price, fib_levels, direction, market.get("dxy_bias", "NEUTRAL"))
                
                # Retrocompat UI
                payload = result.payload or {}
                await self.bb.update_agent(
                    self.name,
                    {
                        "score": result.score,
                        "direction": result.direction,
                        "swing_used": {"low_price": last_low, "high_price": last_high},
                        "ote_low": payload.get("ote_low"),
                        "ote_high": payload.get("ote_high"),
                        "ote_sweet": payload.get("ote_sweet"),
                        "equilibrium": payload.get("equilibrium"),
                        "in_ote": payload.get("in_ote", False),
                        "price_in_ote": payload.get("price_in_ote", False),
                        "in_discount": payload.get("in_discount", False),
                        "in_premium": payload.get("in_premium", False),
                        "precision_pct": payload.get("precision_pct", 0.0),
                        "reason": result.reason,
                        "hard_filter_pass": result.hard_filter_pass,
                    },
                )
                
                await self.bb.publish_agent_dashboard(
                    "agent_4", result, min_interval_sec=0
                )

            except Exception as e:
                self.logger.error(f"❌ Erreur dans Agent 4 (Fibonacci V2) : {e}")
                from config import AGENT_DASHBOARD_PULSE_SEC

                await self.bb.publish_agent_dashboard(
                    "agent_4",
                    idle_result(
                        "agent_4",
                        reason=f"ERROR: {e}",
                        score=25,
                        hard_filter_pass=False,
                    ),
                    min_interval_sec=AGENT_DASHBOARD_PULSE_SEC,
                    trigger_orchestrator=False,
                )
                await asyncio.sleep(5)

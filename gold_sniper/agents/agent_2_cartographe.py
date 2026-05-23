import asyncio
import numpy as np

from core.blackboard import BlackBoard
from core.agent_result import AgentResult
from utils.logger import get_logger

def detect_order_blocks(high: np.ndarray, low: np.ndarray,
                        open_: np.ndarray, close: np.ndarray,
                        swing_highs: list, swing_lows: list,
                        atr_14: float, direction: str) -> list:
    obs = []
    length = len(close)
    
    for i in range(2, length - 2):
        if direction == "LONG":
            if close[i] >= open_[i]:
                continue
            
            post_body = abs(close[i+1] - open_[i+1])
            is_displacement = (
                close[i+1] > open_[i+1] and
                post_body >= 1.5 * atr_14
            )
            
            if not is_displacement:
                continue
            
            recent_shs = [s for s in swing_highs if s["index"] < i]
            if not recent_shs:
                continue
            last_sh_price = recent_shs[-1]["price"]
            bos_confirmed = close[i+1] > last_sh_price or (len(close) > i+2 and close[i+2] > last_sh_price)
            
            if not bos_confirmed:
                continue
            
            ob_zone = {
                "type": "BULLISH",
                "top": high[i],
                "bottom": low[i],
                "entry_zone_top": open_[i],
                "entry_zone_bottom": low[i],
                "candle_index": i,
                "age": length - 1 - i,
            }
        
        elif direction == "SHORT":
            if close[i] <= open_[i]:
                continue
            
            post_body = abs(close[i+1] - open_[i+1])
            is_displacement = (
                close[i+1] < open_[i+1] and
                post_body >= 1.5 * atr_14
            )
            
            if not is_displacement:
                continue
            
            recent_sls = [s for s in swing_lows if s["index"] < i]
            if not recent_sls:
                continue
            last_sl_price = recent_sls[-1]["price"]
            bos_confirmed = close[i+1] < last_sl_price or (len(close) > i+2 and close[i+2] < last_sl_price)
            
            if not bos_confirmed:
                continue
            
            ob_zone = {
                "type": "BEARISH",
                "top": high[i],
                "bottom": low[i],
                "entry_zone_top": high[i],
                "entry_zone_bottom": close[i],
                "candle_index": i,
                "age": length - 1 - i,
            }
        else:
            continue
        
        ob_score = score_order_block(high, low, open_, close, i, atr_14, swing_lows, swing_highs, direction)
        ob_zone["ob_score"] = ob_score
        
        if ob_score >= 40:
            obs.append(ob_zone)
    
    obs.sort(key=lambda x: x["ob_score"], reverse=True)
    return obs[:3]

def score_order_block(high, low, open_, close, i, atr_14, swing_lows, swing_highs, direction) -> float:
    score = 0
    
    if len(close) > i + 1:
        post_body = abs(close[i+1] - open_[i+1])
        displacement_ratio = post_body / atr_14 if atr_14 > 0 else 0
        score += min(displacement_ratio / 2.0, 1.0) * 30
    
    if len(low) > i + 2:
        if direction == "LONG":
            fvg_present = low[i+2] > high[i]
        else:
            fvg_present = high[i+2] < low[i]
        score += 25 if fvg_present else 0
    
    ob_body = abs(close[i] - open_[i])
    ob_range = high[i] - low[i]
    body_ratio = ob_body / ob_range if ob_range > 0 else 0
    score += body_ratio * 20
    
    if direction == "LONG":
        recent_sls = [s for s in swing_lows if i - 5 <= s["index"] < i]
        prior_swept = any(low[i] < s["price"] for s in recent_sls)
    else:
        recent_shs = [s for s in swing_highs if i - 5 <= s["index"] < i]
        prior_swept = any(high[i] > s["price"] for s in recent_shs)
    score += 15 if prior_swept else 0
    
    if len(close) > i + 2:
        if direction == "LONG":
            multi_momentum = close[i+1] > open_[i+1] and close[i+2] > open_[i+2]
        else:
            multi_momentum = close[i+1] < open_[i+1] and close[i+2] < open_[i+2]
        score += 10 if multi_momentum else 0
    
    return min(score, 100.0)

def detect_fvg(high: np.ndarray, low: np.ndarray, atr_14: float, direction: str) -> list:
    fvgs = []
    length = len(high)
    min_size = 0.1 * atr_14
    
    for i in range(2, length):
        if direction == "LONG":
            fvg_size = low[i] - high[i-2]
            if fvg_size >= min_size:
                fvg_top = low[i]
                fvg_bottom = high[i-2]
                equilibrium = (fvg_top + fvg_bottom) / 2
                
                mitigated = False
                for j in range(i+1, length):
                    if low[j] <= equilibrium:
                        mitigated = True
                        break
                
                fvgs.append({
                    "type": "BULLISH",
                    "top": fvg_top,
                    "bottom": fvg_bottom,
                    "equilibrium": equilibrium,
                    "size": fvg_size,
                    "size_ratio": fvg_size / atr_14,
                    "fresh": not mitigated,
                    "candle_index": i,
                })
        
        elif direction == "SHORT":
            fvg_size = low[i-2] - high[i]
            if fvg_size >= min_size:
                fvg_top = low[i-2]
                fvg_bottom = high[i]
                equilibrium = (fvg_top + fvg_bottom) / 2
                
                mitigated = False
                for j in range(i+1, length):
                    if high[j] >= equilibrium:
                        mitigated = True
                        break
                
                fvgs.append({
                    "type": "BEARISH",
                    "top": fvg_top,
                    "bottom": fvg_bottom,
                    "equilibrium": equilibrium,
                    "size": fvg_size,
                    "size_ratio": fvg_size / atr_14,
                    "fresh": not mitigated,
                    "candle_index": i,
                })
    
    fresh_fvgs = [f for f in fvgs if f["fresh"]]
    fresh_fvgs.sort(key=lambda x: x["candle_index"], reverse=True)
    return fresh_fvgs[:3]

def score_agent_2(best_ob: dict | None, best_fvg: dict | None,
                  current_price: float, atr_14: float,
                  blackboard: BlackBoard) -> AgentResult:
    if not best_ob and not best_fvg:
        return AgentResult(
            agent_id="agent_2", score=0,
            reason="NO_FRESH_POI_AVAILABLE",
            direction=None, is_hard_filter=True
        )
    
    best_zone = None
    
    if best_ob and best_fvg:
        ob_has_fvg_confluence = (
            best_ob["bottom"] <= best_fvg["top"] and
            best_fvg["bottom"] <= best_ob["top"]
        )
        if ob_has_fvg_confluence:
            best_zone = {**best_ob, "fvg_confluence": True, "fvg_size_ratio": best_fvg["size_ratio"]}
        else:
            best_zone = best_ob if best_ob["ob_score"] >= 60 else best_fvg
    elif best_ob:
        best_zone = {**best_ob, "fvg_confluence": False}
    else:
        best_zone = best_fvg
        
    if not best_zone:
        return AgentResult(agent_id="agent_2", score=0, reason="NO_VALID_POI", direction=None, is_hard_filter=True)
    
    if best_zone.get("type") == "BULLISH":
        zone_is_fresh = current_price > best_zone["bottom"]
        price_in_zone = best_zone["bottom"] <= current_price <= best_zone["top"]
    else:
        zone_is_fresh = current_price < best_zone["top"]
        price_in_zone = best_zone["bottom"] <= current_price <= best_zone["top"]
    
    if not zone_is_fresh:
        return AgentResult(
            agent_id="agent_2", score=0,
            reason="ZONE_ALREADY_MITIGATED",
            direction=None, is_hard_filter=True
        )
    
    ob_score = best_zone.get("ob_score", 0) if "ob_score" in best_zone else 0
    fvg_size_ratio = best_zone.get("fvg_size_ratio", 0)
    fvg_confluence = best_zone.get("fvg_confluence", False)
    
    score = ob_score * 0.7
    
    if fvg_confluence:
        fvg_bonus = min(fvg_size_ratio * 10, 20)
        score += fvg_bonus
    
    zone_age = best_zone.get("age", 0)
    if zone_age <= 10:
        score += 10
    elif zone_age >= 50:
        score -= 20
    
    score = max(0, min(score, 100))
    
    if price_in_zone and score > 0:
        asyncio.create_task(
            blackboard.notify_price_in_poi({
                "zone": best_zone,
                "score_agent2": score,
                "current_price": current_price,
            })
        )
    
    return AgentResult(
        agent_id="agent_2",
        score=score,
        reason=f"FRESH_POI_OB={ob_score:.0f}_FVG_CONF={fvg_confluence}_AGE={zone_age}",
        direction=None,
        is_hard_filter=True,
        metadata={
            "zone_type": best_zone.get("type"),
            "zone_top": best_zone.get("top"),
            "zone_bottom": best_zone.get("bottom"),
            "ob_score": ob_score,
            "fvg_confluence": fvg_confluence,
            "price_in_zone": price_in_zone,
            "zone_age_15m_candles": zone_age,
        }
    )

class AgentCartographe:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_2_cartographe"
        self._known_zones = []
    
    async def run(self):
        self.logger.info("▶️  Agent 2 (Cartographe V2) démarré")
        while not self.bb.kill_event.is_set():
            try:
                try:
                    await asyncio.wait_for(self.bb._events["new_candle_15m"].wait(), timeout=15.0)
                except asyncio.TimeoutError:
                    pass
                
                agent1_result = await self.bb.wait_for_agent("agent_1", timeout=2.0)
                
                if not agent1_result or agent1_result.score == 0:
                    await self.bb.write_agent_result("agent_2", AgentResult(
                        agent_id="agent_2", score=0,
                        reason="WAITING_ON_AGENT1_FAIL",
                        direction=None, is_hard_filter=True
                    ))
                    # UI Retrocompatibility
                    await self.bb.update_dict(f"agents.{self.name}", {"order_blocks": []})
                    continue
                
                direction = agent1_result.direction
                
                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                atr_14 = self.bb.read_sync("market_data.atr_14")
                current_tick = self.bb.read_sync("market_data.current_tick")
                
                if len(candles_15m) < 10 or not atr_14 or not current_tick:
                    await asyncio.sleep(2)
                    continue
                
                high = np.array([c["high"] for c in candles_15m])
                low = np.array([c["low"] for c in candles_15m])
                open_ = np.array([c["open"] for c in candles_15m])
                close = np.array([c["close"] for c in candles_15m])
                
                agent1_meta = agent1_result.metadata or {}
                
                # Import here as specified
                from agents.agent_1_meteo import detect_swings
                swings = detect_swings(high, low, close, n=3, atr_14=atr_14)
                
                obs = detect_order_blocks(high, low, open_, close,
                                           swings["swing_highs"], swings["swing_lows"],
                                           atr_14, direction)
                fvgs = detect_fvg(high, low, atr_14, direction)
                
                best_ob = obs[0] if obs else None
                best_fvg = fvgs[0] if fvgs else None
                
                bid = current_tick.get("bid", 0.0)
                ask = current_tick.get("ask", 0.0)
                current_price = (bid + ask) / 2 if bid > 0 else close[-1]
                
                result = score_agent_2(best_ob, best_fvg, current_price, atr_14, self.bb)
                
                await self.bb.write_agent_result("agent_2", result)
                
                # Update UI Dashboard compatibility (requires "order_blocks" list)
                ui_obs = []
                if best_ob:
                    ui_obs.append(best_ob)
                if best_fvg and best_fvg not in ui_obs:
                    ui_obs.append(best_fvg)
                
                await self.bb.update_dict(f"agents.{self.name}", {
                    "order_blocks": ui_obs,
                    "fvgs": fvgs
                })
                
                await self.bb.update_dict("market_analysis.zones", {
                    "order_blocks": ui_obs,
                    "fvgs": fvgs
                })
                
            except Exception as e:
                self.logger.error(f"❌ Erreur dans Agent 2 (Cartographe V2) : {e}")
                await asyncio.sleep(5)

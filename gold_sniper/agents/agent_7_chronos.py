import asyncio
from datetime import datetime
import pytz

from core.blackboard import BlackBoard
from core.agent_result import AgentResult
from utils.logger import get_logger

UTC = pytz.UTC

KILL_ZONES = {
    "LONDON_OPEN":   {"start": 7.0, "end": 10.0, "score": 100, "risk": 1.0},
    "NY_OPEN":       {"start": 12.0, "end": 15.0, "score": 100, "risk": 1.0},
}

FORBIDDEN_PERIODS = {
    "ASIAN_SESSION": {"start": 22.0, "end": 6.0},
    "ROLLOVER":      {"start": 23.75, "end": 0.25},
    "DEAD_ZONE":     {"start": 21.0, "end": 22.0},
}

FRIDAY_RULES = {
    "risk_reduced_after": 16.0,
    "halt_after": 19.0,
}

def get_utc_decimal_hour(dt: datetime) -> float:
    return dt.hour + dt.minute / 60.0

def check_kill_zone(utc_time: datetime) -> dict:
    hour_decimal = get_utc_decimal_hour(utc_time)
    day_of_week = utc_time.weekday()
    
    if day_of_week == 4:
        if hour_decimal >= FRIDAY_RULES["halt_after"]:
            return {"in_kz": False, "kz_name": "FRIDAY_HALT", "score": 0, "risk_modifier": 0.0}
        if hour_decimal >= FRIDAY_RULES["risk_reduced_after"]:
            return {"in_kz": True, "kz_name": "FRIDAY_REDUCED", "score": 70, "risk_modifier": 0.5}
    
    if day_of_week in (5, 6):
        return {"in_kz": False, "kz_name": "WEEKEND", "score": 0, "risk_modifier": 0.0}
    
    rollover = FORBIDDEN_PERIODS["ROLLOVER"]
    if hour_decimal >= rollover["start"] or hour_decimal <= rollover["end"]:
        return {"in_kz": False, "kz_name": "ROLLOVER", "score": 0, "risk_modifier": 0.0}
    
    asian = FORBIDDEN_PERIODS["ASIAN_SESSION"]
    if hour_decimal >= asian["start"] or hour_decimal <= asian["end"]:
        return {"in_kz": False, "kz_name": "ASIAN_BLOCKED", "score": 0, "risk_modifier": 0.0}
    
    dead = FORBIDDEN_PERIODS["DEAD_ZONE"]
    if dead["start"] <= hour_decimal <= dead["end"]:
        return {"in_kz": False, "kz_name": "DEAD_ZONE", "score": 0, "risk_modifier": 0.0}
    
    for kz_name, kz in KILL_ZONES.items():
        if kz["start"] <= hour_decimal <= kz["end"]:
            return {
                "in_kz": True,
                "kz_name": kz_name,
                "score": kz["score"],
                "risk_modifier": kz["risk"],
            }
    
    return {"in_kz": False, "kz_name": "OFF_PEAK", "score": 30, "risk_modifier": 0.7}

def calculate_volume_profile(candles_session: list, n_buckets: int = 50) -> dict:
    if not candles_session:
        return {"poc": None, "vah": None, "val": None}
    
    session_high = max(c["high"] for c in candles_session)
    session_low = min(c["low"] for c in candles_session)
    session_range = session_high - session_low
    
    if session_range == 0:
        return {"poc": None, "vah": None, "val": None}
    
    bucket_size = session_range / n_buckets
    volume_by_bucket = [0.0] * n_buckets
    
    for candle in candles_session:
        candle_vol = candle.get("tick_volume", candle.get("volume", 1))
        
        for b in range(n_buckets):
            bucket_bottom = session_low + b * bucket_size
            bucket_top = bucket_bottom + bucket_size
            
            if candle["low"] <= bucket_top and candle["high"] >= bucket_bottom:
                overlap = min(candle["high"], bucket_top) - max(candle["low"], bucket_bottom)
                candle_range = candle["high"] - candle["low"]
                if candle_range > 0:
                    volume_by_bucket[b] += candle_vol * (overlap / candle_range)
    
    poc_bucket = volume_by_bucket.index(max(volume_by_bucket))
    poc_price = session_low + (poc_bucket + 0.5) * bucket_size
    
    total_volume = sum(volume_by_bucket)
    target_volume = 0.70 * total_volume
    
    included = set([poc_bucket])
    cumulative = volume_by_bucket[poc_bucket]
    
    low_ptr = poc_bucket - 1
    high_ptr = poc_bucket + 1
    
    while cumulative < target_volume:
        vol_above = volume_by_bucket[high_ptr] if high_ptr < n_buckets else 0
        vol_below = volume_by_bucket[low_ptr] if low_ptr >= 0 else 0
        
        if vol_above >= vol_below and high_ptr < n_buckets:
            included.add(high_ptr)
            cumulative += vol_above
            high_ptr += 1
        elif low_ptr >= 0:
            included.add(low_ptr)
            cumulative += vol_below
            low_ptr -= 1
        else:
            break
    
    vah_bucket = max(included)
    val_bucket = min(included)
    
    return {
        "poc": poc_price,
        "vah": session_low + (vah_bucket + 1) * bucket_size,
        "val": session_low + val_bucket * bucket_size,
        "total_volume": total_volume,
    }

def score_agent_7(utc_time: datetime, volume_profile: dict, current_price: float) -> AgentResult:
    kz_result = check_kill_zone(utc_time)
    
    if kz_result["score"] == 0:
        return AgentResult(
            agent_id="agent_7", score=0,
            reason=f"SESSION_BLOCKED_{kz_result['kz_name']}",
            direction=None, is_hard_filter=False,
            risk_modifier=0.0
        )
    
    base_score = kz_result["score"]
    
    vp_bonus = 0
    if volume_profile.get("val") and volume_profile.get("vah") and volume_profile.get("poc"):
        if volume_profile["val"] <= current_price <= volume_profile["vah"]:
            vp_bonus = 20
        elif abs(current_price - volume_profile["poc"]) < 0.5:  # Tolérance légère
            vp_bonus = 25
    
    final_score = min(base_score + vp_bonus, 100)
    
    return AgentResult(
        agent_id="agent_7",
        score=final_score,
        reason=f"KZ_{kz_result['kz_name']}_VP_BONUS={vp_bonus}",
        direction=None,
        is_hard_filter=False,
        risk_modifier=kz_result["risk_modifier"],
        metadata={
            "kill_zone": kz_result["kz_name"],
            "volume_profile": volume_profile,
            "vp_bonus": vp_bonus,
        }
    )

class AgentSessions:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_7_sessions"
    
    async def run(self):
        self.logger.info("▶️  Agent 7 (Chronos V2) démarré")
        while not self.bb.kill_event.is_set():
            try:
                utc_time = datetime.now(UTC)
                
                # Fetch recent candles for volume profile (let's say we look at last day 15m candles)
                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                # Only keep today's candles
                today = utc_time.date()
                session_candles = []
                for c in candles_15m:
                    if "time" in c:
                        cdt = c["time"] if isinstance(c["time"], datetime) else datetime.fromtimestamp(c["time"], UTC)
                        if cdt.date() == today:
                            session_candles.append(c)
                
                volume_profile = calculate_volume_profile(session_candles)
                
                tick = self.bb.read_sync("market_data.current_tick")
                bid = tick.get("bid", 0.0) if tick else 0.0
                ask = tick.get("ask", 0.0) if tick else 0.0
                current_price = (bid + ask) / 2 if bid > 0 else (session_candles[-1]["close"] if session_candles else 0.0)
                
                result = score_agent_7(utc_time, volume_profile, current_price)
                await self.bb.write_agent_result("agent_7", result)
                
                await self.bb.update_dict(f"agents.{self.name}", {
                    "in_killzone": result.metadata.get("kill_zone") not in ["ASIAN_BLOCKED", "ROLLOVER", "DEAD_ZONE", "WEEKEND", "FRIDAY_HALT"],
                    "killzone_name": result.metadata.get("kill_zone", "OFF_HOURS"),
                })
                
                await asyncio.sleep(10)
            except Exception as e:
                self.logger.error(f"❌ Erreur dans Agent 7 (Chronos V2) : {e}")
                await asyncio.sleep(5)

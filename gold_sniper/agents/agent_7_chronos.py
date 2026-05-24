import asyncio
from datetime import datetime, timezone

from agents.base_agent import AgentResult
from core.blackboard import BlackBoard
from utils.logger import get_logger


SESSIONS = {
    "TOKYO": {"start": 22.0, "end": 7.0, "confidence": 0.50},
    "LONDON": {"start": 7.0, "end": 12.0, "confidence": 1.00},
    "OVERLAP": {"start": 12.0, "end": 16.0, "confidence": 1.20},
    "NEW_YORK": {"start": 16.0, "end": 21.0, "confidence": 1.00},
    "DEAD": {"start": 21.0, "end": 22.0, "confidence": 0.00},
    "ROLLOVER": {"start": 23.75, "end": 0.25, "confidence": 0.00},
}

KILL_ZONES = {
    "LONDON_OPEN": {"start": 7.0, "end": 10.0, "score": 100},
    "OVERLAP_KZ": {"start": 12.0, "end": 16.0, "score": 95},
    "NY_OPEN": {"start": 12.0, "end": 15.0, "score": 100},
}

FRIDAY_RULES = {
    "risk_reduced_after": 16.0,
    "halt_after": 19.0,
}

TOKYO_OVERRIDE_SCORE = 92.0


def get_utc_decimal_hour(dt: datetime) -> float:
    return dt.hour + dt.minute / 60.0


def _in_time_range(hour: float, start: float, end: float) -> bool:
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def identify_session(utc_time: datetime) -> str:
    hour = get_utc_decimal_hour(utc_time)
    if _in_time_range(hour, SESSIONS["ROLLOVER"]["start"], SESSIONS["ROLLOVER"]["end"]):
        return "ROLLOVER"
    if _in_time_range(hour, SESSIONS["OVERLAP"]["start"], SESSIONS["OVERLAP"]["end"]):
        return "OVERLAP"
    if _in_time_range(hour, SESSIONS["LONDON"]["start"], SESSIONS["LONDON"]["end"]):
        return "LONDON"
    if _in_time_range(hour, SESSIONS["NEW_YORK"]["start"], SESSIONS["NEW_YORK"]["end"]):
        return "NEW_YORK"
    if _in_time_range(hour, SESSIONS["TOKYO"]["start"], SESSIONS["TOKYO"]["end"]):
        return "TOKYO"
    return "DEAD"


def detect_kill_zone(utc_time: datetime) -> dict:
    hour = get_utc_decimal_hour(utc_time)
    for name, config in KILL_ZONES.items():
        if _in_time_range(hour, config["start"], config["end"]):
            return {"in_kill_zone": True, "kill_zone_name": name, "kill_zone_score": config["score"]}
    return {"in_kill_zone": False, "kill_zone_name": None, "kill_zone_score": 50}


def check_session_context(utc_time: datetime) -> dict:
    hour = get_utc_decimal_hour(utc_time)
    day = utc_time.weekday()

    if day in (5, 6):
        return {
            "session": "WEEKEND",
            "trading_allowed": False,
            "confidence": 0.0,
            "reason": "WEEKEND",
            **detect_kill_zone(utc_time),
        }

    if day == 4 and hour >= FRIDAY_RULES["halt_after"]:
        return {
            "session": "FRIDAY_HALT",
            "trading_allowed": False,
            "confidence": 0.0,
            "reason": "FRIDAY_HALT",
            **detect_kill_zone(utc_time),
        }

    session = identify_session(utc_time)
    confidence = SESSIONS.get(session, SESSIONS["DEAD"])["confidence"]
    trading_allowed = session not in {"DEAD", "ROLLOVER", "TOKYO"}
    reason = session

    if day == 4 and hour >= FRIDAY_RULES["risk_reduced_after"]:
        confidence = min(confidence, 0.5)
        reason = "FRIDAY_REDUCED_RISK"

    if session == "TOKYO":
        reason = f"TOKYO_ONLY_MIN_SCORE_{TOKYO_OVERRIDE_SCORE:.0f}"
    elif session in {"DEAD", "ROLLOVER"}:
        reason = f"SESSION_{session}_BLOCKED"

    return {
        "session": session,
        "trading_allowed": trading_allowed,
        "confidence": confidence,
        "reason": reason,
        **detect_kill_zone(utc_time),
    }


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
        candle_range = candle["high"] - candle["low"]
        if candle_range <= 0:
            continue

        for bucket in range(n_buckets):
            bucket_bottom = session_low + bucket * bucket_size
            bucket_top = bucket_bottom + bucket_size
            if candle["low"] <= bucket_top and candle["high"] >= bucket_bottom:
                overlap = min(candle["high"], bucket_top) - max(candle["low"], bucket_bottom)
                if overlap > 0:
                    volume_by_bucket[bucket] += candle_vol * (overlap / candle_range)

    poc_bucket = volume_by_bucket.index(max(volume_by_bucket))
    poc_price = session_low + (poc_bucket + 0.5) * bucket_size

    total_volume = sum(volume_by_bucket)
    target_volume = 0.70 * total_volume
    included = {poc_bucket}
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

    return {
        "poc": poc_price,
        "vah": session_low + (max(included) + 1) * bucket_size,
        "val": session_low + min(included) * bucket_size,
        "total_volume": total_volume,
    }


def score_agent_7(utc_time: datetime, volume_profile: dict, current_price: float) -> AgentResult:
    context = check_session_context(utc_time)
    vp_bonus = 0

    if volume_profile.get("val") and volume_profile.get("vah") and volume_profile.get("poc"):
        if volume_profile["val"] <= current_price <= volume_profile["vah"]:
            vp_bonus = 15
        elif abs(current_price - volume_profile["poc"]) < 0.5:
            vp_bonus = 20

    session_score = context["kill_zone_score"] if context["in_kill_zone"] else 50
    if not context["trading_allowed"] and context["session"] != "TOKYO":
        session_score = 0

    final_score = min(session_score * context["confidence"] + vp_bonus, 100)

    return AgentResult(
        agent_id="agent_7",
        score=final_score,
        hard_filter_pass=context["trading_allowed"] or context["session"] == "TOKYO",
        direction=None,
        reason=f"{context['reason']} | KZ={context['kill_zone_name'] or 'NONE'} | VP_bonus={vp_bonus}",
        risk_modifier=context["confidence"],
        payload={
            "session_name": context["session"],
            "trading_allowed": context["trading_allowed"],
            "session_confidence": context["confidence"],
            "tokyo_override_score": TOKYO_OVERRIDE_SCORE,
            "in_kill_zone": context["in_kill_zone"],
            "kill_zone_name": context["kill_zone_name"],
            "volume_profile": volume_profile,
            "vp_bonus": vp_bonus,
        },
    )


class AgentSessions:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_7"

    async def run(self):
        self.logger.info("Agent 7 (Chronos V2) demarre")
        while not self.bb.kill_event.is_set():
            try:
                utc_time = datetime.now(timezone.utc)
                candles_15m = list(self.bb.read_sync("market_data.candles.15m") or [])
                today = utc_time.date()
                session_candles = []

                for candle in candles_15m:
                    if "time" not in candle:
                        continue
                    candle_time = candle["time"]
                    if not isinstance(candle_time, datetime):
                        candle_time = datetime.fromtimestamp(candle_time, timezone.utc)
                    if candle_time.date() == today:
                        session_candles.append(candle)

                volume_profile = calculate_volume_profile(session_candles)
                tick = self.bb.read_sync("market_data.current_tick")
                bid = tick.get("bid", 0.0) if tick else 0.0
                ask = tick.get("ask", 0.0) if tick else 0.0
                current_price = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
                if current_price <= 0 and session_candles:
                    current_price = session_candles[-1]["close"]

                result = score_agent_7(utc_time, volume_profile, current_price)
                await self.bb.write_agent_result("agent_7", result)

                payload = result.payload
                await self.bb.update_agent(
                    self.name,
                    {
                        "score": result.score,
                        "in_kill_zone": payload["in_kill_zone"],
                        "kill_zone_name": payload["kill_zone_name"],
                        "risk_modifier": result.risk_modifier,
                        "trading_allowed": payload["trading_allowed"],
                        "vp_poc": volume_profile.get("poc"),
                        "vp_vah": volume_profile.get("vah"),
                        "vp_val": volume_profile.get("val"),
                        "price_in_value_area": (
                            bool(volume_profile.get("val") and volume_profile.get("vah"))
                            and volume_profile["val"] <= current_price <= volume_profile["vah"]
                        ),
                        "session_name": payload["session_name"],
                        "reason": result.reason,
                    },
                )
                await self.bb.update_market({"session": payload["session_name"]})
                await asyncio.sleep(10)
            except Exception as exc:
                self.logger.error(f"Erreur Agent 7 (Chronos V2): {exc}")
                await asyncio.sleep(5)

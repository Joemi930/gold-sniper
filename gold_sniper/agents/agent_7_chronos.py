import asyncio
from datetime import datetime, time, timedelta, timezone

from agents.base_agent import AgentResult
from config import (
    FRIDAY_RISK_REDUCTION_HOUR,
    FRIDAY_TRADING_HALT_HOUR,
    KILL_ZONES,
    ROLLOVER_END,
    ROLLOVER_START,
    SESSIONS,
    TZ_LOCAL,
)
from core.blackboard import BlackBoard
from core.visual_layers import VISUAL_LAYERS, VisualBackground, VisualHLine
from utils.logger import get_logger
from utils.discord_notifier import send_discord_notification


SESSION_CONFIDENCE = {
    "ASIA": 0.50,
    "TOKYO": 0.50,
    "LONDON_OPEN": 1.00,
    "LONDON": 0.70,
    "NY_OPEN": 1.00,
    "NY": 0.70,
    "OVERLAP": 1.20,
    "OFF_HOURS": 0.00,
    "ROLLOVER": 0.00,
    "WEEKEND": 0.00,
    "FRIDAY_HALT": 0.00,
}


def _window_to_decimal(config: dict) -> dict:
    start = config["start"][0] + config["start"][1] / 60.0
    end = config["end"][0] + config["end"][1] / 60.0
    return {
        "start": start,
        "end": end,
        "confidence": SESSION_CONFIDENCE.get(config.get("name"), 0.0),
        "trading_allowed": bool(config.get("trading_allowed", False)),
    }


LOCAL_SESSIONS = {
    name: _window_to_decimal({**config, "name": name})
    for name, config in SESSIONS.items()
}

LOCAL_KILL_ZONES = {
    name: {
        "start": config["start"][0] + config["start"][1] / 60.0,
        "end": config["end"][0] + config["end"][1] / 60.0,
        "score": 95 if name == "OVERLAP" else 100,
    }
    for name, config in KILL_ZONES.items()
}
LOCAL_SESSIONS["ROLLOVER"] = {
    "start": ROLLOVER_START[0] + ROLLOVER_START[1] / 60.0,
    "end": ROLLOVER_END[0] + ROLLOVER_END[1] / 60.0,
    "confidence": 0.0,
    "trading_allowed": False,
}

FRIDAY_RULES = {
    "risk_reduced_after": float(FRIDAY_RISK_REDUCTION_HOUR),
    "halt_after": float(FRIDAY_TRADING_HALT_HOUR),
}

TOKYO_OVERRIDE_SCORE = 92.0


def _decimal_hour_to_time(hour: float) -> time:
    whole_hour = int(hour) % 24
    minute = int(round((hour - int(hour)) * 60)) % 60
    return time(whole_hour, minute)


def _local_window_unix(current_utc: datetime, start_hour: float, end_hour: float) -> tuple[int, int]:
    local_now = _to_local_time(current_utc)
    start_local = datetime.combine(local_now.date(), _decimal_hour_to_time(start_hour), tzinfo=TZ_LOCAL)
    end_local = datetime.combine(local_now.date(), _decimal_hour_to_time(end_hour), tzinfo=TZ_LOCAL)
    if end_hour <= start_hour:
        end_local += timedelta(days=1)
    return int(start_local.astimezone(timezone.utc).timestamp()), int(end_local.astimezone(timezone.utc).timestamp())


def _publish_visual_layers_agent7(
    current_utc: datetime,
    vp_poc: float,
    vp_vah: float,
    vp_val: float,
) -> None:
    layers = []

    kill_zone_styles = {
        "LONDON_OPEN": ("rgba(59, 130, 246, 0.07)", "London Open KZ"),
        "NY_OPEN": ("rgba(239, 68, 68, 0.07)", "NY Open KZ"),
        "OVERLAP": ("rgba(168, 85, 247, 0.05)", "Overlap KZ"),
    }
    for name, config in LOCAL_KILL_ZONES.items():
        color, label = kill_zone_styles.get(name, ("rgba(148, 163, 184, 0.05)", name))
        start_unix, end_unix = _local_window_unix(current_utc, config["start"], config["end"])
        layers.append(
            VisualBackground(
                time_start=start_unix,
                time_end=end_unix,
                color=color,
                label=label,
            )
        )

    asian_start, asian_end = _local_window_unix(
        current_utc,
        LOCAL_SESSIONS["TOKYO"]["start"],
        LOCAL_SESSIONS["TOKYO"]["end"],
    )
    layers.append(
        VisualBackground(
            time_start=asian_start,
            time_end=asian_end,
            color="rgba(71, 85, 105, 0.06)",
            label="Asian Session",
        )
    )

    session_start_unix, _ = _local_window_unix(current_utc, 0.0, 23.99)
    if vp_poc and vp_poc > 0:
        layers.append(
            VisualHLine(
                time_start=session_start_unix,
                price=vp_poc,
                color="rgba(239, 68, 68, 0.90)",
                style="solid",
                width=2,
                label=f"POC {vp_poc:.2f}",
                label_side="left",
            )
        )
    if vp_vah and vp_vah > 0:
        layers.append(
            VisualHLine(
                time_start=session_start_unix,
                price=vp_vah,
                color="rgba(16, 185, 129, 0.60)",
                style="dashed",
                width=1,
                label=f"VAH {vp_vah:.2f}",
                label_side="left",
            )
        )
    if vp_val and vp_val > 0:
        layers.append(
            VisualHLine(
                time_start=session_start_unix,
                price=vp_val,
                color="rgba(239, 68, 68, 0.60)",
                style="dashed",
                width=1,
                label=f"VAL {vp_val:.2f}",
                label_side="left",
            )
        )

    VISUAL_LAYERS.set_layers("agent_7", layers)


def get_utc_decimal_hour(dt: datetime) -> float:
    return dt.hour + dt.minute / 60.0


def get_local_decimal_hour(utc_time: datetime) -> float:
    local_time = _to_local_time(utc_time)
    return local_time.hour + local_time.minute / 60.0


def _to_local_time(utc_time: datetime) -> datetime:
    if utc_time.tzinfo is None:
        utc_time = utc_time.replace(tzinfo=timezone.utc)
    return utc_time.astimezone(TZ_LOCAL)


def _in_time_range(hour: float, start: float, end: float) -> bool:
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def identify_session(utc_time: datetime) -> str:
    hour = get_local_decimal_hour(utc_time)
    if _in_time_range(hour, LOCAL_SESSIONS["ROLLOVER"]["start"], LOCAL_SESSIONS["ROLLOVER"]["end"]):
        return "ROLLOVER"
    for name in ("LONDON_OPEN", "NY_OPEN", "OVERLAP", "LONDON", "NY", "TOKYO", "ASIA", "OFF_HOURS"):
        config = LOCAL_SESSIONS.get(name)
        if config and _in_time_range(hour, config["start"], config["end"]):
            return name
    return "OFF_HOURS"


def detect_kill_zone(utc_time: datetime) -> dict:
    hour = get_local_decimal_hour(utc_time)
    for name, config in LOCAL_KILL_ZONES.items():
        if _in_time_range(hour, config["start"], config["end"]):
            return {"in_kill_zone": True, "kill_zone_name": name, "kill_zone_score": config["score"]}
    return {"in_kill_zone": False, "kill_zone_name": None, "kill_zone_score": 50}


def get_friday_mode(utc_time: datetime) -> str:
    local_time = _to_local_time(utc_time)
    if local_time.weekday() != 4:
        return "NORMAL"
    hour = local_time.hour + local_time.minute / 60.0
    if hour >= FRIDAY_RULES["halt_after"]:
        return "HALT"
    if hour >= FRIDAY_RULES["risk_reduced_after"]:
        return "REDUCED"
    return "NORMAL"


async def notify_friday_mode_change(blackboard: BlackBoard, previous_mode: str | None, current_mode: str) -> None:
    if current_mode == previous_mode or current_mode == "NORMAL":
        return
    if current_mode == "REDUCED":
        await send_discord_notification(blackboard, "🟡 Friday Mode — Risque réduit à 0.5%")
    elif current_mode == "HALT":
        await send_discord_notification(blackboard, "🔴 Friday Mode — Trading coupé")


def check_session_context(utc_time: datetime) -> dict:
    local_time = _to_local_time(utc_time)
    hour = local_time.hour + local_time.minute / 60.0
    day = local_time.weekday()

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
    session_config = LOCAL_SESSIONS.get(session, LOCAL_SESSIONS["OFF_HOURS"])
    confidence = session_config["confidence"]
    trading_allowed = bool(session_config.get("trading_allowed", False))
    reason = session

    if day == 4 and hour >= FRIDAY_RULES["risk_reduced_after"]:
        confidence = min(confidence, 0.5)
        reason = "FRIDAY_REDUCED_RISK"

    if session in {"ASIA", "TOKYO"}:
        reason = f"TOKYO_ONLY_MIN_SCORE_{TOKYO_OVERRIDE_SCORE:.0f}"
    elif session in {"OFF_HOURS", "ROLLOVER"}:
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
            "shadow_ict_contract": {
                "agent_id": "agent_7",
                "observations": [f"Session={context['session']}, KZ={context['kill_zone_name']}, trading_allowed={context['trading_allowed']}"],
                "score": final_score,
                "confidence": context["confidence"],
                "hard_veto": not (context["trading_allowed"] or context["session"] == "TOKYO"),
                "reason": f"{context['reason']}",
                "uncertainty": "LOW",
                "alternative_scenario": {"scenario": "WAIT_SESSION_OPEN", "condition": "LONDON_OR_NY_OPEN"} if not context["trading_allowed"] else {"scenario": "NONE", "condition": "NONE"},
                "contextual_notes": {
                    "session_label": context["session"],
                    "macro_window": context["kill_zone_name"] if context["in_kill_zone"] else "NONE",
                    "tradable_window": context["trading_allowed"],
                    "low_liquidity_flag": context["session"] in {"ASIA", "TOKYO", "OFF_HOURS", "ROLLOVER"}
                },
                "diagnostic_present": True,
                "not_applicable_reason": "" if context["trading_allowed"] else f"SESSION_{context['session']}_NOT_TRADABLE"
            }
        },
    )


class AgentSessions:
    def __init__(self, blackboard: BlackBoard):
        self.bb = blackboard
        self.logger = get_logger()
        self.name = "agent_7"
        self._last_friday_mode: str | None = None

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
                friday_mode = get_friday_mode(utc_time)
                await notify_friday_mode_change(self.bb, self._last_friday_mode, friday_mode)
                self._last_friday_mode = friday_mode
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
                        "friday_mode": friday_mode,
                        "reason": result.reason,
                    },
                )
                _publish_visual_layers_agent7(
                    current_utc=utc_time,
                    vp_poc=volume_profile.get("poc") or 0.0,
                    vp_vah=volume_profile.get("vah") or 0.0,
                    vp_val=volume_profile.get("val") or 0.0,
                )
                await self.bb.update_market({"session": payload["session_name"]})
                await asyncio.sleep(10)
            except Exception as exc:
                self.logger.error(f"Erreur Agent 7 (Chronos V2): {exc}")
                VISUAL_LAYERS.clear_agent("agent_7")
                await asyncio.sleep(5)


def _p1_safe_dict(value):    return value if isinstance(value, dict) else {}
def _p1_safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_agent_7_observation(result):
    from gold_sniper.strategy.contracts import AgentObservation, EvidenceSource

    if result is None:
        return AgentObservation(
            agent_id="agent_7",
            source=EvidenceSource.SESSION,
            passed=None,
            score=0.0,
            confidence=0.0,
            reason="UNKNOWN",
            payload={
                "schema_version": "p1.agent_observation.v1",
                "agent_id": "agent_7",
                "status": "UNKNOWN",
                "session": "UNKNOWN",
                "trading_allowed": False,
                "unknown_fields": ["agent_result"],
            },
            missing_evidence=["AGENT_7_RESULT_MISSING"],
        )

    payload = _p1_safe_dict(result.payload)
    session = str(payload.get("session_name") or payload.get("session") or "UNKNOWN").upper()
    trading_allowed = bool(payload.get("trading_allowed", result.hard_filter_pass))
    friday_mode = str(payload.get("friday_mode") or "NORMAL").upper()

    missing = []
    if session == "UNKNOWN":
        missing.append("SESSION_UNKNOWN")

    return AgentObservation(
        agent_id="agent_7",
        source=EvidenceSource.SESSION,
        passed=trading_allowed,
        score=_p1_safe_float(result.score),
        confidence=min(max(_p1_safe_float(result.score) / 100.0, 0.0), 1.0),
        reason=str(result.reason or "UNKNOWN"),
        hard_filter_pass=bool(result.hard_filter_pass),
        payload={
            "schema_version": "p1.agent_observation.v1",
            "agent_id": "agent_7",
            "status": "OK" if session != "UNKNOWN" else "UNKNOWN",
            "session": session,
            "session_grade": "HIGH" if session in {"LONDON_OPEN", "NY_OPEN", "OVERLAP"} else "MEDIUM" if session in {"LONDON", "NY"} else "LOW",
            "trading_allowed": trading_allowed,
            "in_kill_zone": bool(payload.get("in_kill_zone", False)),
            "kill_zone_name": payload.get("kill_zone_name"),
            "risk_multiplier": _p1_safe_float(getattr(result, "risk_modifier", 1.0), 1.0),
            "friday_halt": friday_mode == "HALT",
            "friday_mode": friday_mode,
            "unknown_fields": missing,
        },
        missing_evidence=missing,
        warnings=[],
    )

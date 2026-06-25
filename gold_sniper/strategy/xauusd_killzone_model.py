"""New York killzone model for XAUUSD Kasper/ICT shadow reasoning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class XauusdKillzone:
    ny_time: str
    session: str
    session_allowed: bool
    session_quality: str
    reason: str
    session_grade: str = "D"
    session_score: float = 0.0
    risk_multiplier: float = 0.0
    is_hard_block: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_xauusd_killzone(timestamp: Any) -> XauusdKillzone:
    dt = _parse_timestamp(timestamp)
    if dt is None:
        return _result(datetime.fromtimestamp(0, tz=timezone.utc), "UNKNOWN", False, "BLOCKED", "TIMESTAMP_INVALID")
    ny = dt.astimezone(NY_TZ)
    minutes = ny.hour * 60 + ny.minute
    if _between(minutes, 20 * 60, 24 * 60) or _between(minutes, 0, 2 * 60):
        return _result(ny, "ASIA", False, "LOW", "ASIA_MAPPING_ONLY")
    if _between(minutes, 2 * 60, 5 * 60):
        return _result(ny, "LONDON_KILLZONE", True, "HIGH", "LONDON_KILLZONE")
    if _between(minutes, 7 * 60, 10 * 60):
        return _result(ny, "NY_KILLZONE", True, "HIGH", "NY_KILLZONE")
    if _between(minutes, 10 * 60, 11 * 60):
        return _result(ny, "SILVER_BULLET", True, "HIGH", "SILVER_BULLET")
    if _between(minutes, 11 * 60, 12 * 60):
        return _result(ny, "LONDON_CLOSE", True, "MEDIUM", "LONDON_CLOSE")
    return _result(ny, "OFF_SESSION", False, "BLOCKED", "OFF_SESSION")


def _result(ny: datetime, session: str, allowed: bool, quality: str, reason: str) -> XauusdKillzone:
    grade, score, multiplier, hard_block = _session_modulation(session, allowed, quality, reason)
    return XauusdKillzone(ny.isoformat(), session, allowed, quality, reason, grade, score, multiplier, hard_block)


def _session_modulation(session: str, allowed: bool, quality: str, reason: str) -> tuple[str, float, float, bool]:
    session = str(session or "UNKNOWN").upper()
    quality = str(quality or "UNKNOWN").upper()
    reason = str(reason or "UNKNOWN").upper()
    if session in {"ASIA", "TOKYO"}:
        return "D", 0.0, 0.0, True
    if session == "OFF_SESSION" or not allowed:
        return "D", 0.0, 0.0, True
    if reason == "TIMESTAMP_INVALID":
        return "D", 0.0, 0.0, True
    if quality == "HIGH":
        return "A", 90.0, 1.0, False
    if quality == "MEDIUM":
        return "B", 70.0, 0.75, False
    if quality == "LOW":
        return "C", 45.0, 0.4, False
    return "C", 40.0, 0.4, False


def _between(value: int, start: int, end: int) -> bool:
    return start <= value < end


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None

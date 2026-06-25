"""Deterministic replay clock driven by 1m candles."""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ReplayTick:
    index: int
    candle: dict[str, Any]
    ts_utc: datetime
    ts_ny: datetime
    session_label: str
    bar_closed: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ts_utc"] = self.ts_utc.isoformat()
        payload["ts_ny"] = self.ts_ny.isoformat()
        return payload


class ReplayClock:
    def __init__(self, candles_1m: list[dict[str, Any]]):
        if not candles_1m:
            raise ValueError("ReplayClock requires at least one 1m candle")
        self._candles = sorted(candles_1m, key=lambda candle: _as_utc(candle["time"]))
        self.index = -1

    def __iter__(self) -> Iterator[dict[str, Any]]:
        for tick in self.ticks():
            yield tick.candle

    def ticks(self) -> Iterator[ReplayTick]:
        for index, candle in enumerate(self._candles):
            self.index = index
            ts_utc = _as_utc(candle["time"])
            ts_ny = ts_utc.astimezone(NY_TZ)
            yield ReplayTick(
                index=index,
                candle=candle,
                ts_utc=ts_utc,
                ts_ny=ts_ny,
                session_label=session_label_for_ny(ts_ny),
                bar_closed=True,
            )

    @property
    def current_time(self) -> datetime | None:
        if self.index < 0:
            return None
        return _as_utc(self._candles[self.index]["time"])

    @property
    def total(self) -> int:
        return len(self._candles)


def session_label_for_ny(ts_ny: datetime) -> str:
    minutes = ts_ny.hour * 60 + ts_ny.minute
    if 0 <= minutes < 3 * 60:
        return "ASIA"
    if 3 * 60 <= minutes < 7 * 60:
        return "LONDON_PRE"
    if 7 * 60 <= minutes < 10 * 60:
        return "LONDON_OPEN"
    if 10 * 60 <= minutes < 13 * 60:
        return "LONDON_NY_OVERLAP"
    if 13 * 60 <= minutes < 16 * 60:
        return "NY_OPEN"
    if 16 * 60 <= minutes < 20 * 60:
        return "NY_AFTERNOON"
    return "OFF_SESSION"


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

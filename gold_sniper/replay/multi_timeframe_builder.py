"""Deterministic multi-timeframe builder for P1 replay.
Rules:
- input is canonical closed 1m candles
- output only closed higher timeframe candles
- no lookahead
- UTC anchored
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

TIMEFRAME_SECONDS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
}


@dataclass
class MultiTimeframeBuilder:
    timeframes: tuple[str, ...] = ("5m", "15m", "30m", "1H", "4H")

    def __post_init__(self) -> None:
        self._buffers: dict[str, list[dict[str, Any]]] = {tf: [] for tf in self.timeframes}
        self._closed: dict[str, list[dict[str, Any]]] = {tf: [] for tf in self.timeframes}

    def update(self, candle_1m: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        candle = dict(candle_1m)
        candle["time"] = _as_utc(candle["time"])
        emitted: dict[str, list[dict[str, Any]]] = {tf: [] for tf in self.timeframes}
        for tf in self.timeframes:
            self._buffers[tf].append(candle)
            expected = TIMEFRAME_SECONDS[tf] // 60
            if len(self._buffers[tf]) >= expected and _is_tf_close(candle["time"], tf):
                bar = aggregate_candles(self._buffers[tf][-expected:], timeframe=tf)
                self._closed[tf].append(bar)
                emitted[tf].append(bar)
                self._buffers[tf] = []
        return emitted

    def closed(self, timeframe: str) -> list[dict[str, Any]]:
        return list(self._closed.get(timeframe, []))


def aggregate_candles(candles: list[dict[str, Any]], *, timeframe: str) -> dict[str, Any]:
    if not candles:
        raise ValueError("Cannot aggregate empty candles")
    ordered = sorted(candles, key=lambda item: _as_utc(item["time"]))
    return {
        "time": _as_utc(ordered[-1]["time"]),
        "timeframe": timeframe,
        "open": float(ordered[0]["open"]),
        "high": max(float(c["high"]) for c in ordered),
        "low": min(float(c["low"]) for c in ordered),
        "close": float(ordered[-1]["close"]),
        "volume": sum(float(c.get("volume", c.get("tick_volume", 0.0)) or 0.0) for c in ordered),
        "tick_volume": sum(float(c.get("tick_volume", c.get("volume", 0.0)) or 0.0) for c in ordered),
    }


def _is_tf_close(ts_utc: datetime, timeframe: str) -> bool:
    seconds = TIMEFRAME_SECONDS[timeframe]
    epoch = int(ts_utc.timestamp())
    return (epoch + 60) % seconds == 0


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

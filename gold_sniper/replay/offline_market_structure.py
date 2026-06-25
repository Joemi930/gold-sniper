"""Offline market-structure helpers for Phase 7 replay evidence.

All helpers operate on candles available at or before the replay timestamp.
They do not call live services and do not know future outcomes.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Candle:
    time: datetime
    raw_time: str
    open: float
    high: float
    low: float
    close: float
    tick_volume: float | None = None

    @property
    def bullish(self) -> bool:
        return self.close > self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return max(self.high - self.low, 0.0)


def load_candles_from_csv(path: Path, *, max_rows: int | None = None) -> list[Candle]:
    candles: list[Candle] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            candle = candle_from_row(row)
            if candle is not None:
                candles.append(candle)
            if max_rows is not None and len(candles) >= max_rows:
                break
    candles.sort(key=lambda item: item.time)
    return candles


def candle_from_row(row: dict[str, Any]) -> Candle | None:
    dt = parse_time(row.get("time"))
    if dt is None:
        return None
    try:
        return Candle(
            time=dt,
            raw_time=str(row.get("time")),
            open=float(row.get("open")),
            high=float(row.get("high")),
            low=float(row.get("low")),
            close=float(row.get("close")),
            tick_volume=_float_or_none(row.get("tick_volume")),
        )
    except (TypeError, ValueError):
        return None


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def candles_until(candles: list[Candle], timestamp: datetime, *, limit: int | None = None) -> list[Candle]:
    selected = [item for item in candles if item.time <= timestamp]
    return selected[-limit:] if limit is not None else selected


def detect_swings(candles: list[Candle], *, left: int = 2, right: int = 2) -> dict[str, list[dict[str, Any]]]:
    highs: list[dict[str, Any]] = []
    lows: list[dict[str, Any]] = []
    if len(candles) < left + right + 1:
        return {"highs": highs, "lows": lows}
    for idx in range(left, len(candles) - right):
        window = candles[idx - left : idx + right + 1]
        current = candles[idx]
        if current.high == max(item.high for item in window):
            highs.append({"time": current.raw_time, "price": current.high, "index": idx})
        if current.low == min(item.low for item in window):
            lows.append({"time": current.raw_time, "price": current.low, "index": idx})
    return {"highs": highs, "lows": lows}


def infer_bias_from_swings(candles: list[Candle]) -> dict[str, Any]:
    swings = detect_swings(candles)
    highs = swings["highs"][-2:]
    lows = swings["lows"][-2:]
    if len(highs) < 2 or len(lows) < 2:
        return _fallback_bias(candles, swings)
    higher_high = highs[-1]["price"] > highs[-2]["price"]
    higher_low = lows[-1]["price"] > lows[-2]["price"]
    lower_high = highs[-1]["price"] < highs[-2]["price"]
    lower_low = lows[-1]["price"] < lows[-2]["price"]
    if higher_high and higher_low:
        bias = "BULLISH"
    elif lower_high and lower_low:
        bias = "BEARISH"
    else:
        bias = "RANGE"
    return {"bias": bias, "order_flow": bias, "swings": swings}


def _fallback_bias(candles: list[Candle], swings: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    if len(candles) < 6:
        return {"bias": "UNKNOWN", "order_flow": "UNKNOWN", "swings": swings}
    early = candles[: max(3, len(candles) // 3)]
    late = candles[-max(3, len(candles) // 3) :]
    early_high = max(item.high for item in early)
    early_low = min(item.low for item in early)
    late_high = max(item.high for item in late)
    late_low = min(item.low for item in late)
    early_close = sum(item.close for item in early) / len(early)
    late_close = sum(item.close for item in late) / len(late)
    if late_high > early_high and late_low > early_low and late_close > early_close:
        bias = "BULLISH"
    elif late_high < early_high and late_low < early_low and late_close < early_close:
        bias = "BEARISH"
    elif abs(late_close - early_close) <= max((early_high - early_low) * 0.25, 0.0001):
        bias = "RANGE"
    else:
        bias = "UNKNOWN"
    return {"bias": bias, "order_flow": bias, "swings": swings}


def average_true_range(candles: list[Candle], *, period: int = 14) -> float | None:
    if len(candles) < 2:
        return None
    ranges: list[float] = []
    for prev, current in zip(candles[-period - 1 : -1], candles[-period:]):
        ranges.append(max(current.high - current.low, abs(current.high - prev.close), abs(current.low - prev.close)))
    return sum(ranges) / len(ranges) if ranges else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

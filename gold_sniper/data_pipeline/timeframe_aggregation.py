"""Deterministic candle aggregation for offline replay data."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from gold_sniper.replay.historical_data import TIMEFRAME_SECONDS, normalize_timeframe, parse_timestamp


def aggregate_candles(
    candles_1m: list[dict[str, Any]],
    *,
    target_timeframe: str,
) -> list[dict[str, Any]]:
    """Aggregate normalized 1m candles into a higher timeframe."""
    timeframe = normalize_timeframe(target_timeframe)
    if timeframe == "1m":
        return list(candles_1m)
    seconds = TIMEFRAME_SECONDS[timeframe]
    buckets: dict[datetime, list[dict[str, Any]]] = {}
    for candle in sorted(candles_1m, key=lambda item: parse_timestamp(item["time"])):
        ts = parse_timestamp(candle["time"])
        bucket_start = _floor_time(ts, seconds)
        buckets.setdefault(bucket_start, []).append(candle)

    aggregated: list[dict[str, Any]] = []
    for bucket_start in sorted(buckets):
        bucket = buckets[bucket_start]
        aggregated.append({
            "time": bucket_start,
            "open": float(bucket[0]["open"]),
            "high": max(float(item["high"]) for item in bucket),
            "low": min(float(item["low"]) for item in bucket),
            "close": float(bucket[-1]["close"]),
            "volume": sum(float(item.get("volume", item.get("tick_volume", 0.0)) or 0.0) for item in bucket),
            "tick_volume": sum(float(item.get("tick_volume", item.get("volume", 0.0)) or 0.0) for item in bucket),
        })
    return aggregated


def _floor_time(value: datetime, seconds: int) -> datetime:
    ts = parse_timestamp(value)
    epoch = int(ts.timestamp())
    floored = epoch - (epoch % seconds)
    return datetime.fromtimestamp(floored, tz=timezone.utc)

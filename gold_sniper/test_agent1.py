"""Test isolation Agent 1: 120 bougies 4H baissieres => BOS SHORT."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from agents.agent_1_meteo import calculate_agent_1_result


def _bearish_swing_candles(count: int, start_price: float, step: float, tf_minutes: int) -> list[dict]:
    candles: list[dict] = []
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = start_price
    for i in range(count):
        pullback = 0.45 if i % 8 in (3, 4) else 0.0
        open_price = price + pullback
        close_price = price - step + (pullback * 0.35)
        high = max(open_price, close_price) + 0.35 + (0.25 if i % 8 == 4 else 0.0)
        low = min(open_price, close_price) - 0.35 - (0.25 if i % 8 == 7 else 0.0)
        candles.append(
            {
                "time": base_time + timedelta(minutes=tf_minutes * i),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "tick_volume": 100 + i,
                "is_closed": True,
            }
        )
        price -= step
    return candles


async def main() -> None:
    candles_4h = _bearish_swing_candles(120, 2400.0, 0.9, 240)
    candles_15m = _bearish_swing_candles(160, 2300.0, 0.22, 15)
    atr_14 = 1.2

    result, structure_4h, structure_15m = await calculate_agent_1_result(
        candles_4h,
        candles_15m,
        atr_14,
    )

    print(
        "AGENT1_ISOLATION:",
        f"direction={result.direction}",
        f"score={result.score}",
        f"reason={result.reason}",
        f"4H={structure_4h}",
        f"15M={structure_15m}",
    )

    assert result.direction == "SHORT", result
    assert structure_4h["state"] == "BEARISH", structure_4h
    assert structure_15m["state"] == "BEARISH", structure_15m
    assert structure_4h["last_event"] == "BOS", structure_4h
    assert structure_15m["last_event"] == "BOS", structure_15m
    assert result.reason.startswith("MTF_ALIGNED_SHORT"), result.reason


if __name__ == "__main__":
    asyncio.run(main())

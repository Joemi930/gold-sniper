"""Market weather model for XAUUSD Kasper/ICT shadow reasoning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any


@dataclass(frozen=True)
class XauusdMarketWeather:
    regime: str
    ema_200_m15_bias: str
    atr_state: str
    volume_state: str
    trade_permission: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_xauusd_market_weather(candles: list[dict[str, Any]] | None, *, news_veto: bool = False) -> XauusdMarketWeather:
    rows = [row for row in (candles or []) if _num(row.get("close")) is not None]
    if len(rows) < 20:
        return XauusdMarketWeather("UNKNOWN", "NEUTRAL", "UNKNOWN", "UNKNOWN", "WAIT", "INSUFFICIENT_M15_HISTORY")
    closes = [_num(row["close"]) for row in rows if _num(row.get("close")) is not None]
    highs = [_num(row.get("high")) for row in rows if _num(row.get("high")) is not None]
    lows = [_num(row.get("low")) for row in rows if _num(row.get("low")) is not None]
    ema_period = min(200, len(closes))
    ema = _ema(closes[-ema_period:], ema_period)
    recent = closes[-12:]
    above = sum(1 for item in recent if item > ema)
    below = sum(1 for item in recent if item < ema)
    crosses = sum(1 for prev, cur in zip(recent, recent[1:]) if (prev - ema) * (cur - ema) < 0)
    if crosses >= 3:
        ema_bias = "CHOP"
    elif above >= 9:
        ema_bias = "BULLISH"
    elif below >= 9:
        ema_bias = "BEARISH"
    else:
        ema_bias = "NEUTRAL"
    ranges = [h - l for h, l in zip(highs[-20:], lows[-20:]) if h is not None and l is not None]
    atr = mean(ranges) if ranges else 0.0
    baseline = mean([h - l for h, l in zip(highs[-80:], lows[-80:]) if h is not None and l is not None]) if len(highs) >= 20 else atr
    atr_state = "EXTREME" if baseline and atr > baseline * 2.0 else "HIGH" if baseline and atr > baseline * 1.35 else "LOW" if baseline and atr < baseline * 0.6 else "NORMAL"
    volumes = [_num(row.get("tick_volume")) for row in rows if _num(row.get("tick_volume")) is not None]
    volume_state = "UNKNOWN"
    if volumes:
        volume_state = "HIGH" if volumes[-1] > mean(volumes[-20:]) * 1.3 else "LOW" if volumes[-1] < mean(volumes[-20:]) * 0.7 else "NORMAL"
    regime = "CONSOLIDATING" if ema_bias == "CHOP" else "EXPANDING" if atr_state in {"HIGH", "EXTREME"} else "TRENDING" if ema_bias in {"BULLISH", "BEARISH"} else "RANGING"
    permission = "BLOCK" if news_veto and atr_state == "EXTREME" else "WAIT" if ema_bias == "CHOP" or atr_state == "EXTREME" else "ALLOW"
    return XauusdMarketWeather(regime, ema_bias, atr_state, volume_state, permission, "EMA200_M15_ATR_VOLUME")


def _ema(values: list[float], period: int) -> float:
    alpha = 2 / (period + 1)
    current = values[0]
    for value in values[1:]:
        current = value * alpha + current * (1 - alpha)
    return current


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

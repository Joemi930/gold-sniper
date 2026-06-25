"""VWAP/EMA M1 scalp model for XAUUSD shadow scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class VwapM1Scalp:
    available: bool
    ema_200_m15_bias: str
    trick_move_detected: bool
    vwap_available: bool
    vwap_reclaim: bool
    rejection_candle: bool
    scalp_allowed: bool
    quality: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_vwap_m1_scalp(m1_candles: list[dict[str, Any]] | None, *, ema_200_m15_bias: str = "NEUTRAL") -> VwapM1Scalp:
    rows = [_row(row) for row in (m1_candles or [])]
    rows = [row for row in rows if row is not None]
    if len(rows) < 3:
        return VwapM1Scalp(False, ema_200_m15_bias, False, False, False, False, False, "MISSING")
    vwap = _vwap(rows)
    prev, current = rows[-2], rows[-1]
    reclaim_long = prev["close"] < vwap and current["close"] > vwap and ema_200_m15_bias == "BULLISH"
    reclaim_short = prev["close"] > vwap and current["close"] < vwap and ema_200_m15_bias == "BEARISH"
    wick_ratio = (current["high"] - max(current["open"], current["close"]) if reclaim_short else min(current["open"], current["close"]) - current["low"]) / max(current["high"] - current["low"], 0.0001)
    rejection = wick_ratio >= 0.35
    trick = abs(prev["close"] - vwap) / max(vwap, 0.0001) < 0.002 and (reclaim_long or reclaim_short)
    allowed = bool((reclaim_long or reclaim_short) and rejection and trick)
    return VwapM1Scalp(True, ema_200_m15_bias, trick, True, reclaim_long or reclaim_short, rejection, allowed, "HIGH" if allowed else "LOW")


def _vwap(rows: list[dict[str, float]]) -> float:
    total_volume = sum(row["volume"] for row in rows) or len(rows)
    return sum(((row["high"] + row["low"] + row["close"]) / 3.0) * row["volume"] for row in rows) / total_volume


def _row(row: dict[str, Any]) -> dict[str, float] | None:
    try:
        return {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("tick_volume") or row.get("volume") or 1.0),
        }
    except (KeyError, TypeError, ValueError):
        return None

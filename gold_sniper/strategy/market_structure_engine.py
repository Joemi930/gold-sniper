"""BOS/CHoCH/sweep engine for deterministic Kasper/ICT shadow reasoning."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class MarketStructureResult:
    bos: bool
    bos_direction: str
    choch: bool
    choch_direction: str
    protected_high: float | None
    protected_low: float | None
    sweep: bool
    sweep_side: str
    body_close_confirmed: bool
    double_choch_range: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_market_structure(
    candles: list[dict[str, Any]] | None,
    *,
    protected_high: float | None = None,
    protected_low: float | None = None,
    previous_choch_direction: str | None = None,
) -> MarketStructureResult:
    rows = [_normalize(row) for row in (candles or [])]
    rows = [row for row in rows if row is not None]
    if len(rows) < 3:
        return MarketStructureResult(False, "NONE", False, "NONE", protected_high, protected_low, False, "NONE", False, False)
    prev = rows[-2]
    current = rows[-1]
    ph = protected_high if protected_high is not None else max(row["high"] for row in rows[-10:-1])
    pl = protected_low if protected_low is not None else min(row["low"] for row in rows[-10:-1])
    bos_up = current["close"] > ph
    bos_down = current["close"] < pl
    wick_up = current["high"] > ph and current["close"] <= ph
    wick_down = current["low"] < pl and current["close"] >= pl
    choch_up = bos_up and prev["close"] < ph
    choch_down = bos_down and prev["close"] > pl
    direction = "BULLISH" if bos_up else "BEARISH" if bos_down else "NONE"
    choch_direction = "BULLISH" if choch_up else "BEARISH" if choch_down else "NONE"
    # Double CHoCH is a range warning: two opposing structure shifts without
    # enough expansion context should not be treated as a clean entry signal.
    double_choch = bool(previous_choch_direction and choch_direction != "NONE" and previous_choch_direction != choch_direction)
    return MarketStructureResult(
        bos=bos_up or bos_down,
        bos_direction=direction,
        choch=choch_up or choch_down,
        choch_direction=choch_direction,
        protected_high=ph,
        protected_low=pl,
        sweep=wick_up or wick_down,
        sweep_side="BUY_SIDE" if wick_up else "SELL_SIDE" if wick_down else "NONE",
        body_close_confirmed=bos_up or bos_down,
        double_choch_range=double_choch,
    )


def _normalize(row: dict[str, Any]) -> dict[str, float] | None:
    try:
        return {key: float(row[key]) for key in ("open", "high", "low", "close")}
    except (KeyError, TypeError, ValueError):
        return None

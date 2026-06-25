"""Conservative fill model for offline replay simulations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from replay.execution_model import ReplayExecutionModel


@dataclass(frozen=True)
class FillResult:
    filled: bool
    fill_price: float | None
    reason: str
    spread_points: float
    slippage_points: float
    commission: float
    model: str
    conservative: bool
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply_entry_costs(
    *,
    side: str,
    requested_entry: float,
    execution_model: ReplayExecutionModel,
    news_blocked_or_near: bool = False,
    volume: float = 0.0,
) -> FillResult:
    side = str(side).upper()
    spread = execution_model.spread_points(news_blocked_or_near=news_blocked_or_near)
    slippage = execution_model.slippage_for_event(news_blocked_or_near=news_blocked_or_near)
    if side == "BUY":
        fill = float(requested_entry) + spread / 2.0 + slippage
    elif side == "SELL":
        fill = float(requested_entry) - spread / 2.0 - slippage
    else:
        return FillResult(False, None, "INVALID_SIDE", spread, slippage, 0.0, execution_model.fill_model, True, {})
    commission = execution_model.commission_for_volume(volume, sides=1)
    return FillResult(
        filled=True,
        fill_price=round(fill, 6),
        reason="ENTRY_FILLED_WITH_COSTS",
        spread_points=spread,
        slippage_points=slippage,
        commission=commission,
        model=execution_model.fill_model,
        conservative=True,
        metadata={"requested_entry": requested_entry, "side": side, "news_blocked_or_near": news_blocked_or_near},
    )


def apply_exit_costs(
    *,
    side: str,
    requested_exit: float,
    execution_model: ReplayExecutionModel,
    news_blocked_or_near: bool = False,
    volume: float = 0.0,
    reason: str = "EXIT",
) -> FillResult:
    side = str(side).upper()
    spread = execution_model.spread_points(news_blocked_or_near=news_blocked_or_near)
    slippage = execution_model.slippage_for_event(news_blocked_or_near=news_blocked_or_near)
    if side == "BUY":
        fill = float(requested_exit) - spread / 2.0 - slippage
    elif side == "SELL":
        fill = float(requested_exit) + spread / 2.0 + slippage
    else:
        return FillResult(False, None, "INVALID_SIDE", spread, slippage, 0.0, execution_model.fill_model, True, {})
    commission = execution_model.commission_for_volume(volume, sides=1)
    return FillResult(
        filled=True,
        fill_price=round(fill, 6),
        reason=f"{reason}_FILLED_WITH_COSTS",
        spread_points=spread,
        slippage_points=slippage,
        commission=commission,
        model=execution_model.fill_model,
        conservative=True,
        metadata={"requested_exit": requested_exit, "side": side, "news_blocked_or_near": news_blocked_or_near},
    )


def resolve_intrabar_exit_priority(
    *,
    side: str,
    candle: dict[str, Any],
    sl: float,
    tp1: float | None = None,
    tp2: float | None = None,
    protected_sl: float | None = None,
    partial_closed: bool = False,
) -> tuple[str | None, float | None]:
    """Return the conservative first event for a candle."""
    side = str(side).upper()
    high = float(candle["high"])
    low = float(candle["low"])
    if side == "BUY":
        if not partial_closed:
            if low <= sl:
                return "SL", sl
            if tp1 is not None and high >= tp1:
                return "TP1", tp1
            return None, None
        psl = protected_sl if protected_sl is not None else sl
        if low <= psl:
            return "PROTECTED_SL", psl
        if tp2 is not None and high >= tp2:
            return "TP2", tp2
        return None, None
    if side == "SELL":
        if not partial_closed:
            if high >= sl:
                return "SL", sl
            if tp1 is not None and low <= tp1:
                return "TP1", tp1
            return None, None
        psl = protected_sl if protected_sl is not None else sl
        if high >= psl:
            return "PROTECTED_SL", psl
        if tp2 is not None and low <= tp2:
            return "TP2", tp2
        return None, None
    return None, None

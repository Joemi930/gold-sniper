"""Conservative offline trade outcome simulator for Phase 7 ENTER rows."""

from __future__ import annotations

from enum import Enum
from typing import Any

from replay.offline_market_structure import Candle, parse_time


class SimulatedTradeStatus(str, Enum):
    FILLED = "FILLED"
    TP1 = "TP1"
    TP2 = "TP2"
    SL = "SL"
    PARTIAL_CLOSED = "PARTIAL_CLOSED"
    MISSED_ENTRY = "MISSED_ENTRY"
    EXPIRED = "EXPIRED"


def evaluate_limit_entry_fill(
    *,
    side: str,
    limit_price: float,
    candle: dict[str, Any],
    spread: float = 0.0,
) -> bool:
    """Return whether a limit order is realistically touched by bid/ask."""
    high = float(candle["high"])
    low = float(candle["low"])
    side_u = str(side).upper()
    if side_u in {"BUY", "LONG"}:
        ask_low = low + float(spread)
        return ask_low <= float(limit_price)
    if side_u in {"SELL", "SHORT"}:
        bid_high = high - float(spread)
        return bid_high >= float(limit_price)
    return False


def simulate_order_or_trade(order: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Simulate a single order enough to validate faithful limit-entry handling."""
    side = str(order.get("side") or order.get("signal") or "").upper()
    entry_type = str(order.get("entry_type") or order.get("order_type") or "MARKET").upper()
    spread = float(order.get("spread") or 0.0)
    expiry_bars = max(1, int(order.get("entry_expiry_bars") or 3))
    limit_price = _float_or_none(order.get("entry_price") or order.get("limit_price"))
    checked = list(candles or [])[:expiry_bars]

    if entry_type == "LIMIT":
        if limit_price is None or side not in {"BUY", "LONG", "SELL", "SHORT"}:
            return {
                "status": "REJECTED",
                "reason": "INVALID_LIMIT_ORDER",
                "filled": False,
                "pnl": 0.0,
                "exposure": 0.0,
            }
        for candle in checked:
            if evaluate_limit_entry_fill(side=side, limit_price=limit_price, candle=candle, spread=spread):
                return {
                    "status": SimulatedTradeStatus.FILLED.value,
                    "reason": "LIMIT_TOUCHED_WITH_SPREAD",
                    "side": side,
                    "limit_price": float(limit_price),
                    "spread": spread,
                    "expiry_bars": expiry_bars,
                    "filled": True,
                    "fill_time": str(candle.get("time")),
                    "pnl": 0.0,
                    "exposure": float(order.get("volume") or order.get("lot") or 1.0),
                }
        return {
            "status": SimulatedTradeStatus.MISSED_ENTRY.value,
            "reason": "LIMIT_NOT_TOUCHED_WITH_SPREAD",
            "side": side,
            "limit_price": float(limit_price),
            "spread": spread,
            "expiry_bars": expiry_bars,
            "first_candle_time": str(checked[0].get("time")) if checked else None,
            "last_checked_candle_time": str(checked[-1].get("time")) if checked else None,
            "filled": False,
            "pnl": 0.0,
            "exposure": 0.0,
        }

    return {
        "status": SimulatedTradeStatus.FILLED.value,
        "reason": "MARKET_PROXY_FILLED",
        "side": side,
        "filled": True,
        "pnl": 0.0,
        "exposure": float(order.get("volume") or order.get("lot") or 1.0),
    }


def simulate_enter_outcomes(decisions: list[dict[str, Any]], candles_1m: list[Candle], candles_15m: list[Candle]) -> dict[str, Any]:
    enters = [item for item in decisions if _is_enter_decision(item.get("decision"))]
    if not enters:
        return _empty("NOT_AVAILABLE_NO_ENTER_SHADOW")
    results: list[float | None] = []
    for decision in enters:
        results.append(_simulate_one(decision, candles_1m or candles_15m))
    known = [item for item in results if item is not None]
    wins = sum(1 for item in known if item and item > 0)
    losses = sum(1 for item in known if item and item < 0)
    breakeven = sum(1 for item in known if item == 0)
    unknown = len(results) - len(known)
    if not known:
        data = _empty("NOT_AVAILABLE_NO_SL_TP_MODEL")
        data["total_simulated_trades"] = len(enters)
        data["unknown_outcome"] = len(enters)
        return data
    gross_win = sum(item for item in known if item and item > 0)
    gross_loss = abs(sum(item for item in known if item and item < 0))
    return {
        "total_simulated_trades": len(enters),
        "filled_trades": len(known),
        "missed_entries": 0,
        "skipped_signals": 0,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "unknown_outcome": unknown,
        "win_rate": round(wins / len(known), 4) if known else None,
        "average_R": round(sum(known) / len(known), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else None,
        "max_drawdown_R": round(_max_drawdown(known), 4),
        "expectancy_R": round(sum(known) / len(known), 4),
        "best_trade_R": round(max(known), 4),
        "worst_trade_R": round(min(known), 4),
        "consecutive_losses_max": _max_consecutive_losses(known),
        "simulation_status": "SIMULATED_FROM_OFFLINE_CANDLES",
    }


def _simulate_one(decision: dict[str, Any], candles: list[Candle]) -> float | None:
    timestamp = parse_time(decision.get("timestamp"))
    entry = _float_or_none(decision.get("close"))
    poi_low = _float_or_none(decision.get("poi_low"))
    poi_high = _float_or_none(decision.get("poi_high"))
    direction = str(decision.get("direction") or "").upper()
    if timestamp is None or entry is None or poi_low is None or poi_high is None or direction not in {"LONG", "SHORT"}:
        return None
    risk = entry - poi_low if direction == "LONG" else poi_high - entry
    if risk <= 0:
        return None
    stop = poi_low if direction == "LONG" else poi_high
    target = entry + 2 * risk if direction == "LONG" else entry - 2 * risk
    future = [item for item in candles if item.time > timestamp][:480]
    for candle in future:
        if direction == "LONG":
            if candle.low <= stop:
                return -1.0
            if candle.high >= target:
                return 2.0
        else:
            if candle.high >= stop:
                return -1.0
            if candle.low <= target:
                return 2.0
    return None


def _empty(status: str) -> dict[str, Any]:
    return {
        "total_simulated_trades": 0,
        "filled_trades": 0,
        "missed_entries": 0,
        "skipped_signals": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "unknown_outcome": 0,
        "win_rate": None,
        "average_R": None,
        "profit_factor": None,
        "max_drawdown_R": None,
        "expectancy_R": None,
        "best_trade_R": None,
        "worst_trade_R": None,
        "consecutive_losses_max": 0,
        "simulation_status": status,
    }


def _max_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _max_consecutive_losses(values: list[float]) -> int:
    current = 0
    max_seen = 0
    for value in values:
        if value < 0:
            current += 1
            max_seen = max(max_seen, current)
        else:
            current = 0
    return max_seen


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_enter_decision(value: Any) -> bool:
    return str(value or "").upper() in {"ENTER", "ENTER_FULL", "ENTER_REDUCED"}

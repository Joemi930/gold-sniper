from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

from config import (
    MAX_SPREAD_KILL_ZONE,
    MAX_SPREAD_POINTS,
    MAX_SPREAD_RATIO_PCT,
    ROLLOVER_END,
    ROLLOVER_START,
    SPREAD_ALERT_AFTER_SECONDS,
    SYMBOL,
)
from core.blackboard import BLACKBOARD, BlackBoard
from utils.decision_logger import log_execution_block
from utils.logger import get_logger
from utils.telegram_notifier import send_telegram_notification


logger = get_logger()


def _decimal_hour(now: datetime) -> float:
    return now.hour + now.minute / 60.0 + now.second / 3600.0


def _tuple_to_hour(value: tuple[int, int]) -> float:
    return float(value[0]) + float(value[1]) / 60.0


def _in_wrapped_range(hour: float, start: float, end: float) -> bool:
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end


def is_rollover_time(now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return _in_wrapped_range(
        _decimal_hour(now),
        _tuple_to_hour(ROLLOVER_START),
        _tuple_to_hour(ROLLOVER_END),
    )


def _news_blackout_active(blackboard: BlackBoard) -> bool:
    try:
        gate = blackboard.read_sync("risk_management.volatility_gate") or {}
        agent_6 = blackboard.get_agent("agent_6")
    except KeyError:
        return False
    return bool(
        gate.get("news_blackout")
        or gate.get("allow_trade") is False and gate.get("next_news_time")
        or agent_6.get("blocked")
        or agent_6.get("veto")
        or agent_6.get("impact_level") == "HIGH"
    )


def _read_symbol_point(symbol: str, blackboard: BlackBoard) -> float:
    if mt5 is not None:
        info = mt5.symbol_info(symbol)
        point = float(getattr(info, "point", 0.0) or 0.0) if info else 0.0
        if point > 0:
            return point
    try:
        point = float(blackboard.read_sync("market_data.symbol_info.point_size") or 0.0)
        return point if point > 0 else 0.01
    except KeyError:
        return 0.01


def _read_tick(symbol: str, blackboard: BlackBoard, tick: Any | None = None) -> dict | None:
    if tick is not None:
        if isinstance(tick, dict):
            return tick
        return {
            "bid": float(getattr(tick, "bid", 0.0) or 0.0),
            "ask": float(getattr(tick, "ask", 0.0) or 0.0),
        }

    if mt5 is not None:
        mt5_tick = mt5.symbol_info_tick(symbol)
        if mt5_tick is not None:
            return {
                "bid": float(getattr(mt5_tick, "bid", 0.0) or 0.0),
                "ask": float(getattr(mt5_tick, "ask", 0.0) or 0.0),
            }

    try:
        return blackboard.read_sync("market_data.current_tick")
    except KeyError:
        return None


def check_spread(
    symbol: str = SYMBOL,
    blackboard: BlackBoard = BLACKBOARD,
    tick: Any | None = None,
    now: datetime | None = None,
) -> dict:
    """Verifie le spread courant avant execution."""
    now = now or datetime.now(timezone.utc)
    current_tick = _read_tick(symbol, blackboard, tick)
    point = _read_symbol_point(symbol, blackboard)

    if not current_tick:
        return {
            "allow_trade": False,
            "spread": None,
            "max_allowed": MAX_SPREAD_POINTS,
            "reason": "SPREAD_TICK_UNAVAILABLE",
            "rollover_detected": is_rollover_time(now),
            "news_detected": _news_blackout_active(blackboard),
        }

    bid = float(current_tick.get("bid", 0.0) or 0.0)
    ask = float(current_tick.get("ask", 0.0) or 0.0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return {
            "allow_trade": False,
            "spread": None,
            "max_allowed": MAX_SPREAD_POINTS,
            "reason": "SPREAD_TICK_INVALID",
            "rollover_detected": is_rollover_time(now),
            "news_detected": _news_blackout_active(blackboard),
        }

    spread_points = round((ask - bid) / point, 1)
    agent_7 = blackboard.get_agent("agent_7")
    in_kill_zone = bool(agent_7.get("in_kill_zone"))
    max_allowed = MAX_SPREAD_KILL_ZONE if in_kill_zone else MAX_SPREAD_POINTS
    rollover = is_rollover_time(now)
    news = _news_blackout_active(blackboard)

    result = {
        "allow_trade": True,
        "spread": spread_points,
        "max_allowed": max_allowed,
        "spread_ratio_pct": None,
        "reason": f"SPREAD_OK - {spread_points} pts",
        "rollover_detected": rollover,
        "news_detected": news,
        "in_kill_zone": in_kill_zone,
        "checked_at": now.isoformat(),
    }

    if spread_points > max_allowed:
        context = []
        if rollover:
            context.append("ROLLOVER")
        if news:
            context.append("NEWS")
        suffix = f" ({'+'.join(context)})" if context else ""
        result.update(
            {
                "allow_trade": False,
                "reason": f"SPREAD_TOO_HIGH{suffix} - {spread_points} pts > {max_allowed} pts",
            }
        )
        return result

    try:
        atr = blackboard.get_market().get("atr_14_1m") or blackboard.get_market().get("atr_14_15m")
        if atr:
            spread_ratio = spread_points / (float(atr) / point) * 100.0
            result["spread_ratio_pct"] = round(spread_ratio, 2)
            if spread_ratio > MAX_SPREAD_RATIO_PCT:
                result.update(
                    {
                        "allow_trade": False,
                        "reason": f"SPREAD_RATIO_HIGH - {spread_ratio:.1f}% ATR > {MAX_SPREAD_RATIO_PCT:.1f}%",
                    }
                )
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    return result


class SpreadMonitor:
    def __init__(self, blackboard: BlackBoard = BLACKBOARD, symbol: str = SYMBOL):
        self.blackboard = blackboard
        self.symbol = symbol

    async def check_before_entry(self, signal_data: dict | None = None, tick: Any | None = None) -> dict:
        result = check_spread(self.symbol, self.blackboard, tick=tick)
        await self._publish(result)

        if not result["allow_trade"]:
            logger.warning(result["reason"])
            await log_execution_block(result["reason"], signal_data=signal_data, details=result)
            await self._maybe_alert(result)

        return result

    async def _publish(self, result: dict) -> None:
        previous = self.blackboard.get_market().get("spread_monitor", {}) or {}
        high_since = previous.get("high_since")
        last_alert_at = previous.get("last_alert_at")

        if not result["allow_trade"]:
            high_since = high_since or datetime.now(timezone.utc).isoformat()
        else:
            high_since = None
            last_alert_at = None

        await self.blackboard.update_market(
            {
                "spread_points": result.get("spread"),
                "spread_monitor": {
                    **result,
                    "high_since": high_since,
                    "last_alert_at": last_alert_at,
                },
            }
        )

    async def _maybe_alert(self, result: dict) -> bool:
        monitor = self.blackboard.get_market().get("spread_monitor", {}) or {}
        high_since_raw = monitor.get("high_since")
        if not high_since_raw:
            return False

        try:
            high_since = datetime.fromisoformat(high_since_raw)
        except ValueError:
            return False

        now = datetime.now(timezone.utc)
        if high_since.tzinfo is None:
            high_since = high_since.replace(tzinfo=timezone.utc)
        if (now - high_since).total_seconds() < SPREAD_ALERT_AFTER_SECONDS:
            return False

        last_alert_raw = monitor.get("last_alert_at")
        if last_alert_raw:
            try:
                last_alert = datetime.fromisoformat(last_alert_raw)
                if last_alert.tzinfo is None:
                    last_alert = last_alert.replace(tzinfo=timezone.utc)
                if (now - last_alert).total_seconds() < SPREAD_ALERT_AFTER_SECONDS:
                    return False
            except ValueError:
                pass

        await send_telegram_notification(
            self.blackboard,
            (
                "ALERTE SPREAD ELEVE\n"
                f"{self.symbol}: {result.get('spread')} pts > {result.get('max_allowed')} pts\n"
                f"Depuis: {high_since.isoformat()}\n"
                f"Contexte: rollover={result.get('rollover_detected')} news={result.get('news_detected')}"
            ),
        )
        await self.blackboard.update_market(
            {
                "spread_monitor": {
                    **monitor,
                    "last_alert_at": now.isoformat(),
                }
            }
        )
        return True

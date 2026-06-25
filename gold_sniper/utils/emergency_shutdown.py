import asyncio
from typing import Any

from execution.mt5_runtime import mt5

from config import MAGIC_NUMBER, MAX_SLIPPAGE_POINTS, SYMBOL
from execution.broker_gateway import BrokerAction, BrokerGateway
from utils.logger import get_logger
from utils.discord_notifier import send_discord_notification


SHUTDOWN_CONFIRMATION = "Gold Sniper arrêté proprement."


def _matches_gold_sniper_position(position: Any, active_tickets: set[int]) -> bool:
    ticket = int(getattr(position, "ticket", 0) or 0)
    magic = int(getattr(position, "magic", 0) or 0)
    return ticket in active_tickets or magic == MAGIC_NUMBER


def _record_ticket(record: Any) -> int | None:
    if isinstance(record, dict):
        value = record.get("ticket")
    else:
        value = getattr(record, "ticket", None)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _close_open_positions(blackboard) -> dict:
    logger = get_logger()
    active_trades = blackboard.read_sync("active_trades") or {}
    active_tickets = {int(ticket) for ticket in active_trades.keys()}

    positions = await asyncio.to_thread(mt5.positions_get, symbol=SYMBOL)
    if positions is None:
        logger.warning("Arrêt d'urgence: impossible de lire les positions MT5.")
        return {"seen": 0, "closed": 0, "failed": 0}

    gold_positions = [
        position for position in positions
        if _matches_gold_sniper_position(position, active_tickets)
    ]
    closed = 0
    failed = 0
    closed_tickets: set[int] = set()
    broker_gateway = BrokerGateway(blackboard)

    for position in gold_positions:
        tick = await asyncio.to_thread(mt5.symbol_info_tick, position.symbol)
        if tick is None:
            failed += 1
            logger.error(f"Arrêt d'urgence: tick indisponible pour {position.symbol}.")
            continue

        close_type = (
            mt5.ORDER_TYPE_SELL
            if position.type == mt5.POSITION_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": position.ticket,
            "symbol": position.symbol,
            "volume": float(position.volume),
            "type": close_type,
            "price": float(close_price),
            "deviation": MAX_SLIPPAGE_POINTS,
            "magic": MAGIC_NUMBER,
            "comment": "GoldSniper emergency stop",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = await broker_gateway.send_order(
            BrokerAction.EMERGENCY_CLOSE,
            request,
            context={"source": "emergency_shutdown", "ticket": int(position.ticket)},
        )
        if getattr(result, "allowed", True) is False:
            failed += 1
            logger.warning(
                f"Arrêt d'urgence: fermeture broker bloquee pour {position.ticket} "
                f"({getattr(result, 'comment', '')})."
            )
            continue
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            closed += 1
            closed_tickets.add(int(position.ticket))
            logger.warning(f"Arrêt d'urgence: position {position.ticket} fermée.")
        else:
            failed += 1
            retcode = getattr(result, "retcode", "UNKNOWN")
            logger.error(f"Arrêt d'urgence: fermeture échouée pour {position.ticket} ({retcode}).")

    if closed_tickets:
        async with blackboard._lock:
            active = blackboard._data.setdefault("active_trades", {})
            for key, trade in list(active.items()):
                ticket = _record_ticket(trade)
                if ticket is None:
                    try:
                        ticket = int(key)
                    except (TypeError, ValueError):
                        ticket = None
                if ticket in closed_tickets:
                    active.pop(key, None)

            positions_state = blackboard._data.setdefault("positions", {})
            open_positions = positions_state.get("open_positions", [])
            positions_state["open_positions"] = [
                position for position in open_positions
                if _record_ticket(position) not in closed_tickets
            ]

    return {"seen": len(gold_positions), "closed": closed, "failed": failed}


async def emergency_shutdown(
    blackboard,
    reason: str = "MANUAL",
    notify: bool = True,
    close_positions: bool = True,
) -> dict:
    logger = get_logger()

    meta = blackboard.read_sync("meta") or {}
    if meta.get("shutdown_in_progress"):
        return {"already_running": True, "positions": {"seen": 0, "closed": 0, "failed": 0}}

    async with blackboard._lock:
        blackboard._data.setdefault("meta", {})["shutdown_in_progress"] = True
        blackboard._data["meta"]["shutdown_reason"] = reason
        blackboard._data["meta"]["state"] = "STOPPING"
        blackboard._data["trade_signals"] = {}

    logger.critical(f"Arrêt d'urgence déclenché: {reason}")
    positions_summary = {"seen": 0, "closed": 0, "failed": 0}
    if close_positions:
        positions_summary = await _close_open_positions(blackboard)

    blackboard.trigger_kill()

    if notify:
        await send_discord_notification(blackboard, SHUTDOWN_CONFIRMATION)

    logger.critical(
        "Arrêt propre terminé: "
        f"{positions_summary['closed']}/{positions_summary['seen']} position(s) fermée(s), "
        f"{positions_summary['failed']} échec(s)."
    )
    return {"already_running": False, "positions": positions_summary}

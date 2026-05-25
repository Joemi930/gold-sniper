import asyncio
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

import MetaTrader5 as mt5

sys.path.insert(0, ".")

from config import (
    LIVE_MODE,
    LOG_BACKUP_COUNT,
    LOG_DIR,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    SYMBOL,
    WATCHDOG_HEARTBEAT_FILE,
    WATCHDOG_HEARTBEAT_INTERVAL,
)
from core.blackboard import BlackBoard
from core.engine import run_engine
from core.mt5_bridge import bridge
from core.orchestrator import EXECUTION_THRESHOLD
from core.recovery_manager import load_daily_stats_from_recovery, load_recovery_metadata, recover_open_positions
from data.historical_loader import TIMEFRAMES, dataframe_to_candles, get_warmup_data, preload_historical_data
from utils.emergency_shutdown import emergency_shutdown
from utils.logger import get_logger, setup_logger
from utils.system_tray import run_system_tray
from utils.telegram_commander import telegram_command_loop
from utils.telegram_notifier import send_eod_report, send_telegram_notification


async def cold_start(blackboard: BlackBoard) -> bool:
    logger = get_logger()
    boot_time = datetime.now(timezone.utc)
    recovery_meta = load_recovery_metadata()
    logger.info("=" * 50)
    logger.info("COLD START - sequence de bootstrap")
    logger.info("=" * 50)

    logger.info("Phase 1: connexion MT5")
    if not await bridge.connect():
        return False

    logger.info("Phase 2: recovery manager")
    saved_daily = load_daily_stats_from_recovery()
    if saved_daily:
        async with blackboard._lock:
            blackboard._data.setdefault("daily_stats", {}).update(saved_daily)
        logger.info(
            "Stats journalieres rechargees: "
            f"{saved_daily.get('realized_pnl', 0):.2f}$"
        )

    recovered = await recover_open_positions(blackboard)
    if recovery_meta:
        await send_telegram_notification(
            blackboard,
            "PC REDEMARRE APRES COUPURE\n"
            f"Derniere sauvegarde/extinction estimee: {recovery_meta.get('saved_at', 'N/A')}\n"
            f"Redemarrage: {boot_time.isoformat()}\n"
            f"Etat precedent: {recovery_meta.get('state', 'UNKNOWN')}\n"
            f"Positions recuperees: {recovered} "
            f"(snapshot actif: {recovery_meta.get('active_trade_count', 0)})",
        )
    if recovered > 0:
        logger.warning(f"{recovered} position(s) orpheline(s) recuperee(s).")
        await send_telegram_notification(
            blackboard,
            f"Recovery - {recovered} position(s) orpheline(s) recuperee(s) au redemarrage.",
        )
    else:
        logger.info("Aucune position orpheline.")

    logger.info("Phase 3: prechargement historique 6 mois multi-timeframes")
    try:
        historical = await asyncio.to_thread(preload_historical_data)
        candle_store = blackboard._data.setdefault("market_data", {}).setdefault("candles", {})
        for tf_name, meta in TIMEFRAMES.items():
            df = get_warmup_data(tf_name, lookback=1000)
            if df.empty:
                logger.warning(f"{tf_name}: aucun cache warmup disponible.")
                continue
            bb_key = str(meta["bb_key"])
            candles = dataframe_to_candles(df)
            candle_store[bb_key] = deque(candles, maxlen=max(1000, len(candles)))
            logger.info(
                f"{tf_name}: {len(candles)} bougies warmup ingerees "
                f"(cache total={len(historical.get(tf_name, []))})."
            )
    except Exception as exc:
        logger.error(f"Prechargement historique 6 mois echoue: {exc}")
        candles_15m = await bridge.get_historical_candles_async(SYMBOL, mt5.TIMEFRAME_M15, 1000)
        if candles_15m:
            blackboard._data.setdefault("market_data", {}).setdefault("candles", {})["15m"] = deque(
                candles_15m,
                maxlen=1000,
            )
            logger.info(f"15m: {len(candles_15m)} bougies ingerees via fallback bridge.")
        candles_4h = await bridge.get_historical_candles_async(SYMBOL, mt5.TIMEFRAME_H4, 1000)
        if candles_4h:
            blackboard._data.setdefault("market_data", {}).setdefault("candles", {})["4H"] = deque(
                candles_4h,
                maxlen=1000,
            )
            logger.info(f"4H: {len(candles_4h)} bougies ingerees via fallback bridge.")

    symbol_info = await bridge.get_symbol_info_async(SYMBOL)
    if symbol_info:
        await blackboard.write("market_data.symbol_info", symbol_info)
        logger.info(
            "Infos symbole: "
            f"spread={symbol_info.get('spread', '?')} "
            f"point={symbol_info.get('point_size', '?')}"
        )
    else:
        logger.warning("Echec de recuperation des infos symbole.")

    async with blackboard._lock:
        blackboard._data.setdefault(
            "daily_stats",
            {
                "realized_pnl": 0.0,
                "floating_pnl": 0.0,
                "trades_closed": 0,
                "drawdown_halt": False,
            },
        )

    await blackboard.write("meta.state", "READY")
    logger.info(f"COLD START termine - Mode: {'LIVE' if LIVE_MODE else 'PAPER'}")

    await send_telegram_notification(
        blackboard,
        "Gold Sniper V2 demarre\n"
        f"Mode: {'LIVE' if LIVE_MODE else 'PAPER'}\n"
        f"Symbole: {SYMBOL} | Seuil: {EXECUTION_THRESHOLD:.0f}/100",
    )
    return True


async def async_main(blackboard: BlackBoard, stop_event: threading.Event) -> None:
    logger = get_logger()

    async def write_external_watchdog_heartbeat() -> None:
        heartbeat_path = WATCHDOG_HEARTBEAT_FILE
        while not stop_event.is_set() and not blackboard.kill_event.is_set():
            try:
                with open(heartbeat_path, "w", encoding="utf-8") as file:
                    file.write(datetime.now(timezone.utc).isoformat())
            except Exception as exc:
                logger.warning(f"Ecriture heartbeat watchdog impossible: {exc}")
            await asyncio.sleep(max(1, WATCHDOG_HEARTBEAT_INTERVAL))

    async def watch_stop_event() -> None:
        await asyncio.to_thread(stop_event.wait)
        logger.warning("Arret local demande.")
        try:
            await send_eod_report(blackboard)
        except Exception:
            pass
        await emergency_shutdown(blackboard, reason="LOCAL_STOP")

    asyncio.create_task(watch_stop_event())
    asyncio.create_task(write_external_watchdog_heartbeat())
    asyncio.create_task(telegram_command_loop(blackboard, on_kill=stop_event.set))

    boot_ok = await cold_start(blackboard)
    if not boot_ok:
        logger.critical("COLD START echoue - arret du systeme")
        await emergency_shutdown(blackboard, reason="BOOT_FAILED", notify=False, close_positions=False)
        stop_event.set()
        return
    if blackboard.kill_event.is_set():
        stop_event.set()
        return

    await run_engine(blackboard)


def start_asyncio_thread(blackboard: BlackBoard, stop_event: threading.Event) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(async_main(blackboard, stop_event))
    except Exception as exc:
        get_logger().critical(f"Erreur dans le thread asynchrone: {exc}")
    finally:
        loop.close()


def _configure_stdout_for_console() -> None:
    if sys.stdout and getattr(sys.stdout, "encoding", None) != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass


if __name__ == "__main__":
    _configure_stdout_for_console()
    logger = setup_logger(
        log_dir=LOG_DIR,
        level=LOG_LEVEL,
        max_bytes=LOG_MAX_BYTES,
        backup_count=LOG_BACKUP_COUNT,
    )
    logger.info(f"Demarrage headless du moteur a {datetime.now(tz=timezone.utc).isoformat()}")

    blackboard = BlackBoard()
    stop_event = threading.Event()
    engine_thread = threading.Thread(
        target=start_asyncio_thread,
        args=(blackboard, stop_event),
        daemon=True,
    )
    engine_thread.start()

    try:
        run_system_tray(stop_event=stop_event, on_emergency_stop=stop_event.set)
    except KeyboardInterrupt:
        logger.warning("Ctrl+C detecte - arret force")
        stop_event.set()
    except Exception as exc:
        logger.critical(f"System tray indisponible ({exc}). Moteur maintenu en arriere-plan.")
        while engine_thread.is_alive() and not stop_event.is_set():
            time.sleep(1.0)
    finally:
        stop_event.set()
        engine_thread.join(timeout=2.0)
        logger.info("Processus termine.")

import asyncio
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

sys.path.insert(0, ".")

try:
    from utils.ssl_bundle import configure_ssl_environment

    configure_ssl_environment()
except ImportError:
    pass

from config import (
    KILL_FLAG_PATH,
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
from utils.discord_commander import discord_command_loop
from utils.discord_notifier import send_eod_report
from utils.emergency_shutdown import emergency_shutdown
from utils.logger import get_logger, setup_logger


async def cold_start(blackboard: BlackBoard) -> bool:
    logger = get_logger()
    logger.info("=" * 50)
    logger.info("COLD START - sequence de bootstrap")
    logger.info("=" * 50)

    logger.info("Phase 1: connexion MT5")
    if not await bridge.connect():
        from utils.mt5_bootstrap import ensure_mt5_running

        logger.warning("MT5 non connecte — tentative lancement terminal puis retry")
        ok, detail = await asyncio.to_thread(ensure_mt5_running)
        logger.info("ensure_mt5_running: ok=%s detail=%s", ok, detail)
        if not await bridge.connect():
            logger.critical("Echec connexion MT5 apres ensure_mt5_running")
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

    recovery_meta = load_recovery_metadata()
    recovered = await recover_open_positions(blackboard)
    if recovery_meta:
        logger.info(
            "Recovery apres coupure: saved_at=%s state=%s positions=%s",
            recovery_meta.get("saved_at", "N/A"),
            recovery_meta.get("state", "UNKNOWN"),
            recovered,
        )
    if recovered > 0:
        logger.warning(f"{recovered} position(s) orpheline(s) recuperee(s).")
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
        blackboard._data.setdefault("meta", {})["boot_time"] = datetime.now(timezone.utc).isoformat()

    await blackboard.write("meta.state", "READY")
    logger.info(f"COLD START termine - Mode: {'LIVE' if LIVE_MODE else 'PAPER'}")
    return True


async def async_main(blackboard: BlackBoard, stop_event: threading.Event) -> None:
    logger = get_logger()

    if Path(KILL_FLAG_PATH).exists():
        logger.warning("kill_flag present — arret immediat (attendre !start via pc_manager)")
        stop_event.set()
        return

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

    async def watch_kill_flag() -> None:
        while not stop_event.is_set() and not blackboard.kill_event.is_set():
            if Path(KILL_FLAG_PATH).exists():
                logger.warning("kill_flag detecte — arret du moteur")
                blackboard.kill_event.set()
                try:
                    from web.dashboard_server import stop_cloudflare_tunnel

                    await stop_cloudflare_tunnel()
                except Exception as exc:
                    logger.warning("Arret cloudflared sur kill_flag: %s", exc)
                try:
                    await emergency_shutdown(blackboard, reason="KILL_FLAG", notify=False)
                except Exception:
                    pass
                stop_event.set()
                return
            await asyncio.sleep(2.0)

    asyncio.create_task(watch_stop_event())
    asyncio.create_task(watch_kill_flag())
    asyncio.create_task(write_external_watchdog_heartbeat())
    asyncio.create_task(discord_command_loop(blackboard))

    from web.dashboard_server import bootstrap_dashboard
    from utils.bot_ready import PHASE_CLOUDFLARE_READY, mark_engine_ready, write_bot_ready
    from utils.discord_notifier import _notifier_from_config

    # Dashboard + Cloudflare en parallèle du cold start (réduit le timeout !start)
    dashboard_task = asyncio.create_task(bootstrap_dashboard(blackboard, launch_cloudflare=True))

    boot_ok = await cold_start(blackboard)
    if not boot_ok:
        dashboard_task.cancel()
        logger.critical("COLD START echoue - arret du systeme")
        await emergency_shutdown(blackboard, reason="BOOT_FAILED", notify=False, close_positions=False)
        stop_event.set()
        return
    if blackboard.kill_event.is_set():
        stop_event.set()
        return

    public_url = None
    try:
        public_url = await dashboard_task
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"Bootstrap dashboard echoue: {exc}")

    if public_url and "trycloudflare.com" in public_url:
        write_bot_ready(public_url, phase=PHASE_CLOUDFLARE_READY)
        mark_engine_ready(public_url)
        notifier = _notifier_from_config()
        await notifier.notify_system_start(
            mode="LIVE" if LIVE_MODE else "PAPER",
            symbol=SYMBOL,
            threshold=EXECUTION_THRESHOLD,
            cloudflare_url=public_url,
        )
        logger.info(f"Boot strict OK — Cloudflare: {public_url}")
    else:
        logger.warning(
            "URL Cloudflare non obtenue apres cold start — "
            "verifier cloudflared et CLOUDFLARED_PATH dans config"
        )

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
        while engine_thread.is_alive():
            stop_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        logger.warning("Ctrl+C detecte - arret force")
        stop_event.set()
    finally:
        stop_event.set()
        engine_thread.join(timeout=2.0)
        logger.info("Processus termine.")

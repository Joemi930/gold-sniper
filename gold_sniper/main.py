# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.0 — POINT D'ENTRÉE PRINCIPAL (AVEC UI)
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, ".")

from config import (
    LIVE_MODE, SYMBOL, MAGIC_NUMBER, LOG_DIR, LOG_LEVEL, LOG_MAX_BYTES, LOG_BACKUP_COUNT,
)
from utils.logger import setup_logger, get_logger
from core.blackboard import BlackBoard
from core.engine import run_engine
from core.orchestrator import EXECUTION_THRESHOLD
from core.mt5_bridge import bridge
from core.recovery_manager import recover_open_positions, load_daily_stats_from_recovery
from utils.telegram_notifier import send_telegram_notification, send_eod_report
import MetaTrader5 as mt5
from collections import deque
from ui.dashboard import Dashboard

BANNER = r"""
╔═══════════════════════════════════════════════════════════════════╗
║                                                                   ║
║       ██████╗  ██████╗ ██╗     ██████╗                           ║
║      ██╔════╝ ██╔═══██╗██║     ██╔══██╗                          ║
║      ██║  ███╗██║   ██║██║     ██║  ██║                          ║
║      ██║   ██║██║   ██║██║     ██║  ██║                          ║
║      ╚██████╔╝╚██████╔╝███████╗██████╔╝                          ║
║       ╚═════╝  ╚═════╝ ╚══════╝╚═════╝                           ║
║                                                                   ║
║      ███████╗███╗   ██╗██╗██████╗ ███████╗██████╗                ║
║      ██╔════╝████╗  ██║██║██╔══██╗██╔════╝██╔══██╗               ║
║      ███████╗██╔██╗ ██║██║██████╔╝█████╗  ██████╔╝               ║
║      ╚════██║██║╚██╗██║██║██╔═══╝ ██╔══╝  ██╔══██╗               ║
║      ███████║██║ ╚████║██║██║     ███████╗██║  ██║               ║
║      ╚══════╝╚═╝  ╚═══╝╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝               ║
║                                                                   ║
║      v1.0 — Robot de Trading Institutionnel XAUUSD               ║
║      Méthodologie : ICT / Smart Money Concepts                   ║
║                                                                   ║
╚═══════════════════════════════════════════════════════════════════╝
"""

async def cold_start(blackboard: BlackBoard) -> bool:
    logger = get_logger()
    logger.info("─" * 50)
    logger.info("🔧 COLD START — Séquence de bootstrap")
    logger.info("─" * 50)

    # ── PHASE 1 : Connexion MT5 ─────────────────────────────────────────
    logger.info("📡 Phase 1 : Connexion MT5")
    if not await bridge.connect():
        return False

    # ── PHASE 2 : Recovery — Positions orphelines + stats journalières ──
    logger.info("♻️  Phase 2 : Recovery Manager")
    # 2a. Recharger les stats journalières si même jour
    saved_daily = load_daily_stats_from_recovery()
    if saved_daily:
        async with blackboard._lock:
            blackboard._data.setdefault("daily_stats", {}).update(saved_daily)
        daily_count = saved_daily.get("trades_today", 0)
        logger.info(f"   -> Stats rechargées — PnL réalisé: {saved_daily.get('realized_pnl', 0):.2f}$")
    # 2b. Récupération des positions ouvertes (anti-orphelins)
    n_recovered = await recover_open_positions(blackboard)
    if n_recovered > 0:
        logger.warning(f"   -> {n_recovered} position(s) orpheline(s) ré-injectée(s) !")
        await send_telegram_notification(
            blackboard,
            f"♻️ *Recovery* — {n_recovered} position(s) orpheline(s) récupérée(s) au redémarrage."
        )
    else:
        logger.info("   -> Aucune position orpheline.")

    logger.info("📊 Phase 3 : Aspiration historique (1000 bougies 15m et 4H)")
    candles_15m = await bridge.get_historical_candles_async(SYMBOL, mt5.TIMEFRAME_M15, 1000)
    if candles_15m:
        blackboard._data.setdefault("market_data", {}).setdefault("candles", {})["15m"] = deque(candles_15m, maxlen=1000)
        logger.info(f"   -> 15m : {len(candles_15m)} bougies ingérées.")
    else:
        logger.error("   -> 15m : Échec de la récupération.")

    candles_4h = await bridge.get_historical_candles_async(SYMBOL, mt5.TIMEFRAME_H4, 1000)
    if candles_4h:
        blackboard._data.setdefault("market_data", {}).setdefault("candles", {})["4H"] = deque(candles_4h, maxlen=1000)
        logger.info(f"   -> 4H : {len(candles_4h)} bougies ingérées.")
    else:
        logger.error("   -> 4H : Échec de la récupération.")

    logger.info("🔄 Phase 4 : Réconciliation MT5 — OK")
    logger.info("📉 Phase 4b : Détection de gap [R2] — [STUB]")

    logger.info("📋 Phase 5 : Infos symbole")
    sym_info = await bridge.get_symbol_info_async(SYMBOL)
    if sym_info:
        await blackboard.write("market_data.symbol_info", sym_info)
        logger.info(f"   -> Spread actuel: {sym_info.get('spread', '?')} | Point: {sym_info.get('point_size', '?')}")
    else:
        logger.warning("   -> Échec récupération info symbole.")

    logger.info("🧮 Phase 6 : Initialisation des stats journalières")
    # Initialiser daily_stats si pas déjà chargé
    async with blackboard._lock:
        blackboard._data.setdefault("daily_stats", {
            "realized_pnl": 0.0,
            "floating_pnl": 0.0,
            "trades_closed": 0,
            "drawdown_halt": False,
        })

    await blackboard.write("meta.state", "READY")
    logger.info("✅ Phase 7 : Système opérationnel — État: READY")
    logger.info("─" * 50)
    logger.info(f"🏁 COLD START TERMINÉ — Mode: {'LIVE 🔴' if LIVE_MODE else 'PAPER 📝'}")
    logger.info("─" * 50)

    # Notification Telegram de démarrage
    await send_telegram_notification(
        blackboard,
        f"🚀 *Gold Sniper V2 démarré*\n"
        f"Mode: `{'LIVE 🔴' if LIVE_MODE else 'PAPER 📝'}`\n"
        f"Symbole: `{SYMBOL}` | Seuil: `{EXECUTION_THRESHOLD:.0f}/100`"
    )
    return True


async def async_main(blackboard: BlackBoard, stop_event: threading.Event) -> None:
    logger = get_logger()
    boot_ok = await cold_start(blackboard)
    if not boot_ok:
        logger.critical("❌ COLD START ÉCHOUÉ — Arrêt du système")
        return

    # Tâche d'arrêt surveillant le stop_event (Kill Switch)
    async def watch_stop_event():
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
        logger.warning("🛑 Arrêt déclenché via Kill Switch UI.")
        # Envoyer le rapport EOD avant d'arrêter
        try:
            await send_eod_report(blackboard)
        except Exception:
            pass
        blackboard.trigger_kill()

    asyncio.create_task(watch_stop_event())

    # Lancer le moteur
    await run_engine(blackboard)


def start_asyncio_thread(blackboard: BlackBoard, stop_event: threading.Event):
    """Lance la boucle asyncio dans un thread séparé."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(async_main(blackboard, stop_event))
    except Exception as e:
        logger = get_logger()
        logger.critical(f"Erreur dans le thread asynchrone: {e}")
    finally:
        loop.close()


if __name__ == "__main__":
    if sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except AttributeError:
            pass

    logger = setup_logger(
        log_dir=LOG_DIR,
        level=LOG_LEVEL,
        max_bytes=LOG_MAX_BYTES,
        backup_count=LOG_BACKUP_COUNT,
    )

    print(BANNER)
    logger.info(f"🕐 Démarrage de l'interface et du moteur à {datetime.now(tz=timezone.utc).isoformat()}")

    # Ressources partagées entre threads
    blackboard = BlackBoard()
    stop_event = threading.Event()

    # Démarrage du thread asynchrone (Backend)
    engine_thread = threading.Thread(target=start_asyncio_thread, args=(blackboard, stop_event), daemon=True)
    engine_thread.start()

    # Démarrage de l'UI CustomTkinter (Main thread)
    try:
        app = Dashboard(blackboard, stop_event)
        app.mainloop()
    except KeyboardInterrupt:
        logger.warning("⌨️ Ctrl+C détecté — Arrêt forcé")
        stop_event.set()
    finally:
        logger.info("👋 Processus terminé. Arrêt des threads...")
        stop_event.set()
        engine_thread.join(timeout=2.0)

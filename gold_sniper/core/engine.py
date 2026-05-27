# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — MOTEUR ASYNCHRONE (ENGINE)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Ossature de la boucle événementielle asyncio.
# Lance toutes les coroutines et agents de manière concurrente.
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
import traceback
from typing import Callable, Coroutine

from core.blackboard import BlackBoard
from utils.logger import get_logger
from utils.telegram_notifier import _notifier_from_config, send_telegram_notification
from utils.report_scheduler import report_scheduler_loop
from utils.drive_sync import drive_sync_loop
from web.dashboard_server import dashboard_loop

# Importation des modules concrets (remplacement des stubs)
from core.tick_ingestion import tick_ingestion_loop
from core.candle_builder import candle_builder_loop
from agents.agent_1_meteo import AgentMeteo
from agents.agent_2_cartographe import AgentCartographe
from agents.agent_3_liquidite import AgentLiquidite
from agents.agent_4_fibonacci import AgentFibonacci
from agents.agent_5_microscope import AgentMicroscope
from agents.agent_6_sentinelle import AgentSentinelle
from agents.agent_7_chronos import AgentSessions
from agents.macro_monitor import MacroMonitor
from agents.regime_detector import RegimeDetector
from agents.risk_manager import RiskManager
from core.orchestrator import orchestrator_loop
from core.orchestrator import BASE_WEIGHTS as ORCHESTRATOR_BASE_WEIGHTS
from data.memory_db import memory_learning_loop
from execution.adaptive_weights import AdaptiveWeightEngine
from execution.trade_manager import TradeManager
from core.recovery_manager import recovery_persistence_loop
from utils.mt5_watchdog import MT5Watchdog

# ─────────────────────────────────────────────────────────────────────────────
# 1. WRAPPER DE SUPERVISION DES COROUTINES
# ─────────────────────────────────────────────────────────────────────────────

async def supervised_task(
    name: str,
    coro_factory: Callable[..., Coroutine],
    blackboard: BlackBoard,
    restart_on_error: bool = True,
    max_restarts: int = 5,
    restart_delay: float = 2.0,
    **kwargs,
) -> None:
    logger = get_logger()
    restart_count = 0

    while True:
        try:
            logger.info(f"▶️  Coroutine [{name}] démarrée (tentative {restart_count + 1})")
            await coro_factory(blackboard=blackboard, **kwargs)

            logger.info(f"⏹️  Coroutine [{name}] terminée normalement")
            break

        except asyncio.CancelledError:
            logger.warning(f"🛑 Coroutine [{name}] annulée (CancelledError)")
            break

        except Exception as e:
            restart_count += 1
            logger.error(f"💥 Coroutine [{name}] crash #{restart_count}/{max_restarts} — {type(e).__name__}: {e}\n{traceback.format_exc()}")

            if blackboard.kill_event.is_set():
                break

            if not restart_on_error or restart_count >= max_restarts:
                logger.critical(f"🔴 Coroutine [{name}] ABANDONNÉE après {restart_count} échecs.")
                break

            logger.warning(f"🔄 Redémarrage de [{name}] dans {restart_delay}s... ({restart_count}/{max_restarts})")
            await asyncio.sleep(restart_delay)
            restart_delay = min(restart_delay * 2, 30.0)

# ─────────────────────────────────────────────────────────────────────────────
# 2. TÂCHES SERVICES
# ─────────────────────────────────────────────────────────────────────────────

async def telegram_sender_loop(blackboard: BlackBoard) -> None:
    """
    Boucle de notification Telegram.
    Draine la queue de notifications du Blackboard et les envoie.
    """
    logger = get_logger()
    logger.info("▶️  Telegram Sender démarré")
    while not blackboard.kill_event.is_set():
        try:
            notifs = blackboard.read_sync("notifications")
            queue = notifs.get("queue", []) if notifs else []
            if queue:
                # Prendre le premier message de la queue
                message = queue.pop(0)
                async with blackboard._lock:
                    blackboard._data["notifications"]["queue"] = queue
                await send_telegram_notification(blackboard, message)
        except Exception as e:
            logger.warning(f"⚠️ Telegram sender erreur : {e}")
        await asyncio.sleep(1.0)

async def account_info_fetcher(blackboard: BlackBoard) -> None:
    from core.mt5_bridge import bridge
    while not blackboard.kill_event.is_set():
        if bridge.connected:
            info = await bridge.get_account_info_async()
            if info:
                await blackboard.write("meta.account_info", info)
        await asyncio.sleep(2.0)


async def adaptive_weights_loop(blackboard: BlackBoard) -> None:
    """Observe les trades clotures et applique les poids adaptatifs a l'orchestrateur.

    La boucle s'arrête immédiatement si config.ADAPTIVE_WEIGHTS_ENABLED est False.
    Pendant la semaine démo, seul weight_calibrator.py (batch/50 trades) est utilisé,
    déclenché manuellement via /calibrate.
    """
    import config as _cfg
    logger = get_logger()

    if not _cfg.ADAPTIVE_WEIGHTS_ENABLED:
        logger.info(
            "Adaptive weights loop DÉSACTIVÉE (ADAPTIVE_WEIGHTS_ENABLED=False). "
            "Utiliser /calibrate après 50 trades pour recalibrer les poids."
        )
        await blackboard.update_dict("orchestrator", {"adaptive_weights_enabled": False})
        return  # Boucle inactive — arbitre unique = weight_calibrator

    engine = AdaptiveWeightEngine()
    processed_tickets: set[int] = set()
    logger.info("Adaptive weights loop demarree")

    while not blackboard.kill_event.is_set():
        try:
            closed_today = blackboard.read_sync("positions.closed_today") or []
            for trade in closed_today:
                ticket = int(trade.get("ticket", 0) or 0)
                if ticket in processed_tickets:
                    continue

                breakdown = trade.get("agent_breakdown") or {}
                if not breakdown:
                    continue

                outcome = trade.get("outcome")
                if outcome not in {"WIN", "LOSS", "BREAKEVEN"}:
                    pnl = float(trade.get("pnl", 0.0) or 0.0)
                    outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

                new_weights = engine.record_trade_result(breakdown, outcome)
                ORCHESTRATOR_BASE_WEIGHTS.clear()
                ORCHESTRATOR_BASE_WEIGHTS.update(new_weights)
                processed_tickets.add(ticket)

                await blackboard.update_dict("orchestrator", {
                    "adaptive_weights": new_weights,
                    "adaptive_weights_last_ticket": ticket,
                    "adaptive_weights_last_outcome": outcome,
                    "adaptive_weights_trades_seen": len(engine.session_trades),
                })
                logger.info(
                    f"Adaptive weights recalcules apres trade {ticket} ({outcome}): {new_weights}"
                )
        except Exception as exc:
            logger.warning(f"Adaptive weights loop erreur: {exc}")

        await asyncio.sleep(0.5)



# ─────────────────────────────────────────────────────────────────────────────
# 3. ASSEMBLAGE DU MOTEUR
# ─────────────────────────────────────────────────────────────────────────────

async def run_engine(blackboard: BlackBoard) -> None:
    logger = get_logger()
    logger.info("=" * 60)
    logger.info("🚀 GOLD SNIPER v1.0 — Lancement du Moteur Asynchrone Complet")
    logger.info("=" * 60)

    # Initialisation des classes
    a1 = AgentMeteo(blackboard)
    a2 = AgentCartographe(blackboard)
    a3 = AgentLiquidite(blackboard)
    a4 = AgentFibonacci(blackboard)
    a5 = AgentMicroscope(blackboard)
    a6 = AgentSentinelle(blackboard, telegram=_notifier_from_config())
    a7 = AgentSessions(blackboard)
    macro_monitor = MacroMonitor(blackboard)
    regime_detector = RegimeDetector(blackboard)
    risk_manager = RiskManager(blackboard, telegram=_notifier_from_config())
    trade_manager = TradeManager(blackboard)
    mt5_watchdog = MT5Watchdog(blackboard)

    # ─────────────────────────────────────────────────────────────────────────
    # DÉMARRAGE DES COROUTINES (Pipeline V2 Event-Driven)
    # Les agents tournent tous en tâche de fond, mais la synchronisation
    # s'effectue via le Blackboard (wait_for_agent).
    # ─────────────────────────────────────────────────────────────────────────
    tasks = [
        # --- DATA INGESTION ---
        supervised_task("tick_ingestion", tick_ingestion_loop, blackboard),
        supervised_task("candle_builder", candle_builder_loop, blackboard),
        
        # --- PHASE 0 : GATES (Toujours actifs) ---
        supervised_task("risk_manager", lambda blackboard: risk_manager.run(), blackboard),
        supervised_task("agent_6_senti", lambda blackboard: a6.run(), blackboard),
        supervised_task("agent_7_sess", lambda blackboard: a7.run(), blackboard),
        supervised_task("macro_monitor", lambda blackboard: macro_monitor.run(), blackboard),
        supervised_task("regime_detector", lambda blackboard: regime_detector.run(), blackboard),

        # --- PHASE 1 : ANALYSE STRUCTURELLE (Séquentiel via Events) ---
        supervised_task("agent_1_meteo", lambda blackboard: a1.run(), blackboard),
        supervised_task("agent_2_carto", lambda blackboard: a2.run(), blackboard),

        # --- PHASE 2 : CONFIRMATION & TRIGGER (Simultané sur POI) ---
        supervised_task("agent_3_liqui", lambda blackboard: a3.run(), blackboard),
        supervised_task("agent_4_fibo", lambda blackboard: a4.run(), blackboard),
        supervised_task("agent_5_micro", lambda blackboard: a5.run(), blackboard),
        
        # --- PHASE 3 : ORCHESTRATEUR (Décideur V2) ---
        supervised_task("orchestrator", orchestrator_loop, blackboard),
        
        # --- EXECUTION ---
        supervised_task("trade_manager", lambda blackboard: trade_manager.run(), blackboard),
        supervised_task("adaptive_weights", adaptive_weights_loop, blackboard),
        supervised_task("memory_learning", memory_learning_loop, blackboard),
        
        # --- SERVICES ANNEXES ---
        supervised_task("account_info_fetcher", account_info_fetcher, blackboard),
        supervised_task("mt5_watchdog", lambda blackboard: mt5_watchdog.run(), blackboard),
        supervised_task("report_scheduler", report_scheduler_loop, blackboard),
        supervised_task("drive_sync", drive_sync_loop, blackboard),
        supervised_task("dashboard_web", dashboard_loop, blackboard),
        supervised_task("recovery_persistence", recovery_persistence_loop, blackboard),
        supervised_task("telegram_sender", telegram_sender_loop, blackboard),
    ]

    try:
        await asyncio.gather(*tasks)
    except Exception as e:
        logger.critical(f"💀 Erreur fatale dans asyncio.gather : {type(e).__name__}: {e}")
        raise
    finally:
        logger.warning("⏹️  Moteur asynchrone arrêté")

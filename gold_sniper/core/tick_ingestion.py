# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — TICK INGESTION
# ═══════════════════════════════════════════════════════════════════════════════
#
# Coroutine responsable d'aspirer les prix XAUUSD en temps réel.
# Elle tourne en boucle infinie, interroge le MT5 Bridge, et met à jour
# le Tableau Noir avec le dernier tick connu.
#
# [R15] Protection : L'ingestion est "rate limitée" pour ne pas saturer
# le bridge Python-MT5.
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
import time
from datetime import datetime, timezone

from config import SYMBOL, MT5_MAX_CALLS_PER_SECOND
from core.blackboard import BlackBoard
from core.mt5_bridge import bridge
from utils.logger import get_logger


async def tick_ingestion_loop(blackboard: BlackBoard) -> None:
    """
    Aspire les prix XAUUSD via mt5.symbol_info_tick() en continu.
    Met à jour le Tableau Noir.
    """
    logger = get_logger()
    
    # Calcul de l'intervalle minimum entre deux requêtes pour respecter le rate limit
    # Ex: 10 appels par sec = 0.1s minimum par boucle
    min_sleep_interval = 1.0 / MT5_MAX_CALLS_PER_SECOND

    logger.info(f"▶️  Tick Ingestion démarrée sur {SYMBOL} — Rate limit: {MT5_MAX_CALLS_PER_SECOND} appels/s")

    # On a besoin de connaître la taille du point pour calculer le spread en "points"
    # S'il n'est pas encore disponible, on fera une division par 1 temporairement.
    point_size = 1.0

    while not blackboard.kill_event.is_set():
        loop_start_time = time.monotonic()

        # 1. Vérifier si on doit mettre à jour le point_size depuis le blackboard
        # Cela est normalement injecté lors du Cold Start (Phase 5).
        symbol_info_point = blackboard.read_sync("market_data.symbol_info.point_size")
        if symbol_info_point and symbol_info_point > 0:
            point_size = symbol_info_point

        # 2. Récupérer le tick via le Bridge
        try:
            tick = await bridge.get_tick_async(SYMBOL)
            
            if tick is not None:
                # 3. Calculs et formatage
                bid = tick["bid"]
                ask = tick["ask"]
                tick_time = datetime.fromtimestamp(tick["time"], tz=timezone.utc)
                
                # Spread en points (ex: Or, 1 point = 0.01$)
                spread_points = (ask - bid) / point_size

                # 4. Mise à jour du Tableau Noir
                await blackboard.update_dict("market_data.current_tick", {
                    "bid": bid,
                    "ask": ask,
                    "spread_points": spread_points,
                    "time": tick_time,
                    "volume": tick["volume"],
                })

                # Mettre à jour aussi le timestamp du dernier tick dans les meta
                await blackboard.write("meta.last_tick_time", tick_time)

                # DEBUG (à commenter en prod)
                # logger.debug(f"Tick {SYMBOL} : Bid={bid} Ask={ask} Spread={spread_points:.1f}")

        except Exception as e:
            logger.error(f"❌ Erreur lors de l'ingestion du tick : {e}")

        # 5. Rate Limiting [R15]
        # On calcule combien de temps l'appel a pris, et on dort le reste
        # de l'intervalle pour ne pas dépasser le quota.
        elapsed = time.monotonic() - loop_start_time
        sleep_time = min_sleep_interval - elapsed
        
        if sleep_time > 0:
            await asyncio.sleep(sleep_time)
        else:
            # Si l'appel a été plus long que le quota, on cède quand même
            # le contrôle à l'Event Loop pour éviter le blocage.
            await asyncio.sleep(0.001)

    logger.warning("🛑 Tick Ingestion arrêtée.")

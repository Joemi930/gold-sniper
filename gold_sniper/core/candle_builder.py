# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — CANDLE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════
#
# Coroutine responsable d'agréger les ticks en temps réel pour former les
# bougies OHLCV (Open, High, Low, Close, Volume).
# 
# Elle maintient les bougies actuelles (en cours de formation) et les pousse
# dans l'historique (les deque du Tableau Noir) dès qu'une nouvelle bougie
# commence selon l'horloge UTC.
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

from core.blackboard import BlackBoard
from utils.logger import get_logger


def get_candle_boundary(dt: datetime, timeframe: str) -> datetime:
    """
    Calcule le timestamp de début exact de la bougie pour un timeframe donné.
    dt doit être datetime aware (UTC).
    """
    if timeframe == "1m":
        return dt.replace(second=0, microsecond=0)
    elif timeframe == "15m":
        minute = (dt.minute // 15) * 15
        return dt.replace(minute=minute, second=0, microsecond=0)
    elif timeframe == "4H":
        hour = (dt.hour // 4) * 4
        return dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    else:
        raise ValueError(f"Timeframe inconnu : {timeframe}")


async def candle_builder_loop(blackboard: BlackBoard) -> None:
    """
    Lit le tick actuel en boucle et construit les bougies dynamiquement.
    """
    logger = get_logger()
    logger.info("▶️  Candle Builder démarré")

    # État local pour les bougies en cours de formation
    # (Évite de lire/écrire constamment le deque complet dans le Blackboard)
    current_candles: Dict[str, Optional[Dict[str, Any]]] = {
        "1m": None,
        "15m": None,
        "4H": None,
    }

    # Pour savoir si on a déjà traité ce tick exact (via son timestamp / bid)
    last_processed_tick_time = None

    while not blackboard.kill_event.is_set():
        # 1. Lire le tick courant depuis le Tableau Noir
        tick = blackboard.read_sync("market_data.current_tick")

        if tick and tick["time"] is not None:
            tick_time = tick["time"]
            bid = tick["bid"]
            volume = tick.get("volume", 1.0) # S'assurer d'avoir une valeur

            # Ne traiter que si c'est un nouveau tick dans le temps
            # (Pour plus de précision, on pourrait comparer tick_time + bid)
            if tick_time != last_processed_tick_time:
                last_processed_tick_time = tick_time

                # 2. Mettre à jour chaque timeframe
                for tf in ["1m", "15m", "4H"]:
                    boundary = get_candle_boundary(tick_time, tf)
                    active_candle = current_candles[tf]

                    if active_candle is None:
                        # Initialisation (première fois)
                        current_candles[tf] = {
                            "time": boundary,
                            "open": bid,
                            "high": bid,
                            "low": bid,
                            "close": bid,
                            "tick_volume": volume,
                            "is_closed": False
                        }
                    
                    elif boundary > active_candle["time"]:
                        # ---------------------------------------------------------
                        # FERMETURE DE LA BOUGIE : on a basculé sur la suivante
                        # ---------------------------------------------------------
                        active_candle["is_closed"] = True
                        
                        # Pousser dans l'historique du Blackboard (deque thread-safe)
                        async with blackboard._lock:
                            history = blackboard._data["market_data"]["candles"][tf]
                            history.append(active_candle)
                        
                        # Log pour confirmer la clôture
                        logger.debug(f"[{tf}] Clôture bougie {active_candle['time'].strftime('%H:%M')} -> Close: {active_candle['close']}")

                        # Démarrer la NOUVELLE bougie avec ce tick
                        current_candles[tf] = {
                            "time": boundary,
                            "open": bid,
                            "high": bid,
                            "low": bid,
                            "close": bid,
                            "tick_volume": volume,
                            "is_closed": False
                        }
                    
                    else:
                        # ---------------------------------------------------------
                        # MISE À JOUR DE LA BOUGIE EN COURS
                        # ---------------------------------------------------------
                        if bid > active_candle["high"]:
                            active_candle["high"] = bid
                        if bid < active_candle["low"]:
                            active_candle["low"] = bid
                        
                        active_candle["close"] = bid
                        active_candle["tick_volume"] += volume

        # Petite pause pour ne pas saturer le CPU (10ms)
        # On lit le blackboard environ 100 fois par seconde, ce qui est très léger.
        await asyncio.sleep(0.01)

    logger.warning("🛑 Candle Builder arrêté.")

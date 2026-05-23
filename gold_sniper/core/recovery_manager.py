# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.0 — RECOVERY MANAGER
# ═══════════════════════════════════════════════════════════════════════════════
#
# Gère la persistance et la récupération de l'état du système.
# Au redémarrage, relit les positions MT5 ouvertes et les ré-injecte
# dans le Blackboard pour éviter les trades orphelins (BUG#8 fix).
#
# FONCTIONNALITÉS :
# - Sauvegarde périodique du snapshot Blackboard dans recovery.json
# - Récupération des positions ouvertes MT5 au démarrage (positions orphelines)
# - Chargement de l'état des statistiques journalières
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Optional

import MetaTrader5 as mt5

from config import RECOVERY_FILE_PATH, RECOVERY_DEBOUNCE_SECONDS, MAGIC_NUMBER, SYMBOL
from utils.logger import get_logger

logger = get_logger()


# ─────────────────────────────────────────────────────────────────────────────
# RÉCUPÉRATION AU DÉMARRAGE — Positions Orphelines
# ─────────────────────────────────────────────────────────────────────────────

async def recover_open_positions(blackboard) -> int:
    """
    Au démarrage du bot, vérifie s'il y a des positions MT5 ouvertes
    avec notre MAGIC_NUMBER qui ne sont pas dans le Blackboard.
    Ré-injecte ces positions dans active_trades pour éviter les trades orphelins.

    Retourne le nombre de positions récupérées.
    """
    logger.info("♻️  Recovery — Scan des positions MT5 ouvertes...")

    try:
        positions = await asyncio.to_thread(
            mt5.positions_get, symbol=SYMBOL
        )
    except Exception as e:
        logger.error(f"❌ Recovery: Impossible de récupérer les positions MT5 — {e}")
        return 0

    if positions is None:
        logger.info("♻️  Recovery — Aucune position ouverte trouvée.")
        return 0

    # Filtrer uniquement nos positions (par MAGIC_NUMBER)
    our_positions = [p for p in positions if p.magic == MAGIC_NUMBER]

    if not our_positions:
        logger.info("♻️  Recovery — Aucune position Gold Sniper trouvée.")
        return 0

    recovered = 0
    async with blackboard._lock:
        active_trades = blackboard._data.setdefault("active_trades", {})

        for pos in our_positions:
            ticket = pos.ticket
            if ticket in active_trades:
                logger.debug(f"♻️  Trade {ticket} déjà dans le Blackboard — skip.")
                continue

            # Type de position MT5 → BUY/SELL
            trade_type = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"

            # Reconstituer le record de trade
            trade_record = {
                "ticket": ticket,
                "type": trade_type,
                "entry_price": pos.price_open,
                "original_sl": pos.sl if pos.sl > 0 else pos.price_open,  # Fallback
                "current_sl": pos.sl,
                "tp": pos.tp,
                "volume_original": pos.volume,
                "breakeven_activated": _is_be_activated(pos),
                "partial_closed": False,  # On ne peut pas le savoir sans historique complet
                "opened_at": datetime.fromtimestamp(pos.time, tz=timezone.utc).isoformat(),
                "score": 0,  # Score inconnu après redémarrage
                "recovered": True,  # Marqueur de position récupérée
            }

            active_trades[ticket] = trade_record
            recovered += 1
            logger.warning(
                f"♻️  Position ORPHELINE récupérée — Ticket: {ticket} | "
                f"{trade_type} {pos.volume} lots @ {pos.price_open:.2f} | "
                f"SL: {pos.sl:.2f} | TP: {pos.tp:.2f} | PnL: {pos.profit:.2f}"
            )

    if recovered > 0:
        logger.info(f"♻️  Recovery terminé — {recovered} position(s) réinjectée(s) dans le Blackboard.")
    return recovered


def _is_be_activated(position) -> bool:
    """
    Heuristique : si le SL est très proche du prix d'ouverture (± 1$),
    on considère que le BreakEven a déjà été activé.
    """
    if position.sl <= 0:
        return False
    return abs(position.sl - position.price_open) < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# SAUVEGARDE PÉRIODIQUE
# ─────────────────────────────────────────────────────────────────────────────

async def recovery_persistence_loop(blackboard) -> None:
    """
    Boucle de persistance : sauvegarde périodique du Blackboard dans recovery.json.
    Utilise un debounce pour éviter d'écrire trop fréquemment.
    """
    logger.info("▶️  Recovery Persistence démarré")
    last_save = 0.0

    while not blackboard.kill_event.is_set():
        try:
            now = asyncio.get_event_loop().time()

            # Debounce : sauvegarder maximum toutes les N secondes
            if now - last_save >= RECOVERY_DEBOUNCE_SECONDS * 30:  # Toutes les 30s
                await _save_snapshot(blackboard)
                last_save = now

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"❌ Recovery Persistence erreur : {e}")

        await asyncio.sleep(RECOVERY_DEBOUNCE_SECONDS)


async def _save_snapshot(blackboard) -> None:
    """Sauvegarde un snapshot sérialisable du Blackboard dans recovery.json."""
    try:
        snapshot = await blackboard.snapshot()

        # Garder uniquement les données critiques pour le recovery
        recovery_data = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "active_trades": snapshot.get("active_trades", {}),
            "meta": {
                "daily_trade_count": snapshot.get("meta", {}).get("daily_trade_count", 0),
                "state": snapshot.get("meta", {}).get("state", "UNKNOWN"),
            },
            "daily_stats": snapshot.get("daily_stats", {}),
        }

        with open(RECOVERY_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(recovery_data, f, indent=2, default=str)

        logger.debug(f"💾 Recovery snapshot sauvegardé — {len(recovery_data.get('active_trades', {}))} trade(s) actifs")

    except Exception as e:
        logger.error(f"❌ Échec de la sauvegarde recovery.json : {e}")


def load_daily_stats_from_recovery() -> Optional[dict]:
    """
    Charge les stats journalières depuis recovery.json si le fichier existe
    et est du jour courant. Utilisé au démarrage pour éviter de perdre
    le compteur de trades si le bot a été redémarré dans la journée.
    """
    if not os.path.exists(RECOVERY_FILE_PATH):
        return None

    try:
        with open(RECOVERY_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        saved_at_str = data.get("saved_at", "")
        saved_at = datetime.fromisoformat(saved_at_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)

        # Vérifier que la sauvegarde est du même jour
        if saved_at.date() == now.date():
            logger.info(f"♻️  Stats journalières rechargées depuis recovery.json (sauvegarde: {saved_at_str})")
            return data.get("daily_stats", {})
        else:
            logger.info("♻️  Recovery.json d'un jour précédent — stats journalières ignorées.")
            return None

    except Exception as e:
        logger.warning(f"⚠️ Impossible de lire recovery.json : {e}")
        return None

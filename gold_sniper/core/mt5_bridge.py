# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — BRIDGE METATRADER 5 (ASYNCHRONE)
# ═══════════════════════════════════════════════════════════════════════════════
#
# La librairie MetaTrader5 est SYNCHRONE et bloque le thread.
# Pour respecter l'architecture 100% asynchrone, TOUS les appels à MT5
# doivent obligatoirement passer par `asyncio.to_thread()`.
#
# Ce module est le SEUL autorisé à importer et appeler `MetaTrader5`.
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import MetaTrader5 as mt5

from config import MT5_ACCOUNT, MT5_PASSWORD, MT5_SERVER, MT5_PATH, SYMBOL
from utils.logger import get_logger


class MT5Bridge:
    """
    Encapsulation asynchrone de la librairie MetaTrader5.
    Aucune méthode de cette classe ne doit bloquer l'Event Loop.
    """

    def __init__(self):
        self._logger = get_logger()
        self.connected = False

    async def connect(self) -> bool:
        """
        Initialise la connexion à MT5 et au compte de trading.
        Exécuté dans un thread séparé.
        """
        self._logger.info("📡 Tentative de connexion au terminal MT5...")

        # 1. Initialisation du terminal
        # Les paramètres nommés ne sont pas toujours supportés sur d'anciennes versions,
        # mais la doc officielle recommande de les passer.
        init_kwargs = {}
        if MT5_PATH:
            init_kwargs["path"] = MT5_PATH

        # Appel bloquant délégué à un thread
        initialized = await asyncio.to_thread(mt5.initialize, **init_kwargs)
        
        if not initialized:
            err = mt5.last_error()
            self._logger.critical(f"❌ Échec de l'initialisation MT5 : {err}")
            return False

        # 2. Login au compte broker
        if MT5_ACCOUNT and MT5_PASSWORD and MT5_SERVER:
            self._logger.info(f"🔐 Login au compte {MT5_ACCOUNT} sur {MT5_SERVER}...")
            authorized = await asyncio.to_thread(
                mt5.login,
                login=MT5_ACCOUNT,
                password=MT5_PASSWORD,
                server=MT5_SERVER
            )
            
            if not authorized:
                err = mt5.last_error()
                self._logger.critical(f"❌ Échec de l'authentification MT5 : {err}")
                await self.shutdown()
                return False

        # 3. Vérification de la disponibilité du symbole
        symbol_info = await asyncio.to_thread(mt5.symbol_info, SYMBOL)
        if symbol_info is None:
            self._logger.critical(f"❌ Symbole {SYMBOL} introuvable sur ce broker.")
            await self.shutdown()
            return False

        if not symbol_info.visible:
            self._logger.info(f"👁️ Rendre le symbole {SYMBOL} visible dans le Market Watch...")
            if not await asyncio.to_thread(mt5.symbol_select, SYMBOL, True):
                self._logger.critical(f"❌ Impossible de sélectionner {SYMBOL}.")
                await self.shutdown()
                return False

        self.connected = True
        self._logger.info("✅ Connecté avec succès à MT5.")
        return True

    async def shutdown(self) -> None:
        """Ferme proprement la connexion MT5."""
        if self.connected:
            await asyncio.to_thread(mt5.shutdown)
            self.connected = False
            self._logger.info("🔌 Connexion MT5 fermée.")

    async def get_tick_async(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Récupère le dernier tick d'un symbole.
        Retourne un dictionnaire avec bid, ask, time, volume.
        """
        if not self.connected:
            return None

        # Appel bloquant délégué
        tick = await asyncio.to_thread(mt5.symbol_info_tick, symbol)
        
        if tick is None:
            return None

        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "time": tick.time,            # Timestamp epoch (int, UTC)
            "volume": tick.volume,
            "flags": tick.flags,
        }

    async def get_symbol_info_async(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Récupère les informations structurelles du symbole.
        """
        if not self.connected:
            return None

        info = await asyncio.to_thread(mt5.symbol_info, symbol)
        if info is None:
            return None

        return {
            "point_size": info.point,
            "trade_tick_size": info.trade_tick_size,
            "trade_tick_value": info.trade_tick_value,
            "trade_contract_size": info.trade_contract_size,
            "volume_min": info.volume_min,
            "volume_max": info.volume_max,
            "volume_step": info.volume_step,
            "stoplevel": info.trade_stops_level,
            "spread": info.spread,
        }

    async def get_historical_candles_async(
        self, symbol: str, timeframe: int, count: int
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Récupère l'historique des bougies (utile pour le Cold Start).
        timeframe doit être une constante MT5 (ex: mt5.TIMEFRAME_M1).
        """
        if not self.connected:
            return None

        rates = await asyncio.to_thread(mt5.copy_rates_from_pos, symbol, timeframe, 0, count)
        if rates is None or len(rates) == 0:
            return None

        # Conversion du tableau numpy en liste de dicts
        return [
            {
                "time": datetime.fromtimestamp(r['time'], tz=timezone.utc),
                "open": r['open'],
                "high": r['high'],
                "low": r['low'],
                "close": r['close'],
                "tick_volume": r['tick_volume'],
                "real_volume": r['real_volume']
            }
            for r in rates
        ]

    async def get_account_info_async(self) -> Optional[Dict[str, Any]]:
        """
        Récupère les informations du compte de trading (Balance, Equity, Name).
        """
        if not self.connected:
            return None

        account_info = await asyncio.to_thread(mt5.account_info)
        if account_info is None:
            return None

        return {
            "name": account_info.name,
            "server": account_info.server,
            "currency": account_info.currency,
            "balance": account_info.balance,
            "equity": account_info.equity,
            "margin_free": account_info.margin_free
        }

# Instance globale (Singleton pattern pour simplifier l'accès)
bridge = MT5Bridge()

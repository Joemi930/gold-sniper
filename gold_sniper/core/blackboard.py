# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — TABLEAU NOIR (BLACKBOARD / SHARED STATE)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Le Tableau Noir est le dictionnaire central en RAM où TOUS les agents
# écrivent leurs données. C'est la clé de voûte de l'architecture.
#
# Protégé par un asyncio.Lock() pour garantir la cohérence lors d'écritures
# concurrentes. Chaque agent écrit dans sa propre clé sans interférence.
#
# Référence : architecture.md — Section 2.2
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional, Callable
from core.agent_result import AgentResult

from config import (
    LIVE_MODE,
    CANDLE_HISTORY,
    MT5_MAX_CALLS_PER_SECOND,
    TELEGRAM_ENABLED,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    PAPER_SIMULATED_EQUITY,
    PAPER_CSV_PATH,
    RECOVERY_FILE_PATH,
)
from utils.logger import get_logger


class BlackBoard:
    """
    Tableau Noir — État global partagé entre toutes les coroutines.

    Structure exacte définie dans architecture.md § 2.2.
    Le dictionnaire interne `_data` n'est jamais accédé directement :
    on utilise les méthodes `read()` et `write()` qui gèrent le verrou.

    Usage :
        bb = BlackBoard()
        await bb.write("meta.state", "READY")
        state = await bb.read("meta.state")
    """

    def __init__(self) -> None:
        self._logger = get_logger()
        self._lock = asyncio.Lock()

        # [R1] Event global pour le Kill Switch — vérifié avant chaque order_send
        self._kill_event = asyncio.Event()

        # Timestamp de création
        now = datetime.now(tz=timezone.utc)

        # Bus d'événements V2 : clé = nom de l'événement, valeur = liste de callbacks
        self._subscribers: dict[str, list[Callable]] = {}
        self._events: dict[str, asyncio.Event] = {
            "agent_1_ready": asyncio.Event(),
            "agent_2_ready": asyncio.Event(),
            "agent_3_ready": asyncio.Event(),
            "agent_4_ready": asyncio.Event(),
            "agent_5_ready": asyncio.Event(),
            "agent_6_ready": asyncio.Event(),
            "agent_7_ready": asyncio.Event(),
            "new_candle_1m": asyncio.Event(),
            "new_candle_15m": asyncio.Event(),
            "price_in_poi": asyncio.Event(),   # Agent 2 active cet event → réveille Agent 5
            "pipeline_reset": asyncio.Event(),
        }

        # ─────────────────────────────────────────────────────────────────
        # STRUCTURE COMPLÈTE DU TABLEAU NOIR
        # Miroir exact de architecture.md § 2.2
        # ─────────────────────────────────────────────────────────────────
        self._data: dict[str, Any] = {

            # ── META : État global du système ────────────────────────────
            "meta": {
                "boot_time": now,
                "last_tick_time": None,
                "state": "BOOT",                    # BOOT → READY → TRADING → COOLDOWN → HALTED → KILLED
                "live_mode": LIVE_MODE,              # [R10] False = Paper Trading
                "kill_switch": False,
                "kill_event": self._kill_event,      # [R1] Objet asyncio.Event partagé
                "daily_trade_count": 0,
                "cooldown_until": None,
                "friday_mode": {                     # [R16]
                    "risk_reduced": False,
                    "trading_halted": False,
                },
            },

            # ── MARKET DATA : Prix, bougies, infos symbole ──────────────
            "market_data": {
                "current_tick": {
                    "bid": 0.0,
                    "ask": 0.0,
                    "spread_points": 0.0,
                    "time": None,
                    "volume": 0.0,
                },
                "candles": {
                    "4H":  deque(maxlen=CANDLE_HISTORY["4H"]),
                    "15m": deque(maxlen=CANDLE_HISTORY["15m"]),
                    "1m":  deque(maxlen=CANDLE_HISTORY["1m"]),
                },
                "symbol_info": {
                    "point_size": 0.0,
                    "trade_tick_size": 0.0,
                    "trade_tick_value": 0.0,
                    "trade_contract_size": 0.0,
                    "volume_min": 0.0,
                    "volume_max": 0.0,
                    "volume_step": 0.0,
                    "stoplevel": 0,
                    "spread": 0,
                },
                "atr_14": None, # [V2] Calculé 1 fois, partagé par TOUS les agents
            },

            # ── MARKET ANALYSIS : Analyses partagées des agents ──────────
            "market_analysis": {
                "market_structure": {
                    "overall_bias": "NEUTRAL",
                    "trend_4h": "NEUTRAL",
                    "trend_15m": "NEUTRAL"
                },
                "zones": {},
                "liquidity_pools": {
                    "eqh": [],
                    "eql": []
                },
                "ote_zone": {},
                "microscope": {
                    "is_choch_detected": False,
                    "choch_details": {}
                },
            },

            # ── RISK MANAGEMENT : Boucliers et filtres OPSEC ─────────────
            "risk_management": {
                "volatility_gate": {
                    "allow_trade": True,
                    "next_news_time": None
                },
                "session_gate": {
                    "allow_trade": False,
                    "current_session": "OFF_HOURS"
                },
            },

            # ── AGENTS : Données des 7 agents analytiques ───────────────
            "agents": {

                # Agent 1 — Météo / Structure de Marché
                "agent_1_meteo": {
                    "score": 0,
                    "bias": "NEUTRAL",
                    "market_phase": "RANGE",
                    "bos_4h": {"type": None, "level": 0.0, "time": None},
                    "bos_15m": {"type": None, "level": 0.0, "time": None},
                    "details": "",
                    "updated_at": None,
                },

                # Agent 2 — Cartographe (Order Blocks & FVG)
                "agent_2_cartographe": {
                    "score": 0,
                    "order_blocks": [],
                    "fvg_zones": [],
                    "active_poi": None,
                    "updated_at": None,
                },

                # Agent 3 — Liquidité
                "agent_3_liquidite": {
                    "score": 0,
                    "equal_highs": [],
                    "equal_lows": [],
                    "retail_trendlines": [],
                    "asian_session": {
                        "high": 0.0,
                        "low": 0.0,
                        "swept": "NONE",
                        "ny_prev_high": 0.0,
                        "ny_prev_low": 0.0,
                    },
                    "liquidity_target": 0.0,
                    "updated_at": None,
                },

                # Agent 4 — Fibonacci (OTE Zone)
                "agent_4_fibonacci": {
                    "score": 0,
                    "swing_low": 0.0,
                    "swing_high": 0.0,
                    "swing_direction": None,
                    "levels": {
                        "0.0": 0.0,
                        "0.236": 0.0,
                        "0.382": 0.0,
                        "0.5": 0.0,
                        "0.618": 0.0,
                        "0.705": 0.0,
                        "0.786": 0.0,
                        "1.0": 0.0,
                        "-0.272": 0.0,
                        "-0.618": 0.0,
                        "-1.0": 0.0,
                    },
                    "price_in_ote": False,
                    "ote_zone": {"high": 0.0, "low": 0.0},
                    "updated_at": None,
                },

                # Agent 5 — Microscope 1M (Entry Trigger)
                "agent_5_microscope": {
                    "score": 0,
                    "state": "SLEEPING",
                    "cpu_usage": 0.0,
                    "trigger_zone": None,
                    "choch_detected": {"type": None, "level": 0.0, "time": None},
                    "bos_1m_confirmed": {"type": None, "level": 0.0, "time": None},
                    "entry_signal": {
                        "direction": None,
                        "entry_price": 0.0,
                        "sl_price": 0.0,
                        "tp_price": 0.0,
                        "risk_reward": 0.0,
                        "confidence": 0.0,
                    },
                    "updated_at": None,
                },

                # Agent 6 — Sentinelle Économique
                "agent_6_sentinelle": {
                    "is_clear": True,                   # Par défaut, on considère le marché safe au boot
                    "next_red_event": {
                        "name": None,
                        "currency": None,
                        "time_utc": None,
                        "impact": None,
                        "minutes_until": None,
                    },
                    "blackout_active": False,
                    "blackout_end": None,
                    "assume_hostile": False,              # [R7]
                    "last_scrape_time": None,
                    "scrape_consecutive_failures": 0,     # [R7]
                    "calendar_events_today": [],
                    "updated_at": None,
                },

                # Agent 7 — Temps & Sessions
                "agent_7_sessions": {
                    "is_clear": False,                   # Par défaut hors Kill Zone au boot
                    "current_session": "OFF_HOURS",
                    "in_killzone": False,
                    "killzone_name": None,
                    "rollover_lockout": False,
                    "dst_offset": 0,
                    "updated_at": None,
                },
            },

            # ── ORCHESTRATEUR ────────────────────────────────────────────
            "orchestrator": {
                "star_rating": 0,
                "all_filters_passed": False,
                "pending_signal": None,
                "last_execution_result": {},
                "last_decision": None,
                "pipeline_active": False,
            },

            # ── AGENT RESULTS (V2) ───────────────────────────────────────
            "agent_results": {
                "agent_1": None,
                "agent_2": None,
                "agent_3": None,
                "agent_4": None,
                "agent_5": None,
                "agent_6": None,
                "agent_7": None,
            },

            # ── TRADE SIGNALS (Orchestrator output) ──────────────────────
            "trade_signals": {},

            # ── ACTIVE TRADES (Suivi par TradeManager) ───────────────────
            "active_trades": {},

            # ── POSITIONS ────────────────────────────────────────────────
            "positions": {
                "open_positions": [],
                "closed_today": [],
                "server_synced": False,
                "last_sync_time": None,
            },

            # ── RATE LIMITER MT5 [R15] ───────────────────────────────────
            "rate_limiter": {
                "mt5_calls_this_second": 0,
                "mt5_last_reset": now,
                "mt5_max_calls_per_second": MT5_MAX_CALLS_PER_SECOND,
                "last_known_tick": None,
            },

            # ── NOTIFICATIONS TELEGRAM [R9] ──────────────────────────────
            "notifications": {
                "telegram_enabled": TELEGRAM_ENABLED,
                "telegram_bot_token": TELEGRAM_BOT_TOKEN,
                "telegram_chat_id": TELEGRAM_CHAT_ID,
                "last_notification_time": None,
                "queue": [],
            },

            # ── PAPER TRADING [R10] ──────────────────────────────────────
            "paper_trading": {
                "enabled": not LIVE_MODE,
                "simulated_trades": [],
                "simulated_equity": PAPER_SIMULATED_EQUITY,
                "csv_path": PAPER_CSV_PATH,
            },

            # ── RECOVERY & PERSISTANCE ───────────────────────────────────
            "recovery": {
                "last_save_time": None,
                "file_path": RECOVERY_FILE_PATH,
                "integrity_hash": "",
            },
        }

        self._logger.info(
            f"Tableau Noir initialisé — "
            f"Mode: {'LIVE 🔴' if LIVE_MODE else 'PAPER 📝'} | "
            f"Bougies: 4H×{CANDLE_HISTORY['4H']} / 15m×{CANDLE_HISTORY['15m']} / 1m×{CANDLE_HISTORY['1m']}"
        )

    # ─────────────────────────────────────────────────────────────────────
    # ACCÈS EN LECTURE (thread-safe)
    # ─────────────────────────────────────────────────────────────────────

    async def read(self, path: str) -> Any:
        """
        Lit une valeur du Tableau Noir de manière thread-safe.

        Args:
            path: Chemin en notation pointée (ex: "meta.state", "agents.agent_1_meteo.score")

        Returns:
            La valeur stockée à ce chemin.

        Raises:
            KeyError: Si le chemin n'existe pas.

        Exemple :
            state = await bb.read("meta.state")
            score = await bb.read("agents.agent_1_meteo.score")
        """
        async with self._lock:
            return self._navigate(path)

    def read_sync(self, path: str) -> Any:
        """
        Lecture SYNCHRONE (sans verrou) — à utiliser UNIQUEMENT dans
        les contextes où le verrou est déjà acquis, ou pour des lectures
        non-critiques (ex: affichage UI).

        ⚠️  Aucune garantie de cohérence en écriture concurrente.
        """
        return self._navigate(path)

    # ─────────────────────────────────────────────────────────────────────
    # ACCÈS EN ÉCRITURE (thread-safe)
    # ─────────────────────────────────────────────────────────────────────

    async def write(self, path: str, value: Any) -> None:
        """
        Écrit une valeur dans le Tableau Noir de manière thread-safe.

        Args:
            path:  Chemin en notation pointée (ex: "meta.state")
            value: Nouvelle valeur à écrire.

        Raises:
            KeyError: Si le chemin parent n'existe pas.

        Exemple :
            await bb.write("meta.state", "READY")
            await bb.write("agents.agent_1_meteo.score", 1)
        """
        async with self._lock:
            keys = path.split(".")
            target = self._data

            # Naviguer jusqu'à l'avant-dernier niveau
            for key in keys[:-1]:
                if isinstance(target, dict):
                    target = target[key]
                else:
                    raise KeyError(f"Chemin invalide: '{path}' — '{key}' n'est pas un dict")

            # Écrire la valeur finale
            final_key = keys[-1]
            if isinstance(target, dict):
                target[final_key] = value
            else:
                raise KeyError(f"Chemin invalide: '{path}' — impossible d'écrire dans un non-dict")

    async def update_dict(self, path: str, updates: dict) -> None:
        """
        Met à jour plusieurs clés d'un sous-dictionnaire en une seule acquisition
        du verrou. Plus efficace que plusieurs appels à write() séparés.

        Args:
            path:    Chemin vers le dictionnaire cible (ex: "agents.agent_1_meteo")
            updates: Dictionnaire des clés/valeurs à mettre à jour.

        Exemple :
            await bb.update_dict("agents.agent_1_meteo", {
                "score": 1,
                "bias": "BULLISH",
                "market_phase": "EXPANSION",
                "updated_at": datetime.now(tz=timezone.utc),
            })
        """
        async with self._lock:
            target = self._navigate(path)
            if not isinstance(target, dict):
                raise TypeError(f"'{path}' n'est pas un dictionnaire — update_dict impossible")
            target.update(updates)

    # ─────────────────────────────────────────────────────────────────────
    # KILL EVENT [R1] — Accès direct sans verrou (asyncio.Event est thread-safe)
    # ─────────────────────────────────────────────────────────────────────

    @property
    def kill_event(self) -> asyncio.Event:
        """Retourne l'Event global du Kill Switch (lecture directe, sans verrou)."""
        return self._kill_event

    def trigger_kill(self) -> None:
        """Active le Kill Switch. Irréversible sans redémarrage."""
        self._kill_event.set()
        self._data["meta"]["kill_switch"] = True
        self._data["meta"]["state"] = "KILLED"
        self._logger.critical("🔴🔴🔴 KILL EVENT DÉCLENCHÉ — Système en arrêt d'urgence 🔴🔴🔴")

    # ─────────────────────────────────────────────────────────────────────
    # SNAPSHOT (pour le recovery.json)
    # ─────────────────────────────────────────────────────────────────────

    async def snapshot(self) -> dict:
        """
        Retourne une copie profonde sérialisable du Tableau Noir.
        Utilisé par le Recovery Manager pour sauvegarder l'état.

        Note : les objets asyncio.Event et deque sont convertis pour
        être compatibles JSON.
        """
        import copy
        async with self._lock:
            raw = copy.deepcopy(self._data)

        # Nettoyer les objets non-sérialisables
        raw["meta"].pop("kill_event", None)

        # Convertir les deque en listes
        for tf, dq in raw["market_data"]["candles"].items():
            raw["market_data"]["candles"][tf] = list(dq)

        return raw

    # ─────────────────────────────────────────────────────────────────────
    # UTILITAIRES INTERNES
    # ─────────────────────────────────────────────────────────────────────

    def _navigate(self, path: str) -> Any:
        """
        Navigue dans le dictionnaire imbriqué en suivant un chemin pointé.
        Usage interne uniquement — pas de verrou ici.
        """
        keys = path.split(".")
        target = self._data
        for key in keys:
            if isinstance(target, dict):
                if key not in target:
                    raise KeyError(f"Clé '{key}' introuvable dans le chemin '{path}'")
                target = target[key]
            else:
                raise KeyError(f"Chemin invalide: '{path}' — tentative de naviguer dans un non-dict")
        return target

    def __repr__(self) -> str:
        state = self._data.get("meta", {}).get("state", "UNKNOWN")
        mode = "LIVE" if self._data.get("meta", {}).get("live_mode") else "PAPER"
        stars = self._data.get("orchestrator", {}).get("star_rating", 0)
        n_pos = len(self._data.get("positions", {}).get("open_positions", []))
        return f"<BlackBoard state={state} mode={mode} stars={stars}★ positions={n_pos}>"

    # ─────────────────────────────────────────────────────────────────────
    # V2 EVENT BUS METHODS
    # ─────────────────────────────────────────────────────────────────────

    async def write_agent_result(self, agent_id: str, result: AgentResult):
        """
        Appelé par chaque agent quand il a fini son calcul.
        Publie le résultat ET notifie tous les abonnés instantanément.
        """
        async with self._lock:
            self._data["agent_results"][agent_id] = result
            # Propager la direction de l'Agent 1 à tout le système
            if agent_id == "agent_1" and result.direction:
                self._data["meta"]["current_direction"] = result.direction
        
        # Déclencher l'événement SANS le lock (évite les deadlocks)
        event_name = f"{agent_id}_ready"
        if event_name in self._events:
            self._events[event_name].set()
        
        # Notifier les callbacks abonnés
        for callback in self._subscribers.get(event_name, []):
            asyncio.create_task(callback(result))
    
    async def wait_for_agent(self, agent_id: str, timeout: float = 5.0) -> Optional[AgentResult]:
        """
        Attend le résultat d'un agent spécifique avec timeout de sécurité.
        Usage : résultat = await blackboard.wait_for_agent("agent_1")
        """
        event = self._events.get(f"{agent_id}_ready")
        if not event:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._data["agent_results"].get(agent_id)
        except asyncio.TimeoutError:
            return None
    
    async def notify_price_in_poi(self, zone_data: dict):
        """
        Appelé par Agent 2 quand le prix entre dans un POI (OB/FVG).
        Réveille Agent 5 (Microscope) immédiatement — sans attendre la prochaine bougie.
        """
        async with self._lock:
            self._data["meta"]["active_poi"] = zone_data
        self._events["price_in_poi"].set()
    
    def reset_pipeline(self):
        """Reset des événements pour le prochain cycle d'analyse."""
        for event in self._events.values():
            event.clear()
        for agent_id in self._data["agent_results"]:
            self._data["agent_results"][agent_id] = None

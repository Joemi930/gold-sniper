# =============================================================================
# GOLD SNIPER v1.0 -- TABLEAU NOIR (BLACKBOARD / SHARED STATE)
# =============================================================================
#
# Le Tableau Noir est le dictionnaire central en RAM ou TOUS les agents
# ecrivent leurs donnees. C'est la cle de voute de l'architecture.
#
# Protege par un asyncio.Lock() pour garantir la coherence lors d'ecritures
# concurrentes. Chaque agent ecrit dans sa propre cle sans interference.
#
# Reference : architecture.md -- Section 2.2
#
# =============================================================================

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional, Callable
# NOTE : AgentResult est importe localement dans write_agent_result()
# pour eviter l'import circulaire (base_agent importe BlackBoard).

from config import (
    LIVE_MODE,
    CANDLE_HISTORY,
    MT5_MAX_CALLS_PER_SECOND,
    DISCORD_ENABLED,
    DISCORD_TOKEN,
    TELEGRAM_ENABLED,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    PAPER_SIMULATED_EQUITY,
    PAPER_CSV_PATH,
    RECOVERY_FILE_PATH,
    MAX_SPREAD_POINTS,
    MT5_SYMBOL,
)
from utils.logger import get_logger


class BlackBoard:
    """
    Tableau Noir -- Etat global partage entre toutes les coroutines.

    Structure exacte definie dans architecture.md Sec. 2.2.
    Le dictionnaire interne `_data` n'est jamais accede directement :
    on utilise les methodes `read()` et `write()` qui gerent le verrou.

    Usage :
        bb = BlackBoard()
        await bb.write("meta.state", "READY")
        state = await bb.read("meta.state")
    """

    def __init__(self) -> None:
        self._logger = get_logger()
        self._lock = asyncio.Lock()

        # [R1] Event global pour le Kill Switch -- verifie avant chaque order_send
        self._kill_event = asyncio.Event()
        self._agent_update_event = asyncio.Event()
        self._candle_close_event = asyncio.Event()
        self._critical_orchestrator_event = asyncio.Event()

        # [DASHBOARD] Event declenche a chaque write_agent_result() OU update_market().
        # Le WebSocket l'attend au lieu de dormir 500ms -> latence < 5ms.
        self._dashboard_update_event = asyncio.Event()

        self._agent_update_sequence = 0
        self._latest_agent_update: dict[str, Any] = {
            "sequence": 0,
            "agent_id": None,
            "published_at": None,
            "published_at_perf": None,
        }
        self._candle_close_sequence = 0
        self._latest_candle_close: dict[str, Any] = {
            "sequence": 0,
            "timeframe": None,
            "candle_time": None,
            "closed_at": None,
        }
        self._latest_critical_orchestrator_trigger: dict[str, Any] = {
            "source": None,
            "reason": None,
            "triggered_at": None,
        }
        self._agent_dashboard_last_publish: dict[str, float] = {}

        # Timestamp de creation
        now = datetime.now(tz=timezone.utc)

        # Bus d'evenements V2 : cle = nom de l'evenement, valeur = liste de callbacks
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
            "price_in_poi": asyncio.Event(),   # Agent 2 active cet event -> reveille Agent 5
            "pipeline_reset": asyncio.Event(),
        }

        # -------------------------------------------------------------------------
        # STRUCTURE COMPLETE DU TABLEAU NOIR
        # -------------------------------------------------------------------------
        self._data: dict[str, Any] = {

            # -- META : Etat global du systeme ------------------------------------
            "meta": {
                "boot_time": now,
                "last_tick_time": None,
                "state": "BOOT",           # BOOT -> READY -> TRADING -> COOLDOWN -> HALTED -> KILLED
                "live_mode": LIVE_MODE,    # [R10] False = Paper Trading
                "kill_switch": False,
                "kill_event": self._kill_event,   # [R1] Objet asyncio.Event partage
                "daily_trade_count": 0,
                "cooldown_until": None,
                "friday_mode": {           # [R16]
                    "risk_reduced": False,
                    "trading_halted": False,
                },
            },

            # -- CONTROL : Pause / resume / risque live ---------------------------
            "control": {
                "paused": False,
                "pause_reason": None,
                "paused_at": None,
                "resumed_at": None,
                "risk_pct_per_trade": None,
                "risk_updated_at": None,
                "memory_pause": False,
                "memory_pause_at": None,
                "memory_resumed_at": None,
                "memory_loss_count_alerted": 0,
            },

            # -- MEMORY : Compteurs de memoire ------------------------------------
            "memory": {
                "last_recorded_trade": None,
                "loss_count": 0,
                "last_loss_analysis": None,
                "updated_at": None,
            },

            # -- MARKET DATA : Prix, bougies, infos symbole -----------------------
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
                "atr_14": None,  # [V2] Calcule 1 fois, partage par TOUS les agents
            },

            # -- MARKET ANALYSIS : Analyses partagees des agents ------------------
            "market_analysis": {
                "market_structure": {
                    "overall_bias": "NEUTRAL",
                    "trend_4h": "NEUTRAL",
                    "trend_15m": "NEUTRAL",
                },
                "zones": {},
                "liquidity_pools": {
                    "eqh": [],
                    "eql": [],
                },
                "ote_zone": {},
                "microscope": {
                    "is_choch_detected": False,
                    "choch_details": {},
                },
            },

            # -- RISK MANAGEMENT : Boucliers et filtres OPSEC ---------------------
            "risk_management": {
                "volatility_gate": {
                    "allow_trade": True,
                    "next_news_time": None,
                },
                "session_gate": {
                    "allow_trade": False,
                    "current_session": "OFF_HOURS",
                },
            },

            # -- MARKET V2 (Shared State) -----------------------------------------
            "market": {
                "symbol": MT5_SYMBOL,
                "current_price": None,
                "bid": None,
                "ask": None,
                "spread_points": None,
                "atr_14_1m": None,
                "atr_14_15m": None,
                "atr_14_4h": None,
                "regime": "UNKNOWN",
                "regime_confidence": 0.0,
                "regime_description": "",
                "regime_updated_at": None,
                "dxy_bias": "NEUTRAL",
                "gold_macro_bias": "NEUTRAL",
                "gold_trend": "NEUTRAL",
                "us10y_direction": None,
                "real_rate_favorable": False,
                "pearson_dxy_gold": 0.0,
                "macro_signal_strength": "FAIBLE",
                "macro_score_bonus": 0,
                "macro_feed_alive": False,
                "macro_correlation_interval": "15m",
                "macro_correlation_window": 20,
                "last_macro_update": None,
                "session": "NONE",
                "spread_monitor": {
                    "allow_trade": True,
                    "spread": 0.0,
                    "max_allowed": MAX_SPREAD_POINTS,
                    "reason": "",
                    "high_since": None,
                    "last_alert_at": None,
                    "rollover_detected": False,
                    "news_detected": False,
                },
                "last_tick": None,
            },

            # -- AGENTS V2 --------------------------------------------------------
            "agents": {
                "agent_1": {
                    "score": 0,
                    "direction": None,
                    "bias_4h": None,
                    "bias_15m": None,
                    "mtf_aligned": False,
                    "last_swing_high_4h": None,
                    "last_swing_low_4h": None,
                    "last_swing_high_15m": None,
                    "last_swing_low_15m": None,
                    "bos_level": None,
                    "swing_quality": 0.0,
                    "reason": "",
                    "last_updated": None,
                    "hard_filter_pass": False,
                },
                "agent_2": {
                    "score": 0,
                    "direction": None,
                    "active_ob": None,
                    "active_fvg": None,
                    "breaker_blocks": [],
                    "poi_zone": None,
                    "ob_score": 0,
                    "zone_is_fresh": False,
                    "reason": "",
                    "last_updated": None,
                    "hard_filter_pass": False,
                },
                "agent_3": {
                    "score": 0,
                    "direction": None,
                    "eqh_levels": [],
                    "eql_levels": [],
                    "sweep_detected": False,
                    "sweep_side": None,
                    "sweep_depth_ratio": 0.0,
                    "asian_range": None,
                    "idm_detected": False,
                    "idm_swept": False,
                    "reason": "",
                    "last_updated": None,
                },
                "agent_4": {
                    "score": 0,
                    "direction": None,
                    "swing_used": None,
                    "ote_low": None,
                    "ote_high": None,
                    "ote_sweet": None,
                    "equilibrium": None,
                    "in_ote": False,
                    "in_discount": False,
                    "in_premium": False,
                    "precision_pct": 0.0,
                    "reason": "",
                    "last_updated": None,
                },
                "agent_5": {
                    "score": 0,
                    "direction": None,
                    "choch_detected": False,
                    "choch_price": None,
                    "price_in_poi": False,
                    "sweep_1m_confirmed": False,
                    "amd_phase": 0,
                    "entry_price": None,
                    "sl_price": None,
                    "tp1_price": None,
                    "tp2_price": None,
                    "reason": "",
                    "last_updated": None,
                },
                "agent_6": {
                    "score": 100,
                    "blocked": False,
                    "veto": False,
                    "impact_level": "NONE",
                    "next_event": None,
                    "resume_at": None,
                    "feed_alive": True,
                    "stealth_mode": False,
                    "reason": "",
                    "last_updated": None,
                },
                "agent_7": {
                    "score": 0,
                    "in_kill_zone": False,
                    "kill_zone_name": None,
                    "risk_modifier": 1.0,
                    "trading_allowed": False,
                    "vp_poc": None,
                    "vp_vah": None,
                    "vp_val": None,
                    "price_in_value_area": False,
                    "session_name": None,
                    "reason": "",
                    "last_updated": None,
                },
                "risk_manager": {
                    "score": 100,
                    "veto": False,
                    "equity_protection_active": False,
                    "paper_mode_forced": False,
                    "consecutive_losses": 0,
                    "daily_loss_pct": 0.0,
                    "pause_until": None,
                    "trades_today": 0,
                    "reason": "",
                    "last_updated": None,
                },
            },

            # -- ORCHESTRATEUR V2 -------------------------------------------------
            "orchestrator": {
                "final_score": 0,
                "stars": 0,
                "decision": "WAIT",
                "direction": None,
                "last_signal_time": None,
                "signal_age_seconds": 0,
                "regime_weight_modifier": 1.0,
                "last_updated": None,
            },

            # -- PERFORMANCE V2 ---------------------------------------------------
            "performance": {
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "winrate": 0.0,
                "total_pnl": 0.0,
                "daily_pnl": 0.0,
                "agent_accuracy": {
                    "agent_1": {"correct": 0, "total": 0, "accuracy": 0.0},
                    "agent_2": {"correct": 0, "total": 0, "accuracy": 0.0},
                    "agent_3": {"correct": 0, "total": 0, "accuracy": 0.0},
                    "agent_4": {"correct": 0, "total": 0, "accuracy": 0.0},
                    "agent_5": {"correct": 0, "total": 0, "accuracy": 0.0},
                },
            },

            # -- AGENT RESULTS (V2) -----------------------------------------------
            "agent_results": {
                "agent_1": None,
                "agent_2": None,
                "agent_3": None,
                "agent_4": None,
                "agent_5": None,
                "agent_6": None,
                "agent_7": None,
            },

            # -- TRADE SIGNALS (Orchestrator output) ------------------------------
            "trade_signals": {},

            # -- ACTIVE TRADES (Suivi par TradeManager) ---------------------------
            "active_trades": {},

            # -- POSITIONS --------------------------------------------------------
            "positions": {
                "open_positions": [],
                "closed_today": [],
                "server_synced": False,
                "last_sync_time": None,
            },

            # -- RATE LIMITER MT5 [R15] -------------------------------------------
            "rate_limiter": {
                "mt5_calls_this_second": 0,
                "mt5_last_reset": now,
                "mt5_max_calls_per_second": MT5_MAX_CALLS_PER_SECOND,
                "last_known_tick": None,
            },

            # -- NOTIFICATIONS DISCORD --------------------------------------------
            "notifications": {
                "discord_enabled": DISCORD_ENABLED,
                "discord_token": DISCORD_TOKEN,
                "telegram_enabled": TELEGRAM_ENABLED,
                "telegram_bot_token": TELEGRAM_BOT_TOKEN,
                "telegram_chat_id": TELEGRAM_CHAT_ID,
                "last_notification_time": None,
                "queue": [],
            },

            # -- PAPER TRADING [R10] ----------------------------------------------
            "paper_trading": {
                "enabled": not LIVE_MODE,
                "simulated_trades": [],
                "simulated_equity": PAPER_SIMULATED_EQUITY,
                "csv_path": PAPER_CSV_PATH,
            },

            # -- RECOVERY & PERSISTANCE -------------------------------------------
            "recovery": {
                "last_save_time": None,
                "file_path": RECOVERY_FILE_PATH,
                "integrity_hash": "",
            },
        }

        self._logger.info(
            f"Tableau Noir initialise -- "
            f"Mode: {'LIVE' if LIVE_MODE else 'PAPER'} | "
            f"Bougies: 4H x{CANDLE_HISTORY['4H']} / 15m x{CANDLE_HISTORY['15m']} / 1m x{CANDLE_HISTORY['1m']}"
        )

    # -------------------------------------------------------------------------
    # ACCES EN LECTURE (thread-safe)
    # -------------------------------------------------------------------------

    async def read(self, path: str) -> Any:
        """
        Lit une valeur du Tableau Noir de maniere thread-safe.

        Args:
            path: Chemin en notation pointee (ex: "meta.state", "agents.agent_1.score")

        Returns:
            La valeur stockee a ce chemin.

        Raises:
            KeyError: Si le chemin n'existe pas.
        """
        async with self._lock:
            return self._navigate(path)

    def read_sync(self, path: str) -> Any:
        """
        Lecture SYNCHRONE (sans verrou) -- a utiliser UNIQUEMENT dans
        les contextes ou le verrou est deja acquis, ou pour des lectures
        non-critiques (ex: affichage UI).

        WARNING : Aucune garantie de coherence en ecriture concurrente.
        """
        return self._navigate(path)

    # -------------------------------------------------------------------------
    # ACCES EN ECRITURE (thread-safe)
    # -------------------------------------------------------------------------

    async def write(self, path: str, value: Any) -> None:
        """
        Ecrit une valeur dans le Tableau Noir de maniere thread-safe.

        Args:
            path:  Chemin en notation pointee (ex: "meta.state")
            value: Nouvelle valeur a ecrire.

        Raises:
            KeyError: Si le chemin parent n'existe pas.
        """
        async with self._lock:
            keys = path.split(".")
            target = self._data

            for key in keys[:-1]:
                if isinstance(target, dict):
                    target = target[key]
                else:
                    raise KeyError(f"Chemin invalide: '{path}' -- '{key}' n'est pas un dict")

            final_key = keys[-1]
            if isinstance(target, dict):
                target[final_key] = value
            else:
                raise KeyError(f"Chemin invalide: '{path}' -- impossible d'ecrire dans un non-dict")

    async def update_dict(self, path: str, updates: dict) -> None:
        """
        Met a jour plusieurs cles d'un sous-dictionnaire en une seule acquisition
        du verrou. Plus efficace que plusieurs appels a write() separes.

        Args:
            path:    Chemin vers le dictionnaire cible (ex: "agents.agent_1")
            updates: Dictionnaire des cles/valeurs a mettre a jour.
        """
        async with self._lock:
            target = self._navigate(path)
            if not isinstance(target, dict):
                raise TypeError(f"'{path}' n'est pas un dictionnaire -- update_dict impossible")
            target.update(updates)

    # -------------------------------------------------------------------------
    # KILL EVENT [R1] -- Acces direct sans verrou (asyncio.Event est thread-safe)
    # -------------------------------------------------------------------------

    @property
    def kill_event(self) -> asyncio.Event:
        """Retourne l'Event global du Kill Switch (lecture directe, sans verrou)."""
        return self._kill_event

    @property
    def dashboard_update_event(self) -> asyncio.Event:
        """
        Event declenche a chaque write_agent_result() ou update_market().
        Le handler WebSocket l'attend pour pousser les donnees en < 5ms
        au lieu de poller toutes les 500ms.
        """
        return self._dashboard_update_event

    @property
    def candle_close_event(self) -> asyncio.Event:
        """Event declenche uniquement a la cloture d'une bougie 1m."""
        return self._candle_close_event

    @property
    def critical_orchestrator_event(self) -> asyncio.Event:
        """Event voie rapide pour les veto agent_6 et risk_manager."""
        return self._critical_orchestrator_event

    def trigger_kill(self) -> None:
        """Active le Kill Switch. Irreversible sans redemarrage."""
        self._kill_event.set()
        self._data["meta"]["kill_switch"] = True
        self._data["meta"]["state"] = "KILLED"
        self._logger.critical("KILL EVENT DECLENCHE -- Systeme en arret d'urgence")

    # -------------------------------------------------------------------------
    # SNAPSHOT (pour le recovery.json)
    # -------------------------------------------------------------------------

    async def snapshot(self) -> dict:
        """
        Retourne une copie profonde serialisable du Tableau Noir.
        Utilise par le Recovery Manager pour sauvegarder l'etat.

        Note : les objets asyncio.Event et deque sont convertis pour
        etre compatibles JSON.
        """
        import copy
        async with self._lock:
            raw = copy.deepcopy(self._data)

        # Nettoyer les objets non-serialisables
        raw["meta"].pop("kill_event", None)

        # Convertir les deque en listes
        for tf, dq in raw["market_data"]["candles"].items():
            raw["market_data"]["candles"][tf] = list(dq)

        return raw

    # -------------------------------------------------------------------------
    # UTILITAIRES INTERNES
    # -------------------------------------------------------------------------

    def _navigate(self, path: str) -> Any:
        """
        Navigue dans le dictionnaire imbrique en suivant un chemin pointe.
        Usage interne uniquement -- pas de verrou ici.
        """
        keys = path.split(".")
        target = self._data
        for key in keys:
            if isinstance(target, dict):
                if key not in target:
                    raise KeyError(f"Cle '{key}' introuvable dans le chemin '{path}'")
                target = target[key]
            else:
                raise KeyError(f"Chemin invalide: '{path}' -- tentative de naviguer dans un non-dict")
        return target

    # -------------------------------------------------------------------------
    # V2 METHODS (Direct access & Market context)
    # -------------------------------------------------------------------------

    async def update_agent(self, agent_key: str, data: dict) -> None:
        """Mise a jour atomique et thread-safe d'un agent."""
        critical_trigger: tuple[str, str] | None = None
        async with self._lock:
            if agent_key in self._data["agents"]:
                was_veto = bool(self._data["agents"][agent_key].get("veto", False))
                self._data["agents"][agent_key].update(data)
                self._data["agents"][agent_key]["last_updated"] = datetime.utcnow()
                is_veto = bool(self._data["agents"][agent_key].get("veto", False))
                if agent_key in {"agent_6", "risk_manager"} and is_veto and not was_veto:
                    critical_trigger = (
                        agent_key,
                        str(self._data["agents"][agent_key].get("reason", "")),
                    )

        if critical_trigger is not None:
            source, reason = critical_trigger
            self.notify_critical_orchestrator_trigger(source, reason)

    async def update_market(self, data: dict) -> None:
        """Mise a jour des donnees marche globales."""
        async with self._lock:
            if "market" in self._data:
                self._data["market"].update(data)
                self._data["market"]["last_tick"] = datetime.utcnow()
        # Notifier le dashboard immediatement (hors lock pour eviter deadlock)
        self._dashboard_update_event.set()

    def get_agent(self, key: str) -> dict:
        """Lecture non-bloquante d'un agent (quelques ms de delai acceptables)."""
        return self._data.get("agents", {}).get(key, {})

    def get_market(self) -> dict:
        return self._data.get("market", {})

    def get_all(self) -> dict:
        return self._data

    def __repr__(self) -> str:
        state = self._data.get("meta", {}).get("state", "UNKNOWN")
        mode = "LIVE" if self._data.get("meta", {}).get("live_mode") else "PAPER"
        stars = self._data.get("orchestrator", {}).get("star_rating", 0)
        n_pos = len(self._data.get("positions", {}).get("open_positions", []))
        return f"<BlackBoard state={state} mode={mode} stars={stars}* positions={n_pos}>"

    # -------------------------------------------------------------------------
    # V2 EVENT BUS METHODS
    # -------------------------------------------------------------------------

    async def write_agent_result(self, agent_id: str, result, *, trigger_orchestrator: bool = True) -> None:
        """
        Appele par chaque agent quand il a fini son calcul.
        Publie le resultat ET notifie tous les abonnes instantanement.
        Declenche aussi dashboard_update_event pour le WebSocket (< 5ms).
        """
        published_at = datetime.now(tz=timezone.utc)
        published_at_perf = time.perf_counter()
        async with self._lock:
            self._data["agent_results"][agent_id] = result
            # Propager la direction de l'Agent 1 a tout le systeme
            if agent_id == "agent_1" and result.direction:
                self._data["meta"]["current_direction"] = result.direction
            if trigger_orchestrator:
                self._agent_update_sequence += 1
                self._latest_agent_update = {
                    "sequence": self._agent_update_sequence,
                    "agent_id": agent_id,
                    "published_at": published_at,
                    "published_at_perf": published_at_perf,
                }

        # Declencher les evenements SANS le lock (evite les deadlocks)
        event_name = f"{agent_id}_ready"
        if event_name in self._events:
            self._events[event_name].set()
        if trigger_orchestrator:
            self._agent_update_event.set()
        if agent_id == "agent_6" and bool(getattr(result, "veto", False)):
            self.notify_critical_orchestrator_trigger(agent_id, str(getattr(result, "reason", "")))
        self._dashboard_update_event.set()   # [DASHBOARD] Push immediat < 5ms

        # Notifier les callbacks abonnes
        for callback in self._subscribers.get(event_name, []):
            asyncio.create_task(callback(result))

    async def publish_agent_dashboard(
        self,
        agent_id: str,
        result,
        *,
        min_interval_sec: float = 0.0,
        trigger_orchestrator: bool = True,
    ) -> bool:
        """
        Publie un resultat agent et declenche dashboard_update_event.

        min_interval_sec > 0 : throttle (ex. Agent 5 boucle tick rapide).
        min_interval_sec == 0 : publication a chaque appel (fin de cycle agents 1-4).
        trigger_orchestrator=False : pulse dashboard uniquement (IDLE/WAITING).
        """
        if min_interval_sec > 0:
            last = self._agent_dashboard_last_publish.get(agent_id, 0.0)
            if (time.monotonic() - last) < min_interval_sec:
                return False
        await self.write_agent_result(
            agent_id,
            result,
            trigger_orchestrator=trigger_orchestrator,
        )
        self._agent_dashboard_last_publish[agent_id] = time.monotonic()
        return True

    async def notify_candle_close(self, timeframe: str, candle: dict[str, Any]) -> None:
        """Publie la cloture d'une bougie. Seule la 1m cadence l'orchestrateur."""
        if timeframe != "1m":
            return

        closed_at = datetime.now(tz=timezone.utc)
        async with self._lock:
            self._candle_close_sequence += 1
            self._latest_candle_close = {
                "sequence": self._candle_close_sequence,
                "timeframe": timeframe,
                "candle_time": candle.get("time"),
                "closed_at": closed_at,
            }

        self._candle_close_event.set()

    async def wait_for_candle_close(self, last_sequence: int = 0) -> dict[str, Any]:
        """
        Attend la prochaine cloture de bougie 1m.
        Retourne les metadonnees de cloture, sans polling.
        """
        while not self.kill_event.is_set():
            async with self._lock:
                latest = dict(self._latest_candle_close)
                if latest["sequence"] > last_sequence:
                    latest["trigger"] = "candle_close"
                    return latest
                self._candle_close_event.clear()

            await self._candle_close_event.wait()

        return {"trigger": "kill"}

    def notify_critical_orchestrator_trigger(self, source: str, reason: str = "") -> None:
        """Declenche une decision immediate pour les veto critiques."""
        self._latest_critical_orchestrator_trigger = {
            "source": source,
            "reason": reason,
            "triggered_at": datetime.now(tz=timezone.utc),
        }
        self._critical_orchestrator_event.set()

    async def wait_for_critical_orchestrator_trigger(self) -> dict[str, Any]:
        """Attend un veto agent_6 ou risk_manager devant court-circuiter la bougie."""
        while not self.kill_event.is_set():
            latest = dict(self._latest_critical_orchestrator_trigger)
            if self._critical_orchestrator_event.is_set() and latest.get("source"):
                self._critical_orchestrator_event.clear()
                latest["trigger"] = "critical_veto"
                return latest
            self._critical_orchestrator_event.clear()
            await self._critical_orchestrator_event.wait()
            latest = dict(self._latest_critical_orchestrator_trigger)
            if latest.get("source"):
                latest["trigger"] = "critical_veto"
                return latest

        return {"trigger": "kill"}

    async def wait_for_agent_update(self, last_sequence: int = 0,
                                    timeout: float = 5.0) -> Optional[dict[str, Any]]:
        """
        Attend la prochaine publication d'agent sans polling.
        Retourne les metadonnees de publication ou None au timeout de secours.
        """
        while not self.kill_event.is_set():
            async with self._lock:
                latest = dict(self._latest_agent_update)
                if latest["sequence"] > last_sequence:
                    return latest
                self._agent_update_event.clear()

            try:
                await asyncio.wait_for(self._agent_update_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
        return None

    async def wait_for_agent(self, agent_id: str, timeout: float = 5.0) -> Optional["AgentResult"]:
        """
        Attend le resultat d'un agent specifique avec timeout de securite.
        Usage : resultat = await blackboard.wait_for_agent("agent_1")
        """
        event = self._events.get(f"{agent_id}_ready")
        if not event:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._data["agent_results"].get(agent_id)
        except asyncio.TimeoutError:
            return None

    async def notify_price_in_poi(self, zone_data: dict) -> None:
        """
        Appele par Agent 2 quand le prix entre dans un POI (OB/FVG).
        Reveille Agent 5 (Microscope) immediatement -- sans attendre la prochaine bougie.
        """
        async with self._lock:
            self._data["meta"]["active_poi"] = zone_data
        self._events["price_in_poi"].set()

    def reset_pipeline(self) -> None:
        """Reset des evenements pour le prochain cycle d'analyse."""
        for event in self._events.values():
            event.clear()
        for agent_id in self._data["agent_results"]:
            self._data["agent_results"][agent_id] = None


# =============================================================================
# SINGLETON GLOBAL -- Importe par tous les agents et l'orchestrateur
# Usage : from core.blackboard import BLACKBOARD
# =============================================================================
BLACKBOARD = BlackBoard()

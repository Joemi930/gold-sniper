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
# NOTE : AgentResult est importé localement dans write_agent_result()
# pour éviter l'import circulaire (base_agent importe BlackBoard).

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
    MAX_SPREAD_POINTS,
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

            # ── MARKET V2 (Shared State) ─────────────────────────────────
            "market": {
                "symbol": "XAUUSD",
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
                "macro_score_bonus": 0,
                "macro_feed_alive": False,
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

            # ── AGENTS V2 ───────────────────────────────────────────────
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

            # ── ORCHESTRATEUR V2 ───────────────────────────────────────────
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

            # ── PERFORMANCE V2 ─────────────────────────────────────────────
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
            path: Chemin en notation pointée (ex: "meta.state", "agents.agent_1.score")

        Returns:
            La valeur stockée à ce chemin.

        Raises:
            KeyError: Si le chemin n'existe pas.

        Exemple :
            state = await bb.read("meta.state")
            score = await bb.read("agents.agent_1.score")
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
            await bb.write("agents.agent_1.score", 1)
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
            path:    Chemin vers le dictionnaire cible (ex: "agents.agent_1")
            updates: Dictionnaire des clés/valeurs à mettre à jour.

        Exemple :
            await bb.update_dict("agents.agent_1", {
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

    # ─────────────────────────────────────────────────────────────────────
    # V2 METHODS (Direct access & Market context)
    # ─────────────────────────────────────────────────────────────────────

    async def update_agent(self, agent_key: str, data: dict) -> None:
        """Mise à jour atomique et thread-safe d'un agent."""
        async with self._lock:
            if agent_key in self._data["agents"]:
                self._data["agents"][agent_key].update(data)
                self._data["agents"][agent_key]["last_updated"] = datetime.utcnow()
    
    async def update_market(self, data: dict) -> None:
        """Mise à jour des données marché globales."""
        async with self._lock:
            if "market" in self._data:
                self._data["market"].update(data)
                self._data["market"]["last_tick"] = datetime.utcnow()
    
    def get_agent(self, key: str) -> dict:
        """Lecture non-bloquante d'un agent (quelques ms de délai acceptables)."""
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
        return f"<BlackBoard state={state} mode={mode} stars={stars}★ positions={n_pos}>"

    # ─────────────────────────────────────────────────────────────────────
    # V2 EVENT BUS METHODS
    # ─────────────────────────────────────────────────────────────────────

    async def write_agent_result(self, agent_id: str, result):
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
    
    async def wait_for_agent(self, agent_id: str, timeout: float = 5.0) -> Optional["AgentResult"]:
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


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON GLOBAL — Importé par tous les agents et l'orchestrateur
# Usage : from core.blackboard import BLACKBOARD
# ─────────────────────────────────────────────────────────────────────────────
BLACKBOARD = BlackBoard()

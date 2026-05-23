# 🏗️ ARCHITECTURE.MD — GOLD SNIPER v2.1
## Robot de Trading Institutionnel XAUUSD (MetaTrader 5 / Python)
### Méthodologie : ICT/SMC (Inner Circle Trader — Smart Money Concepts)

> **Référence stratégique :** Kasper — *"Mon Processus de Prise de Trade de A à Z"*
> **Actif unique :** XAUUSD (Or spot contre Dollar US)
> **Aucun code Python dans ce document.** Logique mathématique pure, flux de données et sécurités.

---

## TABLE DES MATIÈRES

1. [Vue Globale du Système](#1-vue-globale-du-système)
2. [Moteur Asynchrone et Gestion d'État (Backbone)](#2-moteur-asynchrone-et-gestion-détat-backbone)
3. [Les 7 Agents Analytiques (Règle des 5 Étoiles)](#3-les-7-agents-analytiques-règle-des-5-étoiles)
4. [Agent Orchestrateur et Filtre d'Unanimité](#4-agent-orchestrateur-et-filtre-dunanimité)
5. [Exécution Chirurgicale et OPSEC (Filtres MT5)](#5-exécution-chirurgicale-et-opsec-filtres-mt5)
6. [Gestion de Trade (Trade Management)](#6-gestion-de-trade-trade-management)
7. [Interface Graphique (UI) et Watchdog](#7-interface-graphique-ui-et-watchdog)
8. [Schéma des Flux de Données](#8-schéma-des-flux-de-données)
9. [Arbre de Fichiers du Projet](#9-arbre-de-fichiers-du-projet)
10. [Recommandations du Sniper (Œil du Sniper)](#10-recommandations-du-sniper-œil-du-sniper)

---

## 🌟 NOUVEAUTÉS VERSION 2.1 (MAJ)

L'architecture a été mise à jour en V2.1 pour inclure :
- **Tableau Noir V2 (Blackboard)** : Remplacement des queues par un bus d'événements asynchrone (Publish/Subscribe) et des `asyncio.Lock`.
- **Orchestrateur V2** : Système de score dynamique (Seuil de tir à 90/100, Seuil de log à 75/100) remplaçant la stricte unanimité binaire.
- **Interface Graphique Dashboard** : UI 1280x720 redimensionnable avec réseau neuronal animé, pipeline d'agents et logs temps réel.
- **OPSEC et Sécurité renforcées** : Daily Drawdown Limit (Arrêt à -5%), Limite de 2 trades/jour (MAX_TRADES_PER_DAY), Kill Switch d'urgence.
- **Trade Management avancé** : Clôture partielle 50% + Break-even automatique à 1:1 R:R, suivi d'un Trailing Stop basé sur l'ATR.
- **Recovery Manager** : Reprise sur crash, scan des positions orphelines MT5 au redémarrage et réinjection dans le moteur de trade.
- **Telegram Notifier** : Alertes EOD (End Of Day) et notifications en direct des trades.

---

## 1. VUE GLOBALE DU SYSTÈME

### 1.1 Philosophie Architecturale

Le système est un **automate à états finis événementiel**, entièrement piloté par `asyncio`. Il ne prend **aucune décision basée sur un seul signal** : un trade n'est ouvert que par **unanimité absolue (5/5)** des 5 agents analytiques de marché, après validation des 2 agents de filtrage contextuel (Sentinelle Éco + Temps & Sessions).

```
┌─────────────────────────────────────────────────────────────┐
│                    GOLD SNIPER v1.0                         │
│                                                             │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │  MT5 API │◄─►│ ASYNC ENGINE │◄─►│  TABLEAU NOIR    │    │
│  │ (Bridge) │   │  (asyncio)   │   │  (Shared State)  │    │
│  └──────────┘   └──────┬───────┘   └────────┬─────────┘    │
│                        │                     │              │
│         ┌──────────────┼─────────────────────┤              │
│         ▼              ▼                     ▼              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              7 AGENTS AUTONOMES                     │    │
│  │  [1.Météo] [2.Carto] [3.Liq] [4.Fib] [5.Micro]    │    │
│  │  [6.Sentinelle] [7.Sessions]                        │    │
│  └─────────────────────┬───────────────────────────────┘    │
│                        ▼                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │           AGENT ORCHESTRATEUR                       │    │
│  │  Filtre 5★ → Calcul Risque → Exécution Atomique    │    │
│  └─────────────────────┬───────────────────────────────┘    │
│                        ▼                                    │
│  ┌─────────────────────────────────────────────────────┐    │
│  │           TRADE MANAGER                             │    │
│  │  Breakeven → Partiels → Trailing                    │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐                       │
│  │  UI (CTk)    │   │  WATCHDOG    │                       │
│  │  Kill Switch │   │  (Process)   │                       │
│  └──────────────┘   └──────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 Contraintes Non-Négociables

| Contrainte | Valeur | Raison |
|---|---|---|
| Risque par trade | **1% de l'Equity** | Survie statistique |
| Trades max/jour | **2** (sauf Diamant 5★) | Anti-overtrade |
| Unanimité requise | **5/5 agents analytiques** | Filtre anti-bruit |
| Fenêtre Rollover interdite | **23h45 → 00h15 UTC+1** | Spread explosion |
| Slippage max | **3 pips (30 points sur XAUUSD)** | Exécution propre |
| Cooldown post-trade | **3 min minimum** | Prévention revenge-trade |
| News Impact Élevé | **Blocage ±10 min** | Protection spike |

---

## 2. MOTEUR ASYNCHRONE ET GESTION D'ÉTAT (BACKBONE)

### 2.1 Architecture Asyncio — Boucles Concurrentes

Le moteur repose sur une **boucle d'événements asyncio unique** gérant N coroutines concurrentes non-bloquantes. Aucun thread n'est utilisé pour la logique métier (les appels MT5 bloquants sont délégués via `asyncio.to_thread()` ou `run_in_executor()`).

#### Coroutines principales et leurs fréquences :

| Coroutine | Fréquence | Priorité | Rôle |
|---|---|---|---|
| `tick_ingestion_loop()` | Chaque tick (≈100-500ms) | CRITIQUE | Aspire les prix XAUUSD via `mt5.symbol_info_tick()` — **[R15] Rate limité à 10 appels/s max** |
| `candle_builder_loop()` | Chaque seconde | HAUTE | Construit les bougies 1m/15m/4H à partir des ticks |
| `agent_dispatch_loop()` | Chaque nouvelle bougie 1m | HAUTE | Déclenche le recalcul des agents 1-4 |
| `agent5_wakeup_loop()` | Chaque tick (conditionnel) | MOYENNE | Réveille l'Agent 5 si prix ∈ zone POI |
| `heartbeat_reconciliation()` | Toutes les 5 secondes | CRITIQUE | Synchronise état local ↔ serveur MT5 |
| `trade_manager_loop()` | Chaque tick (si position ouverte) | HAUTE | Breakeven, partiels, **[R12] trailing stop** |
| `news_scraper_loop()` | Toutes les 60 secondes | BASSE | Rafraîchit le calendrier économique **[R7] dual-source + assume hostile** |
| `recovery_persistence_loop()` | Chaque tick (debounced 1s) | MOYENNE | Sauvegarde `recovery.json` |
| `ui_update_loop()` | Toutes les 250ms | BASSE | Rafraîchit l'interface graphique |
| `watchdog_heartbeat()` | Toutes les 2 secondes | CRITIQUE | Envoie le signal "alive" au Watchdog |
| `telegram_sender_loop()` | Toutes les 2 secondes | BASSE | **[R9]** Dépile et envoie les notifications Telegram |

#### Schéma de la boucle événementielle :

```
asyncio.get_event_loop()
    │
    ├─► gather(
    │       tick_ingestion_loop(),        # ← Flux de prix
    │       candle_builder_loop(),        # ← Construction bougies
    │       agent_dispatch_loop(),        # ← Calculs agents 1-7
    │       heartbeat_reconciliation(),   # ← Sync MT5
    │       trade_manager_loop(),         # ← Gestion positions
    │       news_scraper_loop(),          # ← Calendrier éco
    │       recovery_persistence_loop(),  # ← Sauvegarde état
    │       ui_update_loop(),             # ← Interface
    │       watchdog_heartbeat(),         # ← Signal vie
    │   )
    │
    └─► Exception Handler Global
            └─► Log erreur
            └─► Notification UI
            └─► Restart coroutine si récupérable
```

### 2.2 Le Tableau Noir (Shared State)

Le Tableau Noir est un **dictionnaire Python imbriqué** protégé par un verrou asyncio (`asyncio.Lock`) pour garantir la cohérence lors d'écritures concurrentes. Chaque agent écrit dans sa propre clé sans interférer avec les autres.

#### Structure du Tableau Noir :

```
BLACKBOARD = {
    "meta": {
        "boot_time": datetime,           # Horodatage du démarrage
        "last_tick_time": datetime,       # Dernier tick reçu
        "state": "BOOT|READY|TRADING|COOLDOWN|HALTED|KILLED",
        "live_mode": bool,               # [R10] True = production, False = paper trading
        "kill_switch": bool,             # Commande d'arrêt d'urgence
        "kill_event": asyncio.Event,     # [R1] Event global anti-race condition
        "daily_trade_count": int,        # Compteur journalier (reset à 00h UTC+1)
        "cooldown_until": datetime|None, # Fin du cooldown actuel
        "friday_mode": {                 # [R16] Gestion du vendredi
            "risk_reduced": bool,        # True après 18h UTC+1 vendredi (risque → 0.5%)
            "trading_halted": bool,      # True après 21h UTC+1 vendredi (trading coupé)
        },
    },

    "market_data": {
        "current_tick": {
            "bid": float,
            "ask": float,
            "spread_points": float,      # (ask - bid) / point_size
            "time": datetime,
            "volume": float,
        },
        "candles": {
            "4H": deque(maxlen=120),     # 120 bougies = 20 jours de contexte
            "15m": deque(maxlen=384),    # 384 bougies = 4 jours
            "1m":  deque(maxlen=1440),   # 1440 bougies = 24 heures
        },
        "symbol_info": {                 # Rafraîchi toutes les 60s
            "point_size": float,         # mt5.symbol_info("XAUUSD").point
            "trade_tick_size": float,
            "trade_tick_value": float,
            "trade_contract_size": float,
            "volume_min": float,
            "volume_max": float,
            "volume_step": float,
            "stoplevel": int,            # Distance SL minimum en points
            "spread": int,               # Spread actuel en points
        },
    },

    "agents": {
        "agent_1_meteo": {
            "score": 0|1,                # 0 = conditions non remplies, 1 = validé
            "bias": "BULLISH|BEARISH|NEUTRAL",
            "market_phase": "EXPANSION|RANGE|TRANSITION",
            "bos_4h": { "type": "BULLISH|BEARISH", "level": float, "time": datetime },
            "bos_15m": { "type": "BULLISH|BEARISH", "level": float, "time": datetime },
            "details": str,              # Explication lisible
            "updated_at": datetime,
        },
        "agent_2_cartographe": {
            "score": 0|1,
            "order_blocks": [
                {
                    "type": "BULLISH|BEARISH",
                    "timeframe": "4H|15m|1m",
                    "high": float,
                    "low": float,
                    "midpoint": float,
                    "origin_candle_time": datetime,
                    "mitigated": bool,
                    "strength": float,   # 0.0 → 1.0
                }
            ],
            "fvg_zones": [
                {
                    "type": "BULLISH|BEARISH",
                    "timeframe": "4H|15m|1m",
                    "high": float,       # Haut de l'imbalance
                    "low": float,        # Bas de l'imbalance
                    "filled_percent": float,  # 0% → 100%
                    "origin_time": datetime,
                }
            ],
            "active_poi": {              # POI sélectionné pour le trade potentiel
                "zone_high": float,
                "zone_low": float,
                "type": "OB|FVG|CONFLUENCE",
                "timeframe": str,
            },
            "updated_at": datetime,
        },
        "agent_3_liquidite": {
            "score": 0|1,
            "equal_highs": [{"level": float, "touches": int, "timeframe": str}],
            "equal_lows": [{"level": float, "touches": int, "timeframe": str}],
            "retail_trendlines": [{"start": (float,datetime), "end": (float,datetime), "type": "ASCENDING|DESCENDING"}],
            "asian_session": {
                "high": float,           # Haut de la session 00h-08h UTC+1
                "low": float,            # Bas de la session
                "swept": "HIGH|LOW|BOTH|NONE",
                "ny_prev_high": float,   # Haut de la NY précédente
                "ny_prev_low": float,    # Bas de la NY précédente
            },
            "liquidity_target": float,   # Cible de liquidité identifiée
            "updated_at": datetime,
        },
        "agent_4_fibonacci": {
            "score": 0|1,
            "swing_low": float,
            "swing_high": float,
            "swing_direction": "UP|DOWN",
            "levels": {
                "0.0":   float,          # Swing extreme
                "0.236": float,
                "0.382": float,
                "0.5":   float,
                "0.618": float,          # ← Début zone OTE
                "0.705": float,          # ← Sweet spot
                "0.786": float,          # ← Fin zone OTE
                "1.0":   float,          # Swing extreme opposé
                "-0.272": float,         # Extension TP1
                "-0.618": float,         # Extension TP2
                "-1.0":  float,          # Extension TP3
            },
            "price_in_ote": bool,        # True si prix ∈ [0.618, 0.786]
            "ote_zone": {"high": float, "low": float},
            "updated_at": datetime,
        },
        "agent_5_microscope": {
            "score": 0|1,
            "state": "SLEEPING|SCANNING|CONFIRMED",
            "cpu_usage": float,          # 0.0 quand dort
            "trigger_zone": str,         # Nom de la POI qui l'a réveillé
            "choch_detected": {
                "type": "BULLISH|BEARISH",
                "level": float,
                "time": datetime,
            },
            "bos_1m_confirmed": {
                "type": "BULLISH|BEARISH",
                "level": float,
                "time": datetime,
            },
            "entry_signal": {
                "direction": "BUY|SELL",
                "entry_price": float,
                "sl_price": float,       # Sous le dernier low/high du CHoCh
                "tp_price": float,       # Liquidité ou extension Fibonacci
                "risk_reward": float,
                "confidence": float,     # 0.0 → 1.0
            },
            "updated_at": datetime,
        },
        "agent_6_sentinelle": {
            "is_clear": bool,            # True = aucune news bloquante
            "next_red_event": {
                "name": str,
                "currency": "USD|EUR",
                "time_utc": datetime,
                "impact": "HIGH",
                "minutes_until": int,
            },
            "blackout_active": bool,     # True = dans la fenêtre ±10min
            "blackout_end": datetime|None,
            "assume_hostile": bool,      # [R7] True si scraper down > 5min → blocage préventif
            "last_scrape_time": datetime,
            "scrape_consecutive_failures": int,  # [R7] Compteur d'échecs consécutifs
            "calendar_events_today": list,
            "updated_at": datetime,
        },
        "agent_7_sessions": {
            "is_clear": bool,
            "current_session": "ASIA|LONDON_OPEN|LONDON|NY_OPEN|NY|OVERLAP|OFF_HOURS",
            "in_killzone": bool,         # True = dans une fenêtre de volatilité
            "killzone_name": str|None,
            "rollover_lockout": bool,    # True = 23h45-00h15 UTC+1
            "dst_offset": int,           # Ajustement heure d'été
            "updated_at": datetime,
        },
    },

    "orchestrator": {
        "star_rating": int,              # 0 → 5 (somme des scores agents 1-5)
        "all_filters_passed": bool,      # True si 5★ ET agents 6+7 clear
        "pending_signal": dict|None,     # Signal en attente d'exécution
        "last_execution_result": dict,   # Résultat du dernier trade
    },

    "positions": {
        "open_positions": [
            {
                "ticket": int,
                "direction": "BUY|SELL",
                "entry_price": float,
                "current_sl": float,
                "current_tp": float,
                "initial_sl": float,     # SL d'origine (pour calcul R:R)
                "volume": float,
                "initial_volume": float,
                "open_time": datetime,
                "risk_amount": float,    # Montant en devise risqué
                "rr_current": float,     # R:R actuel
                "breakeven_hit": bool,
                "partial_taken": bool,
                "partial_volume": float,
                "trailing_active": bool, # [R12] True après le partiel → trailing SL actif
                "trailing_sl_last": float|None, # [R12] Dernier niveau du trailing
            }
        ],
        "closed_today": list,
        "server_synced": bool,
        "last_sync_time": datetime,
    },

    "rate_limiter": {                    # [R15] Protection bridge MT5
        "mt5_calls_this_second": int,
        "mt5_last_reset": datetime,
        "mt5_max_calls_per_second": 10,
        "last_known_tick": dict|None,    # Tick réutilisé si rate limit atteint
    },

    "notifications": {                   # [R9] Module Telegram
        "telegram_enabled": bool,
        "telegram_bot_token": str,
        "telegram_chat_id": str,
        "last_notification_time": datetime,
        "queue": list,                   # Messages en attente d'envoi
    },

    "paper_trading": {                   # [R10] Mode simulation
        "enabled": bool,                 # Miroir de meta.live_mode (inversé)
        "simulated_trades": list,        # Historique des trades simulés
        "simulated_equity": float,       # Equity fictive de départ
        "csv_path": "simulation_results.csv",
    },

    "recovery": {
        "last_save_time": datetime,
        "file_path": "recovery.json",
        "integrity_hash": str,           # SHA-256 du dernier snapshot
    },
}
```

### 2.3 Réconciliation MT5 (Heartbeat Loop)

La boucle de réconciliation est le **filet de sécurité #1** contre les désynchronisations réseau.

#### Logique de réconciliation (toutes les 5 secondes) :

```
PROCEDURE heartbeat_reconciliation():
    TANT QUE système actif:
        ATTENDRE 5 secondes (asyncio.sleep)

        # 1. Aspirer les positions ouvertes réelles du serveur MT5
        server_positions ← mt5.positions_get(symbol="XAUUSD")

        # 2. Comparer avec l'état local du Tableau Noir
        local_positions ← BLACKBOARD["positions"]["open_positions"]

        # CAS 1 : Position existe sur serveur mais PAS en local
        #   → "Position fantôme" (micro-coupure pendant l'ouverture)
        #   → ACTION : Importer dans le Tableau Noir, appliquer le Trade Manager
        POUR chaque pos DANS server_positions:
            SI pos.ticket ∉ local_positions:
                AJOUTER pos au Tableau Noir
                LOGGER "⚠️ Position fantôme détectée : #{pos.ticket}"

        # CAS 2 : Position existe en local mais PAS sur serveur
        #   → "Position disparue" (fermée par SL/TP pendant déconnexion, ou intervention manuelle)
        #   → ACTION : Retirer du Tableau Noir, enregistrer le résultat
        POUR chaque pos DANS local_positions:
            SI pos.ticket ∉ server_positions:
                # Vérifier dans l'historique si elle a été fermée
                historique ← mt5.history_deals_get(position=pos.ticket)
                RETIRER pos du Tableau Noir
                ENREGISTRER résultat dans closed_today
                LOGGER "📋 Position #{pos.ticket} fermée côté serveur"

        # CAS 3 : SL/TP modifiés manuellement sur MT5
        POUR chaque pos commune:
            SI server_pos.sl ≠ local_pos.current_sl OU server_pos.tp ≠ local_pos.current_tp:
                METTRE À JOUR local avec les valeurs serveur
                LOGGER "🔄 SL/TP mis à jour depuis serveur"

        # 4. Mettre à jour le flag de synchronisation
        BLACKBOARD["positions"]["server_synced"] ← True
        BLACKBOARD["positions"]["last_sync_time"] ← NOW()
```

#### Matrice des cas de désynchronisation :

| Cas | Local | Serveur | Diagnostic | Action |
|---|---|---|---|---|
| A | ✅ Position | ✅ Position | Normal | Vérifier SL/TP |
| B | ❌ Absent | ✅ Position | Fantôme | Importer + Trade Manager |
| C | ✅ Position | ❌ Absent | Disparue | Clôturer localement + log |
| D | ❌ | ❌ | Normal | Rien |

### 2.4 Persistance & Cold Start (Recovery)

#### 2.4.1 Sauvegarde continue (`recovery.json`)

À chaque tick (debounced à 1 seconde max), un snapshot complet du Tableau Noir est sérialisé en JSON et écrit sur disque. Un hash SHA-256 est calculé pour détecter toute corruption.

```
STRUCTURE recovery.json:
{
    "version": "1.0.0",
    "saved_at": "2025-01-15T14:23:45.123Z",
    "sha256": "a3f2b8c...",
    "blackboard_snapshot": { ... },   # Copie complète du Tableau Noir
    "candle_cache": {                 # Dernières bougies pour reconstruction
        "4H":  [...],
        "15m": [...],
        "1m":  [...],
    },
    "pending_orders": [...],
    "execution_log": [...]
}
```

**Mécanisme d'écriture atomique :**
1. Écrire dans `recovery.tmp`
2. Calculer le SHA-256 de `recovery.tmp`
3. Renommer `recovery.tmp` → `recovery.json` (opération atomique sur NTFS/ext4)
4. Ce procédé évite la corruption si le processus est tué pendant l'écriture

#### 2.4.2 Cold Start (Bootstrapping)

Au démarrage du système, la séquence de bootstrap suivante est exécutée :

```
PROCEDURE cold_start():
    # PHASE 1 : Connexion MT5
    SI mt5.initialize() échoue:
        ABORT avec message d'erreur
    SI mt5.login(account, password, server) échoue:
        ABORT avec message d'erreur

    # PHASE 2 : Vérifier l'existence d'un fichier de recovery
    SI recovery.json EXISTE ET sha256 VALIDE:
        RESTAURER le Tableau Noir depuis le snapshot
        LOGGER "♻️ Recovery : état restauré"
    SINON:
        INITIALISER un Tableau Noir vierge
        LOGGER "🆕 Démarrage à froid"

    # PHASE 3 : Aspirer l'historique des bougies (Cold Fill)
    candles_4H  ← mt5.copy_rates_from_pos("XAUUSD", TIMEFRAME_H4,  0, 120)  # 20 jours
    candles_15m ← mt5.copy_rates_from_pos("XAUUSD", TIMEFRAME_M15, 0, 384)  # 4 jours
    candles_1m  ← mt5.copy_rates_from_pos("XAUUSD", TIMEFRAME_M1,  0, 1440) # 24 heures

    BLACKBOARD["market_data"]["candles"]["4H"]  ← deque(candles_4H)
    BLACKBOARD["market_data"]["candles"]["15m"] ← deque(candles_15m)
    BLACKBOARD["market_data"]["candles"]["1m"]  ← deque(candles_1m)

    # PHASE 4 : Réconcilier les positions ouvertes
    APPELER heartbeat_reconciliation() (synchrone, une seule fois)

    # PHASE 4b : [R2] Détection de gap (lundi matin / réouverture)
    #   Si une position est ouverte et que le prix a gappé au-delà du SL,
    #   le SL n'a pas pu se déclencher → fermer immédiatement au marché.
    POUR chaque position DANS BLACKBOARD["positions"]["open_positions"]:
        tick = mt5.symbol_info_tick("XAUUSD")
        SI position["direction"] == "BUY":
            SI tick.bid ≤ position["current_sl"]:
                LOGGER "🚨 [R2] GAP DÉTECTÉ : BUY #{position.ticket} — prix {tick.bid} ≤ SL {position.current_sl}"
                FERMER position au marché immédiatement
                ENVOYER notification Telegram "⚠️ Gap détecté — Position #{ticket} fermée d'urgence"
        SI position["direction"] == "SELL":
            SI tick.ask ≥ position["current_sl"]:
                LOGGER "🚨 [R2] GAP DÉTECTÉ : SELL #{position.ticket} — prix {tick.ask} ≥ SL {position.current_sl}"
                FERMER position au marché immédiatement
                ENVOYER notification Telegram "⚠️ Gap détecté — Position #{ticket} fermée d'urgence"

    # PHASE 5 : Extraire les infos du symbole
    info ← mt5.symbol_info("XAUUSD")
    BLACKBOARD["market_data"]["symbol_info"] ← {
        "point_size":          info.point,
        "trade_tick_size":     info.trade_tick_size,
        "trade_tick_value":    info.trade_tick_value,
        "trade_contract_size": info.trade_contract_size,
        "volume_min":          info.volume_min,
        "volume_max":          info.volume_max,
        "volume_step":         info.volume_step,
        "stoplevel":           info.trade_stops_level,
    }

    # PHASE 6 : Forcer un premier calcul de tous les agents
    POUR chaque agent DANS [1..7]:
        EXÉCUTER agent.calculate()

    # PHASE 7 : Passer en mode opérationnel
    BLACKBOARD["meta"]["state"] ← "READY"
    LOGGER "✅ Système opérationnel"
```

---

## 3. LES 7 AGENTS ANALYTIQUES (RÈGLE DES 5 ÉTOILES)

### Principe de scoring

Les agents **1 à 5** sont des agents **analytiques de marché**. Chacun produit un score binaire : **0 (rejeté)** ou **1 (validé)**.

Les agents **6 et 7** sont des agents **de filtrage contextuel**. Ils émettent un booléen `is_clear` (True/False) qui agit comme un **veto absolu**.

```
CONDITION D'OUVERTURE D'UN TRADE :
    (Agent1.score + Agent2.score + Agent3.score + Agent4.score + Agent5.score) == 5
    ET Agent6.is_clear == True
    ET Agent7.is_clear == True
```

---

### AGENT 1 — MÉTÉO / STRUCTURE DE MARCHÉ 🌦️

**Rôle :** Détermine le biais directionnel macro (bullish/bearish) et la phase de marché (expansion/range). C'est le filtre stratégique de premier niveau.

**Unités de Temps :** 4H (structure macro) + 15M (structure intermédiaire)

**Inputs :**
- `BLACKBOARD["market_data"]["candles"]["4H"]` — 120 dernières bougies 4H
- `BLACKBOARD["market_data"]["candles"]["15m"]` — 384 dernières bougies 15M

**Logique Mathématique — Identification du BOS (Break of Structure) :**

Un Break of Structure est défini comme suit :

```
DÉFINITION — Swing High (SH) :
    Une bougie C[i] forme un Swing High si :
    C[i].high > C[i-1].high  ET  C[i].high > C[i-2].high
    ET C[i].high > C[i+1].high  ET  C[i].high > C[i+2].high
    (Pivot à 2 bougies de chaque côté, paramétrable à N bougies)

DÉFINITION — Swing Low (SL) :
    Même logique inversée sur C[i].low

DÉFINITION — BOS Bullish :
    Le prix clôture AU-DESSUS du dernier Swing High valide.
    Formellement : ∃ bougie C[j] avec j > i_sh telle que C[j].close > SH[dernier].high
    Où i_sh est l'indice de la bougie du dernier Swing High.

DÉFINITION — BOS Bearish :
    Le prix clôture EN-DESSOUS du dernier Swing Low valide.
    Formellement : ∃ bougie C[j] avec j > i_sl telle que C[j].close < SL[dernier].low

ATTENTION : Un simple mèche (wick) au-delà du swing n'est PAS un BOS.
            Seule la CLÔTURE de bougie compte.
```

**Logique — Phase de Marché :**

```
EXPANSION :
    SI (BOS_4H.direction == BOS_15M.direction)
    ET (le dernier BOS est récent, i.e. ≤ 8 bougies 15M)
    ALORS phase = EXPANSION, bias = direction commune

RANGE :
    SI (aucun nouveau BOS 4H depuis > 3 bougies 4H = 12 heures)
    ET (le prix oscille entre le dernier SH et le dernier SL sur 4H)
    ALORS phase = RANGE, bias = NEUTRAL

    En RANGE, l'agent identifie les bornes :
        range_high = dernier Swing High 4H
        range_low  = dernier Swing Low 4H
        Le score est 0 en RANGE (pas de trade dans un range sauf breakout confirmé)

TRANSITION :
    SI (BOS_4H.direction ≠ BOS_15M.direction)
    ALORS phase = TRANSITION (conflicting bias)
    Le score est 0 en TRANSITION
```

**Output :**

| Champ | Type | Description |
|---|---|---|
| `score` | 0 \| 1 | 1 si expansion avec biais aligné 4H/15M |
| `bias` | BULLISH \| BEARISH \| NEUTRAL | Direction dominante |
| `market_phase` | EXPANSION \| RANGE \| TRANSITION | Phase actuelle |
| `bos_4h` | dict | Dernier BOS 4H (type, level, time) |
| `bos_15m` | dict | Dernier BOS 15M (type, level, time) |

**Critère de score = 1 :**
- Phase = EXPANSION
- Biais 4H et 15M **alignés** dans la même direction
- Le dernier BOS 15M est **récent** (< 8 bougies 15m = 2 heures)

---

### AGENT 2 — CARTOGRAPHE (Order Blocks & FVG) 🗺️

**Rôle :** Cartographie les zones de prix institutionnelles : Order Blocks (OB) et Fair Value Gaps (FVG/Imbalances).

**Unités de Temps :** 4H + 15M (cartographie), 1M (précision)

**Inputs :**
- `BLACKBOARD["market_data"]["candles"]` (toutes UT)
- `BLACKBOARD["agents"]["agent_1_meteo"]["bias"]` — pour filtrer les OB dans la direction

**Logique Mathématique — Identification des Order Blocks :**

```
DÉFINITION — Order Block Bullish (OB+) :
    La DERNIÈRE bougie baissière (C[i].close < C[i].open) AVANT un BOS Bullish.
    L'OB est défini par la zone :
        OB_high = C[i].open     (le haut du corps de la bougie baissière)
        OB_low  = C[i].low      (la mèche basse de la bougie)
        OB_midpoint = (OB_high + OB_low) / 2

    Condition de validité :
        Le mouvement après l'OB doit avoir créé un BOS (structure cassée)
        La bougie suivante C[i+1] doit être bullish avec C[i+1].close > C[i].open

DÉFINITION — Order Block Bearish (OB-) :
    La DERNIÈRE bougie haussière (C[i].close > C[i].open) AVANT un BOS Bearish.
    L'OB est défini par la zone :
        OB_high = C[i].high     (la mèche haute)
        OB_low  = C[i].close    (le bas du corps de la bougie haussière)
        OB_midpoint = (OB_high + OB_low) / 2

MITIGATION d'un Order Block :
    Un OB est considéré "mitigé" (donc invalidé) si le prix revient et TRAVERSE
    le OB_midpoint. Formellement :
        OB+ mitigé si ∃ C[j] avec j > i tel que C[j].low < OB_midpoint
        OB- mitigé si ∃ C[j] avec j > i tel que C[j].high > OB_midpoint

FORCE d'un Order Block (Strength Score 0.0 → 1.0) :
    strength = 0.0
    SI la bougie est un engulfing          : strength += 0.25
    SI volume > moyenne(volume, 20)        : strength += 0.25
    SI l'OB est aligné avec le biais macro : strength += 0.25
    SI une FVG est adjacente à l'OB        : strength += 0.25
    (Un OB "parfait" = 1.0)
```

**Logique Mathématique — Identification des FVG (Fair Value Gaps / Imbalances) :**

```
DÉFINITION — FVG Bullish (Imbalance haussière) :
    Trois bougies consécutives C[i-1], C[i], C[i+1] forment une FVG bullish si :
        C[i-1].high < C[i+1].low
    La zone d'imbalance est :
        FVG_high = C[i+1].low
        FVG_low  = C[i-1].high
    Le "gap" représente une zone où seuls les vendeurs/acheteurs institutionnels
    ont opéré, sans contrepartie → le prix tend à y revenir (rebalancing).

DÉFINITION — FVG Bearish (Imbalance baissière) :
    C[i-1].low > C[i+1].high
    FVG_high = C[i-1].low
    FVG_low  = C[i+1].high

REMPLISSAGE (Fill) :
    filled_percent = 0%
    SI prix entre dans la FVG :
        filled_percent = |prix_extremum - FVG_boundary| / (FVG_high - FVG_low) × 100
    Une FVG remplie à > 50% est considérée "comblée" et perd son intérêt.
```

**Sélection du POI actif (Point Of Interest) :**

```
PROCEDURE select_active_poi(bias):
    candidats ← []

    POUR chaque OB non-mitigé aligné avec le bias:
        SI OB.strength ≥ 0.5:
            AJOUTER OB aux candidats

    POUR chaque FVG non-comblée alignée avec le bias:
        AJOUTER FVG aux candidats

    # Confluence : un OB qui contient ou chevauche une FVG
    POUR chaque paire (OB, FVG) qui se chevauchent:
        CRÉER zone CONFLUENCE avec zone = union(OB, FVG)
        AJOUTER CONFLUENCE aux candidats (priorité maximale)

    # Sélection : le POI le plus PROCHE du prix actuel, dans la direction du biais
    SI bias == BULLISH:
        active_poi ← candidat le plus proche EN DESSOUS du prix actuel
    SI bias == BEARISH:
        active_poi ← candidat le plus proche AU DESSUS du prix actuel

    RETOURNER active_poi
```

**Output :**

| Champ | Type | Description |
|---|---|---|
| `score` | 0 \| 1 | 1 si au moins un POI valide est identifié dans la direction du biais |
| `order_blocks` | list | Liste de tous les OB non-mitigés |
| `fvg_zones` | list | Liste de toutes les FVG non-comblées |
| `active_poi` | dict | Zone POI sélectionnée pour le trade potentiel |

---

### AGENT 3 — LIQUIDITÉ 💧

**Rôle :** Identifie les pools de liquidité (EQH, EQL, Trendlines Retail) et la dynamique de la Session Asiatique. Détermine la **cible** du mouvement institutionnel.

**Inputs :**
- `BLACKBOARD["market_data"]["candles"]` (15M, 1H implicitement agrégé depuis 15M, 4H)
- Horloge système pour identifier les sessions

**Logique Mathématique — Equal Highs / Equal Lows :**

```
DÉFINITION — Equal Highs (EQH) :
    Deux (ou plus) Swing Highs sur la même UT dont les niveaux sont proches :
        |SH[i].high - SH[j].high| ≤ TOLERANCE
    Où TOLERANCE = 1.0 × ATR(14, 15M) × 0.05  (5% de l'ATR 14 périodes en 15M)

    Interprétation ICT : Les EQH forment un "mur de liquidité" au-dessus duquel
    des stop-loss d'acheteurs (retail) s'accumulent. Le Smart Money cherchera à
    sweeper (balayer) cette zone pour remplir ses ordres.

    touches = nombre de Swing Highs formant l'EQH (minimum 2)
    Plus il y a de touches, plus la pool de liquidité est profonde.

DÉFINITION — Equal Lows (EQL) :
    Même logique inversée sur les Swing Lows.
    Les stop-loss des vendeurs retail s'accumulent sous les EQL.
```

**Logique — Trendlines Retail :**

```
IDENTIFICATION DES TRENDLINES :
    # Une trendline ascendante connecte ≥ 3 Swing Lows consécutifs
    POUR chaque combinaison de 2 Swing Lows (SL[i], SL[j]) avec i < j:
        pente = (SL[j].low - SL[i].low) / (index[j] - index[i])
        ordonnée_origine = SL[i].low - pente × index[i]

        # Vérifier que ≥ 1 autre Swing Low touche cette ligne (±TOLERANCE)
        touches = 0
        POUR chaque SL[k] avec k ≠ i, k ≠ j:
            valeur_attendue = pente × index[k] + ordonnée_origine
            SI |SL[k].low - valeur_attendue| ≤ TOLERANCE:
                touches += 1

        SI touches ≥ 1:  # Total ≥ 3 points de contact
            ENREGISTRER trendline(start, end, pente, type="ASCENDING")

    Interprétation ICT : Les trendlines retail sont des "lignes de liquidité"
    dynamiques. Les traders retail placent leurs SL sous les trendlines ascendantes
    → le Smart Money cassera la trendline pour sweeper cette liquidité
    avant de remonter.
```

**Logique — Session Asiatique (Piège à Liquidité) :**

```
DÉFINITION DES SESSIONS (UTC+1, ajustées pour DST):
    ASIA    : 00:00 → 08:00
    LONDON  : 08:00 → 12:00
    NY      : 13:00 → 22:00
    OVERLAP : 13:00 → 17:00

SUIVI SESSION ASIATIQUE :
    CHAQUE JOUR à 08:00 UTC+1 (fin de session Asia):
        asian_high = MAX(candles_1m[00:00→08:00].high)
        asian_low  = MIN(candles_1m[00:00→08:00].low)

    STOCKAGE des hauts/bas de la session NY PRÉCÉDENTE :
        ny_prev_high = MAX(candles_1m[13:00→22:00 de J-1].high)
        ny_prev_low  = MIN(candles_1m[13:00→22:00 de J-1].low)

    SUIVI DU SWEEP :
        SI prix_actuel > asian_high ET ensuite prix redescend sous asian_high:
            asian_session.swept = "HIGH"
        SI prix_actuel < asian_low ET ensuite prix remonte au-dessus asian_low:
            asian_session.swept = "LOW"

    STRATÉGIE ICT (Asia as Liquidity Trap) :
        Pendant London Open (08:00-10:00), le Smart Money sweep souvent
        le high ou le low de la session asiatique, ciblant les liquidités
        laissées par les hauts/bas de la session NY précédente.

        SI bias == BULLISH ET asian_session.swept == "LOW":
            → Le piège a fonctionné, le prix peut maintenant monter
            → Chercher une entrée long après le sweep du low
            → Cible : ny_prev_high ou EQH au-dessus

        SI bias == BEARISH ET asian_session.swept == "HIGH":
            → Le piège a fonctionné, le prix peut maintenant descendre
            → Chercher une entrée short après le sweep du high
            → Cible : ny_prev_low ou EQL en-dessous
```

**Output :**

| Champ | Type | Description |
|---|---|---|
| `score` | 0 \| 1 | 1 si un sweep de liquidité est cohérent avec le biais ET une cible est identifiée |
| `equal_highs` | list | EQH détectés |
| `equal_lows` | list | EQL détectés |
| `retail_trendlines` | list | Trendlines retail identifiées |
| `asian_session` | dict | High/Low Asian, statut de sweep |
| `liquidity_target` | float | Prix-cible du mouvement institutionnel |

**Critère de score = 1 :**
- Au moins un pool de liquidité (EQH/EQL/Trendline) a été identifié
- Un sweep cohérent avec le biais a eu lieu OU est en cours
- Une cible de liquidité est identifiée dans la direction du biais
- **[R8] Filtre Anti-Fakeout Asiatique :** Si `asian_high - asian_low < 0.3 × ATR(14, 4H)`, le range asiatique est jugé trop serré pour contenir de la liquidité significative → le sweep asiatique est **ignoré** comme input. L'agent peut toujours scorer à 1 via les EQH/EQL/Trendlines indépendamment.

---

### AGENT 4 — FIBONACCI (OTE Zone) 📐

**Rôle :** Trace automatiquement les niveaux de Fibonacci et vérifie que le prix est dans la **zone OTE (Optimal Trade Entry)** : entre 61.8% et 78.6%.

**Inputs :**
- `BLACKBOARD["market_data"]["candles"]["15m"]` (pour identifier les swings)
- `BLACKBOARD["agents"]["agent_1_meteo"]["bias"]` (direction du retracement)

**Logique Mathématique — Identification des Swing Points :**

```
IDENTIFICATION DU SWING PERTINENT :
    # Le swing pertinent est le DERNIER mouvement impulsif majeur

    SI bias == BULLISH:
        # On cherche le dernier mouvement UP (Swing Low → Swing High)
        swing_low  = le Swing Low 15M le plus récent AVANT le BOS bullish
        swing_high = le Swing High 15M le plus récent APRÈS le BOS bullish

    SI bias == BEARISH:
        # On cherche le dernier mouvement DOWN (Swing High → Swing Low)
        swing_high = le Swing High 15M le plus récent AVANT le BOS bearish
        swing_low  = le Swing Low 15M le plus récent APRÈS le BOS bearish

    VALIDATION :
        L'amplitude du swing doit être ≥ 1.5 × ATR(14, 15M)
        (Filtre anti-bruit : on ignore les micro-mouvements)
```

**Calcul des niveaux de Fibonacci :**

```
SI bias == BULLISH (retracement vers le bas dans un trend haussier):
    # Le 0% est en haut (Swing High), le 100% est en bas (Swing Low)
    # Le prix retrace du haut vers le bas
    range = swing_high - swing_low
    level(ratio) = swing_high - ratio × range

    Concrètement :
        0.0%   = swing_high                    (pas de retracement)
        23.6%  = swing_high - 0.236 × range
        38.2%  = swing_high - 0.382 × range
        50.0%  = swing_high - 0.500 × range    (discount/premium pivot)
        61.8%  = swing_high - 0.618 × range    ← DÉBUT ZONE OTE
        70.5%  = swing_high - 0.705 × range    ← SWEET SPOT
        78.6%  = swing_high - 0.786 × range    ← FIN ZONE OTE
        100%   = swing_low                     (retracement total)

    Extensions (Targets) :
        -27.2% = swing_high + 0.272 × range    (TP1)
        -61.8% = swing_high + 0.618 × range    (TP2)
        -100%  = swing_high + 1.000 × range    (TP3)

SI bias == BEARISH (retracement vers le haut dans un trend baissier):
    # Logique inversée : 0% en bas (Swing Low), 100% en haut (Swing High)
    range = swing_high - swing_low
    level(ratio) = swing_low + ratio × range

    61.8%  = swing_low + 0.618 × range     ← DÉBUT ZONE OTE (prix doit monter jusque là)
    78.6%  = swing_low + 0.786 × range     ← FIN ZONE OTE
```

**Vérification de la zone OTE :**

```
ZONE OTE (Optimal Trade Entry) :
    ote_low  = level(0.618)
    ote_high = level(0.786)

    price_in_ote = (current_price ≥ ote_low) ET (current_price ≤ ote_high)

    # ATTENTION : la vérification est faite dans le sens du retracement
    # Pour un BULLISH trade : le prix doit être DESCENDU dans l'OTE
    #   → current_price ≥ level(0.618) ET current_price ≤ level(0.786)
    #   → En d'autres termes, le prix a retracé entre 61.8% et 78.6% du mouvement haussier

    # Le Sweet Spot (70.5%) est le niveau idéal :
    #   C'est le golden ratio inversé : 1 - 0.618² ≈ 0.618 × (1 + 0.618 × 0.236)
    #   Ce niveau offre statistiquement le meilleur R:R
```

**Output :**

| Champ | Type | Description |
|---|---|---|
| `score` | 0 \| 1 | 1 si le prix est actuellement dans la zone OTE [0.618, 0.786] |
| `swing_low` | float | Prix du Swing Low identifié |
| `swing_high` | float | Prix du Swing High identifié |
| `levels` | dict | Tous les niveaux Fibonacci calculés |
| `price_in_ote` | bool | True si prix ∈ OTE |
| `ote_zone` | dict | Bornes de la zone OTE |

---

### AGENT 5 — MICROSCOPE 1 MIN (Entry Trigger) 🔬

**Rôle :** Agent dormeur qui ne se réveille que lorsque le prix entre dans une zone POI. Il cherche le pattern d'entrée : **CHoCh (Change of Character)** suivi d'un **BOS 1M** dans la direction du biais macro.

**Optimisation CPU :** Cet agent est en mode `SLEEPING` par défaut. Il consomme **0% de CPU**. Il n'est activé que par un événement du Tableau Noir.

**Inputs :**
- `BLACKBOARD["agents"]["agent_2_cartographe"]["active_poi"]` — zone POI active
- `BLACKBOARD["agents"]["agent_4_fibonacci"]["price_in_ote"]` — statut OTE
- `BLACKBOARD["market_data"]["candles"]["1m"]` — bougies 1 minute
- `BLACKBOARD["market_data"]["current_tick"]` — tick en temps réel

**Machine à états de l'Agent 5 :**

```
┌──────────┐     Prix entre       ┌──────────┐    CHoCh + BOS    ┌───────────┐
│ SLEEPING │────dans zone POI────►│ SCANNING │───confirmé───────►│ CONFIRMED │
│ (0% CPU) │                      │ (actif)  │                   │ (signal!) │
└──────────┘                      └────┬─────┘                   └───────────┘
      ▲                                │                               │
      │         Prix sort de la zone   │                               │
      └────────────────────────────────┘                               │
      ▲                                                                │
      │                Trade exécuté ou timeout                        │
      └────────────────────────────────────────────────────────────────┘
```

**Condition de réveil :**

```
ÉVÉNEMENT DE RÉVEIL :
    prix_actuel = BLACKBOARD["market_data"]["current_tick"]["bid"]
    poi = BLACKBOARD["agents"]["agent_2_cartographe"]["active_poi"]
    in_ote = BLACKBOARD["agents"]["agent_4_fibonacci"]["price_in_ote"]

    SI poi N'EST PAS None:
        SI prix_actuel ≥ poi["zone_low"] ET prix_actuel ≤ poi["zone_high"]:
            Agent5.state ← "SCANNING"
            Agent5.trigger_zone ← poi["type"]
        OU SI in_ote == True:
            Agent5.state ← "SCANNING"
            Agent5.trigger_zone ← "FIBONACCI_OTE"

    SINON:
        Agent5.state ← "SLEEPING"
```

**Logique — CHoCh (Change of Character) en 1M :**

```
DÉFINITION — CHoCh (Change of Character) :
    Un CHoCh est un PREMIER changement de structure sur 1M qui inverse le mini-trend.
    C'est le précurseur du retournement, pas encore la confirmation.

    POUR un trade BULLISH (le prix était en baisse micro, on cherche le retournement) :
        Le prix formait des Lower Lows et Lower Highs sur 1M.
        CHoCh Bullish = Le prix casse au-dessus du DERNIER Lower High.
        Formellement : ∃ C[j] telle que C[j].close > LH[dernier].high
            Où LH[dernier] est le dernier Lower High 1M.

    POUR un trade BEARISH :
        CHoCh Bearish = Le prix casse en-dessous du DERNIER Higher Low.
        Formellement : ∃ C[j] telle que C[j].close < HL[dernier].low
```

**Logique — BOS 1M (Confirmation) :**

```
DÉFINITION — BOS 1M (Break of Structure 1 Minute) :
    Après le CHoCh, le prix doit créer un NOUVEAU point de structure
    et le casser, confirmant le changement de direction.

    POUR un BOS Bullish 1M :
        Après le CHoCh Bullish, le prix forme un Higher Low (HL_new)
        puis casse au-dessus du Swing High formé après le CHoCh.
        C[k].close > SH_post_choch.high

    POUR un BOS Bearish 1M :
        Après le CHoCh Bearish, le prix forme un Lower High (LH_new)
        puis casse en-dessous du Swing Low formé après le CHoCh.
        C[k].close < SL_post_choch.low

    SÉQUENCE COMPLÈTE :
    1. Prix entre dans zone POI (Agent5 se réveille)
    2. Micro-structure 1M : série de LL/LH (pour un setup bullish)
    3. CHoCh : Clôture au-dessus du dernier LH → inversement micro
    4. Formation d'un nouveau HL (pullback)
    5. BOS : Clôture au-dessus du SH post-CHoCh → confirmation
    → SIGNAL D'ENTRÉE
```

**Construction du signal d'entrée :**

```
PROCEDURE build_entry_signal(direction, choch, bos):
    SI direction == "BUY":
        entry_price = prix_actuel (Market Order)
        sl_price    = MIN(choch.candle.low, bos_pullback.low) - buffer
            Où buffer = 2 × BLACKBOARD["market_data"]["symbol_info"]["point_size"]
            (Le SL est placé SOUS le dernier low du CHoCh ou du pullback,
             avec un buffer de 2 points pour absorber le bruit)

        # Cible = liquidité identifiée par Agent 3 ou extension Fibonacci
        tp_price = MIN(
            BLACKBOARD["agents"]["agent_3_liquidite"]["liquidity_target"],
            BLACKBOARD["agents"]["agent_4_fibonacci"]["levels"]["-0.272"]
        )
        # On prend la cible la plus conservatrice (la plus proche)

    SI direction == "SELL":
        entry_price = prix_actuel
        sl_price    = MAX(choch.candle.high, bos_pullback.high) + buffer
        tp_price    = MAX(
            BLACKBOARD["agents"]["agent_3_liquidite"]["liquidity_target"],
            BLACKBOARD["agents"]["agent_4_fibonacci"]["levels"]["-0.272"]
        )

    risk_reward = |tp_price - entry_price| / |entry_price - sl_price|

    # FILTRE R:R MINIMUM
    SI risk_reward < 2.0:
        REJETER le signal ("R:R insuffisant, minimum 1:2 requis")
        Agent5.score ← 0
        RETOURNER

    Agent5.entry_signal ← {
        direction, entry_price, sl_price, tp_price, risk_reward
    }
    Agent5.score ← 1
    Agent5.state ← "CONFIRMED"
```

**Output :**

| Champ | Type | Description |
|---|---|---|
| `score` | 0 \| 1 | 1 si CHoCh + BOS confirmé avec R:R ≥ 2.0 |
| `state` | SLEEPING \| SCANNING \| CONFIRMED | État de la machine |
| `choch_detected` | dict | Détails du CHoCh |
| `bos_1m_confirmed` | dict | Détails du BOS 1M |
| `entry_signal` | dict | Signal d'entrée complet (prix, SL, TP, R:R) |

---

### AGENT 6 — SENTINELLE ÉCONOMIQUE 📡

**Rôle :** Scrape le calendrier économique et impose un **blackout total** ±10 minutes autour des annonces à impact élevé (rouge) sur l'USD et l'EUR.

**Source de données :** **[R7] Double source obligatoire** — Source primaire : Investing.com (scraping). Source de fallback : ForexFactory RSS ou MQL5 Calendar.

**Inputs :**
- Calendrier économique du jour
- Horloge système

**Logique :**

```
PROCEDURE update_economic_calendar():
    # [R7] DOUBLE SOURCE : essayer la source primaire, puis le fallback
    events_today ← None
    ESSAYER:
        events_today ← scrape_investing_calendar(date=TODAY)
        BLACKBOARD["agents"]["agent_6_sentinelle"]["scrape_consecutive_failures"] ← 0
    ATTRAPER Exception:
        LOGGER "⚠️ Source primaire (Investing.com) échouée — tentative fallback"
        ESSAYER:
            events_today ← scrape_forexfactory_rss(date=TODAY)
            BLACKBOARD["agents"]["agent_6_sentinelle"]["scrape_consecutive_failures"] ← 0
        ATTRAPER Exception:
            BLACKBOARD["agents"]["agent_6_sentinelle"]["scrape_consecutive_failures"] += 1
            LOGGER "🔴 TOUTES les sources de news ont échoué"

    # [R7] MODE ASSUME HOSTILE
    failures = BLACKBOARD["agents"]["agent_6_sentinelle"]["scrape_consecutive_failures"]
    time_since_last = NOW() - BLACKBOARD["agents"]["agent_6_sentinelle"]["last_scrape_time"]

    SI failures ≥ 5 OU time_since_last > 5 minutes:
        BLACKBOARD["agents"]["agent_6_sentinelle"]["assume_hostile"] ← True
        BLACKBOARD["agents"]["agent_6_sentinelle"]["is_clear"] ← False
        LOGGER "🚨 [R7] MODE ASSUME HOSTILE ACTIVÉ — Aucune source fiable depuis {time_since_last}"
        LOGGER "🚨 Trading BLOQUÉ tant qu'une source ne répond pas"
        RETOURNER

    SI events_today N'EST PAS None:
        BLACKBOARD["agents"]["agent_6_sentinelle"]["assume_hostile"] ← False
        BLACKBOARD["agents"]["agent_6_sentinelle"]["last_scrape_time"] ← NOW()

    # Filtrer : ne garder que les événements ROUGES (Impact Élevé) sur USD et EUR
    red_events ← FILTRER events_today OÙ:
        event.impact == "HIGH"
        ET event.currency ∈ {"USD", "EUR"}

    # Stocker dans le Tableau Noir
    BLACKBOARD["agents"]["agent_6_sentinelle"]["calendar_events_today"] ← red_events

PROCEDURE check_blackout():
    # [R7] Si Assume Hostile est actif, bloquer inconditionnellement
    SI BLACKBOARD["agents"]["agent_6_sentinelle"]["assume_hostile"] == True:
        BLACKBOARD["agents"]["agent_6_sentinelle"]["is_clear"] ← False
        RETOURNER

    now = datetime.utcnow() + timedelta(hours=1)  # UTC+1

    POUR chaque event DANS red_events:
        event_time = event.time_utc + timedelta(hours=1)  # UTC+1
        delta = |now - event_time|

        SI delta ≤ 10 minutes:
            BLACKBOARD["agents"]["agent_6_sentinelle"]["blackout_active"] ← True
            BLACKBOARD["agents"]["agent_6_sentinelle"]["blackout_end"] ← event_time + 10min
            BLACKBOARD["agents"]["agent_6_sentinelle"]["is_clear"] ← False
            LOGGER "🚫 BLACKOUT ACTIF : {event.name} dans {delta} minutes"
            RETOURNER

    # Aucun blackout actif
    BLACKBOARD["agents"]["agent_6_sentinelle"]["blackout_active"] ← False
    BLACKBOARD["agents"]["agent_6_sentinelle"]["is_clear"] ← True
```

**Fenêtre de blackout visualisée :**

```
         -10min         EVENT          +10min
    ──────[██████████████║██████████████]──────
              ZONE INTERDITE
              is_clear = False
              Aucun trade autorisé
```

**Output :**

| Champ | Type | Description |
|---|---|---|
| `is_clear` | bool | True = aucun blackout en cours |
| `blackout_active` | bool | True = dans la fenêtre ±10min |
| `next_red_event` | dict | Prochaine annonce rouge (nom, heure, devise) |

---

### AGENT 7 — TEMPS & SESSIONS ⏰

**Rôle :** Gère les fenêtres horaires de volatilité (Kill Zones) et le verrouillage Rollover.

**Inputs :**
- Horloge système
- Configuration DST (Daylight Saving Time)

**Définition des Kill Zones (UTC+1) :**

```
KILL ZONES — Fenêtres de haute volatilité sur XAUUSD :

    LONDON OPEN KILL ZONE :    08:00 → 11:00 UTC+1
    NEW YORK OPEN KILL ZONE :  13:00 → 16:00 UTC+1
    LONDON/NY OVERLAP :        13:00 → 17:00 UTC+1 (contient NY Open)

    Ces fenêtres sont les SEULS moments où l'Agent 7 autorise le trading.
    En dehors de ces fenêtres, is_clear = False.

ROLLOVER LOCKOUT :
    23:45 → 00:15 UTC+1 (30 minutes)
    Pendant cette fenêtre, le spread XAUUSD peut exploser (parfois > 50 points).
    → Interdiction ABSOLUE d'ouvrir un trade.
    → Le Trade Manager ne modifie PAS les SL/TP pendant le rollover
      (pour éviter les requêtes rejetées par le broker).

SESSIONS NOMMÉES :
    ASIA       : 00:00 → 08:00  (observation seulement, pas de trading)
    LONDON     : 08:00 → 17:00  (trading autorisé dans la Kill Zone)
    NEW_YORK   : 13:00 → 22:00  (trading autorisé dans la Kill Zone)
    OFF_HOURS  : 22:00 → 00:00  (pas de trading)
```

**Gestion du DST (Daylight Saving Time) :**

```
AJUSTEMENT DST :
    Le broker MT5 fournit la date/heure du serveur via mt5.symbol_info_tick().time
    Le décalage DST affecte les heures des Kill Zones :

    # Heure d'été (dernier dimanche de mars → dernier dimanche d'octobre en Europe)
    SI DST actif:
        LONDON OPEN KZ : 08:00 → 11:00 UTC+1 (inchangé car UTC+1 = CET)
        NY OPEN KZ     : 13:00 → 16:00 UTC+1
    SINON (heure d'hiver):
        LONDON OPEN KZ : 08:00 → 11:00 UTC+1
        NY OPEN KZ     : 14:00 → 17:00 UTC+1  (+1h car NY ne change pas en même temps)

    NOTE : Le décalage US/EU du DST crée une période de 2-3 semaines
    où les heures sont décalées. Le système doit utiliser les fuseaux
    horaires IANA (Europe/Paris, America/New_York) pour gérer ça proprement
    via le module `zoneinfo` (Python 3.9+).
```

**Output :**

| Champ | Type | Description |
|---|---|---|
| `is_clear` | bool | True = dans une Kill Zone ET hors Rollover ET hors Friday halt |
| `current_session` | str | Session actuelle (ASIA, LONDON, etc.) |
| `in_killzone` | bool | True = dans une fenêtre de volatilité |
| `rollover_lockout` | bool | True = 23h45-00h15 |
| `friday_mode` | str | **[R16]** NORMAL \| REDUCED_RISK \| HALTED |

**[R16] Règle du Vendredi :**

```
PROCEDURE check_friday_mode():
    now = datetime.now(tz=timezone_utc_plus_1)

    SI now.weekday() == 4:  # Vendredi = 4 en Python
        SI now.hour ≥ 21:
            # Après 21h UTC+1 vendredi : TRADING COUPÉ
            BLACKBOARD["meta"]["friday_mode"]["trading_halted"] ← True
            BLACKBOARD["meta"]["friday_mode"]["risk_reduced"] ← True
            BLACKBOARD["agents"]["agent_7_sessions"]["is_clear"] ← False
            LOGGER "🔴 [R16] Vendredi > 21h — Trading COUPÉ (fermeture marché imminente)"

        SINON SI now.hour ≥ 18:
            # Après 18h UTC+1 vendredi : RISQUE RÉDUIT à 0.5%
            BLACKBOARD["meta"]["friday_mode"]["risk_reduced"] ← True
            BLACKBOARD["meta"]["friday_mode"]["trading_halted"] ← False
            LOGGER "🟡 [R16] Vendredi > 18h — Risque réduit à 0.5%"

    SINON:
        BLACKBOARD["meta"]["friday_mode"]["risk_reduced"] ← False
        BLACKBOARD["meta"]["friday_mode"]["trading_halted"] ← False
```

---

## 4. AGENT ORCHESTRATEUR ET FILTRE D'UNANIMITÉ

### 4.1 Pipeline de décision

L'Orchestrateur est déclenché à chaque mise à jour de l'Agent 5 (le dernier maillon). Il exécute le pipeline suivant **de manière séquentielle** :

```
PROCEDURE orchestrator_pipeline():
    # ÉTAPE 1 : Vérifier le Kill Switch
    SI BLACKBOARD["meta"]["kill_switch"] == True:
        LOGGER "🔴 Kill Switch actif — ARRÊT"
        RETOURNER

    # ÉTAPE 2 : Vérifier l'état du système
    SI BLACKBOARD["meta"]["state"] ∉ {"READY", "TRADING"}:
        RETOURNER

    # ÉTAPE 3 : Compter les étoiles (agents analytiques 1-5)
    star_count = (
        BLACKBOARD["agents"]["agent_1_meteo"]["score"]
        + BLACKBOARD["agents"]["agent_2_cartographe"]["score"]
        + BLACKBOARD["agents"]["agent_3_liquidite"]["score"]
        + BLACKBOARD["agents"]["agent_4_fibonacci"]["score"]
        + BLACKBOARD["agents"]["agent_5_microscope"]["score"]
    )

    BLACKBOARD["orchestrator"]["star_rating"] ← star_count

    # ÉTAPE 4 : Vérifier l'unanimité (5/5)
    SI star_count < 5:
        BLACKBOARD["orchestrator"]["all_filters_passed"] ← False
        LOGGER "⭐ Score: {star_count}/5 — Insuffisant"
        RETOURNER

    # ÉTAPE 5 : Vérifier les filtres contextuels (veto absolu)
    SI BLACKBOARD["agents"]["agent_6_sentinelle"]["is_clear"] == False:
        LOGGER "🚫 Blackout économique actif"
        RETOURNER

    SI BLACKBOARD["agents"]["agent_7_sessions"]["is_clear"] == False:
        LOGGER "🚫 Hors Kill Zone ou Rollover actif"
        RETOURNER

    # ÉTAPE 6 : Vérifier le cooldown
    SI BLACKBOARD["meta"]["cooldown_until"] N'EST PAS None:
        SI NOW() < BLACKBOARD["meta"]["cooldown_until"]:
            # Vérifier si le cooldown peut être débloqué (nouvelle structure)
            SI cooldown_structurel_debloque() == False:
                LOGGER "⏳ Cooldown actif jusqu'à {cooldown_until}"
                RETOURNER

    # ÉTAPE 7 : Vérifier le compteur journalier
    SI BLACKBOARD["meta"]["daily_trade_count"] ≥ 2:
        # Exception : setup "5 étoiles diamant" (confluence exceptionnelle)
        SI is_diamond_setup() == False:
            LOGGER "🛑 Limite de 2 trades/jour atteinte"
            RETOURNER

    # ÉTAPE 8 : Vérifier le spread
    current_spread = BLACKBOARD["market_data"]["current_tick"]["spread_points"]
    max_spread = CONFIG["max_spread_points"]  # Ex: 30 points
    SI current_spread > max_spread:
        LOGGER "📈 Spread trop élevé : {current_spread} > {max_spread}"
        RETOURNER

    # TOUTES LES CONDITIONS SONT REMPLIES → EXÉCUTER
    BLACKBOARD["orchestrator"]["all_filters_passed"] ← True
    signal = BLACKBOARD["agents"]["agent_5_microscope"]["entry_signal"]
    APPELER execute_trade(signal)
```

### 4.2 Cooldown Structurel

```
PROCEDURE cooldown_structurel_debloque():
    # Le cooldown technique de 3 minutes est INCOMPRESSIBLE
    SI (NOW() - dernier_trade_time) < 3 minutes:
        RETOURNER False

    # Après 3 minutes, vérifier si une nouvelle structure a été créée
    dernier_bos_15m = BLACKBOARD["agents"]["agent_1_meteo"]["bos_15m"]

    SI dernier_bos_15m.time > dernier_trade_time:
        # Un nouveau BOS 15M a été créé depuis le dernier trade
        # → La structure a évolué, le cooldown est levé
        RETOURNER True

    RETOURNER False
```

### 4.3 Setup "5 Étoiles Diamant" (Exception au plafond de 2 trades/jour)

```
PROCEDURE is_diamond_setup():
    # Un setup Diamant est un setup 5★ avec des conditions de confluence exceptionnelles :
    #
    # 1. Le POI actif est de type CONFLUENCE (OB + FVG superposés)
    # 2. Le prix est dans le sweet spot Fibonacci (entre 68% et 73%)
    # 3. Le R:R est ≥ 3.0
    # 4. Un sweep de liquidité Asian a été confirmé
    # 5. Le volume de la bougie BOS 1M est > 2× la moyenne

    poi = BLACKBOARD["agents"]["agent_2_cartographe"]["active_poi"]
    fib = BLACKBOARD["agents"]["agent_4_fibonacci"]
    signal = BLACKBOARD["agents"]["agent_5_microscope"]["entry_signal"]
    liq = BLACKBOARD["agents"]["agent_3_liquidite"]

    conditions = [
        poi["type"] == "CONFLUENCE",
        fib["levels"]["0.705"] est entre ote_low et ote_high,  # Sweet spot
        signal["risk_reward"] >= 3.0,
        liq["asian_session"]["swept"] != "NONE",
        # Volume check sur la bougie de BOS (à implémenter via les données tick)
    ]

    RETOURNER ALL(conditions)
```

---

## 5. EXÉCUTION CHIRURGICALE ET OPSEC (FILTRES MT5)

### 5.1 Calcul du Risque 1% — Formule Dynamique

**Aucune valeur en dur.** Tout est calculé dynamiquement à partir des informations du symbole XAUUSD.

```
PROCÉDURE calcul_lot(equity, sl_distance_price, symbol_info):
    # INPUTS :
    #   equity          = mt5.account_info().equity  (ex: 10000.0 USD)
    #   sl_distance_price = |entry_price - sl_price|  (ex: 3.50 USD pour XAUUSD)
    #   symbol_info     = BLACKBOARD["market_data"]["symbol_info"]

    # ÉTAPE 1 : Montant risqué (1% de l'Equity)
    risk_amount = equity × 0.01
    # Exemple : 10000 × 0.01 = 100.0 USD

    # ÉTAPE 2 : Convertir la distance SL en ticks
    #   point_size       = symbol_info["point_size"]       (ex: 0.01 pour XAUUSD)
    #   trade_tick_size  = symbol_info["trade_tick_size"]   (ex: 0.01)
    #   trade_tick_value = symbol_info["trade_tick_value"]  (ex: 0.01 USD par tick pour 0.01 lot)

    sl_distance_ticks = sl_distance_price / trade_tick_size
    # Exemple : 3.50 / 0.01 = 350 ticks

    # ÉTAPE 3 : Valeur d'un tick par lot standard
    #   trade_contract_size = symbol_info["trade_contract_size"]  (ex: 100 oz)
    tick_value_per_lot = trade_tick_value × (trade_contract_size / 100)
    # Note : trade_tick_value est souvent exprimé pour le lot minimum.
    # Alternative plus fiable :
    tick_value_per_lot = trade_tick_value  # Dépend du broker, vérifier !

    # ÉTAPE 4 : Calcul du lot brut
    lot_brut = risk_amount / (sl_distance_ticks × tick_value_per_lot)
    # Exemple : 100 / (350 × 1.0) = 0.2857 lot

    # ÉTAPE 5 : Arrondir au volume_step inférieur
    volume_step = symbol_info["volume_step"]   # ex: 0.01
    lot_arrondi = FLOOR(lot_brut / volume_step) × volume_step
    # Exemple : FLOOR(0.2857 / 0.01) × 0.01 = 0.28 lot

    # ÉTAPE 6 : Borner au min/max du broker
    lot_final = CLAMP(lot_arrondi, symbol_info["volume_min"], symbol_info["volume_max"])

    # ÉTAPE 7 : Vérification finale du risque réel
    risque_reel = lot_final × sl_distance_ticks × tick_value_per_lot
    SI risque_reel > risk_amount × 1.01:  # Tolérance de 1%
        ERREUR "Risque calculé dépasse 1% — ABORT"

    RETOURNER lot_final
```

### 5.2 Protection Stoplevel

```
PROCÉDURE ajuster_sl_stoplevel(entry_price, sl_price, direction, symbol_info, equity):
    stoplevel = symbol_info["stoplevel"]  # Distance minimum en points
    point_size = symbol_info["point_size"]
    stoplevel_price = stoplevel × point_size  # Conversion en prix

    sl_distance = |entry_price - sl_price|

    SI sl_distance < stoplevel_price:
        LOGGER "⚠️ SL géométrique ({sl_distance}) < stoplevel broker ({stoplevel_price})"

        # Élargir le SL à la limite du broker
        SI direction == "BUY":
            sl_price_adjusted = entry_price - stoplevel_price - (1 × point_size)
            # -1 point supplémentaire de marge de sécurité
        SI direction == "SELL":
            sl_price_adjusted = entry_price + stoplevel_price + (1 × point_size)

        # RECALCULER le lot pour maintenir le risque à 1%
        new_sl_distance = |entry_price - sl_price_adjusted|
        lot_recalcule = calcul_lot(equity, new_sl_distance, symbol_info)

        LOGGER "🔧 SL ajusté de {sl_price} → {sl_price_adjusted}, Lot recalculé : {lot_recalcule}"
        RETOURNER (sl_price_adjusted, lot_recalcule)

    RETOURNER (sl_price, lot_initial)  # Pas de modification nécessaire
```

### 5.3 Filtre Rollover & Spread

```
PROCÉDURE filtre_rollover_spread():
    now = datetime.now(tz=timezone_utc_plus_1)

    # Filtre Rollover : 23h45 → 00h15 UTC+1
    rollover_start = now.replace(hour=23, minute=45, second=0)
    rollover_end   = (now + timedelta(days=1)).replace(hour=0, minute=15, second=0)
    # Gestion du passage de minuit
    SI now.hour == 23 ET now.minute >= 45:
        EN_ROLLOVER = True
    SINON SI now.hour == 0 ET now.minute < 15:
        EN_ROLLOVER = True
    SINON:
        EN_ROLLOVER = False

    SI EN_ROLLOVER:
        LOGGER "🚫 Fenêtre Rollover — Trade interdit"
        RETOURNER False

    # Filtre Spread
    spread_actuel = BLACKBOARD["market_data"]["current_tick"]["spread_points"]
    spread_max = CONFIG["max_spread_points"]  # Paramètre configurable

    SI spread_actuel > spread_max:
        LOGGER "📈 Spread excessif : {spread_actuel} > {spread_max}"
        RETOURNER False

    RETOURNER True
```

### 5.4 Ordre Atomique et Slippage

```
PROCÉDURE execute_trade(signal):
    # PRÉ-VÉRIFICATIONS
    SI filtre_rollover_spread() == False:
        RETOURNER "ABORTED"

    # Récupérer les paramètres
    direction = signal["direction"]
    sl_price  = signal["sl_price"]
    tp_price  = signal["tp_price"]
    equity    = mt5.account_info().equity
    symbol_info = BLACKBOARD["market_data"]["symbol_info"]

    # Calcul du lot avec protection stoplevel
    sl_adjusted, lot = ajuster_sl_stoplevel(
        entry_price=current_price,
        sl_price=sl_price,
        direction=direction,
        symbol_info=symbol_info,
        equity=equity
    )

    # ──── [R1] ANTI-RACE CONDITION : Vérifier le Kill Event ────
    SI BLACKBOARD["meta"]["kill_event"].is_set():
        LOGGER "🔴 [R1] Kill Event détecté AVANT order_send — ABORT"
        RETOURNER "ABORTED_KILL"

    # ──── [R3] ANTI-DOUBLE EXÉCUTION : Vérifier les positions existantes ────
    existing = mt5.positions_get(symbol="XAUUSD")
    POUR chaque pos DANS existing:
        SI pos.magic == 123456:
            LOGGER "🔴 [R3] Position #{pos.ticket} déjà ouverte avec magic 123456 — ABORT"
            RETOURNER "ABORTED_DUPLICATE"

    # ──── [R10] MODE PAPER TRADING : Simuler sans envoyer ────
    SI BLACKBOARD["meta"]["live_mode"] == False:
        LOGGER "📝 [PAPER] Trade simulé : {direction} {lot} lot — SL:{sl_adjusted} TP:{tp_price}"
        simulated_trade = {
            "direction": direction, "lot": lot, "entry": current_price,
            "sl": sl_adjusted, "tp": tp_price, "time": NOW(),
        }
        BLACKBOARD["paper_trading"]["simulated_trades"].AJOUTER(simulated_trade)
        ÉCRIRE dans simulation_results.csv
        RETOURNER "SIMULATED"

    # CONSTRUCTION DE LA REQUÊTE ATOMIQUE MT5
    request = {
        "action":       mt5.TRADE_ACTION_DEAL,         # Market Order
        "symbol":       "XAUUSD",
        "volume":       lot,
        "type":         mt5.ORDER_TYPE_BUY si direction == "BUY" sinon mt5.ORDER_TYPE_SELL,
        "price":        mt5.symbol_info_tick("XAUUSD").ask si BUY sinon .bid,
        "sl":           sl_adjusted,                    # SL DANS LA MÊME REQUÊTE
        "tp":           tp_price,                       # TP DANS LA MÊME REQUÊTE
        "deviation":    30,                             # 3 pips = 30 points sur XAUUSD
        "magic":        123456,                         # Identifiant unique du robot
        "comment":      "GoldSniper_v1",
        "type_time":    mt5.ORDER_TIME_GTC,             # Good Till Cancel
        "type_filling": mt5.ORDER_FILLING_IOC,          # Immediate Or Cancel
    }

    # ──── [R1] DOUBLE-CHECK Kill Event juste avant l'envoi ────
    SI BLACKBOARD["meta"]["kill_event"].is_set():
        LOGGER "🔴 [R1] Kill Event détecté PENDANT construction ordre — ABORT"
        RETOURNER "ABORTED_KILL"

    # ENVOI AU BROKER
    result = mt5.order_send(request)

    # ──── [R3] Gestion du timeout / erreur réseau ────
    SI result EST None OU result.retcode == TIMEOUT:
        LOGGER "⚠️ [R3] Timeout sur order_send — attente 2s puis vérification"
        ATTENDRE 2 secondes
        check = mt5.positions_get(symbol="XAUUSD")
        POUR chaque pos DANS check:
            SI pos.magic == 123456 ET pos.time > NOW() - 5s:
                LOGGER "✅ [R3] Ordre exécuté malgré le timeout — ticket #{pos.ticket}"
                # Continuer normalement avec ce ticket
                result = SIMULER_RESULT(pos)
                ALLER À succès
        LOGGER "❌ [R3] Ordre non trouvé après timeout — abandon"
        RETOURNER "TIMEOUT_NO_FILL"

    SI result.retcode ≠ mt5.TRADE_RETCODE_DONE:
        LOGGER "❌ Ordre rejeté : {result.retcode} — {result.comment}"
        RETOURNER "REJECTED"

    # SUCCÈS — Enregistrer dans le Tableau Noir
    succès:
    position = {
        "ticket":         result.order,
        "direction":      direction,
        "entry_price":    result.price,
        "current_sl":     sl_adjusted,
        "current_tp":     tp_price,
        "initial_sl":     sl_adjusted,   # [R12] SL d'origine pour R:R et trailing
        "volume":         lot,
        "initial_volume": lot,
        "open_time":      NOW(),
        "risk_amount":    equity × 0.01,
        "rr_current":     0.0,
        "breakeven_hit":  False,
        "partial_taken":  False,
        "partial_volume": 0.0,
        "trailing_active": False,        # [R12] Activé après le partiel
        "trailing_sl_last": None,
    }

    BLACKBOARD["positions"]["open_positions"].AJOUTER(position)
    BLACKBOARD["meta"]["daily_trade_count"] += 1
    BLACKBOARD["meta"]["state"] ← "TRADING"

    # ACTIVER LE COOLDOWN
    BLACKBOARD["meta"]["cooldown_until"] ← NOW() + 3 minutes

    LOGGER "✅ Trade #{result.order} ouvert : {direction} {lot} lot à {result.price}"
    RETOURNER "EXECUTED"
```

**⚠️ POINT CRITIQUE — SL et TP dans la même requête :**
Le `sl` et le `tp` sont envoyés dans le **même objet `request`** que l'ordre de marché. Ceci est **non-négociable** : si le SL/TP était envoyé dans une requête séparée (modification d'ordre), une micro-coupure entre les deux requêtes laisserait le trade sans protection pendant quelques millisecondes, s'exposant à un flash crash.

---

## 6. GESTION DE TRADE (TRADE MANAGEMENT)

### 6.1 Vue d'ensemble

Le Trade Manager est une coroutine qui s'active dès qu'une position est ouverte. Il surveille le prix à chaque tick et exécute les actions de gestion.

```
PROCEDURE trade_manager_loop():
    TANT QUE True:
        ATTENDRE le prochain tick

        positions = BLACKBOARD["positions"]["open_positions"]
        SI positions EST VIDE:
            CONTINUER

        POUR chaque pos DANS positions:
            rr_current = calculer_rr(pos)
            pos["rr_current"] ← rr_current

            # ACTION 1 : Breakeven à R:R 1:1
            SI rr_current >= 1.0 ET pos["breakeven_hit"] == False:
                APPELER move_sl_to_breakeven(pos)

            # ACTION 2 : Prise de profits partielle à R:R 1:2
            SI rr_current >= 2.0 ET pos["partial_taken"] == False:
                APPELER close_partial(pos, percent=50)
```

### 6.2 Calcul du R:R en temps réel

```
FONCTION calculer_rr(position):
    entry = position["entry_price"]
    sl    = position["current_sl"]  # SL actuel (peut avoir bougé pour le BE)
    
    # Le risque initial (pour le calcul R:R) est basé sur le SL ORIGINAL
    # car le R:R mesure la performance relative au risque pris
    risk_distance = |entry - position["initial_sl"]|  # Distance SL originale
    
    SI position["direction"] == "BUY":
        reward_distance = current_bid - entry
    SINON:
        reward_distance = entry - current_ask

    SI risk_distance == 0:
        RETOURNER 0.0

    RETOURNER reward_distance / risk_distance
```

### 6.3 Breakeven Dynamique

```
PROCEDURE move_sl_to_breakeven(position):
    # VÉRIFICATIONS ANTI-ROLLOVER
    SI BLACKBOARD["agents"]["agent_7_sessions"]["rollover_lockout"] == True:
        LOGGER "⚠️ Rollover actif — BE report"
        RETOURNER

    entry_price = position["entry_price"]
    spread_cost = BLACKBOARD["market_data"]["current_tick"]["spread_points"]
                  × BLACKBOARD["market_data"]["symbol_info"]["point_size"]

    # Le breakeven inclut le coût du spread pour un vrai "risque zéro"
    SI position["direction"] == "BUY":
        new_sl = entry_price + spread_cost
    SINON:
        new_sl = entry_price - spread_cost

    # Vérifier que le nouveau SL respecte le stoplevel
    stoplevel_price = BLACKBOARD["market_data"]["symbol_info"]["stoplevel"]
                      × BLACKBOARD["market_data"]["symbol_info"]["point_size"]
    current_price = current_bid si BUY sinon current_ask
    
    SI |current_price - new_sl| < stoplevel_price:
        LOGGER "⚠️ BE impossible : distance < stoplevel"
        RETOURNER

    # Envoi de la modification au broker
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": position["ticket"],
        "symbol": "XAUUSD",
        "sl": new_sl,
        "tp": position["current_tp"],   # TP inchangé
    }

    result = mt5.order_send(request)

    SI result.retcode == mt5.TRADE_RETCODE_DONE:
        position["current_sl"] ← new_sl
        position["breakeven_hit"] ← True
        LOGGER "🟢 Breakeven activé pour #{position['ticket']} à {new_sl}"
    SINON:
        LOGGER "❌ Échec BE : {result.comment}"
```

### 6.4 Prise de Profits Partielle (50% à R:R 1:2)

```
PROCEDURE close_partial(position, percent=50):
    # [R4] PRÉ-VÉRIFICATION : Le résidu sera-t-il viable ?
    volume_step = BLACKBOARD["market_data"]["symbol_info"]["volume_step"]
    volume_min  = BLACKBOARD["market_data"]["symbol_info"]["volume_min"]

    volume_to_close_brut = position["volume"] × (percent / 100)
    volume_to_close = FLOOR(volume_to_close_brut / volume_step) × volume_step
    volume_remaining = position["volume"] - volume_to_close

    # [R4] Si le résidu est < volume_min, le broker refusera toute
    # modification de SL/TP sur le résidu → clôture totale plutôt que partielle
    SI volume_remaining < volume_min:
        LOGGER "⚠️ [R4] Résidu {volume_remaining} < volume_min {volume_min} — clôture TOTALE"
        volume_to_close = position["volume"]

    # Construire l'ordre de clôture partielle
    request = {
        "action":   mt5.TRADE_ACTION_DEAL,
        "symbol":   "XAUUSD",
        "volume":   volume_to_close,
        "type":     mt5.ORDER_TYPE_SELL si BUY sinon mt5.ORDER_TYPE_BUY,  # Sens inverse
        "position": position["ticket"],
        "price":    current_bid si BUY sinon current_ask,
        "deviation": 30,
        "magic":    123456,
        "comment":  "GoldSniper_Partial",
    }

    result = mt5.order_send(request)

    SI result.retcode == mt5.TRADE_RETCODE_DONE:
        position["volume"] -= volume_to_close
        position["partial_taken"] ← True
        position["partial_volume"] ← volume_to_close

        # [R12] Activer le trailing stop structurel sur le résidu
        SI position["volume"] > 0:  # S'il reste un résidu (pas de clôture totale R4)
            position["trailing_active"] ← True
            position["trailing_sl_last"] ← position["current_sl"]
            LOGGER "🟢 [R12] Trailing Stop ACTIVÉ sur résidu {position['volume']} lot"

        LOGGER "💰 Partiel 50% pris pour #{position['ticket']} : {volume_to_close} lot"
    SINON:
        LOGGER "❌ Échec partiel : {result.comment}"
```

### 6.5 Trailing Stop Structurel Post-Partiel [R12]

Après la prise de profit partielle, les 50% restants suivent un **trailing stop basé sur la structure 15M** au lieu d'un TP fixe. Cela permet de capturer les mouvements de continuation (runners) sans risque supplémentaire.

```
PROCEDURE update_trailing_stop(position):
    # PRINCIPE : Le SL suit les Swing Lows (pour BUY) ou Swing Highs (pour SELL)
    #            sur l'UT 15M. Il ne recule JAMAIS (ratchet).

    # VÉRIFICATIONS
    SI BLACKBOARD["agents"]["agent_7_sessions"]["rollover_lockout"] == True:
        RETOURNER  # Pas de modification pendant le rollover

    candles_15m = BLACKBOARD["market_data"]["candles"]["15m"]
    buffer = 2 × BLACKBOARD["market_data"]["symbol_info"]["point_size"]

    SI position["direction"] == "BUY":
        # Chercher le dernier Swing Low 15M confirmé
        dernier_swing_low = trouver_dernier_swing_low(candles_15m, pivot_strength=2)

        SI dernier_swing_low N'EST PAS None:
            nouveau_sl = dernier_swing_low - buffer

            # RATCHET : le trailing ne peut que MONTER (pour un BUY)
            SI nouveau_sl > position["trailing_sl_last"]:
                # Vérifier le stoplevel
                stoplevel_price = BLACKBOARD["market_data"]["symbol_info"]["stoplevel"]
                                  × BLACKBOARD["market_data"]["symbol_info"]["point_size"]
                SI |current_bid - nouveau_sl| ≥ stoplevel_price:
                    # Envoyer la modification
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": position["ticket"],
                        "symbol": "XAUUSD",
                        "sl": nouveau_sl,
                        "tp": 0.0,  # [R12] TP retiré — le trailing gère la sortie
                    }
                    result = mt5.order_send(request)
                    SI result.retcode == mt5.TRADE_RETCODE_DONE:
                        position["trailing_sl_last"] ← nouveau_sl
                        position["current_sl"] ← nouveau_sl
                        LOGGER "🔄 [R12] Trailing SL monté à {nouveau_sl}"

    SI position["direction"] == "SELL":
        # Chercher le dernier Swing High 15M confirmé
        dernier_swing_high = trouver_dernier_swing_high(candles_15m, pivot_strength=2)

        SI dernier_swing_high N'EST PAS None:
            nouveau_sl = dernier_swing_high + buffer

            # RATCHET : le trailing ne peut que DESCENDRE (pour un SELL)
            SI nouveau_sl < position["trailing_sl_last"]:
                SI |current_ask - nouveau_sl| ≥ stoplevel_price:
                    request = {
                        "action": mt5.TRADE_ACTION_SLTP,
                        "position": position["ticket"],
                        "symbol": "XAUUSD",
                        "sl": nouveau_sl,
                        "tp": 0.0,
                    }
                    result = mt5.order_send(request)
                    SI result.retcode == mt5.TRADE_RETCODE_DONE:
                        position["trailing_sl_last"] ← nouveau_sl
                        position["current_sl"] ← nouveau_sl
                        LOGGER "🔄 [R12] Trailing SL descendu à {nouveau_sl}"
```

**Schéma du Trailing Stop structurel :**

```
BUY @ 2643.50 | SL initial: 2640.00 | Partiel 50% pris @ 2650.00

Prix:  2643 ---- 2647 ---- 2650(partiel) ---- 2655 ---- 2660 ---- 2657(sorti)
           \                                 /        /
Trailing:   2640 -- 2640 -- 2640 ---------- 2647.80  2652.30 → SL touché
            (fixe avant partiel)   (suit Swing Lows 15M, ratchet up)
```

---

## 7. INTERFACE GRAPHIQUE (UI) ET WATCHDOG

### 7.1 Interface CustomTkinter

L'UI est une fenêtre **non-bloquante** (exécutée dans le thread principal avec `after()` pour les mises à jour). L'interface communique avec le moteur asyncio via le Tableau Noir (lecture seule pour l'UI, sauf le Kill Switch et le curseur de risque).

#### Layout de l'interface :

```
┌──────────────────────────────────────────────────────────┐
│  🏆 GOLD SNIPER v1.0                    [─] [□] [✕]    │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─── ÉTAT DU SYSTÈME ─────────────────────────────┐    │
│  │  État : 🟢 READY    |  Equity : $10,245.33      │    │
│  │  Spread : 18 pts    |  Trades aujourd'hui : 1/2  │    │
│  │  Session : LONDON OPEN KZ   |  Cooldown : --     │    │
│  └──────────────────────────────────────────────────┘    │
│                                                          │
│  ┌─── LEDs DES 7 AGENTS ──────────────────────────────┐ │
│  │  🟢 Agent 1 (Météo)     BULLISH EXPANSION          │ │
│  │  🟢 Agent 2 (Carto)     OB+ 15M @ 2645.20         │ │
│  │  🔴 Agent 3 (Liquidité) EQH non sweepé             │ │
│  │  🟢 Agent 4 (Fibonacci) Prix dans OTE 67.3%        │ │
│  │  ⚫ Agent 5 (Micro 1m)  SLEEPING                   │ │
│  │  🟢 Agent 6 (Sentinelle) Clear — NFP dans 4h       │ │
│  │  🟢 Agent 7 (Sessions)  London Open KZ             │ │
│  │                                                     │ │
│  │  ⭐⭐⭐☆☆  Score : 3/5                             │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                          │
│  ┌─── POSITION OUVERTE ──────────────────────────────┐  │
│  │  #12345678  BUY 0.28 lot @ 2643.50               │  │
│  │  SL: 2640.00 (BE)  |  TP: 2655.00                │  │
│  │  P/L: +$67.20  |  R:R: 1.4:1                     │  │
│  │  ████████████░░░░░░░░  Partiel: Non               │  │
│  └───────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─── CONTRÔLES ─────────────────────────────────────┐  │
│  │                                                    │  │
│  │  Risque : [===●========] 1.0%                     │  │
│  │                                                    │  │
│  │  ┌────────────────────────────────────────────┐   │  │
│  │  │         🔴 KILL SWITCH 🔴                  │   │  │
│  │  │     (Ferme tout + Arrête le robot)          │   │  │
│  │  └────────────────────────────────────────────┘   │  │
│  │                                                    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─── LOG EN TEMPS RÉEL ─────────────────────────────┐  │
│  │  [14:23:45] ✅ Agent 1 → BULLISH EXPANSION        │  │
│  │  [14:23:46] 🟢 Agent 4 → Prix dans OTE (67.3%)   │  │
│  │  [14:23:47] ⚫ Agent 5 → SLEEPING (hors POI)     │  │
│  │  [14:23:48] 🔄 Heartbeat sync OK (3 pos serveur) │  │
│  └───────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

#### Signification des couleurs LED :

| Couleur | Signification |
|---|---|
| 🟢 Vert | Agent validé (score = 1 ou is_clear = True) |
| 🔴 Rouge | Agent non validé (score = 0 ou is_clear = False) |
| ⚫ Gris | Agent en sommeil (Agent 5 SLEEPING) |
| 🟡 Jaune | Agent en cours de calcul / warning |

### 7.2 Kill Switch — Séquence d'urgence

```
PROCÉDURE kill_switch():
    LOGGER "🔴🔴🔴 KILL SWITCH ACTIVÉ 🔴🔴🔴"

    # 1. Marquer l'état
    BLACKBOARD["meta"]["kill_switch"] ← True
    BLACKBOARD["meta"]["state"] ← "KILLED"

    # 2. Fermer TOUTES les positions ouvertes au marché
    positions = mt5.positions_get(symbol="XAUUSD")
    POUR chaque pos DANS positions:
        close_request = {
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   "XAUUSD",
            "volume":   pos.volume,
            "type":     mt5.ORDER_TYPE_SELL si pos.type == ORDER_TYPE_BUY sinon ORDER_TYPE_BUY,
            "position": pos.ticket,
            "price":    mt5.symbol_info_tick("XAUUSD").bid si SELL sinon .ask,
            "deviation": 50,  # Déviation large pour garantir la fermeture
            "magic":    123456,
            "comment":  "KILL_SWITCH",
        }
        result = mt5.order_send(close_request)
        LOGGER "Fermeture #{pos.ticket} : {result.retcode}"

    # 3. Supprimer tous les ordres pendants
    orders = mt5.orders_get(symbol="XAUUSD")
    POUR chaque order DANS orders:
        mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": order.ticket})

    # 4. Arrêter la boucle asyncio
    asyncio.get_event_loop().stop()

    # 5. Sauvegarder l'état final
    ÉCRIRE recovery.json (état KILLED)

    LOGGER "✅ Kill Switch complet — Toutes positions fermées, robot arrêté"
```

### 7.3 Watchdog (Processus Indépendant)

Le Watchdog est un **processus séparé** (lancé via `multiprocessing`) qui surveille la santé du processus principal.

```
ARCHITECTURE WATCHDOG :

    ┌───────────────────────┐         ┌───────────────────────┐
    │   PROCESSUS PRINCIPAL │  ping   │   PROCESSUS WATCHDOG  │
    │   (asyncio engine)    │────────►│   (boucle simple)     │
    │                       │  toutes │                       │
    │   Écrit un timestamp  │  2 sec  │   Lit le timestamp    │
    │   dans un fichier     │         │   depuis le fichier   │
    │   partagé             │         │                       │
    └───────────────────────┘         └───────────────────────┘

FICHIER PARTAGÉ : watchdog_heartbeat.tmp
    Contenu : timestamp (epoch) du dernier heartbeat

LOGIQUE WATCHDOG :
    TANT QUE True:
        ATTENDRE 5 secondes
        dernier_heartbeat = LIRE watchdog_heartbeat.tmp
        age = NOW() - dernier_heartbeat

        SI age > 15 secondes:
            LOGGER "🚨 WATCHDOG : Le processus principal ne répond plus ({age}s)"

            # ÉTAPE 1 : Tenter un Kill Switch via MT5 direct
            mt5.initialize()
            positions = mt5.positions_get(symbol="XAUUSD")
            POUR chaque pos:
                FERMER au marché (même logique que kill_switch)
            mt5.shutdown()

            # ÉTAPE 2 : Tuer le processus principal
            os.kill(main_pid, signal.SIGTERM)

            # ÉTAPE 3 : Relancer le processus principal
            subprocess.Popen([sys.executable, "main.py"])

            # ÉTAPE 4 : Logger l'incident
            ÉCRIRE dans crash_log.txt

        SI age > 30 secondes:
            # ÉTAPE CRITIQUE : Si ça ne marche toujours pas
            ENVOYER notification (Telegram/Email/Desktop)
            LOGGER "🔴 WATCHDOG : Échec de relance — intervention humaine requise"
```

---

## 8. SCHÉMA DES FLUX DE DONNÉES

### 8.1 Flux principal (du Tick au Trade)

```
MT5 Server
    │
    ▼
┌──────────────────────┐
│   TICK INGESTION     │  mt5.symbol_info_tick("XAUUSD")
│   (bid, ask, time)   │  via asyncio.to_thread()
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   CANDLE BUILDER     │  Agrège les ticks en bougies OHLCV
│   1m → 15m → 4H     │  Met à jour BLACKBOARD["market_data"]["candles"]
└──────────┬───────────┘
           │
           │  Nouvelle bougie 1m clôturée
           ▼
┌──────────────────────────────────────────────────────────┐
│                 AGENT DISPATCH                            │
│                                                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ │
│  │ Agent 1  │  │ Agent 2  │  │ Agent 3  │  │ Agent 4  │ │
│  │ Météo    │  │ Carto    │  │ Liquidité│  │ Fibonacci│ │
│  │ (4H/15M) │  │ (OB/FVG) │  │ (EQH/EQL)│  │ (OTE)   │ │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘ │
│       │              │              │              │       │
│       ▼              ▼              ▼              ▼       │
│  ┌────────────────────────────────────────────────────┐   │
│  │        BLACKBOARD["agents"]                        │   │
│  │  scores, POI, liquidité, niveaux Fibonacci         │   │
│  └──────────────────────┬─────────────────────────────┘   │
│                         │                                  │
│                         ▼                                  │
│  ┌──────────────────────────────────────┐                 │
│  │  Agent 5 (Microscope 1M)             │                 │
│  │  SLEEPING ──[prix ∈ POI]──► SCANNING │                 │
│  │  SCANNING ──[CHoCh+BOS]──► CONFIRMED │                 │
│  └──────────────────┬───────────────────┘                 │
│                     │                                      │
│  ┌──────────┐  ┌────┴─────┐                               │
│  │ Agent 6  │  │ Agent 7  │                               │
│  │Sentinelle│  │ Sessions │                               │
│  │(News)    │  │ (KillZone│                               │
│  └────┬─────┘  └────┬─────┘                               │
└───────┼──────────────┼────────────────────────────────────┘
        │              │
        ▼              ▼
┌──────────────────────────────────────┐
│        AGENT ORCHESTRATEUR           │
│                                      │
│  5★ Check   → Filtres 6+7           │
│  → Cooldown → Daily Limit           │
│  → Spread   → Rollover              │
│  → ✅ EXECUTE ou ❌ REJECT          │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│        EXÉCUTION MT5                 │
│                                      │
│  calcul_lot() → stoplevel check     │
│  → Ordre Atomique (SL+TP inclus)    │
│  → Résultat dans Tableau Noir       │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│        TRADE MANAGER                 │
│                                      │
│  R:R 1:1 → Breakeven                │
│  R:R 1:2 → Partial (50%)            │
│  Surveillance continue par tick      │
└──────────────────────────────────────┘
```

### 8.2 Flux de synchronisation

```
                    ┌─────────────────┐
                    │  recovery.json  │◄──── Sauvegarde chaque tick (debounced 1s)
                    └────────┬────────┘
                             │
        ┌────────────────────┤────────────────────┐
        │                    │                     │
        ▼                    ▼                     ▼
  Cold Start           Crash Recovery         Audit Trail
  (Bootstrap)       (Watchdog Restart)     (Logs immutables)
```

---

## 9. ARBRE DE FICHIERS DU PROJET

```
gold_sniper/
│
├── main.py                          # Point d'entrée : setup asyncio, bootstrap
├── config.py                        # Configuration globale (spread max, risk %, magic number)
├── recovery.json                    # État persisté (auto-généré)
├── watchdog_heartbeat.tmp           # Timestamp heartbeat (auto-généré)
├── crash_log.txt                    # Log des crashs watchdog (auto-généré)
│
├── core/
│   ├── __init__.py
│   ├── blackboard.py                # Classe BlackBoard (dict + asyncio.Lock)
│   ├── engine.py                    # Boucle asyncio principale + coroutines
│   ├── tick_ingestion.py            # Aspiration des ticks MT5
│   ├── candle_builder.py            # Construction des bougies OHLCV multi-TF
│   ├── recovery_manager.py          # Sauvegarde/Restauration recovery.json
│   └── mt5_bridge.py                # Wrapper MT5 avec run_in_executor()
│
├── agents/
│   ├── __init__.py
│   ├── base_agent.py                # Classe abstraite pour tous les agents
│   ├── agent_1_meteo.py             # Agent Météo/Structure (4H/15M BOS)
│   ├── agent_2_cartographe.py       # Agent Cartographe (OB + FVG)
│   ├── agent_3_liquidite.py         # Agent Liquidité (EQH/EQL/Trendlines/Asian)
│   ├── agent_4_fibonacci.py         # Agent Fibonacci (OTE zone)
│   ├── agent_5_microscope.py        # Agent Microscope 1M (CHoCh + BOS entry)
│   ├── agent_6_sentinelle.py        # Agent Sentinelle Éco (News scraping)
│   └── agent_7_sessions.py          # Agent Sessions (Kill Zones + Rollover)
│
├── execution/
│   ├── __init__.py
│   ├── orchestrator.py              # Pipeline de décision 5★
│   ├── risk_calculator.py           # Calcul lot dynamique 1%
│   ├── order_executor.py            # Construction + envoi ordres atomiques
│   └── trade_manager.py             # Breakeven + Partiels + Trailing
│
├── ui/
│   ├── __init__.py
│   ├── dashboard.py                 # Fenêtre CustomTkinter principale
│   ├── agent_leds.py                # Composant LEDs des 7 agents
│   ├── position_panel.py            # Panneau de la position ouverte
│   ├── control_panel.py             # Kill Switch + Curseur risque
│   └── log_viewer.py                # Scroll de logs en temps réel
│
├── watchdog/
│   ├── __init__.py
│   └── watchdog_process.py          # Processus indépendant de surveillance
│
├── utils/
│   ├── __init__.py
│   ├── logger.py                    # Logging structuré (console + fichier) [R17]
│   ├── time_utils.py                # Gestion DST, sessions, fuseaux horaires
│   ├── math_utils.py                # Fonctions math (swing detection, ATR, etc.)
│   └── telegram_notifier.py         # Module de notification Telegram [R9]
│
├── scrapers/
│   ├── __init__.py
│   └── economic_calendar.py         # Scraping Investing.com / API calendrier
│
└── tests/
    ├── test_risk_calculator.py
    ├── test_agents.py
    ├── test_candle_builder.py
    └── test_orchestrator.py
```

---

## 10. MATRICE DES RECOMMANDATIONS (VALIDATION OPSEC) 🎯

Suite à la revue stratégique, les recommandations du Sniper ont été filtrées et intégrées selon le principe du **Filtre Anti-Bullshit**.

### ✅ RECOMMANDATIONS ACCEPTÉES & INTÉGRÉES AU BACKBONE

Ces éléments critiques ont été directement intégrés dans les sections précédentes du document d'architecture :

* **[R1] Race Condition Kill Switch :** Intégré via un `asyncio.Event` global vérifié avant et pendant l'exécution (Section 5.4).
* **[R2] Gap de prix au Breakeven :** Intégré via une vérification synchrone au Cold Start pour couper au marché en cas de gap défavorable (Section 2.4.2).
* **[R3] Double Exécution (Réseau lent) :** Intégré avec une boucle de vérification des tickets pendants (Section 5.4).
* **[R4] Volume résiduel après partiel :** Intégré avec pré-vérification du `volume_min` du broker (Section 6.4).
* **[R5] Spread Dynamique :** Intégré dans le Trade Manager, le spread est vérifié avant toute modification d'ordre (Section 6.1).
* **[R7] Mode Assume Hostile :** Intégré à l'Agent 6. En cas d'échec du scraper principal et du fallback pendant > 5 minutes, le système se verrouille (Section 3).
* **[R8] Fakeout Asiatique :** Intégré à l'Agent 3. Un filtre d'amplitude minimum (0.3 ATR) ignore les micro-ranges (Section 3).
* **[R9] Notifications Telegram :** Ajouté au système pour alerter des trades et des interventions du Watchdog.
* **[R10] Mode Paper Trading :** Intégré (`LIVE_MODE = False`). Permet l'exécution de la logique sans risque financier (Section 5.4).
* **[R12] Trailing Stop Post-Partiel :** Intégré. Les 50% restants suivent les Swing Lows/Highs 15M (Section 6.5).
* **[R15] Rate Limiter MT5 :** Intégré à la boucle d'ingestion pour éviter les freezes (max 10 appels/seconde).
* **[R16] Filtre du Vendredi :** Intégré. Baisse de risque à 18h, coupure totale à 21h (Section 3, Agent 7).
* **[R17] Logs Structurés :** Intégré au module `utils/logger.py`.

---

### ❌ RECOMMANDATIONS REJETÉES (Focus OPSEC)

Ces recommandations ont été explicitement écartées pour préserver la légèreté et la robustesse du système.

#### R6. Flash Crash Spike en 1 tick
* **Statut :** ❌ REJETÉ
* **Raison :** Calculer la vélocité par seconde à chaque tick surcharge l'Agent 5 pour un cas extrêmement rare. Le système est déjà protégé par l'ordre atomique (SL inclus) et le filtre de news économiques (Agent 6) qui couvre 95% des flash crashes.

#### R13. Anti-Corrélation EURUSD/DXY
* **Statut :** ❌ REJETÉ
* **Raison :** Règle du "Un seul front à la fois". Aspirer les ticks du DXY double la charge réseau et complexifie l'Agent 1. Risque élevé de manquer de bons setups sur l'Or à cause d'une latence sur le DXY. Focus 100% sur l'action du prix XAUUSD.

#### R14. Gestion fine des Swaps/Commissions pour l'UI
* **Statut :** ❌ REJETÉ
* **Raison :** Calcul lourd informatiquement sans valeur ajoutée pour la prise de décision. La structure du prix prime sur la précision comptable à quelques centimes près sur l'interface graphique.

---

> **Document rédigé par : Claude Opus 4 — Agent Sniper**
> **Version : 1.0.0**
> **Date : 2025-01-15**
> **Statut : EN ATTENTE DE VALIDATION**
>
> *Aucune ligne de code Python n'a été incluse. Ce document est la carte.
> Le prochain livrable sera le territoire (le code).*

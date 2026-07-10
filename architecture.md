# Architecture Gold Sniper v3.1 — Documentation Centrale

Audit local réalisé le 2026-07-06 dans `C:\Users\tetej\Music\Bug bounty\Trading`.

Ce document est la **référence unique** décrivant l'architecture complète de Gold Sniper : tous les modules, flux, intégrations externes, et l'état actuel du projet. Il remplace et consolide `CLAUDE.md`, `GOLD_SNIPER_ULTIMATE_GOAL_LOOP_PROTOCOL.md`, `.claude/loop.md`, les rapports `docs/` et `reports/`, et le code source audité.

---

## Table des matières

1. [Vision Globale](#1-vision-globale)
2. [Architecture des Processus](#2-architecture-des-processus)
3. [Arborescence Complète](#3-arborescence-complète)
4. [Configuration](#4-configuration)
5. [Core Engine & Orchestrateur](#5-core-engine--orchestrateur)
6. [Système d'Agents](#6-système-dagents)
7. [Pipeline Stratégique Kasper/SMC](#7-pipeline-stratégique-kaspersmc)
8. [Réexécution (Replay) & Backtest](#8-réexécution-replay--backtest)
9. [Exécution Live & Risk Management](#9-exécution-live--risk-management)
10. [Intégration Discord](#10-intégration-discord)
11. [Dashboard Web & Cloudflare Tunnel](#11-dashboard-web--cloudflare-tunnel)
12. [Interface Desktop (UI)](#12-interface-desktop-ui)
13. [Watchdogs & Monitoring](#13-watchdogs--monitoring)
14. [Pipeline Data & Stockage](#14-pipeline-data--stockage)
15. [Utilitaires](#15-utilitaires)
16. [Sécurité & Garde-fous](#16-sécurité--garde-fous)
17. [Infrastructure de Tests](#17-infrastructure-de-tests)
18. [Intégrations Externes](#18-intégrations-externes)
19. [Données Historiques](#19-données-historiques)
20. [État Actuel & Roadmap](#20-état-actuel--roadmap)
21. [Historique des Phases](#21-historique-des-phases)
22. [Zones à Confirmer](#22-zones-à-confirmer)

---

## 1. Vision Globale

### 1.1 Objectif ultime

Gold Sniper est une application de trading algorithmique XAUUSD basée sur une lecture SMC/ICT/Kasper. L'objectif cible :

```text
Analyser → filtrer → décider → exécuter en simulation → gérer → clôturer → journaliser → prouver
```

Le système doit produire des décisions défendables, traçables et rejouables. Une entrée n'est valide que si elle est soutenue par une chaîne complète d'evidence : contexte HTF, liquidité, sweep, réintégration, displacement, structure, POI tradable, confirmation micro, session/news/risk acceptables.

### 1.2 Philosophie Kasper / SMC

```text
HTF bias
  → liquidity pool
  → sweep
  → réintégration
  → displacement
  → BOS / CHoCH
  → OB / FVG / imbalance
  → retest
  → micro confirmation
  → risk realism
  → ENTER / WAIT / REJECT
```

### 1.3 Interdits absolus

| Interdit | Raison |
|---|---|
| Forced `ENTER` | Détruit la preuve stratégique et invalide la validation |
| Baisse opportuniste des seuils | Assimilé à du tuning non prouvé |
| `POI_REACTION` tradable | Setup d'observation, pas d'exécution |
| Bypass risk/news/session | Contourne les hard veto |
| `LIVE_MODE` non validé | Le live n'est pas autorisé sans validation longue (>6 mois) |
| `order_send` hors broker gateway | Toute écriture broker doit passer par `execution/broker_gateway.py` |
| MT5 write dans agents/stratégie/replay/data import | MT5 doit rester confiné à l'exécution ou au read-only historique |
| Replay long avant prerequisites P3 | La phase P3 bloque les replays longs tant que data/import/news/lifecycle ne sont pas stabilisés |

### 1.4 Comportement cible

1. Recevoir les ticks/candles XAUUSD
2. Construire et mettre à jour les timeframes utiles
3. Évaluer les agents de contexte, POI, liquidité, structure, micro, news, session et risk
4. Construire une evidence complète
5. Produire une décision `ENTER`, `WAIT` ou `REJECT`
6. Calculer le risque autorisé
7. Simuler ou exécuter uniquement si tous les garde-fous passent
8. Gérer le cycle de vie du trade (2-legs P3)
9. Clôturer proprement
10. Journaliser les décisions, événements, trades, métriques
11. Produire une preuve replayable
12. Notifier l'opérateur via Discord

---

## 2. Architecture des Processus

Gold Sniper s'exécute comme **trois processus indépendants** :

```
┌──────────────────────────────────────────────────────────┐
│                    PC Manager (pc_manager.py)              │
│  • Connexion Gateway Discord (WebSocket)                  │
│  • Commandes lifecycle: !start, !kill, !restart, !pc_status│
│  • Boutons Discord: Pause, Kill                           │
│  • Écriture inbox JSONL pour commandes非-lifecycle         │
│  • Surveillance watchdog recovery                         │
│  • Boot policy (auto-start après reboot Windows)          │
└──────────┬───────────────────────────────────────────────┘
           │ IPC: data/discord_inbox.jsonl
           ▼
┌──────────────────────────────────────────────────────────┐
│                   Watchdog (watchdog.py)                   │
│  • Lance et surveille main.py                             │
│  • Heartbeat fichier                                      │
│  • Restart automatique si crash                           │
└──────────┬───────────────────────────────────────────────┘
           │ subprocess
           ▼
┌──────────────────────────────────────────────────────────┐
│                  Core Engine (main.py)                     │
│  • 22+ tâches asyncio concurrentes                        │
│  • Pipeline agents (7 agents + risk_manager + macro)      │
│  • Orchestrateur, Trade Manager, services                 │
│  • Dashboard Web + Cloudflare Tunnel                       │
│  • Notifications Discord REST                             │
│  • Watchdogs: MT5, réseau, spread                         │
│  • Persistance: recovery.json, memory.db, décision logs   │
└──────────────────────────────────────────────────────────┘
```

**IPC (Inter-Process Communication) :**
- `data/discord_inbox.jsonl` — pc_manager → main : commandes utilisateur
- `data/bot_ready.json` — main → pc_manager : statut de démarrage + URL Cloudflare
- `kill_flag.txt` — signal d'arrêt inter-processus
- `data/watchdog_state.json` — état du watchdog
- `data/watchdog_recovery.json` — demande de recovery
- `pc_manager.lock` — verrou d'instance unique

---

## 3. Arborescence Complète

### 3.1 Carte des dossiers

| Dossier | Rôle | Fichiers |
|---|---|---|
| `gold_sniper/` | Racine du projet | 440+ fichiers Python |
| `gold_sniper/agents/` | Agents d'analyse live | 12 fichiers |
| `gold_sniper/strategy/` | Moteurs stratégiques Kasper/PDE | 30+ fichiers |
| `gold_sniper/strategies/` | Modules stratégiques legacy (gelés) | 10 fichiers |
| `gold_sniper/replay/` | Replay offline/shadow (31 fichiers) | Moteur, pipeline, simulation |
| `gold_sniper/replay_app/` | Application terminal Replay Control Center V3.2 | 5 fichiers |
| `gold_sniper/core/` | Runtime live : blackboard, engine, orchestrator | 11 fichiers |
| `gold_sniper/execution/` | Gateway broker, execution guard, trade manager | 6 fichiers |
| `gold_sniper/context/` | Contexte de marché, zone lifecycle | 5 fichiers |
| `gold_sniper/orchestrator/` | Shadow orchestrator ICT | 2 fichiers |
| `gold_sniper/data/` | Chargement historique, memory DB | 3 fichiers |
| `gold_sniper/data_pipeline/` | Manifest, news JSONL, agrégation TF | 6 fichiers |
| `gold_sniper/web/` | Dashboard Web + Cloudflare | 2 fichiers |
| `gold_sniper/ui/` | Interface desktop Tkinter | 11 fichiers |
| `gold_sniper/utils/` | Utilitaires transverses | 28 fichiers |
| `gold_sniper/safety/` | Garde-fous branche recherche | 1 fichier |
| `gold_sniper/scrapers/` | Scraping calendrier économique | 1 fichier |
| `gold_sniper/backtesting/` | Backtest engine live-data | 1 fichier |
| `gold_sniper/watchdog/` | Watchdog (init seulement) | 1 fichier |
| `gold_sniper/validation/` | Validation P1/P2/P3, performance summaries | 8 fichiers |
| `gold_sniper/tests/` | Tests unitaires et intégration | 215+ fichiers |
| `gold_sniper/tools/` | Outils de diagnostic | 10 fichiers |
| `gold_sniper/scripts/` | Scripts de lancement | 1 fichier |
| `tools/data_import/` | Import MT5 + normalisation calendrier | 4 fichiers |
| `tools/` | Scripts export/validation | 2 fichiers |
| `reports/` | Rapports de phase P1-P4 | Fichiers .md |
| `docs/` | Documentation historique | Fichiers .md |
| `.claude/` | Configuration Claude Code | settings, workflows, loop |

### 3.2 Fichiers critiques (top 20)

| Fichier | Rôle | Taille |
|---|---|---|
| `gold_sniper/config.py` | Configuration unique : 175+ constantes, tout paramétrable par env | 459 lignes |
| `gold_sniper/main.py` | Point d'entrée headless : cold start, watchdog, boucle asyncio | ~300 lignes |
| `gold_sniper/pc_manager.py` | Processus persistant : Gateway Discord, lifecycle, inbox | ~800 lignes |
| `gold_sniper/core/engine.py` | Superviseur asyncio : 22+ tâches concurrentes avec crash recovery | 254 lignes |
| `gold_sniper/core/blackboard.py` | État partagé central : mémoire, events, lock asyncio | 859 lignes |
| `gold_sniper/core/orchestrator.py` | Cerveau de décision : vote pondéré, decay, stratégie | 772 lignes |
| `gold_sniper/core/mt5_bridge.py` | Pont async unique vers MetaTrader5 | 206 lignes |
| `gold_sniper/strategy/professional_decision_engine.py` | PDE : décision ENTER/WAIT/REJECT | ~500 lignes |
| `gold_sniper/strategy/kasper_scenario_engine.py` | Moteur Kasper : séquence, scoring, grade | ~600 lignes |
| `gold_sniper/strategy/unified_xauusd_strategy.py` | Pipeline unifié Phase 1 : orchestration multi-étage | ~400 lignes |
| `gold_sniper/replay/replay_engine.py` | Moteur de replay legacy : boucle M1 complète | ~3200 lignes |
| `gold_sniper/replay/simulated_trade_manager.py` | Simulation trades 2-legs : lifecycle, equity, events | 2179 lignes |
| `gold_sniper/replay/decision_pipeline.py` | Pipeline agents replay → Evidence → PDE → Kasper | 1439 lignes |
| `gold_sniper/replay/evidence_builder.py` | Construction EvidenceBundle depuis résultats agents | 973 lignes |
| `gold_sniper/execution/trade_manager.py` | Exécution ordres : guard chain, sizing, gestion active | ~580 lignes |
| `gold_sniper/execution/broker_gateway.py` | Chemin unique d'écriture broker | ~100 lignes |
| `gold_sniper/execution/execution_guard.py` | Garde fail-closed avant toute écriture broker | ~80 lignes |
| `gold_sniper/utils/discord_notifier.py` | Notifications Discord REST : embeds, fichiers, boutons | 624 lignes |
| `gold_sniper/web/dashboard_server.py` | Serveur WebSocket + REST pour dashboard temps réel | 676 lignes |
| `gold_sniper/replay_app/Gold_Sniper_Replay.py` | App terminal Replay Control Center V3.2 | ~800 lignes |

---

## 4. Configuration

### 4.1 Source unique de vérité

`gold_sniper/config.py` (459 lignes) est le **seul fichier de configuration**. Toute constante du système y est définie. Les overrides se font par variables d'environnement (fichier `.env`).

### 4.2 Sections de configuration

| Section | Contenu |
|---|---|
| **1. Mode opérationnel** | `RUN_MODE` (LIVE/PAPER/REPLAY/BACKTEST), `ALLOW_BROKER_WRITES`, `RESEARCH_SHADOW_ONLY` |
| **2. Identifiants MT5** | Compte, password, serveur (JustMarkets-Demo3), symbole XAUUSD, `MAGIC_NUMBER=240115` |
| **3. Fuseaux horaires** | UTC, Europe/Paris, America/New_York via zoneinfo |
| **4. Gestion du risque** | RISK_PERCENT=1.0%, MAX_TRADES_PER_DAY=3, DRAWDOWN_LIMIT=5%, CONSECUTIVE_LOSS_LIMIT=3 |
| **5. Filtres de spread** | MAX_SPREAD_POINTS=45, MAX_SPREAD_KILL_ZONE=40, ratio ATR |
| **6. Sessions & Kill Zones** | Asia, Tokyo, London_Open, NY_Open, Overlap, Rollover, Friday_Halt |
| **7. News / Sentinelle** | BLACKOUT_MINUTES=10, HOSTILE_AFTER=300s |
| **8. Rate limiter MT5** | MAX_CALLS_PER_SECOND=10 |
| **9. Bougies** | Profondeur: 4H=120, 15m=384, 1m=1440 |
| **9-bis. TF d'exécution** | `EXECUTION_TF` (1m/5m/15m/30m/1H), `AGENT_TF_LADDER` |
| **10. Agent 3** | Filtre anti-fakeout asiatique: ASIAN_RANGE_MIN_ATR_RATIO=0.3 |
| **11. Agent 5** | SWING_PIVOT_STRENGTH=2 |
| **12. Trade Management** | TP1_RR=1.0, TP2_RR=2.0, BE_PLUS_RR=0.10, PARTIAL_CLOSE_PERCENT=50 |
| **12-bis. Filtre de coût** | `COST_FILTER_ENABLED`, `COST_FILTER_MIN_R_COST_MULT=3.0` |
| **12-ter. Runner Trail** | `RUNNER_TRAIL_ENABLED`, `RUNNER_TRAIL_R=0.5` |
| **13. Notifications Discord** | 4 channels: alerts, commands, reports, logs |
| **14. Paper Trading** | PAPER_SIMULATED_EQUITY=$10,000 |
| **15. Logging** | JSONL rotatif, 10MB, 30 backups, rétention 30 jours |
| **16. Recovery** | RECOVERY_FILE_PATH, debounce 1s |
| **17. Watchdog** | Heartbeat 2s, timeout warning 15s, critical 30s |
| **18. Diamant 5★** | DIAMOND_MIN_RR=3.0, Fibonacci sweet spot 0.68-0.73 |
| **19. Poids adaptatifs** | ADAPTIVE_WEIGHTS_ENABLED=False (désactivé, /calibrate manuel) |
| **20. Finnhub/FMP** | Tokens API pour news |
| **21. Dashboard/Cloudflare** | Port 8765, tunnel timeout 120s, boot ready timeout 180s |

### 4.3 Variables d'environnement clés

```bash
# Overrides runtime sans modifier config.py
GS_EXECUTION_TF="15m"           # Migration M1→M15
GS_STRUCT_TP="1"                # TP structurels
GS_BE_AT_1R="1"                 # BE à 1R
GS_REGIME_FILTER="1"            # Filtre de régime
GS_STRATEGY_V2="1"              # Stratégie dual-edge
GS_RUNNER_TRAIL="1"             # Trailing runner
GS_COST_FILTER="1"              # Filtre de coût
GS_LOSS_BREAKER="3"             # Max 3 SL/jour
GS_ROLLING_DD_PCT="10"          # Protection rolling drawdown
GS_MIN_RR="4"                   # Filtre RR minimum
```

---

## 5. Core Engine & Orchestrateur

### 5.1 Flux de données live

```text
MetaTrader5
  → MT5Bridge (core/mt5_bridge.py) [async wrapper, asyncio.to_thread]
  → TickIngestion (core/tick_ingestion.py) [100ms loop]
  → CandleBuilder (core/candle_builder.py) [10ms loop, 1m/15m/4H]
  → BlackBoard (core/blackboard.py) [état partagé, lock asyncio]
  → Agents 1-7 + RiskManager + MacroMonitor + RegimeDetector
  → Orchestrator (core/orchestrator.py) [event-driven, candle close]
  → trade_signals → TradeManager → BrokerGateway → MT5
```

### 5.2 BlackBoard (core/blackboard.py - 859 lignes)

Le BlackBoard est le **bus central de données** en mémoire. Tous les composants y lisent/écrivent.

**Structure interne (30+ sections) :**
- `meta` : mode, état, connexion MT5, statut Cloudflare, shutdown
- `control` : kill_event, pause_trading, veto flags
- `market_data` : current_tick, candles (deques par TF), ATR, symbol_info, spread_monitor
- `market` : regime, session, correlations
- `agents` : slots pour agents 1-7 + risk_manager
- `agent_results` : résultats publiés par chaque agent
- `orchestrator` : final_score, decision, weighted_score, strategy
- `trade_signals` : signaux d'entrée pour le TradeManager
- `active_trades` : positions ouvertes suivies
- `performance` : daily_stats, closed_today, equity
- `notifications` : queue pour Discord
- `recovery` : snapshots périodiques

**Système d'événements asyncio :**
- `agent_X_ready` — chaque agent signale sa complétion
- `new_candle_1m/15m/4h` — close de bougie
- `candle_close_event` — déclenche l'orchestrateur
- `critical_orchestrator_event` — fast-path pour veto
- `dashboard_update_event` — push WebSocket
- `price_in_poi` — Agent2 active quand le prix entre dans un POI

**API clé :**
- `write_agent_result(agent_id, result)` — Publish/subscribe
- `wait_for_candle_close()` / `wait_for_agent()` — Attente asynchrone
- `read("chemin.nested")` — Lecture dot-notation
- `snapshot()` — Export JSON pour recovery

### 5.3 Engine (core/engine.py - 254 lignes)

Lance **22+ tâches asyncio concurrentes** avec crash recovery :

| Groupe | Tâches |
|---|---|
| **Data Ingestion** | `tick_ingestion_loop`, `candle_builder_loop` |
| **Phase 0 (Gates)** | `risk_manager`, `agent_6_sentinelle`, `agent_7_chronos`, `macro_monitor`, `regime_detector` |
| **Phase 1 (Structurelle)** | `agent_1_meteo`, `agent_2_cartographe` |
| **Phase 2 (Confirmation)** | `agent_3_liquidite`, `agent_4_fibonacci`, `agent_5_microscope` |
| **Phase 3 (Décision)** | `orchestrator_loop` |
| **Exécution** | `trade_manager`, `adaptive_weights`, `memory_learning` |
| **Services** | `account_info_fetcher`, `mt5_watchdog`, `network_watchdog`, `report_scheduler`, `drive_sync`, `dashboard_web`, `recovery_persistence`, `discord_sender` |

**Crash recovery :** `supervised_task()` — jusqu'à 5 restart avec backoff exponentiel (2s → 30s).

### 5.4 Orchestrateur (core/orchestrator.py - 772 lignes)

Cerveau de décision. Fonctionne par **vote pondéré** des agents 1-5.

**Poids de base :**
| Agent | Poids | Rôle |
|---|---|---|
| Agent1 (Météo) | 30 | Structure HTF, biais |
| Agent2 (Cartographe) | 25 | POI, OB, FVG |
| Agent3 (Liquidité) | 20 | Sweeps, liquidité |
| Agent4 (Fibonacci) | 15 | OTE, premium/discount |
| Agent5 (Microscope) | 10 | Trigger micro, niveaux |

**Pipeline de décision (7 étapes) :**
1. **Veto absolu** : risk_manager ou agent_6 → VETOED
2. **Hard filter** : agent_1 ou agent_2 score=0 ou hard_filter_pass=False → REJECT
3. **Conflit directionnel** : agent_1 vs agent_3, diff <10 → REJECT
4. **Score pondéré** : weighted_score = Σ(score × poids × regime_mod × strategy_override)
5. **Signal decay** : -5 pts/min après 3 minutes d'attente
6. **Session awareness** : modificateurs de risque par session
7. **Décision finale** : EXECUTE (≥85), WAIT (≥70), REJECT (<70)

**Stratégies actives** (depuis `strategy_dictionary.py`) : 7 stratégies priorisées par session/régime + 1 fallback conservateur. Le DIAMOND_SETUP est **désactivé** de la sélection automatique.

**Daily limits :** 3 trades standard, 4 avec exceptional override (score ≥92). Reset à minuit UTC.

### 5.5 Visual Layers (core/visual_layers.py)

Modèle de données pour les overlays visuels du dashboard. Chaque agent publie ses éléments graphiques :
- Agent2 : rectangles OB + FVG
- Agent3 : lignes EQH/EQL + rectangle Asian Range
- Agent4 : zones Fibonacci OTE + niveaux
- Agent5 : marqueurs CHoCH + lignes de structure
- Agent7 : fonds de Kill Zone + Volume Profile

Stockage central `VISUAL_LAYERS` (singleton), max 50 items par agent.

### 5.6 Diamond Detector (core/diamond_detector.py)

Détecteur de setup "Diamant 5★" — **alerte uniquement, jamais d'exécution automatique**. Vérifie 5 conditions : confluence POI, Fibonacci sweet spot (0.705), RR minimum, confirmation sweep asiatique, score ≥92.

### 5.7 Recovery Manager (core/recovery_manager.py)

Persistance et recovery cross-restart :
- Récupération positions orphelines (BUG#8 fix)
- Détection de gap breach (si prix a dépassé le SL pendant l'arrêt → close urgent)
- Snapshot périodique (30s) vers `recovery.json`
- Restauration des daily stats

### 5.8 Strategy Dictionary (core/strategy_dictionary.py)

Dictionnaire formel liant sessions/régimes à des stratégies exécutables avec seuils, risque et weight overrides.

---

## 6. Système d'Agents

### 6.1 Vue d'ensemble

| Agent | Nom | Rôle | Inputs | Outputs |
|---|---|---|---|---|
| Agent1 | Météo | Structure HTF, biais, régime | Candles 4H/15M, swings | Biais, structure, régime, score |
| Agent2 | Cartographe | POI, OB, FVG, imbalance | Candles, contexte HTF | Zones POI, OB/FVG, proximité |
| Agent3 | Liquidité | Sweeps, pools liquidité | Highs/lows, equal levels | Événements liquidité, sweeps |
| Agent4 | Fibonacci | OTE, structure, scénario | HTF/MTF, POI anchors | Zone OTE, premium/discount |
| Agent5 | Microscope | Trigger micro, niveaux | M1/M5, POI, liquidité | Entrée, SL, TP, CHoCH |
| Agent6 | Sentinelle | News, calendrier éco | Flux news, spread, volatilité | Veto news, blackout |
| Agent7 | Chronos | Sessions, killzones | Timestamp UTC | Session, killzone, veto Friday |
| RiskManager | — | Protection equity | Equity, daily P&L, pertes | Veto, paper mode, pause |
| MacroMonitor | — | Contexte macro | Indicateurs macro | Régime macro |
| RegimeDetector | — | Détection régime | Contexte marché | Classification régime |

### 6.2 Agent1 — Météo (agents/agent_1_meteo.py)

Détection de swings par logique fractale (4H et 15M), classification de structure (BULLISH/BEARISH/NEUTRAL), BOS, scores de fraîcheur.

### 6.3 Agent2 — Cartographe (agents/agent_2_cartographe.py)

Identification des POI institutionnels : Order Blocks, Fair Value Gaps, imbalances, zones de retest. Classification tradable/non-tradable, mitigation.

### 6.4 Agent3 — Liquidité (agents/agent_3_liquidite.py)

Détection de pools de liquidité (buyside/sellside), equal highs/lows, sweeps, réintégration.

### 6.5 Agent4 — Fibonacci (agents/agent_4_fibonacci.py)

Structure BOS/CHoCH, zone OTE (62-79% retracement), premium/discount, hints de scénario.

### 6.6 Agent5 — Microscope (agents/agent_5_microscope.py)

Détection du pattern AMD (Accumulation-Manipulation-Distribution) sur M1. Identification de sweeps, CHoCH, displacement, retests. Production de niveaux entry/SL/TP.

### 6.7 Agent6 — Sentinelle (agents/agent_6_sentinelle.py)

Surveillance calendrier économique. Sources : Finnhub, FMP, ForexFactory. Blackout ±15min autour des news HIGH impact (NFP, FOMC, CPI, etc.). Mode ASSUME_HOSTILE après 5 échecs consécutifs.

### 6.8 Agent7 — Chronos (agents/agent_7_chronos.py)

Classification de session (ASIA, LONDON_OPEN, NY_OPEN, OVERLAP, etc.), modificateurs de confiance/risque, règles du vendredi, Volume Profile (POC/VAH/VAL).

### 6.9 RiskManager (agents/risk_manager.py)

Surveillance de la courbe d'equity. Vérifie : daily loss >3% → paper mode, drawdown >5% → veto absolu, 3 pertes consécutives → pause 2h. Génère des rapports de diagnostic après séries de pertes.

### 6.10 Base Agent (agents/base_agent.py)

Classe abstraite avec `AgentResult` dataclass (score, hard_filter_pass, direction, reason, veto, risk_modifier) et contrat shadow ICT.

---

## 7. Pipeline Stratégique Kasper/SMC

### 7.1 Architecture duale

Le pipeline stratégique a **deux générations** qui coexistent :

| Génération | Localisation | État |
|---|---|---|
| **Phase 0 (Legacy)** | `strategies/` + `strategy/professional_decision_engine.py` | Gelé, maintenu pour shadow |
| **Phase 1 (Unifié)** | `strategy/unified_xauusd_strategy.py` | Cible, en cours de migration |

### 7.2 Pipeline Unifié Phase 1 (strategy/unified_xauusd_strategy.py)

Orchestrateur multi-étage purement shadow (no side effects) :

```text
Kasper ICT Scenarios
  → Killzone Evaluation (xauusd_killzone_model.py)
  → News Permission (Agent6)
  → Session Permission (Agent7)
  → Liquidity State Machine (liquidity_state_machine.py)
  → POI Quality Gate (poi_quality_gate.py)
  → OTE Confluence (ote_confluence_engine.py) + Session Premium Gate
  → Micro Confirmation (micro_confirmation_engine.py)
  → ATR Risk Model (atr_risk_model.py)
  → UnifiedXauusdDecision (ENTER/WAIT/REJECT)
```

Sortie : `UnifiedXauusdDecision` dataclass avec décision, score, confiance, funnel trace complète.

### 7.3 Sous-engines stratégiques

| Engine | Fichier | Rôle |
|---|---|---|
| **KasperScenarioEngine** | `strategy/kasper_scenario_engine.py` | Vérifie la séquence Kasper complète, scoring, grade, scenario identity |
| **KasperICTScenarioEngine** | `strategy/kasper_ict_scenario_engine.py` | Variante ICT pure du Kasper |
| **ProfessionalDecisionEngine (PDE)** | `strategy/professional_decision_engine.py` | Transforme evidence en décision : hard veto → readiness → scorecard → eligibility → ENTER/WAIT/REJECT |
| **MarketStructureEngine** | `strategy/market_structure_engine.py` | Détection BOS, CHoCH, sweeps depuis candles |
| **MicroConfirmationEngine** | `strategy/micro_confirmation_engine.py` | Confirmation micro avec templates ICT (reversal_strict, continuation_light, etc.) |
| **LiquidityStateMachine** | `strategy/liquidity_state_machine.py` | Classifie l'intention du marché via états de liquidité (10 états : CONSUMED, BREAKOUT, SWEEP_REJECTED, PURGE, RUN, etc.) |
| **LiquidityReconciliation** | `strategy/liquidity_reconciliation.py` | Réconcilie liquidité macro (Agent3) et micro (Agent5) |
| **OTEConfluenceEngine** | `strategy/ote_confluence_engine.py` | Zone OTE 62-79%, discount/premium, confluence POI |
| **VWAP-M1 Engine** | `strategy/vwap_m1_engine.py` | Scalp VWAP/EMA M1 avec détection de trick moves |
| **XAUUSD Killzone Model** | `strategy/xauusd_killzone_model.py` | Sessions NY (London KZ, NY KZ, Silver Bullet, London Close), grades A-D |
| **XAUUSD Market Weather** | `strategy/xauusd_market_weather.py` | Régime, biais EMA-200 M15, état ATR, état volume, permission ALLOW/WAIT/BLOCK |
| **ATR Risk Model** | `strategy/atr_risk_model.py` | Sizing basé ATR, risk bands (FULL/REDUCED/MICRO/TINY/ZERO), modulators |

### 7.4 Contrats et gates

| Module | Rôle |
|---|---|
| `strategy/contracts.py` | EvidenceBundle, contrats de données |
| `strategy/kasper_contracts.py` | Contrats spécifiques Kasper |
| `strategy/readiness.py` | Readiness assessment |
| `strategy/scorecard.py` | Scorecard décisionnelle |
| `strategy/hard_veto_registry.py` | Registre des vetos durs |
| `strategy/enter_eligibility.py` | Éligibilité d'entrée |
| `strategy/risk_allocator.py` | Mapping grade → risk |
| `strategy/poi_quality_gate.py` | Gate qualité POI |
| `strategy/poi_readiness_contract.py` | Contrat readiness POI |
| `strategy/poi_rejection_contract.py` | Contrat rejet POI |
| `strategy/poi_micro_synergy_contract.py` | Synergie POI-Micro |
| `strategy/poi_star_rating.py` | Rating 5★ des POI |
| `strategy/micro_readiness_contract.py` | Contrat readiness micro |
| `strategy/readiness_risk_gate_contract.py` | Gate readiness-risk intégré |
| `strategy/session_premium_ote_gate.py` | Gate session/premium OTE |
| `strategy/setup_taxonomy.py` | Taxonomie des setups |
| `strategy/setup_candidate_mapping.py` | Mapping setup → candidat |
| `strategy/setup_signal_inventory.py` | Inventaire des signaux |
| `strategy/decision_explainer.py` | Explication lisible des décisions |

### 7.5 Modules stratégiques legacy (strategies/)

Les 9 modules dans `strategies/` sont **gelés** (frozen after clean repo restart) :

| Module | Stratégie |
|---|---|
| `no_trade_tokyo.py` | Veto dur pendant Tokyo/Asia |
| `fvg_near_only.py` | FVG proches du prix |
| `fvg_ny_london.py` | FVG uniquement NY/London |
| `fvg_sweep_displacement_retest.py` | FVG avec sweep+displacement+retest |
| `ob_wick_tagged_retest.py` | OB avec wick tag |
| `ob_partial_mitigation_watch.py` | OB partiellement mitigés (watch) |
| `ob_five_star_strict.py` | OB 5★ strict |
| `premium_strict.py` | Gate premium |
| `contextual_drawdown_guard.py` | Garde drawdown (placeholder) |

Le sélecteur `professional_strategy_selector.py` évalue les 9 modules et choisit le meilleur candidat.

### 7.6 Séquence Kasper complète

```text
1. HTF context → biais dominant, structure H1/H4/D1, premium/discount
2. Liquidité → pools buyside/sellside, equal highs/lows
3. Sweep → BSL/SSL ou buyside/sellside, rejet ou réintégration
4. Displacement → impulsion après sweep
5. Structure → BOS/CHoCH/close breaking structure
6. POI → OB/FVG/imbalance/zone institutionnelle, tradable, non mitigué
7. Retest → retour dans la zone
8. Micro confirmation → CHoCH/BOS micro, trigger, entry/SL/TP
9. Risk realism → RR minimum, spread/slippage/cost model, daily limiter
10. Décision → ENTER_FULL/ENTER_REDUCED/WAIT/REJECT
```

### 7.7 Grades et risk mapping

| Grade | Risk cible | Condition |
|---|---|---|
| `A_PLUS` | 1.00% | Setup complet ou quasi-complet |
| `A` | 0.75% | Setup fort |
| `B` | 0.50% | Setup acceptable réduit, ENTER_REDUCED |
| `C_CONFIRMED` | 0.25% | C strictement confirmé |
| `C` | 0 ou watch-only | Observation |
| `D` | 0 | Rejet |

### 7.8 Logique deux-legs P3

```text
1 signal = 1 parent setup = 2 child legs

Risk total parent → split 50/50 → leg TP1 + leg TP2
TP1 touché → runner protégé à +0.5R
TP2 touché → parent +1.5R
SL direct → parent -1R
Protected SL → parent +0.75R
```

---

## 8. Réexécution (Replay) & Backtest

### 8.1 Architecture du système de replay

Le replay est le cœur de validation offline. Deux moteurs coexistent :

| Moteur | Fichier | Approche |
|---|---|---|
| **Legacy ReplayEngine** | `replay/replay_engine.py` (~3200 lignes) | Full-scan M1, pipeline complet |
| **V2 ReplayEngineV2** | `replay/replay_engine_v2.py` (259 lignes) | Candidate-driven, cheap gates avant heavy pipeline |

### 8.2 Flux complet V2 (P4.2)

```text
CSV M1 candles
  → MultiTimeframeBuilder (dérivation TF supérieurs)
  → ReplayClock (itération candle par candle)
  → FeatureStore.update() [cheap, incremental]
  → CandidateDiscoveryEngine.scan() [gates pas chers: session, news, HTF, POI, setup, liquidité]
  → SI window: CandidateWindowEvaluator.evaluate() [pipeline lourd, RARE]
     → ReplayDecisionPipeline (agents → EvidenceBuilder → Kasper → PDE → RiskAllocator)
  → TradeLifecycleSimulator.on_candle() [gestion trades ouverts]
  → MetricsAggregator.record() [incrémental]
  → Rapport final
```

### 8.3 Composants du replay (31 fichiers)

| Composant | Fichier | Rôle |
|---|---|---|
| **Entry Points** | `run_replay.py`, `__init__.py` | CLI argparse, chargement données |
| **Moteurs** | `replay_engine.py`, `replay_engine_v2.py` | Boucle principale |
| **Horloge** | `replay_clock.py` | Horloge déterministe, ReplayTick |
| **Multi-TF** | `multi_timeframe_builder.py` | Construction TF supérieurs depuis M1 |
| **Données** | `historical_data.py` | Chargement CSV, DataQualityReport |
| **Pipeline** | `decision_pipeline.py` (1439 lignes) | Orchestration agents → PDE → Kasper |
| **Evidence** | `evidence_builder.py` (973 lignes) | Construction EvidenceBundle |
| **Discovery V2** | `candidate_discovery.py` | Gates pas chers avant pipeline lourd |
| **Window V2** | `candidate_window.py` | Évaluation fenêtre candidate |
| **Feature Store V2** | `feature_store.py` | Cache incrémental no-lookahead |
| **No-Lookahead** | `no_lookahead_guard.py` | Protection lookahead stricte |
| **Trade Manager** | `simulated_trade_manager.py` (2179 lignes) | Cycle 2-legs, equity, events |
| **Lifecycle V2** | `trade_lifecycle_simulator.py` | Scanner M1 trades ouverts |
| **Offline Sim** | `offline_trade_simulator.py` | Simulation conservative Phase 7 |
| **Exécution Model** | `execution_model.py` | Spread, slippage, commission |
| **Fill Model** | `fill_model.py` | Coûts entrée/sortie, priorité intrabar |
| **Shadow Policy** | `shadow_live_policy.py` | Risk sizing, daily limits, grade mapping |
| **Journal** | `trade_journal.py` | TradeJournalEvent, JSONL |
| **Métriques** | `replay_metrics.py` (1336 lignes) | 50+ blocs diagnostic, phases 7-14 |
| **Métriques V2** | `metrics_aggregator.py` | Incrémental P4.2 |
| **Rapports V2** | `report_writer_v2.py` | Reports compacts, NO_TRADES explicite |
| **Alignment** | `alignment_diagnostic.py` | Diagnostic POI/OTE spatial |
| **News** | `news_index.py`, `news_loader.py`, `news_api_fetcher.py`, `local_calendar_importer.py`, `economic_calendar.py` | Calendrier économique |
| **Profiling** | `replay_profiler.py`, `profiler_v2.py` | Mesure temps par section/agent |
| **Runtime** | `replay_runtime_config.py` | Config frozen fast/slow |
| **I/O** | `buffered_jsonl_writer.py` | JSONL bufferisé |
| **Structure** | `offline_market_structure.py` | Structure candles offline |

### 8.4 Replay Control Center V3.2 (replay_app/)

Application terminal interactive avec Rich TUI :

| Fichier | Rôle |
|---|---|
| `Gold_Sniper_Replay.py` | Point d'entrée : menu interactif + CLI |
| `live_runner.py` | Runner asyncio en thread, LiveState |
| `display.py` | Layout Rich : agents, métriques, décisions |
| `report_writer.py` | Rapports compacts LLM-readable |
| `data_prep.py` | Détection data, génération synthétique |

**Presets intégrés :** 1w / 1m / 2m / 3m / 6m, warmup Décembre 2025, capital initial $100.

### 8.5 Backtest Engine (backtesting/backtest_engine.py)

Moteur de backtest nécessitant une connexion MT5 live. Plus simple que le replay : télécharge M1 par chunks de 30 jours, rejoue via les vrais agents, simule avec ATR-based 1:1.5 RR. Pas de Kasper, pas de coûts avancés, pas de 2-legs.

### 8.6 Garde-fous anti-lookahead

Le système V2 implémente la protection la plus rigoureuse :
- `Feature.available_at` — timestamp de disponibilité
- `assert_available()` — lève `LookaheadError` si accès prématuré
- `guard_feature_access()` — décorateur pour getters FeatureStore
- Injection progressive : seules les candles ≤ current_time sont visibles
- Warmup isolé : `eval_active=False`, pas de trades comptabilisés

---

## 9. Exécution Live & Risk Management

### 9.1 Chaîne d'exécution

```text
TradeManager.place_order()
  → BrokerGateway.execute(BrokerRequest(action=OPEN_ORDER))
  → ExecutionGuard.evaluate(...) [fail-closed]
  → MT5BrokerAdapter.send_order()
  → mt5.order_send(...)
```

### 9.2 ExecutionGuard (execution/execution_guard.py)

Garde **fail-closed** avant toute écriture broker. Vérifications :
- `RUN_MODE == "LIVE"` — seul le mode LIVE autorise les écritures
- `ALLOW_BROKER_WRITES == True`
- Branche recherche → bloqué (RESEARCH_SHADOW_ONLY)
- Kill switch actif → bloqué
- Pause trading → bloqué
- Veto Agent6 → bloqué
- Veto RiskManager → bloqué
- Paper mode forcé → simulation

### 9.3 BrokerGateway (execution/broker_gateway.py)

**Unique point de passage** pour toutes les écritures broker. Route vers :
- `MT5BrokerAdapter` — vrai `order_send` MT5
- `SimulatedBrokerAdapter` — retourne bloqué avec raison

Tout autre `order_send` dans le code métier est une **violation architecturale**.

### 9.4 TradeManager (execution/trade_manager.py)

Seul agent autorisé à envoyer des ordres. Chaîne de validation :
1. Broker action allowed (ExecutionGuard)
2. Cooldown check (180s)
3. Tick validity (bid > 0, ask > 0, ask ≥ bid)
4. Spread check (SpreadMonitor)
5. Volume calculation (RiskCalculator : equity × risk% × ATR-adjusted)
6. Order send via BrokerGateway (entry + SL + TP atomique)
7. Trade tracking dans active_trades

**Gestion post-entrée :**
- Partial close 50% à TP1 (1R)
- SL → BE+0.10R après TP1
- Trailing stop 1× ATR après BE
- Runner trail (si activé) : suit le pic à RUNNER_TRAIL_R derrière

### 9.5 RiskCalculator (execution/risk_calculator.py + utils/risk_calculator.py)

Calcul de taille de position : equity-based, ATR-aware. Réduction progressive du risque si volatilité > baseline (jusqu'à -50%). Plafonné à `MAX_RISK_PCT_PER_TRADE` (1%).

### 9.6 Couches de risk management

| Couche | Localisation | Rôle |
|---|---|---|
| Config | `config.py` | RISK_PERCENT, MAX_TRADES_PER_DAY, DRAWDOWN_LIMIT, CONSECUTIVE_LOSS_LIMIT |
| ExecutionGuard | `execution/execution_guard.py` | Fail-closed pre-broker |
| RiskCalculator | `execution/risk_calculator.py` | Sizing ATR-aware |
| RiskManager Agent | `agents/risk_manager.py` | Equity curve surveillance, pause, paper mode |
| SpreadMonitor | `utils/spread_monitor.py` | Spread check pre-entry |
| Agent6 (Sentinelle) | `agents/agent_6_sentinelle.py` | News blackout |
| Rolling DD Guard | `config.py` | Protection drawdown multi-jours/mois |
| Loss Breaker | `config.py` | Stop après N SL pleins/jour |
| Concurrency Guard | `config.py` | Max positions concurrentes |
| Cost Filter | `config.py` | Rejet si 1R < N× coût exécution |
| Regime Filter | `config.py` | Blocage en STRONG_UP/DOWN |
| Research Guard | `safety/research_branch_guard.py` | Blocage broker sur branche recherche |

---

## 10. Intégration Discord

### 10.1 Architecture deux-processus

```
┌─────────────────────────────────┐    ┌─────────────────────────────────┐
│         PC Manager               │    │         Main Engine              │
│  (pc_manager.py)                 │    │  (main.py)                       │
│                                  │    │                                  │
│  • Gateway WebSocket (discord.py)│    │  • REST API (aiohttp)            │
│  • !start, !kill, !restart       │    │  • DiscordNotifier               │
│  • !pc_status                    │    │  • DiscordCommander              │
│  • Boutons Pause/Kill            │    │  • 16 commandes opérationnelles  │
│  • Écrit inbox JSONL ───────────┼───→│  • Lecture inbox JSONL           │
│  • Boot policy                   │    │  • Notifications temps réel      │
└─────────────────────────────────┘    └─────────────────────────────────┘
```

### 10.2 PC Manager (pc_manager.py)

- **Connexion** : `discord.Client` avec Gateway WebSocket, `Intents.default()` + message_content + guilds + members
- **Commandes lifecycle** (traitées directement) : `!start`, `!kill`, `!restart`, `!pc_status`
- **Commandes non-lifecycle** (enqueued vers inbox JSONL) : toutes les autres
- **Boutons** : Pause (`gs_pause`), Kill (`gs_kill`) via `on_interaction`
- **Autorisation** : vérifie DISCORD_USER_ID, DISCORD_GUILD_ID, DISCORD_COMMANDS_CHANNEL
- **Déduplication** : 5 secondes par commande, inbox dedup
- **Boot policy** : auto-start après reboot Windows si pas de kill_flag

### 10.3 DiscordNotifier (utils/discord_notifier.py)

Client REST-only (pas de Gateway). API v10, `Authorization: Bot <TOKEN>`.

**4 channels Discord :**

| Channel | Usage |
|---|---|
| `alerts` | Signaux trading, ouvertures/fermetures trades, alertes risque, setups diamant |
| `commands` | Réponses aux commandes (!status, !trades, etc.) |
| `reports` | Rapports quotidiens/hebdomadaires, fichiers logs, images chart |
| `logs` | Logs système (fallback sur reports) |

**Méthodes de notification (20+) :**
`notify_system_start`, `notify_signal`, `notify_trade_opened` (avec boutons Pause/Kill), `notify_trade_closed`, `notify_exceptional_setup` (mention @user), `notify_news_alert`, `notify_news_result`, `notify_news_feed_down`, `notify_risk_alert`, `notify_consecutive_losses`, `notify_daily_report`, `notify_weekly_report`, `send_document`

### 10.4 DiscordCommander (utils/discord_commander.py)

Consomme `data/discord_inbox.jsonl` toutes les 750ms.

**16 commandes opérationnelles :**

| Commande | Action |
|---|---|
| `!status` | État complet du système |
| `!pause` | Suspend nouveaux trades |
| `!resume` | Reprend trading |
| `!risk <valeur>` | Ajuste risk% (0.1-2.0%) |
| `!trades` | Liste positions ouvertes |
| `!agents` | Scores des 7 agents |
| `!regime` | Régime et stratégie actifs |
| `!news` | Événements éco 24h |
| `!backtest` | Backtest 7 jours |
| `!calibrate` | Recalibre poids agents |
| `!report` | Rapport immédiat |
| `!logs` | Envoie fichiers logs |
| `!memory` | Statistiques SQLite |
| `!health` | Diagnostic complet |
| `!chart` | Graphique XAUUSD matplotlib |
| `!help` | Liste des commandes |

**Support français** (utils/discord_commands.py) : `!statut`, `!aide`, `!etat`, `!demarrer`, `!arreter`, `!redemarrer`

### 10.5 Autorisation Discord

`discord_command_authorization_failure()` — fail-closed :
- Commandes mutating (start, kill, restart, pause, resume, risk, backtest, calibrate) → refusées si config incomplète
- Commandes read-only (status, trades, agents, help) → autorisées même sans config
- Vérification user_id, guild_id, channel_id

### 10.6 Modules déclenchant des notifications Discord

| Émetteur | Notifications |
|---|---|
| `main.py` | System start, EOD report |
| `core/engine.py` | Discord sender loop (queue blackboard) |
| `core/orchestrator.py` | Trade signals |
| `core/diamond_detector.py` | Diamond setup alerts |
| `core/recovery_manager.py` | Recovery/snapshot |
| `agents/agent_6_sentinelle.py` | News alerts, results, feed down |
| `agents/risk_manager.py` | Risk alerts, consecutive losses |
| `execution/trade_manager.py` | Trade open/close |
| `utils/spread_monitor.py` | Spread too high |
| `utils/emergency_shutdown.py` | Shutdown |
| `utils/network_watchdog.py` | Internet loss/recovery |
| `utils/mt5_watchdog.py` | MT5 disconnect/reconnect |
| `utils/drive_sync.py` | Drive sync failure |
| `utils/report_scheduler.py` | Daily (22:00), weekly (Fri 21:30) |

---

## 11. Dashboard Web & Cloudflare Tunnel

### 11.1 Dashboard Server (web/dashboard_server.py - 676 lignes)

Framework : `aiohttp`. Deux modes : local (localhost:8765) ou public (Cloudflare Tunnel).

**Endpoints REST :**
| Endpoint | Contenu |
|---|---|
| `GET /` | SPA dashboard.html |
| `GET /api/state` | État complet blackboard |
| `GET /api/trades` | Positions ouvertes + P&L flottant |
| `GET /api/agents` | Scores/statuts 7 agents |
| `GET /api/candles` | 500 dernières bougies M1 |
| `GET /ws` | WebSocket temps réel |

**WebSocket :** Push immédiat à la connexion, puis sur chaque `dashboard_update_event` avec fallback heartbeat 1s. Payload : market, agents, orchestrateur, performance, visual layers, candles, trades, logs.

**Sécurité :**
- Mode public : token Bearer obligatoire (`DASHBOARD_TOKEN`)
- Redaction automatique des données sensibles (URLs Cloudflare, tokens, passwords, account info)
- Rate limiting implicite via WebSocket (push, pas poll)

### 11.2 Frontend SPA (web/dashboard.html - 1247 lignes)

Single-page application avec JavaScript vanilla + `lightweight-charts` v4.2 (TradingView-compatible).

**Layout :**
- Header : prix, spread, session, régime, P&L, horloge UTC
- Grille 3 colonnes : Agent cards (7) | Chart + overlays | Orchestrator + metrics
- Barre de statut : signal score, risque, latence, total trades
- Decision log

**Overlays visuels :** OB, FVG, Fibonacci, liquidité, CHoCH, Kill Zones, Volume Profile — rendus en divs HTML positionnées au-dessus du canvas chart.

**Reconnexion WebSocket :** backoff exponentiel 1s → 30s max.

### 11.3 Cloudflare Tunnel (web/dashboard_server.py + utils/cloudflared_manager.py)

Lance `cloudflared.exe tunnel --url http://localhost:8765` comme subprocess. Extrait l'URL `*.trycloudflare.com` du stderr. 3 tentatives avec retry.

`cloudflared_manager.py` gère le cleanup des processus orphelins avant démarrage.

---

## 12. Interface Desktop (UI)

Framework : `customtkinter` (Tkinter modernisé). Secondaire par rapport au dashboard web.

**Fichiers :**
| Fichier | Rôle |
|---|---|
| `ui/dashboard.py` | Fenêtre principale, layout 4 panneaux |
| `ui/components/agent_card.py` | Carte agent avec score, statut, détails |
| `ui/components/header_bar.py` | Barre d'en-tête |
| `ui/components/pipeline_canvas.py` | Animation pipeline neuronal |
| `ui/components/account_panel.py` | Compte, equity, trades |
| `ui/components/log_feed.py` | Flux de logs temps réel |
| `ui/theme.py` | Thème sombre, constantes couleur |
| `ui/agent_leds.py` | Indicateurs LED (vert/rouge/jaune/gris) |
| `ui/account_panel.py` | Panneau compte (legacy) |
| `ui/agent_bureau.py` | Bureau des agents (legacy) |
| `ui/control_panel.py` | Panneau de contrôle (legacy) |
| `ui/log_viewer.py` | Viewer logs (legacy) |
| `ui/position_panel.py` | Panneau positions (legacy) |

---

## 13. Watchdogs & Monitoring

### 13.1 MT5 Watchdog (utils/mt5_watchdog.py)

Surveille la connexion MT5. Si déconnecté : veto risk manager → tentative reconnexion (3 essais, délais 2/4/6s) → alertes Discord à 15s (warning) et 30s (critical).

### 13.2 Network Watchdog (utils/network_watchdog.py)

Ping `1.1.1.1:80` toutes les 15s. Si offline >30s : veto risk manager, alerte Discord. Récupération automatique.

### 13.3 Spread Monitor (utils/spread_monitor.py)

Vérifie le spread avant chaque entrée : spread vs max (normal/killzone), ratio spread/ATR, rollover detection, news blackout. Alerte Discord si spread élevé >5 min.

### 13.4 System Metrics (utils/system_metrics.py)

RAM/CPU monitoring via `psutil`. Utilisé par `!health` et `!pc_status`.

### 13.5 Decision Logger (utils/decision_logger.py)

Logging structuré de chaque cycle de décision en JSONL. Rotation quotidienne, rétention 7 jours. Backfill des résultats de trade. Statistiques de performance par agent (min 50 trades).

### 13.6 Report Scheduler (utils/report_scheduler.py)

Rapports automatiques : quotidien 22:00, hebdomadaire vendredi 21:30, mensuel dernier vendredi 21:00 (Africa/Kinshasa). Envoi Discord + sauvegarde fichier.

### 13.7 Emergency Shutdown (utils/emergency_shutdown.py)

Arrêt d'urgence contrôlé : flag shutdown, fermeture positions MT5 (par MAGIC_NUMBER), clear trade signals, kill event blackboard, notification Discord.

---

## 14. Pipeline Data & Stockage

### 14.1 Données historiques

Structure :
```
gold_sniper/data/historical/XAUUSD/
  1m/XAUUSD_1m_COMPLETE_2025-12-01_2026-06-26.csv  (201,513 candles, 12.7 MB)
  5m/  40,253 candles
  15m/ 13,366 candles
  30m/ 6,644 candles
  1H/  3,284 candles
  4H/  738 candles
  1D/  166 candles
  manifest.json
  news/XAUUSD_news_2025-12-31_2026-06-19.jsonl (4,427 events)
```

**M1 est la source de vérité.** Tous les TF supérieurs sont dérivés de M1 via `MultiTimeframeBuilder` (déterministe, UTC-anchored, no lookahead).

**Provenance :**
- Dec 2025 – Fév 2026 : histdata.com (85,657 candles, spread fixe 32 pts)
- Mar 2026 : histdata.com gap-fill (30,595 candles)
- Mar – Juin 2026 : MT5 JustMarkets-Demo3 (85,261 candles, spread réel 28-36 pts)

### 14.2 Memory DB (data/memory_db.py)

SQLite avec WAL mode. Tables :
- `trade_patterns` — trades fermés avec contexte complet
- `agent_performance` — scores agents vs outcomes
- `error_patterns` — patterns d'erreur dédupliqués
- `strategy_performance` — stats par stratégie/session/régime
- `news_reactions` — réactions aux news

### 14.3 Data Pipeline (data_pipeline/)

| Fichier | Rôle |
|---|---|
| `candle_manifest.py` | Génération manifest des couvertures par TF |
| `news_jsonl.py` | Normalisation news → JSONL |
| `news_sources.py` | Sources de news supportées |
| `run_data_manifest.py` | Script de génération manifest |
| `run_news_import.py` | Script d'import news |
| `timeframe_aggregation.py` | Agrégation TF depuis M1 |

### 14.4 Import tools (tools/data_import/)

| Fichier | Rôle |
|---|---|
| `import_mt5_history.py` | Import MT5 read-only (APIs autorisées : initialize, shutdown, copy_rates_range) |
| `import_external_m1.py` | Import multi-source : histdata.com + Dukascopy |
| `close_m1_gap.py` | Script reproductible de fermeture du gap M1 |
| `normalize_calendar_csv.py` | Normalisation ForexFactory → JSONL UTC |

---

## 15. Utilitaires

### 15.1 Index des 28 fichiers dans utils/

| Fichier | Rôle |
|---|---|
| `discord_notifier.py` | Client REST Discord : embeds, fichiers, boutons |
| `discord_commander.py` | Traitement commandes depuis inbox JSONL |
| `discord_commands.py` | Normalisation commandes, aliases FR/EN, autorisation |
| `discord_boot_notify.py` | Notification boot synchrone (urllib, avant init asyncio) |
| `bot_ready.py` | Signal prêt : phase, URL Cloudflare |
| `logger.py` | Logger structuré JSONL + console, niveau TRADE |
| `decision_logger.py` | Logging décisions, backfill résultats, stats agents |
| `report_scheduler.py` | Rapports planifiés daily/weekly/monthly |
| `emergency_shutdown.py` | Arrêt urgence : flag, close positions, kill |
| `spread_monitor.py` | Vérification spread pre-entry, alertes |
| `mt5_watchdog.py` | Surveillance connexion MT5, reconnexion |
| `network_watchdog.py` | Surveillance connectivité Internet |
| `mt5_bootstrap.py` | Lancement terminal MT5 si absent |
| `system_metrics.py` | RAM/CPU monitoring via psutil |
| `system_tray.py` | Icône Windows system tray |
| `drive_sync.py` | Sync Google Drive quotidienne (23:00) |
| `cloudflared_manager.py` | Gestion processus cloudflared |
| `single_instance.py` | Prévention processus dupliqués, PID locks |
| `lifecycle_lock.py` | Verrou cross-process + dédup messages Discord |
| `inbox_lock.py` | Verrou fichier pour IPC inbox |
| `risk_calculator.py` | Calcul taille position : equity, ATR, volatilité |
| `ssl_bundle.py` | Configuration SSL (Python 3.14+ / Windows) |
| `math_utils.py` | Utilitaires mathématiques |
| `weight_calibrator.py` | Recalibrage poids agents depuis logs décision |
| `command_queue.py` | Queue de commandes / débat |
| `debug_session_log.py` | Logging debug session |
| `agent_dashboard_helpers.py` | Formatage payload dashboard |
| `decision_logger.py` | (déjà listé) |

### 15.2 Google Drive Sync (utils/drive_sync.py)

Synchronise les fichiers critiques vers Google Drive chaque jour à 23:00 :
- `memory.db`, `decision_log.jsonl`, `backtest_results.jsonl`
- Tous les `.txt`/`.json`/`.jsonl` dans `logs/reports/`

OAuth2 avec `credentials.json`, token cache local.

---

## 16. Sécurité & Garde-fous

### 16.1 Research Branch Guard (safety/research_branch_guard.py)

Détecte la branche git courante. Si branche de recherche (ex: `P1-opus`) : force mode REPLAY, bloque toute écriture broker.

### 16.2 Chaîne de garde-fous

```
Niveau 1: Config (RUN_MODE, ALLOW_BROKER_WRITES)
    ↓
Niveau 2: Research Branch Guard (blocage branche recherche)
    ↓
Niveau 3: ExecutionGuard (fail-closed, kill switch, pause, veto)
    ↓
Niveau 4: BrokerGateway (seul point d'écriture MT5)
    ↓
Niveau 5: TradeManager (cooldown, spread, tick validity, sizing)
    ↓
Niveau 6: RiskManager Agent (equity protection, drawdown, pertes)
    ↓
Niveau 7: Agent6 Sentinelle (news blackout)
    ↓
Niveau 8: SpreadMonitor (spread trop large)
    ↓
Niveau 9: Network/MT5 Watchdogs (blocage si déconnecté)
```

### 16.3 Single Instance (utils/single_instance.py)

Prévention de processus dupliqués : PID locks, heartbeat freshness (15s), cleanup artefacts avant start.

---

## 17. Infrastructure de Tests

### 17.1 Organisation

**215+ fichiers de test** dans `gold_sniper/tests/`. Framework : `unittest` (90%) + quelques tests pytest isolés. Pas de `conftest.py`.

### 17.2 Catégories de tests

| Catégorie | Nombre | Fichiers clés |
|---|---|---|
| **P1 Clean** | ~22 | `test_p1_*.py` : agents contracts, decision pipeline, engine contracts, replay determinism, smoke validator |
| **P2A Connectivity** | ~4 | `test_p2a_*.py` : POI connectivity, evidence builder |
| **P2B News/Data** | ~10 | `test_p2b_*.py` : news, calendar, candle manifest, MT5 export |
| **P2C Execution** | ~12 | `test_p2c_*.py` : execution model, fill model, trade journal, simulation |
| **P2D Scorecard** | ~6 | `test_p2d_*.py` : PDE decision states, readiness, scorecard |
| **P2E Phase 7-19** | ~60+ | `test_p2e_phase*.py` : evidence flow, taxonomy, eligibility, risk gates, micro/POI contracts, synergy, reconciliation |
| **P3 Payoff** | ~2 | `test_p3_*.py` : two-leg lifecycle, payoff accounting |
| **P4 Reporting** | ~7 | `test_p4_*.py`, `test_cli_parser_p4_2.py`, `test_candidate_discovery.py` |
| **Agents** | ~5 | Agent 2, 4, 5, 7 + dashboard pulse |
| **Replay** | ~20 | Engine, runner, no MT5, historical data, determinism |
| **Stratégie** | ~15 | PDE, décision explainer, POI star rating/quality, killzone, market weather |
| **Structure** | ~10 | Market structure engine, context, liquidity state machine, zone lifecycle |
| **Guards/Safety** | ~7 | Emergency guard, execution guard, recovery guard, dashboard security, Discord auth |
| **Trade Mgmt** | ~10 | Trade manager BE+/modes/veto, fill model, trade journal |
| **Autres** | ~15 | VWAP, news API, latency, offline simulator, feature store, profit sweep |

### 17.3 Couverture et gaps

**Forces :** Pipeline décisionnel, contrats stratégiques, moteur Kasper, simulation trades, replay determinism, safety guards.

**Gaps notables :**
- Pas de tests pour `main.py`, `pc_manager.py`, `config.py`
- Pas de tests pour `replay_app/`, `scrapers/`, `data_pipeline/`, `ui/`
- Pas de tests E2E ou smoke complets
- Pas de CI/CD, pas de linting, pas de type checking
- Pas de `conftest.py`, pas de fixtures partagées
- Données de test générées inline (pas de répertoire fixtures)

### 17.4 Commandes utiles

```powershell
python -m unittest discover gold_sniper/tests -q
pytest gold_sniper/tests
pytest gold_sniper/tests/test_p3_trade_lifecycle_two_legs.py
```

---

## 18. Intégrations Externes

| Service | Module | Type | Authentification |
|---|---|---|---|
| **MetaTrader 5** | `core/mt5_bridge.py`, `execution/mt5_runtime.py` | API native MT5 | Login/password/serveur |
| **Discord** | `pc_manager.py` (Gateway), `utils/discord_notifier.py` (REST) | Bot API | Token Bot |
| **Google Drive** | `utils/drive_sync.py` | API v3 | OAuth2 |
| **Cloudflare Tunnel** | `web/dashboard_server.py`, `utils/cloudflared_manager.py` | CLI subprocess | Aucune (trycloudflare.com) |
| **ForexFactory** | `scrapers/economic_calendar.py` | XML HTTP | Aucune |
| **Finnhub** | Configuré dans `config.py` | API REST | Token API |
| **FMP** | Configuré dans `config.py` | API REST | Token API |
| **histdata.com** | `tools/data_import/import_external_m1.py` | CSV download | Aucune |
| **Dukascopy** | `tools/data_import/import_external_m1.py` | CSV download | Aucune |

---

## 19. Données Historiques

### 19.1 État P1 final

| Timeframe | Candles | Période | Statut |
|---|---|---|---|
| **M1** | 201,513 | Dec 2025 → Jun 2026 | ✅ Continu, gap fermé |
| **M5** | 40,253 | Dec 2025 → Jun 2026 | ✅ Dérivé M1 |
| **M15** | 13,366 | Dec 2025 → Jun 2026 | ✅ Dérivé M1 |
| **M30** | 6,644 | Dec 2025 → Jun 2026 | ✅ Dérivé M1 |
| **H1** | 3,284 | Dec 2025 → Jun 2026 | ✅ Dérivé M1 |
| **H4** | 738 | Dec 2025 → Jun 2026 | ✅ Dérivé M1 |
| **D1** | 166 | Dec 2025 → Jun 2026 | ✅ Dérivé M1 |

- **Gap M1 fermé** : 14,447 candles comblant Fév-Mar 2026
- **Spread realism** : 32 pts fixe conservateur sur segment histdata.com
- **0 doublon** OHLC vérifié
- **Ordre chronologique strict** vérifié

### 19.2 Calendrier News

- **Source** : ForexFactory `calendar-event-list.csv`
- **Normalisé** : `XAUUSD_news_2025-12-31_2026-06-19.jsonl`
- **4,427 événements**, 736 USD HIGH/MEDIUM
- **Timezone** : UTC
- **Index** : `NewsIndex` — bisect O(log n)

---

## 20. État Actuel & Roadmap

### 20.1 Validé / prouvé

- ✅ Doctrine Gold Sniper/Kasper documentée et testée
- ✅ Pipeline replay Kasper/PDE complet
- ✅ EvidenceBuilder, KasperScenarioEngine, PDE, RiskAllocator
- ✅ SimulatedTradeManager 2-legs P3
- ✅ BrokerGateway + ExecutionGuard
- ✅ **P1 Replay Control Center V3.2** : app terminal, 6 presets
- ✅ **Data M1 complète** : 201,513 candles, continu, gap fermé
- ✅ **7 timeframes reconstruits**
- ✅ **Spread realism** : 32 pts fixe segment histdata.com
- ✅ **Provenance auditée** : chaque candle traçable
- ✅ Calendrier news normalisé Jan-Jun
- ✅ Garde-fous P1 vérifiés : 0 future leakage, 0 forced ENTER, 0 broker writes
- ✅ Intégration Discord complète : 2 processus, 4 channels, 16 commandes, boutons
- ✅ Dashboard Web + Cloudflare Tunnel
- ✅ Watchdogs : MT5, réseau, spread
- ✅ Recovery manager : persistance, positions orphelines, gap breach
- ✅ 215+ tests unitaires et d'intégration

### 20.2 Partiellement validé

- ⚠️ Live runtime : présent mais legacy, non validé live-safe
- ⚠️ Intégration Kasper/PDE dans le live : non prouvée dans `core/orchestrator.py`
- ⚠️ Risk/live lifecycle : constantes legacy (`BE_PLUS_RR=0.10` vs `0.5R` P3 replay)
- ⚠️ Fast replay/precomputed : partiel/stub dans les rapports
- ⚠️ Shadow diagnostics performance : `_build_summary()` lent sur >10K candles (2 GB RAM)

### 20.3 Blocages

| Blocage | Impact | Statut |
|---|---|---|
| Live Kasper/PDE non intégré | Interdit live-safe | ❌ BLOCANT LIVE |
| Divergence protected SL live `0.10R` vs replay `0.5R` | Comportement différent live/replay | ⚠️ A HARMONISER |
| `POI_REACTION` dans compteurs intermédiaires | Doit être confirmé non tradable | ⚠️ A CONFIRMER |
| Shadow diagnostics lent | 2 GB RAM, 10-30 min pour 26K+ events | ⚠️ CONNU |

### 20.4 Roadmap recommandée

1. ✅ ~~Stabiliser et committer l'état documentaire/validation actuel~~ (P1)
2. ✅ ~~Confirmer P3-A/P3-B~~ (P1)
3. ✅ ~~Valider import MT5 read-only + compléter M30/D1~~ (P1)
4. ✅ ~~Confirmer news normalisé utilisé par replay~~ (P1)
5. **Lancer les baselines brutes 1w/1m/2m/3m/6m** ← PROCHAINE ÉTAPE
6. Collecter et analyser les métriques de baseline
7. **Seulement après analyse :** recommander optimisations contrôlées
8. Après 6 mois validation positive : planifier unification live-safe
9. Reporter toute activation live jusqu'à intégration Kasper/PDE + validation explicite

---

## 21. Historique des Phases

| Phase | Signal |
|---|---|
| **P1-clean** | Séparation replay/live, interdiction orchestrateur live en replay |
| **P2-A** | Connectivité POI et handoffs agents |
| **P2-B/C** | Simulation fidèle, validation replay, no forced ENTER |
| **P2-D/E** | Diagnostics POI/micro, refus tuning prématuré |
| **P2.2** | Scenario identity, side consistency, duplicate gate, session veto |
| **P2.3** | Correction warmup/sweep type, bottleneck micro identifié |
| **Final Opus** | Rapport 2 mois positif mais insuffisant pour live |
| **P1** | **Replay Control Center V3.2** : app terminal, 6 presets, data M1 complète |
| **P3-A** | Lifecycle 2-legs implémenté et testé |
| **P3-B** | Payoff replay 1M/2M diagnostiqué |
| **P3-C/D/E/F** | MT5 import, calendar, acceleration, long validation |
| **P4.1** | Reporting fixes |
| **P4.2** | ReplayEngineV2 candidate-driven, FeatureStore, no-lookahead guard |

---

## 22. Zones à Confirmer

| Zone | Question |
|---|---|
| Intégration live Kasper/PDE | Le live doit-il remplacer l'orchestrateur legacy ou l'encapsuler ? |
| `C_CONFIRMED` | Grade core ou policy replay/shadow ? |
| Protected SL live | Harmoniser `0.10R` live et `0.5R` replay |
| `POI_REACTION` counters | Confirmer que les compteurs ne correspondent jamais à des trades exécutés |
| Fast replay | Les flags precomputed sont-ils opérationnels ou stubs ? |
| Live safety parity | Les guards live reproduisent-ils les guards replay/shadow ? |
| Stratégie V2 (dual-edge) | Le mode trend_continuation est-il prêt pour validation ? |
| Runner Trail | Activer par défaut après validation baseline ? |
| Cost Filter | Activer par défaut après mesure du cost drag ? |
| Rolling DD Guard | Activer avec seuils calibrés sur baseline 6 mois ? |

---

## Règle d'or

```text
Ne pas optimiser avant d'avoir vu les baselines.
Ne pas tuner avant d'avoir compris les patterns de rejet.
Ne pas activer le live avant preuve statistique sur 6 mois.

Data complète → News branchée → Lifecycle cohérent → Replay court propre
  → Safety counters propres → Replay long validé → Live-safe pipeline unifié
  → Broker gateway guarded → Validation humaine explicite
```

---

*Document généré le 2026-07-06 — Audit architecture complet Gold Sniper v3.1*

# GOLD SNIPER V3.1 — ARCHITECTURE COMPLÈTE

> Document de référence exhaustif. Toute modification du système doit être répercutée ici.
> Dernière mise à jour : 2026-05-29
>
> **Changelog V3.1** : migration Telegram → Discord ; PC Manager comme gateway unique ;
> signal `bot_ready.json` ; correctif Cloudflare `include_listeners` ; anti-doublons processus.
> Voir aussi [`MIGRATION_DISCORD_MACON.md`](MIGRATION_DISCORD_MACON.md).

---

## 1. Vue d'ensemble du système

| Paramètre | Valeur |
|---|---|
| **Objectif** | Robot de trading algorithmique autonome sur XAUUSD |
| **Broker** | JustMarkets — serveur Demo3 |
| **Symbole** | XAUUSD (or spot, CFD) |
| **Mode actif** | Paper Trading (LIVE_MODE=0) — passer à 1 après validation démo |
| **Compte** | Login MT5 = 1200037833 (alias `MT5_ACCOUNT`) |
| **Magic Number** | 240115 (filtre nos ordres dans MT5) |
| **Langage** | Python 3.11+ / asyncio (testé Python 3.14 + `truststore`) |
| **Hébergement** | PC Windows local — PC Manager (Task Scheduler) + Watchdog + `main.py` |
| **Accès distant** | Dashboard Web via Cloudflare Tunnel + pilotage **Discord** (`pc_manager.py`) |
| **Version** | **V3.1** — interface Discord (remplace Telegram V3.0) |

### Philosophie de conception

Le système repose sur le pattern **Blackboard Architecture** : un état global central en RAM, alimenté par des agents spécialisés qui travaillent en parallèle. L'Orchestrateur lit cet état à chaque cycle, agrège les scores, et décide d'exécuter ou non un trade. Aucun agent ne communique directement avec un autre — tout passe par le Blackboard.

---

## 2. Structure complète du dossier

```
gold_sniper/
├── main.py                     # Point d'entrée moteur — cold start, dashboard, engine
├── pc_manager.py               # Gateway Discord UNIQUE — lifecycle !start !kill !restart
├── config.py                   # SEULE source de vérité des paramètres
├── watchdog.py                 # Watchdog externe (surveille main.py)
├── .env                        # Variables sensibles (DISCORD_TOKEN, MT5, …)
├── GoldSniper.bat              # Lance watchdog (avec garde anti-doublon)
├── LancerManager.bat           # Lance le PC Manager manuellement
├── Install_Autostart.bat       # Installe autostart Windows (tâche planifiée manager)
├── recovery.json               # Snapshot de récupération (auto-généré)
├── kill_flag.txt               # Arrêt demandé par !kill (pc_manager)
├── watchdog_heartbeat.tmp      # Battement de cœur watchdog externe
│
├── core/                       # Moteur central
│   ├── blackboard.py           # État global partagé (SINGLETON BLACKBOARD)
│   ├── engine.py               # Boucle principale, tick ingestion, orchestration
│   ├── orchestrator.py         # Orchestrateur V2.0 — agrège les scores, décide
│   ├── strategy_dictionary.py  # 9 stratégies avec leurs paramètres
│   ├── diamond_detector.py     # Détecteur setup 5★ Diamant
│   ├── recovery_manager.py     # Sauvegarde/restore de l'état (recovery.json)
│   ├── mt5_bridge.py           # Couche d'abstraction MT5 (rate-limited)
│   ├── candle_builder.py       # Construit les bougies OHLCV depuis les ticks
│   └── tick_ingestion.py       # Ingestion des ticks MT5 en temps réel
│
├── agents/                     # Les 7 agents d'analyse + Risk Manager
│   ├── base_agent.py           # Classe abstraite commune à tous les agents
│   ├── agent_1_meteo.py        # Météo de marché (bias 4H/15m, BOS/CHOCH)
│   ├── agent_2_cartographe.py  # Cartographie (OB, FVG, Breaker Blocks, POI)
│   ├── agent_3_liquidite.py    # Liquidité (EQH/EQL, sweep, range asiatique)
│   ├── agent_4_fibonacci.py    # OTE Fibonacci (61.8–78.6%, sweet spot 68–73%)
│   ├── agent_5_microscope.py   # Microscope 1m (CHOCH, AMD, confirmation entrée)
│   ├── agent_6_sentinelle.py   # Calendrier économique (news, veto, stealth)
│   ├── agent_7_chronos.py      # Sessions, Kill Zones, Volume Profile
│   ├── risk_manager.py         # Surveillance equity, drawdown, pertes consécutives
│   ├── regime_detector.py      # Détection du régime de marché (trend/range/volatile)
│   └── macro_monitor.py        # DXY, US10Y, corrélations macro via yfinance/FMP
│
├── execution/                  # Gestion des ordres
│   ├── trade_manager.py        # Cycle de vie complet d'un trade (SL/TP, BE, trailing)
│   ├── adaptive_weights.py     # Poids adaptatifs (désactivé semaine démo)
│   └── risk_calculator.py      # Calcul du lot size basé sur l'ATR
│
├── utils/                      # Utilitaires transversaux
│   ├── logger.py               # Logger structuré JSONL (rotation, rétention 30j)
│   ├── discord_notifier.py     # Notifications Discord (embeds REST, canaux alerts/reports)
│   ├── discord_commander.py    # Consommation data/discord_inbox.jsonl (commandes !)
│   ├── discord_commands.py     # Normalisation !status / alias FR
│   ├── discord_boot_notify.py  # Notifications boot PC Manager
│   ├── bot_ready.py            # Écriture data/bot_ready.json (signal Cloudflare)
│   ├── cloudflared_manager.py  # Cleanup cloudflared / port 8765
│   ├── single_instance.py      # Anti-doublons manager + pile bot
│   ├── lifecycle_lock.py       # Déduplication messages lifecycle Discord
│   ├── inbox_lock.py           # Verrou cross-process inbox JSONL
│   ├── ssl_bundle.py           # SSL Python 3.14 Windows (truststore)
│   ├── mt5_bootstrap.py        # Démarrage MT5 avant !start
│   ├── decision_logger.py      # Log JSON de chaque décision (EXECUTE/REJECT/WAIT)
│   ├── drive_sync.py           # Sync quotidien vers Google Drive (23h00)
│   ├── report_scheduler.py     # Rapport hebdomadaire automatique (dimanche 20h)
│   ├── spread_monitor.py       # Surveillance spread temps réel, alertes
│   ├── mt5_watchdog.py         # Watchdog interne MT5 (connexion, reconnexion)
│   ├── weight_calibrator.py    # Calibration des poids (batch 50 trades, /calibrate)
│   ├── system_tray.py          # Icône Windows barre de notification (pystray)
│   ├── math_utils.py           # Fonctions mathématiques (ATR, pivots, etc.)
│   ├── risk_calculator.py      # Calcul de risque (lot sizing ATR)
│   ├── emergency_shutdown.py   # Arrêt d'urgence + fermeture de toutes les positions
│   └── system_metrics.py       # RAM/CPU pour !pc_status et !health
│
├── data/                       # Données persistées
│   ├── discord_inbox.jsonl     # File commandes Discord (PC Manager → moteur)
│   ├── bot_ready.json          # URL Cloudflare + phase boot (lu par pc_manager)
│   ├── pc_manager.lock         # Verrou instance unique PC Manager
│   ├── memory_db.py            # Interface SQLite (mémoire trades, patterns, analyses)
│   ├── memory.db               # Base de données SQLite (générée automatiquement)
│   ├── historical_loader.py    # Chargement des données historiques MT5 → parquet
│   ├── credentials.json        # OAuth2 Google Drive (NE PAS COMMITTER)
│   ├── drive_token.json        # Token Drive auto-généré après première auth
│   └── historical/             # Cache parquet des données historiques XAUUSD
│
├── web/                        # Dashboard Web
│   ├── dashboard.html          # Interface HTML/CSS/JS (SPA, pas de framework)
│   └── dashboard_server.py     # Serveur aiohttp + WebSocket event-driven
│
├── backtesting/
│   └── backtest_engine.py      # Moteur de backtest (cache parquet, warmup, validation)
│
├── scripts/
│   ├── setup_windows_autostart.ps1  # Tâche planifiée PC Manager (une seule entrée)
│   ├── stop_all_gold_sniper.ps1     # Arrêt complet dépannage
│   ├── guard_launch.py              # Refuse double lancement manager/bot
│   ├── install_deps.ps1             # pip install requirements.txt
│   └── start_mt5_minimized.ps1      # Lance MT5 minimisé (legacy / optionnel)
│
├── logs/                       # Fichiers de logs (auto-créé)
│   ├── gold_sniper_YYYY-MM-DD.jsonl   # Logs structurés du bot
│   ├── watchdog.log            # Logs du watchdog externe
│   ├── decision_log.jsonl      # Historique de chaque décision
│   ├── reports/                # Rapports hebdomadaires générés
│   └── backtests/              # Résultats des backtests
│
└── tests/                      # Tests unitaires
```

---

## 3. Pipeline de décision A→Z

```
MT5 (ticks XAUUSD)
        │
        ▼
[tick_ingestion.py]
  • Lit les ticks via mt5.copy_ticks_from()
  • Rate-limiter : max 10 appels MT5/seconde
  • Écrit dans Blackboard["market_data"]["current_tick"]
        │
        ▼
[candle_builder.py]
  • Agrège les ticks en bougies 1m / 15m / 4H
  • Déclenche les events "new_candle_1m" et "new_candle_15m"
  • Stocke dans Blackboard["market_data"]["candles"]
        │
        ▼
[7 Agents en parallèle — asyncio.gather()]
  Agent 1 (Météo)    → lit candles 4H+15m → publie bias + BOS
  Agent 2 (Carto)    → lit candles 15m    → publie OB/FVG/POI
  Agent 3 (Liquidité)→ lit candles 15m+1m → publie EQH/EQL/sweep
  Agent 4 (Fibonacci)→ lit candles 15m    → publie OTE zone
  Agent 5 (Microscope)→ attend "price_in_poi" event → confirme 1m
  Agent 6 (Sentinelle)→ fetch calendrier économique → veto si news
  Agent 7 (Chronos)  → heure UTC → session/kill_zone/VP
        │
        ▼
[Blackboard.write_agent_result(agent_id, result)]
  • Stocke le résultat dans Blackboard["agent_results"]
  • Déclenche dashboard_update_event → WebSocket < 5ms
  • Déclenche agent_N_ready event → Orchestrateur se réveille
        │
        ▼
[orchestrator.py — attend tous les agents]
  1. Vérifie les hard filters (Agent 1, Agent 2 obligatoires)
  2. Vérifie les vetos (Agent 6, Risk Manager, spread_monitor)
  3. Calcule le score pondéré : Σ(agent_score × poids × regime_modifier)
  4. Applique la stratégie active (session × régime)
  5. Seuil de déclenchement : 70 points minimum
  6. Décision : EXECUTE / REJECT / WAIT / COOLDOWN
        │
        ▼
[diamond_detector.py] (si score > 90)
  • Vérifie RR ≥ 3.0, sweet spot Fibonacci 68–73%
  • Labellise le signal comme "DIAMOND_5STAR"
        │
        ▼
[trade_manager.py] (si EXECUTE)
  • Calcule le lot size : equity × risk% / (SL_pips × tick_value)
  • Vérifie spread < MAX_SPREAD_POINTS (45)
  • Vérifie cooldown, limite journalière (2 trades/j)
  • Envoie order_send() via mt5_bridge.py
  • Pose SL + TP1 + TP2 atomiquement
  • Surveille TP1 (BE), trailing SL, TP2 (fermeture partielle 50%)
        │
        ▼
[decision_logger.py] + [discord_notifier.py]
  • Log JSON de la décision
  • Notification Discord : trade ouvert, TP1 atteint, SL touché, etc.
        │
        ▼
[risk_manager.py] (boucle parallèle, toutes les 10s)
  • Recalcule drawdown journalier
  • Veto si drawdown ≥ 5% ou 3 pertes consécutives
        │
        ▼
[Dashboard WebSocket] → Navigateur / Mobile
  • Latence < 5ms grâce à dashboard_update_event (event-driven)
```

---

## 4. Les 7 Agents

### Agent 1 — Météo de marché (`agent_1_meteo.py`)

| Propriété | Valeur |
|---|---|
| **Rôle** | Filtre macro : détermine le biais directionnel global |
| **Input** | Bougies 4H (120 bougies) + 15m (384 bougies) |
| **Output** | `direction` (LONG/SHORT/NEUTRAL), `bias_4h`, `bias_15m`, `mtf_aligned`, `bos_level` |
| **Logique** | Détecte les Break of Structure (BOS) et Change of Character (CHOCH) par analyse des swing highs/lows. Alignement multi-timeframe requis (4H ET 15m dans le même sens). |
| **Score** | 0 (pas d'alignement) ou 1 (alignement parfait) — **Hard Filter** |
| **Poids** | 25% (le plus élevé) |
| **Veto** | `hard_filter_pass = False` bloque toute la pipeline |

### Agent 2 — Cartographe (`agent_2_cartographe.py`)

| Propriété | Valeur |
|---|---|
| **Rôle** | Carte des Points d'Intérêt (POI) : OB, FVG, Breaker Blocks |
| **Input** | Bougies 15m |
| **Output** | `active_ob`, `active_fvg`, `breaker_blocks[]`, `poi_zone`, `zone_is_fresh` |
| **Logique** | Identifie les Order Blocks (dernière bougie avant un mouvement impulsif), Fair Value Gaps (écart entre High[i+1] et Low[i-1]), et les Breaker Blocks (OB cassé qui change de polarité). |
| **Score** | 0–100 basé sur fraîcheur, taille, retouches du POI |
| **Poids** | 20% |
| **Veto** | `hard_filter_pass = False` si aucune zone valide |

### Agent 3 — Liquidité (`agent_3_liquidite.py`)

| Propriété | Valeur |
|---|---|
| **Rôle** | Détecte les chasses de liquidité (liquidity sweeps) |
| **Input** | Bougies 15m + 1m, range asiatique |
| **Output** | `sweep_detected`, `sweep_side`, `sweep_depth_ratio`, `asian_range`, `idm_detected` |
| **Logique** | Identifie les Equal Highs/Lows (EQH/EQL), vérifie si le prix a chassé ces niveaux (sweep). Calcule le range asiatique (session 0h–8h UTC+1) et vérifie qu'il est ≥ 30% de l'ATR(14, 4H) [R8]. |
| **Score** | 0–100 basé sur profondeur du sweep et qualité de l'IDM |
| **Poids** | 15% |

### Agent 4 — Fibonacci (`agent_4_fibonacci.py`)

| Propriété | Valeur |
|---|---|
| **Rôle** | Valide que le prix est dans la zone OTE Fibonacci |
| **Input** | Bougies 15m (swing high/low derniers mouvements) |
| **Output** | `ote_low`, `ote_high`, `ote_sweet`, `in_ote`, `in_discount`, `in_premium`, `precision_pct` |
| **Logique** | Calcule les retracements 61.8%–78.6% du dernier mouvement impulsif. Sweet spot : 68–73% (zone d'Optimal Trade Entry). Distinction premium/discount selon l'équilibre 50%. |
| **Score** | 0–100 basé sur la précision de positionnement dans l'OTE |
| **Poids** | 15% |

### Agent 5 — Microscope (`agent_5_microscope.py`)

| Propriété | Valeur |
|---|---|
| **Rôle** | Confirmation d'entrée en 1 minute (micro-structure) |
| **Input** | Bougies 1m (1440 bougies) — activé par l'event `price_in_poi` |
| **Output** | `choch_detected`, `choch_price`, `sweep_1m_confirmed`, `amd_phase`, `entry_price`, `sl_price`, `tp1_price`, `tp2_price` |
| **Logique** | Attend que le prix entre dans un POI (Agent 2 déclenche `price_in_poi`). Cherche alors un CHOCH sur 1m + sweep de liquidité 1m pour confirmer l'inversion. Identifie la phase AMD (Accumulation/Manipulation/Distribution). |
| **Score** | 0–100 basé sur qualité du CHOCH et timing AMD |
| **Poids** | 15% |

### Agent 6 — Sentinelle (`agent_6_sentinelle.py`)

| Propriété | Valeur |
|---|---|
| **Rôle** | Gardien calendrier économique — veto absolu autour des news |
| **Input** | Finnhub API (primaire) → FMP API (fallback) → ForexFactory (scraping XML, fallback final) |
| **Output** | `veto`, `impact_level`, `next_event`, `resume_at`, `stealth_mode`, `feed_alive` |
| **Logique** | Scrape le calendrier toutes les 60s. Veto ±15min autour des news HIGH impact (USD/XAU). Stealth mode 60min après une news majeure. Si toutes les sources échouent 5× consécutives → `ASSUME_HOSTILE` (blocage automatique). Mémoire 30 jours des événements passés en SQLite. |
| **Score** | 100 (neutre) ou 0 (veto) |
| **Poids** | 0% (agent de veto, pas de score pondéré) |

### Agent 7 — Chronos (`agent_7_chronos.py`)

| Propriété | Valeur |
|---|---|
| **Rôle** | Gestionnaire des sessions et Kill Zones temporelles |
| **Input** | `datetime.now(UTC)`, Volume Profile calculé |
| **Output** | `in_kill_zone`, `kill_zone_name`, `risk_modifier`, `trading_allowed`, `vp_poc`, `vp_vah`, `vp_val` |
| **Logique** | Autorise le trading uniquement dans les 3 Kill Zones : London Open (8h–11h), NY Open (13h–16h), Overlap (13h–17h). Gère le DST automatiquement via `zoneinfo`. Friday Mode : réduction risque 18h, arrêt 21h [R16]. Bloque pendant le rollover 23h45–00h15 [R5]. |
| **Score** | 0 (hors Kill Zone) ou 60–100 (selon qualité de la session) |
| **Poids** | 10% |

---

## 5. L'Orchestrateur V2.0 (`core/orchestrator.py`)

### Fonctionnement event-driven

L'Orchestrateur ne tourne pas en polling. Il attend les events `agent_N_ready` via `asyncio.Event`. Dès que tous les agents requis ont publié, il déclenche son calcul.

### Pipeline de décision

```
1. HARD FILTERS (bloquants absolus)
   • Agent 1 hard_filter_pass = True (alignement multi-tf)
   • Agent 2 hard_filter_pass = True (POI valide)
   • Agent 6 veto = False (pas de news en cours)
   • Risk Manager veto = False (drawdown OK)
   • Control.paused = False (pas en pause via !pause Discord)
   • Spread < MAX_SPREAD_POINTS (45 points)
   • Kill Zone active (Agent 7 trading_allowed = True)

2. CALCUL DU SCORE
   score = Σ(agent_i.score × poids_i × regime_modifier)
   
   Poids par défaut :
   Agent 1 : 25%  Agent 2 : 20%  Agent 3 : 15%
   Agent 4 : 15%  Agent 5 : 15%  Agent 7 : 10%

3. MODIFICATEUR DE RÉGIME
   TRENDING_BULL/BEAR : ×1.2 (bonus momentum)
   RANGING            : ×0.9 (malus choppy)
   VOLATILE/HIGH_VOL  : ×0.7 (malus risque)
   UNKNOWN            : ×1.0 (neutre)

4. STRATÉGIE ACTIVE
   Sélectionnée selon session × régime (9 combinaisons)
   Chaque stratégie a son seuil de déclenchement (70–85 pts)

5. DÉCROISSANCE TEMPORELLE
   Signal non exécuté : score × 0.98 par cycle de 5s
   Score < 40 : signal abandonné

6. DÉCISION FINALE
   score ≥ seuil + tous hard filters : EXECUTE
   veto actif                         : REJECT
   score insuffisant                  : WAIT
   post-trade                         : COOLDOWN (180s)
```

---

## 6. Le Risk Manager (`agents/risk_manager.py`)

### Seuils de protection

| Condition | Action |
|---|---|
| Perte journalière ≥ 3% | Bascule en Paper Trading forcé |
| Perte journalière ≥ 5% | **VETO absolu** — arrêt total |
| 3 pertes consécutives | Pause 2 heures + rapport diagnostic Discord |
| Récupération > 1.5% | Retour en mode Live automatique |

### Intégration `/resume`

La commande `!resume` (Discord) écrit `control.risk_manager_pause_reset = True` dans le Blackboard. Au prochain cycle du Risk Manager (toutes les 10s), ce flag est détecté : `consecutive_losses` est remis à 0, `pause_until = None`. Le flag est ensuite effacé pour éviter les réinitialisations répétées.

### Rapport diagnostic (automatique)

Après 3 pertes consécutives, le Risk Manager génère et envoie via Discord :
- Nombre de pertes
- Agent le plus suspect (score fort sur les trades perdants)
- Session/Régime dominant sur les pertes
- Pattern d'erreur identifié
- Précision de chaque agent en %

---

## 7. Les 9 stratégies du dictionnaire (`core/strategy_dictionary.py`)

| Stratégie | Session | Régime | Seuil | Paramètres spéciaux |
|---|---|---|---|---|
| `LONDON_TREND_FOLLOWER` | London Open | TRENDING | 72 pts | Trailing SL activé dès TP1 |
| `LONDON_OTE_PULLBACK` | London Open | RANGING | 75 pts | OTE strict 68–73% |
| `LONDON_VOLATILE_SNIPER` | London Open | VOLATILE | 82 pts | Lot réduit ×0.7 |
| `NY_TREND_FOLLOWER` | NY Open | TRENDING | 70 pts | Max 2 trades/session |
| `NY_REVERSAL` | NY Open | RANGING | 78 pts | CHOCH 1m obligatoire |
| `NY_VOLATILE_SNIPER` | NY Open | VOLATILE | 85 pts | Score minimum élevé |
| `OVERLAP_MOMENTUM` | Overlap | TRENDING | 70 pts | Volume Profile POC requis |
| `OVERLAP_SCALP` | Overlap | RANGING | 76 pts | TP1 réduit (1.5R) |
| `GENERIC_DIAMOND` | Tout | Tout | 88 pts | Réservé aux setups 5★ |

---

## 8. Gestion des trades (`execution/trade_manager.py`)

### Cycle de vie d'un trade

```
ORDER_SENT
    │
    ▼
OPEN (SL + TP1 + TP2 posés atomiquement)
    │
    ├── TP1 atteint (RR = 1.0) ──→ Breakeven activé (SL → entry)
    │                              Fermeture partielle 50% du volume
    │
    ├── TP2 atteint (RR = 2.0) ──→ Fermeture du solde + notification
    │
    └── SL touché ──────────────→ Trade fermé + compteur pertes++
```

### Lot sizing ATR

```python
lot = (equity × risk_pct / 100) / (sl_pips × tick_value_per_pip)
# sl_pips calculé depuis l'ATR(14, 15m) × 1.5
# Arrondi au volume_step MT5
# Clampé entre volume_min et volume_max
```

### Règles atomiques

- SL + TP1 + TP2 posés dans le même cycle que l'ordre d'entrée
- Si le spread dépasse 2× la moyenne mobile pendant une modification → report de la modification
- Slippage maximum autorisé : 30 points (3 pips)

---

## 9. Calendrier économique — Chaîne Finnhub → FMP → ForexFactory

### Flux de données

```
Finnhub API (https://finnhub.io)
  └─ Gratuit, 60 req/min, JSON propre, fiable
  └─ Échec ? → FMP API (https://financialmodelingprep.com)
              └─ 200 req/jour max, données macro + calendrier
              └─ Échec ? → ForexFactory (scraping XML cache/forexfactory.xml)
                           └─ Dernier recours, pas de clé requise
```

### Règles de blocage

| Condition | Action |
|---|---|
| News HIGH impact USD/XAU dans ±15min | Veto (score Agent 6 = 0) |
| Stealth mode (60min post-news HIGH) | Score réduit de 30% |
| 5 échecs consécutifs toutes sources | Mode ASSUME_HOSTILE : veto permanent jusqu'au redémarrage |
| Feed vivant mais news inconnue | Passage silencieux (feed_alive = True) |

### Mémoire 30 jours (SQLite)

Chaque événement économique récupéré est stocké en base avec son impact réel observé (variation prix XAUUSD ±5min). Utilisé pour affiner l'analyse d'impact.

---

## 10. Sessions et Kill Zones (UTC+1 / Europe/Paris)

| Session | Début | Fin | Trading |
|---|---|---|---|
| ASIA | 00h00 | 08h00 | NON |
| **LONDON_OPEN** (Kill Zone) | **08h00** | **11h00** | **OUI** |
| LONDON (cadre large) | 08h00 | 17h00 | Non (sauf Kill Zone) |
| **NY_OPEN** (Kill Zone) | **13h00** | **16h00** | **OUI** |
| **OVERLAP** (Kill Zone) | **13h00** | **17h00** | **OUI** |
| NY (cadre large) | 13h00 | 22h00 | Non (sauf Kill Zone) |
| OFF_HOURS | 22h00 | 00h00 | NON |
| ROLLOVER | 23h45 | 00h15 | NON (blocage absolu) |

### Modes spéciaux

- **DST** : géré automatiquement via `zoneinfo.ZoneInfo("Europe/Paris")`
- **Friday Mode** : risque → 0.5% à 18h00, trading arrêté à 21h00 [R16]
- **Rollover** : aucune ouverture ni modification d'ordre possible

---

## 11. Mémoire — SQLite (`data/memory_db.py`)

### Tables principales

| Table | Contenu |
|---|---|
| `trades` | Historique complet (ticket, entrée, SL, TP, PnL, session, régime) |
| `agent_scores` | Score de chaque agent sur chaque trade exécuté |
| `news_events` | Calendrier économique archivé (30 jours) |
| `loss_analyses` | Rapports diagnostics post-pertes consécutives |
| `weight_snapshots` | Historique des poids calibrés |

### Analyse 5 pertes consécutives

1. Extraction des 5 derniers trades perdants
2. Identification de l'agent le plus suspect
3. Corrélation session/régime dominant
4. Rapport Discord + stockage en `loss_analyses`
5. Suggestion automatique : réduire le poids de l'agent suspect

### Google Drive sync (`utils/drive_sync.py`)

- Déclenché tous les soirs à **23h00** (scheduler `schedule`)
- Fichiers uploadés : `data/memory.db`, `logs/decision_log.jsonl`, `logs/backtests/`, `logs/reports/`
- Credentials : `data/credentials.json` (OAuth2) + `data/drive_token.json` (token auto-refresh)
- Dossier Drive : `GoldSniper_V3_Backups`
- Nommage : `YYYY-MM-DD__data__memory.db`

---

## 12. Discord — Commandes et notifications (V3.1)

### Architecture communication

```
Utilisateur (#gold-sniper-commands)
        │
        ▼
pc_manager.py (Gateway Discord UNIQUE)
        ├── Lifecycle : !start !kill !restart !pc_status
        │       → watchdog.py → main.py
        │       → attend data/bot_ready.json (URL Cloudflare)
        └── Opérationnel : !status !agents … (moteur actif)
                → data/discord_inbox.jsonl
                        ▼
                discord_commander.py (dans main.py)
                        ▼
                discord_notifier.py → #gold-sniper-alerts / #gold-sniper-reports
```

### Canaux Discord

| Canal | Variable `.env` | Usage |
|---|---|---|
| Alerts | `DISCORD_ALERTS_CHANNEL_ID` | Boot, trades, alertes risque, news |
| Commands | `DISCORD_COMMANDS_CHANNEL_ID` | Commandes utilisateur |
| Reports | `DISCORD_REPORTS_CHANNEL_ID` | Rapports journaliers / hebdo |
| Logs | `DISCORD_LOGS_CHANNEL_ID` | Logs détaillés (optionnel) |

### Commandes lifecycle (PC Manager)

| Commande | Description |
|---|---|
| `!start` | Démarre MT5 si besoin, watchdog, attend URL Cloudflare |
| `!kill` | Arrêt complet (kill_flag, watchdog, main, cloudflared) |
| `!restart` | `!kill` puis `!start` |
| `!pc_status` | RAM, CPU, état pile — disponible même si moteur arrêté |

Alias FR : `!demarrer`, `!arreter`, `!redemarrer`, `!etatpc`.

### Commandes opérationnelles (moteur actif, via inbox)

| Commande | Description |
|---|---|
| `!status` / `!statut` | État complet système |
| `!pause` / `!resume` | Suspendre / reprendre les trades |
| `!kill` | *(lifecycle — géré par PC Manager)* |
| `!risk 0.5` | Modifier le risque (0.1–3.0%) |
| `!trades` | Positions ouvertes |
| `!agents` | Scores des 7 agents |
| `!regime` | Régime, session, stratégie |
| `!news` | Calendrier 24h |
| `!report` | Rapport journalier immédiat |
| `!backtest` | Dernier backtest |
| `!calibrate` | Calibration des poids |
| `!health` | Diagnostic système (psutil) |
| `!chart` | Graphique XAUUSD (matplotlib) |
| `!logs` / `!memory` | Logs session / stats SQLite |
| `!help` / `!aide` | Liste des commandes |

### Notifications automatiques (embeds)

| Événement | Canal typique |
|---|---|
| Démarrage moteur | Alerts — embed « Gold Sniper opérationnel » + URL Cloudflare |
| Trade ouvert / TP / SL | Alerts |
| 3 pertes consécutives | Alerts + rapport diagnostic |
| Drawdown / spread / news | Alerts |
| Rapports planifiés | Reports |
| Crash watchdog | Alerts (REST API) |

### Sécurité Discord

- **Utilisateur unique** : seul `DISCORD_USER_ID` peut commander.
- **Canal commands** : messages hors `DISCORD_COMMANDS_CHANNEL_ID` ignorés.
- **Déduplication lifecycle** : `utils/lifecycle_lock.py` (`claim_discord_message`) — un message = une exécution.
- **Debounce** : cooldown sur `!start` / `!restart` (évite double-clic).
- **Anti-doublon processus** : un seul `pc_manager.py` actif (`data/pc_manager.lock`).
- **Jamais localhost dans Discord** : uniquement URL `trycloudflare.com` publique.

### Signal de boot : `data/bot_ready.json`

Écrit par `web/dashboard_server.py` dès obtention de l’URL Cloudflare :

```json
{
  "ready_at": "2026-05-29T06:08:33Z",
  "cloudflare_url": "https://xxx.trycloudflare.com",
  "phase": "cloudflare_ready",
  "pid": 12345,
  "mode": "PAPER"
}
```

Le PC Manager poll ce fichier dans `_wait_for_bot_ready()` (timeout `BOOT_READY_TIMEOUT`, défaut 180 s).

### Correctif Cloudflare V3.1 (critique)

`utils/cloudflared_manager.cleanup_before_tunnel()` :

- **`include_listeners=False`** (défaut) lors du tunnel depuis `main.py` — ne pas tuer le processus qui écoute sur le port 8765.
- **`include_listeners=True`** uniquement dans `prepare_clean_stack_start()` avant un nouveau `!start`.

---

## 13. Dashboard Web (`web/`)

### Architecture

```
Navigateur / Mobile
        │ WebSocket (wss://)
        ▼
Cloudflare Tunnel
        │ http://localhost:8765
        ▼
aiohttp server (dashboard_server.py)
        │
        ├── GET  /             → dashboard.html (SPA)
        ├── GET  /api/state    → JSON état complet
        ├── GET  /api/trades   → JSON positions ouvertes
        ├── GET  /api/agents   → JSON scores agents
        └── WS   /ws           → WebSocket event-driven
```

### WebSocket event-driven (latence < 5ms)

Le handler WebSocket **ne poll pas** (plus de `sleep(0.5)`). Il attend `blackboard.dashboard_update_event` — un `asyncio.Event` déclenché à chaque :
- `write_agent_result()` (résultat d'un agent)
- `update_market()` (nouveau tick marché)

Timeout de secours : 1 seconde (heartbeat minimal).

### Reconnexion automatique (Cloudflare)

```javascript
function connectWS() {
    ws = new WebSocket(wsUrl);
    ws.onclose = () => { fetchFallback(); setTimeout(connectWS, 2000); };
    ws.onerror = () => ws.close();  // onclose gère le reste
    ws.onmessage = (e) => updateDashboard(JSON.parse(e.data));
}
```

Si Cloudflare coupe la connexion (timeout 100s), reconnexion automatique en 2s avec fallback HTTP polling.

### Payload WebSocket

```json
{
  "type": "state",
  "ts": "2026-05-27T00:00:00.000000",
  "state": { "meta": {...}, "market": {...}, "orchestrator": {...} },
  "trades": { "open_trades": [...], "total_pnl": 0.0, "open_count": 0 },
  "agents": { "agents": [...], "updated_at": "..." },
  "logs":   [ {"ts": "...", "level": "INFO", "msg": "..."} ]
}
```

---

## 14. Infrastructure

### PC Manager (`pc_manager.py`) — V3.1

Processus **toujours actif** sur le PC de trading (autostart Windows). Responsabilités :

| Fonction | Détail |
|---|---|
| Gateway Discord | Seul `discord.Client` du système |
| Lifecycle | `!start`, `!kill`, `!restart`, MT5 bootstrap |
| Attente boot | Poll `data/bot_ready.json` + URL Cloudflare |
| Enqueue commandes | Écrit dans `data/discord_inbox.jsonl` si moteur actif |
| SSL | `utils/ssl_bundle.py` + hook `_async_setup_hook` (Python 3.14) |

**Python** : `PYTHON_BIN` dans `config.py` (défaut `pythoncore-3.14-64\pythonw.exe`).  
**Logs** : `logs/pc_manager.log`.

### Watchdog externe (`watchdog.py`)

Processus Python **indépendant** qui surveille le processus principal :

| Paramètre | Valeur |
|---|---|
| Intervalle de vérification | 5 secondes |
| Timeout heartbeat | 60 secondes |
| Délai avant restart | 10 secondes |
| Max restarts / 10 minutes | 3 |

Le bot principal écrit `watchdog_heartbeat.tmp` toutes les 2 secondes. Si le fichier n'est pas mis à jour sous 60s → le watchdog tue le processus et le relance.

**Exécutable** : `PYTHON_BIN` dans `config.py` (via `_command_from_env()` dans `watchdog.py`). Override : `GOLD_SNIPER_WATCHDOG_COMMAND`.

**Notifications crash** : REST Discord (`notify_discord`) — alias legacy `notify_telegram`.

### Auto-boot Windows (V3.1)

`scripts/setup_windows_autostart.ps1` crée **une seule** tâche planifiée `GoldSniper_PC_Manager` :
- **Déclencheur** : ouverture de session utilisateur
- **Commande** : `pythonw.exe pc_manager.py` (pas de double entrée Démarrage + tâche)
- Au boot : policy `kill_flag` → attente `!start` manuel ou autostart bot selon configuration

`Install_Autostart.bat` / `scripts/check_autostart.ps1` pour vérifier l’installation.

MT5 : démarré par `_ensure_mt5_or_fail()` dans le PC Manager avant chaque `!start`.

### MT5 minimisé

`scripts/start_mt5_minimized.ps1` : lance MT5 avec `SW_SHOWMINIMIZED` pour ne pas polluer l'écran. MT5 doit être déjà installé et configuré sur le compte JustMarkets-Demo3.

### Rotation des logs

- Format JSONL (une ligne JSON par entrée)
- Rotation à 10 MB
- Rétention 30 jours (purge automatique au démarrage)
- Fichier watchdog séparé : `logs/watchdog.log`

---

## 15. APIs connectées

| API | Usage | Clé | Limite | Statut |
|---|---|---|---|---|
| **MT5 (MetaTrader5 lib)** | Ticks, bougies, ordres, compte | Login/Password dans `.env` | 10 appels/s (rate-limiter) | ✅ Principal |
| **Discord API** | Gateway (PC Manager) + REST (notifier) | `DISCORD_TOKEN` dans `.env` | Rate limits Discord | ✅ Actif V3.1 |
| **Telegram Bot API** | *(V3.0 — retiré)* | `TELEGRAM_*` legacy | — | ❌ Remplacé |
| **Finnhub** | Calendrier économique | `FINNHUB_TOKEN` dans `.env` | 60 req/min (gratuit) | ✅ Primaire Agent 6 |
| **FMP** | Macro (DXY, US10Y, calendrier) | `FMP_TOKEN` dans `.env` | 200 req/jour | ⚠️ Fallback Agent 6 |
| **yfinance** | DXY, GLD, US10Y (corrélations macro) | Aucune | Non documentée | ✅ Macro Monitor |
| **Google Drive** | Backup SQLite + logs | OAuth2 `credentials.json` | 15 GB gratuit | ✅ Sync 23h00 |
| **Cloudflare Tunnel** | Dashboard public | `cloudflared.exe` local | Gratuit (trycloudflare) | ✅ Auto au boot |

---

## 16. Règles de trading codées (liste exhaustive)

| Règle | Code | Description |
|---|---|---|
| R1 | `kill_event` | Kill Switch global irréversible — arrêt immédiat de toute activité |
| R2 | Hard filters A1+A2 | Les agents 1 et 2 doivent tous les deux passer leur hard filter |
| R3 | Seuil de score | Score minimum 70 pts pour EXECUTE (variable selon stratégie) |
| R4 | Veto Agent 6 | News HIGH/MEDIUM ±15min = blocage absolu |
| R5 | Rollover | Aucun ordre ni modification entre 23h45 et 00h15 UTC+1 |
| R6 | Spread max | Spread > 45 points = pas d'ouverture |
| R7 | Assume Hostile | 5 échecs consécutifs du feed news = veto automatique |
| R8 | Range asiatique | Range asiatique < 30% ATR(14,4H) = Agent 3 score 0 |
| R9 | Discord | Toutes les alertes critiques envoyées sur Discord (#gold-sniper-alerts) |
| R10 | Paper Trading | LIVE_MODE=0 = simulation pure (aucun ordre réel envoyé à MT5) |
| R11 | Cooldown | 180s de cooldown obligatoire après chaque trade |
| R12 | Risque 1% | Maximum 1% de l'equity par trade (paramétrable via /risk) |
| R13 | Un seul symbole | Seul XAUUSD traité — multi-symbole rejeté |
| R14 | RR minimum | Risk:Reward ≥ 2.0 requis pour valider un signal |
| R15 | Rate limiter MT5 | Maximum 10 appels MT5 par seconde |
| R16 | Friday Mode | Risque réduit à 0.5% après 18h vendredi, arrêt à 21h |
| R17 | Logs 30j | Rotation des logs automatique, rétention 30 jours |
| R18 | Trades/jour | Maximum 2 trades par jour (sauf Diamant 5★) |
| R19 | Slippage max | Slippage > 30 points = ordre refusé |
| R20 | Décroissance | Signal non exécuté : score × 0.98 toutes les 5s |

---

## 17. Backtest Engine (`backtesting/backtest_engine.py`)

### Architecture

- **Cache Parquet** : données historiques XAUUSD stockées dans `data/historical/` au format Parquet. Rechargement instantané (< 1s) sans requête MT5.
- **Warmup** : 100 bougies de warmup avant le début de la période testée pour initialiser les indicateurs (ATR, swings).
- **Fidélité** : rejoue exactement la même logique que le moteur live (mêmes agents, même Orchestrateur).

### Validation

```
Résultats stockés dans : logs/backtests/backtest_results.jsonl
Accessible via Discord : `!backtest`
Métriques : win rate, profit factor, max drawdown, Sharpe ratio
```

---

## 18. Calibration des poids (`utils/weight_calibrator.py`)

### Conditions

- Minimum **50 trades** en base SQLite pour déclencher une calibration
- Calcul : précision de chaque agent sur les trades gagnants vs perdants
- Normalisation : les nouveaux poids somment à 100%
- Stockage : snapshot en base + application immédiate dans l'Orchestrateur

### Adaptive Weights (désactivé)

```python
ADAPTIVE_WEIGHTS_ENABLED = False  # config.py ligne 216
```

Désactivé pendant la 1ère semaine démo pour éviter l'oscillation. Réactiver après 50+ trades validés. La calibration manuelle via `/calibrate` reste disponible.

---

## 19. Problèmes connus et limitations actuelles

| Problème | Sévérité | Statut |
|---|---|---|
| Timeout Cloudflare 180s sur `!start` | Critique | ✅ V3.1 — `include_listeners=False` dans cleanup tunnel |
| `main.py` crash code=1 ~5s (watchdog loop) | Critique | ✅ V3.1 — même correctif |
| Réponses Discord en double | Haute | ✅ V3.1 — single instance PC Manager + autostart unique |
| PC Manager muet (SSL Python 3.14) | Critique | ✅ V3.1 — `ssl_bundle.py` + truststore |
| `!kill` ne stoppait pas tout | Haute | ✅ V3.1 — kill_flag + cleanup cloudflared |
| Port 8765 WinError 10048 | Haute | ✅ Mitigation — `stop_all` + prepare_clean_stack_start |
| Dashboard WebSocket latence (polling) | Haute | ✅ Corrigé (event-driven < 5ms) |
| WebSocket Cloudflare reconnexion | Haute | ✅ Corrigé (connectWS, 2000ms) |
| `data/credentials.json` non versionné | Info | Documenté |
| FMP : limite 200 req/jour | Moyenne | Mitigation : cache 60s |
| ForexFactory XML peut casser | Basse | Fallback `cache_forexfactory.xml` |

---

## 20. Roadmap V4.0

| Fonctionnalité | Priorité | Notes |
|---|---|---|
| **Multi-symbole** (EURUSD, NAS100) | Haute | Nécessite refacto Blackboard (par symbole) |
| **Backtesting vectorisé** (numpy/pandas pur) | Haute | 100× plus rapide que la simulation tick-by-tick |
| **Agent 8 — Sentiment** (COT, retail positioning) | Moyenne | Sources : CFTC COT + MT4/MT5 retail sentiment |
| **Interface web admin** (controls Discord dans le dashboard) | Moyenne | Évite d'ouvrir Discord pour !pause, !resume |
| **Mode Live automatique** après 50 trades démo profitables | Haute | Validation humaine requise avant flip LIVE_MODE=1 |
| **Alertes SMS** (Twilio) en complément Discord | Basse | Redondance si Discord down |
| **Equity curve en temps réel** dans le dashboard | Moyenne | Graphique canvas JS |
| **API REST publique** pour intégration n8n/Zapier | Basse | Export signaux vers d'autres systèmes |
| **Gestion multi-compte** (MT5 + MT4 simultané) | Basse | Architecture complexe |
| **Détection de régime par ML** (SVM/LSTM) | Haute | Remplace le détecteur heuristique actuel |

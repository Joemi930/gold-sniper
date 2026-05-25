# Gold Sniper V2 - Architecture

Gold Sniper V2 est un moteur de trading XAUUSD base sur Python, asyncio,
MetaTrader 5 et une architecture multi-agents. Le systeme est concu pour
avancer par filtres successifs: collecte de donnees, analyse par agents,
veto risque/news, scoring orchestre, puis execution controlee.

## FONCTIONNALITES

Inventaire audite le 2026-05-25. Statuts:

- ACTIF: code branche dans le demarrage ou valide par test local.
- PARTIEL: code present mais dependant d'un token, d'un compte, d'une action OAuth,
  ou non applique de bout en bout.
- INACTIF: code present mais non branche, ancien chemin remplace, ou artefact mort.

| Nom | Fichier | Description (1 phrase) | Statut |
|---|---|---|---|
| Connexion MT5 | `core/mt5_bridge.py` | Initialise MT5, lit ticks, symboles, positions et donnees historiques. | ACTIF |
| Tick ingestion | `core/tick_ingestion.py` | Publie les ticks XAUUSD dans le Blackboard avec cadence limitee. | ACTIF |
| Candle builder | `core/candle_builder.py` | Construit et met a jour les bougies multi-timeframes depuis les ticks. | ACTIF |
| Blackboard central | `core/blackboard.py` | Stocke l'etat partage, les agents, les signaux, le controle et les evenements. | ACTIF |
| Event bus agents | `core/blackboard.py` | Reveil event-driven de l'orchestrateur via `wait_for_agent_update`. | ACTIF |
| Orchestrateur V3 | `core/orchestrator.py` | Agrege les agents, applique veto, strategie active, scores et decision. | ACTIF |
| Dictionnaire de strategies | `core/strategy_dictionary.py` | Selectionne les strategies actives par session, regime, news et diamond setup. | ACTIF |
| Agent 1 Meteo | `agents/agent_1_meteo.py` | Analyse le biais structurel HTF et sert de hard filter directionnel. | ACTIF |
| Agent 2 Cartographe | `agents/agent_2_cartographe.py` | Detecte et score OB/FVG/POI avec bougies institutionnelles. | ACTIF |
| Bougies institutionnelles | `agents/agent_2_cartographe.py` | Detecte engulfing, rejection candle et institutional candle pour booster l'OB. | ACTIF |
| Agent 3 Liquidite | `agents/agent_3_liquidite.py` | Detecte sweep, break, Asian Range et signaux de liquidite. | ACTIF |
| Agent 4 Fibonacci | `agents/agent_4_fibonacci.py` | Calcule premium/discount, OTE et sweet spot 70.5%. | ACTIF |
| Agent 5 Microscope | `agents/agent_5_microscope.py` | Valide la sequence AMD avec sweep puis CHoCH. | ACTIF |
| Agent 6 Sentinelle | `agents/agent_6_sentinelle.py` | Gere news high impact, blackout, alertes et fallback Finnhub -> FMP -> ForexFactory. | ACTIF |
| Agent 7 Chronos | `agents/agent_7_chronos.py` | Determine sessions, kill zones, DST dynamique et Friday mode. | ACTIF |
| Regime detector | `agents/regime_detector.py` | Publie le regime de marche courant dans le Blackboard. | ACTIF |
| Macro monitor Pearson | `agents/macro_monitor.py` | Calcule correlation DXY/Gold et force macro via yfinance. | ACTIF |
| Risk Manager | `agents/risk_manager.py` | Applique drawdown, pertes consecutives, veto et diagnostic. | ACTIF |
| Diagnostic Risk Telegram | `agents/risk_manager.py`, `core/engine.py` | Envoie un rapport apres 3 pertes consecutives si Telegram est configure. | PARTIEL |
| Trade Manager | `execution/trade_manager.py` | Execute les signaux, pose SL/TP broker, gere TP1, BE et trailing. | ACTIF |
| Order_send atomique SL/TP | `execution/trade_manager.py` | Envoie entree, SL et TP broker dans la meme requete MT5. | ACTIF |
| Fermeture partielle TP1 | `execution/trade_manager.py` | Ferme 50% au vrai TP1 stocke a l'ouverture, puis breakeven et trailing. | ACTIF |
| Emergency shutdown | `utils/emergency_shutdown.py` | Ferme ou bloque les positions en cas de commande kill ou risque critique. | ACTIF |
| Recovery positions MT5 | `core/recovery_manager.py` | Reimporte les positions MT5 ouvertes dans le Blackboard au redemarrage. | ACTIF |
| Gap detection cold start | `core/recovery_manager.py` | Ferme en urgence une position recuperee si le prix courant a depasse le SL. | ACTIF |
| Decision log | `utils/decision_logger.py` | Ecrit decisions et opportunites manquees en JSONL avec rotation. | ACTIF |
| Missed opportunities | `utils/decision_logger.py`, `core/diamond_detector.py` | Journalise les signaux non executes et setups diamond. | ACTIF |
| Memoire SQLite | `data/memory_db.py` | Cree `data/memory.db` et tables patterns/performance/erreurs/strategies. | ACTIF |
| Pause apres 5 pertes cumulees | `data/memory_db.py` | Suspend les nouveaux trades, analyse les patterns et notifie Telegram. | PARTIEL |
| Performance agents | `data/memory_db.py` | Enregistre la precision individuelle apres chaque trade cloture. | ACTIF |
| Adaptive weights | `execution/adaptive_weights.py`, `core/engine.py`, `core/orchestrator.py` | Recalcule les poids apres trade cloture, les publie et l'orchestrateur les applique en live. | ACTIF |
| Rapports automatiques | `utils/report_scheduler.py` | Planifie rapports journalier, hebdomadaire et mensuel. | ACTIF |
| Google Drive sync | `utils/drive_sync.py` | Synchronise DB, logs, backtests et rapports vers Drive a 23h UTC+1. | PARTIEL |
| Telegram notifier | `utils/telegram_notifier.py` | Envoie boot, signaux, trades, news, risk alerts et rapports. | PARTIEL |
| Telegram telecommande | `utils/telegram_commander.py` | Gere `/status`, `/pause`, `/resume`, `/restart`, `/risk`, etc. | PARTIEL |
| Dashboard aiohttp | `web/dashboard_server.py` | Expose `/api/state`, `/api/trades`, `/api/agents` et `/ws`. | ACTIF |
| Dashboard HTML | `web/dashboard.html` | Interface sombre avec agents animes, score, positions et logs. | ACTIF |
| Cloudflare tunnel | `web/dashboard_server.py` | Lance cloudflared et capture une URL `trycloudflare.com`. | PARTIEL |
| Backtesting | `backtesting/backtest_engine.py` | Rejoue XAUUSD M1, alimente agents/orchestrateur et logge les resultats. | ACTIF |
| Calibration poids | `utils/weight_calibrator.py` | Calcule des poids depuis le decision log si assez de trades clotures. | PARTIEL |
| Historique 6 mois | `data/historical_loader.py` | Precharge M1/M5/M15/H1/H4 en parquet avec cache incremental. | ACTIF |
| Warmup agents | `main.py`, `data/historical_loader.py` | Injecte les donnees historiques de demarrage dans le Blackboard. | ACTIF |
| Diamond setup | `core/diamond_detector.py` | Detecte le setup 5 etoiles et alerte sans trader automatiquement. | ACTIF |
| Spread monitor | `utils/spread_monitor.py` | Bloque les entrees sur spread anormal et alerte apres 5 minutes. | ACTIF |
| MT5 watchdog interne | `utils/mt5_watchdog.py` | Surveille la connexion MT5 et tente la reconnexion. | ACTIF |
| Watchdog externe | `watchdog.py` | Surveille le heartbeat du moteur et redemarre `main.py` si bloque. | ACTIF |
| Heartbeat moteur | `main.py` | Ecrit un fichier heartbeat pour le watchdog externe. | ACTIF |
| Autostart Windows | `scripts/setup_autostart.ps1` | Cree une tache planifiee Windows pour lancer `GoldSniper.bat`. | PARTIEL |
| MT5 minimise/cache | `GoldSniper.bat`, `scripts/start_mt5_minimized.ps1` | Lance MT5 minimise ou cache via PowerShell et Win32 ShowWindow. | ACTIF |
| Calendrier ForexFactory | `scrapers/economic_calendar.py` | Fallback XML ForexFactory avec cache local. | ACTIF |
| Calendrier Finnhub | `agents/agent_6_sentinelle.py` | Source prioritaire si `FINNHUB_TOKEN` est configure. | PARTIEL |
| FMP token | `config.py` | Token FMP configure avec limite 200 req/jour pour macro/fondamental uniquement. | ACTIF |
| FMP integration | `agents/agent_6_sentinelle.py` | Source intermediaire dans la chaine Finnhub -> FMP -> ForexFactory. | ACTIF |
| Rate limiter MT5 global | `config.py`, `core/blackboard.py`, `core/tick_ingestion.py` | Limite appliquee au tick ingestion, pas a tous les appels MT5. | PARTIEL |
| DST sessions | `config.py`, `agents/agent_7_chronos.py` | Sessions calculees depuis `TZ_LOCAL=Europe/Paris` avec bascule ete/hiver automatique. | ACTIF |
| Friday mode notifications | `agents/agent_7_chronos.py` | Envoie une notification Telegram lors du passage risque reduit puis trading coupe. | ACTIF |
| UI CustomTkinter | `ui/` | Ancienne UI locale encore presente mais non lancee par `main.py`. | INACTIF |
| Ancien `core/agent_result.py` | `core/__pycache__/agent_result*.pyc` | Source supprimee, reste seulement un artefact pyc. | INACTIF |

Fonctionnalites partielles ou inactives a traiter avant lancement reel:

- Telegram et Drive dependent encore de variables d'environnement ou de l'OAuth
  Drive: sans elles, les notifications/sync restent en mode code/fallback.
- Le compte MT5 connecte pendant l'audit n'est pas le JustMarkets-Demo3 attendu.
- Le rate limiter MT5 n'est pas un wrapper central autour de tous les appels MT5.
- L'ancienne UI reste dans le repo et doit etre supprimee ou archivee si le
  dashboard web devient l'unique interface.

## Vue d'ensemble

```text
MT5
 |
 v
core.mt5_bridge
 |
 +--> tick_ingestion + candle_builder
 |
 v
Blackboard
 |
 +--> Risk Manager          veto absolu
 +--> Agent 6 Sentinelle    news / Finnhub / stealth mode
 +--> Agent 7 Chronos       sessions / kill zones
 +--> Macro Monitor         DXY + US10Y
 +--> Regime Detector       regime marche
 |
 +--> Agent 1 Meteo         structure HTF
 +--> Agent 2 Cartographe   OB/FVG scoring
 +--> Agent 3 Liquidite     sweep vs break
 +--> Agent 4 Fibonacci     premium/discount/OTE
 +--> Agent 5 Microscope    AMD: sweep puis CHoCH
 |
 v
core.orchestrator
 |
 v
execution.trade_manager
 |
 v
Decision Log + Telegram + Recovery
```

## Demarrage moteur

Le point d'entree est `main.py`.

1. Connexion MT5 via `core.mt5_bridge.bridge.connect()`.
2. Recovery des positions MT5 ouvertes.
3. Chargement historique 15m et 4H.
4. Initialisation des stats journalieres.
5. Message Telegram de demarrage.
6. Lancement du moteur asynchrone `core.engine.run_engine()`.

Dans `core.engine`, l'ordre de lancement est:

1. `tick_ingestion`
2. `candle_builder`
3. `risk_manager`
4. `agent_6_senti`
5. `agent_7_sess`
6. `macro_monitor`
7. `regime_detector`
8. `agent_1_meteo`
9. `agent_2_carto`
10. `agent_3_liqui`
11. `agent_4_fibo`
12. `agent_5_micro`
13. `orchestrator`
14. `trade_manager`
15. services: account info, MT5 watchdog, recovery persistence, Telegram sender

## Source de verite AgentResult

La seule definition valide est `agents/base_agent.py`.

Tous les agents retournent un `AgentResult` avec:

- `agent_id`
- `score`
- `hard_filter_pass`
- `direction`
- `reason`
- `payload`
- `veto`
- `risk_modifier`

`core/agent_result.py` a ete supprime pour eviter deux formats concurrents.

## Blackboard

`core.blackboard.BlackBoard` est l'etat partage central.

Il contient notamment:

- `meta`: etat systeme, mode, kill switch, connexion MT5.
- `market_data`: ticks, bougies, infos symbole.
- `market`: regime, session, macro bias, spread monitor.
- `agents`: dernier etat lisible de chaque agent.
- `agent_results`: derniers `AgentResult` normalises.
- `orchestrator`: decision courante.
- `trade_signals`: signal executable.
- `active_trades`: trades suivis par le Trade Manager.
- `notifications`: configuration et queue Telegram.

Les ecritures passent par `write`, `update_dict` ou `write_agent_result`.

## Agents

### Agent 1 - Meteo

Analyse la structure HTF et le biais directionnel. C'est un hard filter.

### Agent 2 - Cartographe

Score les Order Blocks sur 5 facteurs independants:

- fraicheur de la zone;
- force de l'impulsion;
- alignement HTF;
- presence FVG;
- confluence liquidite.

Un OB faible est rejete meme s'il est techniquement valide.

### Agent 3 - Liquidite

Differencie un sweep d'un break:

- sweep: prix traverse brievement un niveau puis reintegre, score positif;
- break: prix casse et reste de l'autre cote, signal annule avec `hard_filter_pass=False`.

Il detecte aussi Asian Range et IDM.

### Agent 4 - Fibonacci

Calcule:

- equilibre 50%;
- zone OTE 61.8% a 78.6%;
- sweet spot 70.5%.

Regles absolues:

- LONG en premium interdit;
- SHORT en discount interdit.

### Agent 5 - Microscope

Valide la sequence AMD complete:

1. Accumulation.
2. Manipulation: sweep 1M confirme.
3. Distribution: CHoCH valide.

Un CHoCH sans sweep prealable est rejete.

### Agent 6 - Sentinelle

Lit le calendrier economique via Finnhub quand disponible.

Regles critiques:

- veto absolu 15 minutes avant/apres une news high impact;
- stealth mode pendant 1h apres l'evenement;
- fallback si Finnhub est indisponible;
- publication des alertes dans le Blackboard et Telegram.

### Agent 7 - Chronos

Detecte:

- TOKYO;
- LONDON;
- NEW_YORK;
- OVERLAP London-NY.

TOKYO seul bloque les setups sous 92/100. OVERLAP booste le score via
`risk_modifier`.

## Orchestrateur V2

`core.orchestrator` agregue les agents et applique les veto.

Ordre de decision:

1. Veto absolu Risk Manager ou Agent 6.
2. Hard filters Agent 1 et Agent 2.
3. Conflit directionnel Agent 1 vs Agent 3.
4. Score pondere par regime de marche.
5. Decroissance temporelle du signal.
6. Ajustement session Chronos.
7. Decision finale.

Seuils:

- `EXECUTION_THRESHOLD = 85`
- `WATCH_THRESHOLD = 70`
- `EXCEPTIONAL_THRESHOLD = 92`

Le Risk Manager garde un veto absolu meme si le score global est 95/100.

## Risk Manager

Parametres critiques dans `config.py`:

- `RISK_PCT_PER_TRADE = 1.0`
- `DAILY_LOSS_LIMIT = 3.0`
- `DRAWDOWN_LIMIT = 5.0`
- pause 2h apres 3 pertes consecutives.

Le sizing est calcule dynamiquement avec ATR: plus l'ATR est eleve, plus le
lot est reduit.

## Execution

`execution.trade_manager.TradeManager` consomme `trade_signals` et verifie:

- kill switch;
- mode paper/live;
- spread monitor;
- sizing ATR;
- niveaux entry/SL/TP;
- gestion active du trade.

## Spread Monitor

`utils.spread_monitor.SpreadMonitor` lit le spread MT5 avant entree.

- spread normal: trade autorise;
- spread eleve: trade bloque et logge dans le Decision Log;
- rollover/news: anomalie detectee;
- spread eleve plus de 5 minutes: alerte Telegram.

## MT5 Watchdog

`utils.mt5_watchdog.MT5Watchdog` surveille la connexion MT5.

Si MT5 se deconnecte:

1. `bridge.connected` passe a `False`;
2. les nouvelles entrees sont bloquees par veto Risk Manager;
3. le watchdog tente une reconnexion;
4. Telegram alerte si la deconnexion persiste;
5. au retour MT5, le veto de deconnexion est leve.

## Decision Log

`utils.decision_logger` ecrit:

- `logs/decision_log.jsonl`
- `logs/missed_opportunities.jsonl`

Les fichiers JSONL tournent par taille avec:

- `LOG_MAX_BYTES`
- `LOG_BACKUP_COUNT`

Chaque cycle garde la decision, le score, la direction, le regime et le
detail des agents.

## Backtesting

`backtesting/backtest_engine.py` telecharge XAUUSD M1 depuis MT5 au premier
lancement et cree:

```text
logs/backtests/XAUUSD_M1_cache.parquet
```

Si le cache a moins de 24h, il est recharge sans retelechargement complet.
Le moteur rejoue les bougies chronologiquement, alimente les 7 agents, passe
par l'orchestrateur et ecrit:

```text
logs/backtests/backtest_results.jsonl
```

## Auto-calibration

`utils/weight_calibrator.py` lit `logs/decision_log.jsonl`.

La calibration refuse de tourner avec moins de 50 trades clotures. Si les
donnees sont suffisantes, elle calcule la precision individuelle des agents,
ajuste les poids de l'orchestrateur et logge dans:

```text
logs/calibration_log.jsonl
```

## Telegram

`utils.telegram_notifier` gere:

- demarrage systeme;
- signaux;
- ouverture/fermeture trades;
- news;
- risk alerts;
- spread alerts;
- rapport de fin de jour.

Les identifiants ne sont pas stockes dans le code. `config.py` lit:

- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

## Fichiers principaux

```text
gold_sniper/
  main.py
  config.py
  README.md
  architecture.md
  agents/
  backtesting/
  core/
  execution/
  ui/
  utils/
  logs/
```

## Configuration sensible

Les secrets doivent venir de l'environnement et ne pas etre commites:

- `MT5_ACCOUNT`
- `MT5_PASSWORD`
- `MT5_SERVER`
- `MT5_PATH` optionnel
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

## Commandes utiles

Demarrer:

```powershell
python main.py
```

Backtest rapide:

```powershell
python backtesting\backtest_engine.py --limit 100
```

Calibration dry-run:

```powershell
python utils\weight_calibrator.py --dry-run
```

Validation syntaxe:

```powershell
python -m py_compile main.py core\engine.py core\orchestrator.py
```

# Gold Sniper V2 - Architecture

Ce document decrit l'architecture actuellement publiee du projet
`Joemi930/gold-sniper`. Le code principal est dans `gold_sniper/`.

## Vue systeme

```text
MT5
 |
 v
core.mt5_bridge
 |
 +--> tick_ingestion
 +--> candle_builder
 |
 v
Blackboard
 |
 +--> risk_manager         veto absolu
 +--> agent_6_sentinelle   news / Finnhub / fallback
 +--> agent_7_chronos      sessions / kill zones
 +--> macro_monitor        DXY / US10Y
 +--> regime_detector      regime marche
 |
 +--> agent_1_meteo        structure HTF
 +--> agent_2_cartographe  OB/FVG scoring
 +--> agent_3_liquidite    sweep vs break
 +--> agent_4_fibonacci    premium/discount/OTE
 +--> agent_5_microscope   AMD: sweep puis CHoCH
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

## Demarrage

`gold_sniper/main.py` execute:

1. connexion MT5;
2. recovery positions ouvertes;
3. chargement bougies 15m et 4H;
4. initialisation stats journalieres;
5. notification Telegram de demarrage;
6. lancement du moteur asynchrone.

`core.engine.run_engine()` lance dans cet ordre:

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

## Blackboard

`core.blackboard.BlackBoard` est l'etat partage central. Il contient:

- `meta`
- `market_data`
- `market`
- `agents`
- `agent_results`
- `orchestrator`
- `trade_signals`
- `active_trades`
- `notifications`
- `recovery`

Les agents ecrivent leurs resultats normalises dans `agent_results`.

## AgentResult

Source de verite unique:

```text
gold_sniper/agents/base_agent.py
```

`core/agent_result.py` a ete supprime. Le champ valide pour les hard filters
est `hard_filter_pass`.

## Agents

### Agent 1 - Meteo

Analyse structure HTF et biais directionnel. Hard filter principal.

### Agent 2 - Cartographe

Scoring des Order Blocks sur 5 facteurs:

- fraicheur;
- force impulsion;
- alignement HTF;
- FVG dans la zone;
- confluence liquidite.

Un OB faible est rejete meme s'il est techniquement valide.

### Agent 3 - Liquidite

Differencie:

- sweep: signal positif;
- break: invalidation avec `hard_filter_pass=False`.

Detecte aussi Asian Range et IDM.

### Agent 4 - Fibonacci

Calcule:

- equilibre 50%;
- OTE 61.8% a 78.6%;
- sweet spot 70.5%.

LONG en premium interdit. SHORT en discount interdit.

### Agent 5 - Microscope

Valide AMD complet:

1. Accumulation.
2. Manipulation: sweep 1M confirme.
3. Distribution: CHoCH valide.

Un CHoCH sans sweep prealable est rejete.

### Agent 6 - Sentinelle

Utilise Finnhub quand disponible. Regles:

- veto HIGH IMPACT 15 min avant/apres;
- stealth mode 1h apres;
- fallback si Finnhub tombe;
- alertes Blackboard et Telegram.

### Agent 7 - Chronos

Detecte TOKYO, LONDON, NEW_YORK et OVERLAP London-NY.

TOKYO seul bloque sous 92/100. OVERLAP booste le score.

## Orchestrateur V2

Fichier:

```text
gold_sniper/core/orchestrator.py
```

Ordre de decision:

1. veto Risk Manager ou Agent 6;
2. hard filters Agent 1 et Agent 2;
3. conflit directionnel Agent 1 vs Agent 3;
4. score pondere avec regime de marche;
5. decroissance temporelle;
6. ajustement session Chronos;
7. decision finale.

Seuils:

- EXECUTE: 85;
- WATCH: 70;
- EXCEPTIONAL: 92.

## Risque

Parametres dans `gold_sniper/config.py`:

- `RISK_PCT_PER_TRADE = 1.0`;
- `DAILY_LOSS_LIMIT = 3.0`;
- `DRAWDOWN_LIMIT = 5.0`;
- pause 2h apres 3 pertes consecutives.

Le Risk Manager a un veto absolu, meme si le score global est 95/100.

## Execution

`execution.trade_manager.TradeManager` verifie avant entree:

- kill switch;
- mode paper/live;
- spread monitor;
- sizing ATR;
- niveaux entry, SL, TP;
- etat MT5.

## Spread Monitor

`utils.spread_monitor.SpreadMonitor` bloque les entrees si le spread depasse
le seuil configure. Les blocages sont logges dans le Decision Log. Une alerte
Telegram est envoyee si le spread reste eleve plus de 5 minutes.

## MT5 Watchdog

`utils.mt5_watchdog.MT5Watchdog` surveille la connexion MT5.

Si MT5 tombe:

1. `bridge.connected=False`;
2. nouvelles entrees bloquees par veto Risk Manager;
3. tentatives de reconnexion;
4. Telegram alerte si la coupure persiste;
5. veto leve apres reconnexion.

## Logs

`utils.decision_logger` ecrit:

- `logs/decision_log.jsonl`;
- `logs/missed_opportunities.jsonl`.

La rotation utilise `LOG_MAX_BYTES` et `LOG_BACKUP_COUNT`.

## Backtesting

`backtesting/backtest_engine.py` telecharge XAUUSD M1 depuis MT5 au premier
lancement puis cree:

```text
logs/backtests/XAUUSD_M1_cache.parquet
```

Les decisions simulees sont ecrites dans:

```text
logs/backtests/backtest_results.jsonl
```

## Auto-calibration

`utils/weight_calibrator.py` lit le Decision Log et refuse de tourner avec
moins de 50 trades clotures. Les recalibrations sont loggees dans:

```text
logs/calibration_log.jsonl
```

## Configuration sensible

Les secrets doivent venir de l'environnement:

- `MT5_ACCOUNT`
- `MT5_PASSWORD`
- `MT5_SERVER`
- `MT5_PATH` optionnel
- `TELEGRAM_TOKEN`
- `TELEGRAM_CHAT_ID`

Ils ne doivent pas etre commits.

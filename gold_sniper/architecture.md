# Gold Sniper V2 - Architecture

Gold Sniper V2 est un moteur de trading XAUUSD base sur Python, asyncio,
MetaTrader 5 et une architecture multi-agents. Le systeme est concu pour
avancer par filtres successifs: collecte de donnees, analyse par agents,
veto risque/news, scoring orchestre, puis execution controlee.

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

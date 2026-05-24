# Gold Sniper V2

Gold Sniper V2 est un moteur de trading XAUUSD construit autour de 7 agents,
d'un Blackboard partage, d'un Orchestrateur V2 et de protections operationnelles
avant toute execution MT5.

Le code actif du projet se trouve dans:

```text
gold_sniper/
```

## Etat de la version publiee

Cette version inclut les scripts valides progressivement:

- AgentResult unifie dans `agents/base_agent.py`.
- Orchestrateur V2 avec score pondere et seuil `EXECUTION_THRESHOLD = 85`.
- Decision Log JSONL avec rotation.
- Telegram notifier.
- Risk Manager avec veto absolu.
- Agent 5 AMD complet: Accumulation, sweep 1M, CHoCH.
- Agent 2 OB scoring 5 facteurs.
- Agent 3 sweep vs break, Asian Range, IDM.
- Agent 4 premium/discount et OTE.
- Agent 6 calendrier economique Finnhub avec fallback.
- Macro Monitor DXY / US10Y via yfinance avec fallback.
- Regime Detector.
- Agent 7 Chronos sessions et kill zones.
- Position sizing dynamique ATR.
- Spread Monitor.
- MT5 Watchdog.
- Backtesting engine avec cache parquet.
- Auto-calibration des poids agents apres 50 trades clotures minimum.

## Demarrage rapide

Depuis la racine du repo:

```powershell
cd gold_sniper
python main.py
```

MetaTrader 5 doit etre ouvert et connecte. `XAUUSD` doit etre visible dans le
Market Watch.

## Configuration sensible

Les secrets ne sont pas stockes dans le code. Definir les variables
d'environnement avant lancement si necessaire:

```powershell
$env:MT5_ACCOUNT="ton_login"
$env:MT5_PASSWORD="ton_mot_de_passe"
$env:MT5_SERVER="ton_serveur"
$env:TELEGRAM_TOKEN="ton_token"
$env:TELEGRAM_CHAT_ID="ton_chat_id"
```

`LIVE_MODE` reste defini dans `gold_sniper/config.py`. Par defaut, le projet
reste en paper trading.

## Ordre de lancement moteur

`main.py` execute le cold start:

1. Connexion MT5.
2. Recovery des positions ouvertes.
3. Chargement historique.
4. Initialisation du Blackboard.
5. Message Telegram de demarrage.
6. Lancement de `core.engine.run_engine()`.

Le moteur lance ensuite:

1. data ingestion;
2. Risk Manager et agents contextuels;
3. agents de signal;
4. orchestrateur;
5. trade manager;
6. services: account fetcher, MT5 Watchdog, recovery, Telegram sender.

## Logs

Fichiers principaux:

- `gold_sniper/logs/decision_log.jsonl`
- `gold_sniper/logs/missed_opportunities.jsonl`
- `gold_sniper/logs/calibration_log.jsonl`
- `gold_sniper/logs/backtests/backtest_results.jsonl`
- `gold_sniper/logs/gold_sniper_YYYY-MM-DD.jsonl`

Les JSONL applicatifs tournent par taille avec `LOG_MAX_BYTES` et
`LOG_BACKUP_COUNT`.

## Backtest

Premier lancement avec MT5 ouvert:

```powershell
cd gold_sniper
python backtesting\backtest_engine.py --limit 100
```

Le cache M1 est cree ici:

```text
gold_sniper/logs/backtests/XAUUSD_M1_cache.parquet
```

## Calibration

La calibration refuse de tourner avec moins de 50 trades clotures:

```powershell
cd gold_sniper
python utils\weight_calibrator.py --dry-run
```

## Documentation

Voir aussi:

- `architecture.md`
- `gold_sniper/architecture.md`

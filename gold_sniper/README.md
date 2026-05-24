# Gold Sniper V2 - Guide de demarrage rapide

## Prerequis

- Ouvrir MetaTrader 5 et connecter le compte configure dans `config.py`.
- Verifier que `XAUUSD` est visible dans le Market Watch MT5.
- Verifier `LIVE_MODE` dans `config.py` avant tout lancement. Par defaut le projet reste en paper trading.
- Definir `MT5_ACCOUNT`, `MT5_PASSWORD` et `MT5_SERVER` dans l'environnement si le bot doit se connecter automatiquement au compte.
- Definir `TELEGRAM_TOKEN` et `TELEGRAM_CHAT_ID` dans l'environnement si tu veux recevoir les alertes telephone.

Exemple PowerShell pour la session courante:

```powershell
$env:MT5_ACCOUNT="ton_login"
$env:MT5_PASSWORD="ton_mot_de_passe"
$env:MT5_SERVER="ton_serveur"
$env:TELEGRAM_TOKEN="ton_token"
$env:TELEGRAM_CHAT_ID="ton_chat_id"
```

## Demarrage live/paper

Depuis ce dossier:

```powershell
python main.py
```

Ordre de boot:

1. Connexion MT5.
2. Recovery des positions ouvertes.
3. Chargement des bougies historiques 15m et 4H.
4. Initialisation du Blackboard.
5. Message Telegram de demarrage.
6. Lancement du moteur asynchrone.

Dans le moteur, les flux data demarrent d'abord, puis les gates critiques
`risk_manager`, `agent_6_senti`, `agent_7_sess`, `macro_monitor`,
`regime_detector`, avant les agents de signal `agent_1` a `agent_5`.

## Securites operationnelles

- Risk Manager: veto absolu si drawdown, limite journaliere ou pause pertes consecutives.
- Agent 6 Sentinelle: veto news high impact, stealth mode apres news, fallback si Finnhub tombe.
- Spread Monitor: bloque les entrees si le spread depasse `MAX_SPREAD_POINTS`.
- MT5 Watchdog: detecte une deconnexion MT5, bloque les nouvelles entrees, tente une reconnexion et alerte Telegram.
- Recovery Manager: reinjecte les positions MT5 ouvertes au redemarrage.

## Logs importants

- `logs/decision_log.jsonl`: journal des decisions et blocages execution.
- `logs/missed_opportunities.jsonl`: setups forts refuses.
- `logs/calibration_log.jsonl`: historique des recalibrations.
- `logs/backtests/backtest_results.jsonl`: decisions simulees en backtest.
- `logs/gold_sniper_YYYY-MM-DD.jsonl`: logs techniques avec rotation.

Les logs applicatifs JSONL critiques tournent par taille selon `LOG_MAX_BYTES`
et conservent `LOG_BACKUP_COUNT` backups.

## Backtest Script 18

MT5 doit etre ouvert et connecte au premier lancement.

```powershell
python backtesting\backtest_engine.py --limit 100
```

Le moteur telecharge les bougies M1 depuis MT5 et cree:

```text
logs/backtests/XAUUSD_M1_cache.parquet
```

Au lancement suivant, il recharge le cache si celui-ci a moins de 24h.

## Calibration Script 17

La calibration refuse de tourner avec moins de 50 trades clotures dans
`logs/decision_log.jsonl`.

```powershell
python utils\weight_calibrator.py
```

Pour tester sans appliquer:

```powershell
python utils\weight_calibrator.py --dry-run
```

## Arret

Utiliser le Kill Switch de l'interface ou `Ctrl+C`. Le systeme envoie le
rapport de fin de jour Telegram avant l'arret quand c'est possible.

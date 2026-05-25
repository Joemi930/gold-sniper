# Gold Sniper V3.0

Gold Sniper V3.0 est un moteur de trading XAUUSD en Python, asyncio et MetaTrader 5.
Il combine un Blackboard temps reel, 7 agents specialises, un orchestrateur event-driven,
un Risk Manager, une memoire SQLite, Telegram, un dashboard web et un tunnel Cloudflare.

## Points forts

- Orchestrateur event-driven avec latence publication agent -> decision mesuree sous 1 ms en test local.
- Dictionnaire de strategies actif par session, regime, news et setups exceptionnels.
- Execution MT5 avec entree, SL et TP broker dans la meme requete `order_send`.
- Gestion active: TP1 reel, fermeture partielle 50%, breakeven, puis trailing stop.
- Recovery au cold start avec detection de gap au-dela du SL et fermeture d'urgence.
- Agent 6 Sentinelle: chaine calendrier `Finnhub -> FMP -> ForexFactory`.
- Agent 7 Chronos: sessions calculees avec DST Europe/Paris et Friday Mode Telegram.
- Memoire long terme SQLite: patterns de trades, performance agents, erreurs, strategies.
- Adaptive weights recalcules apres trade cloture et appliques en live par l'orchestrateur.
- Dashboard `aiohttp` avec endpoints JSON et WebSocket, expose via Cloudflare Tunnel.
- Google Drive sync planifiee pour sauvegarder DB, decision log, backtests et rapports.

## Installation

```powershell
python -m pip install -r requirements.txt
python -m pip install python-dotenv
```

Si `requirements.txt` n'est pas encore present dans votre copie locale, installez au minimum
les dependances utilisees par le moteur:

```powershell
python -m pip install MetaTrader5 aiohttp pandas pyarrow yfinance schedule google-api-python-client google-auth-oauthlib python-dotenv requests
```

## Configuration

Creer un fichier `.env` a la racine du projet. Ce fichier est ignore par git.

```env
MT5_ACCOUNT=
MT5_PASSWORD=
MT5_SERVER=JustMarkets-Demo3
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
FINNHUB_TOKEN=
FMP_TOKEN=
```

`config.py` appelle `load_dotenv()` au demarrage, donc les variables sont chargees
automatiquement sans export manuel dans le terminal.

## Demarrage

```powershell
python main.py
```

Sur Windows, `GoldSniper.bat` lance MT5 en mode minimise/cache puis demarre le moteur.
Le script `scripts/setup_autostart.ps1` peut creer une tache planifiee pour le demarrage
automatique de session.

## Dashboard

Le dashboard demarre sur:

```text
http://localhost:8765
```

Endpoints:

- `/api/state`
- `/api/trades`
- `/api/agents`
- `/ws`

Si `cloudflared` est disponible, un lien public `trycloudflare.com` est genere et envoye
sur Telegram au boot.

## Structure

```text
agents/       Agents 1 a 7, macro monitor, regime detector, risk manager
core/         Blackboard, engine, orchestrateur, MT5 bridge, recovery
execution/    Trade manager, risk calculator, adaptive weights
data/         Memoire SQLite et loader historique
utils/        Telegram, reports, Drive sync, watchdogs, logs
web/          Dashboard aiohttp + HTML
scripts/      Autostart et lancement MT5 minimise/cache
backtesting/  Moteur de backtest
```

## Securite

Ne jamais commiter:

- `.env`
- `credentials.json`
- `data/drive_token.json`
- `data/memory.db`
- logs, caches, fichiers parquet historiques

Le mode live reste controle par `LIVE_MODE` dans `config.py`. Verifiez toujours le compte
MT5, le serveur et le symbole avant tout lancement en reel.

## Validation rapide

```powershell
python -m py_compile main.py core\engine.py core\orchestrator.py execution\trade_manager.py
python -c "import config; print(config.MT5_SERVER)"
```


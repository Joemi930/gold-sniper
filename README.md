# Gold Sniper — P1-clean

⚠️ Statut actuel : P1-clean / offline / replay-only / shadow-only.

Pendant P1-clean :

- ne pas lancer le live ;
- ne pas lancer le paper trading ;
- ne pas activer `LIVE_MODE=1` ;
- ne pas connecter le chemin replay à l'orchestrateur live ;
- ne pas exécuter d'ordre broker ;
- ne pas lancer de replay long sans validation Architecte.

Les sections legacy présentes plus bas sont conservées uniquement comme archive historique. Elles ne constituent pas des consignes actives pendant P1-clean.

---

# Gold Sniper - Clean Repo Restart

Gold Sniper est un moteur de recherche et simulation de setups XAUUSD en Python, asyncio et MetaTrader 5.

Le depot a ete redemarre proprement avant la reforme strategique Opus Phase 0 -> Phase 9. L'objectif n'est plus d'empiler plusieurs strategies concurrentes, mais de preparer une seule future strategie centrale XAUUSD.

Etat actuel :

- Phase 0 : Legacy Strategy Freeze.
- No live trading.
- No demo deployment.
- Les anciennes strategies sont gelees comme briques d'analyse, pas comme autorites autonomes.
- L'execution MT5 reste dans le code, mais aucune activation live n'est autorisee sans validation statistique future.

Depot officiel : [Joemi930/gold-sniper](https://github.com/Joemi930/gold-sniper)

## Etat de reforme

- **Clean repo restart** : nouvel historique Git, commit initial unique.
- **Phase 0** : gel de l'ancienne architecture multi-strategies.
- **Doctrine cible** : une seule strategie centrale XAUUSD.
- **Anciennes strategies** : conservees comme indicateurs, vetoes, scores, etats POI, gates ou champs explicatifs.
- **Pas de live** : l'execution broker reste protegee et ne doit pas etre activee avant preuve statistique et validation explicite.

## Documentation

- [architecture.md](https://github.com/Joemi930/gold-sniper/blob/main/architecture.md)
  - cartographie technique actuelle.
- [docs/legacy_strategy_freeze.md](docs/legacy_strategy_freeze.md)
  - gel Phase 0 des anciennes strategies autonomes.

## Points forts

- Architecture Blackboard : les agents publient dans un etat central partage.
- Orchestrateur event-driven : decision reveillee par les evenements agents et bougies.
- Connexion MT5 JustMarkets Demo verifiee : login, serveur, symbole, tick et autorisation
  trading.
- Execution MT5 encapsulee dans `gold_sniper/execution/broker_gateway.py`.
  Elle est conservee mais ne doit pas etre activee sans validation future.
- Risk Manager : drawdown, pertes consecutives, pause, veto et protection equity.
- Recovery au cold start : reprise des positions MT5 ouvertes et snapshots persistants.
- Discord : `!start`, `!kill`, `!restart`, `!status`, `!agents`, `!pause`, `!risk`.
- Dashboard WebSocket : flux temps reel, latence affichee, visual layers dynamiques.
- Cloudflare Tunnel : URL publique envoyee au demarrage quand `cloudflared` est disponible.

## Installation

```powershell
cd gold_sniper
powershell -ExecutionPolicy Bypass -File scripts\install_deps.ps1
```

Ou manuellement :

```powershell
python -m pip install -r requirements.txt
```

Dependances principales : `MetaTrader5`, `aiohttp`, `discord.py`, `truststore`,
`psutil`, `pandas`, `pyarrow`.

## Configuration

Creer un fichier `.env` dans `gold_sniper/.env`. Ce fichier est ignore par git.

> Archive legacy uniquement — ne pas utiliser pendant P1-clean.

```env
MT5_ACCOUNT=
MT5_PASSWORD=
MT5_SERVER=JustMarkets-Demo3
MT5_SYMBOL=XAUUSD.m
LIVE_MODE=1

DISCORD_TOKEN=
DISCORD_GUILD_ID=
DISCORD_USER_ID=
DISCORD_ALERTS_CHANNEL_ID=
DISCORD_COMMANDS_CHANNEL_ID=
DISCORD_REPORTS_CHANNEL_ID=
DISCORD_LOGS_CHANNEL_ID=

FINNHUB_TOKEN=
FMP_TOKEN=
CLOUDFLARED_PATH=
```

Important : ne pas activer `LIVE_MODE=1` pendant la reforme Phase 0 -> Phase 9.
Le mode live/demo n'est pas valide tant que l'edge statistique et le pipeline
unifie n'ont pas ete prouves et valides explicitement.

## Demarrage

### Pilotage quotidien recommande

1. Lancer `LancerManager.bat` ou l'autostart Windows.
2. Sur Discord, utiliser `!start`.
3. Ouvrir le dashboard local ou l'URL Cloudflare fournie.
4. Arreter avec `!kill` avant toute maintenance.

### Debug local

```powershell
python main.py
```

Dashboard local :

```text
http://localhost:8765
```

Endpoints :

- `/api/state`
- `/api/trades`
- `/api/agents`
- `/ws`

## Structure

```text
gold_sniper/
├── agents/        Agents 1 a 7, macro monitor, regime detector, risk manager
├── core/          Blackboard, visual layers, engine, orchestrateur, MT5 bridge
├── execution/     Trade manager, risk calculator, adaptive weights
├── data/          Memoire SQLite, historical loader, inbox Discord
├── utils/         Discord, watchdogs, cloudflared, reports, Drive sync, logs
├── web/           Dashboard aiohttp + HTML V3.2
├── scripts/       Autostart, stop_all, install deps, MT5 bootstrap
├── tests/         Tests unitaires
├── pc_manager.py  Gateway Discord + lifecycle
├── watchdog.py    Surveillance et restart
└── main.py        Moteur de trading
```

## Validation rapide

```powershell
python -m py_compile config.py core\blackboard.py execution\trade_manager.py web\dashboard_server.py
python -m unittest discover tests
```

Validation V3.2 observee :

```text
Ran 10 tests
OK
```

## Securite

Ne jamais commiter :

- `.env`
- tokens Discord/GitHub/API
- `credentials.json`
- `data/drive_token.json`
- `data/memory.db`
- logs, caches, fichiers parquet historiques
- fichiers `.lock`, `data/discord_inbox.jsonl`, `data/bot_ready.json`

Verifier avant chaque session :

- compte MT5 connecte au bon serveur JustMarkets Demo ;
- `LIVE_MODE` voulu ;
- une seule instance `main.py` ;
- dashboard ONLINE ;
- aucun veto Risk Manager / Agent 6.

# Gold Sniper V3.1

Gold Sniper V3.1 est un moteur de trading XAUUSD en Python, asyncio et MetaTrader 5.
Il combine un Blackboard temps réel, 7 agents spécialisés, un orchestrateur event-driven,
un Risk Manager, une mémoire SQLite, **Discord** (pilotage et alertes), un dashboard web
et un tunnel Cloudflare.

## Nouveautés V3.1

- **Migration Telegram → Discord** : commandes `!status`, `!start`, embeds professionnels.
- **PC Manager** (`pc_manager.py`) : seul processus connecté au Gateway Discord.
- **Lifecycle** : `!start`, `!kill`, `!restart`, `!pc_status` depuis `#gold-sniper-commands`.
- **Anti-doublons** : une instance manager, autostart Windows unique, script `stop_all`.
- **Cloudflare** : signal `data/bot_ready.json` ; correctif cleanup port 8765.
- **Python 3.14** : support SSL via `truststore` (`utils/ssl_bundle.py`).

Documentation détaillée :

- [architecture.md](architecture.md) — référence technique complète
- [MIGRATION_DISCORD_MACON.md](MIGRATION_DISCORD_MACON.md) — rapport de migration pour le conseiller

## Points forts

- Orchestrateur event-driven avec latence publication agent -> decision mesuree sous 1 ms en test local.
- Dictionnaire de strategies actif par session, regime, news et setups exceptionnels.
- Execution MT5 avec entree, SL et TP broker dans la meme requete `order_send`.
- Gestion active: TP1 reel, fermeture partielle 50%, breakeven, puis trailing stop.
- Recovery au cold start avec detection de gap au-dela du SL et fermeture d'urgence.
- Agent 6 Sentinelle: chaine calendrier `Finnhub -> FMP -> ForexFactory`.
- Agent 7 Chronos: sessions calculees avec DST Europe/Paris et Friday Mode.
- Memoire long terme SQLite: patterns de trades, performance agents, erreurs, strategies.
- Adaptive weights recalcules apres trade cloture et appliques en live par l'orchestrateur.
- Dashboard `aiohttp` avec endpoints JSON et WebSocket, expose via Cloudflare Tunnel.
- Google Drive sync planifiee pour sauvegarder DB, decision log, backtests et rapports.

## Installation

```powershell
cd gold_sniper
powershell -ExecutionPolicy Bypass -File scripts\install_deps.ps1
```

Ou manuellement :

```powershell
python -m pip install -r requirements.txt
```

Dependances cles : `MetaTrader5`, `aiohttp`, `discord.py`, `truststore`, `psutil`, `pandas`, `pyarrow`.

## Configuration

Creer un fichier `.env` a la racine du projet (`gold_sniper/.env`). Ce fichier est ignore par git.

```env
MT5_ACCOUNT=
MT5_PASSWORD=
MT5_SERVER=JustMarkets-Demo3

DISCORD_TOKEN=
DISCORD_GUILD_ID=
DISCORD_USER_ID=
DISCORD_ALERTS_CHANNEL_ID=
DISCORD_COMMANDS_CHANNEL_ID=
DISCORD_REPORTS_CHANNEL_ID=
DISCORD_LOGS_CHANNEL_ID=

FINNHUB_TOKEN=
FMP_TOKEN=

# Optionnel — interpréteur Python Windows (evite Python Store)
PYTHON_BIN=C:\Users\<vous>\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe
```

Sur le portail Discord Developer : activer **MESSAGE CONTENT INTENT** pour le bot.

`config.py` appelle `load_dotenv()` au demarrage.

## Demarrage

### Pilotage quotidien (recommandé)

1. Lancer le **PC Manager** une fois : `LancerManager.bat` ou autostart Windows.
2. Sur Discord `#gold-sniper-commands` : `!start` (attendre l'URL Cloudflare).
3. Arret : `!kill` — redemarrage : `!restart`.

### Moteur seul (debug)

```powershell
python main.py
```

Sur Windows, `GoldSniper.bat` lance `watchdog.py` (avec garde anti-doublon).

### Autostart Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_windows_autostart.ps1
```

Ou double-clic sur `Install_Autostart.bat`. Une seule tache planifiee `GoldSniper_PC_Manager`
— pas de raccourci Demarrage en double.

### Arret complet (depannage doublons)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all_gold_sniper.ps1
```

Puis relancer **une seule fois** le PC Manager (`LancerManager.bat`).

## Commandes Discord

| Type | Exemples |
|------|----------|
| Lifecycle (PC Manager) | `!start`, `!kill`, `!restart`, `!pc_status` |
| Operationnel (moteur actif) | `!status`, `!agents`, `!pause`, `!risk 0.5`, `!help` |

Alias FR : `!statut`, `!aide`, `!demarrer`, `!arreter`.

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

Si `cloudflared` est disponible, un lien public `trycloudflare.com` est genere et signale
via `data/bot_ready.json` puis envoye sur Discord au boot.

## Structure

```text
agents/       Agents 1 a 7, macro monitor, regime detector, risk manager
core/         Blackboard, engine, orchestrateur, MT5 bridge, recovery
execution/    Trade manager, risk calculator, adaptive weights
data/         Memoire SQLite, loader historique, inbox Discord
utils/        Discord, reports, Drive sync, watchdogs, cloudflared, logs
web/          Dashboard aiohttp + HTML
scripts/      Autostart, stop_all, install_deps
pc_manager.py Gateway Discord + lifecycle
watchdog.py   Surveillance et restart main.py
main.py       Moteur de trading
```

## Securite

Ne jamais commiter:

- `.env`
- `credentials.json`
- `data/drive_token.json`
- `data/memory.db`
- logs, caches, fichiers parquet historiques
- `data/discord_inbox.jsonl`, `data/bot_ready.json`, fichiers `*.lock`

Le mode live reste controle par `LIVE_MODE` dans `config.py`. Verifiez toujours le compte
MT5, le serveur et le symbole avant tout lancement en reel.

## Validation rapide

```powershell
python -m py_compile main.py pc_manager.py core\engine.py
python -c "import config; print('Discord enabled:', config.DISCORD_ENABLED)"
```

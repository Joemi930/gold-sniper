# Changelog

## [3.1.0] — 2026-05-29

### Added

- Migration complète **Telegram → Discord** (notifications embeds + commandes `!`).
- `pc_manager.py` : gateway Discord unique, lifecycle `!start` / `!kill` / `!restart`.
- `utils/discord_notifier.py`, `discord_commander.py`, `discord_commands.py`.
- Signal de boot `data/bot_ready.json` et attente URL Cloudflare.
- `utils/cloudflared_manager.py`, `single_instance.py`, `lifecycle_lock.py`.
- `utils/ssl_bundle.py` (Python 3.14 / Windows).
- Scripts `stop_all_gold_sniper.ps1`, `setup_windows_autostart.ps1`.
- `requirements.txt` avec `discord.py`, `truststore`, `psutil`, `matplotlib`.
- Documentation : `MIGRATION_DISCORD_MACON.md`, `architecture.md` V3.1.

### Fixed

- Timeout Cloudflare 180s : `cleanup_before_tunnel` ne tue plus le dashboard actif (`include_listeners=False`).
- Réponses Discord en double : autostart unique + lock PC Manager.
- SSL Discord sur Python 3.14.
- Interpréteur Python : `PYTHON_BIN` (évite Windows Store pythonw).

### Removed

- `utils/telegram_notifier.py`, `telegram_commander.py` (remplacés par Discord).

## [3.0.0] — 2026-05

- Moteur V3 asyncio, 7 agents, orchestrateur event-driven, dashboard Cloudflare, Telegram.

"""Normalisation des commandes Discord (alias FR + lifecycle)."""
from __future__ import annotations

ALIASES: dict[str, str] = {
    "statut": "status",
    "etat": "status",
    "aide": "help",
    "demarrer": "start",
    "arreter": "kill",
    "stop": "kill",
    "redemarrer": "restart",
    "etatpc": "pc_status",
    "etat_pc": "pc_status",
}

LIFECYCLE_COMMANDS = frozenset({
    "start", "kill", "restart", "pc_status", "pc-status",
})

MUTATING_COMMANDS = frozenset({
    "start",
    "kill",
    "restart",
    "pause",
    "resume",
    "risk",
    "backtest",
    "calibrate",
})

OPERATIONAL_COMMANDS_HELP: dict[str, str] = {
    "status": "État complet du système",
    "pause": "Suspendre les nouveaux trades",
    "resume": "Reprendre les nouveaux trades",
    "risk": "Modifier le risque live (ex: !risk 0.5)",
    "trades": "Positions ouvertes et P&L",
    "agents": "Scores des 7 agents",
    "regime": "Régime et stratégie active",
    "news": "Annonces économiques 24h",
    "backtest": "Backtest rapide",
    "calibrate": "Calibration des poids agents",
    "report": "Rapport journalier immédiat",
    "logs": "Envoie summary.json + report.md du jour",
    "lien": "Lien du dashboard (permanent)",
    "memory": "Stats mémoire SQLite",
    "health": "Diagnostic complet",
    "chart": "Graphique XAUUSD 1M",
    "help": "Liste des commandes",
}

LIFECYCLE_HELP: dict[str, str] = {
    "start": "Démarrer Gold Sniper sur ce PC",
    "kill": "Arrêter le bot (watchdog + moteur)",
    "restart": "Redémarrer le bot",
    "pc_status": "État du PC (RAM, CPU, dashboard)",
}


def resolve_alias(cmd: str) -> str:
    c = (cmd or "").lower().strip()
    return ALIASES.get(c, c)


def canonical_command(cmd: str) -> str:
    return resolve_alias(cmd).replace("-", "_")


def is_mutating_command(cmd: str) -> bool:
    return canonical_command(cmd) in MUTATING_COMMANDS


def discord_command_authorization_failure(
    cmd: str,
    *,
    user_id: int,
    guild_id: int,
    channel_id: int,
    config_module,
) -> str | None:
    cfg_user = int(getattr(config_module, "DISCORD_USER_ID", 0) or 0)
    cfg_guild = int(getattr(config_module, "DISCORD_GUILD_ID", 0) or 0)
    cfg_channel = int(getattr(config_module, "DISCORD_COMMANDS_CHANNEL", 0) or 0)

    if is_mutating_command(cmd) and not (cfg_user and cfg_guild and cfg_channel):
        return "discord_mutating_config_incomplete"
    if cfg_user and int(user_id or 0) != cfg_user:
        return "discord_wrong_user"
    if cfg_guild and int(guild_id or 0) != cfg_guild:
        return "discord_wrong_guild"
    if cfg_channel and int(channel_id or 0) != cfg_channel:
        return "discord_wrong_channel"
    return None


def normalize_command(text: str) -> tuple[str, list[str], str]:
    """Retourne (cmd_canonique, args, texte_normalisé avec préfixe !)."""
    text = (text or "").strip()
    if not text:
        return "", [], ""
    parts = text.split()
    raw = parts[0].lower()
    if raw.startswith("!") or raw.startswith("/"):
        cmd = raw[1:].split("@", 1)[0]
    else:
        cmd = raw
    cmd = canonical_command(cmd)
    args = parts[1:]
    normalized = f"!{cmd}" + (f" {' '.join(args)}" if args else "")
    return cmd, args, normalized


def format_help_text() -> str:
    lines = ["**Commandes opérationnelles** (moteur actif)"]
    for cmd, desc in OPERATIONAL_COMMANDS_HELP.items():
        lines.append(f"`!{cmd}` — {desc}")
    lines.append("")
    lines.append("**PC Manager** (toujours disponible)")
    for cmd, desc in LIFECYCLE_HELP.items():
        lines.append(f"`!{cmd}` — {desc}")
    lines.append("")
    lines.append("Alias FR : `!statut` = `!status`, `!aide` = `!help`")
    return "\n".join(lines)

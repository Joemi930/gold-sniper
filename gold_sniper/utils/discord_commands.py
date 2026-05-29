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
    "logs": "Fichier de logs de session",
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
    cmd = resolve_alias(cmd)
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

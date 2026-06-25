# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — SYSTÈME DE LOGGING STRUCTURÉ [R17]
# ═══════════════════════════════════════════════════════════════════════════════
#
# Logger avec rotation quotidienne, format JSON pour l'analyse automatisée,
# et affichage console propre avec couleurs.
#
# Niveaux personnalisés :
#   DEBUG    (10) — Détails internes (ticks, calculs intermédiaires)
#   INFO     (20) — Événements normaux (agent mis à jour, session changée)
#   TRADE    (25) — ← NIVEAU CUSTOM : exécutions, breakeven, partiels
#   WARNING  (30) — Anomalies récupérables (spread élevé, timeout)
#   CRITICAL (50) — Kill Switch, Watchdog, crash
#
# ═══════════════════════════════════════════════════════════════════════════════

import logging
import logging.handlers
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue

_ui_queue = None

def setup_ui_queue(q: Queue):
    global _ui_queue
    _ui_queue = q

class UIQueueHandler(logging.Handler):
    def emit(self, record):
        if _ui_queue:
            try:
                msg = self.format(record)
                _ui_queue.put(msg)
            except Exception:
                pass

# ─────────────────────────────────────────────────────────────────────────────
# 1. NIVEAU CUSTOM : TRADE (entre INFO et WARNING)
# ─────────────────────────────────────────────────────────────────────────────

TRADE_LEVEL = 25
logging.addLevelName(TRADE_LEVEL, "TRADE")


def _trade(self, message, *args, **kwargs):
    """Méthode ajoutée dynamiquement à Logger pour le niveau TRADE."""
    if self.isEnabledFor(TRADE_LEVEL):
        self._log(TRADE_LEVEL, message, args, **kwargs)


# Injecter la méthode dans la classe Logger
logging.Logger.trade = _trade


# ─────────────────────────────────────────────────────────────────────────────
# 2. FORMATTER JSON (pour les fichiers de log)
# ─────────────────────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """
    Formatte chaque entrée de log en JSON sur une seule ligne.
    Permet l'analyse automatisée avec jq, pandas, ou tout outil JSON.
    
    Format de sortie :
    {"ts": "2025-01-15T14:23:45.123Z", "level": "TRADE", "module": "orchestrator", "msg": "..."}
    """

    def format(self, record: logging.LogRecord) -> str:
        # Timestamp en UTC ISO-8601 avec millisecondes
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        
        log_entry = {
            "ts": dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}Z",
            "level": record.levelname,
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "msg": record.getMessage(),
        }

        # Si une exception est attachée, l'inclure
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Si des données supplémentaires sont passées via extra={"data": {...}}
        if hasattr(record, "data"):
            log_entry["data"] = record.data

        return json.dumps(log_entry, ensure_ascii=False, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# 3. FORMATTER CONSOLE (affichage lisible avec émojis)
# ─────────────────────────────────────────────────────────────────────────────

class ConsoleFormatter(logging.Formatter):
    """
    Affichage console propre avec indicateurs visuels par niveau.
    Format : [HH:MM:SS] 🟢 MODULE — Message
    """

    # Couleurs ANSI pour les terminaux compatibles
    COLORS = {
        "DEBUG":    "\033[90m",      # Gris
        "INFO":     "\033[36m",      # Cyan
        "TRADE":    "\033[32m",      # Vert
        "WARNING":  "\033[33m",      # Jaune
        "ERROR":    "\033[31m",      # Rouge
        "CRITICAL": "\033[91m",      # Rouge vif
    }
    RESET = "\033[0m"

    # Émojis par niveau
    EMOJIS = {
        "DEBUG":    "🔍",
        "INFO":     "📘",
        "TRADE":    "💰",
        "WARNING":  "⚠️",
        "ERROR":    "❌",
        "CRITICAL": "🔴",
    }

    def format(self, record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        timestamp = dt.strftime("%H:%M:%S")
        
        color = self.COLORS.get(record.levelname, "")
        emoji = self.EMOJIS.get(record.levelname, "❓")
        module = record.module.upper()[:12].ljust(12)  # Tronquer/Padder à 12 chars

        message = record.getMessage()

        formatted = f"{color}[{timestamp}] {emoji} {module} — {message}{self.RESET}"

        # Ajouter l'exception si présente
        if record.exc_info and record.exc_info[0] is not None:
            formatted += f"\n{self.formatException(record.exc_info)}"

        return formatted


# ─────────────────────────────────────────────────────────────────────────────
# 4. FACTORY : CONSTRUCTION DU LOGGER
# ─────────────────────────────────────────────────────────────────────────────

def setup_logger(
    name: str = "gold_sniper",
    log_dir: str = "logs",
    level: str = "DEBUG",
    max_bytes: int = 10_485_760,      # 10 MB
    backup_count: int = 30,
) -> logging.Logger:
    """
    Construit et configure le logger principal du système Gold Sniper.

    Deux handlers sont créés :
      1. Console (StreamHandler) — Affichage lisible avec couleurs
      2. Fichier (RotatingFileHandler) — Format JSON avec rotation par taille

    Args:
        name:         Nom du logger (par défaut "gold_sniper")
        log_dir:      Dossier de destination des fichiers de log
        level:        Niveau minimum (DEBUG, INFO, TRADE, WARNING, CRITICAL)
        max_bytes:    Taille max d'un fichier avant rotation (défaut: 10 MB)
        backup_count: Nombre de fichiers de backup conservés (défaut: 30)

    Returns:
        Instance de logging.Logger configurée et prête à l'emploi.
    """
    # Créer le dossier de logs s'il n'existe pas
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Créer le logger
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.DEBUG))

    # Éviter les handlers dupliqués si setup_logger est appelé plusieurs fois
    if logger.handlers:
        return logger

    # ── Handler 1 : Console ──────────────────────────────────────────────
    # Sur Windows, sys.stdout est souvent en cp1252 et ne supporte pas les
    # émojis. On crée un wrapper UTF-8 avec errors='replace' pour que les
    # caractères non-supportés soient remplacés par '?' au lieu de crasher.
    import io
    try:
        console_stream = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True
        )
    except AttributeError:
        # Fallback si sys.stdout n'a pas de .buffer (certains IDE, pytest)
        console_stream = sys.stdout

    console_handler = logging.StreamHandler(console_stream)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(ConsoleFormatter())
    logger.addHandler(console_handler)
    
    # ── Handler 1.5 : UI Queue ───────────────────────────────────────────
    ui_handler = UIQueueHandler()
    ui_handler.setLevel(logging.DEBUG)
    ui_handler.setFormatter(ConsoleFormatter())
    logger.addHandler(ui_handler)

    # ── Handler 2 : Fichier JSON avec rotation ───────────────────────────
    # Le nom du fichier inclut la date pour faciliter le tri
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_file = log_path / f"gold_sniper_{today}.jsonl"

    file_handler = logging.handlers.RotatingFileHandler(
        filename=str(log_file),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)

    # Empêcher la propagation vers le root logger
    logger.propagate = False

    logger.info(
        f"Logger initialisé — Fichier: {log_file} | Niveau: {level} | "
        f"Rotation: {max_bytes // 1_048_576} MB × {backup_count} backups"
    )

    return logger


# ─────────────────────────────────────────────────────────────────────────────
# 5. RACCOURCI GLOBAL
# ─────────────────────────────────────────────────────────────────────────────

def get_logger() -> logging.Logger:
    """
    Récupère l'instance du logger Gold Sniper.
    Si le logger n'a pas encore été initialisé, le crée avec les valeurs par défaut.
    
    Usage dans n'importe quel module :
        from utils.logger import get_logger
        logger = get_logger()
        logger.info("Message")
        logger.trade("Trade exécuté")
    """
    logger = logging.getLogger("gold_sniper")
    if not logger.handlers:
        # Import ici pour éviter les imports circulaires au boot
        from config import LOG_DIR, LOG_LEVEL, LOG_MAX_BYTES, LOG_BACKUP_COUNT
        return setup_logger(
            log_dir=LOG_DIR,
            level=LOG_LEVEL,
            max_bytes=LOG_MAX_BYTES,
            backup_count=LOG_BACKUP_COUNT,
        )
    return logger

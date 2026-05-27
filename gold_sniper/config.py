from dotenv import load_dotenv
load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — CONFIGURATION GLOBALE
# ═══════════════════════════════════════════════════════════════════════════════
#
# Ce fichier est la SEULE source de vérité pour les paramètres du système.
# Aucune constante n'est codée en dur dans les autres modules.
# Tout changement de comportement passe par ici.
#
# ═══════════════════════════════════════════════════════════════════════════════

import os
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────────────────────
# 1. MODE OPÉRATIONNEL
# ─────────────────────────────────────────────────────────────────────────────

# [R10] Flag de protection absolue : False = Paper Trading (aucun ordre réel)
LIVE_MODE = os.getenv("LIVE_MODE", "0") == "1"

# ─────────────────────────────────────────────────────────────────────────────
# 2. IDENTIFIANTS MT5
# ─────────────────────────────────────────────────────────────────────────────

MT5_ACCOUNT   = int(os.getenv("MT5_ACCOUNT", "1200037833") or 1200037833)
MT5_LOGIN     = MT5_ACCOUNT  # Alias V3 pour compatibilite avec les futurs scripts.
MT5_PASSWORD  = os.getenv("MT5_PASSWORD", "")
MT5_SERVER    = os.getenv("MT5_SERVER", "JustMarkets-Demo3")
MT5_PATH      = os.getenv("MT5_PATH", "") or None
MT5_SYMBOL    = os.getenv("MT5_SYMBOL", "XAUUSD")

# Symbole unique — un seul front à la fois (R13 rejeté)
SYMBOL = MT5_SYMBOL

# Identifiant unique du robot pour filtrer nos ordres dans MT5
MAGIC_NUMBER = 240115  # Format : YYMMDD du premier design (2024-01-15)

# ─────────────────────────────────────────────────────────────────────────────
# 3. FUSEAUX HORAIRES
# ─────────────────────────────────────────────────────────────────────────────
# MT5 renvoie les timestamps en UTC. Toutes les conversions passent par ici.
# On utilise zoneinfo (Python 3.9+) pour gérer le DST automatiquement.

TZ_UTC     = timezone.utc                    # Référence UTC pure
TZ_BROKER  = ZoneInfo("Etc/UTC")             # Le serveur MT5 est en UTC
TZ_LOCAL   = ZoneInfo("Europe/Paris")        # Fuseau local de l'opérateur (UTC+1 / UTC+2 DST)
TZ_NY      = ZoneInfo("America/New_York")    # Référence pour les sessions US

# ─────────────────────────────────────────────────────────────────────────────
# 4. GESTION DU RISQUE
# ─────────────────────────────────────────────────────────────────────────────

RISK_PERCENT        = 1.0      # Pourcentage de l'equity risqué par trade
RISK_PCT_PER_TRADE  = RISK_PERCENT  # Script 12: 1% du capital par trade
RISK_PERCENT_FRIDAY = 0.5      # [R16] Risque réduit le vendredi après 18h UTC+1
MAX_TRADES_PER_DAY  = 2        # Limite journalière (sauf setup Diamant 5★)
MIN_RISK_REWARD     = 2.0      # R:R minimum requis pour valider un signal
COOLDOWN_SECONDS    = 180      # 3 minutes de cooldown post-trade
MAX_SLIPPAGE_POINTS = 30       # 3 pips sur config.MT5_SYMBOL (30 points)
MAX_DAILY_DRAWDOWN_PERCENT = 5.0  # Arrêt si perte > 5% de l'equity début de journée
DAILY_LOSS_LIMIT    = 3.0      # -3% -> bascule en paper trading forcé
DRAWDOWN_LIMIT      = 5.0      # -5% -> veto absolu / arrêt total
CONSECUTIVE_LOSS_LIMIT = 3     # 3 pertes consécutives -> pause 2h
CONSECUTIVE_LOSS_PAUSE_HOURS = 2
PAPER_MODE_RECOVERY_PCT = 1.5  # Récupération avant retour live

# ─────────────────────────────────────────────────────────────────────────────
# 5. FILTRES DE SPREAD
# ─────────────────────────────────────────────────────────────────────────────

MAX_SPREAD_POINTS         = 45     # Spread max autorisé pour ouvrir un trade
MAX_SPREAD_KILL_ZONE      = 40     # Tolérance élargie en Kill Zone liquide
MAX_SPREAD_RATIO_PCT      = 5.0    # Spread > 5% ATR = exécution trop chère
SPREAD_ALERT_AFTER_SECONDS = 300   # Alerte Telegram si spread élevé > 5 min
SPREAD_MULTIPLIER_TM      = 2.0    # [R5] Le Trade Manager reporte les modifs si spread > 2× moyenne
SPREAD_AVERAGE_WINDOW     = 100    # Nombre de ticks pour calculer la moyenne mobile du spread

# ─────────────────────────────────────────────────────────────────────────────
# 6. SESSIONS ET KILL ZONES (heures en UTC+1 / Europe/Paris)
# ─────────────────────────────────────────────────────────────────────────────
# Note : les heures sont en tuple (heure, minute). L'ajustement DST est
# géré automatiquement par zoneinfo quand on construit les datetime.

SESSIONS = {
    "ASIA":        {"start": (0, 0),   "end": (8, 0),   "trading_allowed": False},
    "LONDON_OPEN": {"start": (8, 0),   "end": (11, 0),  "trading_allowed": True},  # Kill Zone
    "LONDON":      {"start": (8, 0),   "end": (17, 0),  "trading_allowed": False},  # Cadre large
    "NY_OPEN":     {"start": (13, 0),  "end": (16, 0),  "trading_allowed": True},  # Kill Zone
    "NY":          {"start": (13, 0),  "end": (22, 0),  "trading_allowed": False},  # Cadre large
    "OVERLAP":     {"start": (13, 0),  "end": (17, 0),  "trading_allowed": True},  # Kill Zone
    "OFF_HOURS":   {"start": (22, 0),  "end": (0, 0),   "trading_allowed": False},
}

# Kill Zones : seules fenêtres où l'ouverture de trade est autorisée
KILL_ZONES = {
    "LONDON_OPEN": {"start": (8, 0),  "end": (11, 0)},
    "NY_OPEN":     {"start": (13, 0), "end": (16, 0)},
    "OVERLAP":     {"start": (13, 0), "end": (17, 0)},
}

# Fenêtre de rollover : trading et modifications d'ordres INTERDITS
ROLLOVER_START = (23, 45)   # UTC+1
ROLLOVER_END   = (0, 15)    # UTC+1 (jour suivant)

# [R16] Vendredi : réduction puis coupure
FRIDAY_RISK_REDUCTION_HOUR = 18  # UTC+1 : risque passe à 0.5%
FRIDAY_TRADING_HALT_HOUR   = 21  # UTC+1 : trading coupé totalement

# ─────────────────────────────────────────────────────────────────────────────
# 7. NEWS / SENTINELLE ÉCONOMIQUE
# ─────────────────────────────────────────────────────────────────────────────

NEWS_SCRAPE_INTERVAL_SECONDS = 60       # Fréquence de scraping du calendrier
NEWS_BLACKOUT_MINUTES        = 10       # Fenêtre ±10 minutes autour d'une news rouge
NEWS_ASSUME_HOSTILE_AFTER    = 300      # [R7] 5 minutes sans source fiable → blocage
NEWS_MAX_CONSECUTIVE_FAILURES = 5       # [R7] Nombre d'échecs avant mode ASSUME_HOSTILE

# ─────────────────────────────────────────────────────────────────────────────
# 8. RATE LIMITER MT5 [R15]
# ─────────────────────────────────────────────────────────────────────────────

MT5_MAX_CALLS_PER_SECOND = 10   # Nombre max d'appels MT5 par seconde

# ─────────────────────────────────────────────────────────────────────────────
# 9. BOUGIES — PROFONDEUR HISTORIQUE
# ─────────────────────────────────────────────────────────────────────────────

CANDLE_HISTORY = {
    "4H":  120,    # 120 bougies = 20 jours de contexte
    "15m": 384,    # 384 bougies = 4 jours
    "1m":  1440,   # 1440 bougies = 24 heures
}

# ─────────────────────────────────────────────────────────────────────────────
# 10. AGENT 3 — FILTRE ANTI-FAKEOUT ASIATIQUE [R8]
# ─────────────────────────────────────────────────────────────────────────────

ASIAN_RANGE_MIN_ATR_RATIO = 0.3  # Le range asiatique doit être ≥ 30% de l'ATR(14, 4H)

# ─────────────────────────────────────────────────────────────────────────────
# 11. AGENT 5 — MICROSCOPE
# ─────────────────────────────────────────────────────────────────────────────

SWING_PIVOT_STRENGTH = 2    # Nombre de bougies de chaque côté pour un swing point

# ─────────────────────────────────────────────────────────────────────────────
# 12. TRADE MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

BREAKEVEN_RR_TRIGGER  = 1.0   # R:R minimum pour activer le breakeven
PARTIAL_RR_TRIGGER    = 2.0   # R:R minimum pour prendre le partiel
PARTIAL_CLOSE_PERCENT = 50    # Pourcentage du volume à fermer en partiel
SL_BUFFER_POINTS      = 2     # Marge de sécurité (en points) sous/au-dessus du SL structurel

# ─────────────────────────────────────────────────────────────────────────────
# 13. NOTIFICATIONS TELEGRAM [R9]
# ─────────────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_ENABLED   = bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)
TELEGRAM_BOT_TOKEN = TELEGRAM_TOKEN  # Alias rétrocompatible

# ─────────────────────────────────────────────────────────────────────────────
# 14. PAPER TRADING [R10]
# ─────────────────────────────────────────────────────────────────────────────

PAPER_SIMULATED_EQUITY = 10_000.0    # Capital fictif de départ en mode simulation
PAPER_CSV_PATH         = "simulation_results.csv"

# ─────────────────────────────────────────────────────────────────────────────
# 15. LOGGING [R17]
# ─────────────────────────────────────────────────────────────────────────────

LOG_DIR             = "logs"          # Dossier des fichiers de log
LOG_LEVEL           = "DEBUG"         # Niveau minimum affiché
LOG_RETENTION_DAYS  = 30              # Nombre de jours de conservation
LOG_MAX_BYTES       = 10_485_760      # 10 MB max par fichier avant rotation
LOG_BACKUP_COUNT    = 30              # Nombre de fichiers de backup

# ─────────────────────────────────────────────────────────────────────────────
# 16. RECOVERY & PERSISTANCE
# ─────────────────────────────────────────────────────────────────────────────

RECOVERY_FILE_PATH = "recovery.json"
RECOVERY_DEBOUNCE_SECONDS = 1.0  # Intervalle minimum entre deux sauvegardes

# ─────────────────────────────────────────────────────────────────────────────
# 17. WATCHDOG
# ─────────────────────────────────────────────────────────────────────────────

WATCHDOG_HEARTBEAT_FILE    = "watchdog_heartbeat.tmp"
WATCHDOG_HEARTBEAT_INTERVAL = 2      # Secondes entre chaque ping
WATCHDOG_TIMEOUT_WARNING    = 15     # Secondes avant alerte watchdog
WATCHDOG_TIMEOUT_CRITICAL   = 30     # Secondes avant kill + restart
EVENT_DRIVEN_TIMEOUT        = 5.0    # Fallback A4 si aucun agent ne publie

# ─────────────────────────────────────────────────────────────────────────────
# 18. DIAMANT 5★ — SETUP D'EXCEPTION
# ─────────────────────────────────────────────────────────────────────────────

DIAMOND_MIN_RR         = 3.0     # R:R minimum pour un setup Diamant
DIAMOND_SWEET_SPOT_LOW = 0.68    # Fibonacci sweet spot bas
DIAMOND_SWEET_SPOT_HIGH = 0.73   # Fibonacci sweet spot haut

# ─────────────────────────────────────────────────────────────────────────────
# 19. POIDS ADAPTATIFS — CONTRÔLE SEMAINE DÉMO
# ─────────────────────────────────────────────────────────────────────────────
# Désactivé pendant la 1re semaine démo pour éviter l'oscillation avec
# weight_calibrator.py (batch/50 trades). Réactiver après validation.
# Seul weight_calibrator.py sera utilisé, déclenché via /calibrate.
ADAPTIVE_WEIGHTS_ENABLED = False

# Script 08 — calendrier economique Finnhub
# Cle gratuite: https://finnhub.io -> Dashboard -> API Key.
# Laisser vide force l'Agent 6 a utiliser son fallback ForexFactory.
FINNHUB_TOKEN = os.getenv("FINNHUB_TOKEN", "")

# FMP: limite 200 req/jour — utiliser uniquement pour les données macro et fondamentales, pas pour le tick data.
FMP_TOKEN = os.getenv("FMP_TOKEN", "")
NEWS_HIGH_IMPACT_BLACKOUT_MINUTES = 15
NEWS_STEALTH_AFTER_MINUTES = 60

# Dashboard Web + Cloudflare Tunnel
DASHBOARD_ENABLED = os.getenv("DASHBOARD_ENABLED", "1") not in {"0", "false", "False"}
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
CLOUDFLARE_ENABLED = os.getenv("CLOUDFLARE_ENABLED", "1") not in {"0", "false", "False"}
CLOUDFLARED_PATH = os.getenv(
    "CLOUDFLARED_PATH",
    r"C:\Users\tetej\AppData\Local\Programs\cloudflared\cloudflared.exe",
)

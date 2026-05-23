# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — CONFIGURATION GLOBALE
# ═══════════════════════════════════════════════════════════════════════════════
#
# Ce fichier est la SEULE source de vérité pour les paramètres du système.
# Aucune constante n'est codée en dur dans les autres modules.
# Tout changement de comportement passe par ici.
#
# ═══════════════════════════════════════════════════════════════════════════════

from datetime import timezone, timedelta
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────────────────────────────────────
# 1. MODE OPÉRATIONNEL
# ─────────────────────────────────────────────────────────────────────────────

# [R10] Flag de protection absolue : False = Paper Trading (aucun ordre réel)
LIVE_MODE = False

# ─────────────────────────────────────────────────────────────────────────────
# 2. IDENTIFIANTS MT5
# ─────────────────────────────────────────────────────────────────────────────

MT5_ACCOUNT   = 5050805409   # Numéro de compte (à remplir)
MT5_PASSWORD  = "G-ZvNd6a" # Mot de passe (à remplir)
MT5_SERVER    = "MetaQuotes-Demo" # Serveur du broker (ex: "ICMarketsSC-Demo")
MT5_PATH      = None       # Chemin vers le terminal MT5 (None = auto-détection)

# Symbole unique — un seul front à la fois (R13 rejeté)
SYMBOL = "XAUUSD"

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
RISK_PERCENT_FRIDAY = 0.5      # [R16] Risque réduit le vendredi après 18h UTC+1
MAX_TRADES_PER_DAY  = 2        # Limite journalière (sauf setup Diamant 5★)
MIN_RISK_REWARD     = 2.0      # R:R minimum requis pour valider un signal
COOLDOWN_SECONDS    = 180      # 3 minutes de cooldown post-trade
MAX_SLIPPAGE_POINTS = 30       # 3 pips sur XAUUSD (30 points)
MAX_DAILY_DRAWDOWN_PERCENT = 5.0  # Arrêt si perte > 5% de l'equity début de journée

# ─────────────────────────────────────────────────────────────────────────────
# 5. FILTRES DE SPREAD
# ─────────────────────────────────────────────────────────────────────────────

MAX_SPREAD_POINTS         = 30     # Spread max autorisé pour ouvrir un trade
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

TELEGRAM_ENABLED   = False     # Activer/désactiver les notifications
TELEGRAM_BOT_TOKEN = ""        # Token du bot Telegram (à remplir)
TELEGRAM_CHAT_ID   = ""        # ID du chat de destination (à remplir)

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

# ─────────────────────────────────────────────────────────────────────────────
# 18. DIAMANT 5★ — SETUP D'EXCEPTION
# ─────────────────────────────────────────────────────────────────────────────

DIAMOND_MIN_RR         = 3.0     # R:R minimum pour un setup Diamant
DIAMOND_SWEET_SPOT_LOW = 0.68    # Fibonacci sweet spot bas
DIAMOND_SWEET_SPOT_HIGH = 0.73   # Fibonacci sweet spot haut

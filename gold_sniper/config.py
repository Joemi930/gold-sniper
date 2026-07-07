import os
from dotenv import load_dotenv
from safety.research_branch_guard import current_git_branch, research_shadow_only_enabled

if os.getenv("GOLD_SNIPER_SKIP_DOTENV", "0") != "1":
    load_dotenv()

# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v3.1 — CONFIGURATION GLOBALE
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
VALID_RUN_MODES = {"LIVE", "PAPER", "REPLAY", "BACKTEST"}
CURRENT_BRANCH = current_git_branch()
RESEARCH_SHADOW_ONLY = research_shadow_only_enabled(CURRENT_BRANCH)
RUN_MODE = os.getenv("RUN_MODE", "REPLAY").upper()
ALLOW_BROKER_WRITES = os.getenv("ALLOW_BROKER_WRITES", "0") == "1"
if RESEARCH_SHADOW_ONLY:
    # A research branch must never be capable of sending broker orders.
    RUN_MODE = "REPLAY" if RUN_MODE == "LIVE" else RUN_MODE
    ALLOW_BROKER_WRITES = False
    LIVE_MODE = False
else:
    LIVE_MODE = os.getenv("LIVE_MODE", "1" if RUN_MODE == "LIVE" else "0") == "1"

# ─────────────────────────────────────────────────────────────────────────────
# 2. IDENTIFIANTS MT5
# ─────────────────────────────────────────────────────────────────────────────

MT5_ACCOUNT   = int(os.getenv("MT5_ACCOUNT", "1200037833") or 1200037833)
MT5_LOGIN     = MT5_ACCOUNT  # Alias V3 pour compatibilite avec les futurs scripts.
MT5_PASSWORD  = os.getenv("MT5_PASSWORD", "")
MT5_SERVER    = os.getenv("MT5_SERVER", "JustMarkets-Demo3")
MT5_PATH      = os.getenv("MT5_PATH", "") or None
MT5_TERMINAL_PATH = os.getenv(
    "MT5_TERMINAL_PATH",
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
)
MT5_BOOT_WAIT_SECONDS = int(os.getenv("MT5_BOOT_WAIT_SECONDS", "90"))
MT5_SYMBOL    = os.getenv("MT5_SYMBOL", "XAUUSD")

# Symbole unique — un seul front à la fois (R13 rejeté)
SYMBOL = MT5_SYMBOL

# Identifiant unique du robot pour filtrer nos ordres dans MT5
MAGIC_NUMBER = 240115  # Format : YYMMDD du premier design (2024-01-15)

# §3: Whitelist demo login — refuse execution if connected account != this
MT5_DEMO_LOGIN = int(os.getenv("MT5_DEMO_LOGIN", "1200037833") or "1200037833")

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
MAX_RISK_PCT_PER_TRADE = 1.0  # Plafond absolu: aucune strategie/session > 1%
RISK_PERCENT_FRIDAY = 0.5      # [R16] Risque réduit le vendredi après 18h UTC+1
MAX_TRADES_PER_DAY  = 3        # Limite journaliere standard
EXCEPTIONAL_OVERRIDE_SCORE = 92 # Autorise un 4e trade si setup exceptionnel
MAX_TRADES_ABSOLUTE = 4        # Plafond absolu avec override
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
    "TOKYO":       {"start": (0, 0),   "end": (8, 0),   "trading_allowed": False},
    "LONDON_OPEN": {"start": (8, 0),   "end": (11, 0),  "trading_allowed": True},  # Kill Zone
    "LONDON":      {"start": (8, 0),   "end": (17, 0),  "trading_allowed": False},  # Cadre large
    "NY_OPEN":     {"start": (13, 0),  "end": (16, 0),  "trading_allowed": True},  # Kill Zone
    "NY":          {"start": (13, 0),  "end": (22, 0),  "trading_allowed": False},  # Cadre large
    "OVERLAP":     {"start": (13, 0),  "end": (17, 0),  "trading_allowed": True},  # Kill Zone
    "ROLLOVER":    {"start": (23, 45), "end": (0, 15),  "trading_allowed": False},
    "WEEKEND":     {"start": (0, 0),   "end": (0, 0),   "trading_allowed": False},
    "FRIDAY_HALT": {"start": (21, 0),  "end": (0, 0),   "trading_allowed": False},
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
# 9-bis. TIMEFRAME D'EXÉCUTION — migration M1 → M15  [Étape M15]
# ─────────────────────────────────────────────────────────────────────────────
# Principe: la logique Kasper/SMC est INCHANGÉE; on décale l'échelle d'un cran.
# Le moteur continue d'itérer le flux 1m (fills intrabar précis), mais le pipeline
# de DÉCISION (agents/Kasper/entrée) ne se déclenche que sur la clôture d'une
# bougie EXECUTION_TF, et la structure (stop/target) est lue sur EXECUTION_TF —
# d'où un 1R bien plus grand, donc un coût (~50 pts) en faible fraction de R.
#
# DÉFAUT "1m": chaque bougie est une clôture d'exécution → comportement IDENTIQUE
# à aujourd'hui → ZÉRO régression. Passer en "15m" active la migration.
# Surchargeable par env: $env:GS_EXECUTION_TF="15m"
EXECUTION_TF = os.environ.get("GS_EXECUTION_TF", "1m").strip() or "1m"

# Échelle (ladder): pour chaque TF d'exécution, quelles TF nourrissent les agents.
# "exec"  = la TF de déclenchement (micro-trigger agent_5, structure stop/target)
# "ltf"   = structure basse (agent_1 structure_15m, sweeps agent_3)
# "htf"   = structure haute / biais (agent_1 structure_4h, POI agent_2)
AGENT_TF_LADDER = {
    "1m":  {"exec": "1m",  "ltf": "15m", "htf": "4H"},   # actuel (défaut)
    "5m":  {"exec": "5m",  "ltf": "1H",  "htf": "4H"},
    "15m": {"exec": "15m", "ltf": "1H",  "htf": "4H"},    # 1R ~120 pts (mesuré)
    "30m": {"exec": "30m", "ltf": "1H",  "htf": "4H"},    # 1R ~200-300 pts (à mesurer)
    "1H":  {"exec": "1H",  "ltf": "4H",  "htf": "4H"},    # 1R ~350-500 pts (à mesurer)
}

def execution_ladder() -> dict:
    """TF-ladder actif pour l'EXECUTION_TF courant (fallback sur 1m)."""
    return AGENT_TF_LADDER.get(EXECUTION_TF, AGENT_TF_LADDER["1m"])

# Plancher de stop structurel (étape M15 — finir l'étape 3).
# Le stop d'agent_5 vient du micro-sweep (~mèche), donc 1R reste serré (~120 pts)
# même en 15m. Ce plancher force risk_points >= STOP_ATR_FLOOR_MULT × ATR(exec),
# ce qui agrandit 1R. Les cibles (tp = risk × RR) scalent → RR préservé.
# DÉFAUT 0.0 = INACTIF (zéro régression). Surchargeable: $env:GS_STOP_ATR_FLOOR_MULT="1.5"
STOP_ATR_FLOOR_MULT = float(os.environ.get("GS_STOP_ATR_FLOOR_MULT", "0.0") or "0.0")

# ── CIBLES STRUCTURELLES (doctrine intraday) ──────────────────────────────
# TP1 = dernier swing haut/bas pertinent au-delà de l'entrée (la liquidité
# opposée la plus proche); TP2 = le swing suivant après TP1. Un gagnant peut
# ainsi rapporter 3-5R au lieu d'être plafonné à 1R/2R. Le RR estimé devient
# structurel → le gate Kasper (rr>=1.5) filtre naturellement les entrées sans
# espace (sélectivité par la structure, pas par des règles binaires).
# Défaut ACTIF (nouvelle doctrine). Désactivable: $env:GS_STRUCT_TP="0"
STRUCT_TP = os.environ.get("GS_STRUCT_TP", "1").strip().lower() in ("1","true","yes","on")
STRUCT_TP_MIN_DIST_ATR = float(os.environ.get("GS_STRUCT_MIN_ATR", "0.3") or "0.3")  # dist min TP1 (×ATR), env-réglable
STRUCT_TP_SEP_ATR = 0.5        # séparation minimale TP1→TP2
STRUCT_TP_LOOKBACK = 120        # bougies exec pour la détection des swings
STRUCT_TP_SWING_K = int(float(os.environ.get("GS_SWING_K", "2") or "2"))  # fractale (k↑ = swings plus institutionnels)

# Mise à breakeven indépendante de TP1: quand le trade atteint +1R en sa faveur,
# le SL monte à entrée±0.10R. Indispensable avec des TP structurels lointains
# (un trade à +2R qui se retourne ne doit plus finir à -1R).
# Défaut ACTIF. Désactivable: $env:GS_BE_AT_1R="0"
BE_AT_1R = os.environ.get("GS_BE_AT_1R", "1").strip().lower() in ("1","true","yes","on")

# ── Garde-fous anti-rechute (audit mois tueurs: déc-2024 = 3 re-entrées BUY
# perdantes en 45 min le 27/12). Causaux, appliqués à l'ouverture uniquement.
# GS_LOSS_BREAKER: stop du jour après N pertes SL pleines (0 = off)
# GS_LOSS_COOLDOWN_MIN: après un SL, blocage des ré-entrées MÊME direction N minutes (0 = off)
LOSS_BREAKER_MAX_SL_PER_DAY = int(float(os.environ.get("GS_LOSS_BREAKER", "0") or "0"))
LOSS_COOLDOWN_SAME_SIDE_MIN = int(float(os.environ.get("GS_LOSS_COOLDOWN_MIN", "0") or "0"))
# Plafond d'exposition CONCURRENTE (la vraie cause des gros DD: empilement de
# positions même direction ouvertes en même temps, ex. déc-2024 = 3 BUY concurrents
# perdant ensemble). 0 = illimité. GS_MAX_CONCURRENT=1 → une position à la fois;
# GS_MAX_CONCURRENT_SAME_SIDE=1 → une seule par direction (autorise 1 BUY + 1 SELL).
MAX_CONCURRENT_POSITIONS = int(float(os.environ.get("GS_MAX_CONCURRENT", "0") or "0"))
MAX_CONCURRENT_SAME_SIDE = int(float(os.environ.get("GS_MAX_CONCURRENT_SAME_SIDE", "0") or "0"))
# ── ROLLING DRAWDOWN GUARD (protection régime perdant multi-jours/mois) ──
# La vraie cause du fond à 77.98$: hémorragie lente oct-2024→mars-2025 qu'aucun
# garde court-terme n'attrape. Quand l'équité recule de GS_ROLLING_DD_PCT% depuis
# son pic, on met les NOUVELLES entrées en pause GS_ROLLING_DD_PAUSE_DAYS jours
# (le régime perdant passe), puis on rebase le pic et on reprend. Causal (équité
# réalisée + temps uniquement), non-overfit (règle générale). 0 = off.
# Prouvé sur confirm_final: seuil 10 / pause 7 → DD 23.8%→11.6%, équité préservée.
ROLLING_DD_PCT = float(os.environ.get("GS_ROLLING_DD_PCT", "0") or "0")
ROLLING_DD_PAUSE_DAYS = float(os.environ.get("GS_ROLLING_DD_PAUSE_DAYS", "7") or "7")
# ── FILTRE QUALITÉ STRUCTURELLE (rr_estimate minimum) ──
# Hypothèse d'edge validée par année + buckets: seuls les setups à reward:risk
# structurel élevé tiennent hors-échantillon. rr>=4 = positif en 2024/2025/2026;
# rr<4 = perdant/bruit OOS. rr_estimate est causal (cible = swing des 20 dernières
# bougies clôturées / risque = |entrée-SL|), connu à l'entrée, zéro lookahead.
# 0 = off (garde le gate Kasper 1.5 existant). GS_MIN_RR=4 pour activer le filtre.
MIN_RR = float(os.environ.get("GS_MIN_RR", "0") or "0")

# ── Compte broker (JustMarkets) ──
ACCOUNT_LEVERAGE = float(os.environ.get("GS_LEVERAGE", "2000") or "2000")  # 1:2000
XAUUSD_CONTRACT_SIZE = 100.0  # onces par lot standard
BE_AT_1R_TRIGGER_R = 1.0
BE_AT_1R_LOCK_R = 0.10

# Filtre de régime (audit 5 mois — février 0/5).
# La stratégie ne joue QUE des liquidity_sweep_reversal (mean-reversion). Ce type
# d'edge marche en RANGE/WEAK trend et se fait DÉTRUIRE en tendance forte (fade the
# trend → straight-to-SL). agent_1 expose primary_regime ∈ {RANGE, WEAK_UP/DOWN,
# STRONG_UP/DOWN}. Ce filtre bloque l'entrée dans les régimes listés.
# DÉFAUT OFF (zéro régression). Surchargeable:
#   $env:GS_REGIME_FILTER = "1"
#   $env:GS_REGIME_BLOCK  = "STRONG_UP,STRONG_DOWN"   (régimes à bloquer)
REGIME_FILTER_ENABLED = os.environ.get("GS_REGIME_FILTER", "0").strip().lower() in (
    "1", "true", "yes", "on"
)
# ── STRATEGY V2 — sélecteur de régime dual-edge ──────────────────────────────
# V2: le régime (agent_1 primary_regime) sélectionne l'edge actif dans Kasper:
#   RANGE / WEAK_*  → liquidity_sweep_reversal (mean-reversion, l'edge historique)
#   STRONG_UP/DOWN  → trend_continuation (le modèle continuation, débloqué et
#                     tradé AVEC la tendance) — le reversal y est BLOQUÉ (fade
#                     de tendance forte = le tueur prouvé de février).
# DÉFAUT OFF (zéro régression). $env:GS_STRATEGY_V2="1" pour activer.
# NOTE: quand V2 est actif, NE PAS activer GS_REGIME_FILTER (V2 le remplace).
STRATEGY_V2 = os.environ.get("GS_STRATEGY_V2", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

# ── UNIFIED LIVE DECISION PIPELINE (§2-A) ──────────────────────────────
# Active le pipeline Kasper/PDE validé dans le runtime live.
# DÉFAUT OFF (zéro régression). $env:GS_UNIFIED_PIPELINE="1" pour activer.
# Quand actif: court-circuite le vote legacy dans core/orchestrator.py.
UNIFIED_PIPELINE = os.environ.get("GS_UNIFIED_PIPELINE", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

REGIME_BLOCKED_SET = {
    r.strip().upper()
    for r in os.environ.get("GS_REGIME_BLOCK", "STRONG_UP,STRONG_DOWN").split(",")
    if r.strip()
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

TP1_RR                = 1.0   # TP1 officiel: 1R
TP2_RR                = 2.0   # TP2 officiel: 2R
BE_PLUS_RR            = float(os.environ.get("GS_BE_PLUS_RR", "0.5") or "0.5")  # §2-C: aligne sur replay (0.5R, pas 0.10R)
BREAKEVEN_RR_TRIGGER  = TP1_RR
PARTIAL_RR_TRIGGER    = TP1_RR
PARTIAL_CLOSE_PERCENT = 50    # Pourcentage du volume à fermer en partiel
SL_BUFFER_POINTS      = 2     # Marge de sécurité (en points) sous/au-dessus du SL structurel

# ─────────────────────────────────────────────────────────────────────────────
# 12-bis. FILTRE DE COÛT (rentabilité)  [Étape 1 — analyse drag mois-1]
# ─────────────────────────────────────────────────────────────────────────────
# Refuse les trades dont le gain à TP1 ne couvre pas N× le coût d'exécution.
# AGIT À LA PRISE DE TRADE (manager), PAS sur la géométrie du risque : le grading
# Kasper (A+/A/B) reste intact. On décline seulement les setups non-économiques
# (1R trop petit face aux coûts). Ce n'est pas du cheating : on ne force/baisse
# aucun seuil de signal, on refuse de payer plus de coût que de gain espéré.
#
# Surchargeable par variable d'environnement (pour balayer N sur des fenêtres
# en parallèle sans éditer ce fichier) :
#   $env:GS_COST_FILTER     = "1"|"0"   (active/désactive — défaut INACTIF)
#   $env:GS_COST_FILTER_N   = "3.0"     (multiplicateur N — défaut 3.0)
#   $env:GS_COST_FILTER_COST_PTS = "50" (coût aller-retour estimé en points)
COST_FILTER_ENABLED = os.environ.get("GS_COST_FILTER", "0").strip().lower() in (
    "1", "true", "yes", "on"
)
COST_FILTER_MIN_R_COST_MULT = float(os.environ.get("GS_COST_FILTER_N", "3.0") or "3.0")
COST_FILTER_COST_POINTS = float(os.environ.get("GS_COST_FILTER_COST_PTS", "50.0") or "50.0")

# ─────────────────────────────────────────────────────────────────────────────
# 12-ter. GESTION DES RUNNERS — trailing après TP1  [Étape 2 — analyse payoff]
# ─────────────────────────────────────────────────────────────────────────────
# Constat: quand les gagnants courent jusqu'à TP2, payoff ~1.0 et net positif;
# quand ils sont plafonnés au protected SL (+0.5R), payoff ~0.49 et net négatif.
# On remplace le protected SL FIXE de leg_2 par un trailing qui SUIT le pic de
# prix depuis TP1, à RUNNER_TRAIL_R derrière le plus-haut (en R).
#
# SÛR PAR CONSTRUCTION: leg_1 a déjà encaissé +1R, et le trail ne descend JAMAIS
# sous le plancher protégé (PROTECTED_RUNNER_SL_R). Il ne peut donc que verrouiller
# PLUS de profit — jamais transformer un gagnant en perdant. Le seul arbitrage est
# trail serré (sort tôt, capture > plancher mais < TP2) vs large (proche du fixe).
#
# Surchargeable par variable d'env (balayage parallèle sans éditer le fichier):
#   $env:GS_RUNNER_TRAIL    = "1"|"0"   (active/désactive — défaut INACTIF)
#   $env:GS_RUNNER_TRAIL_R  = "0.5"     (distance du trail derrière le pic, en R)
RUNNER_TRAIL_ENABLED = os.environ.get("GS_RUNNER_TRAIL", "0").strip().lower() in (
    "1", "true", "yes", "on"
)
RUNNER_TRAIL_R = float(os.environ.get("GS_RUNNER_TRAIL_R", "0.5") or "0.5")

# ─────────────────────────────────────────────────────────────────────────────
# 13. NOTIFICATIONS TELEGRAM [R9]
# ─────────────────────────────────────────────────────────────────────────────

# Rollback Telegram (désactivé — migré vers Discord)
# TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN", "")
# TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_TOKEN     = ""
TELEGRAM_CHAT_ID   = ""
TELEGRAM_ENABLED   = False
TELEGRAM_BOT_TOKEN = ""

# ── Discord (remplace Telegram) ──────────────────────────
DISCORD_TOKEN            = os.getenv("DISCORD_TOKEN", "")
DISCORD_GUILD_ID         = int(os.getenv("DISCORD_GUILD_ID", "0") or "0")
DISCORD_USER_ID          = int(os.getenv("DISCORD_USER_ID", "0") or "0")
DISCORD_ALERTS_CHANNEL   = int(os.getenv("DISCORD_ALERTS_CHANNEL_ID", "0") or "0")
DISCORD_COMMANDS_CHANNEL = int(os.getenv("DISCORD_COMMANDS_CHANNEL_ID", "0") or "0")
DISCORD_REPORTS_CHANNEL  = int(os.getenv("DISCORD_REPORTS_CHANNEL_ID", "0") or "0")
DISCORD_LOGS_CHANNEL     = int(os.getenv("DISCORD_LOGS_CHANNEL_ID", "0") or "0")
DISCORD_ENABLED          = bool(DISCORD_TOKEN and DISCORD_ALERTS_CHANNEL)

# IPC Discord (pc_manager <-> main)
DISCORD_INBOX_PATH       = "data/discord_inbox.jsonl"
BOT_READY_PATH           = "data/bot_ready.json"
KILL_FLAG_PATH           = "kill_flag.txt"
WATCHDOG_STATE_PATH      = "data/watchdog_state.json"

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

# Surveillance reseau (WiFi / coupure Internet)
NETWORK_CHECK_HOST          = os.getenv("NETWORK_CHECK_HOST", "1.1.1.1")
NETWORK_CHECK_INTERVAL      = int(os.getenv("NETWORK_CHECK_INTERVAL", "15"))
NETWORK_OFFLINE_VETO_SECONDS = int(os.getenv("NETWORK_OFFLINE_VETO_SECONDS", "30"))

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
DASHBOARD_PUBLIC = os.getenv("DASHBOARD_PUBLIC", "0") == "1"
DASHBOARD_TOKEN = os.getenv("DASHBOARD_TOKEN", "")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
CLOUDFLARE_ENABLED = os.getenv("CLOUDFLARE_ENABLED", "0") not in {"0", "false", "False"}
CLOUDFLARED_PATH = os.getenv(
    "CLOUDFLARED_PATH",
    r"C:\Users\tetej\AppData\Local\Programs\cloudflared\cloudflared.exe",
)
PYTHON_BIN = os.getenv(
    "PYTHON_BIN",
    r"C:\Users\tetej\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe",
)
CLOUDFLARE_TUNNEL_TIMEOUT = float(os.getenv("CLOUDFLARE_TUNNEL_TIMEOUT", "120"))
BOOT_READY_TIMEOUT = float(os.getenv("BOOT_READY_TIMEOUT", "180"))
AGENT_DASHBOARD_PULSE_SEC = float(os.getenv("AGENT_DASHBOARD_PULSE_SEC", "10"))

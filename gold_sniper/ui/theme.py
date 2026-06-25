# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.1 — THEME (Palette de couleurs & Constantes visuelles)
# ═══════════════════════════════════════════════════════════════════════════════

# ─── COULEURS PRINCIPALES ────────────────────────────────────────────────────
BG_DEEP         = "#080c18"      # Fond principal ultra-dark
BG_CARD         = "#0d1225"      # Fond des cartes
BG_CARD_HOVER   = "#111830"      # Fond des cartes au survol
BG_BORDER       = "#1a2545"      # Bordures subtiles
BG_PANEL        = "#0b1020"      # Fond des panneaux latéraux
BG_HEADER       = "#060a14"      # Fond du header

# ─── COULEURS D'ACCENT ───────────────────────────────────────────────────────
GOLD            = "#f5c842"      # Or principal — titre, bordures HARD FILTER
GOLD_DIM        = "#a0820c"      # Or atténué
NEON_GREEN      = "#00ff88"      # Score élevé, sessions actives, trades profitables
NEON_GREEN_DIM  = "#007744"      # Vert atténué (scores moyens)
ELECTRIC_BLUE   = "#00bfff"      # Connexions pipeline actives, infos générales
ELECTRIC_BLUE_DIM = "#004a6e"   # Bleu atténué (connexions inactives)
ORANGE          = "#ff6b35"      # Warnings, scores faibles
RED             = "#ff2d55"      # Erreurs critiques, kill switch, drawdown
RED_DIM         = "#7a0020"      # Rouge atténué
PURPLE          = "#bf5af2"      # Orchestrateur (couleur unique)
CYAN            = "#32d2d2"      # Données de marché, prix

# ─── TEXTES ──────────────────────────────────────────────────────────────────
TEXT_PRIMARY    = "#e8eaf6"      # Texte principal
TEXT_SECONDARY  = "#8892b0"      # Texte secondaire / labels
TEXT_MUTED      = "#4a5270"      # Texte désactivé / timestamps
TEXT_GOLD       = GOLD           # Titres importants

# ─── COULEURS PAR AGENT ──────────────────────────────────────────────────────
AGENT_COLORS = {
    "agent_1": "#00bfff",        # Météo — Bleu électrique
    "agent_2": "#f5c842",        # Cartographe — Or
    "agent_3": "#00ff88",        # Liquidité — Vert néon
    "agent_4": "#ff6b35",        # Fibonacci — Orange
    "agent_5": "#bf5af2",        # Microscope — Violet
    "agent_6": "#ff2d55",        # Sentinelle — Rouge
    "agent_7": "#32d2d2",        # Chronos — Cyan
    "orchestrator": "#f5c842",   # Orchestrateur — Or
}

AGENT_LABELS = {
    "agent_1": "MÉTÉO",
    "agent_2": "CARTOGRAPHE",
    "agent_3": "LIQUIDITÉ",
    "agent_4": "FIBONACCI",
    "agent_5": "MICROSCOPE",
    "agent_6": "SENTINELLE",
    "agent_7": "CHRONOS",
    "orchestrator": "CERVEAU V2",
}

AGENT_WEIGHTS = {
    "agent_1": 30,
    "agent_2": 25,
    "agent_3": 20,
    "agent_4": 15,
    "agent_5": 10,
    "agent_6": 0,   # GATE — pas de poids dans le score
    "agent_7": 0,   # GATE — pas de poids dans le score
}

AGENT_ICONS = {
    "agent_1": "🌤",
    "agent_2": "🗺",
    "agent_3": "💧",
    "agent_4": "📐",
    "agent_5": "🔬",
    "agent_6": "📡",
    "agent_7": "⏱",
    "orchestrator": "🧠",
}

# ─── POLICES ─────────────────────────────────────────────────────────────────
FONT_TITLE      = ("Consolas", 22, "bold")
FONT_SUBTITLE   = ("Consolas", 13, "bold")
FONT_BODY       = ("Consolas", 11)
FONT_SMALL      = ("Consolas", 9)
FONT_MONO_BIG   = ("Consolas", 28, "bold")
FONT_MONO_MED   = ("Consolas", 16, "bold")
FONT_MONO_SMALL = ("Consolas", 11)

# ─── DIMENSIONS ──────────────────────────────────────────────────────────────
HEADER_HEIGHT   = 58
AGENT_COL_W     = 240
ACCOUNT_COL_W   = 280
LOG_HEIGHT      = 200
CARD_RADIUS     = 8
BORDER_W        = 1

# ─── ANIMATION ───────────────────────────────────────────────────────────────
REFRESH_FAST_MS  = 100    # Prix, PnL
REFRESH_MED_MS   = 250    # Scores agents, statuts
REFRESH_SLOW_MS  = 1000   # Log feed drain
PARTICLE_SPEED   = 8      # Pixels/frame pour les particules pipeline

# ─── SCORE → COULEUR ─────────────────────────────────────────────────────────
def score_color(score: float) -> str:
    """Retourne la couleur correspondant au score (0-100)."""
    if score >= 80:
        return NEON_GREEN
    elif score >= 50:
        return GOLD
    elif score >= 20:
        return ORANGE
    else:
        return RED

def score_to_hex_ring(score: float) -> str:
    """Couleur de l'anneau de score."""
    if score >= 90:
        return NEON_GREEN
    elif score >= 70:
        return GOLD
    elif score >= 40:
        return ORANGE
    else:
        return RED

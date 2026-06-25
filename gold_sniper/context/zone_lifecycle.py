"""
Zone Lifecycle Engine — P1.26 / P1.27
======================================
Définit les états du cycle de vie d'un Order Block (ZoneState)
et la logique de classification contextuelle (shadow mode).

⚠️  Ce module est READ-ONLY pour les décisions de trading actuelles.
     Aucune fonction ici ne modifie hard_filter_pass, score ou reason
     de l'Agent 2. Il produit uniquement un enrichissement diagnostique.
"""

from __future__ import annotations

import sys
from typing import Optional, TypedDict

if sys.version_info >= (3, 8):
    from typing import Literal
else:
    from typing_extensions import Literal

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

ZoneState = Literal[
    "FRESH",
    "WICK_TAGGED",
    "PARTIALLY_MITIGATED",
    "MITIGATED",
    "CONSUMED",
    "INVALIDATED",
    "STALE",
    "FLIPPED_BREAKER",
]


class ZoneLifecycle(TypedDict):
    """Résultat d'une classification contextuelle d'une zone OB."""
    zone_id: str
    zone_type: Literal["OB_CONTINUATION", "OB_REVERSAL", "OTHER_POI"]
    direction: Literal["BULLISH", "BEARISH"]
    creation_tf: str
    created_at: str
    state: ZoneState
    touch_count: int
    deepest_penetration_pct: float
    mean_threshold_reached: bool
    close_inside_count: int
    reaction_displacement_score: float
    dOL_aligned: bool
    age_bars: int
    age_sessions: int
    invalidation_reason: Optional[str]
    legacy_fresh: bool
    legacy_mitigated: bool
    legacy_would_reject: bool


# ---------------------------------------------------------------------------
# Thresholds (shadow — ne servent pas encore aux décisions réelles)
# ---------------------------------------------------------------------------

# Un wick ou close dans la zone compte comme "tag"
ZONE_ENTRY_TOLERANCE = 0.0  # px au-delà de la bordure (0 = strict)

# Seuil de pénétration pour PARTIALLY_MITIGATED (% de hauteur de zone)
PARTIAL_MITIGATION_PCT = 0.50   # > 50 % → PARTIALLY_MITIGATED
FULL_MITIGATION_PCT    = 1.00   # 100 % → MITIGATED

# Seuil de fermeture inside pour CONSUMED
CLOSE_INSIDE_THRESHOLD = 2       # ≥ 2 closes inside → CONSUMED

# STALE : zone trop ancienne sans réaction
STALE_AGE_BARS = 96              # > 96 barres 15m (24 h) sans activation

# Invalidation : close au-delà de la zone dans la mauvaise direction
INVALIDATION_MARGIN_PCT = 0.10   # 10 % de l'ATR


# ---------------------------------------------------------------------------
# Fonction principale de classification
# ---------------------------------------------------------------------------

def classify_zone_lifecycle(
    zone: dict,
    candles: list,
    atr_14: float | None = None,
    age_bars: int | None = None,
    age_sessions: int | None = None,
    creation_tf: str = "15m",
) -> ZoneLifecycle:
    """
    Classifie une zone OB en état contextuel (shadow mode).

    Args:
        zone      : dict OB (doit avoir top, bottom, candle_index, type, fresh)
        candles   : liste des bougies 15m disponibles
        atr_14    : ATR 14 bougies en cours
        age_bars  : âge en barres (optionnel, calculé sinon depuis candle_index)
        age_sessions: âge en sessions (optionnel)
        creation_tf: timeframe de création

    Returns:
        ZoneLifecycle dict — JAMAIS utilisé pour les décisions actuelles.
    """
    zone_id   = str(zone.get("candle_index", 0))
    direction = zone.get("type", "BULLISH")   # "BULLISH" / "BEARISH"
    top    = float(zone.get("top", 0.0))
    bottom = float(zone.get("bottom", 0.0))
    ob_idx = zone.get("candle_index")
    height = top - bottom if top > bottom else 0.0

    # Calcul age
    if age_bars is None:
        age_bars = int(zone.get("age", 0))
    if age_sessions is None:
        # approximation : 1 session = 24 h = 96 barres 15m
        age_sessions = age_bars // 96

    # Zone type approximatif
    zone_type: Literal["OB_CONTINUATION", "OB_REVERSAL", "OTHER_POI"] = "OB_CONTINUATION"

    # ----------------------------------------------------------------
    # Analyse des bougies post-création
    # ----------------------------------------------------------------
    if isinstance(ob_idx, int) and ob_idx >= 0:
        post_candles = candles[ob_idx + 2:] if ob_idx + 2 < len(candles) else []
    else:
        post_candles = []

    touch_count = 0
    close_inside_count = 0
    deepest_pct = 0.0
    wick_tagged = False
    close_inside_happened = False
    invalidation_reason: Optional[str] = None
    last_close_beyond = False

    for c in post_candles:
        try:
            c_high  = float(c["high"])
            c_low   = float(c["low"])
            c_close = float(c["close"])
        except (KeyError, TypeError, ValueError):
            continue

        # Contact wick avec la zone
        wick_touches = c_high >= bottom and c_low <= top
        if wick_touches:
            touch_count += 1
            # Profondeur de pénétration
            if height > 0:
                if direction == "BULLISH":
                    penetration = (c_low - bottom) / height   # négatif = entré dans la zone
                    pen_pct = max(0.0, (top - c_low) / height)
                else:
                    pen_pct = max(0.0, (c_high - bottom) / height)
                deepest_pct = max(deepest_pct, pen_pct)
            wick_tagged = True

        # Close inside
        if bottom <= c_close <= top:
            close_inside_count += 1
            close_inside_happened = True

        # Invalidation : close au-delà dans mauvaise direction
        if direction == "BULLISH" and c_close < bottom:
            if not last_close_beyond:
                invalidation_reason = f"CLOSE_BELOW_BOTTOM at {c_close:.2f}"
            last_close_beyond = True
        elif direction == "BEARISH" and c_close > top:
            if not last_close_beyond:
                invalidation_reason = f"CLOSE_ABOVE_TOP at {c_close:.2f}"
            last_close_beyond = True

    # ----------------------------------------------------------------
    # Classification ZoneState (priorité décroissante)
    # ----------------------------------------------------------------
    legacy_is_fresh = bool(zone.get("fresh", False))

    if last_close_beyond:
        # Vérifier si ce pourrait être un Breaker (zone retournée)
        if touch_count >= 2:
            state: ZoneState = "FLIPPED_BREAKER"
        else:
            state = "INVALIDATED"

    elif close_inside_count >= CLOSE_INSIDE_THRESHOLD:
        state = "CONSUMED"

    elif deepest_pct >= FULL_MITIGATION_PCT:
        state = "MITIGATED"

    elif deepest_pct >= PARTIAL_MITIGATION_PCT:
        state = "PARTIALLY_MITIGATED"

    elif wick_tagged:
        state = "WICK_TAGGED"

    elif age_bars > STALE_AGE_BARS and not legacy_is_fresh:
        state = "STALE"

    else:
        state = "FRESH"

    # Score de réaction : pénalise les zones sans réaction visible
    reaction_displacement_score = _estimate_reaction(post_candles, top, bottom, direction, atr_14)

    # mean_threshold (> 50 % de la zone pénétrée)
    mean_threshold_reached = deepest_pct >= PARTIAL_MITIGATION_PCT

    return {
        "zone_id": zone_id,
        "zone_type": zone_type,
        "direction": direction,
        "creation_tf": creation_tf,
        "created_at": "",
        "state": state,
        "touch_count": touch_count,
        "deepest_penetration_pct": round(deepest_pct, 4),
        "mean_threshold_reached": mean_threshold_reached,
        "close_inside_count": close_inside_count,
        "reaction_displacement_score": round(reaction_displacement_score, 4),
        "dOL_aligned": False,   # rempli par couches supérieures
        "age_bars": age_bars,
        "age_sessions": age_sessions,
        "invalidation_reason": invalidation_reason,
        "legacy_fresh": legacy_is_fresh,
        "legacy_mitigated": not legacy_is_fresh,
        "legacy_would_reject": not legacy_is_fresh,
    }


def _estimate_reaction(
    post_candles: list,
    top: float,
    bottom: float,
    direction: str,
    atr_14: float | None,
) -> float:
    """
    Estime un score de réaction 0..1 après le premier touch de zone.
    0 = aucune réaction, 1 = forte réaction.
    Sert uniquement au diagnostic.
    """
    if not post_candles or not atr_14 or atr_14 <= 0:
        return 0.0

    # Cherche le 1er candle qui touche la zone
    first_touch_idx = None
    for i, c in enumerate(post_candles):
        try:
            if float(c["high"]) >= bottom and float(c["low"]) <= top:
                first_touch_idx = i
                break
        except (KeyError, TypeError, ValueError):
            continue

    if first_touch_idx is None or first_touch_idx + 1 >= len(post_candles):
        return 0.0

    # Déplacement sur les 3 bougies suivantes
    reactions = []
    for c in post_candles[first_touch_idx + 1: first_touch_idx + 4]:
        try:
            displacement = abs(float(c["close"]) - float(c["open"]))
            reactions.append(displacement)
        except (KeyError, TypeError, ValueError):
            continue

    if not reactions:
        return 0.0

    avg_reaction = sum(reactions) / len(reactions)
    return min(1.0, avg_reaction / atr_14)


# ---------------------------------------------------------------------------
# Utilitaire : shadow classification batch pour le rapport Replay
# ---------------------------------------------------------------------------

def classify_zone_pool_shadow(
    obs: list,
    candles: list,
    atr_14: float | None = None,
) -> list[ZoneLifecycle]:
    """
    Classifie toutes les zones d'un pool OB en shadow mode.
    Retourne la liste des ZoneLifecycle (diagnostique uniquement).
    """
    results = []
    for zone in obs:
        try:
            lc = classify_zone_lifecycle(zone, candles, atr_14=atr_14)
            results.append(lc)
        except Exception:
            pass
    return results


def zone_lifecycle_pool_summary(lifecycles: list[ZoneLifecycle]) -> dict:
    """
    Produit un résumé du pool de zones classifiées pour le rapport Replay.
    """
    if not lifecycles:
        return {
            "total": 0,
            "by_state": {},
            "killed_by_legacy_but_viable": 0,
            "mean_touch_count": 0.0,
            "mean_deepest_pct": 0.0,
            "mean_reaction_score": 0.0,
        }

    by_state: dict[str, int] = {}
    killed_viable = 0
    touch_counts = []
    deepest_pcts = []
    reaction_scores = []

    for lc in lifecycles:
        state = lc["state"]
        by_state[state] = by_state.get(state, 0) + 1

        # Zones que l'ancienne logique binaire tuait (legacy_would_reject == True)
        # mais que la nouvelle lecture considère encore exploitables
        if lc.get("legacy_would_reject", False) and state in ("WICK_TAGGED", "PARTIALLY_MITIGATED"):
            killed_viable += 1

        touch_counts.append(lc["touch_count"])
        deepest_pcts.append(lc["deepest_penetration_pct"])
        reaction_scores.append(lc["reaction_displacement_score"])

    n = len(lifecycles)
    return {
        "total": n,
        "by_state": by_state,
        "killed_by_legacy_but_viable": killed_viable,
        "mean_touch_count": round(sum(touch_counts) / n, 2),
        "mean_deepest_pct": round(sum(deepest_pcts) / n, 4),
        "mean_reaction_score": round(sum(reaction_scores) / n, 4),
    }

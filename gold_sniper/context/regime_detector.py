"""
Skeleton pour la détection de régime et phase de livraison.
"""

from .market_context import PrimaryRegime, DeliveryPhase

def detect_primary_regime(ctx: dict) -> PrimaryRegime:
    """
    Détermine le régime primaire (STRONG_UP, STRONG_DOWN, RANGE, REVERSAL).
    Actuellement en mode skeleton.
    """
    return "UNKNOWN"

def detect_delivery_phase(ctx: dict) -> DeliveryPhase:
    """
    Détermine la phase de livraison (ACCUMULATION, EXPANSION, RETRACEMENT, DISTRIBUTION).
    Actuellement en mode skeleton.
    """
    return "UNKNOWN"

# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — MATH UTILS
# ═══════════════════════════════════════════════════════════════════════════════
#
# Utilitaires mathématiques purs, sans état ni asynchronisme.
# Permet le calcul d'indicateurs classiques (ATR) et de patterns structurels
# (Swings) directement depuis les listes de bougies brutes.
#
# ═══════════════════════════════════════════════════════════════════════════════

from typing import List, Dict, Any, Optional

def detect_swings(candles: List[Dict[str, Any]], type_: str = 'high') -> List[Dict[str, Any]]:
    """
    Détecte les Swings (High ou Low) en utilisant un pattern fractal classique (Williams Fractal).
    
    Un Swing High est validé si :
        High[i] > High[i-1] AND High[i] > High[i+1]
    Un Swing Low est validé si :
        Low[i] < Low[i-1] AND Low[i] < Low[i+1]

    Args:
        candles: Liste chronologique de bougies (OHLCV). La dernière bougie (en cours) n'est pas évaluée.
        type_: 'high' ou 'low'

    Returns:
        Liste des bougies qui constituent un swing, avec leur index.
    """
    swings = []
    
    # Il faut au moins 3 bougies pour valider un swing au milieu
    if len(candles) < 3:
        return swings

    # On s'arrête à l'avant-dernière bougie car on a besoin de la bougie suivante (i+1)
    for i in range(1, len(candles) - 1):
        prev_c = candles[i - 1]
        curr_c = candles[i]
        next_c = candles[i + 1]
        
        if type_.lower() == 'high':
            if curr_c['high'] > prev_c['high'] and curr_c['high'] > next_c['high']:
                swings.append({
                    "index": i,
                    "time": curr_c['time'],
                    "level": curr_c['high']
                })
        elif type_.lower() == 'low':
            if curr_c['low'] < prev_c['low'] and curr_c['low'] < next_c['low']:
                swings.append({
                    "index": i,
                    "time": curr_c['time'],
                    "level": curr_c['low']
                })

    return swings

def get_latest_swing(candles: List[Dict[str, Any]], type_: str = 'high') -> Optional[Dict[str, Any]]:
    """Retourne le swing le plus récent."""
    swings = detect_swings(candles, type_)
    return swings[-1] if swings else None


def calculate_atr(candles: List[Dict[str, Any]], period: int = 14) -> float:
    """
    Calcule l'Average True Range (ATR) sur les N dernières périodes.
    
    Le True Range (TR) est le maximum entre :
        1. High - Low
        2. |High - Previous Close|
        3. |Low - Previous Close|

    Args:
        candles: Liste chronologique de bougies (OHLCV).
        period: Période de l'ATR (par défaut 14).

    Returns:
        La valeur de l'ATR en points (float).
    """
    if len(candles) <= period:
        return 0.0

    # On calcule l'ATR sur les `period` dernières bougies
    # Il nous faut `period + 1` bougies pour calculer le Previous Close du premier élément.
    recent_candles = candles[-(period + 1):]
    
    true_ranges = []
    for i in range(1, len(recent_candles)):
        curr_h = recent_candles[i]['high']
        curr_l = recent_candles[i]['low']
        prev_c = recent_candles[i - 1]['close']
        
        tr1 = curr_h - curr_l
        tr2 = abs(curr_h - prev_c)
        tr3 = abs(curr_l - prev_c)
        
        true_range = max(tr1, tr2, tr3)
        true_ranges.append(true_range)
    
    # ATR simple (SMA des True Ranges)
    if len(true_ranges) == 0:
        return 0.0
        
    return sum(true_ranges) / len(true_ranges)

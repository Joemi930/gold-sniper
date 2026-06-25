# gold_sniper/core/visual_layers.py
"""
Format standardisé des métadonnées visuelles publiées par les agents.

RÈGLE FONDAMENTALE :
  Les agents calculent les coordonnées en Python (CPU minimal).
  Le rendu (dessin des rectangles, lignes, etc.) est entièrement
  délégué au JavaScript du client (GPU du navigateur).
  Le moteur de trading ne touche jamais à ce code.

TYPES DE CALQUES :
  - rectangle   : zone de prix entre deux niveaux (OB, FVG, Asian Range, OTE)
  - hline       : ligne horizontale à un niveau précis (EQH, EQL, POC, VAH, VAL)
  - marker      : point discret sur une bougie (Sweep, CHoCH)
  - background  : coloration verticale d'une période (Kill Zones)
  - fibonacci   : groupe de niveaux Fibonacci avec labels
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
import time


# ── Types de base ────────────────────────────────────────────────────────────

@dataclass
class VisualRectangle:
    """
    Rectangle coloré entre deux niveaux de prix.
    Utilisé pour : Order Blocks, FVG, Asian Range, Zone OTE.

    Paramètres TradingView :
      time_start  : timestamp UNIX du début du rectangle (en secondes)
      time_end    : timestamp UNIX de la fin (None = s'étend jusqu'à mitigé)
      price_top   : niveau haut du rectangle
      price_bottom: niveau bas du rectangle
      color       : couleur CSS (rgba recommandé pour la transparence)
      border_color: couleur du contour
      label       : texte affiché (ex: "OB 87pts", "FVG", "OTE")
    """
    time_start:   int
    time_end:     Optional[int]
    price_top:    float
    price_bottom: float
    color:        str
    border_color: str
    label:        str
    layer_type:   str = "rectangle"
    id:           str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VisualHLine:
    """
    Ligne horizontale à un niveau de prix fixe.
    Utilisé pour : EQH, EQL, POC, VAH, VAL, niveaux Fibonacci.
    """
    time_start:  int
    price:       float
    color:       str
    style:       str          # "solid" | "dashed" | "dotted"
    width:       int          # 1, 2, ou 3
    label:       str
    label_side:  str = "right"  # "left" | "right"
    layer_type:  str = "hline"
    id:          str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VisualMarker:
    """
    Marqueur discret sur une bougie spécifique.
    Utilisé pour : confirmation de Sweep, CHoCH validé.
    """
    time:        int          # timestamp UNIX de la bougie cible
    price:       float        # prix de positionnement (High ou Low de la bougie)
    color:       str
    shape:       str          # "arrowUp" | "arrowDown" | "circle" | "square"
    size:        int          # 1 = petit, 2 = moyen, 3 = grand
    label:       str
    position:    str = "aboveBar"  # "aboveBar" | "belowBar" | "inBar"
    layer_type:  str = "marker"
    id:          str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VisualBackground:
    """
    Coloration verticale d'une fenêtre temporelle.
    Utilisé pour : Kill Zones, session asiatique.
    """
    time_start: int
    time_end:   int
    color:      str          # rgba avec transparence élevée (ex: "rgba(59,130,246,0.08)")
    label:      str
    layer_type: str = "background"
    id:         str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VisualFibLevel:
    """Un niveau Fibonacci individuel."""
    ratio:       float       # 0.618, 0.705, 0.786, etc.
    price:       float
    label:       str         # "61.8%", "70.5% (Sweet Spot)", "78.6%"
    color:       str
    is_ote_zone: bool = False  # True = dans la zone OTE — affiché différemment


@dataclass
class VisualFibonacci:
    """
    Groupe de niveaux Fibonacci avec rectangle OTE mis en évidence.
    Utilisé pour : Agent 4.
    """
    swing_low_time:  int
    swing_high_time: int
    swing_low_price: float
    swing_high_price: float
    direction:       str          # "LONG" | "SHORT"
    levels:          list         # Liste de VisualFibLevel
    ote_top:         float
    ote_bottom:      float
    equilibrium:     float
    layer_type:      str = "fibonacci"
    id:              str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ── Conteneur principal des calques ─────────────────────────────────────────

class VisualLayerStore:
    """
    Stocke tous les calques visuels actifs de chaque agent.

    L'orchestrateur et le dashboard_server lisent ce store pour
    construire le payload WebSocket.

    Mécanisme de nettoyage automatique :
      Les rectangles "mitigés" sont marqués avec time_end = now.
      Après 10 minutes, ils sont supprimés du store pour ne pas
      accumuler des milliers d'éléments en mémoire.
    """

    def __init__(self):
        self._layers: dict[str, list] = {
            "agent_2": [],   # OB + FVG
            "agent_3": [],   # EQH/EQL + Sweeps + Asian Range
            "agent_4": [],   # Fibonacci OTE
            "agent_5": [],   # CHoCH markers
            "agent_7": [],   # Kill Zones + Volume Profile
        }
        self._max_items_per_agent = 50

    def set_layers(self, agent_id: str, layers: list) -> None:
        """
        Remplace entièrement les calques d'un agent.
        Appelé par chaque agent après chaque cycle de calcul.
        """
        if agent_id not in self._layers:
            return

        # Générer des IDs uniques si absents
        for i, layer in enumerate(layers):
            if hasattr(layer, 'id') and not layer.id:
                layer.id = f"{agent_id}_{i}_{int(time.time())}"

        # Garder seulement les N plus récents
        self._layers[agent_id] = layers[-self._max_items_per_agent:]

    def get_all_as_dict(self) -> dict:
        """
        Sérialise tous les calques en dictionnaires pour le payload WebSocket.
        """
        result = {}
        for agent_id, layers in self._layers.items():
            result[agent_id] = []
            for layer in layers:
                if hasattr(layer, 'to_dict'):
                    result[agent_id].append(layer.to_dict())
                elif isinstance(layer, dict):
                    result[agent_id].append(layer)
        return result

    def clear_agent(self, agent_id: str) -> None:
        """Vide les calques d'un agent (ex: après un reset du marché)."""
        if agent_id in self._layers:
            self._layers[agent_id] = []


# Instance globale — importée par les agents et le dashboard_server
VISUAL_LAYERS = VisualLayerStore()

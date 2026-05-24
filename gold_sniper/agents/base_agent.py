# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.0 — BASE AGENT & AgentResult
# ═══════════════════════════════════════════════════════════════════════════════
#
# Contient deux composants critiques :
#   1. AgentResult — Dataclass standardisé, format de sortie de TOUS les agents V2.
#   2. BaseAgent   — Classe abstraite, fondation commune (accès BB + logger + heartbeat).
#
# Choix d'architecture : AgentResult est ici (et non dans core/) pour éviter
# les imports circulaires : blackboard.py importe AgentResult, pas l'inverse.
#
# Script 00 — Phase 1
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from utils.logger import get_logger


# ─────────────────────────────────────────────────────────────────────────────
# AgentResult — Format de sortie standardisé pour tous les agents V2.0
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentResult:
    """
    Format de sortie standardisé pour tous les agents V2.0.

    Champs obligatoires :
        agent_id        : Identifiant de l'agent (ex: "agent_1", "agent_5")
        score           : Score de 0.0 à 100.0
        hard_filter_pass: False = bloque TOUT le pipeline immédiatement
        direction       : "LONG" | "SHORT" | None
        reason          : Explication lisible pour le Decision Log

    Champs optionnels :
        payload         : Données métier supplémentaires (levels, zones, etc.)
        timestamp       : Horodatage UTC de la création (auto)
        veto            : True = bloque même si score global ≥ 85
    """
    agent_id: str
    score: float                        # [0.0 – 100.0]
    hard_filter_pass: bool              # False = bloque tout le pipeline immédiatement
    direction: Optional[str]            # "LONG" | "SHORT" | None
    reason: str                         # Explication lisible (pour Decision Log)
    payload: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    veto: bool = False                  # True = bloque même si score global ≥ 85
    risk_modifier: float = 1.0          # Modificateur de risque fourni par Agent 7

    def log_line(self) -> str:
        """Génère une ligne de log formatée avec emoji de statut."""
        star = "🔴" if self.score < 50 else ("🟡" if self.score < 75 else "🟢")
        return (
            f"{star} [{self.agent_id.upper()}] "
            f"score={self.score:.1f}/100 | "
            f"hf={'✅' if self.hard_filter_pass else '❌'} | "
            f"veto={'🚫' if self.veto else '✓'} | "
            f"dir={self.direction or 'N/A'} | {self.reason}"
        )

    @property
    def metadata(self) -> dict:
        """Alias de compatibilité vers payload pendant la migration V2."""
        return self.payload


# ─────────────────────────────────────────────────────────────────────────────
# BaseAgent — Classe abstraite commune à tous les agents
# ─────────────────────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """
    Classe de base pour tous les agents du Gold Sniper V2.0.

    Responsabilités :
      - Fournit l'accès standardisé au Blackboard et au logger.
      - Impose l'interface run() (boucle principale de l'agent).
      - Fournit un heartbeat() pour prouver que l'agent est vivant.

    Usage :
        class MyAgent(BaseAgent):
            async def run(self):
                while not self.blackboard.kill_event.is_set():
                    result = await self._calculate()
                    await self.blackboard.write_agent_result(self.name, result)
                    await self.heartbeat()
                    await asyncio.sleep(1)
    """

    def __init__(self, blackboard, name: str):
        """
        Initialise l'agent.

        Args:
            blackboard : Référence au Tableau Noir global (instance BlackBoard).
            name       : Nom système de l'agent (ex: "agent_1", "trade_manager").
                         Doit correspondre à une clé dans blackboard._data["agents"].
        """
        self.blackboard = blackboard
        self.name = name
        self.logger = get_logger()

    @abstractmethod
    async def run(self) -> None:
        """
        Boucle principale de l'agent — à implémenter obligatoirement.
        Doit boucler sur : while not self.blackboard.kill_event.is_set():
        """
        pass

    async def heartbeat(self) -> None:
        """
        Mécanisme de 'Preuve de vie'.
        Met à jour last_updated dans le Blackboard pour ce slot d'agent.
        En V2, utilise update_agent() directement pour la compatibilité.
        """
        now = datetime.now(tz=timezone.utc)
        try:
            await self.blackboard.update_agent(self.name, {"last_updated": now})
        except (KeyError, TypeError):
            # Sécurité si le nom ne correspond pas à un slot agent connu
            pass

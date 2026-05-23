# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — BASE AGENT
# ═══════════════════════════════════════════════════════════════════════════════
#
# Classe abstraite servant de fondation à tous les agents d'analyse.
# Force l'implémentation de la méthode run() et standardise le logging,
# l'accès au Blackboard, et le mécanisme de Heartbeat (preuve de vie).
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
from datetime import datetime, timezone
from abc import ABC, abstractmethod

from core.blackboard import BlackBoard
from utils.logger import get_logger


class BaseAgent(ABC):
    """
    Classe de base pour tous les agents du Gold Sniper.
    Standardise l'interface et le cycle de vie.
    """

    def __init__(self, blackboard: BlackBoard, name: str):
        """
        Initialise l'agent.
        
        Args:
            blackboard: Référence au Tableau Noir global.
            name: Nom système de l'agent (ex: "agent_1_meteo"). Doit correspondre
                  à une clé existante dans blackboard._data["agents"].
        """
        self.blackboard = blackboard
        self.name = name
        self.logger = get_logger()

    @abstractmethod
    async def run(self) -> None:
        """
        La boucle principale de l'agent.
        Doit être implémentée par la classe fille.
        Doit boucler sur `while not self.blackboard.kill_event.is_set():`
        """
        pass

    async def heartbeat(self) -> None:
        """
        Mécanisme de "Preuve de vie".
        Met à jour le timestamp de la dernière exécution réussie de l'agent
        directement dans le Blackboard.
        """
        now = datetime.now(tz=timezone.utc)
        path = f"agents.{self.name}.updated_at"
        
        try:
            await self.blackboard.write(path, now)
        except KeyError:
            # Sécurité au cas où l'agent écrit dans market_analysis au lieu de agents
            pass

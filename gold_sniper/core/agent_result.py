from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

@dataclass
class AgentResult:
    """Structure standardisée que CHAQUE agent doit retourner."""
    agent_id: str                    # "agent_1", "agent_2", etc.
    score: float                     # 0.0 à 100.0
    reason: str                      # Code lisible : "MTF_ALIGNED_LONG", "NO_SWEEP_YET"
    direction: Optional[str]         # "LONG" | "SHORT" | None
    is_hard_filter: bool             # True = si score=0, tout le pipeline s'arrête
    risk_modifier: float = 1.0       # Modificateur de taille de position (1.0 = normal)
    metadata: dict = field(default_factory=dict)  # Données brutes pour audit
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

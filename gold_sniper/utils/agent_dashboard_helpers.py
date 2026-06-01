"""Resultats AgentResult standardises pour pulse dashboard (IDLE / WAITING)."""
from __future__ import annotations

from typing import Any, Optional

from agents.base_agent import AgentResult


def idle_result(
    agent_id: str,
    reason: str = "IDLE",
    score: float = 0.0,
    direction: Optional[str] = None,
    hard_filter_pass: bool = True,
    payload: dict[str, Any] | None = None,
) -> AgentResult:
    """Resultat de cycle sans changement de score — maintient le WebSocket actif."""
    return AgentResult(
        agent_id=agent_id,
        score=score,
        hard_filter_pass=hard_filter_pass,
        direction=direction,
        reason=reason,
        payload=dict(payload or {}),
    )

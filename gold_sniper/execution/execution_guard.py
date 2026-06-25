from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import config
from safety.research_branch_guard import evaluate_broker_write_request


@dataclass(frozen=True)
class ExecutionDecision:
    allowed: bool
    reason: str
    run_mode: str
    broker_writes_allowed: bool
    action: str


class ExecutionGuard:
    """Central broker-write guard. It fails closed by default."""

    VALID_ACTIONS = {
        "OPEN_ORDER",
        "PARTIAL_CLOSE",
        "MODIFY_SLTP",
        "TRAILING_STOP",
        "RECOVERY_CLOSE",
        "EMERGENCY_CLOSE",
    }

    def __init__(self, blackboard):
        self.blackboard = blackboard

    def can_send_broker_order(
        self,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> ExecutionDecision:
        del context  # reserved for future audit metadata
        run_mode = str(getattr(config, "RUN_MODE", "REPLAY") or "REPLAY").upper()
        broker_writes_allowed = bool(getattr(config, "ALLOW_BROKER_WRITES", False))
        valid_modes = set(getattr(config, "VALID_RUN_MODES", {"LIVE", "PAPER", "REPLAY", "BACKTEST"}))

        if action not in self.VALID_ACTIONS:
            return self._deny(action, run_mode, broker_writes_allowed, "UNKNOWN_ACTION")
        if run_mode not in valid_modes:
            return self._deny(action, run_mode, broker_writes_allowed, "INVALID_RUN_MODE")
        research_decision = evaluate_broker_write_request(
            run_mode=run_mode,
            broker_writes_allowed=broker_writes_allowed,
        )
        if not research_decision.allowed:
            return self._deny(
                action,
                run_mode,
                broker_writes_allowed,
                research_decision.reason,
            )
        if run_mode != "LIVE":
            return self._deny(action, run_mode, broker_writes_allowed, "RUN_MODE_NOT_LIVE")
        if not broker_writes_allowed:
            return self._deny(action, run_mode, broker_writes_allowed, "BROKER_WRITES_DISABLED")

        data = self._blackboard_data()
        meta = data.get("meta", {}) or {}
        control = data.get("control", {}) or {}
        agents = data.get("agents", {}) or {}
        agent_6 = agents.get("agent_6", {}) or {}
        risk_manager = agents.get("risk_manager", {}) or {}

        if self._kill_switch_active(meta):
            return self._deny(action, run_mode, broker_writes_allowed, "KILL_SWITCH_ACTIVE")
        if bool(control.get("paused", False)):
            return self._deny(action, run_mode, broker_writes_allowed, "TRADING_PAUSED")
        if bool(agent_6.get("veto", False)):
            return self._deny(action, run_mode, broker_writes_allowed, "AGENT_6_VETO")
        if bool(risk_manager.get("veto", False)):
            return self._deny(action, run_mode, broker_writes_allowed, "RISK_MANAGER_VETO")
        if bool(risk_manager.get("paper_mode_forced", False)):
            return self._deny(action, run_mode, broker_writes_allowed, "PAPER_MODE_FORCED")

        return ExecutionDecision(
            allowed=True,
            reason="ALLOWED",
            run_mode=run_mode,
            broker_writes_allowed=broker_writes_allowed,
            action=action,
        )

    def _blackboard_data(self) -> dict[str, Any]:
        if self.blackboard is None:
            return {}
        try:
            data = self.blackboard.get_all()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _kill_switch_active(self, meta: dict[str, Any]) -> bool:
        if bool(meta.get("kill_switch", False)):
            return True
        kill_event = getattr(self.blackboard, "kill_event", None)
        if kill_event is None:
            return False
        try:
            return bool(kill_event.is_set())
        except Exception:
            return True

    @staticmethod
    def _deny(
        action: str,
        run_mode: str,
        broker_writes_allowed: bool,
        reason: str,
    ) -> ExecutionDecision:
        return ExecutionDecision(
            allowed=False,
            reason=reason,
            run_mode=run_mode,
            broker_writes_allowed=broker_writes_allowed,
            action=action,
        )

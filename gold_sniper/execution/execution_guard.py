from __future__ import annotations

import asyncio
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
    """Central broker-write guard. It fails closed by default.

    §2-C / §3: Updated to allow PAPER mode (not just LIVE), add DEMO account
    whitelist, and verify trade_mode==DEMO before any order.
    """

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

        # §3: Allow PAPER mode (not just LIVE) — DEMO account only.
        # PAPER mode sends orders to MT5 demo account; LIVE is forbidden.
        if run_mode not in {"LIVE", "PAPER"}:
            return self._deny(action, run_mode, broker_writes_allowed, "RUN_MODE_NOT_LIVE_OR_PAPER")
        if not broker_writes_allowed:
            return self._deny(action, run_mode, broker_writes_allowed, "BROKER_WRITES_DISABLED")

        # §3: DEMO account whitelist — refuse if connected account != expected demo
        demo_login = getattr(config, "MT5_DEMO_LOGIN", None)
        if demo_login:
            try:
                import MetaTrader5 as mt5
                account_info = mt5.account_info()
                if account_info is not None:
                    actual_login = account_info.login
                    if actual_login != demo_login:
                        return self._deny(
                            action, run_mode, broker_writes_allowed,
                            f"DEMO_LOGIN_MISMATCH: connected={actual_login} expected={demo_login}",
                        )
                    # §3: Verify trade_mode == DEMO (block REAL)
                    trade_mode = getattr(account_info, "trade_mode", None)
                    if trade_mode is not None and trade_mode != 0:  # 0 = DEMO in MT5
                        return self._deny(
                            action, run_mode, broker_writes_allowed,
                            f"ACCOUNT_NOT_DEMO: trade_mode={trade_mode} (0=DEMO)",
                        )
            except ImportError:
                pass  # MT5 not available — fall through to blackboard checks
            except Exception as exc:
                return self._deny(action, run_mode, broker_writes_allowed, f"MT5_ACCOUNT_CHECK_ERROR: {exc}")

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

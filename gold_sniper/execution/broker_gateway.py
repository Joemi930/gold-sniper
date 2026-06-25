from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from execution.mt5_runtime import mt5

from execution.execution_guard import ExecutionDecision, ExecutionGuard
from utils.logger import get_logger


class BrokerAction:
    OPEN_ORDER = "OPEN_ORDER"
    PARTIAL_CLOSE = "PARTIAL_CLOSE"
    MODIFY_SLTP = "MODIFY_SLTP"
    TRAILING_STOP = "TRAILING_STOP"
    RECOVERY_CLOSE = "RECOVERY_CLOSE"
    EMERGENCY_CLOSE = "EMERGENCY_CLOSE"


@dataclass
class BrokerOrderResult:
    retcode: Any = None
    order: int = 0
    price: float = 0.0
    comment: str = ""
    allowed: bool = False
    simulated: bool = False
    decision: ExecutionDecision | None = None


class MT5BrokerAdapter:
    async def send_order(self, request: dict[str, Any]):
        if mt5 is None:
            return BrokerOrderResult(comment="MetaTrader5 unavailable", allowed=False)
        return await asyncio.to_thread(mt5.order_send, request)


class SimulatedBrokerAdapter:
    async def send_order(self, request: dict[str, Any], decision: ExecutionDecision) -> BrokerOrderResult:
        del request
        return BrokerOrderResult(
            comment=f"Broker write blocked: {decision.reason}",
            allowed=False,
            simulated=True,
            decision=decision,
        )


class BrokerGateway:
    """Single write path for broker order requests."""

    def __init__(
        self,
        blackboard,
        guard: ExecutionGuard | None = None,
        mt5_adapter: MT5BrokerAdapter | None = None,
        simulated_adapter: SimulatedBrokerAdapter | None = None,
    ):
        self.blackboard = blackboard
        self.guard = guard or ExecutionGuard(blackboard)
        self.mt5_adapter = mt5_adapter or MT5BrokerAdapter()
        self.simulated_adapter = simulated_adapter or SimulatedBrokerAdapter()
        self.logger = get_logger()

    async def send_order(
        self,
        action: str,
        request: dict[str, Any],
        context: dict[str, Any] | None = None,
    ):
        decision = self.guard.can_send_broker_order(action, context=context)
        if not decision.allowed:
            self.logger.warning(
                "Broker write blocked | "
                f"action={action} run_mode={decision.run_mode} "
                f"allow_writes={decision.broker_writes_allowed} reason={decision.reason}"
            )
            return await self.simulated_adapter.send_order(request, decision)

        return await self.mt5_adapter.send_order(request)

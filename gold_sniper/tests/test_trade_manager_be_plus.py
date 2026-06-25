from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import patch

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

fake = types.SimpleNamespace()
fake.ORDER_TYPE_BUY = 0
fake.ORDER_TYPE_SELL = 1
fake.POSITION_TYPE_BUY = 0
fake.TRADE_ACTION_DEAL = 1
fake.TRADE_ACTION_SLTP = 2
fake.ORDER_TIME_GTC = 0
fake.ORDER_FILLING_IOC = 0
fake.TRADE_RETCODE_DONE = 10009
fake.last_error = lambda: (0, "OK")
fake.history_deals_get = lambda *args, **kwargs: []
fake.account_info = lambda: None
fake.symbol_info = lambda symbol: None
fake.symbol_info_tick = lambda symbol: None
sys.modules["MetaTrader5"] = fake

from core.blackboard import BlackBoard
from execution.broker_gateway import BrokerAction
from execution.trade_manager import TradeManager


class FakeGateway:
    def __init__(self, *, partial_allowed: bool = True, partial_retcode: int | None = fake.TRADE_RETCODE_DONE):
        self.calls: list[tuple[str, dict]] = []
        self.partial_allowed = partial_allowed
        self.partial_retcode = partial_retcode

    async def send_order(self, action: str, request: dict, context: dict | None = None):
        self.calls.append((action, request))
        if action == BrokerAction.PARTIAL_CLOSE and not self.partial_allowed:
            return types.SimpleNamespace(allowed=False)
        if action == BrokerAction.PARTIAL_CLOSE:
            return types.SimpleNamespace(allowed=True, retcode=self.partial_retcode)
        return types.SimpleNamespace(allowed=True, retcode=fake.TRADE_RETCODE_DONE)


class TestTradeManagerBePlus(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        fake.positions_get = lambda **kwargs: [types.SimpleNamespace(volume=1.0, profit=20.0)]

    async def _manager_with_trade(self, trade: dict, tick: dict, gateway: FakeGateway) -> tuple[TradeManager, BlackBoard]:
        board = BlackBoard()
        board._data["active_trades"] = {trade["ticket"]: dict(trade)}
        board._data["market_data"]["current_tick"] = dict(tick)
        manager = TradeManager(board)
        manager.broker_gateway = gateway
        return manager, board

    async def test_buy_tp1_moves_sl_to_be_plus(self) -> None:
        gateway = FakeGateway()
        manager, board = await self._manager_with_trade(
            {
                "ticket": 1,
                "type": "BUY",
                "entry_price": 100.0,
                "original_sl": 95.0,
                "current_sl": 95.0,
                "tp": 110.0,
                "tp1": 105.0,
                "partial_closed": False,
            },
            {"bid": 105.5, "ask": 105.6},
            gateway,
        )

        with patch("execution.trade_manager.send_discord_notification", autospec=True):
            await manager.manage_active_trades()

        modify = [call for call in gateway.calls if call[0] == BrokerAction.MODIFY_SLTP][0][1]
        self.assertEqual(modify["sl"], 100.5)
        self.assertTrue(board._data["active_trades"][1]["be_plus_activated"])

    async def test_sell_tp1_moves_sl_to_be_plus(self) -> None:
        gateway = FakeGateway()
        manager, _board = await self._manager_with_trade(
            {
                "ticket": 2,
                "type": "SELL",
                "entry_price": 100.0,
                "original_sl": 105.0,
                "current_sl": 105.0,
                "tp": 90.0,
                "tp1": 95.0,
                "partial_closed": False,
            },
            {"bid": 94.9, "ask": 95.0},
            gateway,
        )

        with patch("execution.trade_manager.send_discord_notification", autospec=True):
            await manager.manage_active_trades()

        modify = [call for call in gateway.calls if call[0] == BrokerAction.MODIFY_SLTP][0][1]
        self.assertEqual(modify["sl"], 99.5)

    async def test_partial_close_failure_does_not_move_sl(self) -> None:
        gateway = FakeGateway(partial_retcode=0)
        manager, board = await self._manager_with_trade(
            {
                "ticket": 3,
                "type": "BUY",
                "entry_price": 100.0,
                "original_sl": 95.0,
                "current_sl": 95.0,
                "tp": 110.0,
                "tp1": 105.0,
                "partial_closed": False,
            },
            {"bid": 105.5, "ask": 105.6},
            gateway,
        )

        await manager.manage_active_trades()

        self.assertNotIn(BrokerAction.MODIFY_SLTP, [action for action, _request in gateway.calls])
        self.assertFalse(board._data["active_trades"][3]["partial_closed"])

    async def test_execution_guard_block_keeps_trade_tracking(self) -> None:
        gateway = FakeGateway(partial_allowed=False)
        manager, board = await self._manager_with_trade(
            {
                "ticket": 4,
                "type": "BUY",
                "entry_price": 100.0,
                "original_sl": 95.0,
                "current_sl": 95.0,
                "tp": 110.0,
                "tp1": 105.0,
                "partial_closed": False,
            },
            {"bid": 105.5, "ask": 105.6},
            gateway,
        )

        await manager.manage_active_trades()

        self.assertIn(4, board._data["active_trades"])
        self.assertFalse(board._data["active_trades"][4]["partial_closed"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import sys
import types
import unittest

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

ORDER_SEND_CALLS: list[dict] = []


fake = types.SimpleNamespace()
fake.ORDER_TYPE_BUY = 0
fake.ORDER_TYPE_SELL = 1
fake.POSITION_TYPE_BUY = 0
fake.TRADE_ACTION_DEAL = 1
fake.TRADE_ACTION_SLTP = 2
fake.ORDER_TIME_GTC = 0
fake.ORDER_FILLING_IOC = 0
fake.TRADE_RETCODE_DONE = 10009
fake.order_send = lambda request: ORDER_SEND_CALLS.append(request)
fake.last_error = lambda: (0, "OK")
fake.positions_get = lambda **kwargs: []
fake.history_deals_get = lambda *args, **kwargs: []
fake.account_info = lambda: None
fake.symbol_info = lambda symbol: None
fake.symbol_info_tick = lambda symbol: None
sys.modules["MetaTrader5"] = fake

import config
from core.blackboard import BlackBoard
from execution.trade_manager import TradeManager


SIGNAL = {
    "signal": "BUY",
    "entry_price": 2000.0,
    "stop_loss": 1990.0,
    "take_profit": 2020.0,
}


class TestTradeManagerVeto(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        ORDER_SEND_CALLS.clear()
        self._run_mode = config.RUN_MODE
        self._allow = config.ALLOW_BROKER_WRITES
        config.RUN_MODE = "LIVE"
        config.ALLOW_BROKER_WRITES = True

    def tearDown(self) -> None:
        config.RUN_MODE = self._run_mode
        config.ALLOW_BROKER_WRITES = self._allow

    async def _assert_blocks_with_update(self, path: str, updates: dict) -> None:
        board = BlackBoard()
        board._data["trade_signals"] = dict(SIGNAL)
        board.read_sync(path).update(updates)

        result = await TradeManager(board).place_order(dict(SIGNAL))

        self.assertFalse(result)
        self.assertEqual(ORDER_SEND_CALLS, [])
        self.assertEqual(board.read_sync("trade_signals"), {})

    async def test_agent_6_veto_after_signal_blocks_order(self) -> None:
        await self._assert_blocks_with_update("agents.agent_6", {"veto": True})

    async def test_risk_manager_veto_after_signal_blocks_order(self) -> None:
        await self._assert_blocks_with_update("agents.risk_manager", {"veto": True})

    async def test_paper_mode_forced_blocks_order(self) -> None:
        await self._assert_blocks_with_update("agents.risk_manager", {"paper_mode_forced": True})


if __name__ == "__main__":
    unittest.main()

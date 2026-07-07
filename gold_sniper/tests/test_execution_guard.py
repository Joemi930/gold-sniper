from __future__ import annotations

import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

import config
from core.blackboard import BlackBoard
from execution.execution_guard import ExecutionGuard


class TestExecutionGuard(unittest.TestCase):
    def setUp(self) -> None:
        self._run_mode = config.RUN_MODE
        self._allow = config.ALLOW_BROKER_WRITES
        self._branch = os.environ.get("GOLD_SNIPER_BRANCH")
        os.environ["GOLD_SNIPER_BRANCH"] = "main"

    def tearDown(self) -> None:
        config.RUN_MODE = self._run_mode
        config.ALLOW_BROKER_WRITES = self._allow
        if self._branch is None:
            os.environ.pop("GOLD_SNIPER_BRANCH", None)
        else:
            os.environ["GOLD_SNIPER_BRANCH"] = self._branch

    def _decision(self, run_mode: str, allow: bool, board: BlackBoard | None = None):
        config.RUN_MODE = run_mode
        config.ALLOW_BROKER_WRITES = allow
        return ExecutionGuard(board or BlackBoard()).can_send_broker_order("OPEN_ORDER")

    def test_non_live_modes_block_broker_writes(self) -> None:
        # §3: PAPER mode is now ALLOWED (DEMO account with broker writes).
        # Only REPLAY and BACKTEST are still blocked.
        for mode in ("REPLAY", "BACKTEST"):
            with self.subTest(mode=mode):
                decision = self._decision(mode, False)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "RUN_MODE_NOT_LIVE_OR_PAPER")

    def test_live_requires_explicit_broker_write_flag(self) -> None:
        decision = self._decision("LIVE", False)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "BROKER_WRITES_DISABLED")

    def test_live_allows_only_when_runtime_state_is_clear(self) -> None:
        decision = self._decision("LIVE", True)
        self.assertTrue(decision.allowed)

    def test_veto_pause_kill_and_paper_forced_block(self) -> None:
        cases = [
            ("control", {"paused": True}, "TRADING_PAUSED"),
            ("agents.agent_6", {"veto": True}, "AGENT_6_VETO"),
            ("agents.risk_manager", {"veto": True}, "RISK_MANAGER_VETO"),
            ("agents.risk_manager", {"paper_mode_forced": True}, "PAPER_MODE_FORCED"),
            ("meta", {"kill_switch": True}, "KILL_SWITCH_ACTIVE"),
        ]
        for path, updates, reason in cases:
            with self.subTest(reason=reason):
                board = BlackBoard()
                target = board.read_sync(path)
                target.update(updates)
                decision = self._decision("LIVE", True, board)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, reason)

    def test_invalid_run_mode_blocks(self) -> None:
        decision = self._decision("UNKNOWN", True)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "INVALID_RUN_MODE")

    def test_no_direct_mt5_order_send_outside_broker_gateway(self) -> None:
        root = Path(__file__).resolve().parents[1]
        gateway = root / "execution" / "broker_gateway.py"
        offenders: list[str] = []

        for path in root.rglob("*.py"):
            if path == gateway or "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "order_send"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "mt5"
                ):
                    offenders.append(str(path.relative_to(root)))

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

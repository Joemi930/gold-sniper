from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPLAY = ROOT / "gold_sniper" / "replay"

FORBIDDEN_IMPORTS = {
    "MetaTrader5",
    "execution",
    "execution.broker_gateway",
    "execution.trade_manager",
    "core.orchestrator",
    "main",
    "discord",
    "websockets",
    "requests",
    "aiohttp",
}


class TestP1ReplayStaticGuards(unittest.TestCase):
    def test_replay_has_no_forbidden_imports(self):
        offenders = []
        for path in REPLAY.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
            bad = [
                module for module in found
                if module in FORBIDDEN_IMPORTS or any(module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS)
            ]
            if bad:
                offenders.append((path.relative_to(ROOT).as_posix(), bad))
        self.assertEqual([], offenders)

    def test_replay_engine_does_not_reference_trade_signals(self):
        text = (REPLAY / "replay_engine.py").read_text(encoding="utf-8", errors="ignore")
        forbidden_snippets = [
            "trade_signals",
            'write("trade_signals"',
            "write('trade_signals'",
            'read_sync("trade_signals"',
            "read_sync('trade_signals'",
            'blackboard.write("trade_signals"',
            "blackboard.write('trade_signals'",
        ]
        for snippet in forbidden_snippets:
            self.assertNotIn(snippet, text)

    def test_replay_engine_decision_event_does_not_emit_signal_field(self):
        text = (REPLAY / "replay_engine.py").read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn('"signal": decision.get("signal")', text)
        self.assertNotIn("'signal': decision.get('signal')", text)

    def test_replay_engine_uses_p1_decision_recorder(self):
        text = (REPLAY / "replay_engine.py").read_text(encoding="utf-8", errors="ignore")
        self.assertIn("on_p1_decision", text)

    def test_run_replay_does_not_import_main(self):
        text = (REPLAY / "run_replay.py").read_text(encoding="utf-8", errors="ignore")
        self.assertNotIn("import main", text)
        self.assertNotIn("from main", text)


if __name__ == "__main__":
    unittest.main()

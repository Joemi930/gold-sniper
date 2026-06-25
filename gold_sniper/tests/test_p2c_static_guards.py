from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "replay" / "execution_model.py",
    ROOT / "replay" / "fill_model.py",
    ROOT / "replay" / "trade_journal.py",
    ROOT / "replay" / "simulated_trade_manager.py",
]
FORBIDDEN = [
    "MetaTrader5",
    "order_send",
    "broker_gateway",
    "execution.",
    "core.orchestrator",
    "requests",
    "aiohttp",
    "websockets",
]


class TestP2cStaticGuards(unittest.TestCase):
    def test_forbidden_live_or_network_tokens_absent(self):
        offenders = []
        for path in FILES:
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN:
                if token in text:
                    offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()

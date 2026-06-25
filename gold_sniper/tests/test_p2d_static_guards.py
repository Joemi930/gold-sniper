from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "strategy" / "readiness.py",
    ROOT / "strategy" / "professional_decision_engine.py",
    ROOT / "strategy" / "scorecard.py",
    ROOT / "replay" / "evidence_builder.py",
    ROOT / "replay" / "replay_metrics.py",
]
FORBIDDEN = ["MetaTrader5", "order_send", "broker_gateway", "execution.", "requests", "aiohttp", "websockets"]


class TestP2dStaticGuards(unittest.TestCase):
    def test_forbidden_tokens_absent(self):
        offenders = []
        for path in FILES:
            text = path.read_text(encoding="utf-8")
            for token in FORBIDDEN:
                if token in text:
                    offenders.append(f"{path}:{token}")

        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()

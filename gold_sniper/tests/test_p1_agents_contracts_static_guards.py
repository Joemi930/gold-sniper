from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

AGENT_FILES = [
    REPO_ROOT / "gold_sniper" / "agents" / "agent_1_meteo.py",
    REPO_ROOT / "gold_sniper" / "agents" / "agent_2_cartographe.py",
    REPO_ROOT / "gold_sniper" / "agents" / "agent_3_liquidite.py",
    REPO_ROOT / "gold_sniper" / "agents" / "agent_4_fibonacci.py",
    REPO_ROOT / "gold_sniper" / "agents" / "agent_5_microscope.py",
    REPO_ROOT / "gold_sniper" / "agents" / "agent_6_sentinelle.py",
    REPO_ROOT / "gold_sniper" / "agents" / "agent_7_chronos.py",
]

EVIDENCE_BUILDER = REPO_ROOT / "gold_sniper" / "replay" / "evidence_builder.py"

FORBIDDEN_IMPORTS = {
    "MetaTrader5",
    "execution",
    "execution.broker_gateway",
    "execution.trade_manager",
    "core.orchestrator",
    "core.trade_manager",
    "dotenv",
    "subprocess",
    "socket",
    "requests",
    "aiohttp",
    "discord",
    "websockets",
}


class TestP1AgentsContractsStaticGuards(unittest.TestCase):
    def test_evidence_builder_has_no_forbidden_imports(self):
        tree = ast.parse(EVIDENCE_BUILDER.read_text(encoding="utf-8"))
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
        self.assertEqual([], bad)

    def test_agent_observation_builders_exist(self):
        expected = {
            "agent_1_meteo.py": "build_agent_1_observation",
            "agent_2_cartographe.py": "build_agent_2_observation",
            "agent_3_liquidite.py": "build_agent_3_observation",
            "agent_4_fibonacci.py": "build_agent_4_observation",
            "agent_5_microscope.py": "build_agent_5_observation",
            "agent_6_sentinelle.py": "build_agent_6_observation",
            "agent_7_chronos.py": "build_agent_7_observation",
        }
        for path in AGENT_FILES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertIn(f"def {expected[path.name]}", text)

    def test_agent_observation_builders_do_not_reference_broker_tokens(self):
        forbidden = {"MetaTrader5", "order_send", "TradeManager", "broker_gateway", "OPEN_ORDER", "CLOSE_ORDER"}
        offenders = []
        for path in AGENT_FILES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in forbidden:
                if token in text:
                    offenders.append((path.relative_to(REPO_ROOT).as_posix(), token))
        self.assertEqual([], offenders)

    def test_evidence_builder_declares_forbidden_key_filter(self):
        text = EVIDENCE_BUILDER.read_text(encoding="utf-8", errors="ignore")
        self.assertIn("FORBIDDEN_EVIDENCE_KEYS", text)
        self.assertIn("validate_evidence_bundle", text)


if __name__ == "__main__":
    unittest.main()

"""P2-A Connectivity Static Guards — verify no broker/live/MT5 leakage in P2-A code."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_IMPORTS = {
    "MetaTrader5",
    "execution",
    "execution.broker_gateway",
    "execution.trade_manager",
    "core.orchestrator",
    "main",
    "requests",
    "aiohttp",
    "websockets",
    "discord",
}

P2A_TARGETS = [
    "gold_sniper/agents/agent_2_cartographe.py",
    "gold_sniper/replay/evidence_builder.py",
    "gold_sniper/replay/decision_pipeline.py",
    "gold_sniper/replay/replay_metrics.py",
]


class TestP2aConnectivityStaticGuards(unittest.TestCase):
    def test_p2a_forbidden_imports(self):
        bad_imports = []
        for rel_path in P2A_TARGETS:
            path = ROOT / rel_path
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(text, filename=str(path))
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
            bad = [
                module for module in found
                if module in FORBIDDEN_IMPORTS or any(
                    module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS
                )
            ]
            if bad:
                bad_imports.append((path.relative_to(ROOT).as_posix(), sorted(bad)))
        self.assertEqual([], bad_imports,
                         f"P2A forbidden imports found: {bad_imports}")

    def test_p2a_forbidden_tokens(self):
        bad_tokens = []
        for rel_path in P2A_TARGETS:
            path = ROOT / rel_path
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in ("MetaTrader5", "order_send", "broker_gateway"):
                if token in text:
                    bad_tokens.append((path.relative_to(ROOT).as_posix(), token))
        self.assertEqual([], bad_tokens,
                         f"P2A forbidden tokens found: {bad_tokens}")

    def test_p2a_forbidden_keys_in_p2a_poi_normalization(self):
        """Les helpers P2-A n'introduisent pas de clé trade_signal/entry/sl/tp/lot."""
        from agents.agent_2_cartographe import _p2a_clean_poi, _p2a_execution_readiness, _p2a_price_bounds, _p2a_normalize_lifecycle, _p2a_normalize_poi_type

        # Test _p2a_clean_poi
        poi = _p2a_clean_poi({
            "poi_type_normalized": "OB",
            "lifecycle_normalized": "FRESH",
            "high": 2405.0,
            "low": 2400.0,
        }, source="test")

        forbidden = {"entry", "sl", "tp", "lot", "trade_signal", "order_send",
                     "broker", "signal", "entry_price", "stop_loss", "take_profit"}
        for key in poi:
            self.assertNotIn(str(key).lower(), forbidden,
                            f"Forbidden key '{key}' in _p2a_clean_poi output")

        # Verify execution_readiness is allowed
        self.assertIn("execution_readiness", poi)

        # Normalization helpers return clean strings
        self.assertEqual(_p2a_normalize_poi_type("OB"), "OB")
        self.assertEqual(_p2a_normalize_poi_type("FVG_CONTINUATION"), "FVG")
        self.assertEqual(_p2a_normalize_lifecycle("FRESH"), "FRESH")
        self.assertEqual(_p2a_normalize_lifecycle("PARTIALLY_MITIGATED"), "PARTIAL")
        self.assertEqual(_p2a_normalize_lifecycle("INVALIDATED"), "INVALIDATED")

    def test_build_agent2_observation_no_execution_keys(self):
        """build_agent_2_observation ne produit pas de clés d'exécution."""
        from agents.base_agent import AgentResult
        from agents.agent_2_cartographe import build_agent_2_observation

        result = AgentResult(
            agent_id="agent_2",
            score=80,
            hard_filter_pass=True,
            direction="LONG",
            reason="TEST",
            payload={
                "p2a_poi_connectivity": {
                    "schema_version": "p2a.poi_connectivity.v1",
                    "poi_candidates": [{
                        "poi_type_normalized": "OB",
                        "lifecycle_normalized": "FRESH",
                        "price_bounds": {"low": 2400.0, "high": 2405.0},
                        "execution_readiness": "READY",
                    }],
                    "selected_poi": {
                        "poi_type_normalized": "OB",
                        "lifecycle_normalized": "FRESH",
                        "price_bounds": {"low": 2400.0, "high": 2405.0},
                        "execution_readiness": "READY",
                    },
                    "audit": {},
                }
            },
        )
        obs = build_agent_2_observation(result)
        forbidden = {"entry", "sl", "tp", "lot", "trade_signal", "order_send",
                     "broker", "signal", "entry_price", "stop_loss", "take_profit",
                     "position_size", "lots", "order_type"}
        for key in self._walk_keys(obs.payload):
            self.assertNotIn(key, forbidden,
                             f"Forbidden key '{key}' in build_agent_2_observation payload")

    def test_execution_readiness_is_allowed(self):
        """execution_readiness est autorisée comme clé P2-A légitime."""
        from agents.base_agent import AgentResult
        from agents.agent_2_cartographe import build_agent_2_observation

        result = AgentResult(
            agent_id="agent_2",
            score=80,
            hard_filter_pass=True,
            direction="LONG",
            reason="TEST",
            payload={
                "p2a_poi_connectivity": {
                    "schema_version": "p2a.poi_connectivity.v1",
                    "selected_poi": {
                        "poi_type_normalized": "OB",
                        "lifecycle_normalized": "FRESH",
                        "price_bounds": {"low": 2400.0, "high": 2405.0},
                        "execution_readiness": "READY",
                    },
                    "audit": {},
                }
            },
        )
        obs = build_agent_2_observation(result)
        self.assertEqual(obs.payload["execution_readiness"], "READY")
        # Vérifie que la clé est bien présente (P2-A l'exige)
        keys = list(self._walk_keys(obs.payload))
        self.assertIn("execution_readiness", keys)

    @staticmethod
    def _walk_keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield str(k).lower()
                yield from TestP2aConnectivityStaticGuards._walk_keys(v)
        elif isinstance(value, list):
            for item in value:
                yield from TestP2aConnectivityStaticGuards._walk_keys(item)


if __name__ == "__main__":
    unittest.main()

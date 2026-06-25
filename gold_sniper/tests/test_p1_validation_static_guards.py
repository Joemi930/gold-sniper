from __future__ import annotations
import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATION = ROOT / "gold_sniper" / "validation"

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


class TestP1ValidationStaticGuards(unittest.TestCase):
    def test_validation_has_no_forbidden_imports(self):
        offenders = []
        for path in VALIDATION.rglob("*.py"):
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

    def test_validation_does_not_write_to_execution_paths(self):
        text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in VALIDATION.rglob("*.py"))
        self.assertNotIn("order_send", text)
        self.assertNotIn("MetaTrader5", text)
        self.assertNotIn("LIVE_MODE", text)


if __name__ == "__main__":
    unittest.main()

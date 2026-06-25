from __future__ import annotations

import ast
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STRATEGY_ROOT = REPO_ROOT / "gold_sniper" / "strategy"

FORBIDDEN_IMPORTS = {
    "MetaTrader5",
    "execution",
    "execution.broker_gateway",
    "execution.trade_manager",
    "core.orchestrator",
    "dotenv",
    "subprocess",
    "socket",
    "requests",
    "aiohttp",
    "discord",
    "websockets",
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


class TestP1EngineStaticGuards(unittest.TestCase):
    def test_strategy_has_no_forbidden_imports(self):
        offenders = []
        for path in STRATEGY_ROOT.rglob("*.py"):
            bad = []
            for module in _imports(path):
                if module in FORBIDDEN_IMPORTS or any(
                    module.startswith(prefix + ".") for prefix in FORBIDDEN_IMPORTS
                ):
                    bad.append(module)
            if bad:
                offenders.append((path.relative_to(REPO_ROOT).as_posix(), sorted(bad)))
        self.assertEqual([], offenders, f"Forbidden imports in strategy: {offenders}")

    def test_strategy_has_no_order_send_token_outside_comments(self):
        offenders = []
        token = "order" "_send"
        for path in STRATEGY_ROOT.rglob("*.py"):
            lines = [
                line
                for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                if not line.strip().startswith("#")
            ]
            if token in "\n".join(lines):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual([], offenders, f"order_send token in strategy: {offenders}")

    def test_strategy_no_os_environ(self):
        offenders = []
        for path in STRATEGY_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
            if "os.environ" in "\n".join(lines):
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
        self.assertEqual([], offenders, f"os.environ in strategy: {offenders}")

    def test_contracts_module_pure(self):
        from gold_sniper.strategy import contracts as mod

        source = str(Path(mod.__file__).read_text(encoding="utf-8"))
        lines = [ln for ln in source.splitlines() if not ln.strip().startswith("#")]
        stripped = "\n".join(lines)
        forbidden = ["MetaTrader5", "order_send", "os.environ", "dotenv", "subprocess", "socket"]
        for token in forbidden:
            self.assertNotIn(token, stripped, f"Token '{token}' found in contracts.py")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ORDER_SEND_TOKEN = "order" "_send"
MT5_MODULE_TOKEN = "Meta" "Trader5"
CORE_ORCHESTRATOR_TOKEN = "core" ".orchestrator"
RUN_ORCHESTRATOR_ONCE_TOKEN = "_run" "_orchestrator_once"
PUBLISH_ORCHESTRATOR_STATE_TOKEN = "_publish" "_orchestrator_state"
SIGNAL_FROM_DECISION_TOKEN = "_signal" "_from_decision"

EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "P1_PREFLIGHT_RECOVERY",
}


def iter_python_files():
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        yield path


def is_test_file(relative: str) -> bool:
    return relative.startswith("gold_sniper/tests/")


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def parse_ast(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module)
    return modules


class P1CleanStaticGuardsTest(unittest.TestCase):
    def test_no_mt5_import_in_p1_paths(self):
        allowed = {"tools/export_mt5_historical_candles.py"}
        offenders = []
        for path in iter_python_files():
            relative = rel(path)
            if relative.startswith("gold_sniper/execution/") or relative in allowed:
                continue
            tree = parse_ast(path)
            if MT5_MODULE_TOKEN in imported_modules(tree):
                offenders.append(relative)
        self.assertEqual(offenders, [], f"MT5 import inside P1 paths: {offenders}")

    def test_no_broker_write_token_outside_gateway(self):
        allowed = "gold_sniper/execution/broker_gateway.py"
        offenders = []
        for path in iter_python_files():
            relative = rel(path)
            if is_test_file(relative):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            # strip comment-only lines to avoid false positives
            lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#")]
            stripped = "\n".join(lines)
            if ORDER_SEND_TOKEN in stripped and relative != allowed:
                offenders.append(relative)
        self.assertEqual(offenders, [], f"{ORDER_SEND_TOKEN} outside broker_gateway.py: {offenders}")

    def test_replay_has_no_live_imports(self):
        forbidden_modules = {
            CORE_ORCHESTRATOR_TOKEN,
            "execution.trade_manager",
            "execution.broker_gateway",
            MT5_MODULE_TOKEN,
            "pc_manager",
            "watchdog",
            "web",
            "web_app",
            "discord",
            "cloudflared",
        }
        offenders = []
        replay_root = REPO_ROOT / "gold_sniper" / "replay"
        for path in replay_root.rglob("*.py"):
            tree = parse_ast(path)
            modules = imported_modules(tree)
            bad = sorted(
                module
                for module in modules
                if module in forbidden_modules
                or any(module.startswith(prefix + ".") for prefix in forbidden_modules)
            )
            if bad:
                offenders.append((rel(path), bad))
        self.assertEqual(offenders, [], f"Live imports inside replay/: {offenders}")

    def test_decision_pipeline_no_orchestrator_bridge(self):
        path = REPO_ROOT / "gold_sniper" / "replay" / "decision_pipeline.py"
        text = path.read_text(encoding="utf-8", errors="ignore")
        forbidden = [
            CORE_ORCHESTRATOR_TOKEN,
            RUN_ORCHESTRATOR_ONCE_TOKEN,
            PUBLISH_ORCHESTRATOR_STATE_TOKEN,
            SIGNAL_FROM_DECISION_TOKEN,
        ]
        offenders = [item for item in forbidden if item in text]
        self.assertEqual(offenders, [], f"Forbidden replay/orchestrator bridge remains: {offenders}")

    def test_gitignore_keeps_p1_artifacts_out(self):
        path = REPO_ROOT / ".gitignore"
        text = path.read_text(encoding="utf-8", errors="ignore")
        required = [
            "P1_PREFLIGHT_RECOVERY/",
            "node_modules/",
            "gold_sniper/web_app/node_modules/",
            "gold_sniper/web_app/dist/",
            "gold_sniper/cache_forexfactory.xml",
            "gold_sniper/replay/outputs/",
        ]
        missing = [item for item in required if item not in text]
        self.assertEqual(missing, [], f"Missing .gitignore P1-clean entries: {missing}")


if __name__ == "__main__":
    unittest.main()

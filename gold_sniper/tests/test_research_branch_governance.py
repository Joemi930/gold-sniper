from __future__ import annotations

import ast
import importlib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_p1_opus_is_research_branch(monkeypatch):
    from safety.research_branch_guard import is_research_branch

    assert is_research_branch("P1-opus") is True


def test_research_shadow_only_env_override(monkeypatch):
    from safety.research_branch_guard import research_shadow_only_enabled

    monkeypatch.setenv("GOLD_SNIPER_RESEARCH_SHADOW_ONLY", "1")
    assert research_shadow_only_enabled("main") is True


def test_execution_guard_denies_broker_write_on_research_branch(monkeypatch):
    monkeypatch.setenv("GOLD_SNIPER_SKIP_DOTENV", "1")
    monkeypatch.setenv("GOLD_SNIPER_BRANCH", "P1-opus")
    monkeypatch.setenv("RUN_MODE", "LIVE")
    monkeypatch.setenv("ALLOW_BROKER_WRITES", "1")
    monkeypatch.setenv("LIVE_MODE", "1")

    import config
    import execution.execution_guard as execution_guard

    importlib.reload(config)
    importlib.reload(execution_guard)
    guard = execution_guard.ExecutionGuard(blackboard=None)
    decision = guard.can_send_broker_order("OPEN_ORDER")
    assert decision.allowed is False
    assert decision.reason == "RESEARCH_BRANCH_SHADOW_ONLY"


def _python_files(root: Path):
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_strategy_and_replay_do_not_import_metatrader5():
    roots = [REPO_ROOT / "gold_sniper" / "strategy", REPO_ROOT / "gold_sniper" / "replay"]
    offenders = []
    for root in roots:
        for path in _python_files(root):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name == "MetaTrader5":
                            offenders.append(str(path))
                elif isinstance(node, ast.ImportFrom):
                    if node.module == "MetaTrader5":
                        offenders.append(str(path))
    assert offenders == []


def test_order_send_is_isolated_in_broker_gateway():
    root = REPO_ROOT / "gold_sniper"
    allowed = {
        REPO_ROOT / "gold_sniper" / "execution" / "broker_gateway.py",
    }
    offenders = []
    for path in root.rglob("*.py"):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "mt5." + "order_send" in text and path not in allowed:
            offenders.append(str(path))
    assert offenders == []


def test_research_branch_governance_doc_exists():
    path = REPO_ROOT / "docs" / "research_branch_governance.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "P1-opus" in text
    assert "shadow-only" in text.lower()
    assert "order_send" in text

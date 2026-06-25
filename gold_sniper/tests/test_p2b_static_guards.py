"""P2-B Static Guards — no broker/MT5/API keys in data_pipeline."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_PIPELINE = ROOT / "gold_sniper" / "data_pipeline"

FORBIDDEN_IMPORTS = {
    "MetaTrader5",
    "execution",
    "execution.broker_gateway",
    "execution.trade_manager",
    "core.orchestrator",
    "main",
}

FORBIDDEN_TOKENS = {
    "order_send",
    "broker_gateway",
}


class TestP2bStaticGuards(unittest.TestCase):
    def test_data_pipeline_no_forbidden_imports(self):
        offenders = []
        for path in DATA_PIPELINE.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(text, filename=str(path))
            found = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
            bad = [
                m for m in found
                if m in FORBIDDEN_IMPORTS or any(m.startswith(p + ".") for p in FORBIDDEN_IMPORTS)
            ]
            if bad:
                offenders.append((path.relative_to(ROOT).as_posix(), sorted(bad)))
        self.assertEqual([], offenders, f"Forbidden imports: {offenders}")

    def test_data_pipeline_no_forbidden_tokens(self):
        offenders = []
        for path in DATA_PIPELINE.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in FORBIDDEN_TOKENS:
                if token in text:
                    offenders.append((path.relative_to(ROOT).as_posix(), token))
        self.assertEqual([], offenders, f"Forbidden tokens: {offenders}")

    def test_no_hardcoded_api_key(self):
        """FMP_API_KEY is only accessed via os.environ.get, never hardcoded as a secret value."""
        for path in DATA_PIPELINE.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            lines = text.split("\n")
            for i, line in enumerate(lines):
                stripped = line.strip()
                # Skip comments, import lines, function signatures
                if stripped.startswith("#") or stripped.startswith("def ") or stripped.startswith("from ") or stripped.startswith("import "):
                    continue
                # Check for actual API key hardcoding: something like key="abc123" or var="secret"
                if "FMP_API_KEY" in stripped and "=" in stripped:
                    lhs, rhs = stripped.split("=", 1)
                    rhs = rhs.strip()
                    # Allow: None, "", os.environ.get(...), string containing ONLY "FMP_API_KEY_MISSING" (error message)
                    if rhs in ("None", "none", '""', "''"):
                        continue
                    if "os.environ.get" in rhs:
                        continue
                    if "os.environ[" in rhs:
                        continue
                    # Error messages containing "FMP_API_KEY" as part of a larger string are OK
                    if "FMP_API_KEY_MISSING" in rhs or "FMP_API_KEY" in rhs and len(rhs) > 20:
                        continue
                    self.fail(f"Potential hardcoded API value at {path}:{i+1}: {stripped}")

    def test_no_metatrader5_in_pipeline(self):
        for path in DATA_PIPELINE.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("MetaTrader5", text, f"MetaTrader5 found in {path}")

    def test_execution_readiness_present_in_news_context_status(self):
        """Verify that news_context_at properly distinguishes MISSING vs CLEAR."""
        from gold_sniper.replay.economic_calendar import news_context_at
        from datetime import datetime, timezone

        now = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)

        # calendar_missing=True → MISSING + replay_invalid=True
        ctx_missing = news_context_at([], now, calendar_missing=True)
        self.assertEqual(ctx_missing.status, "MISSING")
        self.assertTrue(ctx_missing.replay_invalid)

        # empty events + calendar_missing=False → EMPTY + replay_invalid=True
        ctx_empty = news_context_at([], now, calendar_missing=False)
        self.assertEqual(ctx_empty.status, "EMPTY")
        self.assertTrue(ctx_empty.replay_invalid)
        self.assertFalse(ctx_empty.news_clear)


if __name__ == "__main__":
    unittest.main()

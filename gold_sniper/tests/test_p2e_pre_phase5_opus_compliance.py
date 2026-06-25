"""Pre-Phase 5 static compliance checks for Opus cleanup gates."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class TestP2ePrePhase5OpusCompliance(unittest.TestCase):
    def test_runtime_has_no_mt5_or_order_send(self) -> None:
        paths = list((ROOT / "gold_sniper" / "replay").glob("*.py"))
        paths += list((ROOT / "gold_sniper" / "strategy").glob("*.py"))
        paths += list((ROOT / "gold_sniper" / "validation").glob("*.py"))
        paths += list((ROOT / "gold_sniper" / "agents").glob("*.py"))
        paths += list((ROOT / "gold_sniper" / "data_pipeline").glob("*.py"))
        joined = "\n".join(path.read_text(encoding="utf-8") for path in paths)

        self.assertNotIn("MetaTrader5", joined)
        self.assertNotIn("order_send", joined)
        self.assertNotIn("broker_gateway", joined)

    def test_scorecard_uses_semantic_missing_helpers(self) -> None:
        source = (ROOT / "gold_sniper" / "strategy" / "scorecard.py").read_text(encoding="utf-8")

        self.assertIn("def _poi_semantic_status", source)
        self.assertIn("def _micro_semantic_status", source)
        self.assertIn("def _liquidity_semantic_status", source)
        self.assertIn('missing.append("LIQUIDITY_UNAVAILABLE")', source)
        self.assertIn('missing.append("MICRO_UNAVAILABLE")', source)

    def test_agent_handoffs_use_p2a_selected_poi(self) -> None:
        """Phase 7E: p2a_poi_connectivity contract lives in poi_contract.py.
        Agents delegate to extract_p2a_selected_poi instead of duplicating extraction."""
        agent3 = (ROOT / "gold_sniper" / "agents" / "agent_3_liquidite.py").read_text(encoding="utf-8")
        agent4 = (ROOT / "gold_sniper" / "agents" / "agent_4_fibonacci.py").read_text(encoding="utf-8")
        agent5 = (ROOT / "gold_sniper" / "agents" / "agent_5_microscope.py").read_text(encoding="utf-8")
        poi_contract = (ROOT / "gold_sniper" / "agents" / "poi_contract.py").read_text(encoding="utf-8")

        # The centralized contract must contain P2-A references
        self.assertIn("p2a_poi_connectivity", poi_contract)
        self.assertIn("P2A_SELECTED_POI", poi_contract)
        self.assertIn("LEGACY_AGENT2_FALLBACK", poi_contract)

        # Each agent must delegate to the centralized contract
        self.assertIn("extract_p2a_selected_poi", agent3)
        self.assertIn("extract_p2a_selected_poi", agent4)
        self.assertIn("extract_p2a_selected_poi", agent5)

        # Handoff markers must be present in each agent (set by delegation)
        self.assertIn("agent3_handoff_source", agent3)
        self.assertIn("agent4_handoff_source", agent4)
        self.assertIn("agent5_handoff_source", agent5)

    def test_agent4_replay_no_longer_blocks_on_agent2_score_zero(self) -> None:
        source = (ROOT / "gold_sniper" / "replay" / "decision_pipeline.py").read_text(encoding="utf-8")
        start = source.index("async def run_replay_agent_4")
        end = source.index("async def run_replay_agent_5")
        agent4_source = source[start:end]

        self.assertNotIn("agent2_result.score == 0", agent4_source)
        self.assertIn("extract_agent2_p2a_ote_anchor", agent4_source)
        self.assertIn("agent4_poi_handoff", agent4_source)


if __name__ == "__main__":
    unittest.main()

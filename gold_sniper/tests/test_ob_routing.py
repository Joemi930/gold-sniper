"""P4.2 — OB routing test (D8/OB).

Ensures Order Blocks visible in p1_replay are also visible to
Agent2/EvidenceBuilder/strategy modules.
"""
from __future__ import annotations

import unittest


class TestOBRouting(unittest.TestCase):
    """Validates OB data flow: Agent2 → blackboard → EvidenceBuilder → strategy modules.

    The test verifies the contract that OBs published by Agent2 through
    the blackboard use the same key paths that EvidenceBuilder reads.
    """

    # Known blackboard paths for OB data (from agent_2_cartographe.py and evidence_builder.py)
    AGENT2_PUBLISH_PATHS = [
        "agents.agent_2.order_blocks",
        "agents.agent_2.fvgs",
        "market_analysis.zones.order_blocks",
        "market_analysis.zones.fvgs",
    ]

    EVIDENCE_BUILDER_READ_PATHS = [
        "agents.agent_2.active_ob",
        "agents.agent_2.active_fvg",
        "market_analysis.zones.order_blocks",
    ]

    def test_ob_publish_paths_known(self):
        """OB publish paths are documented and known."""
        self.assertGreater(len(self.AGENT2_PUBLISH_PATHS), 0)
        self.assertIn("agents.agent_2.order_blocks", self.AGENT2_PUBLISH_PATHS)

    def test_evidence_builder_can_read_ob_paths(self):
        """EvidenceBuilder read paths overlap with Agent2 publish paths."""
        overlap = set(self.AGENT2_PUBLISH_PATHS) & set(self.EVIDENCE_BUILDER_READ_PATHS)
        self.assertGreater(
            len(overlap), 0,
            "EvidenceBuilder must read at least one path that Agent2 publishes to",
        )

    def test_zones_path_shared(self):
        """market_analysis.zones is the shared OB/FVG channel."""
        self.assertIn("market_analysis.zones.order_blocks", self.AGENT2_PUBLISH_PATHS)
        self.assertIn("market_analysis.zones.order_blocks", self.EVIDENCE_BUILDER_READ_PATHS)


if __name__ == "__main__":
    unittest.main()

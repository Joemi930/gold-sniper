from __future__ import annotations

import unittest

from gold_sniper.strategy.contracts import EvidenceBundle
from gold_sniper.strategy.scorecard import WEIGHTS, evaluate_scorecard


class TestP2dScorecardReadinessMetadata(unittest.TestCase):
    def test_metadata_contains_section_failed_reasons_and_preserves_lists(self):
        before = dict(WEIGHTS)
        scorecard = evaluate_scorecard(
            EvidenceBundle(
                context={"reason": "OK", "direction": "BUY"},
                poi={"reason": "POI_UNAVAILABLE", "passed": False},
                micro={},
            )
        )

        self.assertEqual(before, WEIGHTS)
        self.assertIn("section_failed_reasons", scorecard.metadata)
        self.assertIn("poi", scorecard.metadata["section_failed_reasons"])
        self.assertIn("missing_evidence", scorecard.metadata)
        self.assertIn("soft_issues", scorecard.metadata)
        self.assertIsInstance(scorecard.missing_evidence, list)
        self.assertIsInstance(scorecard.soft_issues, list)


if __name__ == "__main__":
    unittest.main()

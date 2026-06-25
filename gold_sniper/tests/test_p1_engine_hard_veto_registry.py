from __future__ import annotations

import unittest

from gold_sniper.strategy.contracts import BlockedStage, EvidenceBundle, HardVetoResult
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto


class TestHardVetoRegistry(unittest.TestCase):
    def test_no_veto_for_empty_evidence(self):
        result = evaluate_hard_veto(EvidenceBundle())
        self.assertFalse(result.hard_veto)
        self.assertIsNone(result.veto_code)

    def test_no_veto_for_none(self):
        result = evaluate_hard_veto(None)
        self.assertFalse(result.hard_veto)

    def test_no_veto_for_empty_dict(self):
        result = evaluate_hard_veto({})
        self.assertFalse(result.hard_veto)

    def test_news_high_impact_blocks(self):
        bundle = EvidenceBundle(news={"high_impact_window": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "NEWS_HIGH_IMPACT_WINDOW")
        self.assertEqual(result.blocked_stage, BlockedStage.NEWS)

    def test_news_blocked_blocks(self):
        bundle = EvidenceBundle(news={"news_blocked": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "NEWS_HIGH_IMPACT_WINDOW")

    def test_news_reason_blackout_blocks(self):
        bundle = EvidenceBundle(news={"reason": "NEWS_BLACKOUT_HIGH_DUE_TO_FOMC"})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "NEWS_HIGH_IMPACT_WINDOW")

    def test_post_news_stealth_blocks(self):
        bundle = EvidenceBundle(news={"post_news_stealth": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "NEWS_POST_EVENT_STEALTH")

    def test_tokyo_session_blocks(self):
        bundle = EvidenceBundle(session={"session": "TOKYO"})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "SESSION_TOKYO_ASIA_BLOCK")

    def test_asia_session_blocks(self):
        bundle = EvidenceBundle(session={"session": "ASIA"})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "SESSION_TOKYO_ASIA_BLOCK")

    def test_explicit_hard_block_blocks(self):
        bundle = EvidenceBundle(session={"is_hard_block": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "SESSION_EXPLICIT_HARD_BLOCK")

    def test_poi_mitigation_over_50_blocks(self):
        bundle = EvidenceBundle(poi={"mitigation_pct": 55.0})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "POI_DEEP_MITIGATION_GT_50")

    def test_poi_mitigation_at_50_passes(self):
        bundle = EvidenceBundle(poi={"mitigation_pct": 50.0})
        result = evaluate_hard_veto(bundle)
        self.assertFalse(result.hard_veto)

    def test_poi_opposes_context_blocks(self):
        bundle = EvidenceBundle(poi={"opposes_htf_dol": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "POI_OPPOSES_HTF_DOL")

    def test_poi_not_aligned_blocks(self):
        bundle = EvidenceBundle(poi={"aligned_with_context": False})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "POI_OPPOSES_HTF_DOL")

    def test_reversal_without_sweep_blocks(self):
        from gold_sniper.strategy.contracts import SetupType

        bundle = EvidenceBundle(setup_type=SetupType.REVERSAL_STRICT, liquidity={})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "REVERSAL_WITHOUT_SWEEP")

    def test_reversal_with_sweep_passes(self):
        from gold_sniper.strategy.contracts import SetupType

        bundle = EvidenceBundle(
            setup_type=SetupType.REVERSAL_STRICT,
            liquidity={"sweep_detected": True},
        )
        result = evaluate_hard_veto(bundle)
        self.assertFalse(result.hard_veto)

    def test_continuation_without_sweep_passes(self):
        from gold_sniper.strategy.contracts import SetupType

        bundle = EvidenceBundle(setup_type=SetupType.CONTINUATION_LIGHT, liquidity={})
        result = evaluate_hard_veto(bundle)
        self.assertFalse(result.hard_veto)

    def test_max_daily_loss_blocks(self):
        bundle = EvidenceBundle(risk={"max_daily_loss_hit": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "MAX_DAILY_LOSS_GUARD")

    def test_max_weekly_loss_blocks(self):
        bundle = EvidenceBundle(risk={"max_weekly_loss_hit": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "MAX_WEEKLY_LOSS_GUARD")

    def test_max_drawdown_blocks(self):
        bundle = EvidenceBundle(risk={"max_drawdown_hit": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "MAX_DRAWDOWN_GUARD")

    def test_friday_halt_blocks(self):
        bundle = EvidenceBundle(session={"friday_halt": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "FRIDAY_HALT")

    def test_replay_invalid_is_not_hard_veto(self):
        bundle = EvidenceBundle(raw={"replay_invalid": True})
        result = evaluate_hard_veto(bundle)
        self.assertFalse(result.hard_veto)
        self.assertTrue(result.replay_invalid)
        self.assertEqual(result.veto_code, "REPLAY_DATA_INVALID")

    def test_replay_invalid_separate_from_strategic_veto(self):
        bundle = EvidenceBundle(raw={"data_invalid": True})
        result = evaluate_hard_veto(bundle)
        self.assertFalse(result.hard_veto)
        self.assertTrue(result.replay_invalid)

    def test_lookahead_violation_blocks(self):
        bundle = EvidenceBundle(raw={"lookahead_violation": True})
        result = evaluate_hard_veto(bundle)
        self.assertTrue(result.hard_veto)
        self.assertEqual(result.veto_code, "NO_LOOKAHEAD_VIOLATION")


if __name__ == "__main__":
    unittest.main()

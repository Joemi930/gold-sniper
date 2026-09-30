"""Graded-entry mode (A+/A/B) for the Kasper reversal model.

Legacy Kasper is binary: a setup must pass all 8 gates sequentially (score=100,
grade A+) or it WAITs/REJECTs. The A/B/C tiers were therefore unreachable as
entries. Graded mode keeps the 4 *validity* gates (htf_bias, liquidity_sweep,
poi, risk_precheck) as hard vetoes but SCORES the 4 *confirmation* gates
(reintegration, displacement, structure_shift, micro) and enters at grade >= B
(score >= 70). This is graded, not diluted: a B still requires real confirmation
on top of the 60-pt validity base. Gated behind GOLD_SNIPER_KASPER_GRADED.
"""
from __future__ import annotations

import unittest

from gold_sniper.strategy.kasper_contracts import (
    Agent1Context,
    Agent2POIContext,
    Agent3LiquidityContext,
    Agent5TriggerContext,
    LiquidityEvent,
    MicroConfirmation,
    SelectedPOI,
)
from gold_sniper.strategy.kasper_scenario_engine import (
    KasperEvidenceBundle,
    KasperScenarioEngine,
    set_graded_entry,
)


def _partial_bundle() -> KasperEvidenceBundle:
    """Valid setup (sweep + POI + bias + RR) + structure_shift only.

    Missing: reintegration, displacement, micro_confirmation.
    Score = 15(htf)+20(sweep)+15(structure)+20(poi)+5(rr) = 75 -> grade B.
    """
    return KasperEvidenceBundle(
        agent1=Agent1Context(htf_bias="bullish", structure_state="BULLISH", confidence=0.8),
        agent2=Agent2POIContext(
            selected_poi=SelectedPOI(
                type="order_block", freshness="FRESH", tradable=True, htf_confluence=True,
            ),
        ),
        agent3=Agent3LiquidityContext(
            liquidity_event=LiquidityEvent(
                type="sellside_sweep",
                close_back_inside=False,      # reintegration FAIL
                displacement_after_sweep=False,  # displacement FAIL
                wick_rejection=False,
            ),
        ),
        agent5=Agent5TriggerContext(
            micro_confirmation=MicroConfirmation(
                confirmed=False,              # micro FAIL
                trigger_type="none",          # no CHoCH/BOS fallback
                close_breaks_structure=True,  # structure_shift PASS
                entry_price=2610.0, stop_loss=2598.0, rr_estimate=2.0,  # RR PASS
            ),
        ),
    )


class TestGradedEntry(unittest.TestCase):
    def tearDown(self):
        set_graded_entry(False)

    def test_binary_mode_partial_setup_does_not_enter(self):
        set_graded_entry(False)
        result = KasperScenarioEngine().evaluate_kasper_reversal(_partial_bundle())
        # Binary: early-exits at reintegration → not an entry.
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_graded_mode_partial_setup_enters_at_grade_B(self):
        set_graded_entry(True)
        result = KasperScenarioEngine().evaluate_kasper_reversal(_partial_bundle())
        self.assertEqual(result.decision_recommendation, "ENTER_ELIGIBLE")
        self.assertEqual(result.grade, "B")
        self.assertAlmostEqual(result.score, 75.0, places=1)

    def test_graded_mode_still_hard_vetoes_missing_validity(self):
        # Remove the sweep (a validity gate) → must NOT enter even in graded mode.
        set_graded_entry(True)
        b = _partial_bundle()
        no_sweep = KasperEvidenceBundle(
            agent1=b.agent1,
            agent2=b.agent2,
            agent3=Agent3LiquidityContext(liquidity_event=LiquidityEvent(type="none")),
            agent5=b.agent5,
        )
        result = KasperScenarioEngine().evaluate_kasper_reversal(no_sweep)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")

    def test_graded_mode_below_B_waits(self):
        # Only validity gates pass (no confirmation at all) → score 60 → C → WAIT.
        set_graded_entry(True)
        b = _partial_bundle()
        no_confirm = KasperEvidenceBundle(
            agent1=b.agent1,
            agent2=b.agent2,
            agent3=b.agent3,
            agent5=Agent5TriggerContext(
                micro_confirmation=MicroConfirmation(
                    confirmed=False, trigger_type="none",
                    close_breaks_structure=False,  # structure_shift now FAIL too
                    entry_price=2610.0, stop_loss=2598.0, rr_estimate=2.0,
                ),
            ),
        )
        result = KasperScenarioEngine().evaluate_kasper_reversal(no_confirm)
        self.assertNotEqual(result.decision_recommendation, "ENTER_ELIGIBLE")


if __name__ == "__main__":
    unittest.main()

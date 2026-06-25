import json
import tempfile
import unittest
from pathlib import Path

from gold_sniper.strategy.contracts import (
    DecisionAction,
    EvidenceBundle,
    HardVetoResult,
    ReadinessResult,
    ReadinessState,
    ScoreCard,
    SetupGrade,
    SetupType,
    TradeSide,
)
from gold_sniper.strategy.enter_eligibility import evaluate_enter_eligibility
from gold_sniper.strategy.liquidity_reconciliation import reconcile_liquidity_readiness
from gold_sniper.strategy.risk_allocator import allocate_risk
from gold_sniper.strategy.setup_signal_inventory import extract_setup_signal_inventory
from gold_sniper.tools.diagnose_phase16_liquidity_reconciliation import (
    diagnose_phase16_liquidity_reconciliation,
    main as diagnose_main,
)


def _bundle(
    *,
    liquidity: dict | None = None,
    micro: dict | None = None,
    synergy: bool = True,
    setup_type: SetupType = SetupType.SWEEP_REVERSAL,
    timing_ready: bool = True,
) -> EvidenceBundle:
    return EvidenceBundle(
        symbol="XAUUSD",
        ts_utc="2026-06-04T10:41:00+00:00",
        setup_type=setup_type,
        side=TradeSide.BUY,
        context={"direction": "BUY", "htf_aligned": True, "in_ote": timing_ready},
        poi={
            "selected_poi_present": True,
            "selected_poi": {"price_bounds": {"low": 100.0, "high": 110.0}, "score": 55.0},
            "price_bounds": {"low": 100.0, "high": 110.0},
            "poi_type": "DEMAND",
            "poi_quality_score": 55.0,
            "execution_readiness": "READY",
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_micro_synergy_enabled": synergy,
            "poi_micro_synergy": {
                "synergy": synergy,
                "micro_confirmed": True,
                "micro_inside_poi": True,
                "micro_outside_poi": False,
                "effective_poi_status": "POI_READY",
                "upgraded_poi_status": "POI_READY",
                "remaining_blockers": [],
            },
        },
        liquidity=liquidity
        if liquidity is not None
        else {
            "readiness_state": "WAIT_FOR_TRIGGER",
            "execution_readiness": "WAIT_FOR_TRIGGER",
            "readiness_reason": "LIQUIDITY_WAITING_SWEEP",
            "liquidity_state": "NONE",
            "sweep_detected": False,
        },
        micro=micro
        if micro is not None
        else {
            "readiness_state": "READY",
            "execution_readiness": "READY",
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": True,
            "price_in_agent2_poi": True,
            "trigger_outside_poi": False,
            "displacement_present": True,
            "reclaim_confirmed": True,
            "retest_confirmed": True,
        },
        news={"news_clear": True},
        session={"readiness_state": "READY", "trading_allowed": True, "session_grade": "HIGH"},
        risk={"risk_context_available": True},
        raw={"timing": {"readiness_state": "READY" if timing_ready else "WAIT_FOR_TRIGGER", "in_ote": timing_ready}},
    )


def _readiness(*, liquidity: str = "READY", timing: str = "READY") -> ReadinessResult:
    sections = {
        "context": "READY",
        "poi": "READY",
        "liquidity": liquidity,
        "timing": timing,
        "micro": "READY",
        "news": "READY",
        "session": "READY",
        "risk": "READY",
    }
    state = ReadinessState.READY if all(value == "READY" for value in sections.values()) else ReadinessState.WAITING_TRIGGER
    return ReadinessResult(state=state, reason="TEST", section_states=sections)


def _decision(timestamp: str, *, enter: bool = False) -> dict:
    return {
        "timestamp": timestamp,
        "setup_type": "SWEEP_REVERSAL",
        "decision": "ENTER_FULL" if enter else "WATCH_ONLY",
        "setup_grade": "C",
        "poi_micro_synergy": True,
        "enter_eligible": enter,
        "risk_multiplier": 1.0 if enter else 0.0,
        "gate_primary_blocker": "NONE" if enter else "TIMING_NOT_READY",
        "readiness_state": "READY" if enter else "WAITING_TRIGGER",
        "readiness_by_section": {
            "context": "READY",
            "poi": "READY",
            "liquidity": "READY",
            "timing": "READY" if enter else "WAITING_TRIGGER",
            "micro": "READY",
            "news": "READY",
            "session": "READY",
            "risk": "READY",
        },
        "liquidity_evidence_source": "AGENT5_MICRO_CONTRACT",
        "micro_liquidity_confirmed": True,
        "liquidity_reconciled": True,
        "liquidity_reconciliation_reason": "LIQUIDITY_POI_ANCHORED_MICRO_SWEEP_READY",
        "liquidity_reconciliation_blockers": [],
        "setup_candidates": [{"candidate_type": "SWEEP_REVERSAL", "confidence": 0.68}],
        "best_setup_candidate": {"candidate_type": "SWEEP_REVERSAL", "confidence": 0.68},
        "hard_veto": False,
        "veto_code": None,
        "risk_allowed": enter,
        "risk_reason": "SHADOW_RISK_ALLOCATED" if enter else "ENTER_NOT_ELIGIBLE",
    }


class TestP2EPhase16LiquidityReconciliation(unittest.TestCase):
    def test_macro_sweep_has_priority(self):
        bundle = _bundle(liquidity={"readiness_state": "READY", "execution_readiness": "READY", "liquidity_state": "SWEEP", "sweep_detected": True})
        result = reconcile_liquidity_readiness(bundle)
        self.assertEqual(result["liquidity_evidence_source"], "AGENT3_MACRO")
        self.assertEqual(result["readiness_state"], "READY")
        self.assertFalse(result["promoted_by_reconciliation"])

    def test_macro_break_blocks_micro_promotion(self):
        bundle = _bundle(liquidity={"readiness_state": "REJECT", "execution_readiness": "REJECT", "liquidity_state": "BREAK", "sweep_detected": False})
        result = reconcile_liquidity_readiness(bundle)
        self.assertEqual(result["readiness_state"], "REJECT")
        self.assertFalse(result["promoted_by_reconciliation"])
        self.assertIn("AGENT3_MACRO_BREAK_OR_REJECT", result["blockers"])

    def test_micro_liquidity_confirmed_promotes_liquidity(self):
        result = reconcile_liquidity_readiness(_bundle())
        self.assertEqual(result["liquidity_evidence_source"], "AGENT5_MICRO_CONTRACT")
        self.assertTrue(result["liquidity_reconciled"])
        self.assertTrue(result["micro_liquidity_confirmed"])
        self.assertEqual(result["readiness_state"], "READY")
        signals = extract_setup_signal_inventory(_bundle(liquidity=result))
        self.assertTrue(signals.liquidity_ready)
        self.assertIn("MICRO_LIQUIDITY_CONFIRMED", signals.present_signals)
        self.assertIn("LIQUIDITY_RECONCILED", signals.present_signals)

    def test_micro_sweep_without_choch_does_not_promote(self):
        micro = dict(_bundle().micro)
        micro["choch_detected"] = False
        result = reconcile_liquidity_readiness(_bundle(micro=micro))
        self.assertNotEqual(result["readiness_state"], "READY")
        self.assertIn("MICRO_CHOCH_MISSING", result["blockers"])

    def test_choch_outside_poi_does_not_promote(self):
        micro = dict(_bundle().micro)
        micro["trigger_inside_poi"] = False
        micro["price_in_agent2_poi"] = False
        micro["trigger_outside_poi"] = True
        result = reconcile_liquidity_readiness(_bundle(micro=micro))
        self.assertNotEqual(result["readiness_state"], "READY")
        self.assertIn("MICRO_NOT_INSIDE_POI", result["blockers"])
        self.assertIn("MICRO_TRIGGER_OUTSIDE_POI", result["blockers"])

    def test_no_synergy_no_promotion(self):
        result = reconcile_liquidity_readiness(_bundle(synergy=False))
        self.assertNotEqual(result["readiness_state"], "READY")
        self.assertIn("POI_MICRO_SYNERGY_MISSING", result["blockers"])

    def test_enter_possible_only_when_all_sections_ready(self):
        liquidity = reconcile_liquidity_readiness(_bundle())
        ready_bundle = _bundle(liquidity=liquidity)
        scorecard = ScoreCard(grade=SetupGrade.C)
        eligible = evaluate_enter_eligibility(
            bundle=ready_bundle,
            scorecard=scorecard,
            readiness=_readiness(liquidity="READY", timing="READY"),
            veto=HardVetoResult(),
        )
        self.assertTrue(eligible.enter_eligible)

        timing_blocked = evaluate_enter_eligibility(
            bundle=_bundle(liquidity=liquidity, timing_ready=False),
            scorecard=scorecard,
            readiness=_readiness(liquidity="READY", timing="WAITING_TRIGGER"),
            veto=HardVetoResult(),
        )
        risk = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.C,
            evidence=ready_bundle,
            capital=100.0,
            enter_eligible=timing_blocked.enter_eligible,
        )
        self.assertFalse(timing_blocked.enter_eligible)
        self.assertEqual(risk.risk_multiplier, 0.0)
        self.assertFalse(risk.allowed)

    def test_no_risk_positive_without_enter_eligible(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.C,
            evidence=_bundle(),
            capital=100.0,
            enter_eligible=False,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.risk_multiplier, 0.0)

    def test_phase16_diagnostic_outputs_files(self):
        rows = [_decision("2026-06-04T10:41:00+00:00"), _decision("2026-06-04T10:42:00+00:00")]
        report = diagnose_phase16_liquidity_reconciliation(rows, {"filled_trades": 0}, top=10)
        self.assertEqual(report["poi_micro_synergy_count"], 2)
        self.assertEqual(report["liquidity_reconciled_count"], 2)
        self.assertEqual(report["liquidity_source_distribution"], {"AGENT5_MICRO_CONTRACT": 2})

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            decisions_path = tmp_path / "decisions.jsonl"
            summary_path = tmp_path / "summary.json"
            decisions_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            summary_path.write_text(json.dumps({"filled_trades": 0, "missed_entries": 0}), encoding="utf-8")
            self.assertEqual(
                diagnose_main([
                    "--decisions",
                    str(decisions_path),
                    "--summary",
                    str(summary_path),
                ]),
                0,
            )
            self.assertTrue((tmp_path / "phase16_liquidity_reconciliation.json").exists())
            self.assertTrue((tmp_path / "phase16_liquidity_reconciliation.md").exists())


if __name__ == "__main__":
    unittest.main()

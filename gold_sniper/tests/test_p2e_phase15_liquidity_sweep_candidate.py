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
    SetupGrade,
    SetupType,
    TradeSide,
)
from gold_sniper.strategy.enter_eligibility import evaluate_enter_eligibility
from gold_sniper.strategy.risk_allocator import allocate_risk
from gold_sniper.strategy.scorecard import evaluate_scorecard
from gold_sniper.strategy.setup_candidate_mapping import map_signals_to_setup_candidates
from gold_sniper.strategy.setup_signal_inventory import extract_setup_signal_inventory
from gold_sniper.strategy.setup_taxonomy import classify_setup
from gold_sniper.tools.diagnose_phase15_liquidity_sweep_candidate import (
    diagnose_phase15_liquidity_sweep_candidate,
    main as diagnose_main,
)


def _synergy_bundle(*, liquidity_ready: bool = False, setup_type=SetupType.SWEEP_REVERSAL) -> EvidenceBundle:
    liquidity_state = "READY" if liquidity_ready else "WAIT_FOR_TRIGGER"
    return EvidenceBundle(
        symbol="XAUUSD",
        ts_utc="2026-06-04T10:41:00+00:00",
        setup_type=setup_type,
        side=TradeSide.BUY,
        context={"direction": "BUY", "htf_aligned": True},
        poi={
            "selected_poi_present": True,
            "selected_poi": {"price_bounds": {"low": 100.0, "high": 110.0}, "score": 55.0},
            "price_bounds": {"low": 100.0, "high": 110.0},
            "poi_type": "DEMAND",
            "poi_quality_score": 55.0,
            "execution_readiness": "READY",
            "poi_semantic_status": "POI_PRESENT_EXECUTABLE",
            "poi_micro_synergy": {
                "synergy": True,
                "status": "SYNERGY_READY",
                "reason": "TEST",
                "micro_confirmed": True,
                "micro_inside_poi": True,
                "micro_outside_poi": False,
                "upgraded_poi_status": "POI_READY",
                "effective_poi_status": "POI_READY",
                "remaining_blockers": [],
            },
        },
        liquidity={
            "readiness_state": liquidity_state,
            "execution_readiness": liquidity_state,
            "readiness_reason": "LIQUIDITY_WAITING_SWEEP" if not liquidity_ready else "LIQUIDITY_READY",
            "liquidity_state": "NONE",
            "sweep_detected": False,
        },
        micro={
            "sweep_1m_confirmed": True,
            "choch_detected": True,
            "trigger_inside_poi": True,
            "price_in_agent2_poi": True,
            "trigger_outside_poi": False,
            "retest_confirmed": False,
            "candles_1m_count": 1440,
        },
        session={"readiness_state": "READY", "trading_allowed": True, "session_grade": "HIGH"},
        news={"news_clear": True},
        risk={"risk_context_available": True},
        raw={"timing": {"readiness_state": "READY", "execution_readiness": "READY", "in_ote": False}},
    )


def _readiness(*, liquidity: str = "WAITING_TRIGGER", timing: str = "READY") -> ReadinessResult:
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


def _decision(timestamp: str, *, setup_type: str = "SWEEP_REVERSAL") -> dict:
    return {
        "timestamp": timestamp,
        "decision": "WATCH_ONLY",
        "setup_type": setup_type,
        "setup_grade": "C",
        "poi_micro_synergy": True,
        "micro_confirmed": True,
        "micro_inside_poi": True,
        "micro_sweep_confirmed": True,
        "setup_sweep_evidence": True,
        "setup_sweep_evidence_source": "MICRO_CONTRACT",
        "sweep_1m_confirmed": True,
        "choch_detected": True,
        "trigger_inside_poi": True,
        "enter_eligible": False,
        "risk_multiplier": 0.0,
        "gate_primary_blocker": "LIQUIDITY_NOT_READY",
        "setup_signal_inventory": {
            "present_signals": [
                "POI_MICRO_SYNERGY",
                "MICRO_CONFIRMED",
                "MICRO_INSIDE_POI",
                "MICRO_SWEEP_CONFIRMED",
                "SETUP_SWEEP_EVIDENCE",
                "SETUP_SWEEP_EVIDENCE_MICRO_CONTRACT",
                "LIQUIDITY_WAITING",
            ],
            "liquidity_ready": False,
            "liquidity_waiting": True,
            "sweep_detected": False,
            "micro_sweep_confirmed": True,
            "setup_sweep_evidence": True,
            "setup_sweep_evidence_source": "MICRO_CONTRACT",
            "micro_ready": True,
            "micro_confirmed": True,
            "micro_inside_poi": True,
            "timing_ready": True,
            "in_ote": False,
            "poi_type": "DEMAND",
            "trend_aligned_poi": True,
            "counter_trend_poi": False,
        },
        "setup_candidates": [
            {
                "candidate_type": "SWEEP_REVERSAL",
                "confidence": 0.68,
                "reason": "POI_MICRO_SYNERGY_WITH_MICRO_SWEEP_EVIDENCE",
                "present": ["POI_MICRO_SYNERGY", "SETUP_SWEEP_EVIDENCE"],
                "missing": ["SWEEP_DETECTED", "LIQUIDITY_READY"],
                "is_strict_candidate": False,
                "is_light_candidate": True,
            }
        ],
        "best_setup_candidate": {"candidate_type": "SWEEP_REVERSAL", "confidence": 0.68},
        "near_miss_missing_signals": ["SWEEP_DETECTED", "LIQUIDITY_READY"],
        "p1_evidence_bundle": {
            "liquidity": {
                "readiness_state": "WAIT_FOR_TRIGGER",
                "execution_readiness": "WAIT_FOR_TRIGGER",
                "readiness_reason": "LIQUIDITY_WAITING_SWEEP",
                "liquidity_state": "NONE",
                "sweep_detected": False,
            },
            "micro": {
                "sweep_1m_confirmed": True,
                "choch_detected": True,
                "trigger_inside_poi": True,
                "price_in_agent2_poi": True,
            },
            "poi": {"poi_type": "DEMAND"},
            "raw": {"timing": {"readiness_state": "READY", "in_ote": False}},
        },
    }


class TestP2EPhase15LiquiditySweepCandidate(unittest.TestCase):
    def test_agent3_sweep_propagates_to_signals(self):
        bundle = EvidenceBundle(
            context={"direction": "BUY"},
            poi={"selected_poi_present": True, "price_bounds": {"low": 1, "high": 2}},
            liquidity={"event": "SWEEP", "readiness_state": "READY"},
        )
        signals = extract_setup_signal_inventory(bundle)
        self.assertTrue(signals.sweep_detected)
        self.assertIn("SWEEP_DETECTED", signals.present_signals)

    def test_micro_sweep_does_not_force_liquidity_ready_or_risk(self):
        bundle = _synergy_bundle(liquidity_ready=False)
        signals = extract_setup_signal_inventory(bundle)
        self.assertTrue(signals.micro_sweep_confirmed)
        self.assertTrue(signals.setup_sweep_evidence)
        self.assertEqual(signals.setup_sweep_evidence_source, "MICRO_CONTRACT")
        self.assertFalse(signals.liquidity_ready)

        scorecard = evaluate_scorecard(bundle, HardVetoResult())
        eligibility = evaluate_enter_eligibility(
            bundle=bundle,
            scorecard=scorecard,
            readiness=_readiness(liquidity="WAITING_TRIGGER"),
            veto=HardVetoResult(),
        )
        risk = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.C,
            evidence=bundle,
            capital=100.0,
            enter_eligible=eligibility.enter_eligible,
        )
        self.assertFalse(eligibility.enter_eligible)
        self.assertIn("SECTION_NOT_READY:liquidity", eligibility.blockers)
        self.assertFalse(risk.allowed)
        self.assertEqual(risk.risk_multiplier, 0.0)

    def test_synergy_micro_sweep_forms_sweep_reversal_candidate(self):
        signals = extract_setup_signal_inventory(_synergy_bundle(liquidity_ready=False))
        candidates = map_signals_to_setup_candidates(signals)
        self.assertTrue(
            any(
                candidate.candidate_type == SetupType.SWEEP_REVERSAL
                and candidate.reason == "POI_MICRO_SYNERGY_WITH_MICRO_SWEEP_EVIDENCE"
                for candidate in candidates
            )
        )
        classification = classify_setup(_synergy_bundle(liquidity_ready=False))
        self.assertEqual(classification.setup_type, SetupType.SWEEP_REVERSAL)
        self.assertIn("liquidity", classification.required_ready_sections)

    def test_poi_reaction_remains_non_tradable(self):
        bundle = _synergy_bundle(liquidity_ready=True, setup_type=SetupType.POI_REACTION)
        scorecard = evaluate_scorecard(bundle, HardVetoResult())
        eligibility = evaluate_enter_eligibility(
            bundle=bundle,
            scorecard=scorecard,
            readiness=_readiness(liquidity="READY"),
            veto=HardVetoResult(),
        )
        self.assertFalse(eligibility.enter_eligible)
        self.assertIn("SETUP_TYPE_POI_REACTION_NOT_TRADABLE", eligibility.blockers)

    def test_no_risk_positive_without_enter_eligible(self):
        plan = allocate_risk(
            action=DecisionAction.ENTER_FULL,
            grade=SetupGrade.C,
            evidence=_synergy_bundle(),
            capital=100.0,
            enter_eligible=False,
        )
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.risk_multiplier, 0.0)

    def test_phase15_diagnostic_outputs_json_and_markdown(self):
        rows = [
            _decision("2026-06-04T10:41:00+00:00"),
            _decision("2026-06-04T10:42:00+00:00"),
        ]
        report = diagnose_phase15_liquidity_sweep_candidate(rows, {"filled_trades": 0}, top=10)
        self.assertEqual(report["synergy_true_count"], 2)
        self.assertEqual(report["micro_sweep_without_agent3_sweep_count"], 2)
        self.assertEqual(report["synergy_candidate_distribution"], {"SWEEP_REVERSAL": 2})

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
            self.assertTrue((tmp_path / "phase15_diagnosis.json").exists())
            self.assertTrue((tmp_path / "phase15_diagnosis.md").exists())


if __name__ == "__main__":
    unittest.main()

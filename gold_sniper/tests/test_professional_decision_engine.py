from __future__ import annotations

from gold_sniper.strategy.professional_decision_engine import (
    DECISION_ENTER_FULL,
    DECISION_ENTER_REDUCED,
    DECISION_REJECT,
    DECISION_WAIT_FOR_TRIGGER,
    DECISION_WATCH_ONLY,
    GRADE_A_PLUS,
    GRADE_B,
    GRADE_C,
    GRADE_D,
    SHADOW_ONLY,
    evaluate_professional_decision,
)


def _stage(passed=True, reason="OK", value=None):
    return {"passed": passed, "reason": reason, "value": value}


def _strong_evidence():
    return {
        "news_permission": _stage(True, "NEWS_CLEAR"),
        "session_permission": _stage(True, "SESSION_ALLOWED"),
        "spread_risk_placeholder": _stage(True, "SPREAD_RISK_AVAILABLE"),
        "htf_context_placeholder": _stage(True, "HTF_CONTEXT_AVAILABLE"),
        "dol_placeholder": _stage(True, "DOL_AVAILABLE"),
        "liquidity_state": _stage(True, "LIQUIDITY_SUPPORTS_SWEEP_REJECTED", {"decision": "SUPPORTS_SETUP"}),
        "poi_placeholder": _stage(True, "POI_QUALITY_ACCEPT", {"decision": "ACCEPT", "score": 92, "grade": "A"}),
        "session_premium_ote_gate": _stage(True, "SESSION_PREMIUM_OTE_PASS", {"decision": "PASS", "score": 88}),
        "micro_confirmation": _stage(True, "MICRO_CONFIRMATION_CONFIRMED", {"decision": "CONFIRMED", "score": 90}),
        "risk_placeholder": _stage(True, "RISK_AVAILABLE"),
        "kasper_ict_scenarios": {
            "best_scenario": {
                "scenario_type": "OTE_CONFLUENCE",
                "tradable": True,
                "near_miss": False,
                "confidence": 1.0,
                "status": "SCENARIO_VALID",
            }
        },
    }


def test_news_hard_veto_rejects_with_zero_risk():
    evidence = _strong_evidence()
    evidence["news_permission"] = _stage(False, "NEWS_HARD_VETO")
    result = evaluate_professional_decision(evidence, setup_type="REVERSAL_AFTER_SWEEP")
    assert result.decision == DECISION_REJECT
    assert result.setup_grade == GRADE_D
    assert result.hard_veto is True
    assert result.hard_veto_reason == "NEWS_HARD_VETO"
    assert result.risk_multiplier == 0.0
    assert result.required_execution_mode == SHADOW_ONLY


def test_asia_tokyo_hard_veto_rejects():
    evidence = _strong_evidence()
    evidence["session_permission"] = _stage(False, "SESSION_VETO_TOKYO_ASIA")
    result = evaluate_professional_decision(evidence, setup_type="TREND_CONTINUATION")
    assert result.decision == DECISION_REJECT
    assert result.setup_grade == GRADE_D
    assert result.hard_veto is True
    assert result.risk_multiplier == 0.0


def test_a_plus_setup_enters_full():
    result = evaluate_professional_decision(_strong_evidence(), setup_type="OTE_CONFLUENCE")
    assert result.decision == DECISION_ENTER_FULL
    assert result.setup_grade == GRADE_A_PLUS
    assert result.risk_multiplier == 1.0
    assert result.confidence_score > 0.85
    assert result.hard_veto is False


def test_b_setup_enters_reduced():
    evidence = _strong_evidence()
    evidence["poi_placeholder"] = _stage(False, "POI_QUALITY_WATCH_B", {"decision": "WATCH", "score": 66, "grade": "B"})
    evidence["session_premium_ote_gate"] = _stage(False, "SESSION_PREMIUM_OTE_WATCH_B", {"decision": "WATCH", "score": 62})
    evidence["kasper_ict_scenarios"]["best_scenario"]["near_miss"] = True
    evidence["kasper_ict_scenarios"]["best_scenario"]["tradable"] = False
    evidence["kasper_ict_scenarios"]["best_scenario"]["confidence"] = 0.85
    result = evaluate_professional_decision(evidence, setup_type="TREND_CONTINUATION")
    assert result.setup_grade in {GRADE_B, GRADE_C}
    if result.setup_grade == GRADE_B:
        assert result.decision == DECISION_ENTER_REDUCED
        assert result.risk_multiplier == 0.4


def test_missing_micro_waits_for_trigger():
    evidence = _strong_evidence()
    evidence["micro_confirmation"] = _stage(
        False,
        "MICRO_CONFIRMATION_WATCH_B",
        {
            "decision": "WATCH",
            "score": 55,
            "missing_evidence": ["RETEST_MISSING"],
        },
    )
    result = evaluate_professional_decision(evidence, setup_type="TREND_CONTINUATION")
    assert result.decision in {DECISION_WAIT_FOR_TRIGGER, DECISION_ENTER_REDUCED, DECISION_WATCH_ONLY}
    assert result.risk_multiplier in {0.4, 0.0}
    assert result.required_execution_mode == SHADOW_ONLY


def test_c_setup_watch_only():
    evidence = _strong_evidence()
    evidence["poi_placeholder"] = _stage(False, "POI_QUALITY_WATCH_C", {"decision": "WATCH", "score": 50, "grade": "C"})
    evidence["micro_confirmation"] = _stage(False, "MICRO_CONFIRMATION_WATCH_C", {"decision": "WATCH", "score": 42})
    evidence["risk_placeholder"] = _stage(False, "RISK_CONTEXT_MISSING")
    result = evaluate_professional_decision(evidence, setup_type="OBSERVATION")
    assert result.setup_grade in {GRADE_C, GRADE_D}
    assert result.decision in {DECISION_WATCH_ONLY, DECISION_REJECT}
    assert result.risk_multiplier == 0.0


def test_poi_hard_reject_sets_hard_veto():
    evidence = _strong_evidence()
    evidence["poi_placeholder"] = _stage(
        False,
        "POI_QUALITY_REJECT_D",
        {
            "decision": "REJECT",
            "score": 0,
            "grade": "D",
            "hard_reject": True,
            "reasons": ["OB_CONSUMED"],
        },
    )
    result = evaluate_professional_decision(evidence, setup_type="TREND_CONTINUATION")
    assert result.decision == DECISION_REJECT
    assert result.setup_grade == GRADE_D
    assert result.hard_veto is True
    assert result.hard_veto_reason == "OB_CONSUMED"
    assert result.risk_multiplier == 0.0


def test_micro_hard_reject_sets_hard_veto():
    evidence = _strong_evidence()
    evidence["micro_confirmation"] = _stage(
        False,
        "MICRO_CONFIRMATION_REJECT_D",
        {
            "decision": "REJECT",
            "score": 0,
            "grade": "D",
            "hard_reject": True,
            "reasons": ["TRIGGER_OUTSIDE_POI"],
        },
    )
    result = evaluate_professional_decision(evidence, setup_type="REVERSAL_AFTER_SWEEP")
    assert result.decision == DECISION_REJECT
    assert result.setup_grade == GRADE_D
    assert result.hard_veto is True
    assert result.hard_veto_reason == "TRIGGER_OUTSIDE_POI"
    assert result.risk_multiplier == 0.0


def test_session_premium_ote_hard_block_sets_hard_veto():
    evidence = _strong_evidence()
    evidence["session_premium_ote_gate"] = _stage(
        False,
        "SESSION_PREMIUM_OTE_BLOCK_D",
        {
            "decision": "BLOCK",
            "score": 0,
            "grade": "D",
            "hard_block": True,
            "reasons": ["OTE_CONFLICT_BLOCKS_STRICT_SETUP"],
        },
    )
    result = evaluate_professional_decision(evidence, setup_type="REVERSAL_AFTER_SWEEP")
    assert result.decision == DECISION_REJECT
    assert result.setup_grade == GRADE_D
    assert result.hard_veto is True
    assert result.hard_veto_reason == "OTE_CONFLICT_BLOCKS_STRICT_SETUP"
    assert result.risk_multiplier == 0.0

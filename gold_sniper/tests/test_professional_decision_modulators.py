from gold_sniper.strategy.professional_decision_engine import (
    DECISION_REJECT,
    SHADOW_ONLY,
    evaluate_professional_decision,
)
from gold_sniper.tests.test_professional_decision_engine import _stage, _strong_evidence


def test_timing_watch_reduces_professional_risk():
    evidence = _strong_evidence()
    evidence["session_premium_ote_gate"] = _stage(
        False,
        "SESSION_PREMIUM_OTE_WATCH_B",
        {
            "decision": "WATCH",
            "timing_quality_score": 55,
            "risk_multiplier": 0.4,
            "execution_readiness": "WATCH",
        },
    )
    result = evaluate_professional_decision(evidence, setup_type="TREND_CONTINUATION")
    assert result.risk_multiplier <= 0.4
    assert result.required_execution_mode == SHADOW_ONLY


def test_liquidity_block_zeroes_professional_risk():
    evidence = _strong_evidence()
    evidence["liquidity_state"] = _stage(
        False,
        "LIQUIDITY_BLOCKS_SETUP_NO_LIQUIDITY_STORY",
        {
            "decision": "BLOCK",
            "hard_block": True,
            "risk_multiplier": 0.0,
            "execution_readiness": "BLOCKED",
            "reasons": ["LIQUIDITY_STORY_MISSING_BLOCKS_SETUP"],
        },
    )
    result = evaluate_professional_decision(evidence, setup_type="TREND_CONTINUATION")
    assert result.decision == DECISION_REJECT
    assert result.hard_veto is True
    assert result.risk_multiplier == 0.0

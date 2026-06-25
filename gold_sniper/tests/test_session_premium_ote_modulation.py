from gold_sniper.strategy.session_premium_ote_gate import evaluate_session_premium_ote_gate


def test_strong_timing_pass_has_quality_and_multiplier():
    result = evaluate_session_premium_ote_gate(
        {
            "session_label": "NY",
            "setup_type": "TREND_CONTINUATION",
            "direction": "LONG",
            "premium_discount": "DISCOUNT",
            "in_ote": True,
            "fibonacci_anchor_valid": True,
            "news_clear": True,
        }
    )
    assert result.timing_quality_score >= 70
    assert result.risk_multiplier in {0.75, 1.0}
    assert result.execution_readiness in {"READY", "REDUCED"}


def test_strict_reversal_premium_conflict_zeroes_risk():
    result = evaluate_session_premium_ote_gate(
        {
            "session_label": "NY",
            "setup_type": "REVERSAL",
            "direction": "LONG",
            "premium_discount": "PREMIUM",
            "in_ote": False,
            "fibonacci_anchor_valid": True,
        }
    )
    assert result.hard_block is True
    assert result.risk_multiplier == 0.0
    assert result.execution_readiness == "BLOCKED"

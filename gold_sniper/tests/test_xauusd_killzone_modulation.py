from gold_sniper.strategy.xauusd_killzone_model import evaluate_xauusd_killzone


def test_ny_killzone_has_a_grade_and_full_multiplier():
    result = evaluate_xauusd_killzone("2025-01-15T13:30:00Z")
    assert result.session == "NY_KILLZONE"
    assert result.session_grade == "A"
    assert result.session_score >= 80
    assert result.risk_multiplier == 1.0
    assert result.is_hard_block is False


def test_asia_is_hard_block_zero_risk():
    result = evaluate_xauusd_killzone("2025-01-15T02:00:00Z")
    assert result.session == "ASIA"
    assert result.session_grade == "D"
    assert result.risk_multiplier == 0.0
    assert result.is_hard_block is True

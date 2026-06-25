from gold_sniper.strategy.atr_risk_model import adjust_risk_for_setup
from gold_sniper.strategy.unified_xauusd_strategy import _evaluate_atr_risk


def test_a_plus_keeps_full_risk_when_modulators_are_clean():
    result = adjust_risk_for_setup(
        setup_grade="A_PLUS",
        professional_multiplier=1.0,
        session_multiplier=1.0,
        timing_multiplier=1.0,
        liquidity_multiplier=1.0,
        atr_valid=True,
        hard_veto=False,
    )
    assert result["base_risk_pct"] == 1.0
    assert result["adjusted_risk_pct"] == 1.0
    assert result["risk_band"] == "FULL"


def test_a_grade_reduced_base_risk():
    result = adjust_risk_for_setup(setup_grade="A")
    assert result["base_risk_pct"] == 0.75
    assert result["adjusted_risk_pct"] <= 0.75


def test_b_grade_is_micro_or_reduced_not_full():
    result = adjust_risk_for_setup(setup_grade="B")
    assert 0.25 <= result["adjusted_risk_pct"] <= 0.5
    assert result["risk_band"] in {"MICRO", "REDUCED"}


def test_c_grade_zero_risk_watch():
    result = adjust_risk_for_setup(setup_grade="C")
    assert result["adjusted_risk_pct"] == 0.0
    assert result["risk_band"] == "ZERO"


def test_hard_veto_zeroes_risk():
    result = adjust_risk_for_setup(setup_grade="A_PLUS", hard_veto=True)
    assert result["adjusted_risk_pct"] == 0.0
    assert result["risk_multiplier"] == 0.0


def test_unified_atr_prepass_valid_atr_does_not_default_to_zero_risk():
    result = _evaluate_atr_risk(
        {
            "entry_price": 2000.0,
            "direction": "LONG",
            "atr": 2.0,
        },
        {},
    )
    assert result["passed"] is True
    assert result["reason"] == "ATR_RISK_VALID"
    value = result["value"]
    assert value["risk_valid"] is True
    assert value["risk_multiplier"] == 1.0
    assert value["adjusted_risk_pct"] == 1.0
    assert value["risk_band"] == "FULL"


def test_unified_atr_prepass_respects_explicit_setup_grade_b():
    result = _evaluate_atr_risk(
        {
            "entry_price": 2000.0,
            "direction": "LONG",
            "atr": 2.0,
            "context": {
                "setup_grade": "B",
            },
        },
        {},
    )
    assert result["passed"] is True
    value = result["value"]
    assert value["base_risk_pct"] == 0.4
    assert 0.25 <= value["adjusted_risk_pct"] <= 0.5
    assert value["risk_band"] in {"MICRO", "REDUCED"}


def test_unified_atr_prepass_invalid_atr_still_zeroes_risk():
    result = _evaluate_atr_risk(
        {
            "entry_price": 2000.0,
            "direction": "LONG",
            "atr": 0.0,
        },
        {},
    )
    assert result["passed"] is False
    value = result["value"]
    assert value["risk_valid"] is False
    assert value["risk_multiplier"] == 0.0
    assert value["adjusted_risk_pct"] == 0.0
    assert value["risk_band"] == "ZERO"

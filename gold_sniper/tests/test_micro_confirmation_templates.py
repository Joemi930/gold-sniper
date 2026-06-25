from gold_sniper.strategy.micro_confirmation_engine import (
    CONFIRMED,
    REJECT,
    TEMPLATE_CONTINUATION_LIGHT,
    TEMPLATE_FAILED_AUCTION_RECLAIM,
    TEMPLATE_REVERSAL_STRICT,
    TEMPLATE_SESSION_REVERSAL_MEDIUM,
    WATCH,
    evaluate_micro_confirmation,
)


def _poi_accept():
    return {"decision": "ACCEPT", "score": 88, "grade": "A"}


def _base_micro(**overrides):
    data = {
        "trigger_kind": "MICRO_CHOCH",
        "trigger_inside_poi": True,
        "displacement_present": True,
        "reclaim_confirmed": True,
        "retest_confirmed": True,
        "sweep_detected": True,
    }
    data.update(overrides)
    return data


def test_reversal_strict_requires_sweep():
    micro = _base_micro(sweep_detected=False)
    result = evaluate_micro_confirmation(
        micro,
        {"minimum_micro_template": TEMPLATE_REVERSAL_STRICT},
        _poi_accept(),
    )
    assert result.template_name == TEMPLATE_REVERSAL_STRICT
    assert result.decision in {WATCH, REJECT}
    assert "MICRO_SWEEP_MISSING_FOR_TEMPLATE" in result.missing_evidence
    assert result.execution_readiness != "READY"


def test_continuation_light_can_confirm_without_sweep_and_retest():
    micro = _base_micro(sweep_detected=False)
    micro.pop("retest_confirmed")
    result = evaluate_micro_confirmation(
        micro,
        {"minimum_micro_template": TEMPLATE_CONTINUATION_LIGHT},
        _poi_accept(),
    )
    assert result.template_name == TEMPLATE_CONTINUATION_LIGHT
    assert "MICRO_SWEEP_MISSING_FOR_TEMPLATE" not in result.missing_evidence
    assert "RETEST_MISSING" not in result.missing_evidence
    assert result.decision in {CONFIRMED, WATCH}


def test_failed_auction_reclaim_requires_sweep_and_reclaim():
    micro = _base_micro(sweep_detected=False, reclaim_confirmed=False)
    result = evaluate_micro_confirmation(
        micro,
        {"minimum_micro_template": TEMPLATE_FAILED_AUCTION_RECLAIM},
        _poi_accept(),
    )
    assert result.template_name == TEMPLATE_FAILED_AUCTION_RECLAIM
    assert "MICRO_SWEEP_MISSING_FOR_TEMPLATE" in result.missing_evidence
    assert "RECLAIM_OR_ACCEPTANCE_MISSING" in result.missing_evidence
    assert result.decision in {WATCH, REJECT}


def test_session_reversal_medium_does_not_require_retest():
    micro = _base_micro()
    micro.pop("retest_confirmed")
    result = evaluate_micro_confirmation(
        micro,
        {"minimum_micro_template": TEMPLATE_SESSION_REVERSAL_MEDIUM},
        _poi_accept(),
    )
    assert result.template_name == TEMPLATE_SESSION_REVERSAL_MEDIUM
    assert "RETEST_MISSING" not in result.missing_evidence

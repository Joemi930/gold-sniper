from gold_sniper.strategy.poi_quality_gate import (
    ACCEPT,
    EXECUTION_BLOCKED,
    EXECUTION_READY,
    EXECUTION_REDUCED,
    EXECUTION_WATCH,
    REJECT,
    WATCH,
    evaluate_poi_quality,
)


def _context(**overrides):
    data = {
        "session_label": "NY",
        "draw_on_liquidity": "BUY_SIDE",
        "liquidity_target_open": True,
        "htf_aligned": True,
        "dol_aligned": True,
        "order_flow_aligned": True,
        "sweep_detected": True,
        "in_ote": True,
        "trigger_kind": "MICRO_CHOCH",
        "displacement_present": True,
        "has_retest": True,
    }
    data.update(overrides)
    return data


def _ob(**overrides):
    data = {
        "normalized_poi_type": "OB",
        "high": 2050.0,
        "low": 2042.0,
        "lifecycle_normalized": "FRESH",
        "displacement_after_ob": True,
        "aligned_with_context": True,
        "has_fvg": True,
        "liquidity_sweep_before": True,
        "is_extreme_ob": True,
        "touch_count": 1,
    }
    data.update(overrides)
    return data


def test_fresh_ob_exposes_quality_score_and_readiness():
    result = evaluate_poi_quality(_ob(), _context())
    assert result.decision == ACCEPT
    assert result.invalidity_reason is None
    assert result.quality_score == result.score
    assert result.execution_readiness in {EXECUTION_READY, EXECUTION_REDUCED}


def test_consumed_ob_exposes_invalidity_reason_and_blocked_readiness():
    result = evaluate_poi_quality(_ob(lifecycle_normalized="CONSUMED"), _context())
    assert result.decision == REJECT
    assert result.hard_reject is True
    assert result.invalidity_reason == "OB_CONSUMED"
    assert result.execution_readiness == EXECUTION_BLOCKED
    assert result.quality_score == 0.0


def test_partial_mitigation_never_ready():
    result = evaluate_poi_quality(
        _ob(lifecycle_normalized="PARTIALLY_MITIGATED", penetration_pct=0.35),
        _context(),
    )
    assert result.decision == WATCH
    assert result.execution_readiness in {EXECUTION_WATCH, EXECUTION_REDUCED}
    assert result.execution_readiness != EXECUTION_READY

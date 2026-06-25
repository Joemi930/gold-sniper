from gold_sniper.replay.replay_metrics import build_replay_metrics


def test_replay_metrics_include_modulator_distributions():
    metrics = build_replay_metrics(
        [
            {
                "decision": "ENTER_FULL",
                "score": 90,
                "confidence": 0.9,
                "setup_type": "OTE_CONFLUENCE",
                "setup_grade": "A_PLUS",
                "risk_multiplier": 1.0,
                "final_risk_multiplier": 1.0,
                "session": "NY_KILLZONE",
                "session_grade": "A",
                "risk_band": "FULL",
                "micro_template": "continuation_light",
                "poi_execution_readiness": "READY",
                "micro_execution_readiness": "READY",
                "timing_execution_readiness": "READY",
                "liquidity_execution_readiness": "READY",
                "timing_quality_score": 88,
                "liquidity_quality_score": 85,
                "poi_quality_score": 92,
            },
            {
                "decision": "WATCH_ONLY",
                "score": 52,
                "confidence": 0.52,
                "setup_type": "TREND_CONTINUATION",
                "setup_grade": "C",
                "risk_multiplier": 0.0,
                "final_risk_multiplier": 0.0,
                "session": "LONDON_CLOSE",
                "session_grade": "B",
                "risk_band": "ZERO",
                "micro_template": "continuation_light",
                "poi_execution_readiness": "WATCH",
                "micro_execution_readiness": "WATCH",
                "timing_execution_readiness": "WATCH",
                "liquidity_execution_readiness": "WATCH",
                "timing_quality_score": 45,
                "liquidity_quality_score": 50,
                "poi_quality_score": 55,
            },
        ],
        symbol="XAUUSD",
        timeframe="M1",
        date_start=None,
        date_end=None,
        data_profile={},
    )
    assert "risk_multiplier_distribution" in metrics
    assert "setup_grade_distribution" in metrics
    assert "session_grade_distribution" in metrics
    assert "micro_template_distribution" in metrics
    assert metrics["average_final_risk_multiplier"] == 0.5

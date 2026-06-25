from gold_sniper.strategy.kasper_ict_scenario_engine import evaluate_kasper_ict_scenarios


def test_reversal_after_sweep_declares_reversal_strict_template():
    result = evaluate_kasper_ict_scenarios(
        {
            "session_allowed": True,
            "news_clear": True,
            "htf_context_available": True,
            "sweep_rejected": True,
            "poi_available": True,
            "poi_grade": "A",
            "micro_confirmed": True,
            "risk_valid": True,
        }
    )
    best = result["best_scenario"]
    assert best["scenario_type"] == "REVERSAL_AFTER_SWEEP"
    assert best["minimum_micro_template"] == "reversal_strict"


def test_failed_auction_declares_failed_auction_reclaim_template():
    result = evaluate_kasper_ict_scenarios(
        {
            "session_allowed": True,
            "news_clear": True,
            "auction_failed": True,
            "reclaim_confirmed": True,
            "sweep_detected": True,
            "risk_valid": True,
        }
    )
    scenarios = result["scenarios"]
    failed = next(item for item in scenarios if item["scenario_type"] == "FAILED_AUCTION")
    assert failed["minimum_micro_template"] == "failed_auction_reclaim"

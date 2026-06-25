from gold_sniper.strategy.poi_star_rating import evaluate_poi_star_rating


def test_5_star_ob_has_ready_readiness():
    result = evaluate_poi_star_rating(
        {
            "poi_type": "OB",
            "has_fvg": True,
            "sweep_before_creation": True,
            "extreme_of_range": True,
            "lifecycle": "FRESH",
            "created_session": "NY",
        },
        {},
    )
    assert result.stars == 5
    assert result.grade == "A+"
    assert result.invalidity_reason is None
    assert result.quality_score == 100.0
    assert result.execution_readiness == "READY"


def test_invalid_star_rating_exposes_invalidity_reason():
    result = evaluate_poi_star_rating({"poi_type": "OB"}, {})
    assert result.grade in {"C", "INVALID"}
    if result.grade == "INVALID":
        assert result.invalidity_reason is not None
        assert result.execution_readiness == "BLOCKED"

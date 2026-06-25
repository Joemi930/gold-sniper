"""POI star rating for Kasper/ICT XAUUSD scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class PoiStarRating:
    poi_type: str
    stars: int
    grade: str
    is_5_star_ob: bool
    is_5_star_poi: bool
    is_3_star_fvg: bool
    criteria: dict[str, bool]
    failure_reasons: list[str]
    near_miss: bool
    invalidity_reason: str | None = None
    quality_score: float = 0.0
    execution_readiness: str = "WATCH"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_poi_star_rating(poi: dict[str, Any] | None, context: dict[str, Any] | None = None) -> PoiStarRating:
    data = poi or {}
    ctx = context or {}
    poi_type = str(data.get("poi_type") or data.get("type") or "NONE").upper()
    bpr_available = _truthy(data.get("bpr_available") or data.get("balanced_price_range") or ctx.get("bpr_available"))
    criteria = {
        "has_imbalance": _truthy(data.get("has_fvg") or data.get("imbalance_attached") or ctx.get("has_imbalance")),
        "sweep_before_creation": _truthy(data.get("sweep_before_creation") or data.get("liquidity_sweep") or ctx.get("sweep_detected")),
        "extreme_position": _truthy(data.get("extreme_of_range") or ctx.get("extreme_position")),
        "unmitigated": str(data.get("lifecycle") or data.get("freshness") or "").upper() in {"FRESH", "UNMITIGATED"},
        "major_session_created": str(data.get("created_session") or ctx.get("session") or ctx.get("session_label") or "").upper() in {"LONDON_KILLZONE", "NY_KILLZONE", "LONDON", "NY", "SILVER_BULLET"},
        "bpr_available": bpr_available,
    }
    star_criteria = {key: value for key, value in criteria.items() if key != "bpr_available"}
    failures = [key.upper() + "_MISSING" for key, value in star_criteria.items() if not value]
    stars = sum(1 for value in star_criteria.values() if value)
    if poi_type == "FVG":
        aligned = _truthy(data.get("aligned_with_bias") or ctx.get("aligned_with_bias"))
        unmitigated = criteria["unmitigated"]
        pd_ok = _truthy(data.get("premium_discount_ok") or ctx.get("premium_discount_ok") or ctx.get("inside_discount_or_premium"))
        is_3_star = aligned and unmitigated and pd_ok
        fvg_stars = 3 if is_3_star else min(stars, 2)
        grade = "B" if is_3_star else "C"
        invalidity = failures[0] if grade == "INVALID" and failures else None
        return PoiStarRating(
            "FVG",
            fvg_stars,
            grade,
            False,
            False,
            is_3_star,
            criteria,
            failures,
            not is_3_star and stars >= 2,
            invalidity,
            _score_from_stars(fvg_stars, "FVG"),
            _readiness_from_grade(grade, not is_3_star and stars >= 2, invalidity),
        )
    grade = "A+" if stars == 5 else "A" if stars == 4 else "B" if stars == 3 else "C" if stars >= 2 else "INVALID"
    ob_like = poi_type in {"OB", "OB_FVG_STACK"}
    invalidity = (failures[0] if failures else "POI_STAR_INVALID") if grade == "INVALID" else None
    near_miss = stars == 4
    return PoiStarRating(
        poi_type,
        stars,
        grade,
        ob_like and stars == 5,
        ob_like and stars == 5,
        False,
        criteria,
        failures,
        near_miss,
        invalidity,
        _score_from_stars(stars, poi_type),
        _readiness_from_grade(grade, near_miss, invalidity),
    )


def _score_from_stars(stars: int, poi_type: str) -> float:
    if poi_type == "FVG":
        return 75.0 if stars >= 3 else 55.0 if stars == 2 else 30.0
    return min(max(float(stars) * 20.0, 0.0), 100.0)


def _readiness_from_grade(grade: str, near_miss: bool, invalidity_reason: str | None) -> str:
    if invalidity_reason:
        return "BLOCKED"
    if grade in {"A+", "A"} and not near_miss:
        return "READY"
    if grade in {"A+", "A", "B"}:
        return "REDUCED"
    if grade == "C":
        return "WATCH"
    return "BLOCKED"


def _truthy(value: Any) -> bool:
    return str(value).upper() in {"TRUE", "1", "YES", "Y"} if isinstance(value, str) else bool(value)

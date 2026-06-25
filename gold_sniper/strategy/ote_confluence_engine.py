"""OTE confluence model for Kasper/ICT XAUUSD scenarios."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class OteConfluence:
    range_valid: bool
    fib_anchor_valid: bool
    ote_low: float | None
    ote_high: float | None
    level_0705: float | None
    inside_ote: bool
    inside_discount_or_premium: bool
    confluence_with_poi: bool
    scenario_ready: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_ote_confluence(range_low: Any, range_high: Any, price: Any, direction: str, *, poi_confluence: bool = False) -> OteConfluence:
    low = _num(range_low)
    high = _num(range_high)
    current = _num(price)
    direction = str(direction or "UNKNOWN").upper()
    if low is None or high is None or current is None or high <= low:
        return OteConfluence(False, False, None, None, None, False, False, poi_confluence, False)
    span = high - low
    if direction == "LONG":
        ote_low, ote_high = high - span * 0.79, high - span * 0.62
        pd_ok = current <= low + span * 0.5
    elif direction == "SHORT":
        ote_low, ote_high = low + span * 0.62, low + span * 0.79
        pd_ok = current >= low + span * 0.5
    else:
        ote_low, ote_high, pd_ok = low + span * 0.62, low + span * 0.79, False
    inside = ote_low <= current <= ote_high
    level_0705 = high - span * 0.705 if direction == "LONG" else low + span * 0.705
    return OteConfluence(True, True, ote_low, ote_high, level_0705, inside, pd_ok, poi_confluence, inside and pd_ok and poi_confluence)


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

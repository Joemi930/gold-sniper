"""P2-E Phase18 — Shadow live-like policy for replay trade sizing, daily limits, and grade-based risk.

This module is replay-only. It does not connect to any broker, does not send orders,
and does not modify the global risk_allocator or risk mapping.

Architecture rule:
    ShadowLivePolicy is a pure data/config object consumed by SimulatedTradeManager.
    It never decides that a setup is good — it only gates and sizes trades that PDE
    has already validated (enter_eligible=True, risk_allowed=True, risk_multiplier>0).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Grade → risk % of current equity ──────────────────────────────────────────
# These are shadow/replay-only and MUST NOT replace risk_allocator global mapping.
# Base scalping: A+ 1% / A 0.75% / B 0.5%. La doctrine intraday (A+ 10% / A 7.5% /
# B 5%) s'active via $env:GS_RISK_SCALE="10" — réversible, testable, sans toucher au code.
import os as _os
_RISK_SCALE = float(_os.environ.get("GS_RISK_SCALE", "1.0") or "1.0")
GRADE_RISK_PCT: dict[str, float] = {
    "A_PLUS": 1.00 * _RISK_SCALE,
    "A+": 1.00 * _RISK_SCALE,
    "A": 0.75 * _RISK_SCALE,
    "B": 0.50 * _RISK_SCALE,
    "C_CONFIRMED": 0.25 * _RISK_SCALE,
    "C": 0.00,
    "D": 0.00,
    "UNKNOWN": 0.00,
}

# Grades that are allowed to open a trade at all.
EXECUTABLE_GRADES: set[str] = {"A_PLUS", "A+", "A", "B", "C_CONFIRMED"}

# Grades that can use the exceptional daily slot (3rd trade/day).
EXCEPTION_GRADES: set[str] = {"A_PLUS", "A+", "A"}

# ── P3 Two-Leg Risk Split ──────────────────────────────────────────────────────
# Each leg gets half the parent risk. Leg 1 targets TP1 (1R), Leg 2 targets TP2 (2R).

LEG_RISK_SPLIT: float = 0.5  # Each leg gets 50% of total risk

LEG_TARGET_RR: dict[int, float] = {
    1: 1.0,   # leg_1 → TP1
    2: 2.0,   # leg_2 → TP2
}

# P3: Protected SL offset for runner leg after TP1 hit (in R multiples)
PROTECTED_RUNNER_SL_R: float = 0.5

# ── Daily trade limiter ───────────────────────────────────────────────────────


@dataclass
class DailyTradeCounter:
    """Tracks trades opened per calendar day (UTC from candle time)."""

    day: str
    standard_count: int = 0
    exceptional_used: bool = False

    @property
    def total(self) -> int:
        return self.standard_count + (1 if self.exceptional_used else 0)

    def record_standard(self) -> None:
        self.standard_count += 1

    def record_exceptional(self) -> None:
        self.exceptional_used = True

    def reset(self) -> None:
        self.standard_count = 0
        self.exceptional_used = False


# ── Shadow live policy ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ShadowLivePolicy:
    """Immutable policy config for shadow trade simulation.

    All fields are replay-only and do NOT modify global risk_allocator.
    """

    initial_equity: float = 100.0
    max_standard_trades_per_day: int = 2
    allow_one_exceptional_trade: bool = True
    max_absolute_trades_per_day: int = 3
    tp1_rr: float = 1.0
    tp2_rr: float = 2.0
    partial_close_pct: float = 50.0
    be_plus_r: float = 0.5
    min_risk_cash: float = 0.01
    min_stop_distance: float = 0.01

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_equity": self.initial_equity,
            "max_standard_trades_per_day": self.max_standard_trades_per_day,
            "allow_one_exceptional_trade": self.allow_one_exceptional_trade,
            "max_absolute_trades_per_day": self.max_absolute_trades_per_day,
            "tp1_rr": self.tp1_rr,
            "tp2_rr": self.tp2_rr,
            "partial_close_pct": self.partial_close_pct,
            "be_plus_r": self.be_plus_r,
            "min_risk_cash": self.min_risk_cash,
            "min_stop_distance": self.min_stop_distance,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────


def normalize_grade(raw: Any) -> str:
    """Normalize any grade representation to the canonical set used by GRADE_RISK_PCT."""
    grade = str(raw or "UNKNOWN").upper().replace("+", "_PLUS")
    if grade == "A_PLUS":
        return "A_PLUS"
    if grade == "C_CONFIRMED":
        return "C_CONFIRMED"
    if grade in {"A", "B", "C", "D"}:
        return grade
    return "UNKNOWN"


def leg_risk_pct(parent_grade: str) -> tuple[float, float]:
    """Return (leg_1_risk_pct, leg_2_risk_pct) for a given parent grade.

    Each leg receives half the parent's total risk percentage.
    """
    total = risk_pct_for_grade(parent_grade)
    half = round(total * LEG_RISK_SPLIT, 6)
    return half, half


def risk_pct_for_grade(raw_grade: Any) -> float:
    """Return the risk percentage of equity for a given grade (0.0-1.0 range)."""
    grade = normalize_grade(raw_grade)
    return float(GRADE_RISK_PCT.get(grade, 0.0))


def grade_is_executable(raw_grade: Any) -> bool:
    """True if this grade is allowed to open a shadow trade at all."""
    return normalize_grade(raw_grade) in EXECUTABLE_GRADES


def grade_allows_daily_exception(raw_grade: Any) -> bool:
    """True if this grade can use the exceptional 3rd trade slot."""
    return normalize_grade(raw_grade) in EXCEPTION_GRADES


def can_open_shadow_trade(
    *,
    policy: ShadowLivePolicy,
    counters: dict[str, DailyTradeCounter],
    candle_time_str: str,
    grade: str,
) -> tuple[bool, str]:
    """Check whether a new shadow trade can be opened given the daily limits.

    Returns (allowed: bool, reason: str).
    """
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(str(candle_time_str).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False, "INVALID_CANDLE_TIME"

    day = dt.date().isoformat()
    counter = counters.get(day)
    if counter is None:
        counter = DailyTradeCounter(day=day)
        counters[day] = counter

    total = counter.total

    if total >= policy.max_absolute_trades_per_day:
        return False, "DAILY_MAX_ABSOLUTE_REACHED"

    if counter.standard_count < policy.max_standard_trades_per_day:
        return True, "STANDARD_SLOT_AVAILABLE"

    if policy.allow_one_exceptional_trade and not counter.exceptional_used:
        if grade_allows_daily_exception(grade):
            return True, "EXCEPTIONAL_SLOT_AVAILABLE"
        return False, "DAILY_LIMIT_REACHED_GRADE_NOT_EXCEPTIONAL"

    return False, "DAILY_LIMIT_REACHED"


def record_trade_opened(
    *,
    counters: dict[str, DailyTradeCounter],
    candle_time_str: str,
    reason: str,
) -> None:
    """Update the daily counter after a trade is opened."""
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(str(candle_time_str).replace("Z", "+00:00"))
        day = dt.date().isoformat()
    except (ValueError, TypeError):
        return

    counter = counters.get(day)
    if counter is None:
        counter = DailyTradeCounter(day=day)
        counters[day] = counter

    if reason == "EXCEPTIONAL_SLOT_AVAILABLE":
        counter.record_exceptional()
    else:
        counter.record_standard()


def worst_case_effective_risk_points(
    *,
    side: str,
    requested_entry: float,
    stop_loss: float,
    spread_points: float = 0.0,
    slippage_points: float = 0.0,
    commission_per_lot: float = 0.0,
) -> dict[str, float]:
    """Compute worst-case effective risk points including entry and exit costs.

    For a SELL:
        effective_entry = requested_entry - half_spread - slippage
        effective_sl_exit = stop_loss + half_spread + slippage
        risk_points = effective_sl_exit - effective_entry

    For a BUY:
        effective_entry = requested_entry + half_spread + slippage
        effective_sl_exit = stop_loss - half_spread - slippage
        risk_points = effective_entry - effective_sl_exit

    Returns dict with effective_entry, effective_sl_exit, effective_risk_points,
    structural_risk_points, spread_points, slippage_points, commission_per_lot.
    """
    side = str(side).upper()
    half_spread = float(spread_points) / 2.0
    slippage = float(slippage_points)

    structural_risk_points = abs(float(requested_entry) - float(stop_loss))

    if side == "BUY":
        effective_entry = float(requested_entry) + half_spread + slippage
        effective_sl_exit = float(stop_loss) - half_spread - slippage
        effective_risk_points = effective_entry - effective_sl_exit
    elif side == "SELL":
        effective_entry = float(requested_entry) - half_spread - slippage
        effective_sl_exit = float(stop_loss) + half_spread + slippage
        effective_risk_points = effective_sl_exit - effective_entry
    else:
        raise ValueError(f"unsupported_side:{side}")

    if effective_risk_points <= 0:
        raise ValueError("INVALID_EFFECTIVE_RISK_POINTS")

    return {
        "effective_entry": round(effective_entry, 6),
        "effective_sl_exit": round(effective_sl_exit, 6),
        "effective_risk_points": round(effective_risk_points, 6),
        "structural_risk_points": round(structural_risk_points, 6),
        "spread_points": round(spread_points, 6),
        "slippage_points": round(slippage_points, 6),
        "commission_per_lot": float(commission_per_lot),
    }


def compute_shadow_position_size(
    *,
    equity: float,
    grade: str,
    entry: float,
    stop_loss: float,
    policy: ShadowLivePolicy | None = None,
    spread_points: float = 0.0,
    slippage_points: float = 0.0,
    side: str = "BUY",
    commission_per_lot: float = 0.0,
) -> dict[str, float]:
    """Compute risk_cash and volume from current equity and grade-based risk %.

    Uses worst-case effective risk points (including spread, slippage, and exit
    costs) so that a full SL loss stays within the intended risk budget.

    Raises ValueError with a descriptive code if sizing is impossible.
    """
    if policy is None:
        policy = ShadowLivePolicy()

    risk_pct = risk_pct_for_grade(grade)
    if risk_pct <= 0.0:
        raise ValueError("GRADE_NOT_EXECUTABLE")

    risk_cash = round(float(equity) * risk_pct / 100.0, 6)

    if risk_cash < policy.min_risk_cash:
        raise ValueError("RISK_CASH_TOO_SMALL")

    # Compute worst-case effective risk (entry costs + exit costs)
    worst_case = worst_case_effective_risk_points(
        side=side,
        requested_entry=float(entry),
        stop_loss=float(stop_loss),
        spread_points=float(spread_points),
        slippage_points=float(slippage_points),
        commission_per_lot=float(commission_per_lot),
    )
    effective_risk_points = worst_case["effective_risk_points"]
    structural_risk_points = worst_case["structural_risk_points"]

    if effective_risk_points < policy.min_stop_distance:
        raise ValueError("STOP_DISTANCE_TOO_SMALL")

    # Volume sized from worst-case effective risk, not structural risk
    volume = round(risk_cash / effective_risk_points, 6)
    if volume <= 0.0:
        raise ValueError("VOLUME_ZERO")

    # ── Levier broker (JustMarkets 1:2000): plafond de marge ──
    # marge requise = volume × contract_size × prix / levier. Le volume est
    # plafonné pour que la marge requise ne dépasse jamais l'équité disponible.
    # Modélise la contrainte réelle du compte au lieu de supposer une marge infinie.
    try:
        from config import ACCOUNT_LEVERAGE, XAUUSD_CONTRACT_SIZE
        _price = float(entry) if float(entry) > 0 else 0.0
        if _price > 0 and ACCOUNT_LEVERAGE > 0:
            _max_vol = (float(equity) * float(ACCOUNT_LEVERAGE)) / (float(XAUUSD_CONTRACT_SIZE) * _price)
            if volume > _max_vol:
                volume = round(_max_vol, 6)
                if volume <= 0.0:
                    raise ValueError("MARGIN_CAP_ZERO")
    except ImportError:
        pass

    # Expected loss at SL if filled at worst-case prices
    expected_sl_loss = round(volume * effective_risk_points, 6)
    risk_realism_ratio = round(expected_sl_loss / risk_cash, 6) if risk_cash > 0 else 1.0

    return {
        "risk_pct": risk_pct,
        "risk_cash": risk_cash,
        "risk_points": structural_risk_points,
        "effective_risk_points": effective_risk_points,
        "volume": volume,
        "expected_sl_loss": expected_sl_loss,
        "risk_realism_ratio": risk_realism_ratio,
        "effective_entry_estimate": worst_case["effective_entry"],
        "effective_sl_exit_estimate": worst_case["effective_sl_exit"],
    }

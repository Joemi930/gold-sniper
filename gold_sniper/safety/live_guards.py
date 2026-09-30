"""Shared live/replay trade guards — single source of truth.

These pure functions are importable by BOTH the live execution path
(execution/trade_manager.py) AND the replay path (replay/simulated_trade_manager.py).
They encode the 5 validated anti-relapse guards with NO side effects.

Usage in live: call before sending a broker order in TradeManager.place_order()
Usage in replay: import and call from SimulatedTradeManager (replace inline methods)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


# ── Guard result type ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class GuardResult:
    """Result of a single guard check. `blocked` is None if allowed, else the reason."""
    blocked: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _p(x: str) -> datetime:
    """Parse ISO timestamp string to datetime."""
    return datetime.fromisoformat(str(x).replace("Z", "+00:00"))


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Guard 1: RR Filter ───────────────────────────────────────────────────
# Validated OOS: rr>=4 positive 2024/2025/2026; rr<4 loses.


def min_rr_block(rr_estimate: float | None, *, min_rr: float = 4.0) -> GuardResult:
    """Block entries whose structural reward:risk is below the validated floor.

    Args:
        rr_estimate: The rr_estimate from the decision payload (causal, no lookahead).
        min_rr: Threshold (from config.MIN_RR, default 4.0). 0 = off.

    Returns:
        GuardResult with blocked="MIN_RR" if filtered, else blocked=None.
    """
    if min_rr <= 0:
        return GuardResult()
    if rr_estimate is None:
        return GuardResult()  # fail-open: no estimate → don't block
    if _safe_float(rr_estimate) < min_rr:
        return GuardResult(blocked="MIN_RR", diagnostics={"rr_estimate": rr_estimate, "min_rr": min_rr})
    return GuardResult()


# ── Guard 2: Cap Concurrent ───────────────────────────────────────────────
# The real driver of big drawdowns: same-direction positions piling up.


def concurrency_block(
    active_positions: dict[int, dict[str, Any]],
    side: str,
    *,
    max_concurrent: int = 0,
    max_concurrent_same_side: int = 0,
) -> GuardResult:
    """Block if concurrent position limits are exceeded.

    Args:
        active_positions: Currently open position dict (ticket → trade dict).
        side: Trade direction (BUY/SELL).
        max_concurrent: Max total positions (0 = unlimited).
        max_concurrent_same_side: Max per direction (0 = unlimited).

    Returns:
        GuardResult.
    """
    if max_concurrent > 0 and len(active_positions) >= max_concurrent:
        return GuardResult(
            blocked="MAX_CONCURRENT",
            diagnostics={"total": len(active_positions), "max": max_concurrent},
        )
    if max_concurrent_same_side > 0:
        same = sum(1 for t in active_positions.values() if str(t.get("type", "")) == str(side))
        if same >= max_concurrent_same_side:
            return GuardResult(
                blocked="MAX_CONCURRENT_SAME_SIDE",
                diagnostics={"same_side": same, "max": max_concurrent_same_side},
            )
    return GuardResult()


# ── Guard 3: Rolling Drawdown ─────────────────────────────────────────────
# Losing-regime protection: pause new entries when equity drops N% from peak.


@dataclass
class RollingDDState:
    """Mutable state for the rolling drawdown guard. Owned by the caller."""
    pause_until: datetime | None = None
    pause_count: int = 0
    rebase_equity: float | None = None


def rolling_dd_block(
    *,
    equity: float,
    peak_equity: float,
    dd_pct: float,
    pause_days: float,
    state: RollingDDState,
    candle_time: str,
) -> GuardResult:
    """Block new entries during a rolling drawdown pause.

    When realized equity is >= dd_pct below peak, pause for pause_days.
    After pause, rebase peak and resume.

    Args:
        equity: Current realized equity.
        peak_equity: Running peak equity (updated externally on new peak or rebase).
        dd_pct: Drawdown threshold in percent (e.g. 10.0).
        pause_days: Pause duration in days (e.g. 7.0).
        state: Mutable state object owned by the caller.
        candle_time: Current candle ISO timestamp.

    Returns:
        GuardResult.
    """
    if dd_pct <= 0:
        return GuardResult()

    now = _p(candle_time)

    # Still in pause?
    if state.pause_until is not None and now < state.pause_until:
        return GuardResult(
            blocked="ROLLING_DD_PAUSE",
            diagnostics={"pause_until": state.pause_until.isoformat(), "equity": equity, "peak": peak_equity},
        )

    if state.pause_until is not None and now >= state.pause_until:
        state.pause_until = None
        state.rebase_equity = equity
        return GuardResult(diagnostics={"rolling_dd_rebase_equity": equity})

    # Check threshold
    if peak_equity > 0:
        dd = (peak_equity - equity) / peak_equity * 100.0
        if dd >= dd_pct:
            state.pause_until = now + timedelta(days=pause_days)
            state.pause_count += 1
            state.rebase_equity = equity
            return GuardResult(
                blocked="ROLLING_DD_PAUSE",
                diagnostics={"dd_pct": round(dd, 2), "threshold": dd_pct, "pause_days": pause_days, "pause_count": state.pause_count},
            )

    return GuardResult()


# ── Guard 4: Loss Breaker ─────────────────────────────────────────────────
# Stop trading for the day after N full-SL losses.


@dataclass
class LossBreakerState:
    """Mutable state for the loss breaker guard. Owned by the caller."""
    slg_day: str = ""
    slg_count: int = 0


def loss_breaker_block(
    *,
    candle_time: str,
    max_sl_per_day: int,
    state: LossBreakerState,
) -> GuardResult:
    """Block if the daily SL loss count has been reached.

    Args:
        candle_time: Current candle ISO timestamp.
        max_sl_per_day: Max SL losses per day (from config.LOSS_BREAKER_MAX_SL_PER_DAY). 0 = off.
        state: Mutable state object.

    Returns:
        GuardResult.
    """
    if max_sl_per_day <= 0:
        return GuardResult()

    day = str(candle_time)[:10]
    if state.slg_day == day and state.slg_count >= max_sl_per_day:
        return GuardResult(
            blocked="DAILY_LOSS_BREAKER",
            diagnostics={"day": day, "sl_count": state.slg_count, "max": max_sl_per_day},
        )
    return GuardResult()


def loss_breaker_record_sl(candle_time: str, state: LossBreakerState) -> None:
    """Record a full-SL loss for the loss breaker.

    Call this when a trade hits its full stop-loss (not protected/trailing exit).
    """
    day = str(candle_time)[:10]
    if state.slg_day != day:
        state.slg_day = day
        state.slg_count = 1
    else:
        state.slg_count += 1


# ── Guard 5: Loss Cooldown ────────────────────────────────────────────────
# Block re-entries in the same direction for N minutes after a full SL.


@dataclass
class LossCooldownState:
    """Mutable state for the loss cooldown guard. Owned by the caller."""
    last_sl_time: dict[str, str] = field(default_factory=dict)  # side → ISO timestamp


def loss_cooldown_block(
    *,
    candle_time: str,
    side: str,
    cooldown_min: int,
    state: LossCooldownState,
) -> GuardResult:
    """Block re-entry in the same direction within N minutes of a full SL.

    Args:
        candle_time: Current candle ISO timestamp.
        side: Trade direction (BUY/SELL).
        cooldown_min: Cooldown duration in minutes. 0 = off.
        state: Mutable state object.

    Returns:
        GuardResult.
    """
    if cooldown_min <= 0:
        return GuardResult()

    last = state.last_sl_time.get(str(side))
    if last:
        delta_min = (_p(candle_time) - _p(last)).total_seconds() / 60.0
        if 0 <= delta_min < cooldown_min:
            return GuardResult(
                blocked="LOSS_COOLDOWN_SAME_SIDE",
                diagnostics={"side": side, "minutes_since_sl": round(delta_min, 1), "cooldown_min": cooldown_min},
            )
    return GuardResult()


def loss_cooldown_record_sl(candle_time: str, side: str, state: LossCooldownState) -> None:
    """Record a full-SL event for the cooldown guard."""
    state.last_sl_time[str(side)] = str(candle_time)


# ── Composite: Run all 5 guards at once ────────────────────────────────────


def run_all_live_guards(
    *,
    rr_estimate: float | None,
    active_positions: dict[int, dict[str, Any]],
    side: str,
    equity: float,
    peak_equity: float,
    candle_time: str,
    # Config
    min_rr: float = 0.0,
    max_concurrent: int = 0,
    max_concurrent_same_side: int = 0,
    rolling_dd_pct: float = 0.0,
    rolling_dd_pause_days: float = 7.0,
    loss_breaker_max: int = 0,
    loss_cooldown_min: int = 0,
    # State (owned by caller, mutated in-place)
    dd_state: RollingDDState | None = None,
    lb_state: LossBreakerState | None = None,
    lc_state: LossCooldownState | None = None,
) -> list[GuardResult]:
    """Run all 5 guards in the canonical order. Returns list of blocking results.

    A non-empty result means at least one guard blocked — the trade MUST be rejected.
    The first blocking guard's reason is the canonical rejection reason.
    """
    if dd_state is None:
        dd_state = RollingDDState()
    if lb_state is None:
        lb_state = LossBreakerState()
    if lc_state is None:
        lc_state = LossCooldownState()

    results: list[GuardResult] = []

    # 1. RR filter
    r = min_rr_block(rr_estimate, min_rr=min_rr)
    if r.blocked:
        results.append(r)

    # 2. Cap concurrent
    r = concurrency_block(active_positions, side, max_concurrent=max_concurrent, max_concurrent_same_side=max_concurrent_same_side)
    if r.blocked:
        results.append(r)

    # 3. Rolling DD
    r = rolling_dd_block(equity=equity, peak_equity=peak_equity, dd_pct=rolling_dd_pct, pause_days=rolling_dd_pause_days, state=dd_state, candle_time=candle_time)
    if r.blocked:
        results.append(r)

    # 4. Loss breaker
    r = loss_breaker_block(candle_time=candle_time, max_sl_per_day=loss_breaker_max, state=lb_state)
    if r.blocked:
        results.append(r)

    # 5. Cooldown
    r = loss_cooldown_block(candle_time=candle_time, side=side, cooldown_min=loss_cooldown_min, state=lc_state)
    if r.blocked:
        results.append(r)

    return results


def loss_guard_diag(results: list[GuardResult]) -> dict[str, Any]:
    """Build a diagnostic summary from guard results, compatible with replay's format."""
    blocks = [r for r in results if r.blocked]
    return {
        "blocked": len(blocks) > 0,
        "primary_blocker": blocks[0].blocked if blocks else None,
        "all_blockers": [r.blocked for r in blocks],
        "diagnostics": {r.blocked: r.diagnostics for r in blocks if r.blocked},
    }

"""P4.2 — MetricsAggregator with NO_TRADES state.

Incremental counters for decisions, trades, and lifecycle events.
Produces honest metrics: when trade_count=0, winrate/expectancy=None
(not 0%), top rejection reasons visible, no synthetic trades.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MetricsAggregator:
    """Collect and aggregate metrics during a replay run.

    All counters are incremental — called per candle/decision.
    Finalize produces a compact summary dict.
    """

    # ── counters ──────────────────────────────────────────────────────
    candle_count: int = 0
    eval_candle_count: int = 0
    warmup_candle_count: int = 0
    decision_count: int = 0
    candidate_count: int = 0
    window_count: int = 0

    # Decision breakdown
    enter_full_count: int = 0
    enter_reduced_count: int = 0
    wait_count: int = 0
    reject_count: int = 0

    # Trade lifecycle
    trade_count: int = 0
    tp1_count: int = 0
    tp2_count: int = 0
    sl_count: int = 0
    protected_sl_count: int = 0
    breakeven_count: int = 0

    # P&L (in R units)
    total_pnl_r: float = 0.0
    total_cost_drag_r: float = 0.0
    winner_count: int = 0
    loser_count: int = 0

    # Rejection tracking
    reject_reasons: Counter = field(default_factory=Counter)
    veto_codes: Counter = field(default_factory=Counter)
    setup_type_counts: Counter = field(default_factory=Counter)
    gate_rejections: Counter = field(default_factory=Counter)

    # Grade distribution
    grade_counts: Counter = field(default_factory=Counter)

    # POI_REACTION diagnostic
    poi_reaction_skipped: int = 0

    # Synthetic trade guard
    _synthetic_trades_detected: int = 0

    # ── record methods ────────────────────────────────────────────────

    def record_candle(self, eval_active: bool = True) -> None:
        self.candle_count += 1
        if eval_active:
            self.eval_candle_count += 1
        else:
            self.warmup_candle_count += 1

    def record_decision(self, decision: str, setup_type: str | None = None,
                        setup_grade: str | None = None,
                        reject_reason: str | None = None,
                        veto_code: str | None = None) -> None:
        """Record one decision outcome."""
        self.decision_count += 1

        d = str(decision).upper()
        if d == "ENTER_FULL":
            self.enter_full_count += 1
        elif d == "ENTER_REDUCED":
            self.enter_reduced_count += 1
        elif d in ("WAIT", "WATCH_ONLY", "WAIT_FOR_TRIGGER"):
            self.wait_count += 1
        else:
            self.reject_count += 1

        if reject_reason:
            self.reject_reasons[str(reject_reason)] += 1
        if veto_code:
            self.veto_codes[str(veto_code)] += 1
        if setup_type:
            self.setup_type_counts[str(setup_type)] += 1
        if setup_grade:
            self.grade_counts[str(setup_grade)] += 1

    def record_trade_open(self) -> None:
        self.trade_count += 1

    def record_trade_close(self, pnl_r: float, cost_drag_r: float = 0.0,
                           close_reason: str = "") -> None:
        """Record a trade closing event."""
        self.total_pnl_r += pnl_r
        self.total_cost_drag_r += cost_drag_r
        if pnl_r > 0:
            self.winner_count += 1
        elif pnl_r < 0:
            self.loser_count += 1

        reason = str(close_reason).upper()
        if "TP2" in reason or "FULL_TP" in reason:
            self.tp2_count += 1
        elif "TP1" in reason:
            self.tp1_count += 1
        elif "PROTECTED" in reason:
            self.protected_sl_count += 1
        elif "BREAKEVEN" in reason or "BE_" in reason:
            self.breakeven_count += 1
        elif "SL" in reason:
            self.sl_count += 1

    def record_candidate(self) -> None:
        self.candidate_count += 1

    def record_window(self) -> None:
        self.window_count += 1

    def record_gate_rejection(self, gate: str) -> None:
        self.gate_rejections[gate] += 1

    def record_poi_reaction_skip(self) -> None:
        self.poi_reaction_skipped += 1

    def flag_synthetic_trade(self) -> None:
        """Flag that a synthetic/fallback trade was detected."""
        self._synthetic_trades_detected += 1

    # ── derived metrics ───────────────────────────────────────────────

    @property
    def has_trades(self) -> bool:
        return self.trade_count > 0

    @property
    def winrate(self) -> float | None:
        """Win rate as fraction, or None if no trades."""
        if self.trade_count == 0:
            return None
        closed = self.winner_count + self.loser_count
        if closed == 0:
            return None
        return self.winner_count / closed

    @property
    def expectancy_r(self) -> float | None:
        """Expectancy per trade in R units, or None if no trades."""
        if self.trade_count == 0:
            return None
        return self.total_pnl_r / max(1, self.trade_count)

    @property
    def avg_cost_drag_r(self) -> float | None:
        """Average cost drag per trade in R, or None."""
        if self.trade_count == 0:
            return None
        return self.total_cost_drag_r / max(1, self.trade_count)

    # ── top-N helpers ─────────────────────────────────────────────────

    def top_reject_reasons(self, n: int = 10) -> list[dict[str, Any]]:
        return [
            {"reason": reason, "count": count}
            for reason, count in self.reject_reasons.most_common(n)
        ]

    def top_veto_codes(self, n: int = 10) -> list[dict[str, Any]]:
        return [
            {"code": code, "count": count}
            for code, count in self.veto_codes.most_common(n)
        ]

    def top_setup_types(self, n: int = 10) -> list[dict[str, Any]]:
        return [
            {"setup_type": st, "count": count}
            for st, count in self.setup_type_counts.most_common(n)
        ]

    # ── finalize → summary ────────────────────────────────────────────

    def finalize(self) -> dict[str, Any]:
        """Produce a compact summary dict — honest, no synthetic trades."""
        summary: dict[str, Any] = {
            "candle_count": self.candle_count,
            "eval_candle_count": self.eval_candle_count,
            "warmup_candle_count": self.warmup_candle_count,
            "decision_count": self.decision_count,
            "candidate_count": self.candidate_count,
            "window_count": self.window_count,
            "trade_count": self.trade_count,
        }

        # Decision breakdown
        summary["decisions"] = {
            "enter_full": self.enter_full_count,
            "enter_reduced": self.enter_reduced_count,
            "wait": self.wait_count,
            "reject": self.reject_count,
        }

        # Trade outcomes
        if self.has_trades:
            summary["trade_outcomes"] = {
                "tp1": self.tp1_count,
                "tp2": self.tp2_count,
                "sl": self.sl_count,
                "protected_sl": self.protected_sl_count,
                "breakeven": self.breakeven_count,
            }
            summary["winrate"] = round(self.winrate, 4) if self.winrate is not None else None
            summary["expectancy_R"] = round(self.expectancy_r, 4) if self.expectancy_r is not None else None
            summary["avg_cost_drag_R"] = round(self.avg_cost_drag_r, 6) if self.avg_cost_drag_r is not None else None
            summary["total_pnl_R"] = round(self.total_pnl_r, 4)
            summary["total_cost_drag_R"] = round(self.total_cost_drag_r, 6)
        else:
            # NO_TRADES state — winrate/expectancy = None, not 0%
            summary["state"] = "NO_TRADES"
            summary["winrate"] = None
            summary["expectancy_R"] = None
            summary["avg_cost_drag_R"] = None
            summary["total_pnl_R"] = 0.0
            summary["total_cost_drag_R"] = 0.0
            summary["no_trade_diagnostic"] = self.no_trade_diagnostic()

        # Rejection diagnostics
        summary["top_reject_reasons"] = self.top_reject_reasons(10)
        summary["top_veto_codes"] = self.top_veto_codes(10)
        summary["top_setup_types"] = self.top_setup_types(10)

        # Gate diagnostics
        if self.gate_rejections:
            summary["gate_rejections"] = dict(self.gate_rejections.most_common())

        # Grade distribution
        if self.grade_counts:
            summary["grade_distribution"] = dict(self.grade_counts)

        # POI_REACTION skipped
        summary["poi_reaction_skipped"] = self.poi_reaction_skipped

        # Synthetic trade guard
        if self._synthetic_trades_detected > 0:
            summary["WARNING"] = f"{self._synthetic_trades_detected} synthetic trades detected"
            summary["synthetic_trades"] = self._synthetic_trades_detected

        return summary

    def no_trade_diagnostic(self) -> dict[str, Any]:
        """Detailed diagnostic for the NO_TRADES state."""
        return {
            "state": "NO_TRADES",
            "winrate": None,
            "expectancy_R": None,
            "candidates": self.candidate_count,
            "windows_evaluated": self.window_count,
            "decisions_total": self.decision_count,
            "top_reject_reasons": self.top_reject_reasons(10),
            "top_veto_codes": self.top_veto_codes(10),
            "setup_type_counts": dict(self.setup_type_counts.most_common()),
            "gate_rejections": dict(self.gate_rejections.most_common()),
            "poi_reaction_skipped": self.poi_reaction_skipped,
            "grade_distribution": dict(self.grade_counts),
        }

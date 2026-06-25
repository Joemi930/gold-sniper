"""Offline simulated trade manager for replay mode.

P2-E Phase18: extended with shadow live-like policy, daily trade limiter,
equity-based position sizing, and open-trade-end tracking.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from replay.execution_model import ReplayExecutionModel, build_default_execution_model
from replay.fill_model import apply_entry_costs, apply_exit_costs, resolve_intrabar_exit_priority
from replay.offline_trade_simulator import SimulatedTradeStatus, evaluate_limit_entry_fill
from replay.shadow_live_policy import (
    DailyTradeCounter,
    LEG_RISK_SPLIT,
    LEG_TARGET_RR,
    PROTECTED_RUNNER_SL_R,
    ShadowLivePolicy,
    can_open_shadow_trade,
    compute_shadow_position_size,
    grade_is_executable,
    leg_risk_pct,
    record_trade_opened,
    risk_pct_for_grade,
)

TP3_RR = 3.0


@dataclass
class SimulatedTradeConfig:
    execution_model: ReplayExecutionModel | None = None
    spread_points: float | None = None
    slippage_points: float | None = None
    equity_initial: float = 100.0
    fixed_volume: float = 1.0
    write_blackboard_positions: bool = True
    event_prefix: str = ""
    require_execution_model: bool = True
    shadow_live_policy: ShadowLivePolicy | None = None
    enable_daily_limits: bool = True
    enable_live_sizing: bool = True

    @property
    def policy(self) -> ShadowLivePolicy:
        return self.shadow_live_policy or ShadowLivePolicy(initial_equity=self.equity_initial)

    def resolved_execution_model(self) -> ReplayExecutionModel:
        return self.execution_model or build_default_execution_model(initial_equity=self.equity_initial)


class SimulatedTradeManager:
    def __init__(self, blackboard, config: SimulatedTradeConfig | None = None):
        self.blackboard = blackboard
        self.config = config or SimulatedTradeConfig()
        self.execution_model = self.config.resolved_execution_model()
        errors = self.execution_model.validate()
        if self.config.require_execution_model and errors:
            raise ValueError(f"invalid_execution_model:{errors}")
        self.active_positions: dict[int, dict[str, Any]] = {}
        self.pending_entries: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.next_ticket = 1
        self.next_pending_id = 1
        self.equity = float(self.config.equity_initial)
        self.peak_equity = self.equity
        self.max_drawdown = 0.0
        self.signal_count = 0
        self.rejected_count = 0
        # Phase18: live-like policy, daily counters, open-end tracking
        self.policy = self.config.policy
        self.daily_counters: dict[str, DailyTradeCounter] = {}
        self.last_seen_candle: dict[str, Any] | None = None
        self.daily_limit_rejections: int = 0
        self.grade_blocked_count: int = 0
        # P1 Kasper: anti-duplicate scenario gate
        self.duplicate_rejections: int = 0
        # P1.1 Kasper: legacy ENTER blocked by Kasper authority gate
        self.legacy_enter_blocked_count: int = 0

    async def on_candle(self, candle: dict[str, Any]) -> list[dict[str, Any]]:
        self.last_seen_candle = dict(candle)
        events = []
        events.extend(await self._manage_triggered_positions(candle))
        events.extend(await self._manage_pending_entries(candle))
        opened_or_rejected = await self._consume_signal(candle)
        if opened_or_rejected:
            events.append(opened_or_rejected)
        return events

    async def on_candle_with_signal(self, candle: dict[str, Any], signal: dict[str, Any] | None) -> list[dict[str, Any]]:
        self.last_seen_candle = dict(candle)
        events = []
        events.extend(await self._manage_triggered_positions(candle))
        events.extend(await self._manage_pending_entries(candle))
        opened_or_rejected = await self._consume_signal(candle, signal=signal, clear_blackboard=False)
        if opened_or_rejected:
            events.append(opened_or_rejected)
        return events

    def summary(self) -> dict[str, Any]:
        close_name = self._event_name("close")
        leg_close_name = self._event_name("leg_close")
        open_name = self._event_name("open")
        rejected_name = self._event_name("rejected")
        missed_name = self._event_name("missed_entry")

        # P3: win/loss computed from PARENT closes (not leg events)
        parent_closes = [e for e in self.events if e["event"] == close_name]
        leg_closes = [e for e in self.events if e["event"] == leg_close_name]
        wins = sum(1 for e in parent_closes if e.get("parent_outcome") == "WIN")
        losses = sum(1 for e in parent_closes if e.get("parent_outcome") == "LOSS")
        pnl = round(self.equity - self.config.equity_initial, 6)
        validation_errors = self.execution_model.validate()

        # P3: payoff metrics from parent closes
        parent_pnl_Rs = [
            e.get("parent_pnl_R") for e in parent_closes
            if e.get("parent_pnl_R") is not None
        ]
        avg_win_R = round(sum(r for r in parent_pnl_Rs if (r or 0) > 0) / max(1, sum(1 for r in parent_pnl_Rs if (r or 0) > 0)), 6) if parent_pnl_Rs else 0.0
        avg_loss_R = round(sum(r for r in parent_pnl_Rs if (r or 0) <= 0) / max(1, sum(1 for r in parent_pnl_Rs if (r or 0) <= 0)), 6) if parent_pnl_Rs else 0.0
        payoff_ratio = round(abs(avg_win_R / avg_loss_R), 6) if avg_loss_R != 0 else 0.0
        gross_profit_R = round(sum(r for r in parent_pnl_Rs if (r or 0) > 0), 6)
        gross_loss_R = round(abs(sum(r for r in parent_pnl_Rs if (r or 0) <= 0)), 6)
        expectancy_R = round(sum(parent_pnl_Rs) / len(parent_pnl_Rs), 6) if parent_pnl_Rs else 0.0

        # P3 Case E: pure R metrics (before exit costs, from requested prices)
        pure_parent_Rs = [
            e.get("pure_parent_pnl_R") for e in parent_closes
            if e.get("pure_parent_pnl_R") is not None
        ]
        pure_avg_win_R = round(sum(r for r in pure_parent_Rs if (r or 0) > 0) / max(1, sum(1 for r in pure_parent_Rs if (r or 0) > 0)), 6) if pure_parent_Rs else 0.0
        pure_avg_loss_R = round(sum(r for r in pure_parent_Rs if (r or 0) <= 0) / max(1, sum(1 for r in pure_parent_Rs if (r or 0) <= 0)), 6) if pure_parent_Rs else 0.0
        pure_expectancy_R = round(sum(pure_parent_Rs) / len(pure_parent_Rs), 6) if pure_parent_Rs else 0.0

        # P3: leg-level exit counts
        tp1_hits = len([e for e in leg_closes if e.get("reason") == "TP1"])
        tp2_hits = len([e for e in leg_closes if e.get("reason") == "TP2"])
        sl_hits = len([e for e in leg_closes if e.get("reason") == "SL"])
        protected_sl_hits = len([e for e in leg_closes if e.get("reason") == "PROTECTED_SL"])
        # TP1 then protected SL: parent has leg_1=TP1, leg_2=PROTECTED_SL
        tp1_then_protected_sl = len([
            e for e in parent_closes
            if e.get("leg_1_exit_reason") == "TP1" and e.get("leg_2_exit_reason") == "PROTECTED_SL"
        ])
        tp1_then_tp2 = len([
            e for e in parent_closes
            if e.get("leg_1_exit_reason") == "TP1" and e.get("leg_2_exit_reason") == "TP2"
        ])
        full_sl_count = len([
            e for e in parent_closes
            if e.get("leg_1_exit_reason") == "SL" and e.get("leg_2_exit_reason") == "SL"
        ])
        # leg_1 pnl_R and leg_2 pnl_R totals
        leg1_pnl_R_total = round(sum(
            (e.get("leg_1_pnl_R") or 0.0) for e in parent_closes
        ), 6)
        leg2_pnl_R_total = round(sum(
            (e.get("leg_2_pnl_R") or 0.0) for e in parent_closes
        ), 6)

        # Open trades at replay end
        open_end_details = [
            self._open_trade_snapshot(trade, self.last_seen_candle)
            for trade in self.active_positions.values()
        ]
        unrealized_r_total = round(
            sum(item.get("unrealized_R") or 0.0 for item in open_end_details), 6
        )
        unrealized_pnl_total = round(
            sum(item.get("unrealized_pnl") or 0.0 for item in open_end_details), 6
        )

        daily_trade_counts = {
            day: counter.total for day, counter in self.daily_counters.items()
        }
        total_daily_trades = sum(daily_trade_counts.values())

        return {
            "initial_equity": round(self.config.equity_initial, 6),
            "signals": self.signal_count,
            "trades": len([e for e in self.events if e["event"] == open_name]),
            "parent_trades": len(parent_closes),
            "closed_trades": len(parent_closes),
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / len(parent_closes)) * 100, 2) if parent_closes else 0.0,
            "pnl": pnl,
            "net_pnl": pnl,
            "net_pnl_pct": round((pnl / self.config.equity_initial) * 100, 6) if self.config.equity_initial else 0.0,
            "equity_final": round(self.equity, 6),
            "final_equity": round(self.equity, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "max_drawdown_pct": round((self.max_drawdown / self.config.equity_initial) * 100, 6)
            if self.config.equity_initial
            else 0.0,
            "rejections": self.rejected_count,
            "missed_entries": len([e for e in self.events if e["event"] == missed_name]),
            # P3: payoff metrics
            "avg_win_R": avg_win_R,
            "avg_loss_R": avg_loss_R,
            "payoff_ratio": payoff_ratio,
            "expectancy_R": expectancy_R,
            # P3 Case E: pure R (before exit costs, from requested prices)
            "pure_avg_win_R": pure_avg_win_R,
            "pure_avg_loss_R": pure_avg_loss_R,
            "pure_expectancy_R": pure_expectancy_R,
            "gross_profit_R": gross_profit_R,
            "gross_loss_R": gross_loss_R,
            # P3: leg-level counts
            "tp1_hit_count": tp1_hits,
            "tp2_hit_count": tp2_hits,
            "tp1_then_protected_sl_count": tp1_then_protected_sl,
            "tp1_then_tp2_count": tp1_then_tp2,
            "full_sl_count": full_sl_count,
            "sl_hit_count": sl_hits,
            "protected_sl_hit_count": protected_sl_hits,
            "leg1_pnl_R_total": leg1_pnl_R_total,
            "leg2_pnl_R_total": leg2_pnl_R_total,
            # Legacy compat
            "tp1_hits": tp1_hits,
            "tp2_hits": tp2_hits,
            "tp3_hits": 0,
            "sl_hits": sl_hits,
            "protected_sl_hits": protected_sl_hits,
            "partial_closes": tp1_hits,
            "be_plus_moves": tp1_hits,
            "avg_r": self._avg_r(parent_closes),
            "execution_model": self.execution_model.to_dict(),
            "execution_model_valid": not validation_errors,
            "total_commission": round(sum(float(e.get("commission", 0.0) or 0.0) for e in self.events), 6),
            "total_spread_points_charged": round(sum(float(e.get("spread_points", 0.0) or 0.0) for e in self.events), 6),
            "total_slippage_points_charged": round(sum(float(e.get("slippage_points", 0.0) or 0.0) for e in self.events), 6),
            "fill_model": self.execution_model.fill_model,
            "p2c_faithful_simulation": True,
            "event_counts": {
                "open": len([e for e in self.events if e["event"] == open_name]),
                "close": len(parent_closes),
                "leg_close": len(leg_closes),
                "rejected": len([e for e in self.events if e["event"] == rejected_name]),
                "missed_entry": len([e for e in self.events if e["event"] == missed_name]),
            },
            "open_trades_end_count": len(open_end_details),
            "open_trades_end_details": open_end_details,
            "unrealized_R_total": unrealized_r_total,
            "unrealized_pnl": unrealized_pnl_total,
            "pending_entries_end_count": len(self.pending_entries),
            "last_seen_candle_time": (
                _iso(self.last_seen_candle.get("time"))
                if self.last_seen_candle
                else None
            ),
            "daily_trade_counts": daily_trade_counts,
            "total_daily_trades": total_daily_trades,
            "daily_limit_rejections": self.daily_limit_rejections,
            "grade_blocked_count": self.grade_blocked_count,
            "legacy_enter_blocked_count": self.legacy_enter_blocked_count,
            "duplicate_rejections": self.duplicate_rejections,
            "shadow_live_policy": self.policy.to_dict(),
            **_risk_realism_summary(self.events, parent_closes),
        }

    def _open_trade_snapshot(
        self, trade: dict[str, Any], candle: dict[str, Any] | None
    ) -> dict[str, Any]:
        """P3: snapshot open trade at replay end with two-leg unrealized PnL."""
        if not candle:
            return {
                "ticket": trade.get("ticket"),
                "status": "OPEN_NO_LAST_CANDLE",
                "opened_at": trade.get("opened_at"),
                "type": trade.get("type"),
                "entry_price": trade.get("entry_price"),
            }

        close = float(candle.get("close") or trade.get("entry_price") or 0.0)
        entry = float(trade.get("entry_price") or 0.0)
        action = str(trade.get("type") or "").upper()

        # P3: compute unrealized PnL per leg that is still open
        leg_1 = trade.get("leg_1", {})
        leg_2 = trade.get("leg_2", {})
        leg_1_unrealized = 0.0
        leg_2_unrealized = 0.0

        if isinstance(leg_1, dict) and leg_1.get("status") == "OPEN":
            vol1 = float(leg_1.get("volume", 0))
            leg_1_unrealized = (
                (close - entry) * vol1 if action == "BUY" else (entry - close) * vol1
            )
        if isinstance(leg_2, dict) and leg_2.get("status") == "OPEN":
            vol2 = float(leg_2.get("volume", 0))
            leg_2_unrealized = (
                (close - entry) * vol2 if action == "BUY" else (entry - close) * vol2
            )

        unrealized_pnl = round(leg_1_unrealized + leg_2_unrealized, 6)
        total_risk = float(trade.get("total_risk_cash") or trade.get("risk_cash") or 0.0)
        unrealized_r = round(unrealized_pnl / total_risk, 6) if total_risk > 0 else None

        return {
            "ticket": trade.get("ticket"),
            "type": action,
            "opened_at": trade.get("opened_at"),
            "entry_price": entry,
            "current_sl": trade.get("current_sl"),
            "protected_sl": trade.get("protected_sl"),
            "tp1": trade.get("tp1"),
            "tp2": trade.get("tp2"),
            "volume_original": trade.get("volume_original"),
            "volume_remaining": trade.get("volume_remaining"),
            "risk_cash": total_risk,
            "risk_pct": trade.get("risk_pct"),
            "risk_points": trade.get("risk_points"),
            "setup_type": trade.get("setup_type"),
            "setup_grade": trade.get("setup_grade"),
            "sizing_source": trade.get("sizing_source"),
            "capital_at_entry": trade.get("capital_at_entry"),
            "last_seen_candle_time": candle.get("time"),
            "last_seen_close": close,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_R": unrealized_r,
            "partial_closed": leg_1.get("status") == "CLOSED" if isinstance(leg_1, dict) else trade.get("partial_closed", False),
            "breakeven_activated": trade.get("breakeven_activated", False),
            "lifecycle_status": "OPEN_AT_REPLAY_END",
            # P3 leg details
            "leg_1_status": leg_1.get("status") if isinstance(leg_1, dict) else "UNKNOWN",
            "leg_2_status": leg_2.get("status") if isinstance(leg_2, dict) else "UNKNOWN",
            "leg_1_unrealized_pnl": round(leg_1_unrealized, 6),
            "leg_2_unrealized_pnl": round(leg_2_unrealized, 6),
        }

    async def _consume_signal(
        self,
        candle: dict[str, Any],
        *,
        signal: dict[str, Any] | None = None,
        clear_blackboard: bool = True,
    ) -> dict[str, Any] | None:
        if signal is None:
            signal = self.blackboard.read_sync("trade_signals")
        if not isinstance(signal, dict) or not signal.get("signal"):
            return None

        self.signal_count += 1
        if _is_limit_signal(signal) and not _limit_signal_fills(signal, candle, self.execution_model):
            if clear_blackboard:
                await self.blackboard.write("trade_signals", {})
            expiry = max(1, int(signal.get("entry_expiry_bars") or 3))
            if expiry <= 1:
                return self._record_event(self._missed_entry_event(signal, candle, candle["time"], bars_checked=1))
            pending_id = self.next_pending_id
            self.next_pending_id += 1
            self.pending_entries[pending_id] = {
                "pending_id": pending_id,
                "signal": dict(signal),
                "created_at": _iso(candle["time"]),
                "first_candle_time": _iso(candle["time"]),
                "bars_checked": 1,
                "expiry_bars": expiry,
            }
            return None

        try:
            trade = self._build_trade(signal, candle)
        except ValueError as exc:
            self.rejected_count += 1
            if clear_blackboard:
                await self.blackboard.write("trade_signals", {})
            return self._record_event(
                {
                    "event": self._event_name("rejected"),
                    "reason": str(exc),
                    "time": _iso(candle["time"]),
                    "signal": dict(signal),
                }
            )

        self.active_positions[trade["ticket"]] = trade
        await self._write_active_positions()
        if clear_blackboard:
            await self.blackboard.write("trade_signals", {})
        return self._record_event({"event": self._event_name("open"), "time": _iso(candle["time"]), **trade})

    async def _manage_pending_entries(self, candle: dict[str, Any]) -> list[dict[str, Any]]:
        events = []
        for pending_id, pending in list(self.pending_entries.items()):
            signal = dict(pending.get("signal") or {})
            bars_checked = int(pending.get("bars_checked") or 0) + 1
            pending["bars_checked"] = bars_checked
            if _limit_signal_fills(signal, candle, self.execution_model):
                try:
                    trade = self._build_trade(signal, candle)
                except ValueError as exc:
                    self.pending_entries.pop(pending_id, None)
                    self.rejected_count += 1
                    events.append(self._record_event({
                        "event": self._event_name("rejected"),
                        "reason": str(exc),
                        "time": _iso(candle["time"]),
                        "signal": signal,
                    }))
                    continue
                self.pending_entries.pop(pending_id, None)
                self.active_positions[trade["ticket"]] = trade
                events.append(self._record_event({"event": self._event_name("open"), "time": _iso(candle["time"]), **trade}))
                continue
            if bars_checked >= int(pending.get("expiry_bars") or 3):
                self.pending_entries.pop(pending_id, None)
                events.append(self._record_event(self._missed_entry_event(signal, candle, pending.get("first_candle_time"), bars_checked=bars_checked)))
        if events:
            await self._write_active_positions()
        return events

    def _missed_entry_event(
        self,
        signal: dict[str, Any],
        candle: dict[str, Any],
        first_candle_time: Any,
        *,
        bars_checked: int,
    ) -> dict[str, Any]:
        return {
            "event": self._event_name("missed_entry"),
            "status": SimulatedTradeStatus.MISSED_ENTRY.value,
            "reason": "LIMIT_NOT_TOUCHED_WITH_SPREAD",
            "time": _iso(candle["time"]),
            "side": str(signal.get("signal") or signal.get("side") or "").upper(),
            "limit_price": float(signal.get("entry_price") or signal.get("limit_price") or 0.0),
            "spread": _limit_signal_spread(signal, self.execution_model),
            "expiry_bars": int(signal.get("entry_expiry_bars") or 3),
            "bars_checked": bars_checked,
            "first_candle_time": _iso(first_candle_time),
            "last_checked_candle_time": _iso(candle["time"]),
            "filled": False,
            "pnl": 0.0,
            "exposure": 0.0,
            "signal": dict(signal),
        }

    def _check_duplicate_scenario(self, signal: dict[str, Any]) -> str | None:
        """P2.2: anti-duplicate scenario gate — scenario_key is primary.

        Returns the veto reason string if the signal duplicates an active trade,
        or None if the signal passes all duplicate checks.

        Rules (in order):
          1. Same scenario_key as any active trade → DUPLICATE_SCENARIO_KEY_ACTIVE
          2. Same side + same POI bounds (type + low/high) → SAME_SIDE_SAME_POI_ACTIVE
          3. Same side + same swept_level + sweep_time → SAME_SIDE_SAME_SWEEP_LEVEL_ACTIVE

        P2.2: Rule 3 (liquidity_event_type only) is REMOVED — it was too generic
        and blocked different setups that shared the same generic sweep type.
        Rule 4 (setup_family) is REMOVED — same family doesn't mean same trade.
        """
        if not self.active_positions:
            return None

        side = str(signal.get("signal", "")).upper()
        scenario_key = signal.get("scenario_key")
        poi_type = signal.get("poi_type")
        poi_low = signal.get("poi_low")
        poi_high = signal.get("poi_high")
        swept_level = signal.get("swept_level")
        sweep_time = signal.get("sweep_time")

        for trade in self.active_positions.values():
            trade_side = str(trade.get("type", "")).upper()

            # Rule 1: same scenario_key (primary, precise)
            if scenario_key and trade.get("scenario_key") == scenario_key:
                return "DUPLICATE_SCENARIO_KEY_ACTIVE"

            # Side must match for rules 2-3
            if not side or side != trade_side:
                continue

            # Rule 2: same side + same POI (type + price bounds within 0.5% tol)
            if poi_type and poi_type != "none" and trade.get("poi_type") == poi_type:
                t_low = trade.get("poi_low")
                t_high = trade.get("poi_high")
                if poi_low is not None and t_low is not None:
                    tolerance = abs(float(poi_low)) * 0.005 if float(poi_low) != 0 else 0.5
                    if abs(float(poi_low) - float(t_low)) < tolerance:
                        return "SAME_SIDE_SAME_POI_ACTIVE"
                if poi_high is not None and t_high is not None:
                    tolerance = abs(float(poi_high)) * 0.005 if float(poi_high) != 0 else 0.5
                    if abs(float(poi_high) - float(t_high)) < tolerance:
                        return "SAME_SIDE_SAME_POI_ACTIVE"

            # Rule 3: same side + same swept_level + sweep_time
            if swept_level is not None and trade.get("swept_level") is not None:
                t_swept = float(trade.get("swept_level"))
                tolerance = abs(float(swept_level)) * 0.005 if float(swept_level) != 0 else 0.5
                if abs(float(swept_level) - t_swept) < tolerance:
                    if sweep_time and trade.get("sweep_time") == sweep_time:
                        return "SAME_SIDE_SAME_SWEEP_LEVEL_ACTIVE"

        return None

    def _build_trade(self, signal: dict[str, Any], candle: dict[str, Any]) -> dict[str, Any]:
        """P3: Build a parent trade with two explicit child legs.

        leg_1 → TP1 (1R on 50% risk),  leg_2 → TP2 (2R on 50% risk).
        After leg_1 TP1, leg_2 SL moves to entry + 0.5R (protected).
        Daily limiter and duplicate gate count the parent, not the legs.
        """
        action = str(signal.get("signal", "")).upper()
        if action not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported_signal:{action}")

        requested_entry = float(signal.get("entry_price") or candle["close"])
        news_near = _is_news_near(signal, candle)
        sl = float(signal.get("stop_loss") or signal.get("sl") or 0.0)
        if sl <= 0:
            raise ValueError("invalid_sl_tp")

        sizing_source = signal.get("sizing_source", "CONFIG_FIXED")
        grade = str(signal.get("setup_grade") or signal.get("tier") or "UNKNOWN")
        risk_pct_value = signal.get("risk_pct")
        risk_cash_value = signal.get("risk_cash")
        structural_risk_points = abs(requested_entry - sl)
        effective_risk_points = structural_risk_points
        risk_realism_status = "UNKNOWN"
        min_lot_adjustment = False

        if self.config.enable_live_sizing and signal.get("source") == "P1_SHADOW_DECISION":
            try:
                spread = self.execution_model.spread_points(news_blocked_or_near=news_near)
                slippage = self.execution_model.slippage_for_event(news_blocked_or_near=news_near)
                sizing = compute_shadow_position_size(
                    equity=self.equity,
                    grade=grade,
                    entry=float(requested_entry),
                    stop_loss=sl,
                    policy=self.policy,
                    side=action,
                    spread_points=spread,
                    slippage_points=slippage,
                )
                volume = sizing["volume"]
                risk_pct_value = sizing["risk_pct"]
                risk_cash_value = sizing["risk_cash"]
                structural_risk_points = sizing["risk_points"]
                effective_risk_points = sizing["effective_risk_points"]
                sizing_source = "PHASE19_COST_AWARE_RISK_MODEL"
                expected_sl_loss = sizing["expected_sl_loss"]
                risk_realism_ratio = sizing["risk_realism_ratio"]
                risk_realism_status = (
                    "PASS" if abs(risk_realism_ratio - 1.0) <= 0.25 else "WARN"
                )
            except ValueError:
                raise
        else:
            volume = float(signal.get("volume") or signal.get("lot") or self.config.fixed_volume)

        # ── P3: split total volume/risk into two equal legs ────────────
        leg_vol = round(volume * LEG_RISK_SPLIT, 6)
        leg_risk_cash = round(float(risk_cash_value or 0) * LEG_RISK_SPLIT, 6)
        leg_1_risk_pct, leg_2_risk_pct = leg_risk_pct(grade)

        # Entry fill for combined volume (costs apply once to parent)
        fill = apply_entry_costs(
            side=action,
            requested_entry=requested_entry,
            execution_model=self.execution_model,
            news_blocked_or_near=news_near,
            volume=volume,
        )
        if not fill.filled or fill.fill_price is None:
            raise ValueError(f"entry_not_filled:{fill.reason}")
        entry = float(fill.fill_price)

        # Risk from actual filled entry → SL (structural)
        risk = entry - sl if action == "BUY" else sl - entry
        if risk <= 0:
            raise ValueError("invalid_risk_points")

        # ── P3: leg TP levels ─────────────────────────────────────────
        # leg_1 TP = 1R from entry, leg_2 TP = 2R from entry
        if action == "BUY":
            leg_1_tp = entry + risk * LEG_TARGET_RR[1]
            leg_2_tp = entry + risk * LEG_TARGET_RR[2]
            protected_sl_price = round(entry + PROTECTED_RUNNER_SL_R * risk, 6)
        else:
            leg_1_tp = entry - risk * LEG_TARGET_RR[1]
            leg_2_tp = entry - risk * LEG_TARGET_RR[2]
            protected_sl_price = round(entry - PROTECTED_RUNNER_SL_R * risk, 6)

        # Validate levels
        if action == "BUY" and not (sl < entry < leg_1_tp <= leg_2_tp):
            raise ValueError("invalid_buy_levels")
        if action == "SELL" and not (leg_2_tp <= leg_1_tp < entry < sl):
            raise ValueError("invalid_sell_levels")

        # Phase19: risk realism
        expected_sl_loss = round(float(volume) * float(risk), 6)
        risk_realism_ratio = round(expected_sl_loss / float(risk_cash_value), 6) if float(risk_cash_value or 0) > 0 else 1.0
        if risk_realism_status == "UNKNOWN" and float(risk_cash_value or 0) > 0:
            risk_realism_status = (
                "PASS" if abs(risk_realism_ratio - 1.0) <= 0.25 else "WARN"
            )

        ticket = self.next_ticket
        self.next_ticket += 1
        return {
            # ── Parent-level fields ──────────────────────────────────
            "ticket": ticket,
            "type": action,
            "requested_entry_price": requested_entry,
            "entry_price": entry,
            "original_sl": sl,
            "current_sl": sl,
            "risk_points": risk,
            "risk_cash": risk_cash_value,
            "risk_pct": risk_pct_value,
            "total_risk_pct": risk_pct_value,
            "total_risk_cash": risk_cash_value,
            "volume_original": volume,
            "volume_remaining": volume,
            "entry_spread_points": fill.spread_points,
            "entry_slippage_points": fill.slippage_points,
            "entry_commission": fill.commission,
            "fill_model": fill.model,
            "execution_conservative": fill.conservative,
            "news_execution_mode": "NEWS_COSTS" if news_near else "NORMAL_COSTS",
            "commission_total": fill.commission,
            "tier": signal.get("tier"),
            "score": signal.get("score"),
            "direction_source": signal.get("direction_source"),
            "entry_source": signal.get("entry_source"),
            "sl_source": signal.get("sl_source"),
            "opened_at": _iso(candle["time"]),
            "source": signal.get("source", "REPLAY"),
            "sizing_source": sizing_source,
            "capital_at_entry": round(self.equity, 6),
            "setup_type": signal.get("setup_type"),
            "setup_grade": grade,
            "daily_slot_reason": signal.get("daily_slot_reason"),
            "structural_risk_points": round(structural_risk_points, 6),
            "effective_risk_points": round(float(risk), 6),
            "cost_aware_volume": round(float(volume), 6),
            "risk_realism_expected_loss": expected_sl_loss,
            "risk_realism_status": risk_realism_status,
            "min_lot_adjustment": min_lot_adjustment,
            # P2.2 identity fields
            "scenario_id": signal.get("scenario_id"),
            "scenario_key": signal.get("scenario_key"),
            "decision_id": signal.get("decision_id"),
            "scenario_type": signal.get("scenario_type"),
            "market_story": signal.get("market_story"),
            "sequence_pass_fail": signal.get("sequence_pass_fail"),
            "identity_components": signal.get("identity_components"),
            "kasper_side": signal.get("kasper_side"),
            "pde_side": signal.get("pde_side"),
            "signal_side": signal.get("signal_side"),
            "trade_side": action,
            "side_alignment_status": signal.get("side_alignment_status"),
            "kasper_grade": signal.get("kasper_grade"),
            "kasper_score": signal.get("kasper_score"),
            "trade_open_source": signal.get("trade_open_source", "KASPER_SCENARIO_ENGINE"),
            "poi_type": signal.get("poi_type"),
            "poi_low": signal.get("poi_low"),
            "poi_high": signal.get("poi_high"),
            "liquidity_event_type": signal.get("liquidity_event_type"),
            "swept_level": signal.get("swept_level"),
            "sweep_time": signal.get("sweep_time"),
            "setup_family": signal.get("setup_family"),
            "rr_estimate": signal.get("rr_estimate"),
            "target_liquidity": signal.get("target_liquidity"),
            "requested_risk_pct": signal.get("requested_risk_pct"),
            "effective_risk_pct": signal.get("effective_risk_pct"),
            "risk_grade_source": signal.get("risk_grade_source", "KASPER_GRADE"),
            "risk_cap_applied": signal.get("risk_cap_applied", False),
            "risk_cap_reason": signal.get("risk_cap_reason"),
            "duplicate_gate_basis": "SCENARIO_KEY",
            # ── P3: two-leg structure ────────────────────────────────
            "parent_pnl_R": None,
            "parent_outcome": None,
            "leg_1": {
                "leg": 1,
                "volume": leg_vol,
                "risk_pct": leg_1_risk_pct,
                "risk_cash": leg_risk_cash,
                "sl": sl,
                "tp": leg_1_tp,
                "target_rr": LEG_TARGET_RR[1],
                "status": "OPEN",
                "exit_reason": None,
                "exit_price": None,
                "pnl": 0.0,
                "pnl_R": 0.0,
                "commission": 0.0,
            },
            "leg_2": {
                "leg": 2,
                "volume": leg_vol,
                "risk_pct": leg_2_risk_pct,
                "risk_cash": leg_risk_cash,
                "sl": sl,
                "tp": leg_2_tp,
                "target_rr": LEG_TARGET_RR[2],
                "protected_sl_r": PROTECTED_RUNNER_SL_R,
                "protected_sl": None,
                "status": "OPEN",
                "exit_reason": None,
                "exit_price": None,
                "pnl": 0.0,
                "pnl_R": 0.0,
                "commission": 0.0,
            },
            # Legacy compat fields (derived from leg structure)
            "tp1": leg_1_tp,
            "tp2": leg_2_tp,
            "tp3": None,
            "tp": leg_2_tp,
            "partial_closed": False,
            "tp2_closed": False,
            "breakeven_activated": False,
            "be_plus_activated": False,
            "protected_sl": None,
            "tp1_close_percent": 50.0,
            "tp2_close_percent": 50.0,
        }

    async def _manage_triggered_positions(self, candle: dict[str, Any]) -> list[dict[str, Any]]:
        events = []
        for _, trade in list(self.active_positions.items()):
            trade_events = self._events_for_candle(trade, candle)
            if not trade_events:
                continue
            for event in trade_events:
                events.append(self._record_event(event))
        if events:
            await self._write_active_positions()
        return events

    def _events_for_candle(self, trade: dict[str, Any], candle: dict[str, Any]) -> list[dict[str, Any]]:
        """P3: Check each leg independently for TP/SL/protected-SL hits.

        Conservative intrabar policy: SL checked before TP for each leg.
        If leg_1 SL is hit in the same candle leg_2 also hits SL (same price).
        After leg_1 TP1, leg_2 SL moves to protected_sl (entry + 0.5R).
        """
        action = trade["type"]
        leg_1 = trade["leg_1"]
        leg_2 = trade["leg_2"]
        events: list[dict[str, Any]] = []

        # ── Phase 1: Check leg_1 (SL vs TP1) ─────────────────────────
        if leg_1["status"] == "OPEN":
            reason1, level1 = resolve_intrabar_exit_priority(
                side=action,
                candle=candle,
                sl=leg_1["sl"],
                tp1=leg_1["tp"],
                partial_closed=False,
            )
            if reason1 == "SL" and level1 is not None:
                events.append(self._close_leg(trade, candle, 1, "SL", level1))
                # leg_2 SL at same level → close both (conservative)
                if leg_2["status"] == "OPEN":
                    events.append(self._close_leg(trade, candle, 2, "SL", level1))
            elif reason1 == "TP1" and level1 is not None:
                events.append(self._close_leg(trade, candle, 1, "TP1", level1))
                # Activate protected SL on leg_2
                self._activate_protected_sl(trade)

        # ── Phase 2: Check leg_2 (SL/protected_SL vs TP2) ────────────
        if leg_2["status"] == "OPEN":
            protected = leg_2.get("protected_sl")
            if protected is not None:
                # After leg_1 TP1: protected SL vs TP2
                reason2, level2 = resolve_intrabar_exit_priority(
                    side=action,
                    candle=candle,
                    sl=leg_2["sl"],
                    tp2=leg_2["tp"],
                    protected_sl=protected,
                    partial_closed=True,
                )
                if reason2 == "PROTECTED_SL" and level2 is not None:
                    events.append(self._close_leg(trade, candle, 2, "PROTECTED_SL", level2))
                elif reason2 == "TP2" and level2 is not None:
                    events.append(self._close_leg(trade, candle, 2, "TP2", level2))
            else:
                # Before leg_1 TP1: SL vs TP2 (intrabar conservative)
                reason2, level2 = self._check_sl_vs_tp2(
                    action, candle, leg_2["sl"], leg_2["tp"]
                )
                if reason2 == "SL" and level2 is not None:
                    events.append(self._close_leg(trade, candle, 2, "SL", level2))
                    # leg_1 also at SL if still open (same candle)
                    if leg_1["status"] == "OPEN":
                        events.append(self._close_leg(trade, candle, 1, "SL", level2))
                elif reason2 == "TP2" and level2 is not None:
                    # TP2 hit before TP1? That means TP1 must have been passed
                    # in the same candle. Close leg_1 at TP1 first, then leg_2 at TP2.
                    if leg_1["status"] == "OPEN":
                        events.append(self._close_leg(trade, candle, 1, "TP1", leg_1["tp"]))
                        self._activate_protected_sl(trade)
                    events.append(self._close_leg(trade, candle, 2, "TP2", level2))

        # ── Phase 3: Parent finalization ──────────────────────────────
        if leg_1["status"] == "CLOSED" and leg_2["status"] == "CLOSED":
            parent_pnl = (leg_1.get("pnl", 0.0) or 0.0) + (leg_2.get("pnl", 0.0) or 0.0)
            total_risk = float(trade.get("total_risk_cash") or trade.get("risk_cash") or 1.0)
            trade["parent_pnl_R"] = round(parent_pnl / abs(total_risk), 6) if abs(total_risk) > 0 else 0.0
            # P3 Case E: pure parent R from requested exits (before costs)
            leg_1_pure = leg_1.get("pure_pnl_R", 0.0) or 0.0
            leg_2_pure = leg_2.get("pure_pnl_R", 0.0) or 0.0
            trade["pure_parent_pnl_R"] = round(LEG_RISK_SPLIT * (leg_1_pure + leg_2_pure), 6)
            trade["parent_outcome"] = "WIN" if trade["parent_pnl_R"] > 0 else "LOSS"
            self.active_positions.pop(trade["ticket"], None)
            events.append(self._parent_close_event(trade, candle))

        return events

    # ── P3 leg helpers ─────────────────────────────────────────────────────

    def _check_sl_vs_tp2(
        self, side: str, candle: dict[str, Any], sl: float, tp2: float
    ) -> tuple[str | None, float | None]:
        """Check SL vs TP2 for leg_2 before leg_1 has closed.

        Conservative: SL checked first.
        """
        side = str(side).upper()
        high = float(candle["high"])
        low = float(candle["low"])
        if side == "BUY":
            if low <= sl:
                return "SL", sl
            if high >= tp2:
                return "TP2", tp2
        else:
            if high >= sl:
                return "SL", sl
            if low <= tp2:
                return "TP2", tp2
        return None, None

    def _close_leg(
        self,
        trade: dict[str, Any],
        candle: dict[str, Any],
        leg_num: int,
        reason: str,
        exit_price: float,
    ) -> dict[str, Any]:
        """Close a single leg and apply PnL."""
        leg = trade[f"leg_{leg_num}"]
        volume = float(leg["volume"])
        fill = self._exit_fill(candle, trade, exit_price, volume, reason)
        actual_exit = float(fill.fill_price) if fill.filled and fill.fill_price is not None else float(exit_price)
        pnl = self._pnl(trade, actual_exit, volume) - fill.commission
        trade["commission_total"] = round(float(trade.get("commission_total", 0.0)) + fill.commission, 6)
        self._apply_pnl(pnl)
        leg["status"] = "CLOSED"
        leg["exit_reason"] = reason
        leg["exit_price"] = actual_exit
        leg["pnl"] = pnl
        leg["commission"] = fill.commission
        # R multiple based on leg's own risk (net, after costs)
        leg_risk = abs(float(leg.get("risk_cash", 0)))
        leg["pnl_R"] = round(float(pnl) / leg_risk, 6) if leg_risk > 0 else 0.0
        # P3 Case E: pure R from requested exit (before costs)
        entry = float(trade["entry_price"])
        pure_pnl = round((float(exit_price) - entry) * volume, 6) if trade["type"] == "BUY" else round((entry - float(exit_price)) * volume, 6)
        leg["pure_pnl_R"] = round(pure_pnl / leg_risk, 6) if leg_risk > 0 else 0.0
        return self._leg_event(candle, trade, leg_num, reason, exit_price, actual_exit, volume, pnl, fill)

    def _activate_protected_sl(self, trade: dict[str, Any]) -> None:
        """After leg_1 TP1, move leg_2 SL to entry + 0.5R (protected)."""
        leg_2 = trade["leg_2"]
        if leg_2["status"] != "OPEN":
            return
        action = trade["type"]
        entry = float(trade["entry_price"])
        risk = float(trade["risk_points"])
        if action == "BUY":
            psl = round(entry + PROTECTED_RUNNER_SL_R * risk, 6)
        else:
            psl = round(entry - PROTECTED_RUNNER_SL_R * risk, 6)
        leg_2["protected_sl"] = psl
        leg_2["sl"] = psl  # Move SL to protected level
        trade["protected_sl"] = psl  # Legacy compat
        trade["breakeven_activated"] = True
        trade["be_plus_activated"] = True

    def _finalize_parent(
        self, trade: dict[str, Any], candle: dict[str, Any]
    ) -> dict[str, Any]:
        """Compute parent outcome and remove from active positions."""
        leg_1 = trade["leg_1"]
        leg_2 = trade["leg_2"]
        parent_pnl = (leg_1.get("pnl", 0.0) or 0.0) + (leg_2.get("pnl", 0.0) or 0.0)
        total_risk = float(trade.get("total_risk_cash") or trade.get("risk_cash") or 1.0)
        trade["parent_pnl_R"] = round(parent_pnl / abs(total_risk), 6) if abs(total_risk) > 0 else 0.0
        # P3 Case E: pure parent R from requested exits (before costs)
        leg_1_pure = leg_1.get("pure_pnl_R", 0.0) or 0.0
        leg_2_pure = leg_2.get("pure_pnl_R", 0.0) or 0.0
        trade["pure_parent_pnl_R"] = round(LEG_RISK_SPLIT * (leg_1_pure + leg_2_pure), 6)
        trade["parent_outcome"] = "WIN" if trade["parent_pnl_R"] > 0 else "LOSS"
        self.active_positions.pop(trade["ticket"], None)
        return self._parent_close_event(trade, candle)

    def _parent_close_event(
        self, trade: dict[str, Any], candle: dict[str, Any]
    ) -> dict[str, Any]:
        """Emit parent-level close event when both legs are closed."""
        return {
            "event": self._event_name("close"),
            "time": _iso(candle["time"]),
            "ticket": trade["ticket"],
            "reason": "PARENT_CLOSE",
            "type": trade["type"],
            "entry_price": trade["entry_price"],
            "parent_pnl_R": trade.get("parent_pnl_R"),
            "pure_parent_pnl_R": trade.get("pure_parent_pnl_R"),  # P3: parent R before exit costs
            "parent_outcome": trade.get("parent_outcome"),
            "leg_1_exit_reason": trade["leg_1"].get("exit_reason"),
            "leg_1_pnl_R": trade["leg_1"].get("pnl_R"),
            "leg_2_exit_reason": trade["leg_2"].get("exit_reason"),
            "leg_2_pnl_R": trade["leg_2"].get("pnl_R"),
            "pnl": round(
                (trade["leg_1"].get("pnl", 0.0) or 0.0)
                + (trade["leg_2"].get("pnl", 0.0) or 0.0),
                6,
            ),
            "commission": trade.get("commission_total", 0.0),
            "equity": self.equity,
            "risk_cash": trade.get("total_risk_cash") or trade.get("risk_cash"),
            "r_multiple": trade.get("parent_pnl_R"),
            "tier": trade.get("tier"),
            "setup_grade": trade.get("setup_grade"),
            "kasper_grade": trade.get("kasper_grade"),
            "risk_pct": trade.get("risk_pct"),
            "score": trade.get("score"),
            "source": trade.get("source"),
            "fill_model": trade.get("fill_model"),
            "execution_conservative": trade.get("execution_conservative"),
            "news_execution_mode": trade.get("news_execution_mode"),
            "setup_grade": trade.get("setup_grade"),
            "scenario_key": trade.get("scenario_key"),
        }

    def _leg_event(
        self,
        candle: dict[str, Any],
        trade: dict[str, Any],
        leg_num: int,
        reason: str,
        requested_price: float,
        actual_price: float,
        volume: float,
        pnl: float,
        fill: Any,
    ) -> dict[str, Any]:
        """Emit a per-leg close event for detailed tracking."""
        leg = trade[f"leg_{leg_num}"]
        return {
            "event": self._event_name("leg_close"),
            "time": _iso(candle["time"]),
            "ticket": trade["ticket"],
            "leg": leg_num,
            "reason": reason,
            "requested_price": requested_price,
            "exit_price": actual_price,
            "fill_price": actual_price,
            "volume": volume,
            "pnl": pnl,
            "pnl_R": leg.get("pnl_R"),
            "pure_pnl_R": leg.get("pure_pnl_R"),  # P3: R before exit costs
            "commission": fill.commission,
            "spread_points": fill.spread_points,
            "slippage_points": fill.slippage_points,
            "fill_model": fill.model,
            "conservative": fill.conservative,
            "equity": self.equity,
            "risk_cash": leg.get("risk_cash"),
            "r_multiple": leg.get("pnl_R"),
            "tier": trade.get("tier"),
            "risk_pct": leg.get("risk_pct"),
            "score": trade.get("score"),
            "source": trade.get("source"),
            "entry_price": trade.get("entry_price"),
            "commission_total": trade.get("commission_total"),
            "fill_model": trade.get("fill_model"),
            "execution_conservative": trade.get("execution_conservative"),
            "news_execution_mode": trade.get("news_execution_mode"),
            "setup_grade": trade.get("setup_grade"),
            "scenario_key": trade.get("scenario_key"),
        }

    def _exit_for_candle(self, trade: dict[str, Any], candle: dict[str, Any]) -> tuple[str | None, float]:
        """P3: find the first exit event for this candle (used by external callers)."""
        events = self._events_for_candle(trade, candle)
        close = next(
            (e for e in events if e["event"] in {self._event_name("close"), self._event_name("leg_close")}),
            None,
        )
        if close:
            return close.get("reason"), close.get("exit_price", 0.0)
        return None, 0.0

    def _apply_pnl(self, pnl: float) -> None:
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.max_drawdown = max(self.max_drawdown, self.peak_equity - self.equity)

    def _pnl(self, trade: dict[str, Any], exit_price: float, volume: float | None = None) -> float:
        volume = float(volume if volume is not None else trade.get("volume_remaining", trade.get("volume_original", 0.0)))
        if trade["type"] == "BUY":
            return round((exit_price - float(trade["entry_price"])) * volume, 6)
        return round((float(trade["entry_price"]) - exit_price) * volume, 6)

    def _exit_fill(self, candle: dict[str, Any], trade: dict[str, Any], exit_price: float, volume: float, reason: str):
        return apply_exit_costs(
            side=trade["type"],
            requested_exit=exit_price,
            execution_model=self.execution_model,
            news_blocked_or_near=_is_news_near(trade, candle),
            volume=volume,
            reason=reason,
        )

    def _protected_sl(self, trade: dict[str, Any]) -> float:
        entry = float(trade["entry_price"])
        risk = float(trade["risk_points"])
        if trade["type"] == "BUY":
            return round(entry + self.execution_model.be_plus_r * risk, 6)
        return round(entry - self.execution_model.be_plus_r * risk, 6)

    async def _write_active_positions(self) -> None:
        if not self.config.write_blackboard_positions:
            return
        async with self.blackboard._lock:
            active = self.blackboard._data.setdefault("active_trades", {})
            active.clear()
            active.update({ticket: dict(trade) for ticket, trade in self.active_positions.items()})
            positions = self.blackboard._data.setdefault("positions", {}).setdefault("open_positions", [])
            positions.clear()
            positions.extend(dict(trade) for trade in self.active_positions.values())

    def _record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event.setdefault("recorded_at", _iso(datetime.now(timezone.utc)))
        self.events.append(event)
        return event

    def _event_name(self, name: str) -> str:
        if self.config.event_prefix == "tier":
            mapping = {
                "open": "tier_trade_open",
                "close": "tier_trade_close",
                "rejected": "tier_trade_rejected",
                "missed_entry": "tier_missed_entry",
                "partial_close": "tier_trade_partial_close",
                "sl_moved_be_plus": "tier_sl_moved_be_plus",
            }
            return mapping.get(name, f"tier_{name}")
        return name

    def _avg_r(self, closed: list[dict[str, Any]]) -> float:
        values = []
        for event in closed:
            risk_cash = event.get("risk_cash")
            if risk_cash is None:
                risk_cash = event.get("risk_points")
            try:
                risk = abs(float(risk_cash))
                if risk > 0:
                    values.append(float(event.get("pnl", 0.0) or 0.0) / risk)
            except (TypeError, ValueError):
                continue
        return round(sum(values) / len(values), 6) if values else 0.0

    async def on_p1_decision(self, candle: dict[str, Any], decision: dict[str, Any]) -> list[dict[str, Any]]:
        """Record P1 shadow decision without broker and without legacy trade_signal.

        Phase18: enforces daily trade limits and grade executability before opening.
        Phase18 fix: manages triggered positions BEFORE consuming signal so that
        existing trades are checked for TP/SL hits on every candle.
        """
        if not isinstance(decision, dict):
            return []
        self.last_seen_candle = dict(candle)

        # ── Check existing positions for TP/SL hits FIRST ──────────────
        events = []
        events.extend(await self._manage_triggered_positions(candle))
        events.extend(await self._manage_pending_entries(candle))

        action = str(decision.get("decision") or "UNKNOWN")
        event = {
            "event": self._event_name("p1_decision"),
            "time": _iso(candle["time"]),
            "decision": action,
            "setup_grade": decision.get("setup_grade"),
            "score_before_veto": decision.get("score_before_veto"),
            "score_after_veto": decision.get("score_after_veto"),
            "hard_veto": decision.get("hard_veto"),
            "veto_code": decision.get("veto_code"),
            "risk_multiplier": decision.get("risk_multiplier", 0.0),
            "risk_plan": decision.get("risk_plan", {}),
            # P2.3: Kasper decision context for audit trail
            "kasper_decision_recommendation": decision.get("kasper_decision_recommendation"),
            "kasper_grade": decision.get("kasper_grade"),
            "kasper_side": decision.get("kasper_side"),
            "kasper_score": decision.get("kasper_score"),
            "kasper_error": decision.get("kasper_error"),
            "scenario_key": decision.get("scenario_key"),
            "decision_id": decision.get("decision_id"),
            "scenario_id": decision.get("scenario_id"),
            "market_story": (decision.get("market_story") or "")[:200],
            "hard_veto_reason": decision.get("hard_veto_reason"),
            "enter_eligible": decision.get("enter_eligible"),
            "risk_allowed": decision.get("risk_allowed"),
        }
        events.insert(0, self._record_event(event))
        shadow_signal = _p1_decision_shadow_signal(candle, decision)

        # ── P1.1 Kasper: journalize legacy ENTER blocked by Kasper gate ──
        if shadow_signal is None and action in {"ENTER_FULL", "ENTER_REDUCED"}:
            if decision.get("_kasper_gate_blocked"):
                self.legacy_enter_blocked_count += 1
                gate_reason = decision.get("_kasper_gate_reason", "LEGACY_ENTER_BLOCKED")
                events.append(self._record_event({
                    "event": self._event_name("rejected"),
                    "reason": gate_reason,
                    "time": _iso(candle["time"]),
                    "decision": action,
                    "kasper_decision_recommendation": decision.get("kasper_decision_recommendation"),
                    "scenario_id": decision.get("scenario_id"),
                    "source": "P1_SHADOW_DECISION",
                }))
                return events

        if shadow_signal:
            # Phase18: pre-checks before consuming signal
            grade = str(shadow_signal.get("setup_grade") or "UNKNOWN")

            # Check grade executability
            if not grade_is_executable(grade):
                self.grade_blocked_count += 1
                events.append(self._record_event({
                    "event": self._event_name("rejected"),
                    "reason": "GRADE_NOT_EXECUTABLE",
                    "time": _iso(candle["time"]),
                    "setup_grade": grade,
                    "setup_type": shadow_signal.get("setup_type"),
                    "source": "P1_SHADOW_DECISION",
                }))
                return events

            # Check daily limits
            if self.config.enable_daily_limits:
                allowed, reason = can_open_shadow_trade(
                    policy=self.policy,
                    counters=self.daily_counters,
                    candle_time_str=_iso(candle["time"]),
                    grade=grade,
                )
                if not allowed:
                    self.daily_limit_rejections += 1
                    events.append(self._record_event({
                        "event": self._event_name("rejected"),
                        "reason": reason,
                        "time": _iso(candle["time"]),
                        "setup_grade": grade,
                        "setup_type": shadow_signal.get("setup_type"),
                        "source": "P1_SHADOW_DECISION",
                    }))
                    return events
                shadow_signal["daily_slot_reason"] = reason

            # ── P1 Kasper: anti-duplicate scenario gate ─────────────────
            dup_check = self._check_duplicate_scenario(shadow_signal)
            if dup_check:
                self.duplicate_rejections += 1
                events.append(self._record_event({
                    "event": self._event_name("rejected"),
                    "reason": dup_check,
                    "time": _iso(candle["time"]),
                    "setup_grade": grade,
                    "setup_type": shadow_signal.get("setup_type"),
                    "scenario_id": shadow_signal.get("scenario_id"),
                    "source": "P1_SHADOW_DECISION",
                }))
                return events

            opened_or_rejected = await self._consume_signal(
                candle,
                signal=shadow_signal,
                clear_blackboard=False,
            )
            if opened_or_rejected:
                # Record daily slot usage on successful open
                event_name = opened_or_rejected.get("event", "")
                if event_name == self._event_name("open") and self.config.enable_daily_limits:
                    slot_reason = shadow_signal.get("daily_slot_reason", "STANDARD_SLOT_AVAILABLE")
                    record_trade_opened(
                        counters=self.daily_counters,
                        candle_time_str=_iso(candle["time"]),
                        reason=slot_reason,
                    )
                events.append(opened_or_rejected)
        return events


def _risk_realism_summary(
    all_events: list[dict[str, Any]],
    closed_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Phase19: compute risk realism metrics from trade events.

    Measures how closely realized SL losses match the intended risk_cash.
    """
    closed_events = closed_events or []
    sl_close_events = [e for e in closed_events if e.get("reason") == "SL"]
    risk_realism_cases: list[dict[str, float]] = []

    for event in sl_close_events:
        risk_cash = _safe_float(event.get("risk_cash"))
        pnl = _safe_float(event.get("pnl"))
        if risk_cash is not None and risk_cash > 0 and pnl is not None:
            loss = abs(pnl)  # SL events should have negative PnL
            ratio = loss / risk_cash
            risk_realism_cases.append({
                "ticket": event.get("ticket"),
                "risk_cash": risk_cash,
                "realized_loss": loss,
                "ratio": round(ratio, 6),
            })

    # Also check open events for their risk_realism_status
    open_events = [e for e in all_events if str(e.get("event") or "") in {"open", "tier_trade_open"}]
    risk_statuses = [str(e.get("risk_realism_status") or "UNKNOWN") for e in open_events]
    failed = [s for s in risk_statuses if s not in {"PASS", "UNKNOWN"}]
    passed = [s for s in risk_statuses if s == "PASS"]

    ratios = [c["ratio"] for c in risk_realism_cases]
    max_error = max(ratios) if ratios else 0.0
    max_abs_r_error = round(abs(max_error - 1.0) if max_error > 0 else 0.0, 6)

    expected_total = round(sum(float(e.get("risk_realism_expected_loss") or e.get("risk_cash") or 0.0) for e in open_events), 6)
    realized_total = round(sum(abs(_safe_float(e.get("pnl")) or 0.0) for e in sl_close_events), 6)
    avg_ratio = round(sum(ratios) / len(ratios), 6) if ratios else None

    return {
        "risk_realism_status": (
            "PASS" if not failed and passed else ("WARN" if not failed and not passed else "FAIL")
        ),
        "risk_realism_max_abs_r_error": max_abs_r_error,
        "risk_realism_failed_count": len(failed),
        "expected_sl_loss_total": expected_total,
        "realized_sl_loss_total": realized_total,
        "avg_realized_loss_to_risk_cash": avg_ratio,
        "risk_realism_cases": risk_realism_cases,
    }


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _trade_event_context(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "tier": trade.get("tier"),
        "risk_pct": trade.get("risk_pct"),
        "score": trade.get("score"),
        "source": trade.get("source"),
        "entry_price": trade.get("entry_price"),
        "requested_entry_price": trade.get("requested_entry_price"),
        "risk_points": trade.get("risk_points"),
        "commission_total": trade.get("commission_total"),
        "fill_model": trade.get("fill_model"),
        "execution_conservative": trade.get("execution_conservative"),
        "news_execution_mode": trade.get("news_execution_mode"),
    }


def _p1_decision_shadow_signal(candle: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a P1 shadow decision into a trade signal.

    P1.1 Kasper Authority Gate: ALL trades MUST pass through
    KasperScenarioEngine. The gate checks (in order):
      1. kasper_decision_recommendation == \"ENTER_ELIGIBLE\"
      2. scenario_id is present and non-empty
      3. market_story is present and non-empty
      4. sequence_pass_fail is present

    Returns None (no trade) with a rejection reason in the decision
    dict if any Kasper gate fails.  This ensures zero trades can open
    outside of KasperScenarioEngine authority.
    """
    action = str(decision.get("decision") or "").upper()
    if action not in {"ENTER_FULL", "ENTER_REDUCED"}:
        return None

    # ── P1.1 Kasper Authority Gate ─────────────────────────────────
    kasper_recommendation = str(decision.get("kasper_decision_recommendation") or "").upper()
    scenario_id = decision.get("scenario_id")
    scenario_key = decision.get("scenario_key")
    decision_id = decision.get("decision_id")
    market_story = decision.get("market_story")
    sequence = decision.get("sequence_pass_fail")
    kasper_side = str(decision.get("kasper_side") or "").upper()
    kasper_grade = str(decision.get("kasper_grade") or decision.get("setup_grade") or "").upper()

    if kasper_recommendation != "ENTER_ELIGIBLE":
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = (
            "KASPER_AUTHORITY_MISSING"
            if not kasper_recommendation
            else f"KASPER_NOT_ENTER_ELIGIBLE:{kasper_recommendation}"
        )
        return None

    if not scenario_id:
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = "KASPER_SCENARIO_ID_MISSING"
        return None

    # P2.2: scenario_key and decision_id required
    if not scenario_key:
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = "KASPER_SCENARIO_KEY_MISSING"
        return None

    if not decision_id:
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = "KASPER_DECISION_ID_MISSING"
        return None

    if not market_story:
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = "KASPER_MARKET_STORY_MISSING"
        return None

    if not isinstance(sequence, dict) or not sequence:
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = "KASPER_SEQUENCE_MISSING"
        return None

    # P2.2: kasper_side must be BUY or SELL
    if kasper_side not in ("BUY", "SELL"):
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = f"KASPER_SIDE_INVALID:{kasper_side}"
        return None

    # P2.2: RR must be >= 1.5
    kasper_rr = decision.get("kasper_rr_estimate")
    if kasper_rr is not None:
        try:
            if float(kasper_rr) < 1.5:
                decision["_kasper_gate_blocked"] = True
                decision["_kasper_gate_reason"] = f"KASPER_RR_BELOW_MINIMUM:{kasper_rr}"
                return None
        except (TypeError, ValueError):
            pass

    # P2.2: grade must be A_PLUS, A, or B for trade
    if kasper_grade and kasper_grade in ("C", "D"):
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = f"KASPER_GRADE_NOT_TRADABLE:{kasper_grade}"
        return None

    # P2.2: kasper_error must be absent
    if decision.get("kasper_error"):
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = f"KASPER_ERROR_PRESENT:{decision.get('kasper_error')}"
        return None
    # ── end Kasper Authority Gate ──────────────────────────────────

    if decision.get("enter_eligible") is not True:
        return None
    risk_plan = decision.get("risk_plan") if isinstance(decision.get("risk_plan"), dict) else {}
    if risk_plan.get("allowed") is not True:
        return None
    if float(decision.get("risk_multiplier") or risk_plan.get("risk_multiplier") or 0.0) <= 0.0:
        return None

    side = _p1_decision_side(decision)
    if side not in {"BUY", "SELL"}:
        return None

    # P1 Kasper: extract scenario/POI/liquidity IDs for anti-duplicate gate
    evidence = decision.get("p1_evidence_bundle") if isinstance(decision.get("p1_evidence_bundle"), dict) else {}
    ev_poi = evidence.get("poi") if isinstance(evidence.get("poi"), dict) else {}
    ev_liq = evidence.get("liquidity") if isinstance(evidence.get("liquidity"), dict) else {}
    ev_micro = evidence.get("micro") if isinstance(evidence.get("micro"), dict) else {}
    selected_poi = ev_poi.get("selected_poi") if isinstance(ev_poi.get("selected_poi"), dict) else {}

    # P1.1 Kasper: prefer Agent5-computed entry/SL when available
    agent5_entry = _safe_float(ev_micro.get("entry_price_candidate"))
    agent5_sl = _safe_float(ev_micro.get("stop_loss_candidate"))
    agent5_rr = _safe_float(ev_micro.get("rr_estimate"))

    if agent5_entry > 0 and agent5_sl > 0:
        entry = agent5_entry
        stop_loss = agent5_sl
        entry_source = "AGENT5_RR_CANDIDATE"
    else:
        entry = float(candle.get("close") or 0.0)
        if entry <= 0.0:
            return None
        bounds = _p1_decision_price_bounds(decision)
        if not bounds:
            return None
        low = _safe_float(bounds.get("low") or bounds.get("bottom"))
        high = _safe_float(bounds.get("high") or bounds.get("top"))
        if low <= 0.0 or high <= 0.0 or high <= low:
            return None
        candle_range = abs(float(candle.get("high") or entry) - float(candle.get("low") or entry))
        buffer = max(candle_range * 0.10, 0.10)
        stop_loss = min(low, entry) - buffer if side == "BUY" else max(high, entry) + buffer
        entry_source = "POI_PRICE_BOUNDS"
        agent5_rr = None

    if side == "BUY" and stop_loss >= entry:
        return None
    if side == "SELL" and stop_loss <= entry:
        return None

    # ── P2.2 Side consistency gate ─────────────────────────────────
    # kasper_side MUST match signal_side. If mismatch, REJECT.
    if kasper_side and kasper_side in ("BUY", "SELL") and kasper_side != side:
        decision["_kasper_gate_blocked"] = True
        decision["_kasper_gate_reason"] = (
            f"SIDE_MISMATCH_KASPER_PDE_TRADE:kasper={kasper_side}_signal={side}"
        )
        return None
    # ── end side consistency gate ──────────────────────────────────

    metadata = risk_plan.get("metadata") if isinstance(risk_plan.get("metadata"), dict) else {}
    # P2.2: determine side alignment fields
    signal_side = side
    side_alignment_status = "ALIGNED" if kasper_side == signal_side else "MISMATCH"
    return {
        "signal": side,
        "entry_type": "MARKET",
        "entry_price": entry,
        "stop_loss": stop_loss,
        "volume": 1.0,
        "risk_pct": metadata.get("effective_risk_pct") or risk_plan.get("risk_pct"),
        "risk_cash": risk_plan.get("risk_amount"),
        "score": decision.get("score_after_veto"),
        "source": "P1_SHADOW_DECISION",
        "direction_source": "P1_EVIDENCE_BUNDLE",
        "entry_source": entry_source,
        "sl_source": "AGENT5_RR_CANDIDATE" if entry_source == "AGENT5_RR_CANDIDATE" else "POI_PRICE_BOUNDS",
        "setup_type": decision.get("setup_type"),
        "setup_grade": decision.get("setup_grade"),
        "setup_family": decision.get("setup_family"),
        # P1.1 Kasper: Agent5 RR fields propagated to trade
        "rr_estimate": agent5_rr,
        "tp1_price": ev_micro.get("tp1_candidate"),
        "tp2_price": ev_micro.get("tp2_candidate"),
        "target_liquidity": ev_micro.get("target_liquidity"),
        # P2.2: Scenario identity fields
        "scenario_id": decision.get("scenario_id"),
        "scenario_key": decision.get("scenario_key"),
        "decision_id": decision.get("decision_id"),
        "scenario_type": decision.get("scenario_type"),
        "market_story": decision.get("market_story"),
        "sequence_pass_fail": decision.get("sequence_pass_fail"),
        "identity_components": decision.get("identity_components"),
        # P2.2: Side consistency fields
        "kasper_side": kasper_side,
        "pde_side": side,
        "signal_side": signal_side,
        "side_alignment_status": side_alignment_status,
        # P1 Kasper: grade + anti-duplicate identifiers
        "kasper_grade": decision.get("kasper_grade"),
        "kasper_score": decision.get("kasper_score"),
        "trade_open_source": "KASPER_SCENARIO_ENGINE",
        "poi_type": selected_poi.get("type"),
        "poi_low": selected_poi.get("low"),
        "poi_high": selected_poi.get("high"),
        "liquidity_event_type": ev_liq.get("sweep_type") or ev_liq.get("liquidity_event_type"),
        "swept_level": ev_liq.get("swept_level"),
        "sweep_time": ev_liq.get("sweep_time"),
        # P2.2: Grade/risk mapping fields
        "requested_risk_pct": metadata.get("effective_risk_pct") or risk_plan.get("risk_pct"),
        "effective_risk_pct": risk_plan.get("risk_pct"),
        "risk_grade_source": "KASPER_GRADE",
        "risk_cap_applied": metadata.get("risk_cap_applied", False),
        "risk_cap_reason": metadata.get("risk_cap_reason"),
    }


def _p1_decision_side(decision: dict[str, Any]) -> str:
    """Determine trade side with priority:
    1. kasper_side (authoritative — from KasperScenarioEngine)
    2. decision.side explicit if coherent with kasper_side
    3. evidence context direction only if kasper_side absent

    P2.2: kasper_side is the source of truth for side.
    """
    kasper_side = str(decision.get("kasper_side") or "").upper()
    decision_side = str(decision.get("side") or "").upper()

    # Priority 1: kasper_side (authoritative)
    if kasper_side in ("BUY", "SELL"):
        return kasper_side

    # Priority 2: decision.side (only if kasper_side absent)
    if decision_side in ("BUY", "LONG"):
        return "BUY"
    if decision_side in ("SELL", "SHORT"):
        return "SELL"

    # Priority 3: evidence context (only if kasper_side absent)
    bundle = decision.get("p1_evidence_bundle") if isinstance(decision.get("p1_evidence_bundle"), dict) else {}
    for value in (
        bundle.get("side"),
        (bundle.get("context") or {}).get("direction"),
    ):
        normalized = str(value or "").upper()
        if normalized in ("BUY", "LONG"):
            return "BUY"
        if normalized in ("SELL", "SHORT"):
            return "SELL"
    return "NONE"


def _p1_decision_price_bounds(decision: dict[str, Any]) -> dict[str, Any]:
    bundle = decision.get("p1_evidence_bundle") if isinstance(decision.get("p1_evidence_bundle"), dict) else {}
    poi = bundle.get("poi") if isinstance(bundle.get("poi"), dict) else {}
    bounds = poi.get("price_bounds")
    if isinstance(bounds, dict) and bounds:
        return bounds
    selected = poi.get("selected_poi")
    if isinstance(selected, dict) and isinstance(selected.get("price_bounds"), dict):
        return selected.get("price_bounds") or {}
    return {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _r_multiple(trade: dict[str, Any], pnl: float) -> float | None:
    risk_cash = trade.get("risk_cash")
    try:
        risk = abs(float(risk_cash if risk_cash is not None else trade.get("risk_points")))
    except (TypeError, ValueError):
        return None
    return round(float(pnl) / risk, 6) if risk > 0 else None


def _is_news_near(payload: dict[str, Any], candle: dict[str, Any]) -> bool:
    for key in ("news_blocked_or_near", "news_near", "near_news", "news_blocked"):
        if bool(payload.get(key)):
            return True
    news_context = payload.get("news_context")
    if isinstance(news_context, dict):
        if bool(news_context.get("blocked") or news_context.get("near") or news_context.get("news_blocked_or_near")):
            return True
        status = str(news_context.get("status") or news_context.get("veto_code") or "").upper()
        if any(token in status for token in ("NEWS", "CPI", "NFP", "FOMC")) and status not in {"NO_NEWS_IN_DATASET", "CLEAR"}:
            return True
    return bool(candle.get("news_blocked_or_near") or candle.get("news_near"))


def _is_limit_signal(signal: dict[str, Any]) -> bool:
    return str(signal.get("entry_type") or signal.get("order_type") or "").upper() == "LIMIT"


def _limit_signal_spread(signal: dict[str, Any], execution_model: ReplayExecutionModel) -> float:
    if signal.get("spread") is not None:
        return float(signal.get("spread") or 0.0)
    return float(execution_model.spread_points(news_blocked_or_near=bool(signal.get("news_blocked_or_near"))))


def _limit_signal_fills(signal: dict[str, Any], candle: dict[str, Any], execution_model: ReplayExecutionModel) -> bool:
    price = signal.get("entry_price") or signal.get("limit_price")
    if price is None:
        return False
    return evaluate_limit_entry_fill(
        side=str(signal.get("signal") or signal.get("side") or ""),
        limit_price=float(price),
        candle=candle,
        spread=_limit_signal_spread(signal, execution_model),
    )

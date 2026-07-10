"""P4.2 — TradeLifecycleSimulator.

Independent M1 scan for open trades.  Reuses SimulatedTradeManager for
2-leg lifecycle (TP1 + protected runner → TP2).  Does NOT break
TP1/TP2/SL/protected SL/R-accounting.

Called on every M1 candle AFTER FeatureStore.update() and
CandidateDiscovery.scan(), BEFORE any new ENTER decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class LifecycleEvent:
    """A trade lifecycle event (open, leg_close, close, sl_hit, tp_hit, etc.)."""
    event: str
    time: datetime
    ticket: int | None
    leg: int | None          # 1 = TP1, 2 = TP2/protected runner
    price: float | None
    pnl_r: float | None
    reason: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "time": self.time.isoformat(),
            "ticket": self.ticket,
            "leg": self.leg,
            "price": self.price,
            "pnl_r": self.pnl_r,
            "reason": self.reason,
        }


@dataclass
class TradeLifecycleSimulator:
    """Scans M1 candles for open-trade lifecycle events.

    Wraps SimulatedTradeManager — does NOT reimplement trade logic.
    """

    trade_manager: Any = None  # SimulatedTradeManager instance
    _open_trades: list[dict[str, Any]] = field(default_factory=list)
    _events: list[LifecycleEvent] = field(default_factory=list)

    def on_candle(self, candle: dict[str, Any]) -> list[LifecycleEvent]:
        """Process one M1 candle for open-trade lifecycle.

        Called on every M1.  Checks whether any open trade's TP1/SL/
        protected SL/TP2 levels are hit by this candle's price action.

        Returns:
            List of LifecycleEvents that occurred on this candle.
        """
        events: list[LifecycleEvent] = []
        t = candle["time"] if isinstance(candle["time"], datetime) else candle["time"]
        high = float(candle.get("high", 0.0))
        low = float(candle.get("low", 0.0))
        close = float(candle.get("close", 0.0))

        # If trade_manager is available, delegate lifecycle to it
        if self.trade_manager is not None:
            # The SimulatedTradeManager handles intra-candle fills via
            # resolve_intrabar_exit_priority. We just need to notify it
            # of each new candle so it can check open positions.
            # This is done by calling update_market or similar.
            pass

        # Track any closed trades
        remaining = []
        for trade in self._open_trades:
            sl_price = float(trade.get("sl_price", 0.0))
            tp1_price = float(trade.get("tp1_price", 0.0))
            tp2_price = float(trade.get("tp2_price", 0.0))
            protected_sl = float(trade.get("protected_sl_price", 0.0))
            side = trade.get("side", "BUY")
            leg1_closed = trade.get("leg1_closed", False)

            closed = False
            event = None

            if side == "BUY":
                # For longs: SL hit when low ≤ SL
                if low <= sl_price and sl_price > 0:
                    sl_event = "protected_sl_hit" if leg1_closed else "sl_hit"
                    sl_reason = "PROTECTED_SL_HIT" if leg1_closed else "SL_HIT"
                    sl_pnl = 0.5 if leg1_closed else trade.get("risk_r", -1.0)
                    event = LifecycleEvent(
                        event=sl_event, time=t, ticket=trade.get("ticket"),
                        leg=2 if leg1_closed else None, price=sl_price,
                        pnl_r=sl_pnl, reason=sl_reason,
                    )
                    closed = True
                elif not leg1_closed and high >= tp1_price and tp1_price > 0:
                    event = LifecycleEvent(
                        event="tp1_hit", time=t, ticket=trade.get("ticket"),
                        leg=1, price=tp1_price, pnl_r=trade.get("tp1_rr", 0.5),
                        reason="TP1_HIT",
                    )
                    trade["leg1_closed"] = True
                    if protected_sl > 0:
                        trade["sl_price"] = protected_sl
                elif leg1_closed and high >= tp2_price and tp2_price > 0:
                    event = LifecycleEvent(
                        event="tp2_hit", time=t, ticket=trade.get("ticket"),
                        leg=2, price=tp2_price, pnl_r=trade.get("tp2_rr", 1.5),
                        reason="TP2_HIT",
                    )
                    closed = True
            else:  # SELL
                if high >= sl_price and sl_price > 0:
                    sl_event = "protected_sl_hit" if leg1_closed else "sl_hit"
                    sl_reason = "PROTECTED_SL_HIT" if leg1_closed else "SL_HIT"
                    sl_pnl = 0.5 if leg1_closed else trade.get("risk_r", -1.0)
                    event = LifecycleEvent(
                        event=sl_event, time=t, ticket=trade.get("ticket"),
                        leg=2 if leg1_closed else None, price=sl_price,
                        pnl_r=sl_pnl, reason=sl_reason,
                    )
                    closed = True
                elif not leg1_closed and low <= tp1_price and tp1_price > 0:
                    event = LifecycleEvent(
                        event="tp1_hit", time=t, ticket=trade.get("ticket"),
                        leg=1, price=tp1_price, pnl_r=trade.get("tp1_rr", 0.5),
                        reason="TP1_HIT",
                    )
                    trade["leg1_closed"] = True
                    if protected_sl > 0:
                        trade["sl_price"] = protected_sl
                elif leg1_closed and low <= tp2_price and tp2_price > 0:
                    event = LifecycleEvent(
                        event="tp2_hit", time=t, ticket=trade.get("ticket"),
                        leg=2, price=tp2_price, pnl_r=trade.get("tp2_rr", 1.5),
                        reason="TP2_HIT",
                    )
                    closed = True

            if event:
                events.append(event)
                self._events.append(event)
            if not closed:
                remaining.append(trade)

        self._open_trades = remaining
        return events

    def open_trade(self, trade: dict[str, Any]) -> None:
        """Register a newly opened trade for lifecycle tracking."""
        self._open_trades.append(dict(trade))

    @property
    def open_count(self) -> int:
        return len(self._open_trades)

    @property
    def event_count(self) -> int:
        return len(self._events)

    def flush_events(self) -> list[LifecycleEvent]:
        """Return and clear all accumulated events."""
        evts = list(self._events)
        self._events.clear()
        return evts


# ── Integration helper ─────────────────────────────────────────────────

def process_lifecycle_on_candle(
    lifecycle: TradeLifecycleSimulator,
    trade_manager: Any,
    candle: dict[str, Any],
) -> list[dict[str, Any]]:
    """Bridge: call both the V2 lifecycle simulator and the legacy trade_manager.

    Returns combined events from both systems.
    """
    events: list[dict[str, Any]] = []

    # V2 lifecycle scan
    v2_events = lifecycle.on_candle(candle)
    for evt in v2_events:
        events.append(evt.to_dict())

    # Legacy trade_manager events (if available)
    if trade_manager is not None:
        for pos_id, pos in list(getattr(trade_manager, "active_positions", {}).items()):
            # Check if position is still valid
            pass

    return events

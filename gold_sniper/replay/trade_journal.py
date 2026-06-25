"""Trade journal JSONL helpers for replay fills."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json


@dataclass(frozen=True)
class TradeJournalEvent:
    event: str
    time: str
    ticket: int | None
    side: str | None = None
    reason: str | None = None
    requested_price: float | None = None
    fill_price: float | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    sl: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    volume: float | None = None
    pnl: float | None = None
    commission: float = 0.0
    spread_points: float = 0.0
    slippage_points: float = 0.0
    fill_model: str = "UNKNOWN"
    conservative: bool = True
    equity: float | None = None
    risk_points: float | None = None
    risk_cash: float | None = None
    r_multiple: float | None = None
    pure_r_multiple: float | None = None  # P3: R before exit costs (requested price)
    leg: int | None = None  # P3: leg number (1 or 2) for leg_close events
    # P1.1/Phase2: Kasper scenario fields
    scenario_id: str | None = None
    scenario_type: str | None = None
    market_story: str | None = None
    sequence_pass_fail: dict[str, Any] | None = None
    kasper_grade: str | None = None
    kasper_score: float | None = None
    entry_source: str | None = None
    sl_source: str | None = None
    rr_estimate: float | None = None
    target_liquidity: float | None = None
    trade_open_source: str = "KASPER_SCENARIO_ENGINE"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeJournal:
    def __init__(self) -> None:
        self.events: list[TradeJournalEvent] = []

    def add(self, event: TradeJournalEvent) -> TradeJournalEvent:
        self.events.append(event)
        return event

    def extend_from_trade_manager_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            self.add(event_from_dict(event))

    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(e.to_dict(), ensure_ascii=False, default=str) for e in self.events)

    def save_jsonl(self, output_path: str | Path) -> None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        text = self.to_jsonl()
        output.write_text(text + ("\n" if text else ""), encoding="utf-8")


def event_from_dict(data: dict[str, Any]) -> TradeJournalEvent:
    return TradeJournalEvent(
        event=str(data.get("event") or "UNKNOWN"),
        time=str(data.get("time") or data.get("recorded_at") or ""),
        ticket=data.get("ticket"),
        side=data.get("type") or data.get("side"),
        reason=data.get("reason"),
        requested_price=data.get("requested_price"),
        fill_price=data.get("fill_price"),
        entry_price=data.get("entry_price"),
        exit_price=data.get("exit_price"),
        sl=data.get("sl") or data.get("current_sl"),
        tp1=data.get("tp1"),
        tp2=data.get("tp2"),
        volume=data.get("volume") or data.get("volume_remaining"),
        pnl=data.get("pnl"),
        commission=float(data.get("commission", 0.0) or 0.0),
        spread_points=float(data.get("spread_points", 0.0) or 0.0),
        slippage_points=float(data.get("slippage_points", 0.0) or 0.0),
        fill_model=str(data.get("fill_model") or "UNKNOWN"),
        conservative=bool(data.get("conservative", True)),
        equity=data.get("equity"),
        risk_points=data.get("risk_points"),
        risk_cash=data.get("risk_cash"),
        r_multiple=data.get("r_multiple"),
        pure_r_multiple=data.get("pure_parent_pnl_R") or data.get("pure_pnl_R"),  # P3: R before exit costs
        leg=data.get("leg"),  # P3: leg number for leg_close events
        # P1.1/Phase2: Kasper scenario fields
        scenario_id=data.get("scenario_id"),
        scenario_type=data.get("scenario_type"),
        market_story=data.get("market_story"),
        sequence_pass_fail=data.get("sequence_pass_fail") if isinstance(data.get("sequence_pass_fail"), dict) else None,
        kasper_grade=data.get("kasper_grade"),
        kasper_score=_safe_float_or_none(data.get("kasper_score")),
        entry_source=data.get("entry_source"),
        sl_source=data.get("sl_source"),
        rr_estimate=_safe_float_or_none(data.get("rr_estimate")),
        target_liquidity=_safe_float_or_none(data.get("target_liquidity")),
        trade_open_source=data.get("trade_open_source", "KASPER_SCENARIO_ENGINE"),
        metadata=dict(data.get("metadata") or {}),
    )


def _safe_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

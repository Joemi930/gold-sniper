"""Faithful offline execution model for replay simulations."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BrokerExecutionProfile:
    broker: str = "JustMarkets"
    symbol: str = "XAUUSD"
    contract_size: float = 100.0
    min_lot: float = 0.01
    max_lot: float = 100.0
    lot_step: float = 0.01
    stops_level_points: float = 0.0
    avg_spread_pips: float = 2.0
    points_per_pip: float = 10.0
    commission_per_lot_side_usd: float = 0.0
    source: str = "JustMarkets XAUUSD official specification"
    source_notes: str = "avg spread 2 pips, commission 0 USD/lot/side, contract size 100"

    @property
    def avg_spread_points(self) -> float:
        return round(float(self.avg_spread_pips) * float(self.points_per_pip), 6)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["avg_spread_points"] = self.avg_spread_points
        return payload


@dataclass(frozen=True)
class ReplayExecutionModel:
    profile: BrokerExecutionProfile = field(default_factory=BrokerExecutionProfile)
    initial_equity: float = 100.0
    tp1_rr: float = 1.0
    tp2_rr: float = 2.0
    partial_close_pct: float = 50.0
    be_plus_r: float = 0.5
    spread_mode: str = "conservative_fixed"
    slippage_points: float = 5.0
    news_slippage_multiplier: float = 3.0
    news_spread_multiplier: float = 2.0
    fill_model: str = "conservative_intrabar"
    require_execution_model: bool = True
    source: str = "P2-C faithful replay simulation"

    def spread_points(self, *, news_blocked_or_near: bool = False) -> float:
        base = self.profile.avg_spread_points
        if news_blocked_or_near:
            base *= self.news_spread_multiplier
        return round(base, 6)

    def slippage_for_event(self, *, news_blocked_or_near: bool = False) -> float:
        base = float(self.slippage_points)
        if news_blocked_or_near:
            base *= float(self.news_slippage_multiplier)
        return round(base, 6)

    def commission_for_volume(self, volume: float, *, sides: int = 1) -> float:
        return round(float(volume) * self.profile.commission_per_lot_side_usd * int(sides), 6)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.initial_equity <= 0:
            errors.append("INITIAL_EQUITY_INVALID")
        if self.tp1_rr <= 0:
            errors.append("TP1_RR_INVALID")
        if self.tp2_rr <= self.tp1_rr:
            errors.append("TP2_MUST_BE_GREATER_THAN_TP1")
        if not 0 < self.partial_close_pct < 100:
            errors.append("PARTIAL_CLOSE_PCT_INVALID")
        if self.be_plus_r < 0 or self.be_plus_r > 1.0:
            errors.append("BE_PLUS_R_OUT_OF_BOUNDS")
        if self.profile.avg_spread_points <= 0:
            errors.append("SPREAD_MUST_BE_POSITIVE")
        if self.slippage_points < 0:
            errors.append("SLIPPAGE_NEGATIVE")
        if self.fill_model not in {"conservative_intrabar", "market_proxy", "limit_touch_conservative"}:
            errors.append("UNSUPPORTED_FILL_MODEL")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.to_dict(),
            "initial_equity": self.initial_equity,
            "tp1_rr": self.tp1_rr,
            "tp2_rr": self.tp2_rr,
            "partial_close_pct": self.partial_close_pct,
            "be_plus_r": self.be_plus_r,
            "spread_mode": self.spread_mode,
            "slippage_points": self.slippage_points,
            "news_slippage_multiplier": self.news_slippage_multiplier,
            "news_spread_multiplier": self.news_spread_multiplier,
            "fill_model": self.fill_model,
            "require_execution_model": self.require_execution_model,
            "source": self.source,
            "validation_errors": self.validate(),
        }


DEFAULT_REPLAY_EXECUTION_MODEL = ReplayExecutionModel()


def build_default_execution_model(initial_equity: float = 100.0) -> ReplayExecutionModel:
    return ReplayExecutionModel(initial_equity=float(initial_equity))

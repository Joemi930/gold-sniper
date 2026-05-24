from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

from config import RISK_PCT_PER_TRADE, SYMBOL


@dataclass(frozen=True)
class SymbolSizingInfo:
    trade_tick_value: float = 1.0
    trade_tick_size: float = 0.01
    volume_min: float = 0.01
    volume_max: float = 5.0
    volume_step: float = 0.01
    contract_size: float = 100.0


def _symbol_info(symbol: str) -> SymbolSizingInfo:
    if mt5 is not None:
        info = mt5.symbol_info(symbol)
        if info is not None:
            return SymbolSizingInfo(
                trade_tick_value=float(getattr(info, "trade_tick_value", 1.0) or 1.0),
                trade_tick_size=float(getattr(info, "trade_tick_size", 0.01) or 0.01),
                volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
                volume_max=float(getattr(info, "volume_max", 5.0) or 5.0),
                volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
                contract_size=float(getattr(info, "trade_contract_size", 100.0) or 100.0),
            )
    return SymbolSizingInfo()


def _round_volume(volume: float, symbol_info: SymbolSizingInfo) -> float:
    step = symbol_info.volume_step or 0.01
    rounded = round(volume / step) * step
    clipped = max(symbol_info.volume_min, min(rounded, min(symbol_info.volume_max, 5.0)))
    decimals = max(0, len(str(step).split(".")[-1]) if "." in str(step) else 0)
    return round(float(clipped), decimals)


def calculate_dynamic_lot(
    account_equity: float,
    entry_price: float,
    sl_price: float,
    risk_pct: float = RISK_PCT_PER_TRADE,
    atr_14: float | None = None,
    atr_baseline: float | None = None,
    risk_modifier: float = 1.0,
    symbol: str = SYMBOL,
    symbol_info: Any | None = None,
) -> dict:
    """
    Calcule une taille de position equity-based et ATR-aware.

    Le risque nominal vient de config.RISK_PCT_PER_TRADE. L'ATR agit sur deux axes:
    - le SL de sizing ne peut pas etre inferieur a l'ATR courant;
    - si ATR > baseline, le risque effectif est reduit progressivement.
    """
    if account_equity <= 0:
        return {"lot": 0.01, "error": "ACCOUNT_EQUITY_ZERO"}

    base_sl_distance = abs(float(entry_price) - float(sl_price))
    atr_distance = float(atr_14 or 0.0)
    sizing_sl_distance = max(base_sl_distance, atr_distance)
    if sizing_sl_distance <= 0:
        return {"lot": 0.01, "error": "SL_DISTANCE_ZERO"}

    effective_risk_pct = float(risk_pct) * float(risk_modifier)
    volatility_ratio = None
    volatility_reduction = 0.0

    if atr_14 is not None and atr_baseline and atr_baseline > 0:
        volatility_ratio = float(atr_14) / float(atr_baseline)
        if volatility_ratio > 1.0:
            volatility_reduction = min((volatility_ratio - 1.0) * 0.5, 0.5)
            effective_risk_pct *= 1.0 - volatility_reduction

    risk_amount = float(account_equity) * (effective_risk_pct / 100.0)
    info = symbol_info or _symbol_info(symbol)
    if not isinstance(info, SymbolSizingInfo):
        info = SymbolSizingInfo(
            trade_tick_value=float(getattr(info, "trade_tick_value", 1.0) or 1.0),
            trade_tick_size=float(getattr(info, "trade_tick_size", 0.01) or 0.01),
            volume_min=float(getattr(info, "volume_min", 0.01) or 0.01),
            volume_max=float(getattr(info, "volume_max", 5.0) or 5.0),
            volume_step=float(getattr(info, "volume_step", 0.01) or 0.01),
            contract_size=float(getattr(info, "trade_contract_size", 100.0) or 100.0),
        )

    if info.trade_tick_size > 0 and info.trade_tick_value > 0:
        loss_per_lot = sizing_sl_distance * (info.trade_tick_value / info.trade_tick_size)
    else:
        loss_per_lot = sizing_sl_distance * info.contract_size

    if loss_per_lot <= 0:
        return {"lot": 0.01, "error": "LOSS_PER_LOT_ZERO"}

    raw_lot = risk_amount / loss_per_lot
    final_lot = _round_volume(raw_lot, info)

    return {
        "lot": final_lot,
        "risk_amount": round(risk_amount, 2),
        "configured_risk_pct": float(risk_pct),
        "effective_risk_pct": round(effective_risk_pct, 4),
        "risk_modifier": float(risk_modifier),
        "sl_distance": round(base_sl_distance, 5),
        "sizing_sl_distance": round(sizing_sl_distance, 5),
        "atr_14": atr_14,
        "atr_baseline": atr_baseline,
        "volatility_ratio": round(volatility_ratio, 4) if volatility_ratio is not None else None,
        "volatility_reduction": round(volatility_reduction, 4),
        "loss_per_lot": round(loss_per_lot, 4),
        "raw_lot": round(raw_lot, 4),
        "volatility_adjusted": atr_14 is not None,
    }

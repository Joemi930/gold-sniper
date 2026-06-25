try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

from config import PAPER_SIMULATED_EQUITY, RISK_PCT_PER_TRADE
from utils.risk_calculator import calculate_dynamic_lot


class RiskCalculator:
    def __init__(self, risk_percent: float = RISK_PCT_PER_TRADE):
        self.risk_percent = risk_percent
        self.default_lot = 0.01

    def _account_equity(self) -> float:
        if mt5 is not None:
            account_info = mt5.account_info()
            if account_info is not None:
                return float(getattr(account_info, "equity", PAPER_SIMULATED_EQUITY))
        return float(PAPER_SIMULATED_EQUITY)

    def calculate_lot_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        risk_modifier: float = 1.0,
        atr_14: float | None = None,
        atr_baseline: float | None = None,
    ) -> float:
        result = calculate_dynamic_lot(
            account_equity=self._account_equity(),
            entry_price=entry_price,
            sl_price=stop_loss,
            risk_pct=self.risk_percent,
            atr_14=atr_14,
            atr_baseline=atr_baseline,
            risk_modifier=risk_modifier,
            symbol=symbol,
        )
        return float(result.get("lot", self.default_lot))

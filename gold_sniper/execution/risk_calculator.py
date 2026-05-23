# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — RISK CALCULATOR
# ═══════════════════════════════════════════════════════════════════════════════
#
# Module chargé de calculer la taille de position dynamique en fonction du
# capital disponible et du risque autorisé (ex: 1%).
#
# ═══════════════════════════════════════════════════════════════════════════════

import MetaTrader5 as mt5

class RiskCalculator:
    def __init__(self, risk_percent: float = 1.0):
        self.risk_percent = risk_percent / 100.0
        # Lot fixe par défaut si le calcul dynamique échoue ou pour le PoC
        self.default_lot = 0.01

    def calculate_lot_size(self, symbol: str, entry_price: float, stop_loss: float, risk_modifier: float = 1.0) -> float:
        """
        Calcule la taille du lot en fonction de la distance du Stop Loss
        et du solde (Equity) du compte MT5. Le risque est multiplié par risk_modifier.
        """
        account_info = mt5.account_info()
        if account_info is None:
            return self.default_lot

        equity = account_info.equity
        risk_amount = equity * self.risk_percent * risk_modifier
        
        # Distance en pips/points
        distance = abs(entry_price - stop_loss)
        if distance == 0:
            return self.default_lot
            
        # Valeur d'un point pour le symbole (ex: XAUUSD)
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return self.default_lot
            
        point_value = symbol_info.trade_tick_value / symbol_info.trade_tick_size if symbol_info.trade_tick_size != 0 else 1.0
        
        # Calcul : (Risque en $) / (Distance * Valeur du point)
        try:
            lot_size = risk_amount / (distance * point_value)
            # Arrondi à la décimale autorisée par le broker (souvent 0.01 pour le forex/gold)
            step = symbol_info.volume_step if symbol_info.volume_step != 0 else 0.01
            lot_size = round(lot_size / step) * step
            
            # Limites max/min
            lot_size = max(symbol_info.volume_min, min(lot_size, symbol_info.volume_max))
            return float(lot_size)
        except Exception:
            return self.default_lot

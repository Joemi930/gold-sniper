# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER v1.0 — ORDER EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════════
#
# Module chargé de construire et d'envoyer de façon atomique la requête de
# passage d'ordre à MetaTrader5.
#
# ═══════════════════════════════════════════════════════════════════════════════

import MetaTrader5 as mt5
from config import SYMBOL, MAGIC_NUMBER
import asyncio
from utils.logger import get_logger

class OrderExecutor:
    def __init__(self):
        self.logger = get_logger()

    async def execute_trade(self, action: str, volume: float, price: float, sl: float, tp: float) -> mt5.OrderSendResult:
        """
        Envoie l'ordre au marché via asyncio.to_thread pour ne pas bloquer.
        """
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": float(volume),
            "type": order_type,
            "price": float(price),
            "sl": float(sl),
            "tp": float(tp),
            "deviation": 20,
            "magic": MAGIC_NUMBER,
            "comment": "GoldSniper V1",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        self.logger.debug(f"Préparation de l'ordre {action} de {volume} lots à {price}")
        result = await asyncio.to_thread(mt5.order_send, request)
        return result

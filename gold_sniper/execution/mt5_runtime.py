from __future__ import annotations

from typing import Any


try:
    import MetaTrader5 as _mt5
except ImportError:  # pragma: no cover - depends on local MT5 installation
    _mt5 = None


mt5: Any = _mt5


def is_mt5_available() -> bool:
    return mt5 is not None


def require_mt5() -> Any:
    if mt5 is None:
        raise RuntimeError("MetaTrader5 unavailable")
    return mt5

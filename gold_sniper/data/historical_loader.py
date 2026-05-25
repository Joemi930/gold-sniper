from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - MT5 est disponible sur la machine trading
    mt5 = None

from config import MT5_ACCOUNT, MT5_PASSWORD, MT5_PATH, MT5_SERVER, SYMBOL


DATA_DIR = Path("data/historical")
DATA_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_DAYS = 183
CACHE_TTL = timedelta(hours=24)
DOWNLOAD_CHUNK_DAYS = 28


class HistoricalProvider(Protocol):
    def copy_rates_range(self, symbol: str, timeframe: int, start: datetime, end: datetime):
        ...


TIMEFRAMES: dict[str, dict[str, int | str]] = {
    "M1": {"mt5": getattr(mt5, "TIMEFRAME_M1", 1), "minutes": 1, "bb_key": "1m"},
    "M5": {"mt5": getattr(mt5, "TIMEFRAME_M5", 5), "minutes": 5, "bb_key": "5m"},
    "M15": {"mt5": getattr(mt5, "TIMEFRAME_M15", 15), "minutes": 15, "bb_key": "15m"},
    "H1": {"mt5": getattr(mt5, "TIMEFRAME_H1", 60), "minutes": 60, "bb_key": "1H"},
    "H4": {"mt5": getattr(mt5, "TIMEFRAME_H4", 240), "minutes": 240, "bb_key": "4H"},
}


class MT5HistoricalProvider:
    """Provider reel MT5, avec initialisation defensive pour lancement standalone."""

    def __init__(self) -> None:
        if mt5 is None:
            raise RuntimeError("MetaTrader5 n'est pas installe.")

    def ensure_connected(self) -> None:
        terminal_info = mt5.terminal_info()
        account_info = mt5.account_info()
        if terminal_info is not None and account_info is not None:
            self._ensure_symbol_selected()
            return

        init_kwargs = {}
        if MT5_PATH:
            init_kwargs["path"] = MT5_PATH
        if not mt5.initialize(**init_kwargs):
            raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

        if MT5_ACCOUNT and MT5_PASSWORD and MT5_SERVER:
            if not mt5.login(login=MT5_ACCOUNT, password=MT5_PASSWORD, server=MT5_SERVER):
                raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

        self._ensure_symbol_selected()

    def _ensure_symbol_selected(self) -> None:
        symbol_info = mt5.symbol_info(SYMBOL)
        if symbol_info is None:
            raise RuntimeError(f"Symbole {SYMBOL} introuvable dans MT5.")
        if not symbol_info.visible and not mt5.symbol_select(SYMBOL, True):
            raise RuntimeError(f"Impossible de selectionner {SYMBOL}: {mt5.last_error()}")

    def copy_rates_range(self, symbol: str, timeframe: int, start: datetime, end: datetime):
        self.ensure_connected()
        return mt5.copy_rates_range(symbol, timeframe, start, end)


def cache_path(tf_name: str, symbol: str = SYMBOL) -> Path:
    tf = normalize_timeframe(tf_name)
    return DATA_DIR / f"{symbol}_{tf}_6months.parquet"


def normalize_timeframe(tf_name: str) -> str:
    tf = str(tf_name).upper()
    aliases = {"1M": "M1", "5M": "M5", "15M": "M15", "1H": "H1", "4H": "H4"}
    tf = aliases.get(tf, tf)
    if tf not in TIMEFRAMES:
        raise ValueError(f"Timeframe invalide: {tf_name}. Attendu: {', '.join(TIMEFRAMES)}")
    return tf


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _is_fresh(path: Path, now: datetime) -> bool:
    if not path.exists():
        return False
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return now - modified_at < CACHE_TTL


def _rates_to_dataframe(rates) -> pd.DataFrame:
    if rates is None or len(rates) == 0:
        return pd.DataFrame()

    df = pd.DataFrame(rates)
    if df.empty or "time" not in df:
        return pd.DataFrame()

    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    ordered_cols = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    ]
    existing_cols = [col for col in ordered_cols if col in df.columns]
    return df[existing_cols].sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def _trim_six_months(df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    if df.empty:
        return df
    cutoff = pd.Timestamp(now - timedelta(days=HISTORY_DAYS))
    return df[df["time"] >= cutoff].sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def _download_dataframe(
    provider: HistoricalProvider,
    symbol: str,
    tf_name: str,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    tf_const = int(TIMEFRAMES[tf_name]["mt5"])
    chunks: list[pd.DataFrame] = []
    cursor = start
    chunk_size = timedelta(days=DOWNLOAD_CHUNK_DAYS)
    while cursor < end:
        chunk_end = min(cursor + chunk_size, end)
        rates = provider.copy_rates_range(symbol, tf_const, cursor, chunk_end)
        chunk = _rates_to_dataframe(rates)
        if not chunk.empty:
            chunks.append(chunk)
        cursor = chunk_end

    if not chunks:
        return pd.DataFrame()
    merged = pd.concat(chunks, ignore_index=True)
    return merged.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def _update_stale_cache(
    cached_df: pd.DataFrame,
    provider: HistoricalProvider,
    symbol: str,
    tf_name: str,
    now: datetime,
) -> pd.DataFrame:
    if cached_df.empty:
        start = now - timedelta(days=HISTORY_DAYS)
    else:
        step = timedelta(minutes=int(TIMEFRAMES[tf_name]["minutes"]))
        last_time = cached_df["time"].max().to_pydatetime()
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        start = last_time + step
        if start >= now:
            return _trim_six_months(cached_df, now)

    missing_df = _download_dataframe(provider, symbol, tf_name, start, now)
    if missing_df.empty:
        return _trim_six_months(cached_df, now)

    merged = pd.concat([cached_df, missing_df], ignore_index=True)
    return _trim_six_months(merged, now)


def preload_historical_data(
    force_refresh: bool = False,
    provider: HistoricalProvider | None = None,
    symbol: str = SYMBOL,
) -> dict[str, pd.DataFrame]:
    """
    Precharge XAUUSD sur M1/M5/M15/H1/H4.

    Cache frais (<24h): lecture parquet. Cache stale: telecharge uniquement les
    bougies manquantes depuis la derniere bougie connue, puis fusionne.
    """
    provider = provider or MT5HistoricalProvider()
    now = _now_utc()
    loaded: dict[str, pd.DataFrame] = {}

    for tf_name in TIMEFRAMES:
        path = cache_path(tf_name, symbol)
        if not force_refresh and _is_fresh(path, now):
            loaded[tf_name] = pd.read_parquet(path)
            continue

        cached_df = pd.read_parquet(path) if path.exists() and not force_refresh else pd.DataFrame()
        if force_refresh or cached_df.empty:
            start = now - timedelta(days=HISTORY_DAYS)
            df = _download_dataframe(provider, symbol, tf_name, start, now)
            df = _trim_six_months(df, now)
        else:
            df = _update_stale_cache(cached_df, provider, symbol, tf_name, now)

        if df.empty:
            loaded[tf_name] = cached_df
            continue

        df.to_parquet(path, index=False)
        loaded[tf_name] = df

    return loaded


def get_warmup_data(tf_name: str, lookback: int = 500, symbol: str = SYMBOL) -> pd.DataFrame:
    """Retourne les N dernieres bougies du cache parquet pour un timeframe."""
    path = cache_path(tf_name, symbol)
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    if df.empty:
        return df
    return df.sort_values("time").tail(int(lookback)).reset_index(drop=True)


def dataframe_to_candles(df: pd.DataFrame) -> list[dict]:
    """Convertit le DataFrame warmup en liste de bougies compatible Blackboard."""
    candles: list[dict] = []
    for row in df.to_dict("records"):
        time_value = row.get("time")
        if hasattr(time_value, "to_pydatetime"):
            time_value = time_value.to_pydatetime()
        candles.append(
            {
                "time": time_value,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "tick_volume": float(row.get("tick_volume", 0.0) or 0.0),
                "real_volume": float(row.get("real_volume", 0.0) or 0.0),
                "spread": float(row.get("spread", 0.0) or 0.0),
            }
        )
    return candles

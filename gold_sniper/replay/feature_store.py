"""P4.2 — FeatureStore with no-lookahead guarantees.

Incremental feature computation driven by M1 candle ingestion.
Every feature carries an `available_at` timestamp — the latest bar close-time
used in its computation.  The no-lookahead guard ensures no access at time `t`
ever sees a feature derived from bars with `close_time > t`.

Cache invalidation: per-timeframe.  When a new higher-TF bar closes,
features derived from that TF are recomputed; the old cache entry is purged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from gold_sniper.replay.multi_timeframe_builder import MultiTimeframeBuilder
from gold_sniper.replay.no_lookahead_guard import LookaheadError, assert_available


# ── Feature dataclass ──────────────────────────────────────────────────

@dataclass(frozen=True)
class Feature:
    """An immutable feature snapshot with a timestamp bounding its data source.

    ``available_at`` is the close-time of the most recent bar used in the
    computation.  It must be ≤ the current candle time for any access.
    """
    value: Any
    available_at: datetime


# ── Feature names (public constants) ───────────────────────────────────

FEATURE_HTF_CONTEXT = "htf_context"
FEATURE_POI_STACK = "poi_stack"
FEATURE_LIQUIDITY = "liquidity"
FEATURE_OTE = "ote"
FEATURE_SESSION = "session"
FEATURE_MICRO = "micro"
FEATURE_NEWS_STATE = "news_state"

ALL_FEATURE_KEYS = [
    FEATURE_HTF_CONTEXT,
    FEATURE_POI_STACK,
    FEATURE_LIQUIDITY,
    FEATURE_OTE,
    FEATURE_SESSION,
    FEATURE_MICRO,
    FEATURE_NEWS_STATE,
]


# ── Helper ─────────────────────────────────────────────────────────────

def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# ── FeatureStore ───────────────────────────────────────────────────────

@dataclass
class FeatureStore:
    """Incremental, no-lookahead feature cache.

    Usage::

        fs = FeatureStore(mtf)
        for candle in m1_stream:
            fs.update(candle)
            htf = fs.get(FEATURE_HTF_CONTEXT, t=candle["time"])
            # htf is a Feature whose available_at ≤ candle["time"]
    """

    mtf: MultiTimeframeBuilder = field(default_factory=MultiTimeframeBuilder)
    _cache: dict[str, Feature] = field(default_factory=dict)
    _candle_count: int = 0
    _last_candle_time: datetime | None = None

    # ── update ───────────────────────────────────────────────────────

    def update(self, candle: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """Ingest one M1 candle. Returns newly closed higher-TF bars.

        Called on every M1.  Cheap — session and micro recompute every M1;
        other features only recompute when a higher-TF bar closes.
        """
        self._candle_count += 1
        t = _as_utc(candle["time"])
        self._last_candle_time = t

        emitted = self.mtf.update(candle)

        # Session and micro are always fresh (cheap, non-MTF-dependent)
        self._cache[FEATURE_SESSION] = self._compute_session(t)
        self._cache[FEATURE_MICRO] = self._compute_micro(t)

        # Bootstrap: on first candle, also compute other features
        if self._candle_count == 1:
            self._cache[FEATURE_HTF_CONTEXT] = self._compute_htf_context(t)
            self._cache[FEATURE_POI_STACK] = self._compute_poi_stack(t)
            self._cache[FEATURE_LIQUIDITY] = self._compute_liquidity(t)
            self._cache[FEATURE_OTE] = self._compute_ote(t)

        # Recompute features for every timeframe that just closed a bar
        for tf, bars in emitted.items():
            if bars:
                self._invalidate_and_recompute(tf, t)

        return emitted

    def _invalidate_and_recompute(self, tf: str, t: datetime) -> None:
        """Purge cached features derived from *tf* and recompute them."""
        # Invalidate any feature whose available_at was tied to this TF
        # (simple approach: recompute all derived features)
        closed_bars = self.mtf.closed(tf)
        if not closed_bars:
            return

        # Recompute each feature family
        self._cache[FEATURE_HTF_CONTEXT] = self._compute_htf_context(t)
        self._cache[FEATURE_POI_STACK] = self._compute_poi_stack(t)
        self._cache[FEATURE_LIQUIDITY] = self._compute_liquidity(t)
        self._cache[FEATURE_OTE] = self._compute_ote(t)
        self._cache[FEATURE_SESSION] = self._compute_session(t)
        # micro is updated every M1 (not just on TF close)
        self._cache[FEATURE_MICRO] = self._compute_micro(t)

    # ── get (guarded) ────────────────────────────────────────────────

    def get(self, key: str, t: datetime | None = None) -> Feature | None:
        """Return the cached feature for *key*, or None.

        If *t* is provided, the no-lookahead guard is applied: a
        `LookaheadError` is raised if the feature's `available_at > t`.
        """
        feat = self._cache.get(key)
        if feat is None:
            return None
        if t is not None:
            assert_available(feat, t)
        return feat

    # ── typed getters ────────────────────────────────────────────────

    def htf_context(self, t: datetime | None = None) -> Feature | None:
        return self.get(FEATURE_HTF_CONTEXT, t)

    def poi_stack(self, t: datetime | None = None) -> Feature | None:
        return self.get(FEATURE_POI_STACK, t)

    def liquidity(self, t: datetime | None = None) -> Feature | None:
        return self.get(FEATURE_LIQUIDITY, t)

    def ote(self, t: datetime | None = None) -> Feature | None:
        return self.get(FEATURE_OTE, t)

    def session(self, t: datetime | None = None) -> Feature | None:
        return self.get(FEATURE_SESSION, t)

    def micro(self, t: datetime | None = None) -> Feature | None:
        return self.get(FEATURE_MICRO, t)

    def news_state(self, t: datetime | None = None) -> Feature | None:
        return self.get(FEATURE_NEWS_STATE, t)

    # ── compute helpers (no-lookahead safe) ──────────────────────────

    def _compute_htf_context(self, t: datetime) -> Feature:
        """HTF bias/trend from 4H + 15m closed bars."""
        bars_4h = self.mtf.closed("4H")
        bars_15m = self.mtf.closed("15m")

        available_at = t
        # Use the close time of the most recent contributing bar
        if bars_4h:
            available_at = max(available_at, _as_utc(bars_4h[-1]["time"]))
        if bars_15m:
            available_at = max(available_at, _as_utc(bars_15m[-1]["time"]))

        return Feature(
            value={
                "bars_4h_count": len(bars_4h),
                "bars_15m_count": len(bars_15m),
                "last_4h_close": float(bars_4h[-1]["close"]) if bars_4h else None,
                "last_15m_close": float(bars_15m[-1]["close"]) if bars_15m else None,
                "ready": len(bars_4h) >= 2 and len(bars_15m) >= 10,
            },
            available_at=available_at,
        )

    def _compute_poi_stack(self, t: datetime) -> Feature:
        """POI zones from 15m order blocks / FVGs."""
        bars_15m = self.mtf.closed("15m")
        available_at = t
        if bars_15m:
            available_at = max(available_at, _as_utc(bars_15m[-1]["time"]))

        # POI data is populated by agents during candidate evaluation;
        # here we provide the bar context that agents need.
        return Feature(
            value={
                "bars_15m_count": len(bars_15m),
                "last_15m_high": float(bars_15m[-1]["high"]) if bars_15m else None,
                "last_15m_low": float(bars_15m[-1]["low"]) if bars_15m else None,
                "last_15m_close": float(bars_15m[-1]["close"]) if bars_15m else None,
                "poi_present": len(bars_15m) >= 10,
            },
            available_at=available_at,
        )

    def _compute_liquidity(self, t: datetime) -> Feature:
        """Liquidity pools from 15m swing highs/lows."""
        bars_15m = self.mtf.closed("15m")
        available_at = t
        if bars_15m:
            available_at = max(available_at, _as_utc(bars_15m[-1]["time"]))

        return Feature(
            value={
                "bars_15m_count": len(bars_15m),
                "ready": len(bars_15m) >= 10,
            },
            available_at=available_at,
        )

    def _compute_ote(self, t: datetime) -> Feature:
        """OTE / Fibonacci context."""
        bars_15m = self.mtf.closed("15m")
        available_at = t
        if bars_15m:
            available_at = max(available_at, _as_utc(bars_15m[-1]["time"]))

        return Feature(
            value={
                "ready": len(bars_15m) >= 10,
            },
            available_at=available_at,
        )

    def _compute_session(self, t: datetime) -> Feature:
        """Session label derived from UTC hour."""
        hour = t.hour
        minute = t.minute
        # Killzone mapping (UTC)
        asia = 0 <= hour < 9
        london = 7 <= hour < 16
        ny_am = 12 <= hour < 17
        ny_pm = 17 <= hour < 22

        session_name = "ASIA"
        if 7 <= hour < 9:
            session_name = "LONDON_OPEN"
        elif 9 <= hour < 12:
            session_name = "LONDON"
        elif 12 <= hour < 14:
            session_name = "NY_AM"
        elif 14 <= hour < 16:
            session_name = "LONDON_NY_OVERLAP"
        elif 16 <= hour < 22:
            session_name = "NY_PM"
        elif hour >= 22:
            session_name = "ASIA_LATE"

        # Non-tradable sessions
        tradable = session_name not in {"ASIA", "ASIA_LATE"}
        # Friday afternoon block
        friday_block = t.weekday() == 4 and hour >= 20

        return Feature(
            value={
                "session_name": session_name,
                "hour": hour,
                "minute": minute,
                "weekday": t.weekday(),
                "tradable": tradable and not friday_block,
                "friday_block": friday_block,
                "is_killzone": session_name in {"LONDON_OPEN", "NY_AM", "LONDON_NY_OVERLAP"},
            },
            available_at=t,
        )

    def _compute_micro(self, t: datetime) -> Feature:
        """Micro context — always recomputed on M1."""
        bars_1m = getattr(self.mtf, "_buffers", {}).get("1m", [])
        return Feature(
            value={
                "m1_bars_count": len(bars_1m) if bars_1m else 0,
                "ready": bool(bars_1m and len(bars_1m) >= 5),
            },
            available_at=t,
        )

    # ── news ──────────────────────────────────────────────────────────

    def set_news_events(self, events: list[dict[str, Any]]) -> None:
        """Pre-load news events for agent_6 lookups."""
        self._cache[FEATURE_NEWS_STATE] = Feature(
            value={"events": events, "count": len(events)},
            available_at=_as_utc("2020-01-01T00:00:00Z"),  # static data
        )

    # ── diagnostics ───────────────────────────────────────────────────

    @property
    def candle_count(self) -> int:
        return self._candle_count

    def cache_keys(self) -> list[str]:
        return sorted(self._cache.keys())

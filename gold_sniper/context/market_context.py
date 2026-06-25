import sys
from typing import TypedDict, List

# Compatibilité pour Literal (Python 3.8+)
if sys.version_info >= (3, 8):
    from typing import Literal
else:
    from typing_extensions import Literal

PrimaryRegime = Literal[
    "STRONG_UP",
    "STRONG_DOWN",
    "RANGE",
    "REVERSAL",
    "UNKNOWN"
]

DeliveryPhase = Literal[
    "ACCUMULATION",
    "EXPANSION",
    "RETRACEMENT",
    "DISTRIBUTION",
    "UNKNOWN"
]

Overlay = Literal[
    "NEWS_HIGH_VOL",
    "LOW_LIQUIDITY_SESSION",
    "LONDON_KZ",
    "NEWYORK_KZ",
    "ASIAN_RANGE_BUILD",
    "MACRO_WINDOW"
]

SetupType = Literal[
    "SNIPER_PULLBACK",
    "TREND_CONTINUATION",
    "REVERSAL",
    "OBSERVATION",
    "UNKNOWN"
]

DecisionMode = Literal[
    "REJECT",
    "WAIT",
    "CANDIDATE_MICRO",
    "STANDARD_PAPER",
    "PREMIUM_PAPER",
    "STRICT_EXECUTE_LATER"
]

class DealingRangeReference(TypedDict):
    high: float
    low: float
    equilibrium: float
    price_position: Literal["PREMIUM", "DISCOUNT", "EQUILIBRIUM", "UNKNOWN"]

class LiquidityState(TypedDict):
    external_target_above: bool
    external_target_below: bool
    latest_event: Literal["NONE", "SWEEP", "PURGE_REVERT", "RUN", "BREAKOUT_ACCEPTANCE"]

class SessionState(TypedDict):
    tradable_window: bool
    session_label: str

class NewsState(TypedDict):
    lockout: bool
    normalized_after_event: bool

class MarketContext(TypedDict):
    symbol: str
    timestamp: str
    primary_regime: PrimaryRegime
    delivery_phase: DeliveryPhase
    overlays: List[Overlay]
    htf_bias: Literal["LONG", "SHORT", "NEUTRAL"]
    htf_draw_on_liquidity: Literal["UP", "DOWN", "TWO_WAY", "UNCLEAR"]
    institutional_order_flow: Literal["BULLISH", "BEARISH", "MIXED"]
    dealing_range_reference: DealingRangeReference
    active_poi_stack: List[str]
    liquidity_state: LiquidityState
    session_state: SessionState
    news_state: NewsState
    setup_hint: SetupType
    uncertainty_flags: List[str]

def build_default_market_context(symbol: str = "XAUUSD", timestamp: str | None = None) -> MarketContext:
    """Retourne un contexte de marché neutre/par défaut."""
    return {
        "symbol": symbol,
        "timestamp": timestamp or "",
        "primary_regime": "UNKNOWN",
        "delivery_phase": "UNKNOWN",
        "overlays": [],
        "htf_bias": "NEUTRAL",
        "htf_draw_on_liquidity": "UNCLEAR",
        "institutional_order_flow": "MIXED",
        "dealing_range_reference": {
            "high": 0.0,
            "low": 0.0,
            "equilibrium": 0.0,
            "price_position": "UNKNOWN"
        },
        "active_poi_stack": [],
        "liquidity_state": {
            "external_target_above": False,
            "external_target_below": False,
            "latest_event": "NONE"
        },
        "session_state": {
            "tradable_window": False,
            "session_label": "UNKNOWN"
        },
        "news_state": {
            "lockout": False,
            "normalized_after_event": False
        },
        "setup_hint": "OBSERVATION",
        "uncertainty_flags": ["CONTEXT_ENGINE_SKELETON_ONLY"]
    }

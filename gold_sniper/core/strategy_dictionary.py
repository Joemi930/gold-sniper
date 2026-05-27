from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Strategy:
    name: str
    description: str
    sessions: list[str]
    regimes: list[str]
    min_score: float
    exceptional_score: float
    risk_pct: float
    sl_atr_multiplier: float
    tp1_rr: float
    tp2_rr: float
    tp3_enabled: bool
    require_sweep: bool
    require_fvg_in_ob: bool
    require_idm_swept: bool
    min_ob_score: float
    weight_overrides: Optional[dict[str, float]]
    priority: int


STRATEGY_DICTIONARY: list[Strategy] = [
    Strategy(
        name="LONDON_OPEN_TRENDING",
        description="Continuation de tendance sur l'ouverture de Londres",
        sessions=["LONDON"],
        regimes=["TRENDING"],
        min_score=82.0,
        exceptional_score=90.0,
        risk_pct=1.0,
        sl_atr_multiplier=0.8,
        tp1_rr=1.5,
        tp2_rr=2.5,
        tp3_enabled=True,
        require_sweep=True,
        require_fvg_in_ob=False,
        require_idm_swept=False,
        min_ob_score=55.0,
        weight_overrides={"agent_1": 35, "agent_3": 25, "agent_4": 10},
        priority=1,
    ),
    Strategy(
        name="LONDON_OPEN_SWEEP_REVERSAL",
        description="Retournement post-sweep de liquidite en ouverture Londres",
        sessions=["LONDON"],
        regimes=["TRENDING", "RANGING"],
        min_score=84.0,
        exceptional_score=92.0,
        risk_pct=1.0,
        sl_atr_multiplier=0.6,
        tp1_rr=2.0,
        tp2_rr=3.5,
        tp3_enabled=True,
        require_sweep=True,
        require_fvg_in_ob=True,
        require_idm_swept=True,
        min_ob_score=65.0,
        weight_overrides={"agent_3": 30, "agent_5": 15, "agent_4": 12},
        priority=2,
    ),
    Strategy(
        name="NY_OPEN_CONTINUATION",
        description="Continuation du move Londres en ouverture New York",
        sessions=["NY_OPEN"],
        regimes=["TRENDING"],
        min_score=80.0,
        exceptional_score=88.0,
        risk_pct=1.0,
        sl_atr_multiplier=1.0,
        tp1_rr=1.5,
        tp2_rr=2.0,
        tp3_enabled=False,
        require_sweep=False,
        require_fvg_in_ob=True,
        require_idm_swept=False,
        min_ob_score=50.0,
        weight_overrides={"agent_1": 32, "agent_2": 28},
        priority=3,
    ),
    Strategy(
        name="OVERLAP_REVERSAL",
        description="Retournement institutionnel en chevauchement London-NY",
        sessions=["OVERLAP"],
        regimes=["TRENDING", "HIGH_VOLATILITY"],
        min_score=88.0,
        exceptional_score=94.0,
        risk_pct=0.8,
        sl_atr_multiplier=1.2,
        tp1_rr=1.8,
        tp2_rr=3.0,
        tp3_enabled=True,
        require_sweep=True,
        require_fvg_in_ob=True,
        require_idm_swept=True,
        min_ob_score=70.0,
        weight_overrides={"agent_5": 15, "agent_3": 25, "agent_6": 1},
        priority=4,
    ),
    Strategy(
        name="RANGING_BOUNDARY",
        description="Retournement aux bornes d'un range etabli",
        sessions=["LONDON", "NY_OPEN", "OVERLAP"],
        regimes=["RANGING"],
        min_score=90.0,
        exceptional_score=95.0,
        risk_pct=0.75,
        sl_atr_multiplier=0.7,
        tp1_rr=1.0,
        tp2_rr=1.8,
        tp3_enabled=False,
        require_sweep=True,
        require_fvg_in_ob=False,
        require_idm_swept=False,
        min_ob_score=60.0,
        weight_overrides={"agent_2": 30, "agent_3": 25, "agent_4": 10},
        priority=5,
    ),
    Strategy(
        name="ACCUMULATION_BREAKOUT",
        description="Cassure d'une zone d'accumulation avec volume institutionnel",
        sessions=["LONDON", "NY_OPEN"],
        regimes=["ACCUMULATION"],
        min_score=86.0,
        exceptional_score=92.0,
        risk_pct=1.0,
        sl_atr_multiplier=0.9,
        tp1_rr=2.0,
        tp2_rr=3.5,
        tp3_enabled=True,
        require_sweep=True,
        require_fvg_in_ob=True,
        require_idm_swept=True,
        min_ob_score=65.0,
        weight_overrides={"agent_3": 28, "agent_5": 15},
        priority=6,
    ),
    Strategy(
        name="POST_NEWS_CONTINUATION",
        description="Continuation 15-120min apres news HIGH IMPACT alignee avec biais HTF",
        sessions=["LONDON", "NY_OPEN", "OVERLAP"],
        regimes=["TRENDING", "HIGH_VOLATILITY"],
        min_score=85.0,
        exceptional_score=92.0,
        risk_pct=0.5,
        sl_atr_multiplier=1.5,
        tp1_rr=1.5,
        tp2_rr=2.5,
        tp3_enabled=False,
        require_sweep=False,
        require_fvg_in_ob=True,
        require_idm_swept=False,
        min_ob_score=55.0,
        weight_overrides={"agent_6": 1, "agent_1": 35},
        priority=7,
    ),
    # ─────────────────────────────────────────────────────────────────────────
    # DIAMOND_SETUP — DÉSACTIVÉ du dictionnaire de stratégies (correction audit)
    # ─────────────────────────────────────────────────────────────────────────
    # Le Diamond Setup est une ALERTE MANUELLE uniquement (core/diamond_detector.py).
    # Il ne doit jamais déclencher un trade automatique via l'orchestrateur.
    # L'entrée ci-dessous est commentée pour garantir que select_active_strategy()
    # ne peut pas retourner DIAMOND_SETUP — la détection reste dans diamond_detector.py.
    #
    # Strategy(
    #     name="DIAMOND_SETUP",
    #     description="Setup 5 etoiles diamant - confluence maximale - 3eme trade autorise",
    #     sessions=["LONDON", "NY_OPEN", "OVERLAP"],
    #     regimes=["TRENDING", "ACCUMULATION"],
    #     min_score=92.0,
    #     exceptional_score=97.0,
    #     risk_pct=1.5,
    #     sl_atr_multiplier=0.7,
    #     tp1_rr=2.0,
    #     tp2_rr=4.0,
    #     tp3_enabled=True,
    #     require_sweep=True,
    #     require_fvg_in_ob=True,
    #     require_idm_swept=True,
    #     min_ob_score=75.0,
    #     weight_overrides=None,
    #     priority=0,
    # ),

    Strategy(
        name="DEFAULT_CONSERVATIVE",
        description="Strategie par defaut hors contexte identifie - tres conservatrice",
        sessions=["LONDON", "NY_OPEN"],
        regimes=["TRENDING", "RANGING", "ACCUMULATION"],
        min_score=90.0,
        exceptional_score=95.0,
        risk_pct=0.5,
        sl_atr_multiplier=1.2,
        tp1_rr=1.5,
        tp2_rr=2.0,
        tp3_enabled=False,
        require_sweep=True,
        require_fvg_in_ob=False,
        require_idm_swept=False,
        min_ob_score=60.0,
        weight_overrides=None,
        priority=99,
    ),
]


def normalize_session(session: str | None) -> str:
    normalized = (session or "UNKNOWN").upper()
    if normalized == "LONDON_OPEN":
        return "LONDON"
    if normalized in {"NONE", ""}:
        return "UNKNOWN"
    return normalized


def normalize_regime(regime: str | None) -> str:
    normalized = (regime or "UNKNOWN").upper()
    if normalized in {"NONE", ""}:
        return "UNKNOWN"
    return normalized


def select_active_strategy(
    session: str,
    regime: str,
    post_news: bool = False,
    diamond_conditions_met: bool = False,
) -> Strategy:
    session = normalize_session(session)
    regime = normalize_regime(regime)

    # DIAMOND_SETUP est retiré du dictionnaire — alerte manuelle uniquement via diamond_detector.py.
    # Le bloc ci-dessous est conservé pour mémoire mais ne peut jamais s'activer.
    # if diamond_conditions_met:
    #     diamond = _by_name("DIAMOND_SETUP")
    #     if session in diamond.sessions and regime in diamond.regimes:
    #         return diamond

    if regime == "RANGING":
        ranging = _by_name("RANGING_BOUNDARY")
        if session in ranging.sessions:
            return ranging

    if post_news:
        post_news_strategy = _by_name("POST_NEWS_CONTINUATION")
        if session in post_news_strategy.sessions and regime in post_news_strategy.regimes:
            return post_news_strategy

    matching = [
        strategy for strategy in STRATEGY_DICTIONARY
        if strategy.name not in {"DIAMOND_SETUP", "POST_NEWS_CONTINUATION"}
        and session in strategy.sessions
        and regime in strategy.regimes
    ]
    if not matching:
        return _by_name("DEFAULT_CONSERVATIVE")

    return min(matching, key=lambda strategy: strategy.priority)


def check_diamond_conditions(agent_data: dict) -> bool:
    a2 = _as_dict(agent_data.get("agent_2"))
    a3 = _as_dict(agent_data.get("agent_3"))
    a4 = _as_dict(agent_data.get("agent_4"))

    ob_and_fvg = bool(
        _pick(a2, "active_ob", "ob_active", "has_ob")
        and _pick(a2, "active_fvg", "fvg_active", "has_fvg")
    )
    ob_score = float(_pick(a2, "ob_score", "poi_score", default=0.0) or 0.0)
    precision = float(_pick(a4, "precision_pct", "sweet_spot_precision", default=0.0) or 0.0)
    asian_range = _pick(a3, "asian_range", default={}) or {}
    asian_swept = bool(
        _pick(a3, "asian_swept", "sweep_confirmed", default=False)
        or asian_range.get("swept_side") is not None
    )

    return ob_and_fvg and ob_score >= 75.0 and precision >= 0.80 and asian_swept


def _by_name(name: str) -> Strategy:
    return next(strategy for strategy in STRATEGY_DICTIONARY if strategy.name == name)


def _as_dict(value: object) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    payload = getattr(value, "payload", {}) or {}
    data = dict(payload)
    for key in ("score", "direction", "hard_filter_pass", "reason"):
        if hasattr(value, key):
            data[key] = getattr(value, key)
    return data


def _pick(data: dict, *keys: str, default: object = None) -> object:
    for key in keys:
        if key in data:
            return data[key]
    return default

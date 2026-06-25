from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class DecisionAction(_StrEnum):
    ENTER_FULL = "ENTER_FULL"
    ENTER_REDUCED = "ENTER_REDUCED"
    WAIT_FOR_TRIGGER = "WAIT_FOR_TRIGGER"
    WAIT_FOR_BETTER_PRICE = "WAIT_FOR_BETTER_PRICE"
    WATCH_ONLY = "WATCH_ONLY"
    REJECT = "REJECT"


class ReadinessState(_StrEnum):
    INVALID = "INVALID"
    UNAVAILABLE = "UNAVAILABLE"
    WAITING_POI = "WAITING_POI"
    WAITING_TRIGGER = "WAITING_TRIGGER"
    WATCH_ONLY = "WATCH_ONLY"
    READY = "READY"
    REJECT = "REJECT"


class SetupGrade(_StrEnum):
    A_PLUS = "A_PLUS"
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class SetupType(_StrEnum):
    # ── P2-E Phase 7A taxonomy (Opus 5) ──────────────────────────
    REVERSAL_STRICT = "REVERSAL_STRICT"
    REVERSAL_LIGHT = "REVERSAL_LIGHT"
    CONTINUATION_STRICT = "CONTINUATION_STRICT"
    CONTINUATION_LIGHT = "CONTINUATION_LIGHT"
    SWEEP_REVERSAL = "SWEEP_REVERSAL"
    OTE_PULLBACK = "OTE_PULLBACK"
    POI_REACTION = "POI_REACTION"
    NO_SETUP = "NO_SETUP"

    # ── Legacy aliases (preserved for backward compatibility) ────
    FAILED_AUCTION_RECLAIM = "FAILED_AUCTION_RECLAIM"
    SESSION_REVERSAL_MEDIUM = "SESSION_REVERSAL_MEDIUM"

    UNKNOWN = "UNKNOWN"


class TradeSide(_StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    NONE = "NONE"


class EvidenceSource(_StrEnum):
    AGENT = "AGENT"
    REPLAY = "REPLAY"
    NEWS = "NEWS"
    SESSION = "SESSION"
    RISK = "RISK"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    POI = "POI"
    LIQUIDITY = "LIQUIDITY"
    MICRO_CONFIRMATION = "MICRO_CONFIRMATION"
    TIMING = "TIMING"


class VetoSeverity(_StrEnum):
    INFO = "INFO"
    SOFT = "SOFT"
    HARD = "HARD"
    REPLAY_INVALID = "REPLAY_INVALID"


class ExecutionMode(_StrEnum):
    SHADOW_ONLY = "SHADOW_ONLY"


class BlockedStage(_StrEnum):
    NONE = "NONE"
    DATA = "DATA"
    NEWS = "NEWS"
    SESSION = "SESSION"
    HTF_CONTEXT = "HTF_CONTEXT"
    LIQUIDITY = "LIQUIDITY"
    POI = "POI"
    MICRO = "MICRO"
    RISK = "RISK"
    ENGINE = "ENGINE"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_enum(enum_cls, value: Any, default):
    try:
        return enum_cls(value)
    except (TypeError, ValueError):
        return default


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _jsonify(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {k: _jsonify(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonify(v) for v in value]
    return value


@dataclass(frozen=True)
class AgentObservation:
    agent_id: str
    source: EvidenceSource = EvidenceSource.AGENT
    passed: bool | None = None
    score: float = 0.0
    confidence: float = 0.0
    reason: str = "UNKNOWN"
    hard_filter_pass: bool | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    missing_evidence: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)


@dataclass(frozen=True)
class EvidenceBundle:
    symbol: str = "XAUUSD"
    ts_utc: str | None = None
    setup_type: SetupType = SetupType.UNKNOWN
    side: TradeSide = TradeSide.NONE
    observations: dict[str, AgentObservation] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    poi: dict[str, Any] = field(default_factory=dict)
    liquidity: dict[str, Any] = field(default_factory=dict)
    micro: dict[str, Any] = field(default_factory=dict)
    news: dict[str, Any] = field(default_factory=dict)
    session: dict[str, Any] = field(default_factory=dict)
    risk: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvidenceBundle":
        if not isinstance(data, dict):
            return cls()
        observations = {}
        raw_obs = data.get("observations") or {}
        if isinstance(raw_obs, dict):
            for key, value in raw_obs.items():
                if isinstance(value, AgentObservation):
                    observations[str(key)] = value
                elif isinstance(value, dict):
                    observations[str(key)] = AgentObservation(
                        agent_id=str(value.get("agent_id") or key),
                        source=_safe_enum(EvidenceSource, value.get("source"), EvidenceSource.AGENT),
                        passed=value.get("passed"),
                        score=_safe_float(value.get("score"), 0.0),
                        confidence=_safe_float(value.get("confidence"), 0.0),
                        reason=str(value.get("reason") or "UNKNOWN"),
                        hard_filter_pass=value.get("hard_filter_pass"),
                        payload=dict(value.get("payload") or {}),
                        missing_evidence=list(value.get("missing_evidence") or []),
                        warnings=list(value.get("warnings") or []),
                    )

        setup = data.get("setup_type") or data.get("setup") or SetupType.UNKNOWN.value
        side = data.get("side") or TradeSide.NONE.value

        return cls(
            symbol=str(data.get("symbol") or "XAUUSD"),
            ts_utc=data.get("ts_utc"),
            setup_type=_safe_enum(SetupType, setup, SetupType.UNKNOWN),
            side=_safe_enum(TradeSide, side, TradeSide.NONE),
            observations=observations,
            context=_safe_dict(data.get("context")),
            poi=_safe_dict(data.get("poi")),
            liquidity=_safe_dict(data.get("liquidity")),
            micro=_safe_dict(data.get("micro")),
            news=_safe_dict(data.get("news")),
            session=_safe_dict(data.get("session")),
            risk=_safe_dict(data.get("risk")),
            raw=_safe_dict(data.get("raw")),
        )


@dataclass(frozen=True)
class HardVetoResult:
    hard_veto: bool = False
    veto_code: str | None = None
    veto_reason: str | None = None
    blocked_stage: BlockedStage = BlockedStage.NONE
    severity: VetoSeverity = VetoSeverity.INFO
    replay_invalid: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    value: float
    weight: float
    reason: str = ""
    source: EvidenceSource = EvidenceSource.REPLAY

    def weighted(self) -> float:
        return max(0.0, min(float(self.value), 100.0)) * float(self.weight)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)


@dataclass(frozen=True)
class ScoreCard:
    components: list[ScoreComponent] = field(default_factory=list)
    score_before_veto: float = 0.0
    score_after_veto: float = 0.0
    grade: SetupGrade = SetupGrade.D
    missing_evidence: list[str] = field(default_factory=list)
    soft_issues: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)


@dataclass(frozen=True)
class ReadinessResult:
    state: ReadinessState = ReadinessState.UNAVAILABLE
    suggested_action: DecisionAction = DecisionAction.WATCH_ONLY
    reason: str = "READINESS_UNAVAILABLE"
    blocked_stage: BlockedStage = BlockedStage.NONE
    section_states: dict[str, str] = field(default_factory=dict)
    missing_evidence: list[str] = field(default_factory=list)
    soft_issues: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)


@dataclass(frozen=True)
class RiskPlan:
    capital: float = 100.0
    risk_pct: float = 0.0
    risk_amount: float = 0.0
    risk_multiplier: float = 0.0
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 4.0
    drawdown_guard_pct: float = 5.0
    allowed: bool = False
    reason: str = "NO_RISK_ALLOCATED"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)


@dataclass(frozen=True)
class DecisionResult:
    action: DecisionAction
    setup_grade: SetupGrade
    setup_type: SetupType = SetupType.UNKNOWN
    side: TradeSide = TradeSide.NONE
    confidence_score: float = 0.0
    score_before_veto: float = 0.0
    score_after_veto: float = 0.0
    hard_veto: bool = False
    hard_veto_reason: str | None = None
    blocked_stage: BlockedStage = BlockedStage.NONE
    risk_multiplier: float = 0.0
    required_execution_mode: ExecutionMode = ExecutionMode.SHADOW_ONLY
    missing_evidence: list[str] = field(default_factory=list)
    soft_issues: list[str] = field(default_factory=list)
    primary_reasons: list[str] = field(default_factory=list)
    closest_valid_setup: str | None = None
    replay_invalid: bool = False
    readiness_state: ReadinessState = ReadinessState.UNAVAILABLE
    readiness_reason: str = "READINESS_UNAVAILABLE"
    readiness_by_section: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)


@dataclass(frozen=True)
class ReplayDecisionRecord:
    evidence: EvidenceBundle
    hard_veto: HardVetoResult
    scorecard: ScoreCard
    decision: DecisionResult
    risk_plan: RiskPlan

    def to_dict(self) -> dict[str, Any]:
        return _jsonify(self)

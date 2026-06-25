"""Unified strategy shadow package.

Phase 1 exposes a pure, shadow-only evaluator. It is not wired to live trading.
P1-engine introduces the strategy decision authority: contracts, veto, scorecard,
risk allocation, and professional decision engine.
"""

from gold_sniper.strategy.contracts import (
    AgentObservation,
    BlockedStage,
    DecisionAction,
    DecisionResult,
    EvidenceBundle,
    EvidenceSource,
    ExecutionMode,
    HardVetoResult,
    ReplayDecisionRecord,
    RiskPlan,
    ScoreCard,
    ScoreComponent,
    SetupGrade,
    SetupType,
    TradeSide,
    VetoSeverity,
)
from gold_sniper.strategy.decision_explainer import DecisionExplanation, explain_professional_decision, explain_unified_decision
from gold_sniper.strategy.hard_veto_registry import evaluate_hard_veto
from gold_sniper.strategy.kasper_ict_scenario_engine import KasperIctScenario, evaluate_kasper_ict_scenarios
from gold_sniper.strategy.liquidity_state_machine import LiquidityStateResult, evaluate_liquidity_state
from gold_sniper.strategy.micro_confirmation_engine import MicroConfirmationResult, evaluate_micro_confirmation
from gold_sniper.strategy.professional_decision_engine import ProfessionalDecisionResult, evaluate_professional_decision
from gold_sniper.strategy.risk_allocator import allocate_risk
from gold_sniper.strategy.scorecard import evaluate_scorecard
from gold_sniper.strategy.session_premium_ote_gate import SessionPremiumOteResult, evaluate_session_premium_ote_gate
from gold_sniper.strategy.setup_taxonomy import get_setup_requirement, resolve_setup_type
from gold_sniper.strategy.unified_xauusd_strategy import UnifiedXauusdDecision, evaluate_unified_xauusd_strategy

__all__ = [
    # P1-engine contracts
    "AgentObservation",
    "BlockedStage",
    "DecisionAction",
    "DecisionResult",
    "EvidenceBundle",
    "EvidenceSource",
    "ExecutionMode",
    "HardVetoResult",
    "ReplayDecisionRecord",
    "RiskPlan",
    "ScoreCard",
    "ScoreComponent",
    "SetupGrade",
    "SetupType",
    "TradeSide",
    "VetoSeverity",
    # P1-engine modules
    "allocate_risk",
    "evaluate_hard_veto",
    "evaluate_scorecard",
    "get_setup_requirement",
    "resolve_setup_type",
    # Legacy shadow (preserved)
    "DecisionExplanation",
    "KasperIctScenario",
    "LiquidityStateResult",
    "MicroConfirmationResult",
    "ProfessionalDecisionResult",
    "SessionPremiumOteResult",
    "UnifiedXauusdDecision",
    "explain_professional_decision",
    "explain_unified_decision",
    "evaluate_kasper_ict_scenarios",
    "evaluate_liquidity_state",
    "evaluate_micro_confirmation",
    "evaluate_professional_decision",
    "evaluate_session_premium_ote_gate",
    "evaluate_unified_xauusd_strategy",
]

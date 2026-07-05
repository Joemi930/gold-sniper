"""P4.2 — CandidateWindowEvaluator.

Runs the heavy pipeline (agents → EvidenceBuilder → Kasper → PDE → risk)
ONLY inside candidate windows identified by CandidateDiscoveryEngine.

Does NOT change Kasper/PDE logic — only changes WHEN they are called.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gold_sniper.replay.candidate_discovery import CandidateWindow


# ── DecisionRecord ─────────────────────────────────────────────────────

@dataclass
class DecisionRecord:
    """The output of evaluating a CandidateWindow through the heavy pipeline."""

    window: CandidateWindow
    decision: str           # ENTER_FULL | ENTER_REDUCED | WAIT | REJECT
    setup_grade: str        # A_PLUS | A | B | C | D
    setup_type: str | None
    side: str | None        # BUY | SELL
    confidence_score: float
    hard_veto: bool
    veto_code: str | None
    risk_multiplier: float
    risk_allowed: bool
    reject_reason: str | None
    p1_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def is_enter(self) -> bool:
        return self.decision in {"ENTER_FULL", "ENTER_REDUCED"}

    @property
    def is_reject(self) -> bool:
        return self.decision == "REJECT"

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window.to_dict(),
            "decision": self.decision,
            "setup_grade": self.setup_grade,
            "setup_type": self.setup_type,
            "side": self.side,
            "confidence_score": self.confidence_score,
            "hard_veto": self.hard_veto,
            "veto_code": self.veto_code,
            "risk_multiplier": self.risk_multiplier,
            "risk_allowed": self.risk_allowed,
            "reject_reason": self.reject_reason,
        }


# ── Evaluator ──────────────────────────────────────────────────────────

@dataclass
class CandidateWindowEvaluator:
    """Evaluates a CandidateWindow through the full heavy pipeline.

    The pipeline is:
      agents → EvidenceBuilder → Kasper scenario → PDE → risk allocation

    This class is intentionally thin — it delegates to the existing
    strategy modules without modifying their logic.
    """

    decision_pipeline: Any = None  # ReplayDecisionPipeline instance
    risk_allocator: Any = None     # allocate_risk function or wrapper
    # Surfaced diagnostics: pipeline exceptions were silently swallowed
    # (p1_payload={}), turning every window into UNKNOWN/REJECT/D with no trace.
    eval_error_count: int = 0
    eval_errors: dict = field(default_factory=dict)
    first_error_trace: str = ""

    def evaluate(
        self,
        window: CandidateWindow,
        blackboard: Any,
        candle: Any = None,
    ) -> DecisionRecord:
        """Run the heavy pipeline for one candidate window.

        Args:
            window: The candidate window from CandidateDiscoveryEngine.
            blackboard: The replay blackboard with current market state.
            candle: The REAL current M1 candle (with real OHLC). Required for
                the agents to see real prices — without it the pipeline ran on
                a zero-price synthetic candle and produced UNKNOWN for every
                window.

        Returns:
            DecisionRecord with the final decision.
        """
        # Use the REAL candle when provided; only synthesize as a last resort.
        if candle is not None:
            candle = dict(candle)
            candle.setdefault("symbol", "XAUUSD")
        else:
            candle = {
                "time": window.start_t,
                "symbol": "XAUUSD",
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close": 0.0,
            }

            # Try to get current price from blackboard
            try:
                tick = blackboard.read_sync("market_data.current_tick")
                if tick:
                    candle["close"] = float(tick.get("bid", 0.0))
            except Exception:
                pass

        p1_payload: dict[str, Any] = {}

        if self.decision_pipeline is not None:
            # Run the full decision pipeline (agents → EvidenceBuilder → PDE → Kasper)
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            try:
                p1_payload = loop.run_until_complete(
                    self.decision_pipeline(candle, blackboard)
                )
            except Exception as exc:  # noqa: BLE001 — surface, don't hide
                self.eval_error_count += 1
                key = type(exc).__name__ + ": " + str(exc)[:120]
                self.eval_errors[key] = self.eval_errors.get(key, 0) + 1
                if not self.first_error_trace:
                    self.first_error_trace = traceback.format_exc()[-1500:]
                p1_payload = {}

        # Extract decision fields from the P1 payload
        decision = str(p1_payload.get("decision", "REJECT")).upper()
        setup_grade = str(p1_payload.get("setup_grade", "D")).upper()
        setup_type = p1_payload.get("setup_type")
        side = p1_payload.get("side")
        confidence = float(p1_payload.get("confidence_score", 0.0))
        hard_veto = bool(p1_payload.get("hard_veto", False))
        veto_code = p1_payload.get("veto_code")
        risk_multiplier = float(p1_payload.get("risk_multiplier", 0.0))
        risk_allowed = bool(p1_payload.get("risk_allowed", False))
        reject_reason = p1_payload.get("reject_reason")

        # ── Post-eval: check if setup_type is POI_REACTION → diagnostic skip ──
        if setup_type and str(setup_type).upper().replace(" ", "_") == "POI_REACTION":
            # POI_REACTION is diagnostic — force REJECT regardless of pipeline output
            decision = "REJECT"
            if not reject_reason:
                reject_reason = "POI_REACTION_DIAGNOSTIC_NOT_TRADABLE"

        return DecisionRecord(
            window=window,
            decision=decision,
            setup_grade=setup_grade,
            setup_type=setup_type,
            side=side,
            confidence_score=confidence,
            hard_veto=hard_veto,
            veto_code=veto_code,
            risk_multiplier=risk_multiplier,
            risk_allowed=risk_allowed,
            reject_reason=reject_reason,
            p1_payload=p1_payload,
        )

    # ── shortcut for direct evaluation (no decision_pipeline) ─────────
    def evaluate_from_payload(
        self,
        window: CandidateWindow,
        p1_payload: dict[str, Any],
    ) -> DecisionRecord:
        """Create a DecisionRecord from an already-computed P1 payload."""
        decision = str(p1_payload.get("decision", "REJECT")).upper()
        setup_type = p1_payload.get("setup_type")
        reject_reason = p1_payload.get("reject_reason")

        if setup_type and str(setup_type).upper().replace(" ", "_") == "POI_REACTION":
            decision = "REJECT"
            if not reject_reason:
                reject_reason = "POI_REACTION_DIAGNOSTIC_NOT_TRADABLE"

        return DecisionRecord(
            window=window,
            decision=decision,
            setup_grade=str(p1_payload.get("setup_grade", "D")).upper(),
            setup_type=setup_type,
            side=p1_payload.get("side"),
            confidence_score=float(p1_payload.get("confidence_score", 0.0)),
            hard_veto=bool(p1_payload.get("hard_veto", False)),
            veto_code=p1_payload.get("veto_code"),
            risk_multiplier=float(p1_payload.get("risk_multiplier", 0.0)),
            risk_allowed=bool(p1_payload.get("risk_allowed", False)),
            reject_reason=reject_reason,
            p1_payload=p1_payload,
        )

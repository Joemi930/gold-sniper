"""P4.2 — ReplayEngineV2 orchestrator.

Candidate-driven replay loop that replaces the legacy full-scan approach.

Flow per M1 candle:
  candle_t → FeatureStore.update(candle_t)           # cheap, incremental
            → TradeLifecycleSimulator.on_candle()     # manage open trades
            → window = CandidateDiscovery.scan(fs, t) # cheap gates
            → if window: rec = Evaluator.evaluate()   # heavy pipeline, RARE
            → MetricsAggregator.record(...)           # incremental
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gold_sniper.replay.candidate_discovery import CandidateDiscoveryEngine
from gold_sniper.replay.candidate_window import CandidateWindowEvaluator, DecisionRecord
from gold_sniper.replay.feature_store import FeatureStore
from gold_sniper.replay.metrics_aggregator import MetricsAggregator
from gold_sniper.replay.multi_timeframe_builder import MultiTimeframeBuilder
from gold_sniper.replay.profiler_v2 import ProfilerV2
from gold_sniper.replay.trade_lifecycle_simulator import TradeLifecycleSimulator


def _ensure_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@dataclass
class ReplayEngineV2:
    """Candidate-driven replay engine.

    Usage::

        engine = ReplayEngineV2(
            candles_1m=m1_list,
            decision_pipeline=pipeline,
            trade_manager=trade_mgr,
            eval_start="2026-01-01T00:00:00Z",
            eval_end="2026-01-08T00:00:00Z",
        )
        summary = engine.run()
    """

    candles_1m: list[dict[str, Any]] = field(default_factory=list)
    decision_pipeline: Any = None       # ReplayDecisionPipeline
    trade_manager: Any = None           # SimulatedTradeManager
    eval_start: str | datetime | None = None
    eval_end: str | datetime | None = None
    initial_equity: float = 100.0
    run_id: str = "v2_run"
    output_root: Path = Path("data/replay_runs")

    # ── internal state ─────────────────────────────────────────────────
    _mtf: MultiTimeframeBuilder = field(default_factory=MultiTimeframeBuilder)
    _fs: FeatureStore | None = None
    _discovery: CandidateDiscoveryEngine | None = None
    _evaluator: CandidateWindowEvaluator | None = None
    _lifecycle: TradeLifecycleSimulator | None = None
    _metrics: MetricsAggregator | None = None
    _profiler: ProfilerV2 | None = None
    _blackboard: Any = None

    def __post_init__(self) -> None:
        if isinstance(self.eval_start, str):
            self.eval_start = _ensure_utc(self.eval_start)
        if isinstance(self.eval_end, str):
            self.eval_end = _ensure_utc(self.eval_end)

    def run(self, blackboard: Any = None, profile: bool = False) -> dict[str, Any]:
        """Execute the V2 replay loop. Returns compact summary dict."""
        self._blackboard = blackboard
        self._fs = FeatureStore(mtf=self._mtf)
        self._discovery = CandidateDiscoveryEngine()
        self._evaluator = CandidateWindowEvaluator(
            decision_pipeline=self.decision_pipeline,
        )
        self._lifecycle = TradeLifecycleSimulator(
            trade_manager=self.trade_manager,
        )
        self._metrics = MetricsAggregator()
        if profile:
            self._profiler = ProfilerV2()
            self._profiler.start()

        t0 = time.perf_counter()

        for candle in self.candles_1m:
            t = _ensure_utc(candle["time"])
            eval_active = self._is_eval(t)

            # ── Profiler tick ──────────────────────────────────────
            if self._profiler:
                self._profiler.tick_candle(eval_active)

            # ── Feature update (cheap) ──────────────────────────────
            if self._profiler:
                with self._profiler.section("feature_update"):
                    self._fs.update(candle)
            else:
                self._fs.update(candle)

            # ── Lifecycle scan (manages open trades) ────────────────
            if self._profiler:
                with self._profiler.section("trade_lifecycle"):
                    lifecycle_events = self._lifecycle.on_candle(candle)
            else:
                lifecycle_events = self._lifecycle.on_candle(candle)

            for evt in lifecycle_events:
                self._metrics.record_trade_close(
                    pnl_r=evt.pnl_r or 0.0,
                    close_reason=evt.reason or "",
                )

            # ── Warmup gate ─────────────────────────────────────────
            if not eval_active:
                self._metrics.record_candle(eval_active=False)
                continue

            self._metrics.record_candle(eval_active=True)

            # ── Candidate scan (cheap gates) ────────────────────────
            current_price = float(candle.get("close", 0.0))
            if self._profiler:
                with self._profiler.section("candidate_scan"):
                    window = self._discovery.scan(self._fs, t, current_price)
            else:
                window = self._discovery.scan(self._fs, t, current_price)

            if window is None:
                continue

            self._metrics.record_candidate()
            self._metrics.record_window()

            # ── Heavy pipeline (only in candidate windows) ──────────
            if self._profiler:
                with self._profiler.section("agents"):
                    rec = self._evaluator.evaluate(window, blackboard)
            else:
                rec = self._evaluator.evaluate(window, blackboard)

            # ── Post-eval filtering ─────────────────────────────────
            setup_type = rec.setup_type
            self._discovery.record_setup_type(setup_type or "UNKNOWN")

            if setup_type and not self._discovery.is_tradable_setup(setup_type):
                self._discovery.record_poi_reaction_skip()
                self._metrics.record_poi_reaction_skip()
                self._metrics.record_decision(
                    decision="REJECT",
                    setup_type=setup_type,
                    setup_grade=rec.setup_grade,
                    reject_reason="POI_REACTION_DIAGNOSTIC_SKIP",
                )
                continue

            # ── Record decision ─────────────────────────────────────
            self._metrics.record_decision(
                decision=rec.decision,
                setup_type=rec.setup_type,
                setup_grade=rec.setup_grade,
                reject_reason=rec.reject_reason,
                veto_code=rec.veto_code,
            )

            # ── ENTER → open trade ──────────────────────────────────
            if rec.is_enter and rec.risk_allowed:
                self._metrics.record_trade_open()
                # Build trade dict for lifecycle tracking
                trade = {
                    "ticket": self._metrics.trade_count,
                    "side": rec.side or "BUY",
                    "entry_price": current_price,
                    "sl_price": 0.0,   # filled by trade_manager
                    "tp1_price": 0.0,
                    "tp2_price": 0.0,
                    "protected_sl_price": 0.0,
                    "risk_r": 1.0,
                    "tp1_rr": 0.5,
                    "tp2_rr": 1.5,
                    "leg1_closed": False,
                }
                self._lifecycle.open_trade(trade)

        # ── Finalize ──────────────────────────────────────────────────
        if self._profiler:
            self._profiler.finish()

        # Transfer gate rejections from discovery to metrics
        for gate, count in self._discovery._gate_rejections.items():
            for _ in range(count):
                self._metrics.record_gate_rejection(gate)

        summary = self._metrics.finalize()
        summary["engine"] = "v2"
        summary["run_id"] = self.run_id
        summary["runtime_ms"] = round((time.perf_counter() - t0) * 1000, 1)
        summary["candles_total"] = len(self.candles_1m)

        if self._profiler:
            summary["profiler"] = self._profiler.report()

        return summary

    def _is_eval(self, t: datetime) -> bool:
        """True if *t* is within the evaluation window."""
        if self.eval_start is not None and t < self.eval_start:
            return False
        if self.eval_end is not None and t > self.eval_end:
            return False
        return True

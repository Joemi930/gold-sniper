"""Live replay runner — wraps existing run_replay infrastructure with display hooks.

Runs the replay in a background asyncio thread, pushing state updates
to a thread-safe queue consumed by the TUI.
"""

from __future__ import annotations

import asyncio
import json
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # gold_sniper/
_REPO_ROOT = _PROJECT_ROOT.parent  # repo root (for `from gold_sniper.X` imports)
for p in (str(_PROJECT_ROOT), str(_REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import os

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

from core.blackboard import BlackBoard
from replay.decision_pipeline import ReplayDecisionPipeline
from replay.economic_calendar import load_calendar_result
from replay.execution_model import build_default_execution_model
from replay.historical_data import load_csv_candles
from replay.news_index import NewsIndex
from replay.replay_engine import ReplayEngine
from replay.replay_profiler import enable_profiling, get_profiler
from replay.run_replay import (
    _load_replay_timeframes,
    _resolve_boundaries,
    _resolve_timeframe_csv,
    resolve_replay_agents,
)
from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager
from data_pipeline.candle_manifest import build_candle_coverage_manifest


# ──────────────────────────────────────────────────────────────────────────────
# Shared state pushed to TUI
# ──────────────────────────────────────────────────────────────────────────────

class LiveState:
    """Thread-safe container for replay display state."""

    __slots__ = (
        "equity", "equity_initial", "progress_pct", "current_candle_utc",
        "candles_processed", "total_candles", "candles_per_sec",
        "agent_scores", "agent_statuses", "last_decision", "last_decision_reason",
        "last_kasper_grade", "last_kasper_decision",
        "trades_open", "tp1_count", "tp2_count", "full_sl_count",
        "protected_sl_count", "winrate", "expectancy_r", "drawdown_pct",
        "net_pnl", "decisions_enter", "decisions_wait", "decisions_reject",
        "phase", "elapsed_sec", "running", "error",
    )

    def __init__(self, initial_equity: float = 100.0):
        self.equity: float = initial_equity
        self.equity_initial: float = initial_equity
        self.progress_pct: float = 0.0
        self.current_candle_utc: str = ""
        self.candles_processed: int = 0
        self.total_candles: int = 1
        self.candles_per_sec: float = 0.0
        self.agent_scores: dict[str, float] = {}
        self.agent_statuses: dict[str, str] = {}
        self.last_decision: str = "—"
        self.last_decision_reason: str = ""
        self.last_kasper_grade: str = "—"
        self.last_kasper_decision: str = "—"
        self.trades_open: int = 0
        self.tp1_count: int = 0
        self.tp2_count: int = 0
        self.full_sl_count: int = 0
        self.protected_sl_count: int = 0
        self.winrate: float = 0.0
        self.expectancy_r: float = 0.0
        self.drawdown_pct: float = 0.0
        self.net_pnl: float = 0.0
        self.decisions_enter: int = 0
        self.decisions_wait: int = 0
        self.decisions_reject: int = 0
        self.phase: str = "initializing"
        self.elapsed_sec: float = 0.0
        self.running: bool = True
        self.error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "equity": self.equity,
            "equity_initial": self.equity_initial,
            "progress_pct": self.progress_pct,
            "current_candle_utc": self.current_candle_utc,
            "candles_processed": self.candles_processed,
            "total_candles": self.total_candles,
            "candles_per_sec": self.candles_per_sec,
            "agent_scores": dict(self.agent_scores),
            "agent_statuses": dict(self.agent_statuses),
            "last_decision": self.last_decision,
            "last_decision_reason": self.last_decision_reason,
            "last_kasper_grade": self.last_kasper_grade,
            "last_kasper_decision": self.last_kasper_decision,
            "trades_open": self.trades_open,
            "tp1_count": self.tp1_count,
            "tp2_count": self.tp2_count,
            "full_sl_count": self.full_sl_count,
            "protected_sl_count": self.protected_sl_count,
            "winrate": self.winrate,
            "expectancy_r": self.expectancy_r,
            "drawdown_pct": self.drawdown_pct,
            "net_pnl": self.net_pnl,
            "decisions_enter": self.decisions_enter,
            "decisions_wait": self.decisions_wait,
            "decisions_reject": self.decisions_reject,
            "phase": self.phase,
            "elapsed_sec": self.elapsed_sec,
            "running": self.running,
            "error": self.error,
        }


LIVE_STATE_SENTINEL_COMPLETE = "__REPLAY_COMPLETE__"
LIVE_STATE_SENTINEL_ERROR = "__REPLAY_ERROR__"


# ──────────────────────────────────────────────────────────────────────────────
# Live replay runner
# ──────────────────────────────────────────────────────────────────────────────

class ReplayInterrupted(Exception):
    """Raised when the user presses Esc to stop a running replay."""


def _extract_display_state(
    candle: dict[str, Any],
    blackboard: BlackBoard,
    decision: dict[str, Any] | None,
    live_state: LiveState,
    engine: ReplayEngine,
    start_time: float,
) -> None:
    """Update a LiveState from the current blackboard, decision, and engine."""
    now = time.monotonic()

    # Progress
    if hasattr(engine, "clock"):
        clock = engine.clock
        try:
            live_state.candles_processed = clock.index + 1 if hasattr(clock, "index") else 0
        except Exception:
            live_state.candles_processed = 0
        try:
            live_state.total_candles = len(clock)
        except Exception:
            live_state.total_candles = 1
        if live_state.total_candles > 0:
            live_state.progress_pct = min(
                100.0, (live_state.candles_processed / live_state.total_candles) * 100.0
            )

    # Timing
    live_state.elapsed_sec = now - start_time
    if live_state.elapsed_sec > 0 and live_state.candles_processed > 0:
        live_state.candles_per_sec = live_state.candles_processed / live_state.elapsed_sec

    # Current candle
    ts = candle.get("time")
    if isinstance(ts, datetime):
        live_state.current_candle_utc = ts.strftime("%Y-%m-%d %H:%M UTC")
    elif isinstance(ts, str):
        live_state.current_candle_utc = str(ts)

    # Phase
    try:
        phase_val = blackboard._data.get("meta", {}).get("phase", "evaluation")
        live_state.phase = str(phase_val) if phase_val else "evaluation"
    except Exception:
        live_state.phase = "evaluation"

    # Agent scores from blackboard
    agent_results = {}
    try:
        agent_results = blackboard._data.get("agent_results", {}) or {}
    except Exception:
        pass

    for agent_id in (f"agent_{i}" for i in range(1, 8)):
        ar = agent_results.get(agent_id)
        if isinstance(ar, dict):
            live_state.agent_scores[agent_id] = float(ar.get("score", 0) or 0)
            live_state.agent_statuses[agent_id] = str(ar.get("status", "—") or "—")
        elif hasattr(ar, "score"):
            live_state.agent_scores[agent_id] = float(getattr(ar, "score", 0) or 0)
            live_state.agent_statuses[agent_id] = str(getattr(ar, "status", "—") or "—")

    # Decision
    if decision:
        live_state.last_decision = str(decision.get("decision", decision.get("action", "—")))
        live_state.last_decision_reason = str(decision.get("readiness_reason", ""))[:120]
        live_state.last_kasper_grade = str(decision.get("kasper_grade", "—"))
        live_state.last_kasper_decision = str(decision.get("kasper_decision_recommendation", "—"))

        action = live_state.last_decision
        if "ENTER" in action:
            live_state.decisions_enter += 1
        elif "WAIT" in action or "WATCH" in action:
            live_state.decisions_wait += 1
        elif "REJECT" in action:
            live_state.decisions_reject += 1

    # Trade metrics from trade manager
    tm = getattr(engine, "trade_manager", None)
    if tm is not None:
        try:
            summary = tm.summary()
            live_state.equity = float(summary.get("final_equity", summary.get("equity", live_state.equity_initial)))
            live_state.net_pnl = float(summary.get("net_pnl", summary.get("net_pnl_R", 0)))
            live_state.winrate = float(summary.get("win_rate", summary.get("winrate", summary.get("win_rate_pct", 0))))
            live_state.expectancy_r = float(summary.get("expectancy_R", 0))
            live_state.drawdown_pct = float(summary.get("max_drawdown_pct", 0))
            live_state.trades_open = int(summary.get("open_trades_end_count", summary.get("open_trades", summary.get("active_trades", 0))))
            live_state.tp1_count = int(summary.get("tp1_hit_count", summary.get("tp1_count", 0)))
            live_state.tp2_count = int(summary.get("tp2_hit_count", summary.get("tp2_count", 0)))
            live_state.full_sl_count = int(summary.get("sl_hit_count", summary.get("full_sl_count", summary.get("sl_count", 0))))
            live_state.protected_sl_count = int(summary.get("protected_sl_hit_count", summary.get("protected_sl_count", 0)))
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point for background-thread replay
# ──────────────────────────────────────────────────────────────────────────────

async def _run_replay_live(
    *,
    run_id: str,
    start: str,
    end: str,
    warmup_start: str | None = None,
    data_root: Path | None = None,
    output_root: Path | None = None,
    news_calendar_path: Path | None = None,
    agent_ids: list[str] | None = None,
    initial_equity: float = 100.0,
    profile: bool = False,
    diagnose_agents: list[str] | None = None,
    state_queue: queue.Queue[Any] | None = None,
    stop_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run a replay with live state updates pushed to state_queue.

    This is the core async function, designed to be run via asyncio in a
    background thread.  It replicates the setup logic from
    ``run_replay.run_replay`` but inserts a display hook into the
    decision pipeline.

    Returns the summary dict produced by the replay engine.
    """
    if agent_ids is None:
        agent_ids = ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"]

    project_root = _PROJECT_ROOT
    if data_root is None:
        data_root = project_root / "data" / "historical" / "XAUUSD"
    if output_root is None:
        output_root = project_root / "data" / "replay_runs"
    if news_calendar_path is None:
        news_calendar_path = (
            project_root / "data" / "historical" / "news" / "calendar_events_20251231_20260619.jsonl"
        )

    live_state = LiveState(initial_equity=initial_equity)
    start_time = time.monotonic()
    engine_ref: list[Any] = [None]  # mutable container so hook can capture engine

    # ---- Build an argparse.Namespace-alike for the boundary helpers ----------
    class Args:
        pass

    args = Args()
    args.start = start
    args.end = end
    args.warmup_start = warmup_start
    # If warmup_start is provided, set eval boundaries explicitly
    # (otherwise _resolve_boundaries requires all three warmup/eval args)
    if warmup_start:
        args.eval_start = start
        args.eval_end = end
    else:
        args.eval_start = None
        args.eval_end = None
    args.data_root = data_root
    args.output_root = output_root
    args.run_id = run_id
    args.profile_replay = profile
    args.replay_agent = agent_ids
    args.news_calendar = news_calendar_path
    args.data_manifest = None
    args.p1_replay = True
    args.with_orchestrator = False
    args.tier_simulation = False
    args.initial_equity = initial_equity
    args.diagnose_agent = diagnose_agents or []
    args.diagnose_alignment = []
    args.diagnose_scoring = False
    args.diagnose_agent2_zonelifecycle = False
    args.diagnose_agent4_contextual_ote = False
    args.diagnose_agent5_contextual_trigger = False
    args.diagnose_contextual_orchestrator = False
    args.fast_precomputed = False
    args.precompute_root = None

    # ---- Phase 1: Resolve boundaries ----------------------------------------
    boundaries = _resolve_boundaries(args)
    load_start = boundaries["load_start"]
    eval_start = boundaries["eval_start"]
    eval_end = boundaries["eval_end"]

    # ---- Phase 2: Load candle data ------------------------------------------
    # _load_replay_timeframes returns: (loaded, sources, derived, missing)
    loaded, sources, derived_timeframes, missing_timeframes = _load_replay_timeframes(
        data_root=data_root,
        symbol="XAUUSD",
        start=load_start,
        end=eval_end,
    )
    candles_1m = loaded.get("1m", [])
    if not candles_1m and "M1" in loaded:
        candles_1m = loaded["M1"]
    external_candles = {
        k: v for k, v in loaded.items() if k not in ("1m", "M1")
    }

    if not candles_1m:
        raise RuntimeError(
            f"No M1 candle data found in {data_root}/1m/. "
            f"Run data preparation first (Option 0 in the menu)."
        )

    # ---- Phase 3: Load news -------------------------------------------------
    calendar = load_calendar_result(
        path=news_calendar_path,
        start=eval_start,
        end=eval_end,
    )
    news_index = NewsIndex.from_jsonl(str(news_calendar_path)) if news_calendar_path.exists() else NewsIndex()
    news_events = calendar.get("events", []) if isinstance(calendar, dict) else []

    # ---- Phase 4: Coverage manifest -----------------------------------------
    coverage = build_candle_coverage_manifest(
        data_root=str(data_root),
        symbol="XAUUSD",
        requested_start_utc=load_start.isoformat() if isinstance(load_start, datetime) else str(load_start),
        requested_end_utc=eval_end.isoformat() if isinstance(eval_end, datetime) else str(eval_end),
    )

    # ---- Phase 5: Decision pipeline -----------------------------------------
    pipeline = ReplayDecisionPipeline.from_agent_ids(
        agent_ids=agent_ids,
        use_orchestrator=False,
        news_events=news_events,
        news_feed_alive=False,
        news_source="REPLAY_JSONL",
        diagnose_agents=set(args.diagnose_agent),
        alignment_diagnostics=set(args.diagnose_alignment),
    )

    # ---- Phase 6: Blackboard -------------------------------------------------
    blackboard = BlackBoard()

    # ---- Phase 7: Trade manager ---------------------------------------------
    exec_model = build_default_execution_model()
    from replay.shadow_live_policy import ShadowLivePolicy

    shadow_policy = ShadowLivePolicy(initial_equity=initial_equity)
    tm_config = SimulatedTradeConfig(
        execution_model=exec_model,
        equity_initial=initial_equity,
        shadow_live_policy=shadow_policy,
        enable_daily_limits=True,
        enable_live_sizing=True,
    )
    trade_manager = SimulatedTradeManager(blackboard, tm_config)

    # ---- Phase 8: Wrap pipeline with display hook ---------------------------
    original_pipeline_call = pipeline.__call__

    async def display_hook(candle: dict[str, Any], bb: BlackBoard) -> dict[str, Any] | None:
        # Check for user interrupt
        if stop_event and stop_event.is_set():
            raise ReplayInterrupted("User stopped replay")

        # Call the real pipeline FIRST — never let display code break the decision
        decision = await original_pipeline_call(candle, bb)

        # Extract state for TUI (best-effort, must never break the pipeline)
        engine = engine_ref[0]
        if engine is not None:
            try:
                _extract_display_state(candle, bb, decision, live_state, engine, start_time)
            except Exception:
                pass  # display state extraction is non-critical

        # Push to queue (non-blocking)
        if state_queue is not None:
            try:
                state_queue.put_nowait(live_state.to_dict())
            except queue.Full:
                pass

        return decision

    pipeline.__call__ = display_hook

    # ---- Phase 9: Metadata --------------------------------------------------
    metadata: dict[str, Any] = {
        "run_id": run_id,
        "start": str(start),
        "end": str(end),
        "warmup_start": str(warmup_start) if warmup_start else None,
        "eval_start": eval_start.isoformat() if isinstance(eval_start, datetime) else str(eval_start),
        "eval_end": eval_end.isoformat() if isinstance(eval_end, datetime) else str(eval_end),
        "initial_equity": initial_equity,
        "symbol": "XAUUSD",
        "agents": agent_ids,
        "timeframes": ["1m", "5m", "15m", "1H", "4H"],
        "derived_timeframes": list(derived_timeframes) if derived_timeframes else [],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "V3.2-P1",
    }

    # ---- Phase 10: Build and run engine -------------------------------------
    engine = ReplayEngine(
        blackboard=blackboard,
        candles_1m=candles_1m,
        output_root=str(output_root),
        run_id=run_id,
        trade_manager=trade_manager,
        on_decision_hook=display_hook,
        candles_by_timeframe=external_candles,
        metadata=metadata,
        warmup_start=load_start,
        eval_start=eval_start,
        eval_end=eval_end,
    )
    engine_ref[0] = engine

    # Set diagnosis flags
    engine.diagnose_scoring = args.diagnose_scoring
    engine.diagnose_agent2_zonelifecycle = args.diagnose_agent2_zonelifecycle
    engine.diagnose_agent4_contextual_ote = args.diagnose_agent4_contextual_ote
    engine.diagnose_agent5_contextual_trigger = args.diagnose_agent5_contextual_trigger
    engine.diagnose_contextual_orchestrator = args.diagnose_contextual_orchestrator

    if profile:
        enable_profiling()

    live_state.phase = "running"
    if state_queue:
        try:
            state_queue.put_nowait(live_state.to_dict())
        except queue.Full:
            pass

    # ---- Run ----------------------------------------------------------------
    try:
        await engine.run()
    except ReplayInterrupted:
        live_state.phase = "interrupted"
        live_state.running = False
        if state_queue:
            state_queue.put({"type": "interrupted", "message": "Replay stopped by user."})
        # Still try to build a partial summary
        try:
            partial = engine._build_summary() if hasattr(engine, "_build_summary") else {}
        except Exception:
            partial = {}
        return partial

    # ---- Post-run -----------------------------------------------------------
    live_state.phase = "complete"
    live_state.running = False
    live_state.progress_pct = 100.0
    if state_queue:
        try:
            state_queue.put_nowait(live_state.to_dict())
        except queue.Full:
            pass

    # Build summary
    try:
        summary = engine._build_summary() if hasattr(engine, "_build_summary") else {}
    except Exception:
        summary = {}

    # Add coverage info
    if coverage:
        summary["data_coverage"] = coverage.to_dict() if hasattr(coverage, "to_dict") else {}

    # Add news index info
    try:
        summary["news_index"] = news_index.to_dict()
    except Exception:
        pass

    # Profiler report
    if profile:
        try:
            profiler = get_profiler()
            profile_report = profiler.report() if profiler else {}
            summary["profile_report"] = profile_report
        except Exception:
            pass

    # Signal completion (use put_nowait to avoid blocking if queue is full)
    if state_queue:
        try:
            state_queue.put_nowait({
                "type": "complete",
                "summary": summary,
                "output_root": str(output_root),
                "run_id": run_id,
                "run_dir": str(getattr(engine, "run_dir", "")),
            })
        except queue.Full:
            pass  # TUI will read summary from file directly

    return summary


def run_replay_in_thread(
    *,
    run_id: str,
    start: str,
    end: str,
    warmup_start: str | None = None,
    data_root: Path | None = None,
    output_root: Path | None = None,
    news_calendar_path: Path | None = None,
    agent_ids: list[str] | None = None,
    initial_equity: float = 100.0,
    profile: bool = False,
    diagnose_agents: list[str] | None = None,
    state_queue: queue.Queue[Any] | None = None,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Launch a replay in a background thread with its own asyncio event loop.

    Returns the thread (already started).  The thread pushes state dicts
    to ``state_queue`` and finishes with a ``{"type": "complete", ...}``
    sentinel dict.
    """

    def _thread_target() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _run_replay_live(
                    run_id=run_id,
                    start=start,
                    end=end,
                    warmup_start=warmup_start,
                    data_root=data_root,
                    output_root=output_root,
                    news_calendar_path=news_calendar_path,
                    agent_ids=agent_ids,
                    initial_equity=initial_equity,
                    profile=profile,
                    diagnose_agents=diagnose_agents,
                    state_queue=state_queue,
                    stop_event=stop_event,
                )
            )
        except Exception as exc:
            if state_queue is not None:
                state_queue.put({
                    "type": "error",
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                })
        finally:
            loop.close()

    thread = threading.Thread(target=_thread_target, daemon=True, name=f"replay-{run_id}")
    thread.start()
    return thread

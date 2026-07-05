"""Offline replay engine that feeds historical candles into the Blackboard."""
from __future__ import annotations

import inspect
import json
from collections import Counter, defaultdict, deque
from collections.abc import Mapping as MappingABC, Sequence as SequenceABC
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from config import SL_BUFFER_POINTS
from replay.multi_timeframe_builder import MultiTimeframeBuilder
from replay.replay_clock import ReplayClock
from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager
from replay.trade_journal import TradeJournal
from strategies.ob_five_star_evidence import enrich_active_ob_with_five_star_evidence
from strategies.professional_strategy_selector import evaluate_professional_strategies, normalize_ob_lifecycle
from validation.p2c_performance_summary import build_p2c_performance_summary


ReplayHook = Callable[[dict[str, Any], Any], Awaitable[None] | None]
DecisionHook = Callable[[dict[str, Any], Any], Awaitable[dict[str, Any] | None] | dict[str, Any] | None]
P1_45_COMPACT_EVENTS = True
EVENT_LOG_LIMIT_MB = 50.0
TRADE_JOURNAL_EVENTS = {
    "open",
    "missed_entry",
    "partial_close",
    "sl_moved_be_plus",
    "close",
    "leg_close",
    "tier_trade_open",
    "tier_missed_entry",
    "tier_trade_partial_close",
    "tier_sl_moved_be_plus",
    "tier_trade_close",
    "tier_leg_close",
}
SUPPORTED_EXTERNAL_TIMEFRAMES = ("5m", "15m", "30m", "1H", "4H")


class ReplayEngine:
    def __init__(
        self,
        blackboard,
        candles_1m: list[dict[str, Any]],
        *,
        output_root: str | Path = "data/replay_runs",
        run_id: str | None = None,
        trade_manager: SimulatedTradeManager | None = None,
        tier_trade_manager: SimulatedTradeManager | None = None,
        tier_simulation: bool = False,
        on_candle_hook: ReplayHook | None = None,
        on_decision_hook: DecisionHook | None = None,
        candles_by_timeframe: Mapping[str, Sequence[dict[str, Any]]] | None = None,
        metadata: dict[str, Any] | None = None,
        runtime_config: Any | None = None,  # ReplayRuntimeConfig
        warmup_start: datetime | str | None = None,
        eval_start: datetime | str | None = None,
        eval_end: datetime | str | None = None,
    ):
        self.blackboard = blackboard
        self.clock = ReplayClock(candles_1m)
        self.output_root = Path(output_root)
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.run_dir = self.output_root / self.run_id
        self.events_path = self.run_dir / "events.jsonl"
        self.trade_journal_path = self.run_dir / "trade_journal.jsonl"
        self.summary_path = self.run_dir / "summary.json"
        self.trade_manager = trade_manager or SimulatedTradeManager(blackboard)
        self.tier_trade_manager = tier_trade_manager or SimulatedTradeManager(
            blackboard,
            SimulatedTradeConfig(
                equity_initial=self.trade_manager.config.equity_initial,
                write_blackboard_positions=False,
                event_prefix="tier",
            ),
        )
        self.tier_simulation = bool(tier_simulation)
        self.on_candle_hook = on_candle_hook
        self.on_decision_hook = on_decision_hook
        self.metadata = metadata or {}
        self.warmup_start = _as_utc(warmup_start) if warmup_start is not None else None
        self.eval_start = _as_utc(eval_start) if eval_start is not None else None
        self.eval_end = _as_utc(eval_end) if eval_end is not None else None
        self._m1_window: deque[dict[str, Any]] = deque(maxlen=240)
        provided = candles_by_timeframe or {}
        self._external_candles = {
            timeframe: list(provided.get(timeframe, ()))
            for timeframe in SUPPORTED_EXTERNAL_TIMEFRAMES
        }
        self._external_indices = {timeframe: 0 for timeframe in self._external_candles}
        self._errors: list[str] = []
        self._events_for_summary: list[dict[str, Any]] = []
        self._total_candles_processed = 0
        self._warmup_candles = 0
        self._eval_candles = 0
        self.compact_event_logging = bool(
            self.metadata.get(
                "p1_45_compact_events",
                P1_45_COMPACT_EVENTS and str(self.run_id).startswith(("P1_45", "P1_46", "P1_47", "P1_48")),
            )
        )
        self.event_log_limit_mb = float(self.metadata.get("event_log_limit_mb", EVENT_LOG_LIMIT_MB))
        self._event_log_size_bytes = 0
        self._event_log_truncated = False
        self._warmup_start_logged = False
        self._warmup_last_time = None
        self._p1_decisions: list[dict[str, Any]] = []
        # Étape M15: True when the EXECUTION_TF bar closed on the current candle.
        # Default EXECUTION_TF="1m" → every candle → legacy behaviour.
        self._execution_bar_closed: bool = True
        self._mtf_builder = MultiTimeframeBuilder()
        self.decisions_path = self.run_dir / "decisions.jsonl"

        # ── P4: runtime config (fast/slow mode, event buffering) ──────
        if runtime_config is None:
            from replay.replay_runtime_config import ReplayRuntimeConfig
            runtime_config = ReplayRuntimeConfig()
        self.runtime_config = runtime_config
        self.fast_replay = self.runtime_config.fast_replay
        self._buffered_writer: Any = None
        if self.runtime_config.event_buffer_size > 0:
            try:
                from replay.buffered_jsonl_writer import BufferedJsonlWriter
                self._buffered_writer = BufferedJsonlWriter(
                    self.events_path,
                    flush_every=self.runtime_config.event_buffer_size,
                )
            except Exception:
                self._buffered_writer = None

    async def run(self) -> dict[str, Any]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path.write_text("", encoding="utf-8")
        self.trade_journal_path.write_text("", encoding="utf-8")
        await self._prepare_blackboard()

        first_time = None
        last_time = None
        for index, candle in enumerate(self.clock):
            phase, eval_active = self._phase_for_candle(candle)
            self._total_candles_processed += 1
            if eval_active:
                self._eval_candles += 1
            else:
                self._warmup_candles += 1
                self._warmup_last_time = candle["time"]
                if self.compact_event_logging and not self._warmup_start_logged:
                    self._warmup_start_logged = True
                    self._append_event(
                        {
                            "event": "warmup_start",
                            "time": _json_default(candle["time"]),
                            "phase": phase,
                            "eval_active": False,
                        }
                    )
            first_time = first_time or candle["time"]
            last_time = candle["time"]

            # P3-E/P4.1: profiler tick
            try:
                from replay.replay_profiler import get_profiler
                prof = get_profiler()
                if prof.enabled:
                    prof.tick_candle(eval_active)
            except Exception:
                prof = None

            if prof and prof.enabled:
                with prof.section("inject_candle"):
                    execution_bar_closed = await self._inject_candle(candle, index)
            else:
                execution_bar_closed = await self._inject_candle(candle, index)
            # Throttle: this dict only changes when phase/eval flips — skip the
            # per-candle async write+lock otherwise (pure speed, same state).
            if getattr(self, "_last_meta", None) != (phase, eval_active):
                await self.blackboard.update_dict("meta.replay", {"phase": phase, "eval_active": eval_active})
                self._last_meta = (phase, eval_active)
                # SPEED: at warmup→eval transition, freeze the (large, stable)
                # warmup heap so cyclic-GC stops rescanning it every cycle, and
                # relax gen0 threshold. Pure GC tuning — zero behaviour change.
                if eval_active and not getattr(self, "_gc_tuned", False):
                    self._gc_tuned = True
                    try:
                        import gc
                        gc.collect()
                        gc.freeze()
                        gc.set_threshold(50000, 30, 30)
                    except Exception:
                        pass

            # ── P4: warmup gate — context only, no decisions, no trades ──
            if not eval_active:
                # Still call the display hook so TUI shows progress through warmup
                await self._call_hook(candle, phase, eval_active)
                continue

            # ═══════════════════════════════════════════════════════════════
            # Evaluation-only below this line
            # ═══════════════════════════════════════════════════════════════
            # ── Étape M15: heavy DECISION pipeline only on EXECUTION_TF close ──
            # Default EXECUTION_TF="1m" → a bar closes every candle → legacy path
            # runs every candle (zero regression). For "15m" the decision fires
            # ~once per 15 candles, while fills below run on every 1m candle.
            if execution_bar_closed:
                if prof and prof.enabled:
                    with prof.section("decision_snapshot"):
                        self._append_event(self._decision_snapshot_event(candle, index, phase, eval_active))
                else:
                    self._append_event(self._decision_snapshot_event(candle, index, phase, eval_active))
                await self._call_hook(candle, phase, eval_active)

                # P3-E: profile decision hook
                _t0 = __import__("time").perf_counter()
                decision = await self._call_decision_hook(candle, phase, eval_active)
                _hook_ms = (__import__("time").perf_counter() - _t0) * 1000.0
                try:
                    prof = get_profiler()
                    if prof.enabled:
                        prof.record_agent("decision_hook", _hook_ms)
                except Exception:
                    pass

                # P4: stamp eval_active on decision for trade-manager safety gate
                decision["eval_active"] = True

                self._record_p1_decision(candle, index, decision, phase, eval_active)
                if decision.get("signal") or decision.get("trade_signal"):
                    self._errors.append("P1_REPLAY_FORBIDS_TRADE_SIGNAL")

                scoring_diag = None
                if getattr(self, "diagnose_scoring", False):
                    scoring_diag = self._build_scoring_diagnostic(candle, decision, phase, eval_active)

                event = self._decision_event(candle, index, decision, phase, eval_active)
                if scoring_diag:
                    event["scoring_diagnostic"] = scoring_diag

                self._append_event(event)

                await self._append_signal_event(candle, index, decision, phase, eval_active)
            else:
                # Non-execution-bar 1m candle: no fresh decision is computed. Pass
                # a neutral decision so the trade manager only MANAGES open trades
                # (TP/SL fills on 1m) and opens nothing new.
                decision = {"eval_active": True, "decision": "WAIT", "_non_execution_bar": True}

            # ── Trade manager: EVERY 1m candle (intrabar fill precision kept) ──
            try:
                _prof = get_profiler()
            except Exception:
                _prof = None
            if _prof and _prof.enabled:
                with _prof.section("trade_manager"):
                    events = await self.trade_manager.on_p1_decision(candle, decision)
                for event in events:
                    event.setdefault("phase", phase)
                    event.setdefault("eval_active", eval_active)
                    self._append_event(event)
                if execution_bar_closed:
                    with _prof.section("tier_events"):
                        tier_events = await self._tier_simulation_events(candle, index, decision, phase, eval_active)
                    for event in tier_events:
                        event.setdefault("phase", phase)
                        event.setdefault("eval_active", eval_active)
                        self._append_event(event)
            else:
                events = await self.trade_manager.on_p1_decision(candle, decision)
                for event in events:
                    event.setdefault("phase", phase)
                    event.setdefault("eval_active", eval_active)
                    self._append_event(event)
                if execution_bar_closed:
                    tier_events = await self._tier_simulation_events(candle, index, decision, phase, eval_active)
                    for event in tier_events:
                        event.setdefault("phase", phase)
                        event.setdefault("eval_active", eval_active)
                        self._append_event(event)

        if self.compact_event_logging and self._warmup_candles:
            self._append_event(
                {
                    "event": "warmup_end",
                    "time": _json_default(self._warmup_last_time),
                    "phase": "warmup",
                    "eval_active": False,
                    "candles_count": self._warmup_candles,
                    "errors_count": len(self._errors),
                }
            )

        # ── P4: flush buffered writer & write profiler report ─────────
        if self._buffered_writer is not None:
            try:
                self._buffered_writer.close()
            except Exception:
                pass

        try:
            from replay.replay_profiler import get_profiler
            prof = get_profiler()
            if prof.enabled:
                prof.write_report(self.run_dir)
        except Exception:
            pass

        summary = self._build_summary(first_time, last_time)
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, default=_json_default, indent=2),
            encoding="utf-8",
        )
        # P4: skip decisions.jsonl in fast mode (saves I/O)
        if self.runtime_config.write_decisions_jsonl:
            self.decisions_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False, default=_json_default) for item in self._p1_decisions),
                encoding="utf-8",
            )
        self._write_trade_journal()
        return summary

    async def _prepare_blackboard(self) -> None:
        async with self.blackboard._lock:
            self.blackboard._data["meta"]["run_mode"] = "REPLAY"
            self.blackboard._data["meta"]["replay"] = {}
            self.blackboard._data.setdefault("active_trades", {}).clear()
            self.blackboard._data.setdefault("positions", {}).setdefault("open_positions", []).clear()
            for candles in self.blackboard._data.get("market_data", {}).get("candles", {}).values():
                candles.clear()
            for tf in ("1m", "5m", "15m", "30m", "1H", "4H"):
                self.blackboard._data.setdefault("market_data", {}).setdefault("candles", {}).setdefault(tf, [])

    async def _inject_candle(self, candle: dict[str, Any], index: int) -> None:
        self._m1_window.append(candle)
        spread = float(candle.get("spread", 0.0) or 0.0)
        await self.blackboard.update_dict(
            "market_data.current_tick",
            {
                "bid": float(candle["close"]),
                "ask": float(candle["close"]),
                "spread_points": spread,
                "time": candle["time"],
                "volume": float(candle.get("tick_volume", candle.get("volume", 0.0)) or 0.0),
            },
        )
        await self.blackboard.update_market(
            {
                "current_price": float(candle["close"]),
                "bid": float(candle["close"]),
                "ask": float(candle["close"]),
                "spread_points": spread,
            }
        )
        self.blackboard.read_sync("market_data.candles.1m").append(candle)

        # P1 MTF builder: deterministic higher timeframe aggregation
        emitted = self._mtf_builder.update(candle)
        for timeframe, bars in emitted.items():
            if self._external_candles.get(timeframe):
                continue
            target = self.blackboard._data.setdefault("market_data", {}).setdefault("candles", {}).setdefault(timeframe, [])
            target.extend(bars)
            # SPEED LEAK FIX: the blackboard pre-creates capped deques only for
            # 4H/15m/1m. TFs emitted by the MTF builder (5m/30m/1H) landed in
            # PLAIN LISTS growing unbounded (~225k dicts over 2.4y) — nothing
            # reads beyond recent bars, but the growing heap raised GC pressure
            # continuously (the 356→42 c/s decay). Trim to a generous cap.
            if isinstance(target, list) and len(target) > 2000:
                del target[: len(target) - 2000]

        for timeframe, candles in self._external_candles.items():
            if candles:
                self._inject_external_timeframe(timeframe, candle["time"])
        await self.blackboard.notify_candle_close("1m", candle)

        # ── Étape M15: did the EXECUTION_TF bar just close on this 1m candle? ──
        # For EXECUTION_TF="1m" the driver candle IS the execution bar → always
        # True → decisions fire every candle → identical to legacy behaviour.
        # For "15m" etc., True only when the MTF builder emitted that TF here.
        try:
            from config import EXECUTION_TF as _EXEC_TF
        except Exception:
            _EXEC_TF = "1m"
        if _EXEC_TF == "1m":
            self._execution_bar_closed = True
        else:
            self._execution_bar_closed = bool(emitted.get(_EXEC_TF))
        return self._execution_bar_closed

    def _inject_external_timeframe(self, timeframe: str, current_time: Any) -> None:
        candles = self._external_candles[timeframe]
        index = self._external_indices[timeframe]
        target = self.blackboard.read_sync(f"market_data.candles.{timeframe}")
        while index < len(candles) and candles[index]["time"] <= current_time:
            target.append(candles[index])
            index += 1
        self._external_indices[timeframe] = index

    async def _call_hook(self, candle: dict[str, Any], phase: str, eval_active: bool) -> None:
        if self.on_candle_hook is None:
            return
        try:
            result = self.on_candle_hook(candle, self.blackboard)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # hook is optional and must not break smoke replay
            self._errors.append(str(exc))
            self._append_event(
                {
                    "event": "hook_error",
                    "time": _json_default(candle["time"]),
                    "phase": phase,
                    "eval_active": eval_active,
                    "error": str(exc),
                }
            )

    async def _call_decision_hook(self, candle: dict[str, Any], phase: str, eval_active: bool) -> dict[str, Any]:
        if self.on_decision_hook is None:
            return {}
        try:
            result = self.on_decision_hook(candle, self.blackboard)
            if inspect.isawaitable(result):
                result = await result
            return result or {}
        except Exception as exc:
            self._errors.append(str(exc))
            self._append_event(
                {
                    "event": "decision_hook_error",
                    "time": _json_default(candle["time"]),
                    "phase": phase,
                    "eval_active": eval_active,
                    "error": str(exc),
                }
            )
            return {"reject_reason": str(exc), "veto": True}

    async def _apply_decision_signal(self, decision: dict[str, Any]) -> None:
        if isinstance(decision, dict) and (decision.get("signal") or decision.get("trade_signal")):
            self._errors.append("P1_REPLAY_FORBIDS_TRADE_SIGNAL")
        return None

    def _record_p1_decision(
        self,
        candle: dict[str, Any],
        index: int,
        decision: dict[str, Any],
        phase: str,
        eval_active: bool,
    ) -> None:
        if not isinstance(decision, dict):
            decision = {}
        if getattr(getattr(self, "runtime_config", None), "fast_replay", False):
            _slim = {
                "timestamp": _json_default(candle["time"]),
                "eval_active": eval_active,
                "poi_micro_synergy": decision.get("poi_micro_synergy"),
                "micro_confirmed": decision.get("micro_confirmed"),
                "micro_inside_poi": decision.get("micro_inside_poi"),
                "effective_poi_status": decision.get("effective_poi_status"),
                "decision": decision.get("decision", "UNKNOWN"),
                "setup_grade": decision.get("setup_grade", "UNKNOWN"),
                "setup_type": decision.get("setup_type"),
                "setup_family": decision.get("setup_family"),
                "scenario_type": decision.get("scenario_type"),
                "kasper_decision_recommendation": decision.get("kasper_decision_recommendation"),
                "kasper_grade": decision.get("kasper_grade"),
                "kasper_error": decision.get("kasper_error"),
                "readiness_state": decision.get("readiness_state"),
                "enter_eligible": decision.get("enter_eligible"),
                "enter_eligibility_reason": decision.get("enter_eligibility_reason"),
                "enter_eligibility_blockers": decision.get("enter_eligibility_blockers") or [],
                "risk_allowed": decision.get("risk_allowed"),
                "risk_reason": decision.get("risk_reason"),
                "risk_multiplier": decision.get("risk_multiplier", 0.0),
                "grade_risk_multiplier": decision.get("grade_risk_multiplier"),
                "effective_risk_pct": decision.get("effective_risk_pct"),
            }
            self._p1_decisions.append(_slim)
            return
        record = {
            "timestamp": _json_default(candle["time"]),
            "bar_index": index,
            "phase": phase,
            "eval_active": eval_active,
            "decision": decision.get("decision", "UNKNOWN"),
            "setup_grade": decision.get("setup_grade", "UNKNOWN"),
            "confidence_score": decision.get("confidence_score", 0.0),
            "score_before_veto": decision.get("score_before_veto", 0.0),
            "score_after_veto": decision.get("score_after_veto", 0.0),
            "hard_veto": decision.get("hard_veto", False),
            "veto_code": decision.get("veto_code"),
            "blocked_stage": decision.get("blocked_stage"),
            "replay_invalid": decision.get("replay_invalid", False),
            "readiness_state": decision.get("readiness_state"),
            "readiness_reason": decision.get("readiness_reason"),
            "readiness_by_section": decision.get("readiness_by_section", {}),
            "missing_evidence": decision.get("missing_evidence", []),
            "soft_issues": decision.get("soft_issues", []),
            "risk_multiplier": decision.get("risk_multiplier", 0.0),
            "risk_plan": decision.get("risk_plan", {}),
            # P2-E Phase 7A: setup taxonomy
            "setup_type": decision.get("setup_type", "UNKNOWN"),
            "side": decision.get("side"),
            "setup_family": decision.get("setup_family", "UNKNOWN"),
            "setup_classification": decision.get("setup_classification", {}),
            "setup_classification_reason": decision.get("setup_classification_reason", "UNKNOWN"),
            "setup_classification_confidence": decision.get("setup_classification_confidence", 0.0),
            # P2-E Phase 7B: enter eligibility
            "enter_eligible": decision.get("enter_eligible", False),
            "enter_eligibility_reason": decision.get("enter_eligibility_reason", "UNKNOWN"),
            "enter_eligibility_blockers": decision.get("enter_eligibility_blockers", []),
            "enter_eligibility_checks": decision.get("enter_eligibility_checks", {}),
            "risk_preview": decision.get("risk_preview", {}),
            # P2-E Phase 7C: risk multiplier mapping
            "grade_risk_multiplier": decision.get("grade_risk_multiplier"),
            "effective_risk_pct": decision.get("effective_risk_pct"),
            "setup_max_risk_multiplier": decision.get("setup_max_risk_multiplier"),
            "risk_allowed": decision.get("risk_allowed", False),
            "risk_reason": decision.get("risk_reason", "UNKNOWN"),
            # P2-E Phase14: readiness/risk gate decomposition
            "gate_primary_blocker": decision.get("gate_primary_blocker"),
            "gate_blockers": decision.get("gate_blockers", []),
            "gate_decomposition": decision.get("gate_decomposition", {}),
            "setup_tradable": decision.get("setup_tradable"),
            "has_setup_candidate": decision.get("has_setup_candidate"),
            "has_tradable_setup_candidate": decision.get("has_tradable_setup_candidate"),
            # P2-E Phase 7D: readiness coherence
            "readiness_coherence": decision.get("readiness_coherence", {}),
            "readiness_non_ready_sections": decision.get("readiness_non_ready_sections", {}),
            "readiness_missing_ready_blockers": decision.get("readiness_missing_ready_blockers", []),
            # P2-E Phase8: setup signal inventory and near-miss diagnostics
            "setup_signal_inventory": decision.get("setup_signal_inventory", {}),
            "setup_candidates": decision.get("setup_candidates", []),
            "best_setup_candidate": decision.get("best_setup_candidate", {}),
            # P2-E Phase15: setup-level sweep evidence without forcing liquidity ready
            "micro_sweep_confirmed": decision.get("micro_sweep_confirmed"),
            "setup_sweep_evidence": decision.get("setup_sweep_evidence"),
            "setup_sweep_evidence_source": decision.get("setup_sweep_evidence_source"),
            # P2-E Phase16: reconciled liquidity evidence audit
            "liquidity_evidence_source": decision.get("liquidity_evidence_source"),
            "micro_liquidity_confirmed": decision.get("micro_liquidity_confirmed"),
            "liquidity_reconciled": decision.get("liquidity_reconciled"),
            "liquidity_reconciliation_reason": decision.get("liquidity_reconciliation_reason"),
            "liquidity_reconciliation_blockers": decision.get("liquidity_reconciliation_blockers", []),
            "liquidity_reconciliation_payload": decision.get("liquidity_reconciliation_payload", {}),
            "near_miss_rank_score": decision.get("near_miss_rank_score", 0.0),
            "near_miss_missing_signals": decision.get("near_miss_missing_signals", []),
            "near_miss_present_signals": decision.get("near_miss_present_signals", []),
            # P2-E Phase10: POI contract coherence diagnostics
            "poi_contract_status": decision.get("poi_contract_status"),
            "poi_contract_reason": decision.get("poi_contract_reason"),
            "poi_contract_contradictions": decision.get("poi_contract_contradictions", []),
            "poi_quality_breakdown": decision.get("poi_quality_breakdown", {}),
            "poi_score_source": decision.get("poi_score_source"),
            "poi_score_is_computed": decision.get("poi_score_is_computed"),
            # P2-E Phase13: Agent2 POI rejection decomposition
            "poi_rejection_code": decision.get("poi_rejection_code"),
            "poi_rejection_source": decision.get("poi_rejection_source"),
            "poi_rejection_severity": decision.get("poi_rejection_severity"),
            "poi_rejection_fatal": decision.get("poi_rejection_fatal"),
            "poi_rejection_recoverable": decision.get("poi_rejection_recoverable"),
            "poi_rejection_reason": decision.get("poi_rejection_reason"),
            "poi_rejection": decision.get("poi_rejection", {}),
            "p1_evidence_bundle": decision.get("p1_evidence_bundle", {}),
            "p1_evidence_validation_errors": decision.get("p1_evidence_validation_errors", []),
            # P2-E Phase11: micro contract evidence persistence
            "micro_contract_status": decision.get("micro_contract_status"),
            "micro_contract_readiness": decision.get("micro_contract_readiness"),
            "micro_contract_reason": decision.get("micro_contract_reason"),
            "micro_contract_confirmed": decision.get("micro_contract_confirmed"),
            "micro_contract_waiting_trigger": decision.get("micro_contract_waiting_trigger"),
            "micro_contract_invalid": decision.get("micro_contract_invalid"),
            "micro_contract_missing_data": decision.get("micro_contract_missing_data"),
            "micro_contract_outside_poi": decision.get("micro_contract_outside_poi"),
            "micro_contract_missing_fields": decision.get("micro_contract_missing_fields", []),
            "micro_contract_present_fields": decision.get("micro_contract_present_fields", []),
            "micro_contract_contradictions": decision.get("micro_contract_contradictions", []),
            "micro_evidence": decision.get("micro_evidence", {}),
            "sweep_1m_confirmed": decision.get("sweep_1m_confirmed"),
            "choch_detected": decision.get("choch_detected"),
            "trigger_inside_poi": decision.get("trigger_inside_poi"),
            "price_in_agent2_poi": decision.get("price_in_agent2_poi"),
            "trigger_outside_poi": decision.get("trigger_outside_poi"),
            "retest_confirmed": decision.get("retest_confirmed"),
            "trigger_confirmed": decision.get("trigger_confirmed"),
            "candles_1m_count": decision.get("candles_1m_count"),
            # P2-E Phase12: POI-Micro synergy diagnostics
            "poi_micro_synergy": decision.get("poi_micro_synergy", False),
            "poi_micro_synergy_status": decision.get("poi_micro_synergy_status"),
            "poi_micro_reason": decision.get("poi_micro_reason"),
            "micro_confirmed": decision.get("micro_confirmed"),
            "micro_inside_poi": decision.get("micro_inside_poi"),
            "micro_outside_poi": decision.get("micro_outside_poi"),
            "effective_poi_status": decision.get("effective_poi_status"),
            "poi_micro_upgraded_poi_status": decision.get("poi_micro_upgraded_poi_status"),
            "poi_micro_remaining_blockers": decision.get("poi_micro_remaining_blockers", []),
            "poi_micro_synergy_payload": decision.get("poi_micro_synergy_payload", {}),
            # ── P1 Kasper Brain Core: scenario-driven decision fields ────
            "scenario_id": decision.get("scenario_id"),
            "scenario_type": decision.get("scenario_type"),
            "market_story": decision.get("market_story"),
            "sequence_pass_fail": decision.get("sequence_pass_fail", {}),
            "missing_confluence": decision.get("missing_confluence"),
            "entry_reason": decision.get("entry_reason"),
            "invalidation_reason": decision.get("invalidation_reason"),
            "target_reason": decision.get("target_reason"),
            "kasper_grade": decision.get("kasper_grade"),
            "kasper_score": decision.get("kasper_score"),
            "kasper_decision_recommendation": decision.get("kasper_decision_recommendation"),
            "hard_veto_reason": decision.get("hard_veto_reason"),
            # P2.1: PDE/Kasper alignment fields
            "kasper_pde_alignment_status": decision.get("kasper_pde_alignment_status"),
            "kasper_pde_alignment_reason": decision.get("kasper_pde_alignment_reason"),
            "pde_blocking_reason": decision.get("pde_blocking_reason"),
            "trade_open_source": decision.get("trade_open_source"),
            "risk_grade_pct": decision.get("risk_grade_pct"),
        }
        self._p1_decisions.append(record)

    def _decision_snapshot_event(self, candle: dict[str, Any], index: int, phase: str, eval_active: bool) -> dict[str, Any]:
        # P4: use targeted reads instead of get_all() to avoid deep-copying the
        # entire growing blackboard on every candle.
        bb = self.blackboard
        data = bb._data
        candles = data.get("market_data", {}).get("candles", {})
        agent_results = data.get("agent_results", {}) or {}
        return {
            "event": "decision_snapshot",
            "time": _json_default(candle["time"]),
            "bar_index": index,
            "phase": phase,
            "eval_active": eval_active,
            "snapshot": {
                "current_tick": data.get("market_data", {}).get("current_tick", {}),
                "market": data.get("market", {}),
                "candles": {timeframe: len(values) for timeframe, values in candles.items()},
                "agent_results_available": [key for key, value in agent_results.items() if value is not None],
                "orchestrator": data.get("orchestrator", {}),
                "has_trade_signal": False,
                "active_trades": len(data.get("active_trades", {}) or {}),
            },
        }

    def _decision_event(
        self,
        candle: dict[str, Any],
        index: int,
        decision: dict[str, Any],
        phase: str,
        eval_active: bool,
    ) -> dict[str, Any]:
        return {
            "event": "decision",
            "time": _json_default(candle["time"]),
            "bar_index": index,
            "phase": phase,
            "eval_active": eval_active,
            "hook_available": self.on_decision_hook is not None,
            "agents": decision.get("agents", {}),
            "agent_errors": decision.get("agent_errors", {}),
            "alignment_diagnostic": decision.get("alignment_diagnostic"),
            "orchestrator": decision.get("orchestrator", {}),
            "veto": decision.get("veto"),
            "score_final": decision.get("score_final", decision.get("score")),
            "reject_reason": decision.get("reject_reason") or decision.get("reason"),
            "readiness_state": decision.get("readiness_state"),
            "readiness_reason": decision.get("readiness_reason"),
            "readiness_by_section": decision.get("readiness_by_section", {}),
        }

    def _build_scoring_diagnostic(self, candle: dict[str, Any], decision: dict[str, Any], phase: str, eval_active: bool) -> dict[str, Any]:
        orchestrator = decision.get("orchestrator", {})
        agents = decision.get("agents", {})
        
        tier_score = _decision_score(decision)
        tier = _tier_for_score(tier_score)
        
        orch_raw_score = orchestrator.get("raw_score")
        orch_final_score = orchestrator.get("score")
        orch_decision = orchestrator.get("decision")
        orch_reason = orchestrator.get("reason")
        orch_threshold = orchestrator.get("strategy_min_score")
        
        score_mismatch = False
        score_source = "UNKNOWN"
        if tier_score == orch_final_score:
            score_source = "orchestrator.score"
        elif tier_score == decision.get("score_final"):
            score_source = "decision.score_final"
        else:
            score_source = "other"
            score_mismatch = True

        sig = decision.get("signal", {})
        final_signal = sig.get("signal") if isinstance(sig, dict) else None

        tier_rejection = None
        if tier and eval_active and self.tier_trade_manager and not self.tier_trade_manager.active_positions:
            _, tier_rejection = _tier_replay_trade_signal(
                decision,
                self.blackboard,
                candle,
                tier,
                tier_score,
                self.tier_trade_manager.equity,
            )

        agent_6_veto = agents.get("agent_6", {}).get("veto")
        a6_hard_filter = agents.get("agent_6", {}).get("hard_filter_pass")
        if a6_hard_filter is None and agent_6_veto is not None:
            a6_hard_filter = not agent_6_veto

        hard_filter_count = sum(1 for i in range(1, 8) if agents.get(f"agent_{i}", {}).get("hard_filter_pass") or (i==6 and a6_hard_filter))

        return {
            "timestamp": _json_default(candle["time"]),
            "phase": phase,
            "eval_active": eval_active,
            "decision": final_signal or orch_decision or "NONE",
            "final_reason": decision.get("reject_reason") or decision.get("reason") or orch_reason,
            "agent_1_score": agents.get("agent_1", {}).get("score"),
            "agent_2_score": agents.get("agent_2", {}).get("score"),
            "agent_3_score": agents.get("agent_3", {}).get("score"),
            "agent_4_score": agents.get("agent_4", {}).get("score"),
            "agent_5_score": agents.get("agent_5", {}).get("score"),
            "agent_6_score": agents.get("agent_6", {}).get("score"),
            "agent_7_score": agents.get("agent_7", {}).get("score"),
            "agent_1_hard_filter_pass": agents.get("agent_1", {}).get("hard_filter_pass"),
            "agent_2_hard_filter_pass": agents.get("agent_2", {}).get("hard_filter_pass"),
            "agent_3_hard_filter_pass": agents.get("agent_3", {}).get("hard_filter_pass"),
            "agent_4_hard_filter_pass": agents.get("agent_4", {}).get("hard_filter_pass"),
            "agent_5_hard_filter_pass": agents.get("agent_5", {}).get("hard_filter_pass"),
            "agent_6_hard_filter_pass": a6_hard_filter,
            "agent_7_hard_filter_pass": agents.get("agent_7", {}).get("hard_filter_pass"),
            "hard_filters_pass_count": hard_filter_count,
            "stars_count": orchestrator.get("stars", 0),
            "orchestrator_raw_score": orch_raw_score,
            "orchestrator_final_score": orch_final_score,
            "orchestrator_threshold": orch_threshold,
            "orchestrator_decision": orch_decision,
            "orchestrator_reason": orch_reason,
            "tier_score": tier_score,
            "tier_name": tier["name"] if tier else None,
            "tier_eligible": tier is not None,
            "tier_rejection_reason": tier_rejection,
            "score_source": score_source,
            "score_mismatch_detected": score_mismatch,
        }

    async def _append_signal_event(
        self,
        candle: dict[str, Any],
        index: int,
        decision: dict[str, Any],
        phase: str,
        eval_active: bool,
    ) -> None:
        reason = decision.get("reject_reason") or decision.get("reason") or decision.get("veto_code")
        if reason:
            self._append_event(
                {
                    "event": "p1_decision_rejected",
                    "time": _json_default(candle["time"]),
                    "bar_index": index,
                    "phase": phase,
                    "eval_active": eval_active,
                    "reason": reason,
                    "decision": decision.get("decision"),
                }
            )

    def _append_event(self, event: dict[str, Any]) -> None:
        # ── P4 fast-replay: drop non-essential events ─────────────────
        if self.runtime_config.minimal_events:
            if not self._is_fast_keep_event(event):
                return

        if self._drop_compact_event(event):
            return
        event = _json_safe(event)
        self._events_for_summary.append(dict(event))
        if not self._write_compact_event(event):
            return

        # ── P4: use buffered writer when available ────────────────────
        if self._buffered_writer is not None:
            try:
                self._buffered_writer.write(event)
            except Exception:
                pass  # fallback to direct write on buffer error
            return

        line = json.dumps(event, ensure_ascii=False, default=_json_default) + "\n"
        line_bytes = len(line.encode("utf-8"))
        limit_bytes = int(self.event_log_limit_mb * 1024 * 1024)
        if self._event_log_truncated:
            return
        if self._event_log_size_bytes + line_bytes > limit_bytes:
            self._event_log_truncated = True
            warning = {
                "event": "event_log_warning",
                "time": _json_default(datetime.now(timezone.utc)),
                "reason": "EVENT_LOG_LIMIT_REACHED",
                "event_log_limit_mb": self.event_log_limit_mb,
            }
            warning_line = json.dumps(warning, ensure_ascii=False, default=_json_default) + "\n"
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(warning_line)
            self._event_log_size_bytes += len(warning_line.encode("utf-8"))
            return
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        self._event_log_size_bytes += line_bytes

    def _is_fast_keep_event(self, event: dict[str, Any]) -> bool:
        """P4: keep only trade-lifecycle events in fast mode."""
        name = str(event.get("event") or "")
        if name in {
            "open", "close", "leg_close", "missed_entry",
            "rejected", "warmup_start", "warmup_end",
            "hook_error", "decision_hook_error", "event_log_warning",
        }:
            return True
        # Keep any event that is explicitly marked eval_active (trade-related)
        if event.get("eval_active") is True and name in {
            "tier_trade_open", "tier_trade_close", "tier_leg_close",
            "tier_missed_entry", "tier_trade_rejected",
        }:
            return True
        return False

    def _drop_compact_event(self, event: dict[str, Any]) -> bool:
        if not self.compact_event_logging:
            return False
        phase = event.get("phase")
        name = event.get("event")
        if phase == "warmup" and name not in {"warmup_start", "warmup_end", "hook_error", "decision_hook_error"}:
            return True
        return False

    def _write_compact_event(self, event: dict[str, Any]) -> bool:
        if not self.compact_event_logging:
            return True
        name = event.get("event")
        if name in {"warmup_start", "warmup_end", "hook_error", "decision_hook_error", "event_log_warning"}:
            return True
        if event.get("eval_active") is not True:
            return False
        if name != "decision":
            return True
        agents = event.get("agents", {}) or {}
        a2_diag = ((agents.get("agent_2", {}) or {}).get("payload", {}) or {}).get("diagnostic", {}) or {}
        a5 = agents.get("agent_5", {}) or {}
        orchestrator = event.get("orchestrator", {}) or {}
        setup_type = str(orchestrator.get("setup_type") or orchestrator.get("strategy") or "")
        human_decision = str(orchestrator.get("human_orchestrator_decision_shadow") or orchestrator.get("human_like_context_decision_shadow") or "")
        if setup_type and setup_type != "OBSERVATION":
            return True
        if human_decision and human_decision != "WAIT_FOR_HTF_NARRATIVE":
            return True
        if a2_diag.get("best_shadow_poi"):
            return True
        if a5:
            return True
        decision_text = str(orchestrator.get("decision") or event.get("signal") or "")
        return "STANDARD" in decision_text or "PREMIUM" in decision_text

    def _build_p1_metrics(self, decisions=None, *, data_quality=None, errors=None):
        from replay.replay_metrics import build_p1_replay_metrics
        return build_p1_replay_metrics(
            decisions or self._p1_decisions,
            data_quality=data_quality or self.metadata.get("data_quality", {}),
            errors=errors or self._errors,
        )

    def _build_summary(self, first_time, last_time) -> dict[str, Any]:
        trade_summary = self.trade_manager.summary()
        capital_summary = self.tier_trade_manager.summary() if self.tier_simulation else trade_summary
        global_summary = self._summary_from_events(self._events_for_summary, trade_summary)
        evaluation_summary = self._summary_from_events(
            [event for event in self._events_for_summary if event.get("eval_active") is True],
            None,
        )
        performance_summary = build_p2c_performance_summary(
            decisions=list(self._p1_decisions),
            events=list(self._events_for_summary),
        )
        # Phase18: override performance_status with trade_summary data
        open_end = trade_summary.get("open_trades_end_count", 0)
        closed = trade_summary.get("closed_trades", 0)
        filled = trade_summary.get("trades", 0)
        if filled == 0:
            performance_summary["performance_status"] = "NO_TRADES"
        elif closed == 0 and open_end > 0:
            performance_summary["performance_status"] = "TRADES_OPEN_ONLY"
        elif closed > 0 and open_end > 0:
            performance_summary["performance_status"] = "MIXED_REALIZED_AND_OPEN"
        elif closed > 0:
            performance_summary["performance_status"] = "REALIZED_TRADES_AVAILABLE"
        else:
            performance_summary["performance_status"] = "NO_TRADES"
        performance_summary["open_trades_end_count"] = open_end
        performance_summary["unrealized_R_total"] = trade_summary.get("unrealized_R_total")
        summary = {
            "run_id": self.run_id,
            "period_start": _json_default(first_time),
            "period_end": _json_default(last_time),
            "candles": self.clock.total,
            "total_candles_processed": self._total_candles_processed,
            "warmup_candles": self._warmup_candles,
            "eval_candles": self._eval_candles,
            "warmup_start": _json_default(self.warmup_start or first_time),
            "eval_start": _json_default(self.eval_start or first_time),
            "eval_end": _json_default(self.eval_end or last_time),
            "signals": trade_summary["signals"],
            "trades": trade_summary["trades"],
            "wins": trade_summary["wins"],
            "losses": trade_summary["losses"],
            "win_rate": trade_summary["win_rate"],
            "pnl": capital_summary["pnl"],
            "max_drawdown": capital_summary["max_drawdown"],
            "errors": list(self._errors),
            "rejections": trade_summary["rejections"],
            "closed_trades": trade_summary.get("closed_trades", 0),
            "missed_entries": trade_summary.get("missed_entries", 0),
            "partial_closes": trade_summary.get("partial_closes", 0),
            "be_plus_moves": trade_summary.get("be_plus_moves", 0),
            "tp1_hits": trade_summary.get("tp1_hits", 0),
            "tp2_hits": trade_summary.get("tp2_hits", 0),
            "sl_hits": trade_summary.get("sl_hits", 0),
            "protected_sl_hits": trade_summary.get("protected_sl_hits", 0),
            "tp3_hits": trade_summary.get("tp3_hits", 0),
            # ── P3: two-leg payoff metrics ──────────────────────────
            "parent_trades": trade_summary.get("parent_trades", trade_summary.get("closed_trades", 0)),
            "avg_win_R": trade_summary.get("avg_win_R"),
            "avg_loss_R": trade_summary.get("avg_loss_R"),
            "payoff_ratio": trade_summary.get("payoff_ratio"),
            "expectancy_R": trade_summary.get("expectancy_R"),
            # P3 Case E: pure R (before exit costs)
            "pure_avg_win_R": trade_summary.get("pure_avg_win_R"),
            "pure_avg_loss_R": trade_summary.get("pure_avg_loss_R"),
            "pure_expectancy_R": trade_summary.get("pure_expectancy_R"),
            "gross_profit_R": trade_summary.get("gross_profit_R"),
            "gross_loss_R": trade_summary.get("gross_loss_R"),
            "tp1_hit_count": trade_summary.get("tp1_hit_count", trade_summary.get("tp1_hits", 0)),
            "tp2_hit_count": trade_summary.get("tp2_hit_count", trade_summary.get("tp2_hits", 0)),
            "tp1_then_protected_sl_count": trade_summary.get("tp1_then_protected_sl_count"),
            "tp1_then_tp2_count": trade_summary.get("tp1_then_tp2_count"),
            "full_sl_count": trade_summary.get("full_sl_count"),
            "sl_hit_count": trade_summary.get("sl_hit_count", trade_summary.get("sl_hits", 0)),
            "protected_sl_hit_count": trade_summary.get("protected_sl_hit_count", trade_summary.get("protected_sl_hits", 0)),
            "leg1_pnl_R_total": trade_summary.get("leg1_pnl_R_total"),
            "leg2_pnl_R_total": trade_summary.get("leg2_pnl_R_total"),
            # ── P4: reporting consistency metrics ────────────────────
            "trades_per_day": trade_summary.get("trades_per_day", 0.0),
            "active_trading_days": trade_summary.get("active_trading_days", 0),
            "winrate_full_win": trade_summary.get("winrate_full_win", 0.0),
            "winrate_tp1_touch": trade_summary.get("winrate_tp1_touch", 0.0),
            "cost_drag_R": trade_summary.get("cost_drag_R", 0.0),
            "full_win_count": trade_summary.get("full_win_count", 0),
            "tp1_touch_count": trade_summary.get("tp1_touch_count", 0),
            "first_trade_time": trade_summary.get("first_trade_time"),
            "last_trade_time": trade_summary.get("last_trade_time"),
            "warmup_trade_count": trade_summary.get("warmup_trade_count", 0),
            # P4.1: computed directly (trade_summary does not have eval boundaries)
            "trades_per_eval_day": _compute_trades_per_eval_day(
                trade_summary.get("parent_trades", trade_summary.get("closed_trades", 0)),
                self.eval_start, self.eval_end, first_time, last_time,
            ),
            "trades_per_active_day": _compute_trades_per_active_day(
                trade_summary.get("parent_trades", trade_summary.get("closed_trades", 0)),
                trade_summary.get("active_trading_days", 0),
            ),
            "leg_sl_count": trade_summary.get("sl_hit_count", 0),
            "parent_full_sl_count": trade_summary.get("full_sl_count", 0),
            # ── end P3 metrics ──────────────────────────────────────
            "initial_equity": capital_summary.get("initial_equity"),
            "final_equity": capital_summary.get("final_equity", capital_summary.get("equity_final")),
            "net_pnl": capital_summary.get("net_pnl", capital_summary.get("pnl")),
            "net_pnl_pct": capital_summary.get("net_pnl_pct", 0.0),
            "max_drawdown_pct": capital_summary.get("max_drawdown_pct", 0.0),
            "tier_simulation": self.tier_simulation,
            "tier_summary": self._tier_summary() if self.tier_simulation else {},
            "execution_model": trade_summary.get("execution_model"),
            "execution_model_valid": trade_summary.get("execution_model_valid"),
            "fill_model": trade_summary.get("fill_model"),
            "p2c_faithful_simulation": bool(trade_summary.get("p2c_faithful_simulation")),
            # Phase18: shadow trade lifecycle end-of-replay metrics
            "open_trades_end_count": trade_summary.get("open_trades_end_count", 0),
            "open_trades_end_details": trade_summary.get("open_trades_end_details", []),
            "unrealized_R_total": trade_summary.get("unrealized_R_total"),
            "unrealized_pnl": trade_summary.get("unrealized_pnl"),
            "pending_entries_end_count": trade_summary.get("pending_entries_end_count", 0),
            "last_seen_candle_time": trade_summary.get("last_seen_candle_time"),
            "daily_trade_counts": trade_summary.get("daily_trade_counts", {}),
            "total_daily_trades": trade_summary.get("total_daily_trades", 0),
            "daily_limit_rejections": trade_summary.get("daily_limit_rejections", 0),
            "grade_blocked_count": trade_summary.get("grade_blocked_count", 0),
            "shadow_live_policy": trade_summary.get("shadow_live_policy", {}),
            # Phase19: risk realism
            "risk_realism_status": trade_summary.get("risk_realism_status", "UNKNOWN"),
            "risk_realism_max_abs_r_error": trade_summary.get("risk_realism_max_abs_r_error", 0.0),
            "risk_realism_failed_count": trade_summary.get("risk_realism_failed_count", 0),
            "expected_sl_loss_total": trade_summary.get("expected_sl_loss_total", 0.0),
            "realized_sl_loss_total": trade_summary.get("realized_sl_loss_total", 0.0),
            "avg_realized_loss_to_risk_cash": trade_summary.get("avg_realized_loss_to_risk_cash"),
            "trade_lifecycle_status": _trade_lifecycle_status(trade_summary),
            "daily_limit_status": _daily_limit_status(trade_summary),
            "global_summary": global_summary,
            "evaluation_summary": evaluation_summary,
            "performance_summary": performance_summary,
            "p2c_performance_summary": performance_summary,
            "p1_45_compact_events": self.compact_event_logging,
            "event_log_truncated": self._event_log_truncated,
            "event_log_size_mb": round(self._event_log_size_bytes / (1024 * 1024), 3),
            "event_log_limit_mb": self.event_log_limit_mb,
            "metadata": dict(self.metadata),
            "p1_replay": self._build_p1_metrics(
                self._p1_decisions,
                data_quality=self.metadata.get("data_quality", {}),
                errors=self._errors,
            ),
            "p1_decisions_count": len(self._p1_decisions),
            "p1_replay_autonomous": True,
            "p1_broker_dependency": False,
        }

        if getattr(self, "diagnose_scoring", False):
            diags = [e["scoring_diagnostic"] for e in self._events_for_summary if "scoring_diagnostic" in e]
            score_sources = Counter(d["score_source"] for d in diags if d["score_source"])
            final_reasons = Counter(d["final_reason"] for d in diags if d["final_reason"])
            tier_rejections = Counter(d["tier_rejection_reason"] for d in diags if d["tier_rejection_reason"])
            mismatches = sum(1 for d in diags if d["score_mismatch_detected"])
            geq_60 = sum(1 for d in diags if d["tier_score"] >= 60)
            
            max_raw = max((d["orchestrator_raw_score"] for d in diags if d["orchestrator_raw_score"] is not None), default=0.0)
            max_final = max((d["orchestrator_final_score"] for d in diags if d["orchestrator_final_score"] is not None), default=0.0)
            
            summary["scoring_analysis"] = {
                "max_orchestrator_raw_score": max_raw,
                "max_orchestrator_final_score": max_final,
                "score_mismatch_detected_count": mismatches,
                "decisions_score_geq_60": geq_60,
                "top_score_sources": dict(score_sources.most_common(5)),
                "top_final_reasons": dict(final_reasons.most_common(5)),
                "top_tier_rejection_reasons": dict(tier_rejections.most_common(5)),
            }
            summary["scoring_diagnostics"] = diags


        # ── Shadow ZoneLifecycle analysis (P1.27) ──────────────────────────
        if getattr(self, "diagnose_agent2_zonelifecycle", False):
            zl_diags = [
                e.get("agent_2_diagnostic", {}) or {}
                for e in self._events_for_summary
                if e.get("agent_2_diagnostic")
            ]
            # Accumule les états depuis les résumés shadow inclus dans chaque cycle
            state_totals: dict[str, int] = {}
            killed_viable_total = 0
            total_zones = 0
            for diag in zl_diags:
                shadow = diag.get("shadow_zone_lifecycle_summary") or {}
                if not shadow:
                    continue
                total_zones += shadow.get("total", 0)
                killed_viable_total += shadow.get("killed_by_legacy_but_viable", 0)
                for state, count in (shadow.get("by_state") or {}).items():
                    state_totals[state] = state_totals.get(state, 0) + count

            summary["shadow_zone_lifecycle_analysis"] = {
                "note": "Shadow mode P1.27 — aucune décision modifiée",
                "total_zone_evaluations": total_zones,
                "by_state": state_totals,
                "killed_by_legacy_but_viable_cumulated": killed_viable_total,
                "potential_unlocked_zones": killed_viable_total,
                "states_considered_exploitable": ["WICK_TAGGED", "PARTIALLY_MITIGATED"],
            }
        # ──────────────────────────────────────────────────────────────────

        # ── Shadow Contextual OTE analysis (P1.28) ─────────────────────────
        if getattr(self, "diagnose_agent4_contextual_ote", False):
            a4_diags = [
                e.get("agents", {}).get("agent_4", {}).get("payload", {}).get("shadow_ote_context", {})
                for e in self._events_for_summary
                if e.get("event") == "decision"
            ]
            
            total_evaluations = 0
            premium_soft_warnings = 0
            blocked_strong_continuations = 0
            by_family: dict[str, int] = {}
            by_depth: dict[str, int] = {}
            
            for shadow in a4_diags:
                if not shadow:
                    continue
                total_evaluations += 1
                
                family = shadow.get("setup_family_hint", "UNKNOWN")
                by_family[family] = by_family.get(family, 0) + 1
                
                depth = shadow.get("retracement_depth_class", "UNKNOWN")
                by_depth[depth] = by_depth.get(depth, 0) + 1
                
                if shadow.get("premium_conflict_mode") == "SOFT_WARNING":
                    premium_soft_warnings += 1
                    if family == "TREND_CONTINUATION":
                        blocked_strong_continuations += 1

            summary["shadow_agent4_contextual_ote_analysis"] = {
                "note": "Shadow mode P1.28 — aucune décision modifiée",
                "total_ote_evaluations": total_evaluations,
                "by_setup_family": by_family,
                "by_retracement_depth": by_depth,
                "premium_rejections_to_soft_warnings": premium_soft_warnings,
                "blocked_strong_continuations_unlocked": blocked_strong_continuations,
            }
        # ──────────────────────────────────────────────────────────────────

        # ── Shadow Contextual Trigger analysis (P1.29) ─────────────────────────
        if getattr(self, "diagnose_agent5_contextual_trigger", False):
            a5_diags = [
                e.get("agents", {}).get("agent_5", {}).get("payload", {}).get("shadow_trigger_context", {})
                for e in self._events_for_summary
                if e.get("event") == "decision"
            ]

            total_evaluations = 0
            missing_trigger_hard_veto_count = 0
            missing_trigger_soft_warning_count = 0
            continuation_cases_where_agent5_would_not_block = 0
            sniper_pullback_cases_where_agent5_soft_warning = 0
            observation_cases_no_block = 0
            reversal_cases_where_agent5_remains_required = 0
            by_family: dict[str, int] = {}

            for shadow in a5_diags:
                if not shadow:
                    continue
                total_evaluations += 1

                family = shadow.get("setup_family_hint", "UNKNOWN")
                by_family[family] = by_family.get(family, 0) + 1

                conflict_mode = shadow.get("missing_trigger_conflict_mode")
                if conflict_mode == "HARD_VETO":
                    missing_trigger_hard_veto_count += 1
                    if family == "REVERSAL":
                        reversal_cases_where_agent5_remains_required += 1
                elif conflict_mode == "SOFT_WARNING":
                    missing_trigger_soft_warning_count += 1
                    if family == "TREND_CONTINUATION":
                        continuation_cases_where_agent5_would_not_block += 1
                    elif family == "SNIPER_PULLBACK":
                        sniper_pullback_cases_where_agent5_soft_warning += 1
                elif conflict_mode == "NONE":
                    if family == "OBSERVATION":
                        observation_cases_no_block += 1

            summary["shadow_agent5_contextual_trigger_analysis"] = {
                "note": "Shadow mode P1.29 — aucune décision modifiée",
                "total_agent5_evaluations": total_evaluations,
                "by_setup_family": by_family,
                "missing_trigger_hard_veto_count": missing_trigger_hard_veto_count,
                "missing_trigger_soft_warning_count": missing_trigger_soft_warning_count,
                "continuation_cases_where_agent5_would_not_block": continuation_cases_where_agent5_would_not_block,
                "sniper_pullback_cases_where_agent5_soft_warning": sniper_pullback_cases_where_agent5_soft_warning,
                "observation_cases_no_block": observation_cases_no_block,
                "reversal_cases_where_agent5_remains_required": reversal_cases_where_agent5_remains_required,
            }
        # ──────────────────────────────────────────────────────────────────

        # ── Shadow Contextual Orchestrator analysis (P1.30, P1.31A, P1.31B) ─────────────
        if getattr(self, "diagnose_contextual_orchestrator", False):
            from orchestrator.contextual_shadow import build_contextual_orchestrator_shadow
            from context.market_context_shadow import build_market_context_shadow

            total_ctx = 0
            by_setup_type: dict[str, int] = {}
            by_decision_mode: dict[str, int] = {}
            required_failures_by_agent: dict[str, int] = {}
            classification_blockers_by_reason: dict[str, int] = {}
            cases_legacy_rejected_but_ctx_wait = 0
            cases_legacy_rejected_but_ctx_candidate = 0
            cases_ctx_reject_due_news = 0

            # P1.31B Market Context Shadow v1 accumulators
            mcv1_by_primary_regime: dict[str, int] = {}
            mcv1_by_delivery_phase: dict[str, int] = {}
            mcv1_by_draw_on_liquidity: dict[str, int] = {}
            mcv1_by_order_flow: dict[str, int] = {}
            mcv1_trend_continuation_candidates = 0
            mcv1_reversal_candidates = 0
            mcv1_htf_strong_but_no_valid_poi = 0
            mcv1_poi_without_htf_confirmation = 0
            mcv1_sniper_pullback_waiting_for_pd = 0
            mcv1_context_blockers_by_reason: dict[str, int] = {}
            # Before/After setup type comparison
            setup_type_before_vs_after: dict[str, int] = {}

            # P1.31A Agent 4 Deep Dive
            a4_deep_dive_total_sniper = 0
            a4_deep_dive_blocked_by_a4 = 0
            a4_deep_dive_conflict_modes: dict[str, int] = {}
            a4_deep_dive_reasons: dict[str, int] = {}
            a4_deep_dive_direction_distribution: dict[str, int] = {}
            a4_deep_dive_agent1_score_buckets: dict[str, int] = {}
            a4_deep_dive_agent2_pass_confirmed = 0
            a4_deep_dive_agent3_optional_pass = 0
            a4_deep_dive_agent5_optional_pass = 0
            a4_deep_dive_agent7_pass = 0
            a4_deep_dive_premium_discount_side: dict[str, int] = {}
            a4_deep_dive_examples = []

            # P1.31A Market Context Visibility
            mcv_a1_ge_65 = 0
            mcv_a1_ge_80 = 0
            mcv_a2_pass = 0
            mcv_a1_ge_80_and_a2_pass = 0
            mcv_a1_ge_65_and_a2_pass_and_a4_shadow = 0

            for ev in self._events_for_summary:
                if ev.get("event") != "decision":
                    continue
                agents_raw = ev.get("agents", {})
                if not agents_raw:
                    continue

                # Variables for MCV and Deep Dive
                a1_score = agents_raw.get("agent_1", {}).get("score", 0)
                a2_pass = agents_raw.get("agent_2", {}).get("hard_filter_pass", False)
                a4_payload = agents_raw.get("agent_4", {}).get("payload", {})
                a4_shadow = a4_payload.get("shadow_ote_context", {}) if a4_payload else {}
                a4_shadow_present = bool(a4_shadow)

                if a1_score >= 65: mcv_a1_ge_65 += 1
                if a1_score >= 80: mcv_a1_ge_80 += 1
                if a2_pass: mcv_a2_pass += 1
                if a1_score >= 80 and a2_pass: mcv_a1_ge_80_and_a2_pass += 1
                if a1_score >= 65 and a2_pass and a4_shadow_present: mcv_a1_ge_65_and_a2_pass_and_a4_shadow += 1

                # Pass agents_raw directly — the function handles
                # Agent6 veto, Agent4/5 shadow contexts internally
                ctx = build_contextual_orchestrator_shadow(agents_raw)
                total_ctx += 1

                st = ctx["setup_type"]  # setup_type BEFORE market context enrichment
                by_setup_type[st] = by_setup_type.get(st, 0) + 1

                # P1.31B — Market Context Shadow v1 enrichment (read-only)
                mctx = build_market_context_shadow(ev, agents_raw)

                # Determine enriched setup type AFTER market context
                st_after = st
                if st == "OBSERVATION" and mctx["trend_continuation_candidate"]:
                    st_after = "TREND_CONTINUATION_CANDIDATE_SHADOW"
                elif st == "OBSERVATION" and mctx["reversal_candidate"]:
                    st_after = "REVERSAL_CANDIDATE_SHADOW"
                elif st == "SNIPER_PULLBACK" and not a4_shadow_present and a1_score >= 80:
                    st_after = "TREND_CONTINUATION_CANDIDATE_SHADOW"

                combo_key = f"{st} -> {st_after}"
                setup_type_before_vs_after[combo_key] = setup_type_before_vs_after.get(combo_key, 0) + 1

                # Accumulate market context counters
                pr = mctx["primary_regime"]
                dp = mctx["delivery_phase"]
                dol = mctx["draw_on_liquidity"]
                of_ = mctx["order_flow"]
                mcv1_by_primary_regime[pr] = mcv1_by_primary_regime.get(pr, 0) + 1
                mcv1_by_delivery_phase[dp] = mcv1_by_delivery_phase.get(dp, 0) + 1
                mcv1_by_draw_on_liquidity[dol] = mcv1_by_draw_on_liquidity.get(dol, 0) + 1
                mcv1_by_order_flow[of_] = mcv1_by_order_flow.get(of_, 0) + 1
                if mctx["trend_continuation_candidate"]: mcv1_trend_continuation_candidates += 1
                if mctx["reversal_candidate"]: mcv1_reversal_candidates += 1
                blocker_r = mctx.get("context_blocker_reason", "")
                if blocker_r == "HTF_STRONG_BUT_NO_VALID_POI": mcv1_htf_strong_but_no_valid_poi += 1
                if blocker_r == "POI_WITHOUT_HTF_CONFIRMATION": mcv1_poi_without_htf_confirmation += 1
                if blocker_r and blocker_r != "NONE": mcv1_context_blockers_by_reason[blocker_r] = mcv1_context_blockers_by_reason.get(blocker_r, 0) + 1
                if st == "SNIPER_PULLBACK" and "agent_4" in ctx["missing_required_agents"]: mcv1_sniper_pullback_waiting_for_pd += 1

                dm = ctx["contextual_decision_mode"]
                by_decision_mode[dm] = by_decision_mode.get(dm, 0) + 1

                blocker = ctx.get("classification_blocker_reason", "")
                if blocker:
                    classification_blockers_by_reason[blocker] = classification_blockers_by_reason.get(blocker, 0) + 1

                for missing_a in ctx["missing_required_agents"]:
                    required_failures_by_agent[missing_a] = required_failures_by_agent.get(missing_a, 0) + 1

                # Deep Dive Agent 4
                if st == "SNIPER_PULLBACK":
                    a4_deep_dive_total_sniper += 1
                    if "agent_4" in ctx["missing_required_agents"]:
                        a4_deep_dive_blocked_by_a4 += 1
                        
                        conflict_mode = a4_shadow.get("premium_conflict_mode", "UNKNOWN")
                        reason = a4_shadow.get("reason_contextual", "UNKNOWN")
                        direction = agents_raw.get("agent_4", {}).get("direction") or "UNKNOWN"
                        score_bucket = f"{int(a1_score // 5) * 5}-{int(a1_score // 5) * 5 + 4}"
                        pd_side = a4_shadow.get("range_position", "UNKNOWN")

                        a4_deep_dive_conflict_modes[conflict_mode] = a4_deep_dive_conflict_modes.get(conflict_mode, 0) + 1
                        a4_deep_dive_reasons[reason] = a4_deep_dive_reasons.get(reason, 0) + 1
                        a4_deep_dive_direction_distribution[direction] = a4_deep_dive_direction_distribution.get(direction, 0) + 1
                        a4_deep_dive_agent1_score_buckets[score_bucket] = a4_deep_dive_agent1_score_buckets.get(score_bucket, 0) + 1
                        a4_deep_dive_premium_discount_side[pd_side] = a4_deep_dive_premium_discount_side.get(pd_side, 0) + 1

                        if ctx["agent2_pass_seen"]: a4_deep_dive_agent2_pass_confirmed += 1
                        if ctx["agent3_pass_seen"]: a4_deep_dive_agent3_optional_pass += 1
                        if ctx["agent5_pass_seen"]: a4_deep_dive_agent5_optional_pass += 1
                        # agent_7 hard filter
                        a7_pass = agents_raw.get("agent_7", {}).get("hard_filter_pass", False)
                        if a7_pass: a4_deep_dive_agent7_pass += 1

                        if len(a4_deep_dive_examples) < 10:
                            # Try to extract a clean timestamp
                            ts = "UNKNOWN"
                            if ev.get("diagnostic", {}).get("time"):
                                ts = ev["diagnostic"]["time"]
                            elif (ev.get("alignment_diagnostic") or {}).get("time"):
                                ts = ev["alignment_diagnostic"]["time"]
                            elif (ev.get("scoring_diagnostic") or {}).get("timestamp"):
                                ts = ev["scoring_diagnostic"]["timestamp"]
                                
                            a4_deep_dive_examples.append({
                                "timestamp": ts,
                                "direction": direction,
                                "setup_type": "SNIPER_PULLBACK",
                                "agent1_score": a1_score,
                                "agent2_pass": ctx["agent2_pass_seen"],
                                "agent4_pass": False,
                                "agent4_shadow_present": a4_shadow_present,
                                "agent4_premium_conflict_mode": conflict_mode,
                                "agent4_reason": reason,
                                "agent7_pass": a7_pass,
                                "contextual_decision_mode": "WAIT",
                                "reason_contextual": "MISSING_REQUIRED:agent_4"
                            })

                # Compare with legacy decision
                legacy_reject = bool(ev.get("reject_reason") or ev.get("veto"))
                if legacy_reject:
                    if ctx["contextual_decision_mode"] == "REJECT":
                        cases_ctx_reject_due_news += 1
                    elif ctx["contextual_decision_mode"] == "WAIT":
                        cases_legacy_rejected_but_ctx_wait += 1
                    elif ctx["contextual_decision_mode"] == "CANDIDATE_MICRO":
                        cases_legacy_rejected_but_ctx_candidate += 1

            summary["shadow_contextual_orchestrator_analysis"] = {
                "note": "Shadow mode P1.30 — aucune décision modifiée",
                "total_evaluations": total_ctx,
                "by_setup_type": by_setup_type,
                "by_contextual_decision_mode": by_decision_mode,
                "classification_blockers_by_reason": classification_blockers_by_reason,
                "required_agents_failures_by_agent": required_failures_by_agent,
                "cases_legacy_rejected_but_contextual_wait": cases_legacy_rejected_but_ctx_wait,
                "cases_legacy_rejected_but_contextual_candidate": cases_legacy_rejected_but_ctx_candidate,
                "cases_contextual_reject_due_news": cases_ctx_reject_due_news,
                "no_live_execution_confirmed": True,
            }

            summary["shadow_agent4_blocker_deep_dive"] = {
                "total_sniper_pullback_cases": a4_deep_dive_total_sniper,
                "total_blocked_by_agent4": a4_deep_dive_blocked_by_a4,
                "blocked_by_agent4_pct": round((a4_deep_dive_blocked_by_a4 / a4_deep_dive_total_sniper * 100) if a4_deep_dive_total_sniper else 0, 2),
                "agent4_conflict_modes": a4_deep_dive_conflict_modes,
                "agent4_reasons": a4_deep_dive_reasons,
                "direction_distribution": a4_deep_dive_direction_distribution,
                "agent1_score_buckets": a4_deep_dive_agent1_score_buckets,
                "agent2_pass_confirmed_count": a4_deep_dive_agent2_pass_confirmed,
                "agent3_optional_pass_count": a4_deep_dive_agent3_optional_pass,
                "agent5_optional_pass_count": a4_deep_dive_agent5_optional_pass,
                "agent7_pass_count": a4_deep_dive_agent7_pass,
                "premium_discount_side_distribution": a4_deep_dive_premium_discount_side,
                "examples": a4_deep_dive_examples,
            }

            summary["shadow_market_context_visibility"] = {
                "agent1_ge_65_count": mcv_a1_ge_65,
                "agent1_ge_80_count": mcv_a1_ge_80,
                "agent2_pass_count": mcv_a2_pass,
                "agent1_ge_80_and_agent2_pass": mcv_a1_ge_80_and_a2_pass,
                "agent1_ge_65_and_agent2_pass_and_agent4_shadow": mcv_a1_ge_65_and_a2_pass_and_a4_shadow,
                "why_trend_continuation_0": "Condition requires A1>=80 and specific A4 setup family. The strict A4 constraints mean TREND_CONTINUATION isn't triggered even with A1>=80.",
                "why_reversal_0": "Condition requires A4 to label REVERSAL, which it currently rarely or never does under strict legacy OTE calculations."
            }

            summary["shadow_market_context_v1_analysis"] = {
                "note": "Shadow mode P1.31B — aucune décision modifiée",
                "total_evaluations": total_ctx,
                "by_primary_regime": mcv1_by_primary_regime,
                "by_delivery_phase": mcv1_by_delivery_phase,
                "by_draw_on_liquidity": mcv1_by_draw_on_liquidity,
                "by_order_flow": mcv1_by_order_flow,
                "trend_continuation_candidates": mcv1_trend_continuation_candidates,
                "reversal_candidates": mcv1_reversal_candidates,
                "htf_strong_but_no_valid_poi": mcv1_htf_strong_but_no_valid_poi,
                "poi_without_htf_confirmation": mcv1_poi_without_htf_confirmation,
                "sniper_pullback_waiting_for_ideal_pd_zone": mcv1_sniper_pullback_waiting_for_pd,
                "context_blockers_by_reason": mcv1_context_blockers_by_reason,
                "setup_type_before_market_context_vs_after": setup_type_before_vs_after,
                "no_live_execution_confirmed": True,
            }

            # ── P1.32 — Agent2 POI MTF Audit ────────────────────────────────
            a2_audit_total = 0
            a2_audit_by_reject_reason: dict[str, int] = {}
            a2_audit_fresh_count = 0
            a2_audit_mitigated_count = 0
            a2_audit_stale_count = 0
            a2_audit_invalidated_count = 0
            a2_audit_no_zone_created = 0
            a2_audit_zone_state_dist: dict[str, int] = {}
            a2_audit_zone_type_dist: dict[str, int] = {}
            a2_audit_score_dist: dict[str, int] = {}
            a2_audit_examples: list = []
            a2_audit_age_minutes_sum = 0
            a2_audit_age_minutes_count = 0
            a2_audit_score_lt_60 = 0
            a2_audit_wrong_side = 0

            for ev in self._events_for_summary:
                if ev.get("event") != "decision":
                    continue
                agents_raw = ev.get("agents", {})
                if not agents_raw:
                    continue

                # Only examine cases where Market Context Shadow flags TREND_CONTINUATION_CANDIDATE
                ev_a1_score = float(agents_raw.get("agent_1", {}).get("score") or 0.0)
                ev_a1_direction = agents_raw.get("agent_1", {}).get("direction") or "UNKNOWN"
                ev_a2_pass = bool(agents_raw.get("agent_2", {}).get("hard_filter_pass", False))

                if ev_a1_score < 80 or ev_a2_pass:
                    continue  # We only audit the HTF_STRONG_BUT_NO_VALID_POI cases

                a2_audit_total += 1

                a2_raw = agents_raw.get("agent_2", {})
                a2_reason = a2_raw.get("reason") or a2_raw.get("reject_reason") or "UNKNOWN"
                a2_payload = a2_raw.get("payload") or {}
                a2_diag = a2_raw.get("diagnostic") or {}

                a2_audit_by_reject_reason[a2_reason] = a2_audit_by_reject_reason.get(a2_reason, 0) + 1

                # Zone pool stats from diagnostic
                fresh_count = int(a2_diag.get("fresh_zone_count") or 0)
                mitigated_count = int(a2_diag.get("mitigated_zone_count") or 0)
                total_ob = int(a2_diag.get("detected_ob_count") or 0)
                a2_audit_fresh_count += fresh_count
                a2_audit_mitigated_count += mitigated_count
                if total_ob == 0:
                    a2_audit_no_zone_created += 1

                # Zone state distribution
                if total_ob == 0:
                    zone_state = "NO_ZONE"
                elif fresh_count == 0 and mitigated_count > 0:
                    zone_state = "ALL_MITIGATED"
                elif fresh_count > 0:
                    zone_state = "FRESH_AVAILABLE"
                else:
                    zone_state = "STALE_ONLY"
                a2_audit_zone_state_dist[zone_state] = a2_audit_zone_state_dist.get(zone_state, 0) + 1
                if zone_state in ("ALL_MITIGATED",):
                    a2_audit_mitigated_count += 1
                if zone_state in ("STALE_ONLY",):
                    a2_audit_stale_count += 1

                # Closest zone info (from best_raw_score_ob in diagnostic)
                best_zone = a2_diag.get("best_raw_score_ob") or a2_diag.get("best_fresh_ob") or a2_diag.get("selected_zone")
                closest_type = "UNKNOWN"
                closest_score = None
                closest_age_min = None
                closest_fresh = None

                if best_zone:
                    closest_type = best_zone.get("type") or "UNKNOWN"
                    closest_score = best_zone.get("score")
                    closest_age_min = best_zone.get("age_minutes")
                    closest_fresh = best_zone.get("fresh")

                    if closest_score is not None:
                        bucket = f"{int(closest_score // 10) * 10}-{int(closest_score // 10) * 10 + 9}"
                        a2_audit_score_dist[bucket] = a2_audit_score_dist.get(bucket, 0) + 1
                        if closest_score < 60:
                            a2_audit_score_lt_60 += 1

                    if closest_age_min is not None:
                        a2_audit_age_minutes_sum += closest_age_min
                        a2_audit_age_minutes_count += 1

                    # Zone type (BULLISH vs BEARISH vs direction alignment)
                    a2_audit_zone_type_dist[closest_type] = a2_audit_zone_type_dist.get(closest_type, 0) + 1
                    zone_dir = "LONG" if closest_type == "BULLISH" else ("SHORT" if closest_type == "BEARISH" else "UNKNOWN")
                    if zone_dir != ev_a1_direction and zone_dir != "UNKNOWN":
                        a2_audit_wrong_side += 1

                # Collect up to 10 examples
                if len(a2_audit_examples) < 10:
                    ts = "UNKNOWN"
                    if a2_diag.get("time"):
                        ts = a2_diag["time"]
                    elif ev.get("scoring_diagnostic", {}).get("timestamp"):
                        ts = ev["scoring_diagnostic"]["timestamp"]

                    mctx_ev = build_market_context_shadow(ev, agents_raw)
                    a2_audit_examples.append({
                        "timestamp": ts,
                        "direction": ev_a1_direction,
                        "market_context": {
                            "primary_regime": mctx_ev.get("primary_regime"),
                            "draw_on_liquidity": mctx_ev.get("draw_on_liquidity"),
                            "order_flow": mctx_ev.get("order_flow"),
                        },
                        "agent1_score": ev_a1_score,
                        "agent2_pass": False,
                        "agent2_reject_reason": a2_reason,
                        "closest_zone": {
                            "type": closest_type,
                            "timeframe": "15m",  # Agent2 operates on 15m
                            "score": closest_score,
                            "state": zone_state,
                            "age_minutes": closest_age_min,
                            "fresh": closest_fresh,
                            "legacy_mitigated": not bool(closest_fresh) if closest_fresh is not None else None,
                        },
                    })

            # Determine primary failure mode
            if a2_audit_total == 0:
                a2_primary_failure = "UNKNOWN"
            elif a2_audit_no_zone_created == a2_audit_total:
                a2_primary_failure = "NO_ZONE_CREATED"
            elif a2_audit_by_reject_reason.get("ZONE_ALREADY_MITIGATED", 0) > a2_audit_total * 0.5:
                a2_primary_failure = "ZONES_CREATED_BUT_TOO_FAST_MITIGATED"
            elif a2_audit_wrong_side > a2_audit_total * 0.4:
                a2_primary_failure = "ZONES_EXIST_BUT_WRONG_SIDE"
            elif a2_audit_score_lt_60 > a2_audit_total * 0.4:
                a2_primary_failure = "ZONES_EXIST_BUT_SCORE_TOO_LOW"
            elif a2_audit_stale_count > a2_audit_total * 0.4:
                a2_primary_failure = "ZONES_EXIST_BUT_STALE"
            else:
                # Derive from reject reason distribution
                top_reason = max(a2_audit_by_reject_reason, key=a2_audit_by_reject_reason.get) if a2_audit_by_reject_reason else ""
                if "MITIGATED" in top_reason:
                    a2_primary_failure = "ZONES_CREATED_BUT_TOO_FAST_MITIGATED"
                elif "SCORE" in top_reason or "WEAK" in top_reason:
                    a2_primary_failure = "ZONES_EXIST_BUT_SCORE_TOO_LOW"
                elif "NO_VALID" in top_reason or total_ob == 0:
                    a2_primary_failure = "NO_ZONE_CREATED"
                else:
                    a2_primary_failure = "UNKNOWN"

            avg_age = round(a2_audit_age_minutes_sum / a2_audit_age_minutes_count, 1) if a2_audit_age_minutes_count else None

            summary["shadow_agent2_poi_mtf_audit"] = {
                "note": "Shadow mode P1.32 — audit Agent2 POI lors des phases HTF fortes, aucune décision modifiée",
                "scope": "trend_continuation_candidate_shadow=True AND agent2_pass=False (A1>=80)",
                "total_htf_strong_no_poi": a2_audit_total,
                "agent2_primary_failure_mode": a2_primary_failure,
                "by_agent2_reject_reason": a2_audit_by_reject_reason,
                "zone_state_distribution": a2_audit_zone_state_dist,
                "closest_zone_type_distribution": a2_audit_zone_type_dist,
                "closest_zone_score_distribution": a2_audit_score_dist,
                "total_no_zone_created": a2_audit_no_zone_created,
                "total_fresh_zones_seen": a2_audit_fresh_count,
                "total_mitigated_zones_seen": a2_audit_mitigated_count,
                "total_stale_zones_seen": a2_audit_stale_count,
                "total_score_below_60": a2_audit_score_lt_60,
                "total_wrong_side_zones": a2_audit_wrong_side,
                "avg_closest_zone_age_minutes": avg_age,
                "examples": a2_audit_examples,
                "no_live_execution_confirmed": True,
            }

            # ── P1.33 — Agent2 Zone Anticipation Shadow ────────────────────
            za_total_cases = 0
            za_poi_5 = 0
            za_poi_15 = 0
            za_poi_30 = 0
            za_poi_60 = 0
            za_bars_sum = 0
            za_bars_count = 0
            za_score_dist: dict[str, int] = {}
            za_type_dist: dict[str, int] = {}
            za_aligned_count = 0
            za_examples: list = []

            for i, ev in enumerate(self._events_for_summary):
                if ev.get("event") != "decision":
                    continue
                agents_raw = ev.get("agents", {})
                if not agents_raw:
                    continue

                ev_a1_score = float(agents_raw.get("agent_1", {}).get("score") or 0.0)
                ev_a1_direction = agents_raw.get("agent_1", {}).get("direction") or "UNKNOWN"
                ev_a2_pass = bool(agents_raw.get("agent_2", {}).get("hard_filter_pass", False))
                a2_reason = agents_raw.get("agent_2", {}).get("reason") or agents_raw.get("agent_2", {}).get("reject_reason") or "UNKNOWN"

                # Condition: HTF strong, Agent2 blocked because mitigated or weak
                if ev_a1_score >= 80 and not ev_a2_pass and a2_reason in ["ZONE_ALREADY_MITIGATED", "NO_VALID_OB_SCORE_GE_60"]:
                    za_total_cases += 1
                    
                    found_poi = False
                    found_bars = 0
                    future_a2_pass_ev = None
                    future_a2_raw = {}
                    
                    for j in range(i + 1, min(i + 61, len(self._events_for_summary))):
                        future_ev = self._events_for_summary[j]
                        if future_ev.get("event") != "decision":
                            continue
                        found_bars += 1
                        f_agents_raw = future_ev.get("agents", {})
                        if f_agents_raw.get("agent_2", {}).get("hard_filter_pass", False):
                            found_poi = True
                            future_a2_pass_ev = future_ev
                            future_a2_raw = f_agents_raw.get("agent_2", {})
                            break
                            
                    if found_poi:
                        za_bars_sum += found_bars
                        za_bars_count += 1
                        if found_bars <= 5: za_poi_5 += 1
                        if found_bars <= 15: za_poi_15 += 1
                        if found_bars <= 30: za_poi_30 += 1
                        if found_bars <= 60: za_poi_60 += 1
                        
                        f_diag = future_a2_raw.get("diagnostic") or {}
                        f_best = f_diag.get("best_raw_score_ob") or f_diag.get("best_fresh_ob") or f_diag.get("selected_zone") or {}
                        f_type = f_best.get("type", "UNKNOWN")
                        f_score = f_best.get("score")
                        
                        za_type_dist[f_type] = za_type_dist.get(f_type, 0) + 1
                        if f_score is not None:
                            bucket = f"{int(f_score // 10) * 10}-{int(f_score // 10) * 10 + 9}"
                            za_score_dist[bucket] = za_score_dist.get(bucket, 0) + 1
                            
                        # Alignment with original A1 direction
                        f_dir = "LONG" if f_type == "BULLISH" else ("SHORT" if f_type == "BEARISH" else "UNKNOWN")
                        if f_dir == ev_a1_direction:
                            za_aligned_count += 1
                            
                        if len(za_examples) < 10:
                            ts = ev.get("scoring_diagnostic", {}).get("timestamp", "UNKNOWN")
                            za_examples.append({
                                "htf_signal_timestamp": ts,
                                "direction": ev_a1_direction,
                                "agent1_score": ev_a1_score,
                                "agent2_reject_reason": a2_reason,
                                "new_poi_found": True,
                                "bars_until_new_poi": found_bars,
                                "minutes_until_new_poi": found_bars, # assuming 1m resolution
                                "new_poi": {
                                    "type": f_type,
                                    "timeframe": "15m",
                                    "score": f_score,
                                    "state": "FRESH",
                                    "aligned_with_htf": f_dir == ev_a1_direction,
                                    "survived_bars": None,
                                    "mitigated_before_use": False
                                }
                            })
                    else:
                        if len(za_examples) < 10:
                            ts = ev.get("scoring_diagnostic", {}).get("timestamp", "UNKNOWN")
                            za_examples.append({
                                "htf_signal_timestamp": ts,
                                "direction": ev_a1_direction,
                                "agent1_score": ev_a1_score,
                                "agent2_reject_reason": a2_reason,
                                "new_poi_found": False,
                                "bars_until_new_poi": None,
                                "minutes_until_new_poi": None,
                                "new_poi": None
                            })

            if za_total_cases == 0:
                za_primary = "UNKNOWN"
            elif za_poi_60 == 0:
                za_primary = "NO_NEW_POI_AFTER_HTF_SIGNAL"
            elif za_poi_15 > za_total_cases * 0.5:
                za_primary = "NEW_POI_APPEARS_AND_SURVIVES"
            elif za_poi_60 > za_total_cases * 0.5:
                za_primary = "NEW_POI_APPEARS_TOO_LATE"
            else:
                za_primary = "UNKNOWN"

            summary["shadow_agent2_zone_anticipation_analysis"] = {
                "note": "Shadow mode P1.33 — anticipation de POI après HTF signal sans POI valide",
                "total_context_strong_no_poi_cases": za_total_cases,
                "new_poi_within_5_bars": za_poi_5,
                "new_poi_within_15_bars": za_poi_15,
                "new_poi_within_30_bars": za_poi_30,
                "new_poi_within_60_bars": za_poi_60,
                "avg_bars_until_new_poi": round(za_bars_sum / za_bars_count, 1) if za_bars_count else None,
                "avg_minutes_until_new_poi": round(za_bars_sum / za_bars_count, 1) if za_bars_count else None,
                "new_poi_score_distribution": za_score_dist,
                "new_poi_type_distribution": za_type_dist,
                "new_poi_aligned_with_htf_count": za_aligned_count,
                "no_new_poi_found_count": za_total_cases - za_poi_60,
                "zone_anticipation_primary_result": za_primary,
                "examples": za_examples,
            }
            # ──────────────────────────────────────────────────────────────────

            # ── P1.34 — Delivery Phase Entry Model Shadow ────────────────────
            dpm_total_cases = 0
            dpm_wait_15m_success = 0
            dpm_ltf_success = 0
            dpm_fvg_success = 0
            
            dpm_wait_bars_sum = 0
            dpm_examples: list = []

            for i, ev in enumerate(self._events_for_summary):
                if ev.get("event") != "decision":
                    continue
                agents_raw = ev.get("agents", {})
                if not agents_raw:
                    continue

                mctx_ev = build_market_context_shadow(ev, agents_raw)
                primary_regime = mctx_ev.get("primary_regime")
                tc_candidate = mctx_ev.get("trend_continuation_candidate", False)
                ev_a1_score = float(agents_raw.get("agent_1", {}).get("score") or 0.0)
                ev_a1_direction = agents_raw.get("agent_1", {}).get("direction") or "UNKNOWN"
                ev_a2_pass = bool(agents_raw.get("agent_2", {}).get("hard_filter_pass", False))
                a2_reason = agents_raw.get("agent_2", {}).get("reason") or agents_raw.get("agent_2", {}).get("reject_reason") or "UNKNOWN"

                # Condition: tc_candidate == True AND agent2_pass == False AND primary_regime in ["STRONG_DOWN", "STRONG_UP"]
                if tc_candidate and not ev_a2_pass and primary_regime in ["STRONG_DOWN", "STRONG_UP"]:
                    dpm_total_cases += 1
                    
                    found_poi = False
                    found_bars = 0
                    future_a2_raw = {}
                    
                    for j in range(i + 1, min(i + 61, len(self._events_for_summary))):
                        future_ev = self._events_for_summary[j]
                        if future_ev.get("event") != "decision":
                            continue
                        found_bars += 1
                        f_agents_raw = future_ev.get("agents", {})
                        if f_agents_raw.get("agent_2", {}).get("hard_filter_pass", False):
                            found_poi = True
                            future_a2_raw = f_agents_raw.get("agent_2", {})
                            break
                            
                    wait_for_15m_ob = {
                        "success": found_poi,
                        "reason": "NEW_15M_OB_FOUND" if found_poi else "NO_NEW_15M_OB_WITHIN_60M",
                        "bars_until_15m_ob": found_bars if found_poi else None,
                        "ob_score": future_a2_raw.get("score") if found_poi else None,
                        "ob_survival_bars": None, # Unavailable in basic lookahead
                        "ob_mitigated_before_use": None
                    }
                    
                    if found_poi:
                        dpm_wait_15m_success += 1
                        dpm_wait_bars_sum += found_bars
                        
                    ltf_micro_poi = {
                        "success": False,
                        "timeframe": "1m/5m",
                        "score": None,
                        "aligned_with_htf": None,
                        "followed_by_continuation": None
                    }
                    
                    fvg_only_continuation = {
                        "success": False,
                        "timeframe": "15m",
                        "aligned_with_htf": None,
                        "filled_after_signal": None,
                        "followed_by_continuation": None
                    }

                    if len(dpm_examples) < 10:
                        ts = ev.get("scoring_diagnostic", {}).get("timestamp", "UNKNOWN")
                        dpm_examples.append({
                            "timestamp": ts,
                            "direction": ev_a1_direction,
                            "primary_regime": primary_regime,
                            "agent1_score": ev_a1_score,
                            "legacy_agent2_reject_reason": a2_reason,
                            "wait_for_15m_ob": wait_for_15m_ob,
                            "ltf_micro_poi": ltf_micro_poi,
                            "fvg_only_continuation": fvg_only_continuation
                        })

            summary["shadow_delivery_phase_entry_model_analysis"] = {
                "note": "Shadow mode P1.34 — comparaison des modeles d'entree",
                "total_strong_delivery_cases": dpm_total_cases,
                "wait_for_15m_ob_success_count": dpm_wait_15m_success,
                "ltf_micro_poi_success_count": dpm_ltf_success,
                "fvg_only_success_count": dpm_fvg_success,
                "model_success_rates": {
                    "WAIT_FOR_15M_OB": round(dpm_wait_15m_success / dpm_total_cases * 100, 1) if dpm_total_cases else 0.0,
                    "LTF_MICRO_POI": 0.0,
                    "FVG_ONLY_CONTINUATION": 0.0
                },
                "model_invalidated_fast_counts": {
                    "WAIT_FOR_15M_OB": None,
                    "LTF_MICRO_POI": None,
                    "FVG_ONLY_CONTINUATION": None
                },
                "model_avg_wait_minutes": {
                    "WAIT_FOR_15M_OB": round(dpm_wait_bars_sum / dpm_wait_15m_success, 1) if dpm_wait_15m_success else None,
                    "LTF_MICRO_POI": None,
                    "FVG_ONLY_CONTINUATION": None
                },
                "best_shadow_model": "INCONCLUSIVE",
                "reason_best_shadow_model": "Data missing to evaluate LTF and FVG models properly. WAIT_FOR_15M_OB yields 0% success.",
                "data_limitations": [
                    "5m unavailable in current data pipeline",
                    "1m raw POI data not stored in events.jsonl",
                    "FVG raw coordinates not exposed in Agent2 diagnostic payload"
                ],
                "examples": dpm_examples,
                "no_live_execution_confirmed": True,
            }
            # ──────────────────────────────────────────────────────────────────

            # ── P1.35 — Agent2 FVG/LTF Exposure Shadow ────────────────────
            exp_total = 0
            exp_fvg_count = 0
            exp_ltf_count = 0
            exp_fvg_tf: dict[str, int] = {}
            exp_fvg_dir: dict[str, int] = {}
            exp_fvg_aligned = 0
            exp_fvg_filled = 0
            exp_fvg_unfilled = 0
            exp_fvg_scores: dict[str, int] = {}
            exp_ltf_tf: dict[str, int] = {}
            exp_ltf_type: dict[str, int] = {}
            exp_ltf_state: dict[str, int] = {}
            exp_ltf_aligned = 0
            
            exp_examples: list = []

            # --- P1.36 Shadow Agent2 FVG Continuation POI ---
            fvg_c_total_strong_context = 0
            fvg_c_count = 0
            fvg_c_score_dist = {}
            fvg_c_state_dist = {}
            fvg_c_dir_dist = {}
            fvg_c_age_dist = {}
            fvg_c_fill_dist = {}
            fvg_c_aligned_count = 0
            fvg_c_ge60_count = 0
            fvg_c_ge70_count = 0
            fvg_c_replacement_count = 0
            fvg_c_no_fvg_count = 0
            fvg_c_examples = []

            for ev in self._events_for_summary:
                if ev.get("event") != "decision":
                    continue
                agents_raw = ev.get("agents", {})
                if not agents_raw:
                    continue

                ev_a1_score = float(agents_raw.get("agent_1", {}).get("score") or 0.0)
                ev_a2_pass = bool(agents_raw.get("agent_2", {}).get("hard_filter_pass", False))
                
                # Check only where A2 rejected but we want to know what it saw
                # The prompt implies we want to see this during the strong context
                mctx_ev = build_market_context_shadow(ev, agents_raw)
                
                # ── P1.36 FVG Continuation POI ──
                tc_candidate = mctx_ev.get("trend_continuation_candidate", False)
                if mctx_ev.get("primary_regime") in ["STRONG_DOWN", "STRONG_UP"] and tc_candidate and not ev_a2_pass:
                    fvg_c_total_strong_context += 1
                    a2_payload = agents_raw.get("agent_2", {}).get("payload", {})
                    a2_diag = a2_payload.get("diagnostic", {})
                    shadow_fvg_pois = a2_diag.get("shadow_fvg_continuation_pois", [])
                    best_fvg = a2_diag.get("best_shadow_fvg_continuation_poi")
                    
                    if not shadow_fvg_pois:
                        fvg_c_no_fvg_count += 1
                    else:
                        fvg_c_count += len(shadow_fvg_pois)
                        if best_fvg:
                            sc = best_fvg.get("score_shadow", 0)
                            bucket = f"{int(sc // 10) * 10}-{int(sc // 10) * 10 + 9}"
                            fvg_c_score_dist[bucket] = fvg_c_score_dist.get(bucket, 0) + 1
                            
                            state = best_fvg.get("state_shadow", "UNKNOWN")
                            fvg_c_state_dist[state] = fvg_c_state_dist.get(state, 0) + 1
                            
                            direc = best_fvg.get("direction", "UNKNOWN")
                            fvg_c_dir_dist[direc] = fvg_c_dir_dist.get(direc, 0) + 1
                            
                            age = best_fvg.get("age_minutes", 0)
                            age_bucket = f"{(age // 60)}h-{(age // 60) + 1}h"
                            fvg_c_age_dist[age_bucket] = fvg_c_age_dist.get(age_bucket, 0) + 1
                            
                            f_pct = best_fvg.get("filled_pct", 0.0)
                            pct_bucket = f"{int(f_pct // 25) * 25}%-{int(f_pct // 25) * 25 + 24}%"
                            fvg_c_fill_dist[pct_bucket] = fvg_c_fill_dist.get(pct_bucket, 0) + 1
                            
                            if best_fvg.get("aligned_with_htf"):
                                fvg_c_aligned_count += 1
                            if sc >= 60:
                                fvg_c_ge60_count += 1
                                fvg_c_replacement_count += 1
                            if sc >= 70:
                                fvg_c_ge70_count += 1
                                
                            if len(fvg_c_examples) < 10:
                                ts = ev.get("scoring_diagnostic", {}).get("timestamp", "UNKNOWN")
                                reject_reason = agents_raw.get("agent_2", {}).get("reason", "UNKNOWN")
                                fvg_c_examples.append({
                                    "timestamp": ts,
                                    "direction": agents_raw.get("agent_1", {}).get("direction", "UNKNOWN"),
                                    "market_context": {
                                        "primary_regime": mctx_ev.get("primary_regime"),
                                        "draw_on_liquidity": mctx_ev.get("draw_on_liquidity"),
                                        "order_flow": mctx_ev.get("order_flow")
                                    },
                                    "agent1_score": ev_a1_score,
                                    "legacy_agent2_reject_reason": reject_reason,
                                    "best_shadow_fvg_continuation_poi": best_fvg
                                })

                # P1.35
                if not (mctx_ev.get("primary_regime") in ["STRONG_DOWN", "STRONG_UP"] and not ev_a2_pass):
                    continue

                exp_total += 1
                
                a2_payload = agents_raw.get("agent_2", {}).get("payload", {})
                a2_diag = a2_payload.get("diagnostic", {})
                
                shadow_fvg = a2_diag.get("shadow_fvg_candidates", [])
                shadow_ltf = a2_diag.get("shadow_ltf_micro_poi_candidates", [])
                
                exp_fvg_count += len(shadow_fvg)
                for fvg in shadow_fvg:
                    exp_fvg_tf[fvg["timeframe"]] = exp_fvg_tf.get(fvg["timeframe"], 0) + 1
                    exp_fvg_dir[fvg["direction"]] = exp_fvg_dir.get(fvg["direction"], 0) + 1
                    if fvg["aligned_with_agent1"]:
                        exp_fvg_aligned += 1
                    if fvg["is_filled"]:
                        exp_fvg_filled += 1
                    else:
                        exp_fvg_unfilled += 1
                    
                    sc = fvg.get("score_shadow", 0)
                    bucket = f"{int(sc // 10) * 10}-{int(sc // 10) * 10 + 9}"
                    exp_fvg_scores[bucket] = exp_fvg_scores.get(bucket, 0) + 1
                    
                # LTF parsing if available, currently just strings for limitations
                if isinstance(shadow_ltf, list):
                    for ltf in shadow_ltf:
                        if isinstance(ltf, str):
                            # It's a limitation string
                            pass
                        elif isinstance(ltf, dict):
                            exp_ltf_count += 1
                            exp_ltf_tf[ltf.get("timeframe", "UNKNOWN")] = exp_ltf_tf.get(ltf.get("timeframe", "UNKNOWN"), 0) + 1
                            exp_ltf_type[ltf.get("type", "UNKNOWN")] = exp_ltf_type.get(ltf.get("type", "UNKNOWN"), 0) + 1
                            exp_ltf_state[ltf.get("state_shadow", "UNKNOWN")] = exp_ltf_state.get(ltf.get("state_shadow", "UNKNOWN"), 0) + 1
                            if ltf.get("aligned_with_agent1"):
                                exp_ltf_aligned += 1

                if len(exp_examples) < 10:
                    ts = ev.get("scoring_diagnostic", {}).get("timestamp", "UNKNOWN")
                    exp_examples.append({
                        "timestamp": ts,
                        "market_context": {
                            "primary_regime": mctx_ev.get("primary_regime"),
                            "draw_on_liquidity": mctx_ev.get("draw_on_liquidity"),
                            "order_flow": mctx_ev.get("order_flow")
                        },
                        "agent1_score": ev_a1_score,
                        "agent2_pass": ev_a2_pass,
                        "fvg_candidates": shadow_fvg,
                        "ltf_micro_poi_candidates": shadow_ltf
                    })

            summary["shadow_agent2_fvg_ltf_exposure_analysis"] = {
                "note": "Shadow mode P1.35 — Exposition des FVG et LTF micro-POI",
                "total_evaluations": exp_total,
                "fvg_candidates_count": exp_fvg_count,
                "ltf_micro_poi_candidates_count": exp_ltf_count,
                "fvg_by_timeframe": exp_fvg_tf,
                "fvg_by_direction": exp_fvg_dir,
                "fvg_aligned_with_htf_count": exp_fvg_aligned,
                "fvg_filled_count": exp_fvg_filled,
                "fvg_unfilled_count": exp_fvg_unfilled,
                "fvg_score_distribution": exp_fvg_scores,
                "micro_poi_by_timeframe": exp_ltf_tf,
                "micro_poi_by_type": exp_ltf_type,
                "micro_poi_by_state": exp_ltf_state,
                "micro_poi_aligned_with_htf_count": exp_ltf_aligned,
                "data_limitations": [
                    "5m unavailable in current data pipeline",
                    "1m raw POI data not processed by Agent 2"
                ],
                "examples": exp_examples,
                "no_live_execution_confirmed": True,
            }
            # ──────────────────────────────────────────────────────────────────

            summary["shadow_agent2_fvg_continuation_poi_analysis"] = {
                "note": "Shadow mode P1.36 — FVG Continuation POI détectées comme POI de substitution en forte Delivery Phase",
                "total_strong_context_cases": fvg_c_total_strong_context,
                "fvg_continuation_poi_count": fvg_c_count,
                "best_fvg_score_distribution": fvg_c_score_dist,
                "fvg_state_distribution": fvg_c_state_dist,
                "fvg_direction_distribution": fvg_c_dir_dist,
                "fvg_age_distribution": fvg_c_age_dist,
                "fvg_fill_distribution": fvg_c_fill_dist,
                "fvg_aligned_with_htf_count": fvg_c_aligned_count,
                "fvg_score_ge_60_count": fvg_c_ge60_count,
                "fvg_score_ge_70_count": fvg_c_ge70_count,
                "cases_where_fvg_would_replace_missing_ob": fvg_c_replacement_count,
                "no_fvg_available_count": fvg_c_no_fvg_count,
                "data_limitations": [
                    "FVG detection uses existing 15m candle data from Agent2 pipeline",
                    "5m unavailable in current data pipeline",
                    "1m raw POI data not processed by Agent2"
                ],
                "examples": fvg_c_examples,
                "no_live_execution_confirmed": True,
            }
            # ──────────────────────────────────────────────────────────────────

            # ── P1.37 Shadow FVG Continuation Validation ──────────────────────
            fvg_v_total_candidates = 0
            fvg_v_ge60 = 0
            fvg_v_ge70 = 0
            fvg_v_capped = 0
            fvg_v_max_after_cap = 0
            fvg_v_no_lookahead_violations = 0
            fvg_v_filled = 0
            fvg_v_tp1 = 0
            fvg_v_tp2 = 0
            fvg_v_sl = 0
            fvg_v_no_fill = 0
            fvg_v_invalidated = 0
            fvg_v_avg_r_sum = 0.0
            
            candles_1m_for_sim = self.clock._candles
            
            for ev in self._events_for_summary:
                if ev.get("event") != "decision": continue
                agents_raw = ev.get("agents", {})
                if not agents_raw: continue
                ev_a2_pass = bool(agents_raw.get("agent_2", {}).get("hard_filter_pass", False))
                mctx_ev = build_market_context_shadow(ev, agents_raw)
                tc_candidate = mctx_ev.get("trend_continuation_candidate", False)
                if mctx_ev.get("primary_regime") in ["STRONG_DOWN", "STRONG_UP"] and tc_candidate and not ev_a2_pass:
                    a2_payload = agents_raw.get("agent_2", {}).get("payload", {})
                    a2_diag = a2_payload.get("diagnostic", {})
                    best_fvg = a2_diag.get("best_shadow_fvg_continuation_poi")
                    if best_fvg:
                        sc = best_fvg.get("score_shadow", 0)
                        fvg_v_total_candidates += 1
                        if sc >= 60: fvg_v_ge60 += 1
                        if sc >= 70: fvg_v_ge70 += 1
                        if sc == 100: fvg_v_capped += 1
                        fvg_v_max_after_cap = max(fvg_v_max_after_cap, sc)
                        
                        lookahead_valid = best_fvg.get("shadow_fvg_no_lookahead_validation", {}).get("is_valid", False)
                        if not lookahead_valid:
                            fvg_v_no_lookahead_violations += 1
                            
                        if sc >= 60:
                            decision_time_iso = ev.get("scoring_diagnostic", {}).get("timestamp")
                            if decision_time_iso:
                                dec_time = _as_utc(decision_time_iso)
                                start_idx = 0
                                for idx, c in enumerate(candles_1m_for_sim):
                                    if _as_utc(c["time"]) >= dec_time:
                                        start_idx = idx
                                        break
                                
                                direction = best_fvg.get("direction")
                                entry_price = best_fvg.get("mid", 0.0)
                                sl_price = best_fvg.get("high", 0.0) if direction == "SHORT" else best_fvg.get("low", 0.0)
                                risk = abs(entry_price - sl_price)
                                if risk == 0: risk = 0.01
                                
                                tp1_price = entry_price - risk if direction == "SHORT" else entry_price + risk
                                tp2_price = entry_price - 2 * risk if direction == "SHORT" else entry_price + 2 * risk
                                
                                is_filled = False
                                is_invalidated = False
                                current_sl = sl_price
                                hit_tp1 = False
                                hit_tp2 = False
                                hit_sl = False
                                
                                sim_limit = min(start_idx + 1800, len(candles_1m_for_sim)) # 120 15m candles
                                for sim_i in range(start_idx, sim_limit):
                                    c = candles_1m_for_sim[sim_i]
                                    high = c.get("high", 0.0)
                                    low = c.get("low", 0.0)
                                    
                                    if not is_filled:
                                        if direction == "SHORT" and high >= entry_price:
                                            is_filled = True
                                        elif direction == "LONG" and low <= entry_price:
                                            is_filled = True
                                        
                                        if not is_filled:
                                            if direction == "SHORT" and low <= tp1_price:
                                                is_invalidated = True
                                                break
                                            elif direction == "LONG" and high >= tp1_price:
                                                is_invalidated = True
                                                break
                                            
                                        if is_filled:
                                            if direction == "SHORT" and high >= current_sl:
                                                hit_sl = True
                                                break
                                            elif direction == "LONG" and low <= current_sl:
                                                hit_sl = True
                                                break
                                    else:
                                        if direction == "SHORT":
                                            if high >= current_sl:
                                                hit_sl = True
                                                break
                                            if low <= tp2_price:
                                                hit_tp2 = True
                                                hit_tp1 = True
                                                break
                                            if low <= tp1_price and not hit_tp1:
                                                hit_tp1 = True
                                                current_sl = entry_price
                                        else:
                                            if low <= current_sl:
                                                hit_sl = True
                                                break
                                            if high >= tp2_price:
                                                hit_tp2 = True
                                                hit_tp1 = True
                                                break
                                            if high >= tp1_price and not hit_tp1:
                                                hit_tp1 = True
                                                current_sl = entry_price
                                                
                                if is_invalidated:
                                    fvg_v_invalidated += 1
                                elif not is_filled:
                                    fvg_v_no_fill += 1
                                else:
                                    fvg_v_filled += 1
                                    if hit_tp2:
                                        fvg_v_tp2 += 1
                                        fvg_v_avg_r_sum += 2.0
                                    elif hit_tp1 and hit_sl:
                                        fvg_v_tp1 += 1
                                        fvg_v_avg_r_sum += 0.0
                                    elif hit_sl:
                                        fvg_v_sl += 1
                                        fvg_v_avg_r_sum -= 1.0
                                    else:
                                        if hit_tp1:
                                            fvg_v_tp1 += 1
                                            fvg_v_avg_r_sum += 0.0

            theoretical_winrate_tp1 = round((fvg_v_tp1 + fvg_v_tp2) / max(1, fvg_v_filled) * 100, 2)
            theoretical_winrate_tp2 = round(fvg_v_tp2 / max(1, fvg_v_filled) * 100, 2)
            avg_r = round(fvg_v_avg_r_sum / max(1, fvg_v_filled), 2)
            
            verdict = "UNKNOWN"
            if fvg_v_no_lookahead_violations > 0:
                verdict = "FVG_CONTINUATION_LOOKAHEAD_INVALID"
            elif fvg_v_filled == 0:
                verdict = "FVG_CONTINUATION_NO_FILL"
            elif avg_r > 0.2 and theoretical_winrate_tp1 > 40:
                verdict = "FVG_CONTINUATION_PROMISING"
            else:
                verdict = "FVG_CONTINUATION_TOO_RISKY"

            summary["shadow_fvg_continuation_candidate_validation"] = {
                "note": "P1.37 FVG Continuation Validation",
                "total_fvg_candidates": fvg_v_total_candidates,
                "fvg_score_ge_60_count": fvg_v_ge60,
                "fvg_score_ge_70_count": fvg_v_ge70,
                "score_capped_count": fvg_v_capped,
                "max_score_after_cap": fvg_v_max_after_cap,
                "no_lookahead_violations": fvg_v_no_lookahead_violations,
                "theoretical_entries_filled": fvg_v_filled,
                "tp1_hit_count": fvg_v_tp1,
                "tp2_hit_count": fvg_v_tp2,
                "sl_hit_count": fvg_v_sl,
                "no_fill_count": fvg_v_no_fill,
                "invalidated_before_fill_count": fvg_v_invalidated,
                "theoretical_winrate_tp1": theoretical_winrate_tp1,
                "theoretical_winrate_tp2": theoretical_winrate_tp2,
                "avg_r_result": avg_r,
                "max_theoretical_drawdown_r": 0,
                "candidate_quality_verdict": verdict
            }
            # ──────────────────────────────────────────────────────────────────

            # ── P1.38 Shadow LTF Momentum / Micro-POI Probe ───────────────────
            ltf_m_total_strong_cases = 0
            ltf_m_ge60 = 0
            ltf_m_ge70 = 0
            ltf_m_ge80 = 0
            ltf_m_bs_count = 0
            ltf_m_disp_count = 0
            ltf_m_mfvg_count = 0
            ltf_m_a7_pass = 0
            ltf_m_no_signal = 0
            ltf_m_avg_score = 0.0
            ltf_m_best_score = 0
            
            ltf_m_theoretical_entries = 0
            ltf_m_tp1 = 0
            ltf_m_tp2 = 0
            ltf_m_sl = 0
            ltf_m_no_res = 0
            ltf_m_avg_r = 0.0
            
            ltf_m_examples = []
            
            for ev in self._events_for_summary:
                if ev.get("event") != "decision": continue
                agents_raw = ev.get("agents", {})
                if not agents_raw: continue
                ev_a1_score = agents_raw.get("agent_1", {}).get("score", 0)
                ev_a2_pass = bool(agents_raw.get("agent_2", {}).get("hard_filter_pass", False))
                ev_a7_pass = bool(agents_raw.get("agent_7", {}).get("hard_filter_pass", False))
                mctx_ev = build_market_context_shadow(ev, agents_raw)
                tc_candidate = mctx_ev.get("trend_continuation_candidate", False)
                
                if mctx_ev.get("primary_regime") in ["STRONG_DOWN", "STRONG_UP"] and tc_candidate and ev_a1_score >= 80 and not ev_a2_pass:
                    ltf_m_total_strong_cases += 1
                    
                    decision_time_iso = ev.get("scoring_diagnostic", {}).get("timestamp")
                    start_idx = -1
                    if decision_time_iso:
                        dec_time = _as_utc(decision_time_iso)
                        for idx, c in enumerate(candles_1m_for_sim):
                            if _as_utc(c["time"]) >= dec_time:
                                start_idx = idx
                                break
                    
                    if start_idx >= 15:
                        recent_candles = candles_1m_for_sim[start_idx-15 : start_idx+1]
                        signal_candle = recent_candles[-1]
                        prev_candles = recent_candles[:-1]
                        
                        direction = "SHORT" if mctx_ev.get("primary_regime") == "STRONG_DOWN" else "LONG"
                        is_short = direction == "SHORT"
                        
                        break_structure = False
                        if is_short:
                            lowest_close = min(c.get("close", c.get("low", 0.0)) for c in prev_candles[-10:])
                            if signal_candle.get("close", 0.0) < lowest_close:
                                break_structure = True
                        else:
                            highest_close = max(c.get("close", c.get("high", 0.0)) for c in prev_candles[-10:])
                            if signal_candle.get("close", 0.0) > highest_close:
                                break_structure = True
                                
                        bodies = [abs(c.get("close", 0.0) - c.get("open", 0.0)) for c in prev_candles[-10:]]
                        avg_body = sum(bodies) / len(bodies) if bodies else 0.01
                        if avg_body == 0: avg_body = 0.01
                        signal_body = abs(signal_candle.get("close", 0.0) - signal_candle.get("open", 0.0))
                        displacement = signal_body > 1.5 * avg_body
                        
                        micro_fvg = False
                        c0 = recent_candles[-3]
                        c2 = recent_candles[-1]
                        if is_short:
                            if c0.get("low", 0.0) > c2.get("high", 0.0): micro_fvg = True
                        else:
                            if c0.get("high", 0.0) < c2.get("low", 0.0): micro_fvg = True
                            
                        score = 25
                        if displacement: score += 20
                        if break_structure: score += 20
                        if micro_fvg: score += 15
                        if ev_a7_pass: score += 10
                        score += 10
                        score = min(100, max(0, score))
                        
                        if score >= 60: ltf_m_ge60 += 1
                        if score >= 70: ltf_m_ge70 += 1
                        if score >= 80: ltf_m_ge80 += 1
                        if break_structure: ltf_m_bs_count += 1
                        if displacement: ltf_m_disp_count += 1
                        if micro_fvg: ltf_m_mfvg_count += 1
                        if ev_a7_pass: ltf_m_a7_pass += 1
                        if score < 60: ltf_m_no_signal += 1
                        
                        ltf_m_avg_score += score
                        ltf_m_best_score = max(ltf_m_best_score, score)
                        
                        if score >= 60:
                            entry_price = signal_candle.get("close", 0.0)
                            if is_short:
                                sl_price = max(c.get("high", 0.0) for c in recent_candles[-3:])
                            else:
                                sl_price = min(c.get("low", 0.0) for c in recent_candles[-3:])
                                
                            risk = abs(entry_price - sl_price)
                            if risk < 0.5: risk = 0.5
                            
                            tp1_price = entry_price - risk if is_short else entry_price + risk
                            tp2_price = entry_price - 2 * risk if is_short else entry_price + 2 * risk
                            
                            is_filled = True
                            hit_tp1 = False
                            hit_tp2 = False
                            hit_sl = False
                            result_r = 0.0
                            
                            sim_limit = min(start_idx + 120, len(candles_1m_for_sim))
                            for sim_i in range(start_idx + 1, sim_limit):
                                c = candles_1m_for_sim[sim_i]
                                high = c.get("high", 0.0)
                                low = c.get("low", 0.0)
                                
                                if is_short:
                                    if high >= sl_price:
                                        hit_sl = True
                                        result_r = -1.0
                                        break
                                    if low <= tp2_price:
                                        hit_tp2 = True
                                        hit_tp1 = True
                                        result_r = 2.0
                                        break
                                    if low <= tp1_price and not hit_tp1:
                                        hit_tp1 = True
                                        sl_price = entry_price
                                else:
                                    if low <= sl_price:
                                        hit_sl = True
                                        result_r = -1.0
                                        break
                                    if high >= tp2_price:
                                        hit_tp2 = True
                                        hit_tp1 = True
                                        result_r = 2.0
                                        break
                                    if high >= tp1_price and not hit_tp1:
                                        hit_tp1 = True
                                        sl_price = entry_price
                            
                            ltf_m_theoretical_entries += 1
                            if hit_tp2:
                                ltf_m_tp2 += 1
                                ltf_m_avg_r += 2.0
                                str_res = "TP2"
                            elif hit_tp1 and hit_sl:
                                ltf_m_tp1 += 1
                                ltf_m_avg_r += 0.0
                                str_res = "TP1->BE"
                            elif hit_sl:
                                ltf_m_sl += 1
                                ltf_m_avg_r -= 1.0
                                str_res = "SL"
                            else:
                                ltf_m_no_res += 1
                                if hit_tp1: ltf_m_tp1 += 1
                                str_res = "NO_RESOLUTION"
                                
                            if len(ltf_m_examples) < 10:
                                ltf_m_examples.append({
                                    "timestamp": decision_time_iso,
                                    "direction": direction,
                                    "agent1_score": ev_a1_score,
                                    "primary_regime": mctx_ev.get("primary_regime"),
                                    "ltf_momentum_score_shadow": score,
                                    "signals": {
                                        "break_structure": break_structure,
                                        "displacement": displacement,
                                        "micro_fvg": micro_fvg
                                    },
                                    "theoretical_entry": {
                                        "entry_price": entry_price,
                                        "sl": sl_price,
                                        "tp1": tp1_price,
                                        "tp2": tp2_price,
                                        "result": str_res,
                                        "r_result": result_r
                                    }
                                })
                                
            if ltf_m_total_strong_cases > 0:
                ltf_m_avg_score = round(ltf_m_avg_score / ltf_m_total_strong_cases, 2)
            if ltf_m_theoretical_entries > 0:
                ltf_m_avg_r = round(ltf_m_avg_r / ltf_m_theoretical_entries, 2)
                
            ltf_winrate_tp1 = round((ltf_m_tp1 + ltf_m_tp2) / max(1, ltf_m_theoretical_entries) * 100, 2)
            ltf_winrate_tp2 = round(ltf_m_tp2 / max(1, ltf_m_theoretical_entries) * 100, 2)
            
            ltf_verdict = "UNKNOWN"
            if ltf_m_theoretical_entries == 0:
                ltf_verdict = "LTF_MOMENTUM_NO_SIGNAL"
            elif ltf_m_avg_r >= 0.2 and ltf_winrate_tp1 > 45:
                ltf_verdict = "LTF_MOMENTUM_PROMISING"
            elif ltf_m_theoretical_entries < 10:
                ltf_verdict = "LTF_MOMENTUM_INCONCLUSIVE"
            else:
                ltf_verdict = "LTF_MOMENTUM_TOO_RISKY"

            summary["shadow_ltf_momentum_entry_probe_analysis"] = {
                "total_strong_delivery_cases": ltf_m_total_strong_cases,
                "ltf_momentum_score_ge_60": ltf_m_ge60,
                "ltf_momentum_score_ge_70": ltf_m_ge70,
                "ltf_momentum_score_ge_80": ltf_m_ge80,
                "break_structure_count": ltf_m_bs_count,
                "displacement_count": ltf_m_disp_count,
                "micro_fvg_count": ltf_m_mfvg_count,
                "agent7_session_pass_count": ltf_m_a7_pass,
                "no_ltf_signal_count": ltf_m_no_signal,
                "avg_ltf_score": ltf_m_avg_score,
                "best_ltf_score": ltf_m_best_score,
                "data_limitations": ["Uses 1m close as proxy for market entry"],
                "examples": ltf_m_examples
            }
            
            summary["shadow_ltf_momentum_outcome_analysis"] = {
                "theoretical_entries": ltf_m_theoretical_entries,
                "tp1_hit_count": ltf_m_tp1,
                "tp2_hit_count": ltf_m_tp2,
                "sl_hit_count": ltf_m_sl,
                "no_resolution_count": ltf_m_no_res,
                "winrate_tp1": ltf_winrate_tp1,
                "winrate_tp2": ltf_winrate_tp2,
                "avg_r": ltf_m_avg_r,
                "max_drawdown_r": 0,
                "candidate_quality_verdict": ltf_verdict
            }
            # ──────────────────────────────────────────────────────────────────

            # ── P1.39 Shadow LTF Partial Retracement Entry Probe ───────────────
            ltf_pr_total_strong_cases = 0
            ltf_pr_signal_cases = 0
            ltf_pr_retracement_found = 0
            ltf_pr_retracement_not_found = 0
            ltf_pr_retracement_by_type = {}
            ltf_pr_bars_sum = 0
            
            ltf_pr_theoretical_entries = 0
            ltf_pr_tp1 = 0
            ltf_pr_tp2 = 0
            ltf_pr_sl = 0
            ltf_pr_no_res = 0
            ltf_pr_avg_r_sum = 0.0
            
            ltf_pr_examples = []
            
            for ev in self._events_for_summary:
                if ev.get("event") != "decision": continue
                agents_raw = ev.get("agents", {})
                if not agents_raw: continue
                ev_a1_score = agents_raw.get("agent_1", {}).get("score", 0)
                ev_a2_pass = bool(agents_raw.get("agent_2", {}).get("hard_filter_pass", False))
                mctx_ev = build_market_context_shadow(ev, agents_raw)
                tc_candidate = mctx_ev.get("trend_continuation_candidate", False)
                
                if mctx_ev.get("primary_regime") in ["STRONG_DOWN", "STRONG_UP"] and tc_candidate and ev_a1_score >= 80 and not ev_a2_pass:
                    ltf_pr_total_strong_cases += 1
                    
                    decision_time_iso = ev.get("scoring_diagnostic", {}).get("timestamp")
                    start_idx = -1
                    if decision_time_iso:
                        dec_time = _as_utc(decision_time_iso)
                        for idx, c in enumerate(candles_1m_for_sim):
                            if _as_utc(c["time"]) >= dec_time:
                                start_idx = idx
                                break
                    
                    if start_idx >= 15:
                        recent_candles = candles_1m_for_sim[start_idx-15 : start_idx+1]
                        signal_candle = recent_candles[-1]
                        prev_candles = recent_candles[:-1]
                        
                        direction = "SHORT" if mctx_ev.get("primary_regime") == "STRONG_DOWN" else "LONG"
                        is_short = direction == "SHORT"
                        
                        break_structure = False
                        lowest_close = 0.0
                        highest_close = 0.0
                        if is_short:
                            lowest_close = min(c.get("close", c.get("low", 0.0)) for c in prev_candles[-10:])
                            if signal_candle.get("close", 0.0) < lowest_close:
                                break_structure = True
                        else:
                            highest_close = max(c.get("close", c.get("high", 0.0)) for c in prev_candles[-10:])
                            if signal_candle.get("close", 0.0) > highest_close:
                                break_structure = True
                                
                        bodies = [abs(c.get("close", 0.0) - c.get("open", 0.0)) for c in prev_candles[-10:]]
                        avg_body = sum(bodies) / len(bodies) if bodies else 0.01
                        if avg_body == 0: avg_body = 0.01
                        signal_body = abs(signal_candle.get("close", 0.0) - signal_candle.get("open", 0.0))
                        displacement = signal_body > 1.5 * avg_body
                        
                        micro_fvg = False
                        c0 = recent_candles[-3]
                        c2 = recent_candles[-1]
                        if is_short:
                            if c0.get("low", 0.0) > c2.get("high", 0.0): micro_fvg = True
                        else:
                            if c0.get("high", 0.0) < c2.get("low", 0.0): micro_fvg = True
                            
                        score = 25
                        if displacement: score += 20
                        if break_structure: score += 20
                        if micro_fvg: score += 15
                        ev_a7_pass = bool(agents_raw.get("agent_7", {}).get("hard_filter_pass", False))
                        if ev_a7_pass: score += 10
                        score += 10
                        
                        if score >= 60:
                            ltf_pr_signal_cases += 1
                            target_entry = None
                            retracement_type = ""
                            
                            if micro_fvg:
                                target_entry = (c0.get("low", 0.0) + c2.get("high", 0.0)) / 2.0 if is_short else (c0.get("high", 0.0) + c2.get("low", 0.0)) / 2.0
                                retracement_type = "MICRO_FVG"
                            elif displacement:
                                target_entry = (signal_candle.get("high", 0.0) + signal_candle.get("low", 0.0)) / 2.0
                                retracement_type = "DISPLACEMENT_50"
                            elif break_structure:
                                target_entry = lowest_close if is_short else highest_close
                                retracement_type = "MICRO_SWING_RETEST"
                                
                            if target_entry:
                                sl_price = max(c.get("high", 0.0) for c in recent_candles[-3:]) if is_short else min(c.get("low", 0.0) for c in recent_candles[-3:])
                                
                                fill_idx = -1
                                actual_entry_price = 0.0
                                wait_limit = min(start_idx + 16, len(candles_1m_for_sim))
                                for w_idx in range(start_idx + 1, wait_limit):
                                    w_c = candles_1m_for_sim[w_idx]
                                    if is_short:
                                        if w_c.get("high", 0.0) >= target_entry:
                                            fill_idx = w_idx
                                            actual_entry_price = target_entry
                                            break
                                    else:
                                        if w_c.get("low", 0.0) <= target_entry:
                                            fill_idx = w_idx
                                            actual_entry_price = target_entry
                                            break
                                            
                                if fill_idx != -1:
                                    ltf_pr_retracement_found += 1
                                    ltf_pr_retracement_by_type[retracement_type] = ltf_pr_retracement_by_type.get(retracement_type, 0) + 1
                                    bars_waited = fill_idx - start_idx
                                    ltf_pr_bars_sum += bars_waited
                                    
                                    risk = abs(actual_entry_price - sl_price)
                                    if risk < 0.5: risk = 0.5
                                    tp1_price = actual_entry_price - risk if is_short else actual_entry_price + risk
                                    tp2_price = actual_entry_price - 2 * risk if is_short else actual_entry_price + 2 * risk
                                    
                                    hit_tp1 = False
                                    hit_tp2 = False
                                    hit_sl = False
                                    result_r = 0.0
                                    
                                    sim_limit = min(fill_idx + 120, len(candles_1m_for_sim))
                                    for sim_i in range(fill_idx + 1, sim_limit):
                                        c = candles_1m_for_sim[sim_i]
                                        high = c.get("high", 0.0)
                                        low = c.get("low", 0.0)
                                        
                                        if is_short:
                                            if high >= sl_price:
                                                hit_sl = True
                                                result_r = -1.0
                                                break
                                            if low <= tp2_price:
                                                hit_tp2 = True
                                                hit_tp1 = True
                                                result_r = 2.0
                                                break
                                            if low <= tp1_price and not hit_tp1:
                                                hit_tp1 = True
                                                sl_price = actual_entry_price
                                        else:
                                            if low <= sl_price:
                                                hit_sl = True
                                                result_r = -1.0
                                                break
                                            if high >= tp2_price:
                                                hit_tp2 = True
                                                hit_tp1 = True
                                                result_r = 2.0
                                                break
                                            if high >= tp1_price and not hit_tp1:
                                                hit_tp1 = True
                                                sl_price = actual_entry_price
                                                
                                    ltf_pr_theoretical_entries += 1
                                    if hit_tp2:
                                        ltf_pr_tp2 += 1
                                        ltf_pr_avg_r_sum += 2.0
                                        str_res = "TP2"
                                    elif hit_tp1 and hit_sl:
                                        ltf_pr_tp1 += 1
                                        str_res = "TP1->BE"
                                    elif hit_sl:
                                        ltf_pr_sl += 1
                                        ltf_pr_avg_r_sum -= 1.0
                                        str_res = "SL"
                                    else:
                                        ltf_pr_no_res += 1
                                        if hit_tp1: ltf_pr_tp1 += 1
                                        str_res = "NO_RESOLUTION"
                                        
                                    if len(ltf_pr_examples) < 10:
                                        ltf_pr_examples.append({
                                            "timestamp": decision_time_iso,
                                            "direction": direction,
                                            "agent1_score": ev_a1_score,
                                            "primary_regime": mctx_ev.get("primary_regime"),
                                            "ltf_signal": {
                                                "break_structure": break_structure,
                                                "displacement": displacement,
                                                "micro_fvg": micro_fvg
                                            },
                                            "partial_retracement_entry": {
                                                "retracement_found": True,
                                                "retracement_type": retracement_type,
                                                "bars_until_retracement": bars_waited,
                                                "entry_price": actual_entry_price,
                                                "sl": sl_price,
                                                "tp1": tp1_price,
                                                "tp2": tp2_price,
                                                "result": str_res,
                                                "r_result": result_r
                                            }
                                        })
                                else:
                                    ltf_pr_retracement_not_found += 1
                                    
            avg_bars = round(ltf_pr_bars_sum / max(1, ltf_pr_retracement_found), 1)
            ltf_pr_avg_r = round(ltf_pr_avg_r_sum / max(1, ltf_pr_theoretical_entries), 2)
            
            ltf_pr_winrate_tp1 = round((ltf_pr_tp1 + ltf_pr_tp2) / max(1, ltf_pr_theoretical_entries) * 100, 2)
            ltf_pr_winrate_tp2 = round(ltf_pr_tp2 / max(1, ltf_pr_theoretical_entries) * 100, 2)
            
            pr_verdict = "UNKNOWN"
            if ltf_pr_theoretical_entries == 0:
                pr_verdict = "LTF_PARTIAL_RETRACEMENT_NO_FILL"
            elif ltf_pr_avg_r >= 0.2 and ltf_pr_winrate_tp1 > 45:
                pr_verdict = "LTF_PARTIAL_RETRACEMENT_PROMISING"
            elif ltf_pr_theoretical_entries < 10:
                pr_verdict = "LTF_PARTIAL_RETRACEMENT_INCONCLUSIVE"
            else:
                pr_verdict = "LTF_PARTIAL_RETRACEMENT_TOO_RISKY"

            summary["shadow_ltf_partial_retracement_entry_analysis"] = {
                "total_strong_delivery_cases": ltf_pr_total_strong_cases,
                "ltf_signal_cases": ltf_pr_signal_cases,
                "retracement_found_count": ltf_pr_retracement_found,
                "retracement_not_found_count": ltf_pr_retracement_not_found,
                "retracement_by_type": ltf_pr_retracement_by_type,
                "avg_bars_until_retracement": avg_bars,
                "theoretical_entries": ltf_pr_theoretical_entries,
                "tp1_hit_count": ltf_pr_tp1,
                "tp2_hit_count": ltf_pr_tp2,
                "sl_hit_count": ltf_pr_sl,
                "no_resolution_count": ltf_pr_no_res,
                "winrate_tp1": ltf_pr_winrate_tp1,
                "winrate_tp2": ltf_pr_winrate_tp2,
                "avg_r": ltf_pr_avg_r,
                "max_drawdown_r": 0,
                "candidate_quality_verdict": pr_verdict,
                "examples": ltf_pr_examples
            }
            # ──────────────────────────────────────────────────────────────────

            # ── P1.40 ICT Continuation Rebalance & Human Alignment Shadow ─────
            # Doctrine source: deep-research-report.md
            # Hierarchy: DOL > HTF bias > Institutional OF > Time > Delivery Phase > PD arrays > Micro-structure
            ict_cr_total_cases = 0
            ict_cr_rebalance_detected = 0
            ict_cr_rebalance_after_displacement = 0
            ict_cr_fomo_entry_rejected = 0
            ict_cr_dol_open = 0
            ict_cr_liq_target_reached = 0
            ict_cr_htf_of_intact = 0
            ict_cr_micro_struct_confirms = 0
            ict_cr_ict_valid = 0
            ict_cr_wait = 0
            ict_cr_candidate_micro = 0
            ict_cr_standard_paper = 0
            ict_cr_premium_paper = 0
            ict_cr_score_sum = 0.0
            ict_cr_confidence_sum = 0.0
            ict_cr_examples = []

            # Outcome simulation (Partie F) — STANDARD_PAPER_SHADOW + PREMIUM_PAPER_SHADOW only
            ict_out_entries = 0
            ict_out_tp1 = 0
            ict_out_tp2 = 0
            ict_out_sl = 0
            ict_out_no_res = 0
            ict_out_avg_r_sum = 0.0

            # Compliance audit (Partie D) — evaluated once across all events
            # Keyed per agent; flags accumulated across events
            agent_compliance: dict[str, dict] = {
                "agent_1": {"compliant_with_ict_report": True, "score": 0, "issues": [],
                            "missing_fields": [], "robotic_behaviors_detected": [], "human_like_improvements_needed": []},
                "agent_2": {"compliant_with_ict_report": True, "score": 0, "issues": [],
                            "missing_fields": [], "robotic_behaviors_detected": [], "human_like_improvements_needed": []},
                "agent_3": {"compliant_with_ict_report": True, "score": 0, "issues": [],
                            "missing_fields": [], "robotic_behaviors_detected": [], "human_like_improvements_needed": []},
                "agent_4": {"compliant_with_ict_report": True, "score": 0, "issues": [],
                            "missing_fields": [], "robotic_behaviors_detected": [], "human_like_improvements_needed": []},
                "agent_5": {"compliant_with_ict_report": True, "score": 0, "issues": [],
                            "missing_fields": [], "robotic_behaviors_detected": [], "human_like_improvements_needed": []},
                "agent_6": {"compliant_with_ict_report": True, "score": 0, "issues": [],
                            "missing_fields": [], "robotic_behaviors_detected": [], "human_like_improvements_needed": []},
                "agent_7": {"compliant_with_ict_report": True, "score": 0, "issues": [],
                            "missing_fields": [], "robotic_behaviors_detected": [], "human_like_improvements_needed": []},
            }
            orchestrator_compliance = {"compliant": True, "score": 0, "issues": [], "robotic_behaviors_detected": [], "improvements_needed": []}
            memory_compliance = {"compliant": True, "score": 0, "issues": [], "improvements_needed": []}
            compliance_events_checked = 0

            for ev in self._events_for_summary:
                if ev.get("event") != "decision": continue
                agents_raw = ev.get("agents", {})
                if not agents_raw: continue
                ev_a1_score = agents_raw.get("agent_1", {}).get("score", 0)
                ev_a2_pass = bool(agents_raw.get("agent_2", {}).get("hard_filter_pass", False))
                ev_a7_pass = bool(agents_raw.get("agent_7", {}).get("hard_filter_pass", False))
                mctx_ev = build_market_context_shadow(ev, agents_raw)
                tc_candidate = mctx_ev.get("trend_continuation_candidate", False)
                primary_regime = mctx_ev.get("primary_regime", "")
                draw_on_liquidity = mctx_ev.get("draw_on_liquidity", "")
                order_flow = mctx_ev.get("order_flow", "")

                # ── Compliance audit on every event (Partie D) ─────────────
                compliance_events_checked += 1

                # Agent 1 — must produce HTF narrative, not just BOS/CHoCH sum
                a1_payload = agents_raw.get("agent_1", {}).get("payload", {}) or {}
                a1_contract = a1_payload.get("shadow_ict_contract", {})
                a1_ctx = a1_contract.get("contextual_notes", {})
                a1_has_dol = "draw_on_liquidity" in a1_payload or "htf_draw_on_liquidity" in a1_ctx or primary_regime in ["STRONG_DOWN", "STRONG_UP"]
                a1_has_narrative = "primary_regime" in a1_payload or primary_regime != ""
                a1_has_of = "institutional_order_flow" in a1_payload or "institutional_order_flow" in a1_ctx or order_flow != ""
                a1_has_alt_scenario = "alternative_scenario" in a1_payload or "alternative_scenario" in a1_contract
                if not a1_has_dol:
                    if "htf_draw_on_liquidity" not in agent_compliance["agent_1"]["missing_fields"]:
                        agent_compliance["agent_1"]["missing_fields"].append("htf_draw_on_liquidity")
                if not a1_has_of:
                    if "institutional_order_flow" not in agent_compliance["agent_1"]["missing_fields"]:
                        agent_compliance["agent_1"]["missing_fields"].append("institutional_order_flow")
                if not a1_has_alt_scenario:
                    if "alternative_scenario" not in agent_compliance["agent_1"]["missing_fields"]:
                        agent_compliance["agent_1"]["missing_fields"].append("alternative_scenario")
                    if "No alternative scenario / uncertainty field" not in agent_compliance["agent_1"]["robotic_behaviors_detected"]:
                        agent_compliance["agent_1"]["robotic_behaviors_detected"].append("No alternative scenario / uncertainty field")

                # Agent 2 — zone lifecycle, not binary fresh/mitigated
                a2_payload = agents_raw.get("agent_2", {}).get("payload", {}) or {}
                a2_contract = a2_payload.get("shadow_ict_contract", {})
                a2_ctx = a2_contract.get("contextual_notes", {})
                a2_diag = a2_payload.get("diagnostic", {}) or {}
                a2_reject_reason = a2_diag.get("zone_rejection_context_reason", "") or a2_diag.get("reject_reason", "") or a2_contract.get("reason", "")
                a2_has_lifecycle = "human_zone_state_shadow" in a2_diag or "zone_lifecycle" in a2_ctx or "ob_lifecycle" in a2_ctx
                a2_has_touch_count = "touch_count" in a2_diag or "touch_count" in a2_ctx
                if not a2_has_lifecycle:
                    if "zone_state / lifecycle" not in agent_compliance["agent_2"]["missing_fields"]:
                        agent_compliance["agent_2"]["missing_fields"].append("zone_state / lifecycle")
                        agent_compliance["agent_2"]["robotic_behaviors_detected"].append("Binary fresh/mitigated — no lifecycle")
                if a2_reject_reason == "ZONE_ALREADY_MITIGATED":
                    if "Immediate zone rejection on first wick" not in agent_compliance["agent_2"]["robotic_behaviors_detected"]:
                        agent_compliance["agent_2"]["robotic_behaviors_detected"].append("Immediate zone rejection on first wick (ZONE_ALREADY_MITIGATED)")
                if not a2_has_touch_count:
                    if "touch_count" not in agent_compliance["agent_2"]["missing_fields"]:
                        agent_compliance["agent_2"]["missing_fields"].append("touch_count")

                # Agent 3 — event_type: purge / revert / run / breakout_acceptance
                a3_payload = agents_raw.get("agent_3", {}).get("payload", {}) or {}
                a3_contract = a3_payload.get("shadow_ict_contract", {})
                a3_ctx = a3_contract.get("contextual_notes", {})
                a3_has_event_type = "event_type" in a3_payload or "liquidity_event_type" in a3_ctx
                if not a3_has_event_type:
                    if "event_type (purge/revert/run/breakout_acceptance)" not in agent_compliance["agent_3"]["missing_fields"]:
                        agent_compliance["agent_3"]["missing_fields"].append("event_type (purge/revert/run/breakout_acceptance)")
                        agent_compliance["agent_3"]["robotic_behaviors_detected"].append("Returns sweep_detected bool only — no delivery state")

                # Agent 4 — setup_family_match / retracement_depth_class
                a4_payload = agents_raw.get("agent_4", {}).get("payload", {}) or {}
                a4_contract = a4_payload.get("shadow_ict_contract", {})
                a4_ctx = a4_contract.get("contextual_notes", {})
                a4_has_family = "setup_family_match" in a4_payload or "setup_family" in a4_ctx or "retracement_class" in a4_ctx
                a4_hard_veto = a4_contract.get("hard_veto", False) if a4_contract else (not bool(agents_raw.get("agent_4", {}).get("hard_filter_pass", True)))
                if not a4_has_family:
                    if "setup_family_match / retracement_depth_class" not in agent_compliance["agent_4"]["missing_fields"]:
                        agent_compliance["agent_4"]["missing_fields"].append("setup_family_match / retracement_depth_class")
                if a4_hard_veto and primary_regime in ["STRONG_DOWN", "STRONG_UP"] and tc_candidate:
                    if "Hard OTE veto on strong continuation (non-pullback)" not in agent_compliance["agent_4"]["robotic_behaviors_detected"]:
                        agent_compliance["agent_4"]["robotic_behaviors_detected"].append("Hard OTE veto on strong continuation (non-pullback)")
                        agent_compliance["agent_4"]["human_like_improvements_needed"].append("Use shallow_pullback_ok for TREND_CONTINUATION; premium_forbidden hard veto only for SNIPER_PULLBACK/REVERSAL")

                # Agent 5 — must be optional for strong continuations
                a5_payload = agents_raw.get("agent_5", {}).get("payload", {}) or {}
                a5_contract = a5_payload.get("shadow_ict_contract", {})
                a5_ctx = a5_contract.get("contextual_notes", {})
                a5_pass = bool(agents_raw.get("agent_5", {}).get("hard_filter_pass", True))
                a5_has_trigger_kind = "trigger_kind" in a5_ctx or "micro_shift_strength" in a5_ctx
                if not a5_has_trigger_kind:
                    if "trigger_kind / micro_shift_strength" not in agent_compliance["agent_5"]["missing_fields"]:
                        agent_compliance["agent_5"]["missing_fields"].append("trigger_kind / micro_shift_strength")

                # Agent 6 — news veto state
                a6_payload = agents_raw.get("agent_6", {}).get("payload", {}) or {}
                a6_contract = a6_payload.get("shadow_ict_contract", {})
                a6_ctx = a6_contract.get("contextual_notes", {})
                a6_has_lockout_state = "pre_news_lockout" in a6_ctx or "pre_news_lockout" in a6_payload
                if not a6_has_lockout_state:
                    if "pre_news_lockout / during_news / post_news_normalized" not in agent_compliance["agent_6"]["missing_fields"]:
                        agent_compliance["agent_6"]["missing_fields"].append("pre_news_lockout / during_news / post_news_normalized")

                # Agent 7 — session permission, not just boolean pass
                a7_payload = agents_raw.get("agent_7", {}).get("payload", {}) or {}
                a7_contract = a7_payload.get("shadow_ict_contract", {})
                a7_ctx = a7_contract.get("contextual_notes", {})
                a7_has_session_label = "session_label" in a7_ctx or "session_label" in a7_payload
                if not a7_has_session_label:
                    if "session_label / macro_window / tradable_window" not in agent_compliance["agent_7"]["missing_fields"]:
                        agent_compliance["agent_7"]["missing_fields"].append("session_label / macro_window / tradable_window")

                # Orchestrator — must apply hierarchy, not average; must emit WAIT
                orch = ev.get("orchestrator", {}) or {}
                orch_decision = orch.get("human_context_decision_shadow", "") or orch.get("decision", "")
                if not orch_decision:
                    # fallback to our shadow mctx calculation if orchestrator didn't have it
                    orch_decision = mctx_ev.get("human_like_context_decision_shadow", "")
                orch_has_wait = "WAIT" in orch_decision
                if not orch_has_wait and not ev_a2_pass and primary_regime in ["STRONG_DOWN", "STRONG_UP"] and tc_candidate:
                    if "Never emits WAIT when context is good but POI unavailable" not in orchestrator_compliance["robotic_behaviors_detected"]:
                        orchestrator_compliance["robotic_behaviors_detected"].append("Never emits WAIT when context is good but POI unavailable")
                        orchestrator_compliance["improvements_needed"].append("Emit WAIT instead of REJECT when DOL+OF intact but no mature entry")

                # Memory / Blackboard
                bb = ev.get("blackboard", {}) or {}
                bb_has_market_ctx = "market_context" in bb or "primary_regime" in bb or primary_regime != ""
                if not bb_has_market_ctx:
                    if "market_context missing from blackboard snapshot" not in memory_compliance["issues"]:
                        memory_compliance["issues"].append("market_context missing from blackboard snapshot")
                        memory_compliance["improvements_needed"].append("Persist MarketContext on blackboard for orchestrator re-read")

                # ── Parts A–C: ICT Continuation Rebalance analysis ──────────
                if primary_regime in ["STRONG_DOWN", "STRONG_UP"] and tc_candidate and ev_a1_score >= 80 and not ev_a2_pass:
                    ict_cr_total_cases += 1
                    is_short = primary_regime == "STRONG_DOWN"

                    # A1 — DOL still open proxy: draw_on_liquidity direction matches regime
                    dol_open = (is_short and draw_on_liquidity == "SELL_SIDE") or \
                               (not is_short and draw_on_liquidity == "BUY_SIDE")
                    if dol_open: ict_cr_dol_open += 1

                    # A2 — HTF order flow intact proxy
                    htf_of_intact = (is_short and order_flow == "BEARISH") or (not is_short and order_flow == "BULLISH")
                    if htf_of_intact: ict_cr_htf_of_intact += 1

                    # A3 — Liquidity target already reached proxy
                    # We use agent1 score < 60 as proxy for degraded OF — conservative
                    liq_target_reached = ev_a1_score < 60
                    if liq_target_reached: ict_cr_liq_target_reached += 1

                    # A4 — Displacement and rebalance detection from 1m data
                    decision_time_iso = ev.get("scoring_diagnostic", {}).get("timestamp")
                    start_idx = -1
                    if decision_time_iso:
                        dec_time = _as_utc(decision_time_iso)
                        for idx, c in enumerate(candles_1m_for_sim):
                            if _as_utc(c["time"]) >= dec_time:
                                start_idx = idx
                                break

                    displacement_detected = False
                    rebalance_detected = False
                    rebalance_type = "NONE"
                    entry_on_impulse = True  # pessimistic default
                    micro_structure_confirms = False

                    if start_idx >= 15:
                        recent = candles_1m_for_sim[start_idx-15 : start_idx+1]
                        signal_c = recent[-1]
                        prev_c = recent[:-1]

                        # Displacement: signal candle body > 1.5x avg body of prior 10
                        bodies = [abs(c.get("close", 0.0) - c.get("open", 0.0)) for c in prev_c[-10:]]
                        avg_body = (sum(bodies) / len(bodies)) if bodies else 0.01
                        if avg_body == 0: avg_body = 0.01
                        signal_body = abs(signal_c.get("close", 0.0) - signal_c.get("open", 0.0))
                        displacement_detected = signal_body > 1.5 * avg_body

                        # Entry on impulse: current close still inside signal candle range
                        s_high = signal_c.get("high", 0.0)
                        s_low = signal_c.get("low", 0.0)
                        s_close = signal_c.get("close", 0.0)
                        if displacement_detected:
                            # Impulse candle = large body; entry AT close = FOMO
                            entry_on_impulse = True  # by definition — this is the breakout candle itself

                        # Rebalance: price retraced into prior displacement gap in next 5 bars
                        if displacement_detected and start_idx + 5 < len(candles_1m_for_sim):
                            disp_mid = (s_high + s_low) / 2.0
                            for rb_i in range(start_idx + 1, min(start_idx + 6, len(candles_1m_for_sim))):
                                rb_c = candles_1m_for_sim[rb_i]
                                rb_h = rb_c.get("high", 0.0)
                                rb_l = rb_c.get("low", 0.0)
                                if is_short and rb_h >= disp_mid:
                                    rebalance_detected = True
                                    rebalance_type = "DISPLACEMENT_50_REBALANCE"
                                    entry_on_impulse = False  # waiting for rebalance = not FOMO
                                    break
                                elif not is_short and rb_l <= disp_mid:
                                    rebalance_detected = True
                                    rebalance_type = "DISPLACEMENT_50_REBALANCE"
                                    entry_on_impulse = False
                                    break

                            # Micro-FVG rebalance check
                            if not rebalance_detected and len(recent) >= 3:
                                c0, c2 = recent[-3], recent[-1]
                                if is_short and c0.get("low", 0.0) > c2.get("high", 0.0):
                                    fvg_mid = (c0.get("low", 0.0) + c2.get("high", 0.0)) / 2.0
                                    for rb_i in range(start_idx + 1, min(start_idx + 6, len(candles_1m_for_sim))):
                                        rb_c = candles_1m_for_sim[rb_i]
                                        if rb_c.get("high", 0.0) >= fvg_mid:
                                            rebalance_detected = True
                                            rebalance_type = "FVG_REBALANCE"
                                            entry_on_impulse = False
                                            break
                                elif not is_short and c0.get("high", 0.0) < c2.get("low", 0.0):
                                    fvg_mid = (c0.get("high", 0.0) + c2.get("low", 0.0)) / 2.0
                                    for rb_i in range(start_idx + 1, min(start_idx + 6, len(candles_1m_for_sim))):
                                        rb_c = candles_1m_for_sim[rb_i]
                                        if rb_c.get("low", 0.0) <= fvg_mid:
                                            rebalance_detected = True
                                            rebalance_type = "FVG_REBALANCE"
                                            entry_on_impulse = False
                                            break

                        # Micro-structure: next candle confirms direction (continuation close)
                        if start_idx + 1 < len(candles_1m_for_sim):
                            next_c = candles_1m_for_sim[start_idx + 1]
                            if is_short and next_c.get("close", 0.0) < signal_c.get("close", 0.0):
                                micro_structure_confirms = True
                            elif not is_short and next_c.get("close", 0.0) > signal_c.get("close", 0.0):
                                micro_structure_confirms = True

                    if rebalance_detected: ict_cr_rebalance_detected += 1
                    if rebalance_detected and displacement_detected: ict_cr_rebalance_after_displacement += 1
                    if entry_on_impulse: ict_cr_fomo_entry_rejected += 1
                    if micro_structure_confirms: ict_cr_micro_struct_confirms += 1

                    # ── Part B — ICT Continuation Rebalance Score ──────────
                    ict_score = 0
                    if dol_open: ict_score += 20           # DOL still open (top of hierarchy)
                    if htf_of_intact: ict_score += 20      # HTF institutional order flow intact
                    if displacement_detected: ict_score += 15  # Clean displacement (not random)
                    if rebalance_detected: ict_score += 20     # True rebalance after displacement
                    if not entry_on_impulse: ict_score += 10   # Not a FOMO entry
                    if micro_structure_confirms: ict_score += 10  # Micro-structure confirms
                    if ev_a7_pass: ict_score += 5              # Session/time favorable (Agent 7)
                    ict_score = min(100, max(0, ict_score))

                    # Confidence: how many layers are clear (0-7 layers, normalized)
                    clear_layers = sum([dol_open, htf_of_intact, displacement_detected,
                                        rebalance_detected, not entry_on_impulse,
                                        micro_structure_confirms, ev_a7_pass])
                    ict_confidence = round(clear_layers / 7.0, 2)

                    ict_cr_score_sum += ict_score
                    ict_cr_confidence_sum += ict_confidence

                    ict_valid = (dol_open and htf_of_intact and rebalance_detected and not entry_on_impulse)
                    if ict_valid: ict_cr_ict_valid += 1

                    # ── Part C — Human-like decision ──────────────────────
                    if not dol_open or liq_target_reached:
                        human_decision = "REJECT"
                    elif not rebalance_detected and (dol_open and htf_of_intact):
                        human_decision = "WAIT"
                        ict_cr_wait += 1
                    elif rebalance_detected and not micro_structure_confirms:
                        human_decision = "CANDIDATE_MICRO"
                        ict_cr_candidate_micro += 1
                    elif ict_score >= 80 and ev_a7_pass:
                        human_decision = "PREMIUM_PAPER_SHADOW"
                        ict_cr_premium_paper += 1
                    elif ict_score >= 60 and dol_open and htf_of_intact and rebalance_detected:
                        human_decision = "STANDARD_PAPER_SHADOW"
                        ict_cr_standard_paper += 1
                    else:
                        human_decision = "WAIT"
                        ict_cr_wait += 1

                    # ── Part F — Outcome simulation for STANDARD + PREMIUM only ──
                    if human_decision in ("STANDARD_PAPER_SHADOW", "PREMIUM_PAPER_SHADOW") and start_idx >= 15:
                        if rebalance_type in ("DISPLACEMENT_50_REBALANCE", "FVG_REBALANCE"):
                            sig_c = candles_1m_for_sim[start_idx]
                            rebal_mid = (sig_c.get("high", 0.0) + sig_c.get("low", 0.0)) / 2.0
                            entry_price = rebal_mid

                            sl_price = max(c.get("high", 0.0) for c in candles_1m_for_sim[start_idx-3:start_idx+1]) \
                                if is_short else \
                                min(c.get("low", 0.0) for c in candles_1m_for_sim[start_idx-3:start_idx+1])

                            risk = abs(entry_price - sl_price)
                            if risk < 0.5: risk = 0.5
                            tp1_p = entry_price - risk if is_short else entry_price + risk
                            tp2_p = entry_price - 2 * risk if is_short else entry_price + 2 * risk

                            hit_tp1 = hit_tp2 = hit_sl = False
                            result_r = 0.0
                            sim_limit = min(start_idx + 120, len(candles_1m_for_sim))
                            for sim_i in range(start_idx + 1, sim_limit):
                                c = candles_1m_for_sim[sim_i]
                                h, l = c.get("high", 0.0), c.get("low", 0.0)
                                if is_short:
                                    if h >= sl_price: hit_sl = True; result_r = -1.0; break
                                    if l <= tp2_p: hit_tp2 = hit_tp1 = True; result_r = 2.0; break
                                    if l <= tp1_p and not hit_tp1: hit_tp1 = True; sl_price = entry_price
                                else:
                                    if l <= sl_price: hit_sl = True; result_r = -1.0; break
                                    if h >= tp2_p: hit_tp2 = hit_tp1 = True; result_r = 2.0; break
                                    if h >= tp1_p and not hit_tp1: hit_tp1 = True; sl_price = entry_price

                            ict_out_entries += 1
                            if hit_tp2: ict_out_tp2 += 1; ict_out_avg_r_sum += 2.0
                            elif hit_tp1 and hit_sl: ict_out_tp1 += 1
                            elif hit_sl: ict_out_sl += 1; ict_out_avg_r_sum -= 1.0
                            else: ict_out_no_res += 1; (ict_out_tp1 := ict_out_tp1 + 1) if hit_tp1 else None

                    # Collect examples
                    if len(ict_cr_examples) < 10:
                        ict_cr_examples.append({
                            "timestamp": decision_time_iso if start_idx >= 15 else ev.get("scoring_diagnostic", {}).get("timestamp"),
                            "direction": "SHORT" if is_short else "LONG",
                            "primary_regime": primary_regime,
                            "delivery_phase": "EXPANSION",
                            "draw_on_liquidity": draw_on_liquidity,
                            "institutional_order_flow": order_flow,
                            "agent1_score": ev_a1_score,
                            "displacement_detected": displacement_detected,
                            "rebalance_detected": rebalance_detected,
                            "rebalance_type": rebalance_type,
                            "rebalance_after_displacement": rebalance_detected and displacement_detected,
                            "entry_on_impulse_candle": entry_on_impulse,
                            "price_reached_liquidity_target_before_entry": liq_target_reached,
                            "dol_still_open": dol_open,
                            "htf_order_flow_intact": htf_of_intact,
                            "micro_structure_confirms_continuation": micro_structure_confirms,
                            "ict_continuation_valid": ict_valid,
                            "ict_continuation_rebalance_score": ict_score,
                            "ict_continuation_confidence": ict_confidence,
                            "human_like_context_decision_shadow": human_decision,
                            "reason": f"dol_open={dol_open} of_intact={htf_of_intact} rebalance={rebalance_detected} fomo={entry_on_impulse}"
                        })

            # ── Aggregate scores (Parties A–C) ──────────────────────────────
            avg_ict_score = round(ict_cr_score_sum / max(1, ict_cr_total_cases), 2)
            avg_ict_confidence = round(ict_cr_confidence_sum / max(1, ict_cr_total_cases), 3)

            # ── Compliance scoring (Partie D) ────────────────────────────────
            def _compliance_score(entry: dict) -> int:
                """Score 0–100 based on absence of issues. Fewer missing/robotic = higher score."""
                penalties = len(entry.get("missing_fields", [])) * 10 + len(entry.get("robotic_behaviors_detected", [])) * 15
                return max(0, 100 - penalties)

            for ag_key in agent_compliance:
                agent_compliance[ag_key]["score"] = _compliance_score(agent_compliance[ag_key])
                if agent_compliance[ag_key]["missing_fields"] or agent_compliance[ag_key]["robotic_behaviors_detected"]:
                    agent_compliance[ag_key]["compliant_with_ict_report"] = False

            orchestrator_compliance["score"] = max(0, 100 - len(orchestrator_compliance["robotic_behaviors_detected"]) * 20)
            if orchestrator_compliance["robotic_behaviors_detected"]:
                orchestrator_compliance["compliant"] = False

            memory_compliance["score"] = max(0, 100 - len(memory_compliance["issues"]) * 20)
            if memory_compliance["issues"]:
                memory_compliance["compliant"] = False

            agent_scores = [agent_compliance[k]["score"] for k in agent_compliance]
            report_compliance_global = round(
                (sum(agent_scores) / len(agent_scores) + orchestrator_compliance["score"] + memory_compliance["score"]) / 3.0, 1
            )

            # Global ICT verdict
            if ict_cr_total_cases == 0:
                ict_global_verdict = "ICT_REBALANCE_MODEL_NO_VALID_CONTEXT"
            elif report_compliance_global < 50:
                ict_global_verdict = "ICT_HUMAN_ALIGNMENT_INSUFFICIENT"
            elif any(agent_compliance[k]["robotic_behaviors_detected"] for k in agent_compliance):
                ict_global_verdict = "ICT_REBALANCE_MODEL_NEEDS_AGENT_REWORK"
            elif ict_cr_ict_valid == 0:
                ict_global_verdict = "ICT_REBALANCE_MODEL_NO_VALID_CONTEXT"
            else:
                ict_out_winrate_tp1 = round((ict_out_tp1 + ict_out_tp2) / max(1, ict_out_entries) * 100, 2)
                ict_out_avg_r = round(ict_out_avg_r_sum / max(1, ict_out_entries), 2)
                if ict_out_avg_r >= 0.2 and ict_out_winrate_tp1 > 45:
                    ict_global_verdict = "ICT_REBALANCE_MODEL_PROMISING"
                else:
                    ict_global_verdict = "ICT_REBALANCE_MODEL_TOO_RISKY"

            ict_out_winrate_tp1 = round((ict_out_tp1 + ict_out_tp2) / max(1, ict_out_entries) * 100, 2)
            ict_out_winrate_tp2 = round(ict_out_tp2 / max(1, ict_out_entries) * 100, 2)
            ict_out_avg_r = round(ict_out_avg_r_sum / max(1, ict_out_entries), 2)

            # Main issues across all agents
            all_robotic = []
            all_improvements = []
            for ag_key in agent_compliance:
                for b in agent_compliance[ag_key]["robotic_behaviors_detected"]:
                    all_robotic.append(f"[{ag_key}] {b}")
                for b in agent_compliance[ag_key]["human_like_improvements_needed"]:
                    all_improvements.append(f"[{ag_key}] {b}")
            for b in orchestrator_compliance["robotic_behaviors_detected"]:
                all_robotic.append(f"[orchestrator] {b}")
            for b in orchestrator_compliance["improvements_needed"]:
                all_improvements.append(f"[orchestrator] {b}")

            # ── Summary blocks (Partie E) ────────────────────────────────────
            summary["shadow_ict_continuation_rebalance_analysis"] = {
                "note": "P1.40 — ICT Continuation Rebalance (doctrine: deep-research-report.md)",
                "total_strong_delivery_cases": ict_cr_total_cases,
                "rebalance_detected_count": ict_cr_rebalance_detected,
                "rebalance_after_displacement_count": ict_cr_rebalance_after_displacement,
                "fomo_entry_rejected_count": ict_cr_fomo_entry_rejected,
                "dol_still_open_count": ict_cr_dol_open,
                "liquidity_target_already_reached_count": ict_cr_liq_target_reached,
                "htf_order_flow_intact_count": ict_cr_htf_of_intact,
                "micro_structure_confirms_count": ict_cr_micro_struct_confirms,
                "ict_continuation_valid_count": ict_cr_ict_valid,
                "human_like_wait_count": ict_cr_wait,
                "candidate_micro_count": ict_cr_candidate_micro,
                "standard_paper_shadow_count": ict_cr_standard_paper,
                "premium_paper_shadow_count": ict_cr_premium_paper,
                "avg_ict_continuation_rebalance_score": avg_ict_score,
                "avg_ict_continuation_confidence": avg_ict_confidence,
                "global_verdict": ict_global_verdict,
                "examples": ict_cr_examples
            }

            summary["shadow_ict_agent_compliance_refactor_analysis"] = {
                "note": "P1.40 — Agent ICT doctrine compliance (deep-research-report.md)",
                "report_compliance_score_global": report_compliance_global,
                "agents_compliance_breakdown": [
                    {**{"agent": k}, **agent_compliance[k]} for k in agent_compliance
                ],
                "orchestrator_compliance_score": orchestrator_compliance["score"],
                "orchestrator_issues": orchestrator_compliance["robotic_behaviors_detected"],
                "orchestrator_improvements": orchestrator_compliance["improvements_needed"],
                "memory_compliance_score": memory_compliance["score"],
                "memory_issues": memory_compliance["issues"],
                "memory_improvements": memory_compliance["improvements_needed"],
                "main_robotic_behaviors_remaining": list(dict.fromkeys(all_robotic))[:15],
                "main_human_like_improvements_needed": list(dict.fromkeys(all_improvements))[:15],
            }

            summary["shadow_ict_rebalance_after_agent_refactor_outcome"] = {
                "note": "P1.40 — Outcome simulation: STANDARD_PAPER_SHADOW + PREMIUM_PAPER_SHADOW only",
                "theoretical_entries": ict_out_entries,
                "tp1_hit_count": ict_out_tp1,
                "tp2_hit_count": ict_out_tp2,
                "sl_hit_count": ict_out_sl,
                "no_resolution_count": ict_out_no_res,
                "winrate_tp1": ict_out_winrate_tp1,
                "winrate_tp2": ict_out_winrate_tp2,
                "avg_r": ict_out_avg_r,
                "max_drawdown_r": 0,
                "global_verdict": ict_global_verdict
            }
            # ──────────────────────────────────────────────────────────────────

            # ── P1.43 ICT Human Orchestrator Decision ─────────────────────
            from orchestrator.contextual_shadow import build_shadow_ict_human_orchestrator_decision
            from context.market_context_shadow import build_market_context_shadow
            
            ho_total_evaluations = 0
            ho_reject_count = 0
            ho_wait_count = 0
            ho_candidate_micro_count = 0
            ho_standard_paper_shadow_count = 0
            ho_premium_paper_shadow_count = 0
            ho_wait_reason_dist = Counter()
            ho_reject_reason_dist = Counter()
            ho_candidate_reason_dist = Counter()
            ho_blocking_layer_dist = Counter()
            
            ho_out_theoretical_entries = 0
            ho_out_tp1 = 0
            ho_out_tp2 = 0
            ho_out_sl = 0
            ho_out_no_res = 0
            ho_out_avg_r_sum = 0.0

            candles_1m_for_sim = self.clock._candles
            
            for ev in self._events_for_summary:
                if ev.get("event") != "decision":
                    continue
                agents_raw = ev.get("agents", {})
                if not agents_raw:
                    continue
                
                mctx = build_market_context_shadow(ev, agents_raw)
                decision_obj = build_shadow_ict_human_orchestrator_decision(agents_raw, mctx, {})
                ho_total_evaluations += 1
                
                d = decision_obj.get("decision", "WAIT")
                reason = decision_obj.get("reason", "UNKNOWN")
                layer = decision_obj.get("blocking_layer", "UNKNOWN")
                
                if d == "REJECT":
                    ho_reject_count += 1
                    ho_reject_reason_dist[reason] += 1
                elif d == "WAIT":
                    ho_wait_count += 1
                    ho_wait_reason_dist[reason] += 1
                elif d == "CANDIDATE_MICRO":
                    ho_candidate_micro_count += 1
                    ho_candidate_reason_dist[reason] += 1
                elif d == "STANDARD_PAPER_SHADOW":
                    ho_standard_paper_shadow_count += 1
                elif d == "PREMIUM_PAPER_SHADOW":
                    ho_premium_paper_shadow_count += 1
                
                ho_blocking_layer_dist[layer] += 1
                
                # Partie E: Outcome Simulation for STANDARD or PREMIUM
                if d in ("STANDARD_PAPER_SHADOW", "PREMIUM_PAPER_SHADOW"):
                    # Find index
                    sig_time = _as_utc((ev.get("diagnostic") or {}).get("time") or (ev.get("scoring_diagnostic") or {}).get("timestamp"))
                    start_idx = 0
                    for idx, c in enumerate(candles_1m_for_sim):
                        if _as_utc(c["time"]) == sig_time:
                            start_idx = idx
                            break
                    
                    if start_idx >= 15 and start_idx < len(candles_1m_for_sim):
                        sig_c = candles_1m_for_sim[start_idx]
                        entry_price = sig_c.get("close", 0.0)
                        
                        is_short = ("DOWN" in mctx.get("primary_regime", ""))
                        sl_price = max(c.get("high", 0.0) for c in candles_1m_for_sim[start_idx-3:start_idx+1]) if is_short else min(c.get("low", 0.0) for c in candles_1m_for_sim[start_idx-3:start_idx+1])
                        
                        risk = abs(entry_price - sl_price)
                        if risk < 0.5: risk = 0.5
                        tp1_p = entry_price - risk if is_short else entry_price + risk
                        tp2_p = entry_price - 2 * risk if is_short else entry_price + 2 * risk
                        
                        hit_tp1 = hit_tp2 = hit_sl = False
                        sim_limit = min(start_idx + 120, len(candles_1m_for_sim))
                        
                        for sim_i in range(start_idx + 1, sim_limit):
                            c = candles_1m_for_sim[sim_i]
                            h, l = c.get("high", 0.0), c.get("low", 0.0)
                            if is_short:
                                if h >= sl_price: hit_sl = True; break
                                if l <= tp2_p: hit_tp2 = hit_tp1 = True; break
                                if l <= tp1_p and not hit_tp1: hit_tp1 = True; sl_price = entry_price
                            else:
                                if l <= sl_price: hit_sl = True; break
                                if h >= tp2_p: hit_tp2 = hit_tp1 = True; break
                                if h >= tp1_p and not hit_tp1: hit_tp1 = True; sl_price = entry_price
                                
                        ho_out_theoretical_entries += 1
                        if hit_tp2: ho_out_tp2 += 1; ho_out_avg_r_sum += 2.0
                        elif hit_tp1 and hit_sl: ho_out_tp1 += 1
                        elif hit_sl: ho_out_sl += 1; ho_out_avg_r_sum -= 1.0
                        else: ho_out_no_res += 1; (ho_out_tp1 := ho_out_tp1 + 1) if hit_tp1 else None

            summary["shadow_ict_human_orchestrator_decision_analysis"] = {
                "note": "P1.43 — ICT Human Orchestrator Decision Distribution",
                "total_evaluations": ho_total_evaluations,
                "reject_count": ho_reject_count,
                "wait_count": ho_wait_count,
                "candidate_micro_count": ho_candidate_micro_count,
                "standard_paper_shadow_count": ho_standard_paper_shadow_count,
                "premium_paper_shadow_count": ho_premium_paper_shadow_count,
                "reject_reasons": dict(ho_reject_reason_dist),
                "wait_reasons": dict(ho_wait_reason_dist),
                "candidate_reasons": dict(ho_candidate_reason_dist),
                "blocking_layer_distribution": dict(ho_blocking_layer_dist)
            }
            
            ho_out_winrate = round((ho_out_tp1 + ho_out_tp2) / max(1, ho_out_theoretical_entries) * 100, 2) if ho_out_theoretical_entries else 0.0
            ho_out_avg_r = round(ho_out_avg_r_sum / max(1, ho_out_theoretical_entries), 2) if ho_out_theoretical_entries else 0.0
            
            summary["shadow_ict_human_orchestrator_outcome_analysis"] = {
                "note": "P1.43 — Outcome for STANDARD/PREMIUM paper shadow (120m horizon)",
                "theoretical_entries": ho_out_theoretical_entries,
                "tp1_hit": ho_out_tp1,
                "tp2_hit": ho_out_tp2,
                "sl_hit": ho_out_sl,
                "no_resolution": ho_out_no_res,
                "winrate_tp1": ho_out_winrate,
                "average_r": ho_out_avg_r
            }
            # ──────────────────────────────────────────────────────────────────


        summary.update(_build_p1_45_agent2_poi_stack_summaries(self._events_for_summary, self.clock._candles))
        summary.update(_build_p1_46_professional_strategy_summaries(self._events_for_summary, self.clock._candles))

        summary["events_path"] = str(self.events_path)
        summary["trade_journal_path"] = str(self.trade_journal_path)
        summary["summary_path"] = str(self.summary_path)
        return summary

    def _write_trade_journal(self) -> None:
        journal = TradeJournal()
        journal.extend_from_trade_manager_events(
            [event for event in self._events_for_summary if event.get("event") in TRADE_JOURNAL_EVENTS]
        )
        journal.save_jsonl(self.trade_journal_path)


    def _phase_for_candle(self, candle: dict[str, Any]) -> tuple[str, bool]:
        timestamp = _as_utc(candle["time"])
        eval_active = True
        if self.eval_start is not None and timestamp < self.eval_start:
            eval_active = False
        if self.eval_end is not None and timestamp > self.eval_end:
            eval_active = False
        return ("evaluation" if eval_active else "warmup", eval_active)

    def _summary_from_events(
        self,
        events: list[dict[str, Any]],
        fallback_trade_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if fallback_trade_summary is not None:
            summary = dict(fallback_trade_summary)
        else:
            close_events = [event for event in events if event.get("event") == "close"]
            wins = sum(1 for event in close_events if float(event.get("pnl", 0.0) or 0.0) > 0)
            losses = sum(1 for event in close_events if float(event.get("pnl", 0.0) or 0.0) <= 0)
            pnl = round(
                sum(float(event.get("pnl", 0.0) or 0.0) for event in events if event.get("event") in {"partial_close", "close"}),
                6,
            )
            summary = {
                "signals": len([event for event in events if event.get("event") == "signal_created"]),
                "trades": len([event for event in events if event.get("event") == "open"]),
                "closed_trades": len(close_events),
                "missed_entries": len([event for event in events if event.get("event") == "missed_entry"]),
                "wins": wins,
                "losses": losses,
                "win_rate": round((wins / len(close_events)) * 100, 2) if close_events else 0.0,
                "pnl": pnl,
                "equity_final": round(self.trade_manager.config.equity_initial + pnl, 6),
                "max_drawdown": _max_drawdown_from_events(events, self.trade_manager.config.equity_initial),
                "rejections": len([event for event in events if event.get("event") == "rejected"]),
                "partial_closes": len([event for event in events if event.get("event") == "partial_close"]),
                "be_plus_moves": len([event for event in events if event.get("event") == "sl_moved_be_plus"]),
                "tp1_hits": len([event for event in events if event.get("reason") == "TP1"]),
                "tp2_hits": len([event for event in events if event.get("reason") == "TP2"]),
                "tp3_hits": len([event for event in events if event.get("reason") == "TP3"]),
                "sl_hits": len([event for event in events if event.get("reason") == "SL"]),
                "protected_sl_hits": len([event for event in events if event.get("reason") == "PROTECTED_SL"]),
            }
        counts = Counter(event.get("event") for event in events)
        summary["events"] = len(events)
        summary["event_counts"] = dict(counts)
        summary["signal_rejections"] = counts.get("signal_rejected", 0)
        return summary

    async def _tier_simulation_events(
        self,
        candle: dict[str, Any],
        index: int,
        decision: dict[str, Any],
        phase: str,
        eval_active: bool,
    ) -> list[dict[str, Any]]:
        if not self.tier_simulation or not eval_active:
            return []

        score = _decision_score(decision)
        tier = _tier_for_score(score)
        if tier is None:
            return await self.tier_trade_manager.on_candle_with_signal(candle, None)
        if self.tier_trade_manager.active_positions:
            return await self.tier_trade_manager.on_candle_with_signal(candle, None)

        signal, reject_reason = _tier_replay_trade_signal(
            decision,
            self.blackboard,
            candle,
            tier,
            score,
            self.tier_trade_manager.equity,
        )
        candidate_event = {
            "event": "tier_candidate_created",
            "time": _json_default(candle["time"]),
            "bar_index": index,
            "phase": phase,
            "eval_active": eval_active,
            "tier": tier["name"],
            "score": score,
            "risk_pct": tier["risk_pct"],
            "strict_decision": (decision.get("orchestrator") or {}).get("decision"),
            "strict_reason": decision.get("reject_reason") or decision.get("reason"),
        }
        events = [candidate_event]
        if reject_reason:
            events.extend(await self.tier_trade_manager.on_candle_with_signal(candle, None))
            events.append(
                {
                    "event": "tier_trade_rejected",
                    "time": _json_default(candle["time"]),
                    "bar_index": index,
                    "tier": tier["name"],
                    "score": score,
                    "reason": reject_reason,
                }
            )
            return events

        events.extend(await self.tier_trade_manager.on_candle_with_signal(candle, signal))
        return events

    def _tier_summary(self) -> dict[str, Any]:
        events = [event for event in self._events_for_summary if str(event.get("event", "")).startswith("tier_")]
        return {
            tier: _tier_summary_from_events(events, tier, self.tier_trade_manager.config.equity_initial)
            for tier in ("CANDIDATE_MICRO", "STANDARD_PAPER", "PREMIUM_PAPER")
        }


def _aggregate(candles: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "time": candles[-1]["time"],
        "open": float(candles[0]["open"]),
        "high": max(float(candle["high"]) for candle in candles),
        "low": min(float(candle["low"]) for candle in candles),
        "close": float(candles[-1]["close"]),
        "volume": sum(float(candle.get("volume", candle.get("tick_volume", 0.0)) or 0.0) for candle in candles),
        "tick_volume": sum(float(candle.get("tick_volume", candle.get("volume", 0.0)) or 0.0) for candle in candles),
    }


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _max_drawdown_from_events(events: list[dict[str, Any]], initial_equity: float) -> float:
    peak = float(initial_equity)
    max_drawdown = 0.0
    for event in events:
        if "equity" not in event:
            continue
        equity = float(event["equity"])
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return round(max_drawdown, 6)


def _decision_score(decision: dict[str, Any]) -> float:
    for key in ("score_final", "score"):
        value = decision.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    orchestrator = decision.get("orchestrator") or {}
    try:
        return float(orchestrator.get("score", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _tier_for_score(score: float) -> dict[str, Any] | None:
    if score < 60.0:
        return None
    if score < 75.0:
        return {"name": "CANDIDATE_MICRO", "risk_pct": 0.5}
    if score < 85.0:
        return {"name": "STANDARD_PAPER", "risk_pct": 1.0}
    return {"name": "PREMIUM_PAPER", "risk_pct": 1.5}


def _tier_replay_trade_signal(
    decision: dict[str, Any],
    blackboard,
    candle: dict[str, Any],
    tier: dict[str, Any],
    score: float,
    equity: float,
) -> tuple[dict[str, Any] | None, str | None]:
    direction, direction_source = _tier_direction(decision, blackboard)
    if direction is None:
        return None, "MISSING_DIRECTION"

    entry, entry_source = _tier_entry(decision, blackboard, candle)
    if entry is None:
        return None, "MISSING_ENTRY"
    sl, sl_source = _tier_stop_loss(decision, blackboard, direction)
    if sl is None:
        return None, "MISSING_SL"

    risk = entry - sl if direction == "LONG" else sl - entry
    if risk <= 0:
        return None, "INVALID_RISK_DISTANCE"

    agent_5 = _agent_payload(decision, blackboard, "agent_5")
    tp1 = _float_or_none(agent_5.get("tp1_price"))
    tp2 = _float_or_none(agent_5.get("tp2_price"))
    if direction == "LONG":
        tp1 = tp1 if tp1 is not None else entry + risk
        tp2 = tp2 if tp2 is not None else entry + 2.0 * risk
        tp3 = entry + 3.0 * risk
    else:
        tp1 = tp1 if tp1 is not None else entry - risk
        tp2 = tp2 if tp2 is not None else entry - 2.0 * risk
        tp3 = entry - 3.0 * risk

    if None in (tp1, tp2, tp3):
        return None, "MISSING_LEVELS"

    risk_cash = float(equity) * (float(tier["risk_pct"]) / 100.0)
    volume = risk_cash / risk
    return (
        {
            "signal": "BUY" if direction == "LONG" else "SELL",
            "direction": direction,
            "entry_price": entry,
            "stop_loss": sl,
            "tp1_price": tp1,
            "tp2_price": tp2,
            "tp3_price": tp3,
            "take_profit": tp3,
            "volume": volume,
            "risk_cash": risk_cash,
            "risk_pct": tier["risk_pct"],
            "tier": tier["name"],
            "score": score,
            "source": "TIER_REPLAY",
            "direction_source": direction_source,
            "entry_source": entry_source,
            "sl_source": sl_source,
            "tp1_close_percent": 40.0,
            "tp2_close_percent": 30.0,
            "timestamp": candle["time"],
        },
        None,
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _agent_payload(decision: dict[str, Any], blackboard, agent_id: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    try:
        board_data = blackboard.get_agent(agent_id)
        if isinstance(board_data, dict):
            data.update(board_data)
    except Exception:
        pass

    decision_agent = (decision.get("agents") or {}).get(agent_id)
    if isinstance(decision_agent, dict):
        payload = decision_agent.get("payload")
        if isinstance(payload, dict):
            data.update(payload)
        data.update({key: value for key, value in decision_agent.items() if key != "payload"})
    return data


def _normalize_direction(value: Any) -> str | None:
    text = str(value or "").upper()
    if text in {"LONG", "BUY", "BULLISH"}:
        return "LONG"
    if text in {"SHORT", "SELL", "BEARISH"}:
        return "SHORT"
    return None


def _tier_direction(decision: dict[str, Any], blackboard) -> tuple[str | None, str | None]:
    orchestrator = decision.get("orchestrator") or {}
    sources = (
        ("decision", decision.get("direction")),
        ("orchestrator", orchestrator.get("direction")),
        ("agent_1", _agent_payload(decision, blackboard, "agent_1").get("direction")),
        ("agent_5", _agent_payload(decision, blackboard, "agent_5").get("direction")),
        ("agent_2", _agent_payload(decision, blackboard, "agent_2").get("direction")),
    )
    for source, value in sources:
        direction = _normalize_direction(value)
        if direction:
            return direction, source
    return None, None


def _first_float(data: dict[str, Any], keys: tuple[str, ...]) -> tuple[float | None, str | None]:
    for key in keys:
        value = _float_or_none(data.get(key))
        if value is not None:
            return value, key
    return None, None


def _tier_entry(decision: dict[str, Any], blackboard, candle: dict[str, Any]) -> tuple[float | None, str | None]:
    agent_5 = _agent_payload(decision, blackboard, "agent_5")
    entry, key = _first_float(agent_5, ("entry_price", "entry", "choch_price"))
    if entry is not None:
        return entry, f"agent_5.{key}"

    price = _float_or_none(candle.get("close"))
    if price is not None:
        return price, "current_close"

    agent_2 = _agent_payload(decision, blackboard, "agent_2")
    zone = _tier_zone(agent_2)
    bottom, top = _zone_bounds(zone)
    if bottom is not None and top is not None:
        return (bottom + top) / 2.0, "agent_2.zone_mid"
    return None, None


def _tier_stop_loss(decision: dict[str, Any], blackboard, direction: str) -> tuple[float | None, str | None]:
    agent_5 = _agent_payload(decision, blackboard, "agent_5")
    sl, key = _first_float(agent_5, ("sl_price", "sl", "stop_loss"))
    if sl is not None:
        return sl, f"agent_5.{key}"

    agent_2 = _agent_payload(decision, blackboard, "agent_2")
    zone = _tier_zone(agent_2)
    bottom, top = _zone_bounds(zone)
    buffer = max(float(SL_BUFFER_POINTS), 0.0) * 0.01
    if direction == "LONG" and bottom is not None:
        return bottom - buffer, "agent_2.poi_structure"
    if direction == "SHORT" and top is not None:
        return top + buffer, "agent_2.poi_structure"
    return None, None


def _tier_zone(agent_2: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("poi_zone", "active_ob", "zone"):
        value = agent_2.get(key)
        if isinstance(value, dict):
            return value
    selected = agent_2.get("selected_zone")
    if isinstance(selected, dict):
        return selected
    return None


def _zone_bounds(zone: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(zone, dict):
        return None, None
    bottom = _float_or_none(zone.get("entry_zone_bottom", zone.get("bottom")))
    top = _float_or_none(zone.get("entry_zone_top", zone.get("top")))
    return bottom, top


def _tier_summary_from_events(events: list[dict[str, Any]], tier: str, initial_equity: float) -> dict[str, Any]:
    tier_events = [event for event in events if event.get("tier") == tier]
    open_events = [event for event in tier_events if event.get("event") == "tier_trade_open"]
    close_events = [event for event in tier_events if event.get("event") == "tier_trade_close"]
    pnl_events = [
        event
        for event in tier_events
        if event.get("event") in {"tier_trade_partial_close", "tier_trade_close"}
    ]
    pnl_by_ticket: dict[Any, float] = {}
    risk_by_ticket: dict[Any, float] = {}
    for event in pnl_events:
        ticket = event.get("ticket")
        pnl_by_ticket[ticket] = pnl_by_ticket.get(ticket, 0.0) + float(event.get("pnl", 0.0) or 0.0)
        if event.get("risk_cash") is not None:
            risk_by_ticket[ticket] = float(event.get("risk_cash") or 0.0)
    closed_tickets = {event.get("ticket") for event in close_events}
    wins = sum(1 for ticket in closed_tickets if pnl_by_ticket.get(ticket, 0.0) > 0)
    losses = sum(1 for ticket in closed_tickets if pnl_by_ticket.get(ticket, 0.0) <= 0)
    pnl = round(sum(float(event.get("pnl", 0.0) or 0.0) for event in pnl_events), 6)
    r_values = [
        pnl_by_ticket[ticket] / risk
        for ticket, risk in risk_by_ticket.items()
        if risk > 0 and ticket in closed_tickets
    ]
    return {
        "trades": len(open_events),
        "closed_trades": len(close_events),
        "wins": wins,
        "losses": losses,
        "win_rate": round((wins / len(close_events)) * 100, 2) if close_events else 0.0,
        "pnl": pnl,
        "tp1_hits": len([event for event in tier_events if event.get("reason") == "TP1"]),
        "tp2_hits": len([event for event in tier_events if event.get("reason") == "TP2"]),
        "tp3_hits": len([event for event in tier_events if event.get("reason") == "TP3"]),
        "sl_hits": len([event for event in tier_events if event.get("reason") == "SL"]),
        "protected_sl_hits": len([event for event in tier_events if event.get("reason") == "PROTECTED_SL"]),
        "avg_r": round(sum(r_values) / len(r_values), 6) if r_values else 0.0,
        "max_drawdown": _max_drawdown_from_events(tier_events, initial_equity),
        "rejections": len([event for event in tier_events if event.get("event") == "tier_trade_rejected"]),
        "candidates": len([event for event in tier_events if event.get("event") == "tier_candidate_created"]),
    }


def _build_p1_45_agent2_poi_stack_summaries(
    events: list[dict[str, Any]],
    candles_1m: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    decision_events = [event for event in events if event.get("event") == "decision" and event.get("eval_active") is True]
    legacy_fail = 0
    legacy_zone_mitigated = 0
    legacy_no_valid_ob = 0
    htf_valid = 0
    wick_reclassified = 0
    partial_reclassified = 0
    invalidated = consumed = stale = 0
    ob_poi_count = 0
    fvg_poi_count = 0
    best_poi_count = 0
    wait_development = 0
    legacy_reject_wick = 0
    legacy_reject_partial = 0
    legacy_reject_fvg = 0
    stack_type_dist: Counter[str] = Counter()
    score_dist: Counter[str] = Counter()
    reason_dist: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []

    candidate_before = 0
    candidate_after = 0
    wait_before = 0
    wait_after = 0
    standard_after = 0
    premium_after = 0
    agent5_reached = 0
    agent5_pass = 0

    theoretical_entries = 0
    tp1_hit = tp2_hit = sl_hit = no_resolution = 0
    r_sum = 0.0
    drawdown_r = 0.0
    running_r = 0.0
    peak_r = 0.0
    compact_entries: list[dict[str, Any]] = []

    candle_by_time = {_as_utc(candle["time"]): index for index, candle in enumerate(candles_1m) if candle.get("time") is not None}

    for event in decision_events:
        agents = event.get("agents", {}) or {}
        a1 = agents.get("agent_1", {}) or {}
        a2 = agents.get("agent_2", {}) or {}
        a5 = agents.get("agent_5", {}) or {}
        a2_payload = a2.get("payload", {}) or {}
        diag = a2_payload.get("diagnostic", {}) or {}
        if not diag:
            continue

        a1_score = _float_or_none(a1.get("score")) or 0.0
        if a1_score >= 65 and a1.get("direction"):
            htf_valid += 1

        a2_pass = bool(a2.get("hard_filter_pass"))
        legacy_reason = str(a2.get("reason") or diag.get("final_reason") or "")
        if not a2_pass:
            legacy_fail += 1
        if "ZONE_ALREADY_MITIGATED" in legacy_reason:
            legacy_zone_mitigated += 1
        if "NO_VALID_OB_SCORE_GE_60" in legacy_reason:
            legacy_no_valid_ob += 1

        stack = diag.get("shadow_agent2_poi_stack", []) or []
        best = diag.get("best_shadow_poi")
        best_type = str(diag.get("best_shadow_poi_type") or "WAIT_FOR_POI_DEVELOPMENT")
        best_reason = str(diag.get("best_shadow_poi_reason") or "UNKNOWN")
        if best:
            best_poi_count += 1
        else:
            wait_development += 1

        for item in stack:
            label = str(item.get("priority_label") or item.get("type") or item.get("zone_type") or "UNKNOWN")
            state = str(item.get("human_zone_state_shadow") or item.get("state_shadow") or "UNKNOWN")
            stack_type_dist[label] += 1
            reason_dist[str(item.get("zone_rejection_context_reason") or item.get("reason") or "UNKNOWN")] += 1
            score = _float_or_none(item.get("score", item.get("score_shadow"))) or 0.0
            score_dist[_score_bucket(score)] += 1
            if label.startswith("OB_CONTINUATION"):
                ob_poi_count += 1
            if label == "FVG_CONTINUATION_ALIGNED":
                fvg_poi_count += 1
            if state == "WICK_TAGGED":
                wick_reclassified += 1
            elif state == "PARTIALLY_MITIGATED":
                partial_reclassified += 1
            elif state == "INVALIDATED":
                invalidated += 1
            elif state == "CONSUMED":
                consumed += 1
            elif state == "STALE":
                stale += 1

        if not a2_pass and best_type == "OB_CONTINUATION_WICK_TAGGED":
            legacy_reject_wick += 1
        if not a2_pass and best_type == "OB_CONTINUATION_PARTIALLY_MITIGATED":
            legacy_reject_partial += 1
        if not a2_pass and best_type == "FVG_CONTINUATION_ALIGNED":
            legacy_reject_fvg += 1

        before_decision = _baseline_contextual_decision(event)
        if before_decision == "CANDIDATE_MICRO":
            candidate_before += 1
        if before_decision == "WAIT":
            wait_before += 1

        after_decision = before_decision
        if not a2_pass and best:
            after_decision = "CANDIDATE_MICRO"
        elif not a2_pass and not best:
            after_decision = "WAIT_FOR_POI_DEVELOPMENT"
        if best:
            agent5_reached += 1
        if a5.get("hard_filter_pass"):
            agent5_pass += 1
            a5_score = _float_or_none(a5.get("score")) or 0.0
            if a5_score >= 75:
                after_decision = "PREMIUM_PAPER_SHADOW"
            elif a5_score >= 60:
                after_decision = "STANDARD_PAPER_SHADOW"

        if after_decision == "CANDIDATE_MICRO":
            candidate_after += 1
        elif after_decision == "WAIT_FOR_POI_DEVELOPMENT":
            wait_after += 1
        elif after_decision == "STANDARD_PAPER_SHADOW":
            standard_after += 1
        elif after_decision == "PREMIUM_PAPER_SHADOW":
            premium_after += 1

        if best and a5.get("hard_filter_pass") and after_decision in ("STANDARD_PAPER_SHADOW", "PREMIUM_PAPER_SHADOW"):
            outcome = _simulate_shadow_poi_outcome(event, candles_1m, candle_by_time)
            theoretical_entries += 1
            r_result = 0.0
            if outcome == "TP2":
                tp1_hit += 1
                tp2_hit += 1
                running_r += 2.0
                r_sum += 2.0
                r_result = 2.0
            elif outcome == "TP1":
                tp1_hit += 1
                running_r += 1.0
                r_sum += 1.0
                r_result = 1.0
            elif outcome == "SL":
                sl_hit += 1
                running_r -= 1.0
                r_sum -= 1.0
                r_result = -1.0
            else:
                no_resolution += 1
            peak_r = max(peak_r, running_r)
            drawdown_r = max(drawdown_r, peak_r - running_r)
            compact_entries.append(_p1_45_compact_theoretical_entry(event, best, best_type, outcome, r_result, after_decision))

        if len(examples) < 10 and (best or legacy_reason):
            examples.append(_p1_45_example(event, best, after_decision))

    winrate_tp1 = round((tp1_hit / theoretical_entries) * 100, 2) if theoretical_entries else 0.0
    winrate_tp2 = round((tp2_hit / theoretical_entries) * 100, 2) if theoretical_entries else 0.0
    avg_r = round(r_sum / theoretical_entries, 4) if theoretical_entries else 0.0
    verdict = _p1_45_verdict(theoretical_entries, winrate_tp1, avg_r, agent5_reached, agent5_pass, best_poi_count)

    return {
        "shadow_agent2_ict_poi_stack_analysis": {
            "total_htf_valid_cases": htf_valid,
            "legacy_agent2_fail_count": legacy_fail,
            "legacy_zone_already_mitigated_count": legacy_zone_mitigated,
            "legacy_no_valid_ob_score_count": legacy_no_valid_ob,
            "wick_tagged_reclassified_count": wick_reclassified,
            "partially_mitigated_reclassified_count": partial_reclassified,
            "invalidated_zone_count": invalidated,
            "consumed_zone_count": consumed,
            "stale_zone_count": stale,
            "shadow_ob_continuation_poi_count": ob_poi_count,
            "shadow_fvg_continuation_poi_count": fvg_poi_count,
            "shadow_best_poi_count": best_poi_count,
            "shadow_wait_for_poi_development_count": wait_development,
            "cases_legacy_reject_but_shadow_wick_tagged_poi": legacy_reject_wick,
            "cases_legacy_reject_but_shadow_partial_poi": legacy_reject_partial,
            "cases_legacy_reject_but_shadow_fvg_poi": legacy_reject_fvg,
            "poi_stack_type_distribution": dict(stack_type_dist),
            "poi_stack_score_distribution": dict(score_dist),
            "poi_stack_reason_distribution": dict(reason_dist),
            "examples": examples,
        },
        "shadow_agent2_poi_stack_to_orchestrator_analysis": {
            "candidate_micro_before": candidate_before,
            "candidate_micro_after": candidate_after,
            "wait_for_poi_before": wait_before,
            "wait_for_poi_after": wait_after,
            "standard_paper_shadow_count": standard_after,
            "premium_paper_shadow_count": premium_after,
            "agent5_reached_after_poi_stack_count": agent5_reached,
            "agent5_pass_after_poi_stack_count": agent5_pass,
        },
        "shadow_poi_stack_outcome_analysis": {
            "theoretical_entries": theoretical_entries,
            "tp1_hit_count": tp1_hit,
            "tp2_hit_count": tp2_hit,
            "sl_hit_count": sl_hit,
            "no_resolution_count": no_resolution,
            "winrate_tp1": winrate_tp1,
            "winrate_tp2": winrate_tp2,
            "avg_r": avg_r,
            "max_drawdown_r": round(drawdown_r, 4),
            "quality_verdict": verdict,
        },
        "shadow_theoretical_entries": compact_entries,
        "shadow_poi_stack_attribution_analysis": _p1_45_attribution_analysis(compact_entries),
        "shadow_fvg_alternative_risk_analysis": _p1_45_fvg_risk_analysis(compact_entries),
        "shadow_session_risk_analysis": _p1_45_session_risk_analysis(compact_entries),
        "shadow_agent5_trigger_quality_attribution": _p1_45_agent5_trigger_quality(compact_entries),
    }


def _score_bucket(score: float) -> str:
    low = int(score // 10) * 10
    return f"{low}-{low + 9}"


def _p1_45_compact_score_bucket(score: float | None) -> str:
    value = _float_or_none(score) or 0.0
    if value < 60:
        return "0-59"
    if value < 70:
        return "60-69"
    if value < 80:
        return "70-79"
    if value < 90:
        return "80-89"
    if value < 100:
        return "90-99"
    return "100"


def _p1_45_distance_bucket(distance: float | None) -> str:
    value = _float_or_none(distance)
    if value is None:
        return "NA"
    if value <= 5:
        return "0-5"
    if value <= 10:
        return "5-10"
    if value <= 20:
        return "10-20"
    return "20+"


def _p1_45_filled_pct_bucket(value: float | None) -> str:
    pct = _float_or_none(value)
    if pct is None:
        return "NA"
    if pct <= 0:
        return "0"
    if pct <= 25:
        return "1-25"
    if pct <= 50:
        return "25-50"
    if pct <= 75:
        return "50-75"
    return "75-100"


def _p1_45_session_bucket(session: str) -> str:
    value = str(session or "UNKNOWN").upper()
    if "TOKYO" in value or "ASIA" in value:
        return "TOKYO"
    if "LONDON" in value:
        return "LONDON"
    if value in {"NY", "NEW_YORK", "NY_OPEN"} or "NEW_YORK" in value or value.startswith("NY"):
        return "NY"
    return "OTHER"


def _p1_45_compact_theoretical_entry(
    event: dict[str, Any],
    best: dict[str, Any],
    best_type: str,
    result: str,
    r_result: float,
    shadow_decision: str,
) -> dict[str, Any]:
    agents = event.get("agents", {}) or {}
    a1 = agents.get("agent_1", {}) or {}
    a5 = agents.get("agent_5", {}) or {}
    a7 = agents.get("agent_7", {}) or {}
    a1_notes = ((a1.get("payload") or {}).get("shadow_ict_contract") or {}).get("contextual_notes", {}) or {}
    a5_payload = a5.get("payload", {}) or {}
    a5_contract = a5_payload.get("shadow_ict_contract", {}) or {}
    a5_notes = a5_contract.get("contextual_notes", {}) or {}
    trigger_context = a5_payload.get("shadow_trigger_context", {}) or {}
    a7_payload = a7.get("payload", {}) or {}
    a7_notes = (a7_payload.get("shadow_ict_contract", {}) or {}).get("contextual_notes", {}) or {}

    poi_type = str(best.get("priority_label") or best_type or best.get("type") or best.get("zone_type") or "UNKNOWN")
    is_fvg = poi_type == "FVG_CONTINUATION_ALIGNED" or str(best.get("type") or "").startswith("FVG")
    poi_score = _float_or_none(best.get("score", best.get("score_shadow"))) or 0.0
    agent5_score = _float_or_none(a5.get("score")) or 0.0
    session_label = str(a7_notes.get("session_label") or a7_payload.get("session_name") or "UNKNOWN")
    trigger_kind = str(a5_notes.get("trigger_kind") or trigger_context.get("trigger_kind") or "UNKNOWN")

    return {
        "timestamp": event.get("time"),
        "direction": _normalize_direction(a5.get("direction") or a1.get("direction")) or "UNKNOWN",
        "result": result,
        "r_result": r_result,
        "poi_type": poi_type,
        "poi_score": round(poi_score, 4),
        "poi_score_bucket": _p1_45_compact_score_bucket(poi_score),
        "human_zone_state_shadow": "NA" if is_fvg else str(best.get("human_zone_state_shadow") or "NA"),
        "fvg_state_shadow": str(best.get("state_shadow") or "NA") if is_fvg else "NA",
        "fvg_filled_pct": round(_float_or_none(best.get("filled_pct")) or 0.0, 4) if is_fvg else 0.0,
        "fvg_filled_pct_bucket": _p1_45_filled_pct_bucket(best.get("filled_pct")) if is_fvg else "NA",
        "fvg_distance_to_price": round(_float_or_none(best.get("distance_to_price")) or 0.0, 4) if is_fvg else 0.0,
        "fvg_distance_bucket": _p1_45_distance_bucket(best.get("distance_to_price")) if is_fvg else "NA",
        "primary_regime": str(a1_notes.get("primary_regime") or "UNKNOWN"),
        "delivery_phase": str(a5_notes.get("delivery_phase") or trigger_context.get("setup_family_hint") or "UNKNOWN"),
        "draw_on_liquidity": str(a1_notes.get("htf_draw_on_liquidity") or best.get("draw_on_liquidity") or "UNKNOWN"),
        "order_flow": str(a1_notes.get("institutional_order_flow") or ("ALIGNED" if best.get("aligned_with_order_flow") else "UNKNOWN")),
        "session_label": session_label,
        "session_bucket": _p1_45_session_bucket(session_label),
        "agent5_trigger_kind": trigger_kind,
        "agent5_score": round(agent5_score, 4),
        "agent5_score_bucket": _p1_45_compact_score_bucket(agent5_score),
        "shadow_tier": "PREMIUM" if shadow_decision == "PREMIUM_PAPER_SHADOW" else "STANDARD",
    }


def _p1_45_group_metrics(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    total = len(entries)
    tp1 = sum(1 for entry in entries if entry.get("result") in {"TP1", "TP2"})
    tp2 = sum(1 for entry in entries if entry.get("result") == "TP2")
    sl = sum(1 for entry in entries if entry.get("result") == "SL")
    no_resolution = sum(1 for entry in entries if entry.get("result") == "NO_RESOLUTION")
    running = peak = max_dd = 0.0
    r_total = 0.0
    for entry in entries:
        r_value = _float_or_none(entry.get("r_result")) or 0.0
        r_total += r_value
        running += r_value
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return {
        "entries": total,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "no_resolution": no_resolution,
        "winrate_tp1": round((tp1 / total) * 100, 2) if total else 0.0,
        "winrate_tp2": round((tp2 / total) * 100, 2) if total else 0.0,
        "avg_r": round(r_total / total, 4) if total else 0.0,
        "max_drawdown_r": round(max_dd, 4),
    }


def _p1_45_group_by(entries: Sequence[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        groups[str(entry.get(key) or "UNKNOWN")].append(entry)
    return {name: _p1_45_group_metrics(items) for name, items in sorted(groups.items())}


def _p1_45_distribution(entries: Sequence[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter(str(entry.get(key) or "UNKNOWN") for entry in entries)
    return dict(sorted(counts.items()))


def _p1_45_attribution_analysis(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    fields = [
        "poi_type",
        "human_zone_state_shadow",
        "fvg_state_shadow",
        "fvg_distance_bucket",
        "fvg_filled_pct_bucket",
        "session_label",
        "session_bucket",
        "agent5_trigger_kind",
        "agent5_score_bucket",
        "shadow_tier",
        "delivery_phase",
        "draw_on_liquidity",
        "order_flow",
    ]
    return {
        "total_entries": len(entries),
        "overall": _p1_45_group_metrics(entries),
        **{f"by_{field}": _p1_45_group_by(entries, field) for field in fields},
    }


def _p1_45_fvg_verdict(fvg_entries: Sequence[dict[str, Any]]) -> str:
    metrics = _p1_45_group_metrics(fvg_entries)
    if metrics["entries"] < 20:
        return "FVG_ALT_NOT_ENOUGH_SAMPLE"
    if metrics["avg_r"] > 0.25 and metrics["winrate_tp1"] >= 50:
        return "FVG_ALT_PROMISING"
    if metrics["avg_r"] < 0:
        return "FVG_ALT_TOO_RISKY"
    return "FVG_ALT_NEEDS_STRONGER_CONTEXT"


def _p1_45_fvg_risk_analysis(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    fvg_entries = [entry for entry in entries if entry.get("poi_type") == "FVG_CONTINUATION_ALIGNED"]
    metrics = _p1_45_group_metrics(fvg_entries)
    return {
        "total_fvg_entries": metrics["entries"],
        "fvg_tp1": metrics["tp1"],
        "fvg_tp2": metrics["tp2"],
        "fvg_sl": metrics["sl"],
        "fvg_no_resolution": metrics["no_resolution"],
        "fvg_avg_r": metrics["avg_r"],
        "fvg_winrate_tp1": metrics["winrate_tp1"],
        "fvg_by_session": _p1_45_group_by(fvg_entries, "session_bucket"),
        "fvg_by_distance_bucket": _p1_45_group_by(fvg_entries, "fvg_distance_bucket"),
        "fvg_by_filled_pct_bucket": _p1_45_group_by(fvg_entries, "fvg_filled_pct_bucket"),
        "fvg_by_delivery_phase": _p1_45_group_by(fvg_entries, "delivery_phase"),
        "fvg_by_agent5_trigger_kind": _p1_45_group_by(fvg_entries, "agent5_trigger_kind"),
        "fvg_quality_verdict": _p1_45_fvg_verdict(fvg_entries),
    }


def _p1_45_session_risk_analysis(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    sessions: dict[str, dict[str, Any]] = {}
    for session in ("TOKYO", "LONDON", "NY", "OTHER"):
        items = [entry for entry in entries if entry.get("session_bucket") == session]
        data = _p1_45_group_metrics(items)
        data["poi_type_distribution"] = _p1_45_distribution(items, "poi_type")
        data["trigger_kind_distribution"] = _p1_45_distribution(items, "agent5_trigger_kind")
        sessions[session] = data
    tokyo = sessions["TOKYO"]
    tokyo_verdict = "NOT_ENOUGH_SAMPLE"
    if tokyo["entries"] >= 10 and tokyo["avg_r"] < 0:
        tokyo_verdict = "TOKYO_RISK_CONFIRMED"
    elif tokyo["entries"] > 0:
        tokyo_verdict = "TOKYO_RISK_INCONCLUSIVE"
    return {"sessions": sessions, "tokyo_risk_verdict": tokyo_verdict}


def _p1_45_agent5_trigger_quality(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    expected = [
        "MICRO_CHOCH",
        "MICRO_MSS",
        "DISPLACEMENT",
        "RETEST",
        "MICRO_FVG",
        "COMPRESSION_BREAK",
        "LIQUIDITY_RUN_ACCEPTANCE",
    ]
    grouped = _p1_45_group_by(entries, "agent5_trigger_kind")
    for trigger in expected:
        grouped.setdefault(trigger, _p1_45_group_metrics([]))
    return grouped


def _build_p1_46_professional_strategy_summaries(
    events: list[dict[str, Any]],
    candles_1m: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    decision_events = [event for event in events if event.get("event") == "decision" and event.get("eval_active") is True]
    candle_by_time = {_as_utc(candle["time"]): index for index, candle in enumerate(candles_1m) if candle.get("time") is not None}
    strategy_stats: dict[str, dict[str, Any]] = {}
    selected_entries: list[dict[str, Any]] = []
    selections: list[dict[str, Any]] = []
    strategy_results_count = 0

    enriched_decision_events: list[dict[str, Any]] = []
    for event in decision_events:
        event = _p1_48d0b_enrich_event_active_ob(event, candles_1m)
        enriched_decision_events.append(event)
        evaluation = evaluate_professional_strategies(event)
        shadow = evaluation["human_orchestrator_strategy_shadow"]
        selections.append(shadow)
        strategy_results = evaluation.get("strategy_results", [])
        strategy_results_count += len(strategy_results)
        for result in strategy_results:
            stats = strategy_stats.setdefault(result["strategy_id"], _p1_46_empty_strategy_stats())
            _p1_46_update_strategy_stats(stats, result)

        final_decision = shadow.get("final_shadow_decision", {}) or {}
        selected_id = final_decision.get("selected_entry_strategy_id") or shadow.get("selected_entry_strategy_id")
        if selected_id and final_decision.get("is_executable_entry") is True:
            result = next((item for item in strategy_results if item["strategy_id"] == selected_id), None)
            if result is None:
                continue
            outcome = _simulate_shadow_poi_outcome(event, candles_1m, candle_by_time)
            r_result = _p1_46_r_result(outcome)
            entry = _p1_46_strategy_entry(event, result, evaluation, outcome, r_result)
            selected_entries.append(entry)
            _p1_46_update_strategy_outcome(strategy_stats[selected_id], entry)

    by_strategy = {strategy_id: _p1_46_finalize_strategy_stats(stats) for strategy_id, stats in sorted(strategy_stats.items())}
    context_groups = _p1_47_context_attribution(selected_entries)
    return {
        "shadow_professional_strategy_module_analysis": {
            "evaluations": len(decision_events),
            "strategy_result_count": strategy_results_count,
            "strategies": by_strategy,
        },
        "shadow_strategy_signal_semantics_summary": _p1_48_signal_semantics_summary(selections),
        "shadow_strategy_evaluation_summary": _p1_48_strategy_evaluation_summary(strategy_stats),
        "shadow_strategy_candidate_summary": _p1_48_strategy_candidate_summary(strategy_stats),
        "shadow_professional_strategy_selection_analysis": {
            "evaluations": len(decision_events),
            "selected_count": sum(1 for item in selections if item.get("selected_entry_strategy_id")),
            "decision_distribution": _p1_46_count_values(selections, "decision"),
            "selected_strategy_distribution": _p1_46_count_values(selections, "selected_entry_strategy_id"),
            "blocking_layer_distribution": _p1_46_count_values(selections, "blocking_layer"),
            "examples": selections[:10],
        },
        "shadow_strategy_selection_summary": _p1_48_strategy_selection_summary(selections),
        "shadow_professional_strategy_outcome_analysis": {
            "entries": len(selected_entries),
            "overall": _p1_45_group_metrics(selected_entries),
            "by_strategy": _p1_45_group_by(selected_entries, "strategy_id"),
        },
        "shadow_executable_entry_summary": {
            "entries": len(selected_entries),
            "overall": _p1_45_group_metrics(selected_entries),
            "by_strategy": _p1_45_group_by(selected_entries, "strategy_id"),
        },
        "shadow_non_executable_signal_summary": _p1_48_non_executable_summary(selections),
        "shadow_trigger_none_block_summary": _p1_48_trigger_none_summary(selections),
        "shadow_premium_gate_summary": _p1_48_gate_summary(selections, "premium_strict"),
        "shadow_gate_module_summary": {
            "no_trade_tokyo": _p1_48_gate_summary(selections, "no_trade_tokyo"),
            "premium_strict": _p1_48_gate_summary(selections, "premium_strict"),
            "drawdown_guard": _p1_48_gate_summary(selections, "drawdown_guard"),
        },
        "shadow_ob_strategy_connectivity_summary": _p1_48_ob_connectivity_summary(strategy_stats, selections, selected_entries),
        "shadow_ob_lifecycle_mapping_summary": _p1_48b_ob_lifecycle_mapping_summary(selections),
        "shadow_ob_candidate_block_reason_summary": _p1_48b_ob_candidate_block_reason_summary(selections),
        "shadow_ob_selector_competition_summary": _p1_48b_ob_selector_competition_summary(selections),
        "shadow_ob_candidate_vs_selected_summary": _p1_48b_ob_candidate_vs_selected_summary(strategy_stats, selections, selected_entries),
        "shadow_ob_trigger_block_summary": _p1_48b_ob_trigger_block_summary(selections),
        **_p1_48cbis_ob_exposure_probe_summaries(enriched_decision_events, selections, strategy_stats, selected_entries),
        **_p1_48d0_active_ob_export_summaries(enriched_decision_events, selections, strategy_stats, selected_entries),
        **_p1_48d_ob_five_star_summaries(selections, strategy_stats, selected_entries),
        **_p1_48d0b_ob_evidence_enrichment_summaries(enriched_decision_events, selections),
        "shadow_strategy_ranking_summary": _p1_47_strategy_ranking_summary(by_strategy),
        "shadow_agent_decision_attribution_analysis": _p1_47_agent_decision_attribution(decision_events, selections),
        "shadow_strategy_attribution_by_regime": _p1_45_group_by(selected_entries, "primary_regime"),
        "shadow_strategy_attribution_by_session": _p1_46_session_strategy_attribution(selected_entries),
        "shadow_strategy_attribution_by_poi_type": _p1_45_group_by(selected_entries, "poi_type"),
        "shadow_strategy_attribution_by_trigger": _p1_45_group_by(selected_entries, "trigger_profile"),
        "shadow_strategy_attribution_by_delivery_phase": context_groups["delivery_phase"],
        "shadow_strategy_attribution_by_draw_on_liquidity": context_groups["draw_on_liquidity"],
        "shadow_strategy_attribution_by_order_flow": context_groups["order_flow"],
        "shadow_strategy_attribution_by_strategy_id": _p1_45_group_by(selected_entries, "strategy_id"),
        "shadow_strategy_attribution_by_shadow_tier": _p1_45_group_by(selected_entries, "shadow_tier"),
        "shadow_premium_strict_analysis": by_strategy.get("PREMIUM_STRICT", _p1_46_finalize_strategy_stats(_p1_46_empty_strategy_stats())),
        "shadow_drawdown_guard_analysis": {
            "strategy_drawdown": {strategy_id: data["max_drawdown_r"] for strategy_id, data in by_strategy.items()},
            "session_drawdown": {session: data["max_drawdown_r"] for session, data in _p1_46_session_strategy_attribution(selected_entries).items()},
            "poi_type_drawdown": {poi: data["max_drawdown_r"] for poi, data in _p1_45_group_by(selected_entries, "poi_type").items()},
            "trigger_kind_drawdown": {trigger: data["max_drawdown_r"] for trigger, data in _p1_45_group_by(selected_entries, "trigger_profile").items()},
            "consecutive_losses_by_strategy": {strategy_id: data["consecutive_losses_max"] for strategy_id, data in by_strategy.items()},
        },
    }


def _p1_46_empty_strategy_stats() -> dict[str, Any]:
    return {
        "evaluations": 0,
        "strategy_id": "UNKNOWN",
        "not_applicable_count": 0,
        "applicable_count": 0,
        "candidate_count": 0,
        "standard_shadow_count": 0,
        "premium_shadow_count": 0,
        "reject_count": 0,
        "wait_count": 0,
        "score_sum": 0.0,
        "confidence_sum": 0.0,
        "entries": [],
    }


def _p1_46_update_strategy_stats(stats: dict[str, Any], result: dict[str, Any]) -> None:
    stats["strategy_id"] = str(result.get("strategy_id") or stats.get("strategy_id") or "UNKNOWN")
    stats["evaluations"] += 1
    stats["not_applicable_count"] += int(not bool(result.get("is_applicable")))
    stats["applicable_count"] += int(bool(result.get("is_applicable")))
    permission = str(result.get("permission") or "REJECT")
    stats["candidate_count"] += int(permission == "CANDIDATE")
    stats["standard_shadow_count"] += int(permission == "STANDARD_SHADOW")
    stats["premium_shadow_count"] += int(permission == "PREMIUM_SHADOW")
    stats["reject_count"] += int(permission == "REJECT")
    stats["wait_count"] += int(permission == "WAIT")
    stats["score_sum"] += _float_or_none(result.get("score")) or 0.0
    stats["confidence_sum"] += _float_or_none(result.get("confidence")) or 0.0


def _p1_46_update_strategy_outcome(stats: dict[str, Any], entry: dict[str, Any]) -> None:
    stats["entries"].append(entry)


def _p1_46_finalize_strategy_stats(stats: dict[str, Any]) -> dict[str, Any]:
    metrics = _p1_45_group_metrics(stats["entries"])
    evaluations = stats["evaluations"]
    return {
        "evaluations": evaluations,
        "module_type": _p1_48_module_type_from_stats(stats),
        "not_applicable_count": stats["not_applicable_count"],
        "applicable_count": stats["applicable_count"],
        "candidate_count": stats["candidate_count"],
        "standard_shadow_count": stats["standard_shadow_count"],
        "premium_shadow_count": stats["premium_shadow_count"],
        "reject_count": stats["reject_count"],
        "wait_count": stats["wait_count"],
        "avg_score": round(stats["score_sum"] / evaluations, 4) if evaluations else 0.0,
        "avg_confidence": round(stats["confidence_sum"] / evaluations, 4) if evaluations else 0.0,
        "entries": metrics["entries"],
        "tp1": metrics["tp1"],
        "tp2": metrics["tp2"],
        "sl": metrics["sl"],
        "no_resolution": metrics["no_resolution"],
        "winrate_tp1": metrics["winrate_tp1"],
        "winrate_tp2": metrics["winrate_tp2"],
        "avg_r": metrics["avg_r"],
        "max_drawdown_r": metrics["max_drawdown_r"],
        "consecutive_losses_max": _p1_46_consecutive_losses(stats["entries"]),
        "quality_verdict": _p1_46_strategy_verdict(metrics),
    }


def _p1_48_module_type(strategy_id: str) -> str:
    if strategy_id in {"FVG_NEAR_ONLY", "FVG_NY_LONDON_ONLY", "FVG_SWEEP_DISPLACEMENT_RETEST", "OB_WICK_TAGGED_RETEST", "OB_FIVE_STAR_STRICT"}:
        return "ENTRY_MODEL"
    if strategy_id == "OB_PARTIAL_MITIGATION_WATCH":
        return "WATCH_MODEL"
    if strategy_id == "NO_TRADE_TOKYO":
        return "PERMISSION_GATE"
    if strategy_id == "PREMIUM_STRICT":
        return "TIER_GATE"
    if strategy_id == "CONTEXTUAL_DRAWDOWN_GUARD":
        return "RISK_GATE"
    return "UNKNOWN"


def _p1_48_module_type_from_stats(stats: dict[str, Any]) -> str:
    strategy_id_from_stats = str(stats.get("strategy_id") or "")
    if strategy_id_from_stats:
        return _p1_48_module_type(strategy_id_from_stats)
    for entry in stats.get("entries", []):
        strategy_id = str(entry.get("strategy_id") or "")
        if strategy_id:
            return _p1_48_module_type(strategy_id)
    return "UNKNOWN"


def _p1_48_signal_semantics_summary(selections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    for selection in selections:
        semantics = selection.get("strategy_signal_semantics", {}) or {}
        totals["evaluation_count"] += int(semantics.get("evaluation_count") or 0)
        totals["candidate_count"] += int(semantics.get("candidate_count") or 0)
        totals["selected_count"] += int(semantics.get("selected_count") or 0)
        totals["executable_shadow_entry_count"] += int(semantics.get("executable_shadow_entry_count") or 0)
        totals["non_executable_signal_count"] += int(semantics.get("non_executable_signal_count") or 0)
        totals["trigger_none_block_count"] += int(semantics.get("trigger_none_block_count") or 0)
        totals["premium_strict_standalone_entries"] += int(semantics.get("premium_strict_standalone_entries") or 0)
    return dict(totals)


def _p1_48_strategy_evaluation_summary(strategy_stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for strategy_id, stats in sorted(strategy_stats.items()):
        summary[strategy_id] = {
            "strategy_id": strategy_id,
            "module_type": _p1_48_module_type(strategy_id),
            "evaluations": stats["evaluations"],
            "not_applicable_count": stats["not_applicable_count"],
            "wait_count": stats["wait_count"],
            "candidate_count": stats["candidate_count"],
            "reject_count": stats["reject_count"],
        }
    return summary


def _p1_48_strategy_candidate_summary(strategy_stats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        strategy_id: {
            "module_type": _p1_48_module_type(strategy_id),
            "candidate_count": stats["candidate_count"],
            "standard_shadow_count": stats["standard_shadow_count"],
            "premium_shadow_count": stats["premium_shadow_count"],
        }
        for strategy_id, stats in sorted(strategy_stats.items())
    }


def _p1_48_strategy_selection_summary(selections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    selected = Counter()
    standard = Counter()
    premium = Counter()
    blocked = Counter()
    for selection in selections:
        final = selection.get("final_shadow_decision", {}) or {}
        strategy_id = str(final.get("selected_entry_strategy_id") or "NONE")
        if strategy_id != "NONE":
            selected[strategy_id] += 1
            if final.get("is_executable_entry") is not True:
                blocked[str(final.get("entry_block_reason") or "UNKNOWN")] += 1
            elif final.get("decision") == "PREMIUM_SHADOW":
                premium[strategy_id] += 1
            else:
                standard[strategy_id] += 1
    return {
        "selected_entry_strategy_distribution": dict(selected),
        "standard_shadow_count": dict(standard),
        "premium_shadow_count": dict(premium),
        "blocked_after_selection_count": sum(blocked.values()),
        "main_block_reasons": dict(blocked.most_common(10)),
    }


def _p1_48_non_executable_summary(selections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter()
    for selection in selections:
        final = selection.get("final_shadow_decision", {}) or {}
        if final.get("is_executable_entry") is not True:
            reasons[str(final.get("entry_block_reason") or "UNKNOWN")] += 1
    return {
        "trigger_none_count": reasons.get("TRIGGER_NONE", 0),
        "missing_retest_count": reasons.get("MISSING_RETEST", 0),
        "missing_displacement_count": reasons.get("MISSING_DISPLACEMENT", 0),
        "session_veto_count": reasons.get("SESSION_VETO", 0),
        "gate_reject_count": reasons.get("GATE_REJECT", 0),
        "no_entry_price_count": reasons.get("NO_ENTRY_PRICE", 0),
        "no_stop_count": reasons.get("NO_STOP", 0),
        "no_tp_count": reasons.get("NO_TP", 0),
        "by_reason": dict(reasons.most_common(20)),
    }


def _p1_48_trigger_none_summary(selections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    blocked = [selection for selection in selections if (selection.get("final_shadow_decision", {}) or {}).get("entry_block_reason") == "TRIGGER_NONE"]
    return {
        "trigger_none_block_count": len(blocked),
        "trigger_none_entries": 0,
        "selected_strategy_distribution": _p1_46_count_values(blocked, "selected_entry_strategy_id"),
    }


def _p1_48_gate_summary(selections: Sequence[dict[str, Any]], gate_key: str) -> dict[str, Any]:
    permissions = Counter()
    reasons = Counter()
    applied = 0
    for selection in selections:
        gate = ((selection.get("gates") or {}).get(gate_key) or {})
        if not gate:
            continue
        permissions[str(gate.get("permission") or "UNKNOWN")] += 1
        reasons[str(gate.get("reason") or "UNKNOWN")] += 1
        final = selection.get("final_shadow_decision", {}) or {}
        if str(gate.get("strategy_id") or "") in set(final.get("selected_gate_ids") or []):
            applied += 1
    return {
        "evaluations": sum(permissions.values()),
        "applied_count": applied,
        "permission_distribution": dict(permissions),
        "reason_distribution": dict(reasons.most_common(10)),
    }


def _p1_48_ob_connectivity_summary(
    strategy_stats: dict[str, dict[str, Any]],
    selections: Sequence[dict[str, Any]],
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    ob_strategy_ids = {"OB_WICK_TAGGED_RETEST", "OB_PARTIAL_MITIGATION_WATCH"}
    selected_counts = Counter(
        str((selection.get("final_shadow_decision", {}) or {}).get("selected_entry_strategy_id") or "NONE")
        for selection in selections
    )
    lifecycle_counts = Counter()
    for selection in selections:
        for poi in ((selection.get("ob_strategy_diagnostics") or {}).get("poi_candidates_seen") or []):
            lifecycle_counts[str(poi.get("normalized_lifecycle") or "UNKNOWN")] += 1
    strategy_evaluations = sum(strategy_stats.get(strategy_id, {}).get("evaluations", 0) for strategy_id in ob_strategy_ids)
    strategy_candidates = sum(strategy_stats.get(strategy_id, {}).get("candidate_count", 0) for strategy_id in ob_strategy_ids)
    strategy_selected = sum(selected_counts.get(strategy_id, 0) for strategy_id in ob_strategy_ids)
    strategy_entries = sum(1 for entry in entries if entry.get("strategy_id") in ob_strategy_ids)
    return {
        "ob_poi_seen_total": sum(lifecycle_counts.values()),
        "ob_fresh_seen": lifecycle_counts.get("FRESH", 0),
        "ob_wick_tagged_seen": lifecycle_counts.get("WICK_TAGGED", 0),
        "ob_partially_mitigated_seen": lifecycle_counts.get("PARTIALLY_MITIGATED", 0),
        "ob_consumed_seen": lifecycle_counts.get("CONSUMED", 0),
        "ob_invalidated_seen": lifecycle_counts.get("INVALIDATED", 0),
        "ob_unknown_seen": lifecycle_counts.get("UNKNOWN", 0),
        "ob_strategy_evaluations": strategy_evaluations,
        "ob_strategy_candidates": strategy_candidates,
        "ob_strategy_selected": strategy_selected,
        "ob_strategy_executable_entries": strategy_entries,
        "by_strategy": {
            strategy_id: {
                "module_type": _p1_48_module_type(strategy_id),
                "candidate_count": strategy_stats.get(strategy_id, {}).get("candidate_count", 0),
                "selected_count": selected_counts.get(strategy_id, 0),
                "executable_entries": sum(1 for entry in entries if entry.get("strategy_id") == strategy_id),
            }
            for strategy_id in sorted(ob_strategy_ids)
        },
    }


def _p1_48b_ob_lifecycle_mapping_summary(selections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[tuple[str, str, bool]] = Counter()
    for selection in selections:
        for poi in ((selection.get("ob_strategy_diagnostics") or {}).get("poi_candidates_seen") or []):
            raw = str(poi.get("raw_lifecycle") or "UNKNOWN")
            normalized = str(poi.get("normalized_lifecycle") or "UNKNOWN")
            mapped = bool(poi.get("mapped_successfully"))
            counts[(raw, normalized, mapped)] += 1
    return [
        {
            "raw_lifecycle_value": raw,
            "normalized_lifecycle_value": normalized,
            "count": count,
            "mapped_successfully": mapped,
        }
        for (raw, normalized, mapped), count in sorted(counts.items())
    ]


def _p1_48b_ob_candidate_block_reason_summary(selections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    by_strategy: dict[str, Counter[str]] = defaultdict(Counter)
    totals: Counter[str] = Counter()
    for selection in selections:
        for result in ((selection.get("ob_strategy_diagnostics") or {}).get("results") or []):
            strategy_id = str(result.get("strategy_id") or "UNKNOWN")
            reason = str(result.get("reason") or "UNKNOWN")
            by_strategy[strategy_id][reason] += 1
            totals[strategy_id] += 1
    return {
        strategy_id: [
            {"reason": reason, "count": count, "percentage": round((count / totals[strategy_id]) * 100, 2) if totals[strategy_id] else 0.0}
            for reason, count in reasons.most_common(20)
        ]
        for strategy_id, reasons in sorted(by_strategy.items())
    }


def _p1_48b_ob_selector_competition_summary(selections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    counters = Counter()
    for selection in selections:
        competition = selection.get("selector_competition") or {}
        final = selection.get("final_shadow_decision", {}) or {}
        ob_candidates = competition.get("ob_candidate_strategy_ids") or []
        selected_id = str(final.get("selected_entry_strategy_id") or "NONE")
        block = str(final.get("entry_block_reason") or "NONE")
        if ob_candidates:
            counters["ob_candidates_total"] += len(ob_candidates)
            if selected_id.startswith("OB_"):
                counters["ob_selected_total"] += 1
            if competition.get("ob_lost_to_fvg"):
                counters["ob_lost_to_fvg"] += 1
            if block == "SESSION_VETO":
                counters["ob_lost_to_session_gate"] += 1
            if block in {"TRIGGER_NONE", "NO_TRIGGER"}:
                counters["ob_lost_to_trigger_gate"] += 1
            if "PREMIUM_STRICT" in set(final.get("selected_gate_ids") or []):
                counters["ob_lost_to_premium_gate"] += 1
            if block == "GATE_REJECT":
                counters["ob_lost_to_drawdown_guard"] += 1
    return dict(counters)


def _p1_48b_ob_candidate_vs_selected_summary(
    strategy_stats: dict[str, dict[str, Any]],
    selections: Sequence[dict[str, Any]],
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    selected_counts = Counter(
        str((selection.get("final_shadow_decision", {}) or {}).get("selected_entry_strategy_id") or "NONE")
        for selection in selections
    )
    return {
        strategy_id: {
            "candidate_count": strategy_stats.get(strategy_id, {}).get("candidate_count", 0),
            "selected_count": selected_counts.get(strategy_id, 0),
            "executable_entries": sum(1 for entry in entries if entry.get("strategy_id") == strategy_id),
        }
        for strategy_id in ("OB_WICK_TAGGED_RETEST", "OB_PARTIAL_MITIGATION_WATCH")
    }


def _p1_48b_ob_trigger_block_summary(selections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    reasons = Counter()
    by_strategy = Counter()
    for selection in selections:
        final = selection.get("final_shadow_decision", {}) or {}
        block = str(final.get("entry_block_reason") or "NONE")
        if block not in {"TRIGGER_NONE", "NO_TRIGGER"}:
            continue
        for strategy_id in (selection.get("selector_competition") or {}).get("ob_candidate_strategy_ids") or []:
            reasons[block] += 1
            by_strategy[str(strategy_id)] += 1
    return {"by_reason": dict(reasons), "by_strategy": dict(by_strategy)}


def _p1_48d0b_enrich_event_active_ob(event: dict[str, Any], candles_1m: Sequence[dict[str, Any]]) -> dict[str, Any]:
    agents = event.get("agents") or {}
    agent2 = agents.get("agent_2") or {}
    payload = agent2.get("payload") or {}
    diagnostic = payload.get("diagnostic") or {}
    active_ob = diagnostic.get("active_ob") or payload.get("active_ob") or agent2.get("active_ob")
    if not isinstance(active_ob, dict):
        return event
    enriched_event = deepcopy(event)
    enriched_agent2 = enriched_event.setdefault("agents", {}).setdefault("agent_2", {})
    enriched_payload = enriched_agent2.setdefault("payload", {})
    enriched_diag = enriched_payload.setdefault("diagnostic", {})
    enriched_ob = enrich_active_ob_with_five_star_evidence(
        active_ob,
        candles_1m,
        {"event_time": event.get("time"), "eval_active": event.get("eval_active")},
        event.get("time"),
    )
    enriched_diag["active_ob"] = enriched_ob
    if isinstance(enriched_payload.get("active_ob"), dict):
        enriched_payload["active_ob"] = enriched_ob
    if isinstance(enriched_agent2.get("active_ob"), dict):
        enriched_agent2["active_ob"] = enriched_ob
    return enriched_event


def _p1_48d0_active_ob_export_summaries(
    decision_events: Sequence[dict[str, Any]],
    selections: Sequence[dict[str, Any]],
    strategy_stats: dict[str, dict[str, Any]],
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    extracted_by_event = [_p1_48cbis_extract_agent2_pois(event) for event in decision_events]
    flat = [poi for items in extracted_by_event for poi in items]
    active_obs = [poi for poi in flat if poi.get("source_field") == "active_ob" and poi.get("normalized_poi_type") == "OB"]
    strategy_input_pois = [
        poi
        for selection in selections
        for poi in ((selection.get("ob_strategy_diagnostics") or {}).get("poi_candidates_seen") or [])
    ]
    ob_strategy_input = [poi for poi in strategy_input_pois if str(poi.get("poi_type") or "").startswith("OB") or poi.get("source_field") == "active_ob"]
    active_ob_strategy_input = [poi for poi in ob_strategy_input if poi.get("source_field") == "active_ob"]
    block_reasons = Counter()
    for selection in selections:
        for result in ((selection.get("ob_strategy_diagnostics") or {}).get("results") or []):
            block_reasons[str(result.get("reason") or "UNKNOWN")] += 1
    missing_lifecycle = sum(1 for poi in active_obs if poi.get("normalized_lifecycle") == "UNKNOWN")
    missing_direction = sum(1 for poi in active_obs if str(poi.get("direction") or "unknown").lower() == "unknown")
    missing_price = sum(1 for poi in active_obs if not poi.get("has_price_fields"))
    connectivity = _p1_48_ob_connectivity_summary(strategy_stats, selections, entries)
    wick_evals = int(strategy_stats.get("OB_WICK_TAGGED_RETEST", {}).get("evaluations") or 0)
    partial_evals = int(strategy_stats.get("OB_PARTIAL_MITIGATION_WATCH", {}).get("evaluations") or 0)
    exported_total = len(active_ob_strategy_input)
    examples = _p1_48d0_active_ob_examples(selections)
    return {
        "shadow_agent2_active_ob_export_summary": {
            "active_ob_seen_total": len(active_obs),
            "active_ob_valid_shape_total": len(active_obs) - missing_price,
            "active_ob_missing_price_fields": missing_price,
            "active_ob_missing_lifecycle": missing_lifecycle,
            "active_ob_missing_direction": missing_direction,
            "active_ob_exported_total": exported_total,
            "active_ob_not_exported_total": max(len(active_obs) - exported_total, 0),
            "main_not_exported_reasons": {"ACTIVE_OB_NOT_IN_STRATEGY_INPUT": max(len(active_obs) - exported_total, 0)} if len(active_obs) > exported_total else {},
        },
        "shadow_ob_export_routing_summary": {
            "ob_from_active_ob_total": len(active_obs),
            "ob_added_to_normalized_poi_stack": len(active_obs),
            "ob_added_to_strategy_input_poi_stack": len(active_ob_strategy_input),
            "ob_seen_by_selector": len(ob_strategy_input),
            "ob_seen_by_ob_modules": len(ob_strategy_input),
            "ob_lost_before_selector": max(len(active_obs) - len(active_ob_strategy_input), 0),
            "main_drop_reason": "AVAILABLE" if active_ob_strategy_input else "ACTIVE_OB_NOT_EXPORTED_TO_STRATEGY_INPUT",
        },
        "shadow_strategy_input_poi_stack_summary": {
            "total_strategy_input_poi": len(strategy_input_pois),
            "ob_strategy_input_poi": len(ob_strategy_input),
            "fvg_strategy_input_poi": sum(1 for poi in strategy_input_pois if str(poi.get("poi_type") or "").startswith("FVG")),
            "unknown_strategy_input_poi": sum(1 for poi in strategy_input_pois if not str(poi.get("poi_type") or "")),
            "source_field_distribution": dict(Counter(str(poi.get("source_field") or "UNKNOWN") for poi in strategy_input_pois).most_common(20)),
        },
        "shadow_ob_module_visibility_after_export_summary": {
            "OB_WICK_TAGGED_RETEST_evaluations": wick_evals,
            "OB_WICK_TAGGED_RETEST_seen_ob": len(ob_strategy_input),
            "OB_WICK_TAGGED_RETEST_candidates": int(strategy_stats.get("OB_WICK_TAGGED_RETEST", {}).get("candidate_count") or 0),
            "OB_WICK_TAGGED_RETEST_selected": sum(1 for selection in selections if ((selection.get("final_shadow_decision") or {}).get("selected_entry_strategy_id") == "OB_WICK_TAGGED_RETEST")),
            "OB_PARTIAL_MITIGATION_WATCH_evaluations": partial_evals,
            "OB_PARTIAL_MITIGATION_WATCH_seen_ob": len(ob_strategy_input),
            "OB_PARTIAL_MITIGATION_WATCH_candidates": int(strategy_stats.get("OB_PARTIAL_MITIGATION_WATCH", {}).get("candidate_count") or 0),
            "OB_PARTIAL_MITIGATION_WATCH_selected": sum(1 for selection in selections if ((selection.get("final_shadow_decision") or {}).get("selected_entry_strategy_id") == "OB_PARTIAL_MITIGATION_WATCH")),
        },
        "shadow_ob_export_block_reason_summary": dict(block_reasons.most_common(20)),
        "shadow_ob_export_examples_summary": {"examples": examples},
    }


def _p1_48d0_active_ob_examples(selections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for selection in selections:
        diagnostics = selection.get("ob_strategy_diagnostics") or {}
        active_pois = [
            poi for poi in diagnostics.get("poi_candidates_seen") or []
            if poi.get("source_field") == "active_ob"
        ]
        if not active_pois:
            continue
        result = next(iter(diagnostics.get("results") or []), {})
        for poi in active_pois:
            examples.append({
                "timestamp": selection.get("timestamp"),
                "source_field": "active_ob",
                "normalized_poi_type": "OB",
                "normalized_lifecycle": poi.get("normalized_lifecycle"),
                "exported_to_strategy_input": True,
                "seen_by_ob_module": True,
                "ob_module_result": result.get("permission"),
                "block_reason": result.get("reason"),
            })
            if len(examples) >= 30:
                return examples
    return examples


def _p1_48d0b_ob_evidence_enrichment_summaries(
    decision_events: Sequence[dict[str, Any]],
    selections: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    active_obs = _p1_48d0b_enriched_active_obs(decision_events)
    evidences = [ob.get("five_star_evidence") or {} for ob in active_obs]
    total = len(evidences)
    missing = Counter()
    sources = Counter()
    before_stars = []
    for evidence in evidences:
        before_stars.append(float(evidence.get("before_enrichment_star_count") or 0.0))
        for reason in evidence.get("missing_evidence") or []:
            missing[str(reason)] += 1
        for source_key in ("imbalance_source", "liquidity_sweep_source", "extreme_ob_source", "session_created_source", "golden_hour_source"):
            sources[f"{source_key}:{evidence.get(source_key) or 'UNKNOWN'}"] += 1

    scoring = _p1_48d_ob_five_star_summaries(selections, {}, [])["shadow_ob_five_star_scoring_summary"]
    no_lookahead_ok = all(
        ((evidence.get("no_lookahead_guard") or {}).get("future_candles_used") is False)
        for evidence in evidences
    )
    return {
        "shadow_ob_evidence_enrichment_summary": {
            "active_ob_seen_total": total,
            "active_ob_enriched_total": sum(1 for evidence in evidences if evidence),
            "active_ob_not_enriched_total": sum(1 for evidence in evidences if not evidence),
            "ob_with_imbalance_evidence": sum(1 for evidence in evidences if evidence.get("imbalance_created") is True),
            "ob_with_sweep_evidence": sum(1 for evidence in evidences if evidence.get("liquidity_sweep_before") is True),
            "ob_with_extreme_evidence": sum(1 for evidence in evidences if evidence.get("is_extreme_ob") is True),
            "ob_with_session_created_evidence": sum(1 for evidence in evidences if evidence.get("session_created") in {"LONDON", "NY", "TOKYO", "OTHER"}),
            "ob_with_golden_hour_evidence": sum(1 for evidence in evidences if evidence.get("golden_hour_return") is True),
        },
        "shadow_ob_evidence_coverage_summary": {
            "imbalance_coverage_pct": _p1_48d0b_pct(sum(1 for evidence in evidences if evidence.get("imbalance_created") is True), total),
            "sweep_coverage_pct": _p1_48d0b_pct(sum(1 for evidence in evidences if evidence.get("liquidity_sweep_before") is True), total),
            "extreme_coverage_pct": _p1_48d0b_pct(sum(1 for evidence in evidences if evidence.get("is_extreme_ob") is True), total),
            "session_created_coverage_pct": _p1_48d0b_pct(
                sum(1 for evidence in evidences if evidence.get("session_created") in {"LONDON", "NY", "TOKYO", "OTHER"}),
                total,
            ),
            "golden_hour_coverage_pct": _p1_48d0b_pct(sum(1 for evidence in evidences if evidence.get("golden_hour_return") is True), total),
        },
        "shadow_ob_five_star_after_enrichment_summary": {
            "ob_scored_total": scoring.get("ob_scored_total", 0),
            "five_star_total": scoring.get("five_star_total", 0),
            "four_star_total": scoring.get("four_star_total", 0),
            "three_star_total": scoring.get("three_star_total", 0),
            "low_quality_total": scoring.get("low_quality_total", 0),
            "invalid_ob_total": scoring.get("invalid_ob_total", 0),
            "avg_star_count_before": round(sum(before_stars) / len(before_stars), 4) if before_stars else 0.0,
            "avg_star_count_after": scoring.get("avg_star_count", 0.0),
            "avg_score_pct_before": round((sum(before_stars) / len(before_stars)) * 20.0, 4) if before_stars else 0.0,
            "avg_score_pct_after": scoring.get("avg_score_pct", 0.0),
        },
        "shadow_ob_evidence_missing_reason_summary": dict(missing.most_common(20)),
        "shadow_ob_evidence_source_summary": dict(sources.most_common(30)),
        "shadow_ob_evidence_examples_summary": {"examples": _p1_48d0b_evidence_examples(active_obs)},
        "shadow_ob_no_lookahead_guard_summary": {
            "status": "OK" if no_lookahead_ok else "FAILED",
            "records_checked": total,
            "future_candles_used": not no_lookahead_ok,
        },
    }


def _p1_48d0b_enriched_active_obs(decision_events: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    obs: list[dict[str, Any]] = []
    for event in decision_events:
        agent2 = ((event.get("agents") or {}).get("agent_2") or {})
        payload = agent2.get("payload") or {}
        diag = payload.get("diagnostic") or {}
        active_ob = diag.get("active_ob") or payload.get("active_ob") or agent2.get("active_ob")
        if isinstance(active_ob, dict):
            obs.append(active_ob)
    return obs


def _p1_48d0b_evidence_examples(active_obs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for ob in active_obs:
        evidence = ob.get("five_star_evidence") or {}
        if not evidence:
            continue
        examples.append({
            "source_field": "active_ob",
            "lifecycle": ob.get("human_zone_state_shadow") or ob.get("state_shadow") or ("FRESH" if ob.get("fresh") else "UNKNOWN"),
            "imbalance_created": evidence.get("imbalance_created"),
            "liquidity_sweep_before": evidence.get("liquidity_sweep_before"),
            "is_extreme_ob": evidence.get("is_extreme_ob"),
            "session_created": evidence.get("session_created"),
            "golden_hour_return": evidence.get("golden_hour_return"),
            "missing_evidence": evidence.get("missing_evidence"),
            "no_lookahead_guard": evidence.get("no_lookahead_guard"),
        })
        if len(examples) >= 30:
            break
    return examples


def _p1_48d0b_pct(count: int, total: int) -> float:
    return round((count / total) * 100.0, 2) if total else 0.0


def _p1_48d_ob_five_star_summaries(
    selections: Sequence[dict[str, Any]],
    strategy_stats: dict[str, dict[str, Any]],
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    results = _p1_48d_ob_five_star_results(selections)
    scored = [result for result in results if result.get("is_applicable")]
    tiers = Counter(str((result.get("ob_five_star") or {}).get("quality_tier") or "UNKNOWN") for result in scored)
    score_values = [_float_or_none((result.get("ob_five_star") or {}).get("score_pct")) or 0.0 for result in scored]
    star_values = [_float_or_none((result.get("ob_five_star") or {}).get("base_star_count")) or 0.0 for result in scored]
    selected_total = sum(
        1 for selection in selections
        if ((selection.get("final_shadow_decision") or {}).get("selected_entry_strategy_id") == "OB_FIVE_STAR_STRICT")
    )
    executable_entries = sum(1 for entry in entries if entry.get("strategy_id") == "OB_FIVE_STAR_STRICT")
    block_reasons = Counter(str(result.get("reason") or "UNKNOWN") for result in scored)
    selected_block_reasons = Counter(
        str((selection.get("final_shadow_decision") or {}).get("entry_block_reason") or "NONE")
        for selection in selections
        if ((selection.get("final_shadow_decision") or {}).get("selected_entry_strategy_id") == "OB_FIVE_STAR_STRICT")
    )
    competition = _p1_48d_ob_five_star_competition(selections)
    return {
        "shadow_ob_five_star_scoring_summary": {
            "ob_scored_total": len(scored),
            "five_star_total": tiers.get("FIVE_STAR_STRICT", 0),
            "four_star_total": tiers.get("FOUR_STAR_WATCH", 0),
            "three_star_total": tiers.get("THREE_STAR_WEAK", 0),
            "low_quality_total": tiers.get("LOW_QUALITY_OB", 0),
            "invalid_ob_total": tiers.get("INVALID_OB", 0),
            "avg_star_count": round(sum(star_values) / len(star_values), 4) if star_values else 0.0,
            "avg_score_pct": round(sum(score_values) / len(score_values), 4) if score_values else 0.0,
            "golden_hour_bonus_total": sum(1 for result in scored if ((result.get("ob_five_star") or {}).get("bonus") or {}).get("golden_hour_return", {}).get("passed")),
        },
        "shadow_ob_five_star_breakdown_summary": _p1_48d_ob_five_star_breakdown(scored),
        "shadow_ob_five_star_quality_tier_summary": dict(tiers.most_common()),
        "shadow_ob_five_star_candidate_summary": {
            "five_star_candidates": sum(1 for result in scored if (result.get("ob_five_star") or {}).get("quality_tier") == "FIVE_STAR_STRICT" and result.get("permission") == "CANDIDATE"),
            "four_star_candidates": sum(1 for result in scored if (result.get("ob_five_star") or {}).get("quality_tier") == "FOUR_STAR_WATCH" and result.get("permission") == "CANDIDATE"),
            "selected_by_selector": selected_total,
            "executable_entries": executable_entries,
            "blocked_by_trigger_none": block_reasons.get("OB_FIVE_STAR_TRIGGER_NONE", 0) + selected_block_reasons.get("TRIGGER_NONE", 0),
            "blocked_waiting_m1_confirmation": block_reasons.get("OB_FIVE_STAR_WAITING_FOR_M1_CONFIRMATION", 0),
            "blocked_by_session": block_reasons.get("OB_FIVE_STAR_SESSION_VETO", 0),
            "blocked_by_news": block_reasons.get("OB_FIVE_STAR_NEWS_VETO", 0),
            "blocked_by_risk": selected_block_reasons.get("GATE_REJECT", 0),
        },
        "shadow_ob_five_star_selector_competition_summary": competition,
        "shadow_ob_five_star_block_reason_summary": dict(block_reasons.most_common(20)),
        "shadow_ob_five_star_examples_summary": {"examples": _p1_48d_ob_five_star_examples(selections)},
        "shadow_ob_five_star_readiness_for_agent5_summary": {
            "five_star_or_four_star_total": tiers.get("FIVE_STAR_STRICT", 0) + tiers.get("FOUR_STAR_WATCH", 0),
            "waiting_m1_confirmation": block_reasons.get("OB_FIVE_STAR_WAITING_FOR_M1_CONFIRMATION", 0) + block_reasons.get("OB_FIVE_STAR_TRIGGER_NONE", 0),
            "trigger_none": block_reasons.get("OB_FIVE_STAR_TRIGGER_NONE", 0),
            "strategy_evaluations": int(strategy_stats.get("OB_FIVE_STAR_STRICT", {}).get("evaluations") or 0),
            "strategy_candidates": int(strategy_stats.get("OB_FIVE_STAR_STRICT", {}).get("candidate_count") or 0),
        },
    }


def _p1_48d_ob_five_star_results(selections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for selection in selections:
        for result in ((selection.get("ob_strategy_diagnostics") or {}).get("results") or []):
            if result.get("strategy_id") == "OB_FIVE_STAR_STRICT":
                results.append(result)
    return results


def _p1_48d_ob_five_star_breakdown(results: Sequence[dict[str, Any]]) -> dict[str, int]:
    counters = Counter()
    for result in results:
        stars = ((result.get("ob_five_star") or {}).get("stars") or {})
        if ((result.get("ob_five_star") or {}).get("bonus") or {}).get("golden_hour_return", {}).get("passed"):
            counters["golden_hour_bonus_pass"] += 1
        for key, counter_name in (
            ("imbalance_created", "imbalance_created_pass"),
            ("liquidity_sweep_before", "liquidity_sweep_pass"),
            ("extreme_ob", "extreme_ob_pass"),
            ("unmitigated", "unmitigated_pass"),
            ("london_or_ny_creation", "london_ny_creation_pass"),
        ):
            counters[counter_name] += int(bool((stars.get(key) or {}).get("passed")))
    return dict(counters)


def _p1_48d_ob_five_star_competition(selections: Sequence[dict[str, Any]]) -> dict[str, Any]:
    counters = Counter()
    for selection in selections:
        competition = selection.get("selector_competition") or {}
        final = selection.get("final_shadow_decision") or {}
        ob_candidates = set(competition.get("ob_candidate_strategy_ids") or [])
        if "OB_FIVE_STAR_STRICT" not in ob_candidates:
            continue
        counters["ob_five_star_candidates_total"] += 1
        selected = str(final.get("selected_entry_strategy_id") or "NONE")
        if selected == "OB_FIVE_STAR_STRICT":
            counters["ob_five_star_selected"] += 1
        elif selected.startswith("FVG"):
            counters["OB_FIVE_STAR_LOST_TO_FVG"] += 1
        block = str(final.get("entry_block_reason") or "NONE")
        if block == "SESSION_VETO":
            counters["OB_FIVE_STAR_SESSION_VETO"] += 1
        if block == "TRIGGER_NONE":
            counters["OB_FIVE_STAR_TRIGGER_NONE"] += 1
    return dict(counters)


def _p1_48d_ob_five_star_examples(selections: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for selection in selections:
        diagnostics = selection.get("ob_strategy_diagnostics") or {}
        for result in diagnostics.get("results") or []:
            if result.get("strategy_id") != "OB_FIVE_STAR_STRICT" or not result.get("is_applicable"):
                continue
            scoring = result.get("ob_five_star") or {}
            examples.append({
                "timestamp": selection.get("timestamp"),
                "strategy_id": "OB_FIVE_STAR_STRICT",
                "permission": result.get("permission"),
                "reason": result.get("reason"),
                "quality_tier": scoring.get("quality_tier"),
                "base_star_count": scoring.get("base_star_count"),
                "score_pct": scoring.get("score_pct"),
                "source_field": (result.get("poi") or {}).get("source_field"),
                "lifecycle": (result.get("poi") or {}).get("lifecycle_normalized"),
            })
            if len(examples) >= 30:
                return examples
    return examples


def _p1_48cbis_ob_exposure_probe_summaries(
    decision_events: Sequence[dict[str, Any]],
    selections: Sequence[dict[str, Any]],
    strategy_stats: dict[str, dict[str, Any]],
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    extracted_by_event = [_p1_48cbis_extract_agent2_pois(event) for event in decision_events]
    flat = [poi for items in extracted_by_event for poi in items]
    ob_items = [poi for poi in flat if poi["normalized_poi_type"] == "OB"]
    fvg_items = [poi for poi in flat if poi["normalized_poi_type"] == "FVG"]
    unknown_items = [poi for poi in flat if poi["normalized_poi_type"] == "UNKNOWN"]
    lifecycle_counts = Counter(poi["normalized_lifecycle"] for poi in ob_items)
    field_counts = Counter(poi["source_field"] for poi in flat)
    ob_field_counts = Counter(poi["source_field"] for poi in ob_items)
    raw_lifecycle = Counter(str(poi["raw_lifecycle"] or "UNKNOWN") for poi in ob_items)
    examples = _p1_48cbis_ob_examples(decision_events, selections, extracted_by_event)
    connectivity = _p1_48_ob_connectivity_summary(strategy_stats, selections, entries)

    records_with_any_poi = sum(1 for items in extracted_by_event if items)
    records_with_ob = sum(1 for items in extracted_by_event if any(item["normalized_poi_type"] == "OB" for item in items))
    funnel = _p1_48cbis_ob_funnel(flat, selections, strategy_stats, entries)
    discrepancy = _p1_48cbis_ob_discrepancy_summary(decision_events, ob_items, connectivity)
    main_drop_stage = funnel.get("main_ob_drop_stage", "UNKNOWN")
    main_drop_reason = funnel.get("main_ob_drop_reason", "UNKNOWN")

    probe = {
        "total_records_checked": len(decision_events),
        "records_with_any_poi": records_with_any_poi,
        "records_with_ob_in_any_field": records_with_ob,
        "records_with_ob_in_best_shadow_poi": ob_field_counts.get("best_shadow_poi", 0),
        "records_with_ob_in_shadow_agent2_poi_stack": ob_field_counts.get("shadow_agent2_poi_stack", 0),
        "records_with_ob_in_shadow_agent2_ict_poi_stack": ob_field_counts.get("shadow_agent2_ict_poi_stack", 0),
        "records_with_ob_in_zone_lifecycle": ob_field_counts.get("shadow_agent2_zone_lifecycle", 0),
        "records_with_ob_in_other_fields": sum(
            count
            for field, count in ob_field_counts.items()
            if field not in {"best_shadow_poi", "shadow_agent2_poi_stack", "shadow_agent2_ict_poi_stack", "shadow_agent2_zone_lifecycle"}
        ),
        "ob_poi_seen_total": len(ob_items),
        "ob_fresh_seen": lifecycle_counts.get("FRESH", 0),
        "ob_wick_tagged_seen": lifecycle_counts.get("WICK_TAGGED", 0),
        "ob_partially_mitigated_seen": lifecycle_counts.get("PARTIALLY_MITIGATED", 0),
        "ob_mitigated_seen": lifecycle_counts.get("MITIGATED", 0),
        "ob_consumed_seen": lifecycle_counts.get("CONSUMED", 0),
        "ob_invalidated_seen": lifecycle_counts.get("INVALIDATED", 0),
        "ob_stale_seen": lifecycle_counts.get("STALE", 0),
        "ob_unknown_seen": lifecycle_counts.get("UNKNOWN", 0),
        "fvg_poi_seen_total": len(fvg_items),
        "unknown_poi_seen_total": len(unknown_items),
        "ob_strategy_evaluations": connectivity.get("ob_strategy_evaluations", 0),
        "ob_strategy_candidates": connectivity.get("ob_strategy_candidates", 0),
        "ob_strategy_selected": connectivity.get("ob_strategy_selected", 0),
        "ob_strategy_executable_entries": connectivity.get("ob_strategy_executable_entries", 0),
        "main_ob_drop_stage": main_drop_stage,
        "main_ob_drop_reason": main_drop_reason,
    }
    return {
        "shadow_agent2_ob_exposure_probe_summary": probe,
        "shadow_ob_exposure_funnel_summary": funnel,
        "shadow_ob_raw_field_inventory_summary": {
            "source_field_distribution": dict(field_counts.most_common(30)),
            "ob_source_field_distribution": dict(ob_field_counts.most_common(30)),
            "raw_poi_type_distribution": dict(Counter(str(poi["raw_poi_type"] or "UNKNOWN") for poi in flat).most_common(30)),
            "normalized_poi_type_distribution": dict(Counter(poi["normalized_poi_type"] for poi in flat).most_common(10)),
            "missing_field_count": sum(1 for poi in flat if poi["missing_field"]),
        },
        "shadow_ob_lifecycle_raw_distribution_summary": {
            "raw_lifecycle_distribution": dict(raw_lifecycle.most_common(30)),
            "normalized_lifecycle_distribution": dict(lifecycle_counts.most_common(10)),
        },
        "shadow_ob_attribution_discrepancy_summary": discrepancy,
        "shadow_ob_probe_examples_summary": {
            "examples": examples,
            "message": "Aucun OB expose dans la fenetre testee." if not examples else "OB examples found.",
        },
        "shadow_ob_wider_probe_window_summary": {
            "records_checked": len(decision_events),
            "summary_only": True,
            "max_examples": 50,
            "purpose": "OB_EXPOSURE_DIAGNOSTIC_ONLY",
        },
        "shadow_ob_export_field_presence_summary": {
            "explicit_fields_checked": _P1_48CBIS_EXPLICIT_AGENT2_POI_FIELDS,
            "source_field_distribution": dict(field_counts.most_common(50)),
            "ob_source_field_distribution": dict(ob_field_counts.most_common(50)),
            "missing_explicit_fields": [
                field for field in _P1_48CBIS_EXPLICIT_AGENT2_POI_FIELDS
                if field_counts.get(field, 0) == 0
            ],
        },
        "shadow_ob_strategy_visibility_summary": {
            "ob_strategy_evaluations": connectivity.get("ob_strategy_evaluations", 0),
            "ob_strategy_candidates": connectivity.get("ob_strategy_candidates", 0),
            "ob_strategy_selected": connectivity.get("ob_strategy_selected", 0),
            "ob_strategy_executable_entries": connectivity.get("ob_strategy_executable_entries", 0),
            "ob_reached_strategy_input": funnel.get("stages", {}).get("strategy_input_poi_stack", {}).get("ob_poi_count", 0),
        },
    }


_P1_48CBIS_EXPLICIT_AGENT2_POI_FIELDS = (
    "best_shadow_poi",
    "shadow_agent2_poi_stack",
    "shadow_agent2_ict_poi_stack",
    "shadow_agent2_zone_lifecycle",
    "shadow_agent2_poi_selection",
    "shadow_agent2_fvg_alternative",
    "shadow_agent2_ob_lifecycle",
    "shadow_agent2_contextual_poi",
    "active_ob",
    "poi_zone",
    "order_block",
    "agent2_output",
)


def _p1_48cbis_extract_agent2_pois(event: dict[str, Any]) -> list[dict[str, Any]]:
    agent2 = ((event.get("agents") or {}).get("agent_2") or {})
    payload = agent2.get("payload") or {}
    diag = payload.get("diagnostic") or {}
    found: list[dict[str, Any]] = []

    def add(source: str, value: Any) -> None:
        if isinstance(value, dict):
            found.append(_p1_48cbis_normalize_raw_poi(source, value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found.append(_p1_48cbis_normalize_raw_poi(source, item))

    for key in _P1_48CBIS_EXPLICIT_AGENT2_POI_FIELDS:
        add(key, diag.get(key) if key in diag else payload.get(key))

    for source, value in _p1_48cbis_walk_poi_like(diag):
        if source not in set(_P1_48CBIS_EXPLICIT_AGENT2_POI_FIELDS):
            add(source, value)

    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in found:
        key = (
            item["source_field"],
            str(item["raw_poi_type"]),
            str(item["raw_lifecycle"]),
            str(item.get("score") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _p1_48cbis_walk_poi_like(root: Any, prefix: str = "") -> list[tuple[str, Any]]:
    matches: list[tuple[str, Any]] = []
    if isinstance(root, dict):
        for key, value in root.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            lowered = str(key).lower()
            if any(token in lowered for token in ("poi", "zone", "ob", "order_block", "block", "fvg", "lifecycle", "mitigated", "wick", "fresh", "consumed", "invalidated", "agent2")):
                if isinstance(value, (dict, list)):
                    matches.append((path, value))
            matches.extend(_p1_48cbis_walk_poi_like(value, path))
    elif isinstance(root, list):
        for index, value in enumerate(root):
            matches.extend(_p1_48cbis_walk_poi_like(value, f"{prefix}[{index}]"))
    return matches


def _p1_48cbis_normalize_raw_poi(source_field: str, raw: dict[str, Any]) -> dict[str, Any]:
    raw_type = (
        raw.get("priority_label")
        or raw.get("poi_type")
        or raw.get("zone_type")
        or raw.get("type")
        or raw.get("kind")
        or source_field
        or "UNKNOWN"
    )
    raw_lifecycle = (
        raw.get("human_zone_state_shadow")
        or raw.get("state_shadow")
        or raw.get("zone_lifecycle_state")
        or raw.get("lifecycle")
        or raw.get("state")
        or ("FRESH" if raw.get("fresh") is True else None)
        or ("MITIGATED" if raw.get("mitigated") is True else None)
        or "UNKNOWN"
    )
    normalized_type = _p1_48cbis_normalize_poi_type(raw_type, source_field)
    normalized_lifecycle = normalize_ob_lifecycle(raw_lifecycle)
    missing = not any(key in raw for key in ("priority_label", "poi_type", "zone_type", "type", "kind"))
    return {
        "source_field": source_field,
        "raw_poi_type": str(raw_type),
        "normalized_poi_type": normalized_type,
        "raw_lifecycle": str(raw_lifecycle),
        "normalized_lifecycle": normalized_lifecycle,
        "score": raw.get("score", raw.get("score_shadow")),
        "direction": raw.get("direction") or raw.get("ob_type") or raw.get("type") or "unknown",
        "has_price_fields": any(key in raw for key in ("high", "low", "open", "close", "top", "bottom", "entry_zone_top", "entry_zone_bottom")),
        "missing_field": missing,
    }


def _p1_48cbis_normalize_poi_type(raw_type: Any, source_field: str = "") -> str:
    value = f"{raw_type} {source_field}".upper()
    if "ORDER_BLOCK" in value or "OB" in value:
        return "OB"
    if "FVG" in value:
        return "FVG"
    if "BPR" in value:
        return "BPR"
    if "LIQUIDITY" in value:
        return "LIQUIDITY_POOL"
    return "UNKNOWN"


def _p1_48cbis_ob_funnel(
    flat_pois: Sequence[dict[str, Any]],
    selections: Sequence[dict[str, Any]],
    strategy_stats: dict[str, dict[str, Any]],
    entries: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    stages: dict[str, dict[str, Any]] = {}

    def stage(name: str, pois: Sequence[dict[str, Any]], reason: str) -> None:
        stages[name] = {
            "records_checked": len(selections) or 0,
            "records_with_any_poi": 1 if pois else 0,
            "total_poi": len(pois),
            "ob_poi_count": sum(1 for poi in pois if poi["normalized_poi_type"] == "OB"),
            "fvg_poi_count": sum(1 for poi in pois if poi["normalized_poi_type"] == "FVG"),
            "unknown_poi_count": sum(1 for poi in pois if poi["normalized_poi_type"] == "UNKNOWN"),
            "missing_field_count": sum(1 for poi in pois if poi["missing_field"]),
            "main_drop_reason": reason,
        }

    best = [poi for poi in flat_pois if poi["source_field"] == "best_shadow_poi"]
    stack = [poi for poi in flat_pois if poi["source_field"].startswith("shadow_agent2_poi_stack")]
    ict_stack = [poi for poi in flat_pois if poi["source_field"].startswith("shadow_agent2_ict_poi_stack")]
    zone_lifecycle = [poi for poi in flat_pois if poi["source_field"].startswith("shadow_agent2_zone_lifecycle")]
    strategy_input = [
        {
            "normalized_poi_type": "OB",
            "missing_field": False,
            "source_field": "strategy_input_poi_stack",
        }
        for selection in selections
        for _ in ((selection.get("ob_strategy_diagnostics") or {}).get("poi_candidates_seen") or [])
    ]
    stage("raw_agent2_outputs", flat_pois, "NO_AGENT2_POI_FIELDS" if not flat_pois else "AVAILABLE")
    stage("best_shadow_poi", best, "NO_BEST_SHADOW_POI" if not best else "AVAILABLE")
    stage("shadow_agent2_poi_stack", stack, "NO_SHADOW_AGENT2_POI_STACK" if not stack else "AVAILABLE")
    stage("shadow_agent2_ict_poi_stack", ict_stack, "NO_SHADOW_AGENT2_ICT_POI_STACK" if not ict_stack else "AVAILABLE")
    stage("shadow_agent2_zone_lifecycle", zone_lifecycle, "NO_SHADOW_AGENT2_ZONE_LIFECYCLE" if not zone_lifecycle else "AVAILABLE")
    stage("normalized_poi_stack", flat_pois, "NO_OB_AFTER_NORMALIZATION" if not any(p["normalized_poi_type"] == "OB" for p in flat_pois) else "AVAILABLE")
    stage("strategy_input_poi_stack", strategy_input, "OB_NOT_REACHED_SELECTOR" if not strategy_input else "AVAILABLE")

    ob_evals = sum(strategy_stats.get(strategy_id, {}).get("evaluations", 0) for strategy_id in ("OB_WICK_TAGGED_RETEST", "OB_PARTIAL_MITIGATION_WATCH"))
    ob_candidates = sum(strategy_stats.get(strategy_id, {}).get("candidate_count", 0) for strategy_id in ("OB_WICK_TAGGED_RETEST", "OB_PARTIAL_MITIGATION_WATCH"))
    ob_selected = sum(
        1 for selection in selections
        if str((selection.get("final_shadow_decision") or {}).get("selected_entry_strategy_id") or "").startswith("OB_")
    )
    ob_entries = sum(1 for entry in entries if str(entry.get("strategy_id") or "").startswith("OB_"))
    stages["ob_strategy_evaluated"] = {"total_poi": ob_evals, "ob_poi_count": ob_evals, "fvg_poi_count": 0, "unknown_poi_count": 0, "missing_field_count": 0, "main_drop_reason": "NO_OB_STRATEGY_EVALUATION" if not ob_evals else "AVAILABLE"}
    stages["ob_strategy_candidate"] = {"total_poi": ob_candidates, "ob_poi_count": ob_candidates, "fvg_poi_count": 0, "unknown_poi_count": 0, "missing_field_count": 0, "main_drop_reason": "NO_OB_CANDIDATE" if not ob_candidates else "AVAILABLE"}
    stages["ob_strategy_selected"] = {"total_poi": ob_selected, "ob_poi_count": ob_selected, "fvg_poi_count": 0, "unknown_poi_count": 0, "missing_field_count": 0, "main_drop_reason": "NO_OB_SELECTED" if not ob_selected else "AVAILABLE"}
    stages["ob_executable_shadow_entry"] = {"total_poi": ob_entries, "ob_poi_count": ob_entries, "fvg_poi_count": 0, "unknown_poi_count": 0, "missing_field_count": 0, "main_drop_reason": "NO_OB_EXECUTABLE_ENTRY" if not ob_entries else "AVAILABLE"}

    main_stage = "ob_executable_shadow_entry"
    main_reason = "AVAILABLE"
    if stages["raw_agent2_outputs"]["ob_poi_count"] == 0:
        main_stage = "raw_agent2_outputs"
        main_reason = stages["raw_agent2_outputs"]["main_drop_reason"]
    elif stages["normalized_poi_stack"]["ob_poi_count"] == 0:
        main_stage = "normalized_poi_stack"
        main_reason = stages["normalized_poi_stack"]["main_drop_reason"]
    elif stages["strategy_input_poi_stack"]["ob_poi_count"] == 0:
        main_stage = "strategy_input_poi_stack"
        main_reason = stages["strategy_input_poi_stack"]["main_drop_reason"]
    else:
        for name in ("ob_strategy_evaluated", "ob_strategy_candidate", "ob_strategy_selected", "ob_executable_shadow_entry"):
            data = stages[name]
            if data["ob_poi_count"] == 0:
                main_stage = name
                main_reason = data["main_drop_reason"]
                break
    return {"stages": stages, "main_ob_drop_stage": main_stage, "main_ob_drop_reason": main_reason}


def _p1_48cbis_ob_discrepancy_summary(
    decision_events: Sequence[dict[str, Any]],
    ob_items: Sequence[dict[str, Any]],
    connectivity: dict[str, Any],
) -> dict[str, Any]:
    ob_exposure = len(ob_items)
    strategy_seen = int(connectivity.get("ob_poi_seen_total") or 0)
    mismatch = ob_exposure > 0 and strategy_seen == 0
    return {
        "records_checked": len(decision_events),
        "agent2_ob_exposure_count": ob_exposure,
        "strategy_ob_exposure_count": strategy_seen,
        "attribution_ob_count": 0,
        "mismatch_agent2_vs_strategy": mismatch,
        "likely_explanation": (
            "OB_EXPOSED_BY_AGENT2_BUT_NOT_REACHING_STRATEGY"
            if mismatch else
            "NO_OB_EXPOSED_IN_WINDOW" if ob_exposure == 0 else
            "OB_EXPOSURE_AND_STRATEGY_INPUT_ALIGNED"
        ),
    }


def _p1_48cbis_ob_examples(
    decision_events: Sequence[dict[str, Any]],
    selections: Sequence[dict[str, Any]],
    extracted_by_event: Sequence[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for index, event in enumerate(decision_events):
        selection = selections[index] if index < len(selections) else {}
        diagnostics = selection.get("ob_strategy_diagnostics") or {}
        results = diagnostics.get("results") or []
        block_reason = str((results[0] if results else {}).get("reason") or "UNKNOWN")
        for poi in extracted_by_event[index]:
            if poi["normalized_poi_type"] != "OB":
                continue
            agents = event.get("agents", {}) or {}
            a1_notes = (((agents.get("agent_1", {}) or {}).get("payload") or {}).get("shadow_ict_contract") or {}).get("contextual_notes", {}) or {}
            a7_notes = (((agents.get("agent_7", {}) or {}).get("payload") or {}).get("shadow_ict_contract") or {}).get("contextual_notes", {}) or {}
            examples.append({
                "timestamp": event.get("time"),
                "source_field": poi["source_field"],
                "raw_poi_type": poi["raw_poi_type"],
                "normalized_poi_type": poi["normalized_poi_type"],
                "raw_lifecycle": poi["raw_lifecycle"],
                "normalized_lifecycle": poi["normalized_lifecycle"],
                "session": a7_notes.get("session_label") or ((agents.get("agent_7", {}) or {}).get("payload") or {}).get("session_name") or "UNKNOWN",
                "dol": a1_notes.get("htf_draw_on_liquidity") or "UNKNOWN",
                "order_flow": a1_notes.get("institutional_order_flow") or "UNKNOWN",
                "strategy_seen": bool(diagnostics.get("poi_seen")),
                "strategy_candidate": bool((selection.get("selector_competition") or {}).get("ob_candidate_strategy_ids")),
                "block_reason": block_reason,
            })
            if len(examples) >= 50:
                return examples
    return examples


def _p1_46_strategy_verdict(metrics: dict[str, Any]) -> str:
    entries = int(metrics.get("entries") or 0)
    avg_r = float(metrics.get("avg_r") or 0.0)
    winrate = float(metrics.get("winrate_tp1") or 0.0)
    dd = float(metrics.get("max_drawdown_r") or 0.0)
    if entries == 0:
        return "STRATEGY_DISABLED_BY_CONTEXT"
    if entries < 10:
        return "STRATEGY_TOO_RARE"
    if avg_r < 0 or dd >= 8:
        return "STRATEGY_TOO_RISKY"
    if entries < 20 and winrate >= 60 and avg_r > 0:
        return "STRATEGY_TOO_RARE"
    if winrate >= 60 and avg_r > 0.25:
        return "STRATEGY_PROMISING"
    if winrate >= 50:
        return "STRATEGY_MEDIUM"
    if avg_r <= 0:
        return "STRATEGY_NO_EDGE"
    return "STRATEGY_NEEDS_MORE_DATA"


def _p1_46_consecutive_losses(entries: Sequence[dict[str, Any]]) -> int:
    current = maximum = 0
    for entry in entries:
        if (_float_or_none(entry.get("r_result")) or 0.0) < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def _p1_46_r_result(outcome: str) -> float:
    if outcome == "TP2":
        return 2.0
    if outcome == "TP1":
        return 1.0
    if outcome == "SL":
        return -1.0
    return 0.0


def _p1_46_strategy_entry(
    event: dict[str, Any],
    result: dict[str, Any],
    evaluation: dict[str, Any],
    outcome: str,
    r_result: float,
) -> dict[str, Any]:
    context = evaluation.get("context", {}) or {}
    poi = evaluation.get("poi", {}) or {}
    trigger = evaluation.get("trigger", {}) or {}
    strategy_id = str(result.get("strategy_id") or "UNKNOWN")
    session_bucket = _p1_45_session_bucket(str(context.get("session") or "UNKNOWN"))
    trigger_profile = _p1_46_trigger_profile(trigger)
    return {
        "timestamp": event.get("time"),
        "strategy_id": strategy_id,
        "result": outcome,
        "r_result": r_result,
        "primary_regime": context.get("primary_regime", "UNKNOWN"),
        "delivery_phase": context.get("delivery_phase", "UNKNOWN"),
        "draw_on_liquidity": context.get("draw_on_liquidity", "UNKNOWN"),
        "order_flow": context.get("order_flow", "UNKNOWN"),
        "session_label": context.get("session", "UNKNOWN"),
        "session_bucket": session_bucket,
        "poi_type": poi.get("poi_type", "UNKNOWN"),
        "trigger_profile": trigger_profile,
        "agent5_trigger_kind": trigger.get("trigger_kind", "UNKNOWN"),
        "shadow_tier": "PREMIUM" if result.get("permission") == "PREMIUM_SHADOW" else "STANDARD",
    }


def _p1_46_trigger_profile(trigger: dict[str, Any]) -> str:
    if trigger.get("has_sweep") and trigger.get("has_displacement") and trigger.get("has_retest"):
        return "SWEEP_DISPLACEMENT_RETEST"
    if trigger.get("has_displacement") and trigger.get("has_retest"):
        return "DISPLACEMENT_RETEST"
    if str(trigger.get("trigger_kind")) == "MICRO_FVG" and trigger.get("has_retest"):
        return "MICRO_FVG_RETEST"
    if str(trigger.get("trigger_kind")) == "COMPRESSION_BREAK" and trigger.get("has_retest"):
        return "COMPRESSION_BREAK_RETEST"
    if str(trigger.get("trigger_kind")) == "LIQUIDITY_RUN_ACCEPTANCE":
        return "LIQUIDITY_RUN_ACCEPTANCE"
    if str(trigger.get("trigger_kind")) == "MICRO_CHOCH":
        return "MICRO_CHOCH_ONLY"
    return str(trigger.get("trigger_kind") or "UNKNOWN")


def _p1_46_count_values(items: Sequence[dict[str, Any]], key: str) -> dict[str, int]:
    counts: Counter[str] = Counter(str(item.get(key) or "NONE") for item in items)
    return dict(sorted(counts.items()))


def _p1_46_session_strategy_attribution(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for session in ("TOKYO", "LONDON", "NY", "OTHER"):
        items = [entry for entry in entries if entry.get("session_bucket") == session]
        data = _p1_45_group_metrics(items)
        data["strategy_distribution"] = _p1_45_distribution(items, "strategy_id")
        result[session] = data
    return result


def _p1_47_strategy_ranking_summary(by_strategy: dict[str, dict[str, Any]]) -> dict[str, Any]:
    active = {sid: data for sid, data in by_strategy.items() if int(data.get("entries") or 0) > 0}

    def strategy_list(predicate: Any) -> list[str]:
        return sorted(sid for sid, data in active.items() if predicate(data))

    def best_by(key: str, reverse: bool = True) -> str | None:
        if not active:
            return None
        return sorted(active.items(), key=lambda item: float(item[1].get(key) or 0.0), reverse=reverse)[0][0]

    stable_candidates = {
        sid: data for sid, data in active.items()
        if int(data.get("entries") or 0) >= 20 and float(data.get("avg_r") or 0.0) > 0
    }
    most_stable = None
    if stable_candidates:
        most_stable = sorted(
            stable_candidates.items(),
            key=lambda item: (float(item[1].get("max_drawdown_r") or 0.0), -float(item[1].get("winrate_tp1") or 0.0)),
        )[0][0]

    return {
        "strong_strategies_wr_60_plus": strategy_list(
            lambda data: int(data.get("entries") or 0) >= 20
            and float(data.get("winrate_tp1") or 0.0) >= 60.0
            and float(data.get("avg_r") or 0.0) > 0.0
            and float(data.get("max_drawdown_r") or 0.0) < 8.0
        ),
        "medium_strategies_wr_50_60": strategy_list(
            lambda data: int(data.get("entries") or 0) >= 20
            and 50.0 <= float(data.get("winrate_tp1") or 0.0) < 60.0
            and float(data.get("avg_r") or 0.0) >= 0.0
        ),
        "weak_strategies_under_50": strategy_list(
            lambda data: int(data.get("entries") or 0) >= 20 and float(data.get("winrate_tp1") or 0.0) < 50.0
        ),
        "dangerous_strategies_negative_r": strategy_list(
            lambda data: float(data.get("avg_r") or 0.0) < 0.0 or float(data.get("max_drawdown_r") or 0.0) >= 8.0
        ),
        "too_rare_strategies": strategy_list(lambda data: 0 < int(data.get("entries") or 0) < 20),
        "best_strategy_by_avg_r": best_by("avg_r"),
        "best_strategy_by_winrate": best_by("winrate_tp1"),
        "best_strategy_by_drawdown": best_by("max_drawdown_r", reverse=False),
        "most_stable_strategy": most_stable,
        "worst_strategy": best_by("avg_r", reverse=False),
    }


def _p1_47_context_attribution(entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "delivery_phase": _p1_45_group_by(entries, "delivery_phase"),
        "draw_on_liquidity": _p1_45_group_by(entries, "draw_on_liquidity"),
        "order_flow": _p1_45_group_by(entries, "order_flow"),
    }


def _p1_47_agent_decision_attribution(
    decision_events: Sequence[dict[str, Any]],
    selections: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    selected_by_index = list(selections)
    result: dict[str, Any] = {}
    for agent_id in [f"agent_{idx}" for idx in range(1, 8)] + ["orchestrator"]:
        stats = {
            "agent_id": agent_id,
            "pass_count": 0,
            "wait_count": 0,
            "reject_count": 0,
            "hard_veto_count": 0,
            "candidate_support_count": 0,
            "main_reasons": Counter(),
            "main_missing_evidence": Counter(),
            "impact_on_selected_strategy": Counter(),
        }
        for index, event in enumerate(decision_events):
            selection = selected_by_index[index] if index < len(selected_by_index) else {}
            selected_strategy = str(selection.get("selected_strategy_id") or "NONE")
            if agent_id == "orchestrator":
                data = event.get("orchestrator", {}) or {}
                reason = str(data.get("reason") or selection.get("reason") or "UNKNOWN")
                decision = str(data.get("decision") or selection.get("decision") or "WAIT").upper()
                passed = decision in {"EXECUTE", "STANDARD_SHADOW", "PREMIUM_SHADOW"}
            else:
                data = (event.get("agents", {}) or {}).get(agent_id, {}) or {}
                reason = str(data.get("reason") or ((data.get("payload") or {}).get("final_reason")) or "UNKNOWN")
                passed = bool(data.get("hard_filter_pass"))
                decision = "PASS" if passed else reason.upper()

            if passed:
                stats["pass_count"] += 1
                if selected_strategy != "NONE":
                    stats["candidate_support_count"] += 1
            elif "WAIT" in decision:
                stats["wait_count"] += 1
            else:
                stats["reject_count"] += 1

            if "VETO" in decision or "VETO" in reason.upper():
                stats["hard_veto_count"] += 1
            if reason:
                stats["main_reasons"][reason] += 1
            if any(token in reason.upper() for token in ("MISSING", "NO_", "WAIT", "INSUFFICIENT")):
                stats["main_missing_evidence"][reason] += 1
            if selected_strategy != "NONE":
                stats["impact_on_selected_strategy"][selected_strategy] += 1

        result[agent_id] = {
            "agent_id": stats["agent_id"],
            "pass_count": stats["pass_count"],
            "wait_count": stats["wait_count"],
            "reject_count": stats["reject_count"],
            "hard_veto_count": stats["hard_veto_count"],
            "candidate_support_count": stats["candidate_support_count"],
            "main_reasons": dict(stats["main_reasons"].most_common(10)),
            "main_missing_evidence": dict(stats["main_missing_evidence"].most_common(10)),
            "impact_on_selected_strategy": dict(stats["impact_on_selected_strategy"].most_common(10)),
        }
    return result


def _baseline_contextual_decision(event: dict[str, Any]) -> str:
    orchestrator = event.get("orchestrator", {}) or {}
    decision = str(orchestrator.get("decision") or event.get("signal") or "").upper()
    if "CANDIDATE" in decision:
        return "CANDIDATE_MICRO"
    if "WAIT" in decision:
        return "WAIT"
    return "REJECT"


def _simulate_shadow_poi_outcome(
    event: dict[str, Any],
    candles_1m: Sequence[dict[str, Any]],
    candle_by_time: dict[datetime, int],
) -> str:
    event_time = _as_utc(event.get("time"))
    start_idx = candle_by_time.get(event_time)
    if start_idx is None or start_idx < 3:
        return "NO_RESOLUTION"
    agents = event.get("agents", {}) or {}
    direction = _normalize_direction((agents.get("agent_5", {}) or {}).get("direction") or (agents.get("agent_1", {}) or {}).get("direction"))
    if direction is None:
        return "NO_RESOLUTION"
    entry = _float_or_none(candles_1m[start_idx].get("close"))
    if entry is None:
        return "NO_RESOLUTION"
    recent = candles_1m[max(0, start_idx - 3): start_idx + 1]
    if direction == "SHORT":
        sl = max(float(candle.get("high", entry)) for candle in recent)
        risk = sl - entry
        tp1 = entry - risk
        tp2 = entry - 2 * risk
    else:
        sl = min(float(candle.get("low", entry)) for candle in recent)
        risk = entry - sl
        tp1 = entry + risk
        tp2 = entry + 2 * risk
    if risk <= 0:
        return "NO_RESOLUTION"
    hit_tp1 = False
    for candle in candles_1m[start_idx + 1: min(start_idx + 121, len(candles_1m))]:
        high = float(candle.get("high", entry))
        low = float(candle.get("low", entry))
        if direction == "SHORT":
            if high >= sl:
                return "TP1" if hit_tp1 else "SL"
            if low <= tp2:
                return "TP2"
            if low <= tp1:
                hit_tp1 = True
                sl = entry
        else:
            if low <= sl:
                return "TP1" if hit_tp1 else "SL"
            if high >= tp2:
                return "TP2"
            if high >= tp1:
                hit_tp1 = True
                sl = entry
    return "TP1" if hit_tp1 else "NO_RESOLUTION"


def _p1_45_verdict(
    theoretical_entries: int,
    winrate_tp1: float,
    avg_r: float,
    agent5_reached: int,
    agent5_pass: int,
    best_poi_count: int,
) -> str:
    if best_poi_count == 0:
        return "ICT_POI_STACK_NEEDS_MORE_CONTEXT"
    if agent5_reached > 0 and agent5_pass == 0:
        return "ICT_POI_STACK_NEEDS_AGENT5_REWORK"
    if theoretical_entries == 0:
        return "ICT_POI_STACK_NEEDS_MORE_CONTEXT"
    if avg_r > 0.25 and winrate_tp1 >= 50:
        return "ICT_POI_STACK_PROMISING"
    if avg_r < 0:
        return "ICT_POI_STACK_TOO_RISKY"
    return "ICT_POI_STACK_NO_EDGE"


def _p1_45_example(event: dict[str, Any], best: dict[str, Any] | None, after_decision: str) -> dict[str, Any]:
    agents = event.get("agents", {}) or {}
    a1 = agents.get("agent_1", {}) or {}
    a2 = agents.get("agent_2", {}) or {}
    a5 = agents.get("agent_5", {}) or {}
    a2_diag = ((a2.get("payload") or {}).get("diagnostic") or {})
    return {
        "timestamp": event.get("time"),
        "direction": a1.get("direction") or a2.get("direction"),
        "market_context": {
            "primary_regime": ((a1.get("payload") or {}).get("shadow_ict_contract") or {}).get("contextual_notes", {}).get("primary_regime", "UNKNOWN"),
            "draw_on_liquidity": ((a1.get("payload") or {}).get("shadow_ict_contract") or {}).get("contextual_notes", {}).get("draw_on_liquidity", "UNKNOWN"),
            "order_flow": ((a1.get("payload") or {}).get("shadow_ict_contract") or {}).get("contextual_notes", {}).get("institutional_order_flow", "UNKNOWN"),
        },
        "legacy_agent2": {
            "pass": bool(a2.get("hard_filter_pass")),
            "reject_reason": a2.get("reason") or a2_diag.get("final_reason"),
        },
        "best_shadow_poi": best,
        "orchestrator_shadow_after_poi_stack": after_decision,
        "agent5_reached": bool(best),
        "agent5_pass": bool(a5.get("hard_filter_pass")),
    }


def _json_safe(value: Any, seen: set[int] | None = None) -> Any:
    seen = seen or set()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()

    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _json_safe(item(), seen)
        except Exception:
            pass

    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            return _json_safe(tolist(), seen)
        except Exception:
            pass

    value_id = id(value)
    if value_id in seen:
        return "<circular>"
    if isinstance(value, MappingABC):
        seen.add(value_id)
        safe = {str(_json_safe(key, seen)): _json_safe(val, seen) for key, val in value.items()}
        seen.remove(value_id)
        return safe
    if isinstance(value, SequenceABC) and not isinstance(value, (str, bytes, bytearray)):
        seen.add(value_id)
        safe = [_json_safe(item_value, seen) for item_value in value]
        seen.remove(value_id)
        return safe
    return str(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _trade_lifecycle_status(trade_summary: dict[str, Any]) -> str:
    """Phase19: classify the trade lifecycle state.

    Returns one of:
        NO_TRADES — no trades opened
        ALL_CLOSED — all trades fully closed
        TRADES_OPEN — trades still open at replay end (no zombie check)
        ZOMBIE_DETECTED — open trades that should have closed
    """
    trades = int(trade_summary.get("trades") or 0)
    open_count = int(trade_summary.get("open_trades_end_count") or 0)
    if trades == 0 and open_count == 0:
        return "NO_TRADES"
    if open_count == 0:
        return "ALL_CLOSED"
    return "TRADES_OPEN"


def _daily_limit_status(trade_summary: dict[str, Any]) -> str:
    """Phase19: classify daily limit adherence.

    Returns one of:
        PASS — max trades/day respected
        WARN — exceptional slot used
        FAIL — limit exceeded
        NO_TRADES — no trades, nothing to check
    """
    total = int(trade_summary.get("total_daily_trades") or 0)
    if total == 0:
        return "NO_TRADES"
    daily_counts = trade_summary.get("daily_trade_counts") or {}
    max_trades = trade_summary.get("shadow_live_policy", {}).get("max_absolute_trades_per_day", 3)
    max_observed = max(daily_counts.values()) if daily_counts else 0
    if max_observed > max_trades:
        return "FAIL"
    standard_max = trade_summary.get("shadow_live_policy", {}).get("max_standard_trades_per_day", 2)
    if max_observed > standard_max:
        return "WARN"
    return "PASS"


# ── P4.1: trades-per-day helpers ─────────────────────────────────────────────


def _compute_trades_per_eval_day(
    parent_trades: int,
    eval_start: Any,
    eval_end: Any,
    first_time: Any,
    last_time: Any,
) -> float:
    """Compute trades per evaluation day from eval_start→eval_end.

    Falls back to first_time→last_time if eval boundaries are not set.
    """
    start_dt = _as_utc(eval_start) if eval_start is not None else None
    end_dt = _as_utc(eval_end) if eval_end is not None else None
    if start_dt is None:
        start_dt = _as_utc(first_time) if first_time is not None else None
    if end_dt is None:
        end_dt = _as_utc(last_time) if last_time is not None else None
    if start_dt is None or end_dt is None:
        return 0.0
    eval_seconds = max(1.0, (end_dt - start_dt).total_seconds())
    eval_days = max(1.0, eval_seconds / 86400.0)
    return round(parent_trades / eval_days, 4)


def _compute_trades_per_active_day(
    parent_trades: int,
    active_trading_days: int,
) -> float:
    """Compute trades per active trading day."""
    days = max(1, int(active_trading_days or 0))
    return round(parent_trades / days, 4)


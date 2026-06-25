"""Local offline replay runner."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

from core.blackboard import BlackBoard
from replay.decision_pipeline import ReplayDecisionPipeline
from replay.economic_calendar import load_calendar_result
from replay.historical_data import build_data_quality_report, load_csv_candles, parse_timestamp
from replay.replay_engine import ReplayEngine
from replay.execution_model import build_default_execution_model
from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager
from replay.replay_profiler import enable_profiling, get_profiler
from replay.news_index import NewsIndex
from data_pipeline.timeframe_aggregation import aggregate_candles


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "historical" / "XAUUSD"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "replay_runs"
DEFAULT_NEWS_CALENDAR = PROJECT_ROOT / "data" / "historical" / "news" / "economic_calendar_2026-04-01_2026-06-05.jsonl"
DEFAULT_REPLAY_AGENTS = ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"]
DEFAULT_TIMEFRAMES = ("1m", "5m", "15m", "1H", "4H")


def resolve_replay_agents(values: list[str] | None) -> list[str]:
    return list(values) if values else list(DEFAULT_REPLAY_AGENTS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an offline Gold Sniper replay.")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--warmup-start")
    parser.add_argument("--eval-start")
    parser.add_argument("--eval-end")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--replay-agent",
        action="append",
        choices=("agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"),
        default=[],
    )
    parser.add_argument("--news-calendar", type=Path, default=DEFAULT_NEWS_CALENDAR)
    parser.add_argument("--data-manifest", type=Path, default=None)
    parser.add_argument("--p1-replay", action="store_true", default=True)
    parser.add_argument("--with-orchestrator", action="store_true")
    parser.add_argument("--tier-simulation", action="store_true")
    parser.add_argument("--initial-equity", type=float, default=100.0)
    parser.add_argument(
        "--diagnose-agent",
        action="append",
        choices=("agent_2", "agent_5"),
        default=[],
        help="Enable replay-only compact diagnostic payloads for a supported agent.",
    )
    parser.add_argument(
        "--diagnose-alignment",
        action="append",
        choices=("poi_ote",),
        default=[],
        help="Enable replay-only alignment diagnostics for supported cross-agent checks.",
    )
    parser.add_argument("--diagnose-scoring", action="store_true", help="Diagnose score mismatch")
    parser.add_argument(
        "--diagnose-agent2-zonelifecycle",
        action="store_true",
        help="Shadow mode: classify OB zones with ZoneLifecycle engine (P1.27). No decision change.",
    )
    parser.add_argument(
        "--diagnose-agent4-contextual-ote",
        action="store_true",
        help="Shadow mode: diagnose Agent 4 OTE as contextual continuation vs strict pullback. No decision change.",
    )
    parser.add_argument(
        "--diagnose-agent5-contextual-trigger",
        action="store_true",
        help="Shadow mode: diagnose Agent 5 trigger contextually. No decision change.",
    )
    parser.add_argument(
        "--diagnose-contextual-orchestrator",
        action="store_true",
        help="Shadow mode P1.30: contextual orchestrator classification per setup type. No decision change.",
    )
    # P3-E: replay acceleration flags
    parser.add_argument(
        "--profile-replay",
        action="store_true",
        help="P3: Measure per-agent timing and write profile_report.json.",
    )
    parser.add_argument(
        "--fast-precomputed",
        action="store_true",
        help="P3-E: Use precomputed feature caches (NOT YET IMPLEMENTED — will skip agent recomputation when available).",
    )
    parser.add_argument(
        "--precompute-root",
        type=Path,
        default=None,
        help="P3-E: Directory for precomputed Parquet caches (NOT YET IMPLEMENTED).",
    )
    return parser


async def run_replay(args: argparse.Namespace) -> dict[str, Any]:
    boundaries = _resolve_boundaries(args)
    load_start = boundaries["load_start"]
    eval_start = boundaries["eval_start"]
    eval_end = boundaries["eval_end"]
    loaded, timeframe_sources, derived_timeframes, missing_timeframes = _load_replay_timeframes(
        args.data_root,
        symbol="XAUUSD",
        start=load_start,
        end=eval_end,
    )
    if not loaded["1m"]:
        raise RuntimeError("No 1m candles available for requested replay period")
    calendar_result = load_calendar_result(args.news_calendar, start=load_start, end=eval_end)
    news_events = calendar_result.events
    news_calendar_exists = not calendar_result.missing
    news_calendar_empty = calendar_result.empty
    news_feed_alive = news_calendar_exists and not news_calendar_empty

    # ── P3-E: build news bisect index for O(log n) lookups ───────────
    news_index: NewsIndex | None = None
    t0 = __import__("time").perf_counter()
    if news_feed_alive and args.news_calendar.suffix == ".jsonl":
        try:
            news_index = NewsIndex.from_jsonl(args.news_calendar)
            _elapsed = (__import__("time").perf_counter() - t0) * 1000
            print(f"[P3] News index: {news_index.count} events indexed in {_elapsed:.0f}ms")
        except Exception:
            pass
    elif news_feed_alive and args.news_calendar.suffix == ".csv":
        try:
            news_index = NewsIndex.from_csv(args.news_calendar)
            _elapsed = (__import__("time").perf_counter() - t0) * 1000
            print(f"[P3] News index (CSV): {news_index.count} events indexed in {_elapsed:.0f}ms")
        except Exception:
            pass

    # ── P3-E: enable profiling if requested ──────────────────────────
    if args.profile_replay:
        profiler = enable_profiling()
        print(f"[P3] Replay profiling enabled — report will be written to <run_dir>/profile_report.json")
    else:
        profiler = None

    # ── P2-B data manifest ──────────────────────────────────────────
    from gold_sniper.data_pipeline.candle_manifest import build_candle_coverage_manifest
    data_manifest = build_candle_coverage_manifest(
        data_root=args.data_root,
        symbol="XAUUSD",
        requested_start_utc=load_start,
        requested_end_utc=eval_end,
    )
    # ─────────────────────────────────────────────────────────────────

    agent_ids = resolve_replay_agents(args.replay_agent)
    decision_hook = None
    diagnostics = list(args.diagnose_agent) if args.diagnose_agent else []
    if args.diagnose_agent2_zonelifecycle and "agent_2" not in diagnostics:
        diagnostics.append("agent_2")
    if args.diagnose_agent5_contextual_trigger and "agent_5" not in diagnostics:
        diagnostics.append("agent_5")

    if args.with_orchestrator:
        raise RuntimeError("P1-replay forbids live orchestrator")

    if agent_ids:
        decision_hook = ReplayDecisionPipeline.from_agent_ids(
            agent_ids,
            use_orchestrator=False,
            news_events=news_events,
            news_feed_alive=news_feed_alive,
            news_source=calendar_result.source_format if news_feed_alive else "REPLAY_EMPTY",
            diagnose_agents=diagnostics,
            alignment_diagnostics=args.diagnose_alignment,
        )

    board = BlackBoard()
    execution_model = build_default_execution_model(initial_equity=float(args.initial_equity))
    trade_manager = SimulatedTradeManager(
        board,
        SimulatedTradeConfig(
            equity_initial=float(args.initial_equity),
            execution_model=execution_model,
            require_execution_model=True,
        ),
    )
    tier_trade_manager = SimulatedTradeManager(
        board,
        SimulatedTradeConfig(
            equity_initial=float(args.initial_equity),
            execution_model=execution_model,
            require_execution_model=True,
            write_blackboard_positions=False,
            event_prefix="tier",
        ),
    )

    engine = ReplayEngine(
        board,
        loaded["1m"],
        output_root=args.output_root,
        run_id=args.run_id,
        trade_manager=trade_manager,
        tier_trade_manager=tier_trade_manager,
        tier_simulation=bool(args.tier_simulation),
        candles_by_timeframe={
            "5m": loaded.get("5m", []),
            "15m": loaded.get("15m", []),
            "1H": loaded.get("1H", []),
            "4H": loaded.get("4H", []),
        },
        on_decision_hook=decision_hook,
        warmup_start=load_start,
        eval_start=eval_start,
        eval_end=eval_end,
        metadata={
            "runner": "replay.run_replay",
            "data_root": str(args.data_root),
            "requested_start": load_start,
            "requested_end": eval_end,
            "requested_warmup_start": load_start,
            "requested_eval_start": eval_start,
            "requested_eval_end": eval_end,
            "loaded_timeframes": {timeframe: len(candles) for timeframe, candles in loaded.items()},
            "timeframe_sources": dict(timeframe_sources),
            "derived_timeframes": list(derived_timeframes),
            "missing_timeframes": list(missing_timeframes),
            "replay_agents": list(agent_ids),
            "replay_orchestrator": False,
            "p1_replay": True,
            "offline_only": True,
            "broker_dependency": False,
            "data_quality": {
                timeframe: build_data_quality_report(candles, symbol="XAUUSD", timeframe=timeframe, source=str(args.data_root)).to_dict()
                for timeframe, candles in loaded.items()
            },
            "news_calendar_missing": calendar_result.missing,
            "news_calendar_errors": calendar_result.errors,
            "news_calendar_source_format": calendar_result.source_format,
            "news_calendar_raw_events_count": calendar_result.raw_events_count,
            "news_calendar_loaded_events_count": calendar_result.loaded_events_count,
            "news_calendar_filtered_events_count": calendar_result.filtered_events_count,
            "news_calendar_duplicate_id_count": calendar_result.duplicate_id_count,
            "news_calendar_duplicate_key_count": calendar_result.duplicate_key_count,
            "diagnose_agents": list(args.diagnose_agent),
            "diagnose_alignment": list(args.diagnose_alignment),
            "diagnose_scoring": bool(args.diagnose_scoring),
            "diagnose_agent2_zonelifecycle": bool(args.diagnose_agent2_zonelifecycle),
            "diagnose_agent4_contextual_ote": bool(args.diagnose_agent4_contextual_ote),
            "diagnose_agent5_contextual_trigger": bool(args.diagnose_agent5_contextual_trigger),
            "diagnose_contextual_orchestrator": bool(args.diagnose_contextual_orchestrator),
            "news_calendar": str(args.news_calendar),
            "news_calendar_requested": "agent_6" in agent_ids,
            "news_calendar_exists": news_calendar_exists,
            "news_calendar_empty": news_calendar_empty,
            "news_calendar_coverage_start_utc": calendar_result.coverage_start_utc,
            "news_calendar_coverage_end_utc": calendar_result.coverage_end_utc,
            "loaded_news_events": len(news_events),
            "data_manifest": data_manifest.to_dict(),
            "data_manifest_status": data_manifest.overall_status,
            "available_start_utc": data_manifest.available_start_utc,
            "available_end_utc": data_manifest.available_end_utc,
            "tier_simulation": bool(args.tier_simulation),
            "initial_equity": float(args.initial_equity),
            "execution_model": execution_model.to_dict(),
            "execution_model_required": True,
            "p2c_faithful_simulation": True,
        },
    )
    engine.diagnose_scoring = bool(args.diagnose_scoring)
    engine.diagnose_agent2_zonelifecycle = bool(args.diagnose_agent2_zonelifecycle)
    engine.diagnose_agent4_contextual_ote = bool(args.diagnose_agent4_contextual_ote)
    engine.diagnose_agent5_contextual_trigger = bool(args.diagnose_agent5_contextual_trigger)
    engine.diagnose_contextual_orchestrator = bool(args.diagnose_contextual_orchestrator)
    summary = await engine.run()

    # ── P3-E: write profile report if profiling was enabled ─────────
    if profiler is not None:
        profile_path = engine.run_dir / "profile_report.json"
        profiler.finish()
        profiler.write_report(engine.run_dir)
        print(f"[P3] Profile report written to {profile_path}")
        summary["profile_report_path"] = str(profile_path)
        summary["profile_report"] = profiler.report()

    # ── P3-E: write news index summary if available ─────────────────
    if news_index is not None:
        summary["news_index"] = news_index.to_dict()

    print(json.dumps(summary, ensure_ascii=False, default=str))
    return summary


def _load_replay_timeframes(
    data_root: Path,
    *,
    symbol: str,
    start: str,
    end: str,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], list[str], list[str]]:
    loaded: dict[str, list[dict[str, Any]]] = {}
    sources: dict[str, str] = {}
    derived: list[str] = []
    missing: list[str] = []

    m1_path = _resolve_timeframe_csv(data_root, symbol, "1m", start=start, end=end)
    if m1_path is None:
        loaded["1m"] = []
        sources["1m"] = "MISSING"
        missing.append("1m")
    else:
        loaded["1m"] = load_csv_candles(m1_path, "1m", start=start, end=end)
        sources["1m"] = str(m1_path)

    for timeframe in DEFAULT_TIMEFRAMES:
        if timeframe == "1m":
            continue
        csv_path = _resolve_timeframe_csv(data_root, symbol, timeframe, start=start, end=end)
        if csv_path is not None:
            loaded[timeframe] = load_csv_candles(csv_path, timeframe, start=start, end=end)
            sources[timeframe] = str(csv_path)
            continue
        if loaded.get("1m") and timeframe in {"5m", "15m", "1H", "4H"}:
            loaded[timeframe] = aggregate_candles(loaded["1m"], target_timeframe=timeframe)
            sources[timeframe] = "DERIVED_FROM_1M"
            derived.append(timeframe)
            continue
        loaded[timeframe] = []
        sources[timeframe] = "MISSING"
        missing.append(timeframe)
    return loaded, sources, derived, missing


def _resolve_timeframe_csv(data_root: Path, symbol: str, timeframe: str, *, start: str, end: str) -> Path | None:
    folder = data_root / timeframe
    if not folder.exists():
        return None
    candidates = sorted(folder.glob(f"{symbol}_{timeframe}_*.csv"))
    if not candidates:
        candidates = sorted(folder.glob("*.csv"))
    if not candidates:
        return None
    start_dt = parse_timestamp(start)
    end_dt = parse_timestamp(end)
    ranked = [_timeframe_file_rank(path, timeframe, start_dt, end_dt) for path in candidates]
    ranked = [item for item in ranked if item is not None]
    if ranked:
        ranked.sort(reverse=True)
        return ranked[0][3]
    return candidates[-1]


def _timeframe_file_rank(path: Path, timeframe: str, start: Any, end: Any) -> tuple[int, float, float, Path] | None:
    try:
        candles = load_csv_candles(path, timeframe)
    except Exception:
        return None
    if not candles:
        return None
    first = candles[0]["time"]
    last = candles[-1]["time"]
    covers = int(first <= start and last >= end)
    overlap_start = max(first, start)
    overlap_end = min(last, end)
    overlap = max(0.0, (overlap_end - overlap_start).total_seconds())
    return covers, overlap, last.timestamp(), path


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    asyncio.run(run_replay(args))
    return 0


def _normalize_boundary(value: str, *, is_end: bool) -> str:
    text = str(value).strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return f"{text}T23:59:59Z" if is_end else f"{text}T00:00:00Z"
    return text


def _resolve_boundaries(args: argparse.Namespace) -> dict[str, str]:
    warmup_values = [args.warmup_start, args.eval_start, args.eval_end]
    has_warmup_window = any(value is not None for value in warmup_values)
    if has_warmup_window:
        if not all(warmup_values):
            raise RuntimeError("--warmup-start, --eval-start and --eval-end must be provided together")
        return {
            "load_start": _normalize_boundary(args.warmup_start, is_end=False),
            "eval_start": _normalize_boundary(args.eval_start, is_end=False),
            "eval_end": _normalize_boundary(args.eval_end, is_end=True),
        }
    if not args.start or not args.end:
        raise RuntimeError("--start and --end are required unless warmup/eval window options are used")
    start = _normalize_boundary(args.start, is_end=False)
    end = _normalize_boundary(args.end, is_end=True)
    return {"load_start": start, "eval_start": start, "eval_end": end}


if __name__ == "__main__":
    raise SystemExit(main())

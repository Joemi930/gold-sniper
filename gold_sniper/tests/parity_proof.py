"""Architectural parity proof: unified live adapter vs ReplayDecisionPipeline.

This is intentionally stricter than a smoke test: it runs the replay pipeline
with the replay agents, then calls unified_live_decision on the same blackboard
state. The unified adapter must reproduce the replay decision payload fields
that matter before any PAPER/live execution is allowed.

Run:
    PYTHONPATH=gold_sniper python gold_sniper/tests/parity_proof.py
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

GS_PATH = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(GS_PATH) not in sys.path:
    sys.path.insert(0, str(GS_PATH))

from core.blackboard import BlackBoard
from core.unified_live_decision import unified_live_decision
from replay.decision_pipeline import ReplayDecisionPipeline


DATA_PATH = (
    REPO_ROOT
    / "gold_sniper"
    / "data"
    / "historical"
    / "XAUUSD"
    / "1m"
    / "XAUUSD_1m_COMPLETE_2025-12-01_2026-06-26.csv"
)


def load_candles(csv_path: Path, start: str, days: int) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=days)
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = datetime.fromisoformat(str(row["time"]).replace("Z", "+00:00"))
            if start_dt <= ts < end_dt:
                candles.append(
                    {
                        "time": ts,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "tick_volume": int(row.get("tick_volume", 0) or 0),
                        "spread": int(row.get("spread", 0) or 0),
                        "symbol": "XAUUSD",
                    }
                )
    candles.sort(key=lambda c: c["time"])
    return candles


def build_tf_candles(m1_candles: list[dict[str, Any]], current_idx: int, minutes: int) -> list[dict[str, Any]]:
    buckets: dict[datetime, dict[str, Any]] = {}
    for i in range(max(0, current_idx - 6000), current_idx + 1):
        c = m1_candles[i]
        ts = c["time"]
        bucket_ts = ts.replace(minute=(ts.minute // minutes) * minutes, second=0, microsecond=0)
        if bucket_ts not in buckets:
            buckets[bucket_ts] = {
                "time": bucket_ts,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "tick_volume": c.get("tick_volume", 0),
                "spread": c.get("spread", 0),
                "symbol": "XAUUSD",
            }
            continue
        b = buckets[bucket_ts]
        b["high"] = max(float(b["high"]), c["high"])
        b["low"] = min(float(b["low"]), c["low"])
        b["close"] = c["close"]
        b["tick_volume"] = int(b.get("tick_volume", 0) or 0) + int(c.get("tick_volume", 0) or 0)
    return list(buckets.values())


def populate_market(board: BlackBoard, candles: list[dict[str, Any]], idx: int) -> None:
    candle = candles[idx]
    board._data.setdefault("market_data", {}).setdefault("candles", {})
    board._data["market_data"]["candles"]["1m"] = [dict(c) for c in candles[max(0, idx - 1440) : idx + 1]]
    board._data["market_data"]["candles"]["15m"] = build_tf_candles(candles, idx, 15)
    board._data["market_data"]["candles"]["4H"] = build_tf_candles(candles, idx, 240)
    board._data["market_data"]["current_tick"] = {
        "bid": candle["close"] - 0.15,
        "ask": candle["close"] + 0.15,
        "time": candle["time"].isoformat(),
    }


def comparable(replay: dict[str, Any], unified: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    fields = {
        "decision": (replay.get("decision"), unified.get("pde_decision")),
        "setup_grade": (replay.get("setup_grade"), unified.get("setup_grade")),
        "kasper_grade": (replay.get("kasper_grade"), unified.get("kasper_grade")),
        "kasper_rec": (
            replay.get("kasper_decision_recommendation"),
            unified.get("kasper_decision_recommendation"),
        ),
        "scenario_type": (replay.get("scenario_type"), unified.get("scenario_type")),
    }
    replay_rr = replay.get("kasper_rr_estimate")
    unified_rr = unified.get("kasper_rr_estimate")
    if replay_rr is None or unified_rr is None:
        fields["kasper_rr_estimate"] = (replay_rr, unified_rr)
    else:
        fields["kasper_rr_estimate"] = (round(float(replay_rr), 6), round(float(unified_rr), 6))
    return fields


async def run(args: argparse.Namespace) -> int:
    if not DATA_PATH.exists():
        print(f"ERROR: historical data not found: {DATA_PATH}")
        return 2

    os.environ["GS_UNIFIED_PIPELINE"] = "1"
    os.environ["GS_RISK_SCALE"] = str(args.risk_scale)
    os.environ["GS_MIN_RR"] = "4"
    os.environ["GS_STRATEGY_V2"] = "1"
    os.environ["GS_EXECUTION_TF"] = "15m"
    os.environ["GOLD_SNIPER_KASPER_GRADED"] = "1"

    candles = load_candles(DATA_PATH, args.start, args.days)
    if len(candles) <= args.warmup:
        print(f"ERROR: not enough candles: loaded={len(candles)} warmup={args.warmup}")
        return 2

    pipeline = ReplayDecisionPipeline.from_agent_ids(
        ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"]
    )
    checked = 0
    divergences: list[dict[str, Any]] = []

    for idx in range(args.warmup, len(candles), args.step):
        candle = candles[idx]
        board = BlackBoard()
        populate_market(board, candles, idx)
        replay = await pipeline(candle, board)
        unified = unified_live_decision(board, candle=candle, symbol="XAUUSD")
        pairs = comparable(replay, unified)
        mismatch = {key: value for key, value in pairs.items() if value[0] != value[1]}
        checked += 1
        if mismatch:
            divergences.append({"time": candle["time"].isoformat(), "mismatch": mismatch})
            if len(divergences) >= args.max_divergences:
                break

    print("=== ARCHITECTURAL PARITY PROOF ===")
    print(f"start={args.start} days={args.days} warmup={args.warmup} step={args.step}")
    print(f"candles_loaded={len(candles)} comparisons={checked}")
    print(f"divergences={len(divergences)}")
    for item in divergences[: args.max_divergences]:
        print(f"DIVERGENCE {item['time']}: {item['mismatch']}")

    if checked <= 0:
        print("PARITY FAILED: zero comparisons")
        return 1
    if divergences:
        print("PARITY FAILED")
        return 1
    print("PARITY PROVED: unified_live_decision matches ReplayDecisionPipeline")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-06-01")
    parser.add_argument("--days", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=1440)
    parser.add_argument("--step", type=int, default=15)
    parser.add_argument("--risk-scale", type=float, default=6.0)
    parser.add_argument("--max-divergences", type=int, default=5)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

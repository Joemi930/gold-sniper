"""P3 — Session & Regime Breakdown Analysis.

Reads a replay summary.json + trade_journal.jsonl and produces
per-session, per-grade, and per-month breakdowns for statistical proof (P3 doc §12).

Usage:
  python gold_sniper/validation/session_breakdown.py <run_dir>
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Session classification (UTC) ──────────────────────────────────────────
# Asia/Tokyo: 00:00-08:00 UTC
# London:     08:00-16:00 UTC
# NY:         13:00-21:00 UTC (overlaps London 13:00-16:00)
# Other:      remaining

def classify_session(timestamp: str) -> str:
    """Classify a UTC timestamp into a trading session."""
    try:
        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        hour = dt.hour
        if 0 <= hour < 8:
            return "Asia"
        if 13 <= hour < 21:
            return "NY"
        if 8 <= hour < 16:
            return "London"
        return "Other"
    except (ValueError, TypeError):
        return "Unknown"


def load_replay(run_dir: str | Path) -> dict[str, Any]:
    """Load summary + trade journal from a replay run directory."""
    run_dir = Path(run_dir)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    journal_path = run_dir / "trade_journal.jsonl"
    trades: list[dict[str, Any]] = []
    if journal_path.exists():
        with journal_path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    trades.append(json.loads(line))
    return {"summary": summary, "trades": trades}


def session_breakdown(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-session winrate, PnL, trade count."""
    by_session: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "pure_r_total": 0.0, "net_r": []}
    )
    for t in trades:
        if t.get("reason") != "PARENT_CLOSE":
            continue
        session = classify_session(t.get("time", ""))
        r = t.get("r_multiple") or 0.0
        pure_r = t.get("pure_r_multiple") or r
        by_session[session]["trades"] += 1
        if r > 0:
            by_session[session]["wins"] += 1
        else:
            by_session[session]["losses"] += 1
        by_session[session]["pnl"] += float(t.get("pnl", 0.0) or 0.0)
        by_session[session]["pure_r_total"] += pure_r
        by_session[session]["net_r"].append(r)

    result: dict[str, Any] = {}
    for session, data in sorted(by_session.items()):
        n = data["trades"]
        wr = round(data["wins"] / n * 100, 1) if n > 0 else 0.0
        avg_r = round(sum(data["net_r"]) / n, 4) if n > 0 else 0.0
        avg_pure = round(data["pure_r_total"] / n, 4) if n > 0 else 0.0
        result[session] = {
            "trades": n,
            "wins": data["wins"],
            "losses": data["losses"],
            "winrate_pct": wr,
            "pnl_usd": round(data["pnl"], 4),
            "avg_net_R": avg_r,
            "avg_pure_R": avg_pure,
        }
    return result


def grade_breakdown(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-grade winrate, PnL."""
    by_grade: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "net_r": []}
    )
    for t in trades:
        if t.get("reason") != "PARENT_CLOSE":
            continue
        grade = t.get("kasper_grade") or t.get("setup_grade") or "UNKNOWN"
        r = t.get("r_multiple") or 0.0
        by_grade[grade]["trades"] += 1
        if r > 0:
            by_grade[grade]["wins"] += 1
        else:
            by_grade[grade]["losses"] += 1
        by_grade[grade]["pnl"] += float(t.get("pnl", 0.0) or 0.0)
        by_grade[grade]["net_r"].append(r)

    result: dict[str, Any] = {}
    for grade, data in sorted(by_grade.items()):
        n = data["trades"]
        wr = round(data["wins"] / n * 100, 1) if n > 0 else 0.0
        avg_r = round(sum(data["net_r"]) / n, 4) if n > 0 else 0.0
        result[grade] = {
            "trades": n,
            "wins": data["wins"],
            "losses": data["losses"],
            "winrate_pct": wr,
            "pnl_usd": round(data["pnl"], 4),
            "avg_net_R": avg_r,
        }
    return result


def leg_outcome_breakdown(trades: list[dict[str, Any]]) -> dict[str, int]:
    """Count leg outcome patterns."""
    patterns: dict[str, int] = defaultdict(int)
    for t in trades:
        if t.get("event") != "leg_close":
            continue
        reason = t.get("reason", "UNKNOWN")
        leg = t.get("leg", "?")
        patterns[f"leg{leg}_{reason}"] += 1
    return dict(sorted(patterns.items()))


def monthly_breakdown(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-month winrate, PnL."""
    by_month: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "net_r": []}
    )
    for t in trades:
        if t.get("reason") != "PARENT_CLOSE":
            continue
        try:
            month = str(t.get("time", ""))[:7]  # "2026-05"
        except Exception:
            month = "Unknown"
        r = t.get("r_multiple") or 0.0
        by_month[month]["trades"] += 1
        if r > 0:
            by_month[month]["wins"] += 1
        else:
            by_month[month]["losses"] += 1
        by_month[month]["pnl"] += float(t.get("pnl", 0.0) or 0.0)
        by_month[month]["net_r"].append(r)

    result: dict[str, Any] = {}
    for month, data in sorted(by_month.items()):
        n = data["trades"]
        wr = round(data["wins"] / n * 100, 1) if n > 0 else 0.0
        avg_r = round(sum(data["net_r"]) / n, 4) if n > 0 else 0.0
        result[month] = {
            "trades": n,
            "wins": data["wins"],
            "losses": data["losses"],
            "winrate_pct": wr,
            "pnl_usd": round(data["pnl"], 4),
            "avg_net_R": avg_r,
        }
    return result


def build_report(run_dir: str | Path) -> dict[str, Any]:
    """Produce complete breakdown report."""
    data = load_replay(run_dir)
    summary = data["summary"]
    trades = data["trades"]

    parent_closes = [t for t in trades if t.get("reason") == "PARENT_CLOSE"]
    leg_closes = [t for t in trades if t.get("event") == "leg_close"]

    return {
        "run_id": summary.get("run_id", str(run_dir)),
        "period": f'{summary.get("period_start", "?")} -> {summary.get("period_end", "?")}',
        "overall": {
            "parent_trades": len(parent_closes),
            "leg_events": len(leg_closes),
            "wins": summary.get("wins"),
            "losses": summary.get("losses"),
            "winrate_pct": summary.get("win_rate"),
            "net_pnl_usd": summary.get("net_pnl"),
            "avg_net_win_R": summary.get("avg_win_R"),
            "avg_net_loss_R": summary.get("avg_loss_R"),
            "pure_avg_win_R": summary.get("pure_avg_win_R"),
            "pure_expectancy_R": summary.get("pure_expectancy_R"),
        },
        "by_session": session_breakdown(trades),
        "by_grade": grade_breakdown(trades),
        "by_month": monthly_breakdown(trades),
        "leg_outcomes": leg_outcome_breakdown(trades),
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="P3 Session & Regime Breakdown")
    parser.add_argument("run_dir", type=Path, help="Replay run directory")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output JSON path")
    args = parser.parse_args()

    report = build_report(args.run_dir)
    report_json = json.dumps(report, indent=2, ensure_ascii=False, default=str)

    if args.output:
        args.output.write_text(report_json, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(report_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

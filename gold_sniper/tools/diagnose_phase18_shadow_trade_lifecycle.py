"""P2-E Phase18 — Shadow Trade Lifecycle diagnostic tool.

Reads a replay run directory and produces:
  - phase18_shadow_trade_lifecycle.json
  - phase18_shadow_trade_lifecycle.md

Usage:
  python -m gold_sniper.tools.diagnose_phase18_shadow_trade_lifecycle --run-dir data/replay_runs/<RUN_ID>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any


def load_jsonl(path: str) -> list[dict[str, Any]]:
    if not os.path.exists(path):
        return []
    items = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def load_json(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh) or {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def diagnose(run_dir: str) -> dict[str, Any]:
    summary = load_json(os.path.join(run_dir, "summary.json"))
    events = load_jsonl(os.path.join(run_dir, "events.jsonl"))
    decisions = load_jsonl(os.path.join(run_dir, "decisions.jsonl"))
    trade_journal = load_jsonl(os.path.join(run_dir, "trade_journal.jsonl"))

    run_id = summary.get("run_id", os.path.basename(run_dir))
    total_decisions = len(decisions)

    # ── Decision counts ──────────────────────────────────────────────────
    decision_counter = Counter(
        str(item.get("decision") or "UNKNOWN") for item in decisions
    )
    enter_full = decision_counter.get("ENTER_FULL", 0)
    enter_reduced = decision_counter.get("ENTER_REDUCED", 0)
    enter_eligible_count = sum(1 for item in decisions if item.get("enter_eligible"))
    risk_positive = sum(
        1 for item in decisions
        if _safe_float(item.get("risk_multiplier")) > 0
    )
    risk_positive_not_eligible = sum(
        1 for item in decisions
        if not item.get("enter_eligible") and _safe_float(item.get("risk_multiplier")) > 0
    )

    # ── Trade events ─────────────────────────────────────────────────────
    open_events = [e for e in events if str(e.get("event") or "") in {"open", "tier_trade_open"}]
    close_events = [e for e in events if str(e.get("event") or "") in {"close", "tier_trade_close"}]
    partial_events = [e for e in events if str(e.get("event") or "") in {"partial_close", "tier_trade_partial_close"}]
    missed_events = [e for e in events if str(e.get("event") or "") in {"missed_entry", "tier_missed_entry"}]
    rejected_events = [e for e in events if str(e.get("event") or "") in {"rejected", "tier_trade_rejected"}]
    be_events = [e for e in events if str(e.get("event") or "") in {"sl_moved_be_plus", "tier_sl_moved_be_plus"}]

    # ── Open trades at end ───────────────────────────────────────────────
    open_end_count = summary.get("open_trades_end_count", 0)
    open_end_details = summary.get("open_trades_end_details", [])
    unrealized_r_total = summary.get("unrealized_R_total")
    unrealized_pnl = summary.get("unrealized_pnl")

    # ── Realized R values ────────────────────────────────────────────────
    realized_r_values = [
        _safe_float(e.get("r_multiple"))
        for e in close_events
        if e.get("r_multiple") is not None
    ]
    realized_r_values = [v for v in realized_r_values if v != 0.0 or _safe_float(e.get("pnl")) == 0.0 for e in close_events]

    # ── Performance ratios ───────────────────────────────────────────────
    gross_win = sum(v for v in realized_r_values if v > 0)
    gross_loss = abs(sum(v for v in realized_r_values if v < 0))
    profit_factor = round(gross_win / gross_loss, 6) if gross_loss else None
    expectancy_r = (
        round(sum(realized_r_values) / len(realized_r_values), 6)
        if realized_r_values
        else None
    )

    # ── Performance status ───────────────────────────────────────────────
    filled_trades = len(open_events)
    closed_trades_count = len(close_events)
    if filled_trades == 0:
        performance_status = "NO_TRADES"
    elif closed_trades_count == 0 and open_end_count > 0:
        performance_status = "TRADES_OPEN_ONLY"
    elif closed_trades_count > 0 and open_end_count > 0:
        performance_status = "MIXED_REALIZED_AND_OPEN"
    elif closed_trades_count > 0:
        performance_status = "REALIZED_TRADES_AVAILABLE"
    else:
        performance_status = "NO_TRADES"

    # ── Daily metrics ────────────────────────────────────────────────────
    daily_trade_counts = summary.get("daily_trade_counts", {})
    daily_limit_rejections = summary.get("daily_limit_rejections", 0)
    grade_blocked = summary.get("grade_blocked_count", 0)

    # ── Trades by category ───────────────────────────────────────────────
    trades_by_grade: Counter = Counter()
    trades_by_setup: Counter = Counter()
    trades_by_direction: Counter = Counter()
    trade_details: list[dict[str, Any]] = []

    for event in open_events:
        grade = str(event.get("setup_grade") or event.get("tier") or "UNKNOWN")
        setup = str(event.get("setup_type") or "UNKNOWN")
        direction = str(event.get("type") or event.get("side") or "UNKNOWN")
        trades_by_grade[grade] += 1
        trades_by_setup[setup] += 1
        trades_by_direction[direction] += 1

        # Find matching close event
        ticket = event.get("ticket")
        close_evt = next(
            (e for e in close_events if e.get("ticket") == ticket), None
        )
        partial_evts = [e for e in partial_events if e.get("ticket") == ticket]
        be_evt = next(
            (e for e in be_events if e.get("ticket") == ticket), None
        )

        detail: dict[str, Any] = {
            "ticket": ticket,
            "source": event.get("source"),
            "setup_type": event.get("setup_type"),
            "grade": grade,
            "direction": direction,
            "opened_at": event.get("time") or event.get("opened_at"),
            "entry_price": event.get("entry_price"),
            "stop_loss": event.get("current_sl") or event.get("original_sl"),
            "risk_cash": event.get("risk_cash"),
            "risk_pct": event.get("risk_pct"),
            "volume": event.get("volume_original"),
            "tp1": event.get("tp1"),
            "tp2": event.get("tp2"),
            "partial_closed": bool(partial_evts),
            "breakeven_activated": bool(be_evt),
            "bars_alive": None,
        }

        if close_evt:
            detail["close_reason"] = close_evt.get("reason")
            detail["close_time"] = close_evt.get("time")
            detail["realized_R"] = close_evt.get("r_multiple")
            detail["lifecycle_status"] = "CLOSED"
            detail["pnl"] = close_evt.get("pnl")
        else:
            # Check if open at end
            open_snap = next(
                (o for o in open_end_details if o.get("ticket") == ticket), None
            )
            if open_snap:
                detail["lifecycle_status"] = "OPEN_AT_REPLAY_END"
                detail["unrealized_R"] = open_snap.get("unrealized_R")
                detail["last_seen_candle"] = open_snap.get("last_seen_candle_time")
                detail["last_seen_close"] = open_snap.get("last_seen_close")
            else:
                detail["lifecycle_status"] = "UNKNOWN"

        trade_details.append(detail)

    # ── Rejection breakdown ──────────────────────────────────────────────
    rejection_reasons = Counter(
        str(e.get("reason") or "UNKNOWN") for e in rejected_events
    )

    # ── Blocker distribution (if < 10 trades) ────────────────────────────
    blockers_distribution: dict[str, int] = {}
    enter_decisions = [
        d for d in decisions
        if str(d.get("decision") or "") in {"ENTER_FULL", "ENTER_REDUCED"}
    ]
    if len(trade_details) < 10:
        blocker_counter: Counter = Counter()
        for d in decisions:
            action = str(d.get("decision") or "UNKNOWN")
            if action in {"ENTER_FULL", "ENTER_REDUCED"}:
                continue
            readiness = str(d.get("readiness_state") or d.get("readiness_reason") or "UNKNOWN")
            blocker_counter[readiness] += 1
            for blocker in d.get("enter_eligibility_blockers") or []:
                blocker_counter[str(blocker)] += 1
        blockers_distribution = dict(blocker_counter.most_common(20))

    # ── Near-miss top reasons ────────────────────────────────────────────
    near_miss_reasons: Counter = Counter()
    near_miss_setup_types: Counter = Counter()
    for d in decisions:
        action = str(d.get("decision") or "UNKNOWN")
        if action in {"WATCH_ONLY", "WAIT_FOR_TRIGGER"}:
            setup = str(d.get("setup_type") or "UNKNOWN")
            if setup != "UNKNOWN":
                near_miss_setup_types[setup] += 1
            reason = str(d.get("readiness_reason") or d.get("veto_code") or "UNKNOWN")
            near_miss_reasons[reason] += 1
            for blocker in d.get("enter_eligibility_blockers") or []:
                near_miss_reasons[str(blocker)] += 1

    return {
        "run_id": run_id,
        "total_decisions": total_decisions,
        "ENTER_FULL": enter_full,
        "ENTER_REDUCED": enter_reduced,
        "enter_eligible_count": enter_eligible_count,
        "risk_multiplier_positive": risk_positive,
        "risk_positive_but_not_enter_eligible_count": risk_positive_not_eligible,
        "signals": summary.get("signals", 0),
        "open_events": len(open_events),
        "closed_events": len(close_events),
        "missed_entries": len(missed_events),
        "partial_closes": len(partial_events),
        "be_plus_moves": len(be_events),
        "open_trades_end_count": open_end_count,
        "open_trades_end_details": open_end_details,
        "unrealized_R_total": unrealized_r_total,
        "unrealized_pnl": unrealized_pnl,
        "realized_R_values": realized_r_values,
        "profit_factor": profit_factor,
        "expectancy_R": expectancy_r,
        "performance_status": performance_status,
        "daily_trade_counts": daily_trade_counts,
        "daily_limit_rejections": daily_limit_rejections,
        "grade_blocked_count": grade_blocked,
        "trades_by_grade": dict(trades_by_grade.most_common()),
        "trades_by_setup_type": dict(trades_by_setup.most_common()),
        "trades_by_direction": dict(trades_by_direction.most_common()),
        "trade_details": trade_details,
        "rejection_reasons": dict(rejection_reasons.most_common(25)),
        "blockers_distribution": blockers_distribution,
        "near_miss_reasons": dict(near_miss_reasons.most_common(20)),
        "near_miss_setup_types": dict(near_miss_setup_types.most_common(20)),
        "decision_distribution": dict(decision_counter.most_common()),
        "p2c_performance_summary": summary.get("p2c_performance_summary") or summary.get("performance_summary", {}),
    }


def _format_currency(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _format_r(value: Any) -> str:
    try:
        v = float(value)
        return f"{v:+.2f}R"
    except (TypeError, ValueError):
        return str(value)


def generate_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Phase18 Shadow Trade Lifecycle Diagnostic")
    lines.append("")
    lines.append(f"**Run ID:** `{report['run_id']}`")
    lines.append(f"**Performance Status:** `{report['performance_status']}`")
    lines.append("")

    # ── Overview ─────────────────────────────────────────────────────────
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- Total decisions: **{report['total_decisions']}**")
    lines.append(f"- ENTER_FULL: **{report['ENTER_FULL']}**")
    lines.append(f"- ENTER_REDUCED: **{report['ENTER_REDUCED']}**")
    lines.append(f"- enter_eligible_count: **{report['enter_eligible_count']}**")
    lines.append(f"- risk_multiplier_positive: **{report['risk_multiplier_positive']}**")
    lines.append(f"- risk_positive_but_not_enter_eligible: **{report['risk_positive_but_not_enter_eligible_count']}**")
    lines.append("")

    # ── Trade lifecycle ──────────────────────────────────────────────────
    lines.append("## Trade Lifecycle")
    lines.append("")
    lines.append(f"- Signals: **{report['signals']}**")
    lines.append(f"- Open events: **{report['open_events']}**")
    lines.append(f"- Closed events: **{report['closed_events']}**")
    lines.append(f"- Missed entries: **{report['missed_entries']}**")
    lines.append(f"- Partial closes: **{report['partial_closes']}**")
    lines.append(f"- BE+ moves: **{report['be_plus_moves']}**")
    lines.append(f"- Open trades at replay end: **{report['open_trades_end_count']}**")
    lines.append(f"- Unrealized R total: **{_format_r(report['unrealized_R_total']) if report['unrealized_R_total'] is not None else 'N/A'}**")
    lines.append(f"- Unrealized PnL: **{_format_currency(report['unrealized_pnl'])}**")
    lines.append("")

    # ── Performance ──────────────────────────────────────────────────────
    lines.append("## Performance")
    lines.append("")
    pf = report["profit_factor"]
    ex = report["expectancy_R"]
    lines.append(f"- Profit Factor: **{pf if pf is not None else 'null (no closed trades)'}**")
    lines.append(f"- Expectancy R: **{ex if ex is not None else 'null (no closed trades)'}**")
    if report["realized_R_values"]:
        lines.append(f"- Realized R values: {', '.join(_format_r(v) for v in report['realized_R_values'][:20])}")
    lines.append(f"- Daily limit rejections: **{report['daily_limit_rejections']}**")
    lines.append(f"- Grade blocked: **{report['grade_blocked_count']}**")
    lines.append("")

    # ── Daily trade counts ───────────────────────────────────────────────
    daily = report.get("daily_trade_counts", {})
    if daily:
        lines.append("## Daily Trade Counts")
        lines.append("")
        for day in sorted(daily):
            lines.append(f"- {day}: **{daily[day]}** trade(s)")
        lines.append("")

    # ── Trades by category ───────────────────────────────────────────────
    lines.append("## Trades by Category")
    lines.append("")
    lines.append("### By Grade")
    for grade, count in report.get("trades_by_grade", {}).items():
        lines.append(f"- {grade}: **{count}**")
    lines.append("")
    lines.append("### By Setup Type")
    for setup, count in report.get("trades_by_setup_type", {}).items():
        lines.append(f"- {setup}: **{count}**")
    lines.append("")
    lines.append("### By Direction")
    for direction, count in report.get("trades_by_direction", {}).items():
        lines.append(f"- {direction}: **{count}**")
    lines.append("")

    # ── Trade details ────────────────────────────────────────────────────
    details = report.get("trade_details") or []
    if details:
        lines.append("## Trade Details")
        lines.append("")
        for t in details[:30]:
            lines.append(f"### Ticket #{t.get('ticket')} — {t.get('grade')} {t.get('direction')} {t.get('setup_type')}")
            lines.append("")
            lines.append(f"- Status: **{t.get('lifecycle_status')}**")
            lines.append(f"- Opened: {t.get('opened_at')}")
            lines.append(f"- Entry: {t.get('entry_price')}")
            lines.append(f"- SL: {t.get('stop_loss')}")
            lines.append(f"- TP1: {t.get('tp1')} | TP2: {t.get('tp2')}")
            lines.append(f"- Risk: {_format_currency(t.get('risk_cash'))} ({t.get('risk_pct')}%)")
            lines.append(f"- Volume: {t.get('volume')}")
            lines.append(f"- Partial closed: {t.get('partial_closed')}")
            lines.append(f"- BE+ activated: {t.get('breakeven_activated')}")
            if t.get("close_reason"):
                lines.append(f"- Close reason: **{t.get('close_reason')}**")
                lines.append(f"- Realized R: **{_format_r(t.get('realized_R'))}**")
            if t.get("unrealized_R") is not None:
                lines.append(f"- Unrealized R: **{_format_r(t.get('unrealized_R'))}**")
                lines.append(f"- Last seen candle: {t.get('last_seen_candle')}")
            lines.append("")

    # ── Blockers ─────────────────────────────────────────────────────────
    blockers = report.get("blockers_distribution", {})
    if blockers:
        lines.append("## Blocker Distribution")
        lines.append("")
        for reason, count in list(blockers.items())[:20]:
            lines.append(f"- {reason}: **{count}**")
        lines.append("")

    # ── Near-miss ────────────────────────────────────────────────────────
    near_miss = report.get("near_miss_reasons", {})
    if near_miss:
        lines.append("## Top Near-Miss Reasons")
        lines.append("")
        for reason, count in list(near_miss.items())[:20]:
            lines.append(f"- {reason}: **{count}**")
        lines.append("")

    # ── Decision distribution ────────────────────────────────────────────
    lines.append("## Decision Distribution")
    lines.append("")
    for decision, count in report.get("decision_distribution", {}).items():
        lines.append(f"- {decision}: **{count}**")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase18 Shadow Trade Lifecycle Diagnostic")
    parser.add_argument("--run-dir", required=True, help="Path to replay run directory")
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print(f"ERROR: run-dir not found: {run_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Diagnosing Phase18 shadow trade lifecycle from: {run_dir}")

    report = diagnose(run_dir)

    json_path = os.path.join(run_dir, "phase18_shadow_trade_lifecycle.json")
    md_path = os.path.join(run_dir, "phase18_shadow_trade_lifecycle.md")

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=_iso, ensure_ascii=False)

    markdown = generate_markdown(report)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)

    print(f"JSON report written to: {json_path}")
    print(f"Markdown report written to: {md_path}")
    print(f"\nPerformance Status: {report['performance_status']}")
    print(f"Trades: {report['open_events']} open, {report['closed_events']} closed, {report['open_trades_end_count']} open at end")
    pf = report["profit_factor"]
    ex = report["expectancy_R"]
    print(f"Profit Factor: {pf if pf is not None else 'null'}")
    print(f"Expectancy R: {ex if ex is not None else 'null'}")


if __name__ == "__main__":
    main()

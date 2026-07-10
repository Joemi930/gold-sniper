"""Compact report extraction from replay outputs.

Extracts the essential information from replay summaries and writes
compact, GPT/Opus-readable reports.  Temporary logs are cleaned up.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # gold_sniper/
_REPO_ROOT = _PROJECT_ROOT.parent  # repo root
for p in (str(_PROJECT_ROOT), str(_REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_TMP_ROOT = Path(".tmp/replay_runs")
DEFAULT_REPORTS_ROOT = Path("reports/replay")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def extract_important_trades(summary: dict[str, Any], run_dir: str | None = None) -> list[dict[str, Any]]:
    """Extract key trades from a replay summary and/or trade journal JSONL.

    If run_dir is provided, reads the trade_journal.jsonl for per-trade
    details.  Falls back to summary-level aggregation.
    """
    trades: list[dict[str, Any]] = []

    # ---- Try trade journal JSONL first (most accurate) ----
    if run_dir:
        journal_path = Path(run_dir) / "trade_journal.jsonl"
        if journal_path.exists():
            try:
                raw_trades: dict[int, dict[str, Any]] = {}
                with open(journal_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                        except Exception:
                            continue
                        ticket = event.get("ticket")
                        if ticket is None:
                            continue
                        if ticket not in raw_trades:
                            raw_trades[ticket] = {
                                "ticket": ticket, "entry_time": "", "exit_time": "",
                                "side": "", "entry": 0.0, "exit": 0.0,
                                "pnl_r": 0.0, "grade": "", "result": "",
                                "tp1": False, "tp2": False, "sl": False, "protected_sl": False,
                            }
                        t = raw_trades[ticket]
                        etype = str(event.get("event", event.get("reason", "")))
                        reason = str(event.get("reason", ""))
                        if etype == "open":
                            t["entry_time"] = str(event.get("time", t["entry_time"]))
                            t["side"] = str(event.get("side") or event.get("type") or t["side"])
                            t["entry"] = _safe_float(event.get("entry_price", t["entry"]))
                        elif etype == "close" or "PARENT" in reason.upper():
                            # Parent close — captures final P&L and grade
                            t["pnl_r"] = _safe_float(event.get("parent_pnl_R") or event.get("r_multiple") or event.get("pnl_R") or event.get("net_r") or 0)
                            t["result"] = reason
                            # BUG-4: grade from parent close or open event
                            grade = str(event.get("setup_grade") or event.get("kasper_grade") or event.get("tier") or "")
                            if grade:
                                t["grade"] = grade
                        elif etype == "leg_close":
                            t["exit_time"] = str(event.get("time", t["exit_time"]))
                            if "TP1" in reason:
                                t["tp1"] = True
                            if "TP2" in reason:
                                t["tp2"] = True
                            if "SL" in reason and "PROTECTED" not in reason:
                                t["sl"] = True
                            if "PROTECTED" in reason:
                                t["protected_sl"] = True
                            t["exit"] = _safe_float(event.get("exit_price", t["exit"]))
                trades = sorted(raw_trades.values(), key=lambda x: x["entry_time"])
            except Exception:
                pass

    # BUG-6: NO synthetic fallback. If no real trade journal, trades stays empty.
    # The report will show "INVALID: no real trade journal" downstream.

    return trades


def build_optimization_findings(summary: dict[str, Any]) -> dict[str, Any]:
    """Derive optimization hints from summary data."""
    findings: dict[str, Any] = {
        "top_rejection_reasons": [],
        "best_sessions": [],
        "grade_performance": {},
        "suggestions": [],
    }

    # Grade performance
    grades = summary.get("grade_breakdown", summary.get("grades", {}))
    if isinstance(grades, dict):
        for grade, data in grades.items():
            if isinstance(data, dict):
                findings["grade_performance"][grade] = {
                    "trades": _safe_int(data.get("trades", data.get("count", 0))),
                    "winrate": _safe_float(data.get("winrate", data.get("win_rate_pct", 0))),
                    "expectancy_r": _safe_float(data.get("expectancy_R", data.get("expectancy_r", 0))),
                }

    # Rejection reasons
    rejections = summary.get("rejection_reasons", summary.get("blocked_reasons", {}))
    if isinstance(rejections, dict):
        sorted_r = sorted(rejections.items(), key=lambda x: _safe_int(x[1]) if isinstance(x[1], int) else 0, reverse=True)
        findings["top_rejection_reasons"] = sorted_r[:10]

    # Session performance
    sessions = summary.get("session_breakdown", summary.get("sessions", {}))
    if isinstance(sessions, dict):
        for sess, data in sessions.items():
            if isinstance(data, dict):
                findings["best_sessions"].append({
                    "session": sess,
                    "trades": _safe_int(data.get("trades", data.get("count", 0))),
                    "winrate": _safe_float(data.get("winrate", data.get("win_rate_pct", 0))),
                })
        findings["best_sessions"].sort(
            key=lambda x: x["winrate"], reverse=True
        )

    # Generate suggestions
    # P4.1 fix: correct field name priority — engine uses "win_rate"
    wr = _safe_float(
        summary.get("win_rate")
        or summary.get("winrate")
        or summary.get("win_rate_pct")
        or summary.get("winrate_pct")
        or 0
    )
    ex = _safe_float(summary.get("expectancy_R", 0))
    trade_count = _safe_int(summary.get("parent_trades", summary.get("total_trades", summary.get("trades_closed", 0))))
    # P4.1: use trades_per_eval_day if available, else fall back with sensible default
    avg_per_day = _safe_float(summary.get("trades_per_eval_day", summary.get("trades_per_day", 0)))
    if avg_per_day == 0 and trade_count > 0:
        avg_per_day = 0.01  # non-zero sentinel to avoid divide-by-zero noise

    if avg_per_day < 1.0:
        findings["suggestions"].append(
            "Trade frequency below 1/day — consider reviewing POI strictness or "
            "micro confirmation requirements if winrate is healthy."
        )
    if avg_per_day > 3.0:
        findings["suggestions"].append(
            "Trade frequency above 3/day — consider stricter filters or higher "
            "minimum grade to avoid overtrading."
        )
    if wr < 50:
        findings["suggestions"].append(
            "Winrate below 50% — audit losing trades for common failure patterns, "
            "review Kasper sequence gate strictness."
        )
    if ex < 0:
        findings["suggestions"].append(
            "Negative expectancy — review risk/reward structure, check if "
            "full SL events dominate TP2 events."
        )
    if wr >= 65 and ex > 0.2:
        findings["suggestions"].append(
            "Healthy winrate and expectancy — focus on increasing trade frequency "
            "without degrading quality."
        )

    return findings


def write_compact_report(
    summary: dict[str, Any],
    run_id: str,
    output_dir: Path | str,
    *,
    trades: list[dict[str, Any]] | None = None,
    optimization_findings: dict[str, Any] | None = None,
    run_dir: str | None = None,
) -> Path:
    """Write the compact report directory for a replay run.

    Returns the path to the REPORT.md file.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    if trades is None:
        trades = extract_important_trades(summary, run_dir=run_dir)
    if optimization_findings is None:
        optimization_findings = build_optimization_findings(summary)

    # --- metrics.json ---
    # Map both legacy and current summary field names
    pure_R = _safe_float(summary.get("pure_expectancy_R", summary.get("pure_R", 0)))
    net_R = _safe_float(summary.get("expectancy_R", summary.get("net_R", 0)))
    cost_drag = _safe_float(summary.get("cost_drag_R", round(pure_R - net_R, 6)))

    metrics = {
        "initial_equity": _safe_float(summary.get("initial_equity", 100)),
        "final_equity": _safe_float(summary.get("final_equity", summary.get("equity", 100))),
        "net_pnl": _safe_float(summary.get("net_pnl", summary.get("net_pnl_R", 0))),
        "net_pnl_pct": _safe_float(summary.get("net_pnl_pct", 0)),
        "pure_R": pure_R,
        "net_R": net_R,
        "winrate_pct": _safe_float(summary.get("win_rate", summary.get("winrate", summary.get("win_rate_pct", 0)))),
        "winrate_full_win": _safe_float(summary.get("winrate_full_win", 0)),
        "winrate_tp1_touch": _safe_float(summary.get("winrate_tp1_touch", 0)),
        "expectancy_R": net_R,
        "cost_drag_R": cost_drag,
        "max_drawdown_pct": _safe_float(summary.get("max_drawdown_pct", 0)),
        "total_trades": _safe_int(summary.get("parent_trades", summary.get("trades", summary.get("closed_trades", 0)))),
        "tp1_count": _safe_int(summary.get("tp1_hit_count", summary.get("tp1_count", 0))),
        "tp2_count": _safe_int(summary.get("tp2_hit_count", summary.get("tp2_count", 0))),
        # P4: separate parent full SL from leg SL
        "full_sl_count": _safe_int(summary.get("parent_full_sl_count", summary.get("full_sl_count", 0))),
        "leg_sl_count": _safe_int(summary.get("leg_sl_count", summary.get("sl_hit_count", 0))),
        "protected_sl_count": _safe_int(summary.get("protected_sl_hit_count", summary.get("protected_sl_count", 0))),
        "trades_per_day": _safe_float(summary.get("trades_per_day", 0)),
        "trades_per_eval_day": _safe_float(summary.get("trades_per_eval_day", 0)),
        "trades_per_active_day": _safe_float(summary.get("trades_per_active_day", 0)),
        "avg_win_R": _safe_float(summary.get("avg_win_R", 0)),
        "avg_loss_R": _safe_float(summary.get("avg_loss_R", 0)),
        "payoff_ratio": _safe_float(summary.get("payoff_ratio", 0)),
        # P4: payoff diagnostics
        "tp1_tp2_avg_net_R": _safe_float(summary.get("tp1_tp2_avg_net_R", 0)),
        "tp1_tp2_avg_pure_R": _safe_float(summary.get("tp1_tp2_avg_pure_R", 0)),
        "tp1_protected_avg_net_R": _safe_float(summary.get("tp1_protected_avg_net_R", 0)),
        "tp1_protected_avg_pure_R": _safe_float(summary.get("tp1_protected_avg_pure_R", 0)),
        "avg_cost_drag_per_trade_R": _safe_float(summary.get("avg_cost_drag_per_trade_R", 0)),
        # P4.1: cost component breakdown
        "total_spread_points": _safe_float(summary.get("total_spread_points", 0)),
        "total_slippage_points": _safe_float(summary.get("total_slippage_points", 0)),
        "total_commission_R": _safe_float(summary.get("total_commission_R", 0)),
        "avg_spread_per_trade": _safe_float(summary.get("avg_spread_per_trade", 0)),
        "avg_slippage_per_trade": _safe_float(summary.get("avg_slippage_per_trade", 0)),
        "avg_commission_per_trade": _safe_float(summary.get("avg_commission_per_trade", 0)),
        # P4: trade time boundaries
        "first_trade_time": summary.get("first_trade_time"),
        "last_trade_time": summary.get("last_trade_time"),
        "warmup_trade_count": _safe_int(summary.get("warmup_trade_count", 0)),
    }
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- summary.json ---
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    # --- important_trades.jsonl ---
    trades_path = output / "important_trades.jsonl"
    with open(trades_path, "w", encoding="utf-8") as f:
        for t in trades:
            f.write(json.dumps(t, ensure_ascii=False, default=str) + "\n")

    # --- optimization_findings.json ---
    (output / "optimization_findings.json").write_text(
        json.dumps(optimization_findings, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # --- REPORT.md ---
    _write_markdown_report(output / "REPORT.md", run_id, metrics, trades, optimization_findings, summary)

    return output / "REPORT.md"


def _write_markdown_report(
    path: Path,
    run_id: str,
    metrics: dict[str, Any],
    trades: list[dict[str, Any]],
    findings: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    """Write a compact, readable REPORT.md."""
    lines = [
        f"# Gold Sniper Replay Report — {run_id}",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "---",
        "",
        "## Summary Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]

    metric_labels = [
        ("Initial Equity", f"${metrics['initial_equity']:.2f}"),
        ("Final Equity", f"${metrics['final_equity']:.2f}"),
        ("Net P&L", f"${metrics['net_pnl']:+.2f}"),
        ("Net P&L %", f"{metrics['net_pnl_pct']:+.2f}%"),
        ("Pure R", f"{metrics['pure_R']:+.2f}R"),
        ("Net R (expectancy)", f"{metrics['net_R']:+.2f}R"),
        ("Winrate (parent)", f"{metrics['winrate_pct']:.1f}%"),
        ("Winrate (full win / TP1 touch)", f"{metrics['winrate_full_win']:.1f}% / {metrics['winrate_tp1_touch']:.1f}%"),
        ("Cost Drag R", f"{metrics['cost_drag_R']:+.4f}R"),
        ("Max Drawdown", f"{metrics['max_drawdown_pct']:.2f}%"),
        ("Total Trades", str(metrics['total_trades'])),
        ("TP1 / TP2 / Parent Full SL / Prot SL / Leg SL",
         f"{metrics['tp1_count']} / {metrics['tp2_count']} / {metrics['full_sl_count']} / {metrics['protected_sl_count']} / {metrics['leg_sl_count']}"),
        ("Trades/Day", f"{metrics['trades_per_day']:.2f}"),
        ("Trades/Eval Day", f"{metrics['trades_per_eval_day']:.2f}"),
        ("Trades/Active Day", f"{metrics['trades_per_active_day']:.2f}"),
        ("Avg Win R", f"{metrics['avg_win_R']:.2f}R"),
        ("Avg Loss R", f"{metrics['avg_loss_R']:.2f}R"),
        ("Payoff Ratio", f"{metrics['payoff_ratio']:.2f}"),
    ]
    for label, value in metric_labels:
        lines.append(f"| {label} | {value} |")

    # ── P4: Period & trade boundaries ──────────────────────────────────
    warmup_start = summary.get("warmup_start", "—")
    eval_start = summary.get("eval_start", "—")
    eval_end = summary.get("eval_end", "—")
    first_trade = metrics.get("first_trade_time") or "—"
    last_trade = metrics.get("last_trade_time") or "—"
    warmup_trades = metrics.get("warmup_trade_count", 0)

    lines += [
        "",
        "---",
        "",
        "## Period & Boundaries",
        "",
        f"- **Warmup start:** {warmup_start}",
        f"- **Eval start:** {eval_start}",
        f"- **Eval end:** {eval_end}",
        f"- **First trade:** {first_trade}",
        f"- **Last trade:** {last_trade}",
        f"- **Warmup trades:** {warmup_trades}",
        "",
    ]

    # ── P4: Payoff diagnostics ─────────────────────────────────────────
    tp1_tp2_net = metrics.get("tp1_tp2_avg_net_R", 0)
    tp1_tp2_pure = metrics.get("tp1_tp2_avg_pure_R", 0)
    tp1_prot_net = metrics.get("tp1_protected_avg_net_R", 0)
    tp1_prot_pure = metrics.get("tp1_protected_avg_pure_R", 0)
    avg_cost_drag = metrics.get("avg_cost_drag_per_trade_R", 0)

    if tp1_tp2_net or tp1_tp2_pure or tp1_prot_net or tp1_prot_pure:
        avg_spread = metrics.get("avg_spread_per_trade", 0)
        avg_slippage = metrics.get("avg_slippage_per_trade", 0)
        avg_commission = metrics.get("avg_commission_per_trade", 0)
        total_spread = metrics.get("total_spread_points", 0)
        total_slippage = metrics.get("total_slippage_points", 0)
        total_commission = metrics.get("total_commission_R", 0)
        lines += [
            "---",
            "",
            "## Payoff Diagnostics (Pure vs Net R)",
            "",
            "| Scenario | Avg Net R | Avg Pure R | Cost Drag |",
            "|----------|-----------|------------|-----------|",
            f"| TP1+TP2 | {tp1_tp2_net:+.4f}R | {tp1_tp2_pure:+.4f}R | {round(tp1_tp2_pure - tp1_tp2_net, 4):+.4f}R |",
            f"| TP1+Protected | {tp1_prot_net:+.4f}R | {tp1_prot_pure:+.4f}R | {round(tp1_prot_pure - tp1_prot_net, 4):+.4f}R |",
            f"| **Avg/Trade** | — | — | {avg_cost_drag:+.4f}R |",
            "",
            "### Cost Component Breakdown",
            "",
            "| Component | Total (all trades) | Per Trade Avg |",
            "|-----------|-------------------|---------------|",
            f"| Spread | {total_spread:.1f} pts | {avg_spread:.1f} pts/trade |",
            f"| Slippage | {total_slippage:.1f} pts | {avg_slippage:.1f} pts/trade |",
            f"| Commission | {total_commission:.4f} R | {avg_commission:.4f} R/trade |",
            f"| **Total Cost Drag** | **{avg_cost_drag:+.4f} R/trade** | — |",
            "",
        ]

    lines += [
        "---",
        "",
        "## Important Trades",
        "",
    ]

    if trades:
        lines += [
            "| # | Time | Side | Grade | P&L (R) | TP1 | TP2 | SL |",
            "|---|------|------|-------|---------|-----|-----|----|",
        ]
        for i, t in enumerate(trades[:50], 1):  # max 50 trades in report
            lines.append(
                f"| {i} | {t.get('entry_time', '')} | {t.get('side', '')} | "
                f"{t.get('grade', '')} | {t.get('pnl_r', 0):+.2f}R | "
                f"{'Y' if t.get('tp1') else ''} | {'Y' if t.get('tp2') else ''} | "
                f"{'X' if t.get('sl') else ''} |"
            )
    else:
        lines.append("_No trades taken during this replay period._")

    lines += [
        "",
        "---",
        "",
        "## Optimization Findings",
        "",
    ]

    suggestions = findings.get("suggestions", [])
    if suggestions:
        for s in suggestions:
            lines.append(f"- {s}")
    else:
        lines.append("_No specific optimization suggestions._")

    # Grade performance
    grades = findings.get("grade_performance", {})
    if grades:
        lines += [
            "",
            "### Grade Performance",
            "",
            "| Grade | Trades | Winrate | Expectancy R |",
            "|-------|--------|---------|--------------|",
        ]
        for grade, data in sorted(grades.items()):
            lines.append(
                f"| {grade} | {data.get('trades', 0)} | "
                f"{data.get('winrate', 0):.1f}% | "
                f"{data.get('expectancy_r', 0):+.2f}R |"
            )

    # Top rejection reasons
    rejections = findings.get("top_rejection_reasons", [])
    if rejections:
        lines += [
            "",
            "### Top Rejection Reasons",
            "",
        ]
        for reason, count in rejections[:5]:
            lines.append(f"- **{reason}**: {count} occurrences")

    # Best sessions
    sessions = findings.get("best_sessions", [])
    if sessions:
        lines += [
            "",
            "### Session Performance",
            "",
            "| Session | Trades | Winrate |",
            "|---------|--------|---------|",
        ]
        for s in sessions[:6]:
            lines.append(f"| {s['session']} | {s['trades']} | {s['winrate']:.1f}% |")

    lines += [
        "",
        "---",
        "",
        "## Data Coverage",
        "",
    ]
    coverage = summary.get("data_coverage", {})
    if coverage:
        tfs = coverage.get("timeframes", {})
        if isinstance(tfs, dict):
            for tf_name, tf_data in sorted(tfs.items()):
                if isinstance(tf_data, dict):
                    lines.append(
                        f"- **{tf_name}**: {tf_data.get('candles', 0)} candles, "
                        f"{tf_data.get('coverage_status', '?')}, "
                        f"gaps: {tf_data.get('gaps', '?')}"
                    )

    path.write_text("\n".join(lines), encoding="utf-8")


def cleanup_temp_logs(run_id: str | None = None) -> int:
    """Remove temporary replay logs.  Returns number of files removed."""
    count = 0
    tmp_root = DEFAULT_TMP_ROOT
    if run_id:
        target = tmp_root / run_id
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            count = 1
    else:
        if tmp_root.exists():
            # Remove all subdirectories
            for child in list(tmp_root.iterdir()):
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                    count += 1
    return count

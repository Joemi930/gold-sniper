"""P4.2 — ReportWriterV2.

Produces honest, compact reports from MetricsAggregator data.
- NO_TRADES state clearly marked (winrate/expectancy = None, not 0%)
- Top blockers/rejection reasons visible
- No synthetic/fallback trades
- Compact markdown + JSON output
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ReportWriterV2:
    """Writes performance, gating, runtime, and Opus-ready summary reports.

    Input: MetricsAggregator.finalize() dict + optional profiler report.
    """

    run_dir: Path
    summary: dict[str, Any]
    profiler_report: dict[str, Any] | None = None

    # ── write all ─────────────────────────────────────────────────────

    def write_all(self) -> list[Path]:
        """Write all report files. Returns list of paths written."""
        paths: list[Path] = []
        paths.append(self.write_summary_json())
        paths.append(self.write_performance_md())
        paths.append(self.write_gating_md())
        return paths

    # ── summary.json ──────────────────────────────────────────────────

    def write_summary_json(self) -> Path:
        """Write compact summary JSON."""
        path = self.run_dir / "summary_v2.json"
        # Filter out verbose internals
        clean = self._clean_summary(self.summary)
        path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _clean_summary(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Remove verbose/diagnostic-only fields from the public summary."""
        keep = {
            "state", "candle_count", "eval_candle_count", "warmup_candle_count",
            "decision_count", "candidate_count", "window_count", "trade_count",
            "decisions", "trade_outcomes", "winrate", "expectancy_R",
            "avg_cost_drag_R", "total_pnl_R", "total_cost_drag_R",
            "top_reject_reasons", "top_veto_codes", "top_setup_types",
            "gate_rejections", "grade_distribution", "poi_reaction_skipped",
            "no_trade_diagnostic", "profiler",
            "WARNING", "synthetic_trades",  # synthetic trade guard — always visible
        }
        return {k: v for k, v in raw.items() if k in keep or k.startswith("profiler_")}

    # ── performance.md ────────────────────────────────────────────────

    def write_performance_md(self) -> Path:
        """Write a compact performance report in markdown."""
        path = self.run_dir / "performance_v2.md"
        s = self.summary
        lines: list[str] = []

        lines.append("# Performance Report — ReplayEngineV2")
        lines.append("")

        # State
        state = s.get("state", "OK")
        lines.append(f"**State**: `{state}`")
        lines.append("")

        # Counts
        lines.append("## Counts")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Candles (total) | {s.get('candle_count', 0)} |")
        lines.append(f"| Candles (eval) | {s.get('eval_candle_count', 0)} |")
        lines.append(f"| Candles (warmup) | {s.get('warmup_candle_count', 0)} |")
        lines.append(f"| Decisions | {s.get('decision_count', 0)} |")
        lines.append(f"| Candidates | {s.get('candidate_count', 0)} |")
        lines.append(f"| Windows evaluated | {s.get('window_count', 0)} |")
        lines.append(f"| Trades | {s.get('trade_count', 0)} |")
        lines.append("")

        # Decision breakdown
        dec = s.get("decisions", {})
        if dec:
            lines.append("## Decision Breakdown")
            lines.append("")
            lines.append(f"| Decision | Count |")
            lines.append(f"|----------|-------|")
            for k, v in dec.items():
                lines.append(f"| {k} | {v} |")
            lines.append("")

        # Trade outcomes
        if s.get("trade_count", 0) > 0:
            outcomes = s.get("trade_outcomes", {})
            lines.append("## Trade Outcomes")
            lines.append("")
            lines.append(f"| Outcome | Count |")
            lines.append(f"|---------|-------|")
            for k, v in outcomes.items():
                lines.append(f"| {k} | {v} |")
            lines.append("")
            lines.append(f"- **Winrate**: {_fmt_pct(s.get('winrate'))}")
            lines.append(f"- **Expectancy**: {_fmt_r(s.get('expectancy_R'))}")
            lines.append(f"- **Avg Cost Drag**: {_fmt_r(s.get('avg_cost_drag_R'))}")
            lines.append(f"- **Total PnL**: {_fmt_r(s.get('total_pnl_R'))}")
            lines.append("")

        # Top rejection reasons
        top_rej = s.get("top_reject_reasons", [])
        if top_rej:
            lines.append("## Top Rejection Reasons")
            lines.append("")
            for item in top_rej[:5]:
                lines.append(f"- **{item['reason']}**: {item['count']}")
            lines.append("")

        # Profiler
        if self.profiler_report:
            lines.append("## Profiler")
            lines.append("")
            pr = self.profiler_report
            lines.append(f"- **Total**: {pr.get('ms_total', 0):.0f}ms")
            lines.append(f"- **Coverage**: {pr.get('coverage_pct', 0)}%")
            lines.append(f"- **Unaccounted**: {pr.get('unaccounted_ms', 0):.0f}ms")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    # ── gating.md ─────────────────────────────────────────────────────

    def write_gating_md(self) -> Path:
        """Write a compact gating diagnostic report."""
        path = self.run_dir / "gating_v2.md"
        s = self.summary
        lines: list[str] = []

        lines.append("# Gating Report — ReplayEngineV2")
        lines.append("")

        gate_rej = s.get("gate_rejections", {})
        if gate_rej:
            lines.append("## Gate Rejections")
            lines.append("")
            for gate, count in sorted(gate_rej.items(), key=lambda x: -x[1]):
                lines.append(f"- **{gate}**: {count}")
            lines.append("")

        top_veto = s.get("top_veto_codes", [])
        if top_veto:
            lines.append("## Top Veto Codes")
            lines.append("")
            for item in top_veto[:5]:
                lines.append(f"- **{item['code']}**: {item['count']}")
            lines.append("")

        poi_skipped = s.get("poi_reaction_skipped", 0)
        lines.append(f"## POI_REACTION Skipped: {poi_skipped}")
        lines.append("")

        grade_dist = s.get("grade_distribution", {})
        if grade_dist:
            lines.append("## Grade Distribution")
            lines.append("")
            for grade, count in sorted(grade_dist.items()):
                lines.append(f"- **{grade}**: {count}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        return path


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _fmt_r(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.4f}R"
    except (TypeError, ValueError):
        return str(value)

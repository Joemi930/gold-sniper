"""TUI display helpers for Gold Sniper Replay Control Center.

Uses ``rich`` when available; falls back to plain-text output otherwise.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

_HAS_RICH = False
try:
    from rich.console import Console
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich.progress import BarColumn, Progress, TextColumn, TaskProgressColumn
    from rich import box

    _HAS_RICH = True
except ImportError:
    pass

AGENT_LABELS = {
    "agent_1": "Agent1  HTF/Meteo",
    "agent_2": "Agent2  POI/Carto",
    "agent_3": "Agent3  Liquidite",
    "agent_4": "Agent4  Structure",
    "agent_5": "Agent5  Micro/Trigger",
    "agent_6": "Agent6  News/Sentinel",
    "agent_7": "Agent7  Sessions",
}

DECISION_COLORS = {
    "ENTER_FULL": "bold green",
    "ENTER_REDUCED": "green",
    "WAIT_FOR_TRIGGER": "yellow",
    "WAIT_FOR_BETTER_PRICE": "yellow",
    "WATCH_ONLY": "dim yellow",
    "REJECT": "red",
    "—": "dim white",
}


def has_rich() -> bool:
    return _HAS_RICH


# ═══════════════════════════════════════════════════════════════════════════════
# Rich-based TUI
# ═══════════════════════════════════════════════════════════════════════════════

if _HAS_RICH:
    _console = Console()

    def _agent_status_bar(scores: dict[str, float], statuses: dict[str, str]) -> Table:
        """Build an agent workspace table."""
        table = Table(box=box.SIMPLE, expand=True, show_header=True, header_style="bold cyan")
        table.add_column("Agent", style="dim white", width=22)
        table.add_column("Score", justify="right", width=8)
        table.add_column("État", width=24)

        for agent_id, label in AGENT_LABELS.items():
            score = scores.get(agent_id, 0.0)
            status = statuses.get(agent_id, "—")
            score_style = "green" if score >= 70 else ("yellow" if score >= 50 else "red")
            table.add_row(label, f"[{score_style}]{score:.0f}[/{score_style}]", status[:28])

        return table

    def _metrics_bar(state: dict[str, Any]) -> Table:
        """Build a compact metrics table."""
        table = Table(box=box.SIMPLE, expand=True, show_header=False)
        table.add_column("Metric", style="bold cyan", width=18)
        table.add_column("Value", style="white", width=16)
        table.add_column("Metric", style="bold cyan", width=18)
        table.add_column("Value", style="white", width=16)

        wr_str = f"{state.get('winrate', 0):.1f}%"
        ex_str = f"{state.get('expectancy_r', 0):+.2f}R"
        dd_str = f"{state.get('drawdown_pct', 0):.2f}%"
        pnl_str = f"${state.get('net_pnl', 0):+.2f}"

        table.add_row(
            "Winrate", f"[{'green' if state.get('winrate', 0) >= 50 else 'red'}]{wr_str}[/]",
            "Expectancy", f"[{'green' if state.get('expectancy_r', 0) >= 0 else 'red'}]{ex_str}[/]",
        )
        table.add_row(
            "Drawdown", f"[red]{dd_str}[/]",
            "Net P&L", f"[{'green' if state.get('net_pnl', 0) >= 0 else 'red'}]{pnl_str}[/]",
        )

        # Trade outcomes
        tp1 = state.get("tp1_count", 0)
        tp2 = state.get("tp2_count", 0)
        sl = state.get("full_sl_count", 0)
        psl = state.get("protected_sl_count", 0)
        table.add_row(
            f"TP1/TP2", f"[green]{tp1}[/] / [bold green]{tp2}[/]",
            "Full SL/Prot", f"[red]{sl}[/] / [yellow]{psl}[/]",
        )

        enter = state.get("decisions_enter", 0)
        wait = state.get("decisions_wait", 0)
        reject = state.get("decisions_reject", 0)
        table.add_row(
            f"ENTER", f"[green]{enter}[/]",
            f"WAIT/REJECT", f"[yellow]{wait}[/] / [red]{reject}[/]",
        )

        return table

    def _build_header(state: dict[str, Any]) -> str:
        equity = state.get("equity", 100.0)
        initial = state.get("equity_initial", 100.0)
        pct = (equity / initial * 100) - 100 if initial > 0 else 0
        color = "green" if pct >= 0 else "red"
        return (
            f"[bold]Equity:[/] [{color}]${equity:.2f}[/{color}] "
            f"([{color}]{pct:+.2f}%[/{color}])  "
            f"[dim]•[/]  "
            f"Candles: [bold]{state.get('candles_processed', 0):,}[/bold] / "
            f"{state.get('total_candles', 0):,}  "
            f"[dim]•[/]  "
            f"Speed: [bold]{state.get('candles_per_sec', 0):,.0f}[/] c/s  "
            f"[dim]•[/]  "
            f"Phase: [{ 'green' if state.get('phase') == 'evaluation' else 'dim yellow' }]"
            f"{state.get('phase', '—').upper()}[/]"
        )

    def build_live_layout(state: dict[str, Any]) -> Layout:
        """Build a rich Layout for the live replay display."""
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="body"),
        )
        layout["body"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=2),
        )
        layout["left"].split(
            Layout(name="agents", ratio=3),
            Layout(name="metrics", ratio=2),
        )
        layout["right"].split(
            Layout(name="progress", size=5),
            Layout(name="decision", size=7),
        )

        # Header
        header_text = _build_header(state)
        last_decision = state.get("last_decision", "—")
        dec_style = DECISION_COLORS.get(last_decision, "white")
        kasper_info = (
            f"  Kasper: [{dec_style}]{state.get('last_kasper_grade', '—')}[/{dec_style}] "
            f"-> [{dec_style}]{state.get('last_kasper_decision', '—')}[/{dec_style}]"
        )
        reason = state.get("last_decision_reason", "")
        if reason:
            reason = f"  Reason: [dim]{reason[:100]}[/dim]"

        header_content = Text.from_markup(
            header_text + "\n"
            + f"Decision: [{dec_style}][bold]{last_decision}[/bold][/{dec_style}]"
            + kasper_info
            + reason
        )
        layout["header"].update(Panel(header_content, title="Gold Sniper Live Replay", border_style="cyan"))

        # Agents panel
        layout["agents"].update(
            Panel(
                _agent_status_bar(state.get("agent_scores", {}), state.get("agent_statuses", {})),
                title="Agent Workspace",
                border_style="blue",
            )
        )

        # Metrics panel
        layout["metrics"].update(
            Panel(_metrics_bar(state), title="Trades & Metrics", border_style="magenta")
        )

        # Progress bar
        pct = min(100.0, max(0.0, state.get("progress_pct", 0.0)))
        bar_width = 40
        filled = int(bar_width * pct / 100)
        bar = "█" * filled + "░" * (bar_width - filled)
        candle_ts = state.get("current_candle_utc", "—")
        elapsed = state.get("elapsed_sec", 0)
        elapsed_str = f"{int(elapsed // 60):02d}:{int(elapsed % 60):02d}"

        progress_text = Text.from_markup(
            f"[bold]Progress:[/] {pct:.1f}%  [{elapsed_str}]\n"
            f"[{'green' if pct > 0 else 'dim'}]{bar}[/]\n"
            f"Candle: [cyan]{candle_ts}[/]  "
            f"Trades open: [bold]{state.get('trades_open', 0)}[/]"
        )
        layout["progress"].update(Panel(progress_text, title="Market Clock", border_style="green"))

        # Decision detail
        decision_details = [
            f"Last decision:  [{DECISION_COLORS.get(state.get('last_decision', '—'), 'white')}]"
            f"{state.get('last_decision', '—')}[/]",
            f"Kasper grade:   {state.get('last_kasper_grade', '—')}",
            f"Kasper says:    {state.get('last_kasper_decision', '—')}",
        ]
        reason = state.get("last_decision_reason", "")
        if reason:
            decision_details.append(f"\n[dim]{reason[:150]}[/dim]")

        layout["decision"].update(
            Panel(Text.from_markup("\n".join(decision_details)), title="Orchestrator / PDE", border_style="yellow")
        )

        return layout


def simple_status_line(state: dict[str, Any]) -> str:
    """Plain-text status line for non-rich terminals."""
    pct = state.get("progress_pct", 0)
    bar_width = 30
    filled = int(bar_width * pct / 100)
    bar = "#" * filled + "-" * (bar_width - filled)

    return (
        f"\r[{bar}] {pct:5.1f}% | "
        f"Equity: ${state.get('equity', 100):.2f} | "
        f"Decision: {state.get('last_decision', '—'):20s} | "
        f"Grade: {state.get('last_kasper_grade', '—'):6s} | "
        f"WR: {state.get('winrate', 0):.1f}% | "
        f"E[R]: {state.get('expectancy_r', 0):+.2f} | "
        f"Candles: {state.get('candles_processed', 0):,d} "
        f"({state.get('candles_per_sec', 0):.0f}/s)"
    )

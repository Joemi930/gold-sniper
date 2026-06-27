#!/usr/bin/env python3
"""
Gold Sniper Replay Control Center — V3.2 Trading & Optimisation
================================================================

Terminal interactive replay application for XAUUSD trading strategy
optimization.  Arrow-key menu, live replay display, compact reports.

Usage:
    python -m gold_sniper.replay_app.Gold_Sniper_Replay
    python gold_sniper/replay_app/Gold_Sniper_Replay.py

P1-clean: offline replay only.  No broker writes, no live trading,
no future leakage.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Project root setup ───────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parents[1]  # gold_sniper/
_REPO_ROOT = _PROJECT_ROOT.parent  # repo root
for p in (str(_PROJECT_ROOT), str(_REPO_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("GOLD_SNIPER_SKIP_DOTENV", "1")

from replay_app import __version__
from replay_app.display import (
    has_rich,
    build_live_layout,
    simple_status_line,
    AGENT_LABELS,
)

# ── Rich detection ───────────────────────────────────────────────────────────
_HAS_RICH = has_rich()

if _HAS_RICH:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    from rich import box

    _console = Console()
else:
    _console = None

# ── Default paths ────────────────────────────────────────────────────────────
DEFAULT_DATA_ROOT = _PROJECT_ROOT / "data" / "historical" / "XAUUSD"
DEFAULT_OUTPUT_ROOT = _PROJECT_ROOT / "data" / "replay_runs"
DEFAULT_NEWS_CALENDAR = (
    _PROJECT_ROOT / "data" / "historical" / "news" / "calendar_events_20251231_20260619.jsonl"
)
REPORTS_DIR = _REPO_ROOT / "reports" / "replay"
TMP_ROOT = _REPO_ROOT / ".tmp" / "replay_runs"


# ═══════════════════════════════════════════════════════════════════════════════
# Data check
# ═══════════════════════════════════════════════════════════════════════════════

def _check_prerequisites() -> dict[str, Any]:
    """Quick check of what's available before showing the menu."""
    from replay_app.data_prep import check_data_availability

    status: dict[str, Any] = {
        "data": check_data_availability(DEFAULT_DATA_ROOT),
        "news": DEFAULT_NEWS_CALENDAR.exists(),
    }

    data_status = status["data"]["overall_status"]
    if data_status == "COVERAGE_OK":
        status["ready"] = True
        status["message"] = "Data and news ready. All replay modes available."
    elif data_status == "PARTIAL":
        status["ready"] = True
        status["message"] = (
            f"Partial data — missing: {status['data']['missing_timeframes']}. "
            "Will attempt aggregation from M1."
        )
    else:
        status["ready"] = False
        status["message"] = (
            "No data found. Generate synthetic test data (menu option 0) "
            "or import from MT5 first."
        )

    return status


# ═══════════════════════════════════════════════════════════════════════════════
# Menu system
# ═══════════════════════════════════════════════════════════════════════════════

MENU_OPTIONS = [
    ("0", "Generate synthetic test data (quick test)"),
    ("1", "Replay 1 week smoke     (2026-01-01 -> 2026-01-08)"),
    ("2", "Replay 1 month           (2026-01-01 -> 2026-02-01)"),
    ("3", "Replay 2 months          (2026-01-01 -> 2026-03-01)"),
    ("4", "Replay 3 months          (2026-01-01 -> 2026-04-01)"),
    ("5", "Replay 6 months          (2026-01-01 -> 2026-06-01)"),
    ("6", "Replay custom"),
    ("7", "View reports"),
    ("8", "Clean temporary logs"),
    ("9", "Advanced options"),
    ("Q", "Quit"),
]

REPLAY_PRESETS = {
    "1": {"start": "2026-01-01", "end": "2026-01-08", "warmup_start": "2025-12-01", "label": "1-week smoke"},
    "2": {"start": "2026-01-01", "end": "2026-02-01", "warmup_start": "2025-12-01", "label": "1-month"},
    "3": {"start": "2026-01-01", "end": "2026-03-01", "warmup_start": "2025-12-01", "label": "2-month"},
    "4": {"start": "2026-01-01", "end": "2026-04-01", "warmup_start": "2025-12-01", "label": "3-month"},
    "5": {"start": "2026-01-01", "end": "2026-06-01", "warmup_start": "2025-12-01", "label": "6-month"},
}


def _print_banner() -> None:
    """Print the welcome banner."""
    banner = r"""
╔══════════════════════════════════════════════════════════════╗
║                  GOLD SNIPER REPLAY CENTER                  ║
║                 V3.2 — Trading & Optimisation               ║
╠══════════════════════════════════════════════════════════════╣
║ Capital initial : 100.00 USD                                ║
║ Data range      : 2025-12-01 -> 2026-06-01                   ║
║ Symbol          : XAUUSD                                    ║
║ Mode            : Offline replay / no broker / no future     ║
╚══════════════════════════════════════════════════════════════╝
"""
    print(banner)


def _print_menu(selected: int = 0) -> None:
    """Print menu with highlighted selection."""
    _print_banner()
    for i, (key, label) in enumerate(MENU_OPTIONS):
        prefix = " >" if i == selected else "  "
        suffix = " <" if i == selected else ""
        style = "bold white" if i == selected else "dim white"
        if _HAS_RICH:
            from rich.markup import escape
            print(f"{prefix}[{style}]{key}. {label}[/{style}]{suffix}")
        else:
            print(f"{prefix}{key}. {label}{suffix}")
    print("\n↑/↓ select | Enter launch | Esc quit")


if _HAS_RICH:
    def _menu_header(selected: int) -> Table:
        """Build a rich table for the menu."""
        table = Table(box=box.SIMPLE, expand=True, show_header=False)
        table.add_column("", width=3, justify="right")
        table.add_column("", width=50)
        for i, (key, label) in enumerate(MENU_OPTIONS):
            if i == selected:
                table.add_row(f"[bold cyan]▶ {key}[/]", f"[bold cyan]{label}[/]")
            else:
                table.add_row(f"[dim]{key}[/]", f"[dim]{label}[/]")
        return table

    def _build_menu_panel(selected: int, status_msg: str) -> Panel:
        header = Text.from_markup(
            "[bold]GOLD SNIPER REPLAY CENTER[/]  "
            "[dim]V3.2 — Trading & Optimisation[/]\n"
            "Capital: [bold cyan]$100.00 USD[/] | "
            "Symbol: [bold]XAUUSD[/] | "
            "Mode: [dim]Offline replay / no broker[/]"
        )
        body = _menu_header(selected)
        footer = Text.from_markup(f"\n[dim]{status_msg}[/]\n[yellow]↑/↓[/] select  "
                                   "[green]Enter[/] launch  [red]Esc[/] quit")
        return Panel(
            header + Text("\n") + Table(box=box.SIMPLE, show_header=False)  + footer,
            border_style="cyan",
        )
        # Simplified:
        content = Text()
        content.append(header)
        content.append("\n\n")
        content.append(_menu_header(selected))
        content.append("\n")
        content.append(footer)
        return Panel(content, border_style="cyan")


# ═══════════════════════════════════════════════════════════════════════════════
# Replay launch
# ═══════════════════════════════════════════════════════════════════════════════

def _run_replay_interactive(
    run_id: str,
    start: str,
    end: str,
    warmup_start: str | None = None,
    initial_equity: float = 100.0,
    agent_ids: list[str] | None = None,
    profile: bool = False,
    fast_replay: bool = False,
    minimal_events: bool = False,
    event_buffer_size: int = 1000,
    no_tui: bool = False,
) -> int:
    """Launch a replay with live TUI display. Returns 0 on success, 1 on error."""
    from replay_app.live_runner import run_replay_in_thread
    from replay_app.report_writer import write_compact_report, extract_important_trades, build_optimization_findings, cleanup_temp_logs

    state_queue: queue.Queue[Any] = queue.Queue(maxsize=500)
    stop_event = threading.Event()

    label = f"{start} -> {end}"
    print(f"\nStarting replay: {label}")
    print(f"Run ID: {run_id}")
    print(f"Initial equity: ${initial_equity:.2f}")
    if fast_replay:
        print("Mode: FAST REPLAY (warmup context-only, minimal events, buffered writes)")
    if profile:
        print("Profiling: ENABLED")
    print("Press Esc to stop replay gracefully.\n")

    # Launch replay in background thread
    thread = run_replay_in_thread(
        run_id=run_id,
        start=start,
        end=end,
        warmup_start=warmup_start,
        data_root=DEFAULT_DATA_ROOT,
        output_root=DEFAULT_OUTPUT_ROOT,
        news_calendar_path=DEFAULT_NEWS_CALENDAR,
        agent_ids=agent_ids,
        initial_equity=initial_equity,
        profile=profile,
        fast_replay=fast_replay,
        minimal_events=minimal_events,
        event_buffer_size=event_buffer_size,
        no_tui=no_tui,
        state_queue=state_queue,
        stop_event=stop_event,
    )

    # ── TUI display loop ─────────────────────────────────────────────────
    complete_data: dict[str, Any] | None = None
    last_state: dict[str, Any] = {
        "equity": initial_equity,
        "equity_initial": initial_equity,
        "progress_pct": 0.0,
        "candles_processed": 0,
        "total_candles": 1,
        "candles_per_sec": 0.0,
        "agent_scores": {},
        "agent_statuses": {},
        "last_decision": "—",
        "last_decision_reason": "",
        "last_kasper_grade": "—",
        "last_kasper_decision": "—",
        "trades_open": 0,
        "tp1_count": 0,
        "tp2_count": 0,
        "full_sl_count": 0,
        "protected_sl_count": 0,
        "winrate": 0.0,
        "expectancy_r": 0.0,
        "drawdown_pct": 0.0,
        "net_pnl": 0.0,
        "decisions_enter": 0,
        "decisions_wait": 0,
        "decisions_reject": 0,
        "phase": "initializing",
        "elapsed_sec": 0.0,
    }

    try:
        if _HAS_RICH:
            live = Live(
                build_live_layout(last_state),
                console=_console,
                refresh_per_second=4,
                screen=True,
            )
            live.start()
            try:
                while thread.is_alive() or not state_queue.empty():
                    try:
                        item = state_queue.get(timeout=0.25)
                        if isinstance(item, dict):
                            if item.get("type") == "complete":
                                complete_data = item
                                last_state["phase"] = "complete"
                                last_state["progress_pct"] = 100.0
                                last_state["running"] = False
                            elif item.get("type") == "error":
                                print(f"\n[ERROR] {item.get('message', 'Unknown error')}")
                                complete_data = item
                                break
                            elif item.get("type") == "interrupted":
                                print(f"\n[STOPPED] {item.get('message', 'Replay interrupted')}")
                                complete_data = item
                                break
                            else:
                                last_state = item
                        live.update(build_live_layout(last_state))
                    except queue.Empty:
                        if not thread.is_alive():
                            break
                        live.update(build_live_layout(last_state))
            except KeyboardInterrupt:
                stop_event.set()
                print("\nInterrupted — stopping replay...")
                thread.join(timeout=5.0)
            finally:
                live.stop()
        else:
            # Simple text-based display
            print("Running replay (simple mode)...")
            while thread.is_alive() or not state_queue.empty():
                try:
                    item = state_queue.get(timeout=0.5)
                    if isinstance(item, dict):
                        if item.get("type") == "complete":
                            complete_data = item
                            break
                        elif item.get("type") == "error":
                            print(f"\n[ERROR] {item.get('message', '')}")
                            complete_data = item
                            break
                        elif item.get("type") == "interrupted":
                            print(f"\n[STOPPED] {item.get('message', '')}")
                            complete_data = item
                            break
                        else:
                            last_state = item
                            print(simple_status_line(last_state), end="", flush=True)
                except queue.Empty:
                    if not thread.is_alive():
                        break
            print()  # final newline

    except Exception as exc:
        stop_event.set()
        print(f"\nFatal error: {exc}")
        return 1
    finally:
        stop_event.set()
        if thread.is_alive():
            thread.join(timeout=10.0)

    # ── Report generation ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("REPLAY COMPLETE")
    print("=" * 60)

    if complete_data and complete_data.get("type") == "complete" or complete_data is None:
        # Read summary directly from the output file (most reliable)
        output_root = complete_data.get("output_root", str(DEFAULT_OUTPUT_ROOT)) if complete_data else str(DEFAULT_OUTPUT_ROOT)
        run_dir = complete_data.get("run_dir", str(DEFAULT_OUTPUT_ROOT / run_id)) if complete_data else str(DEFAULT_OUTPUT_ROOT / run_id)

        # Try to load summary from the replay output file
        summary_path = Path(run_dir) / "summary.json"
        summary = {}
        if summary_path.exists():
            try:
                import json as _json
                summary = _json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if not summary and complete_data:
            summary = complete_data.get("summary", {})

        # Extract trades
        trades = extract_important_trades(summary, run_dir=run_dir)

        # Build optimization findings
        opt_findings = build_optimization_findings(summary)

        # Write compact report
        report_dir = REPORTS_DIR / run_id
        report_path = write_compact_report(
            summary, run_id, report_dir,
            trades=trades, optimization_findings=opt_findings,
            run_dir=run_dir,
        )

        # Print summary
        wr = summary.get("win_rate", summary.get("winrate", summary.get("win_rate_pct", 0)))
        ex = summary.get("expectancy_R", 0)
        trades_count = summary.get("parent_trades", summary.get("trades", summary.get("closed_trades", len(trades))))

        print(f"Run ID:       {run_id}")
        print(f"Trades:       {trades_count}")
        print(f"Winrate:      {float(wr):.1f}%")
        print(f"Expectancy:   {float(ex):+.2f}R")
        print(f"Report:       {report_path}")
        print(f"Run data:     {run_dir}")
        print()

        # Clean up temporary logs
        cleaned = cleanup_temp_logs(run_id)
        if cleaned:
            print(f"Temporary logs cleaned ({cleaned} dirs removed).")

        return 0
    elif complete_data and complete_data.get("type") == "error":
        print(f"Replay failed: {complete_data.get('message', 'Unknown error')}")
        return 1
    elif complete_data and complete_data.get("type") == "interrupted":
        print("Replay interrupted by user.")
        # Try to write partial report
        try:
            report_dir = REPORTS_DIR / run_id
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "PARTIAL.txt").write_text(
                f"Replay {run_id} interrupted at {datetime.now(timezone.utc).isoformat()}\n"
            )
            print(f"Partial report written to {report_dir / 'PARTIAL.txt'}")
        except Exception:
            pass
        return 0
    else:
        print("Replay finished but no summary data was returned.")
        return 1


# ═══════════════════════════════════════════════════════════════════════════════
# Advanced options (generated as JSON, used by --menu replay)
# ═══════════════════════════════════════════════════════════════════════════════

def _advanced_options_menu() -> dict[str, Any] | None:
    """Interactive advanced options prompt (rich-based)."""
    if not _HAS_RICH:
        print("\nAdvanced options (rich not available — using defaults):")
        print("  Agents: all 7")
        print("  Initial equity: $100.00")
        print("  Profiling: off")
        return {}

    try:
        from rich.prompt import Prompt, Confirm
    except ImportError:
        return {}

    print("\n[Advanced Options]")
    print("Press Enter to accept defaults.\n")

    agent_choices = Prompt.ask(
        "Agents (1-7, comma-separated, default=all)",
        default="1,2,3,4,5,6,7",
    )
    agent_ids = [f"agent_{a.strip()}" for a in agent_choices.split(",") if a.strip().isdigit()]

    equity_str = Prompt.ask("Initial equity (USD)", default="100.00")
    try:
        equity = float(equity_str)
    except ValueError:
        equity = 100.0

    do_profile = Confirm.ask("Enable profiling?", default=False)

    return {
        "agent_ids": agent_ids,
        "initial_equity": equity,
        "profile": do_profile,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# View reports
# ═══════════════════════════════════════════════════════════════════════════════

def _view_reports() -> None:
    """List existing reports."""
    reports_root = REPORTS_DIR
    if not reports_root.exists():
        print("\nNo reports directory found. Run a replay first.\n")
        return

    reports = sorted(reports_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        print("\nNo reports found. Run a replay first.\n")
        return

    print(f"\n{'='*60}")
    print(f"REPORTS ({len(reports)} found)")
    print(f"{'='*60}\n")

    for i, report_dir in enumerate(reports):
        if not report_dir.is_dir():
            continue
        report_md = report_dir / "REPORT.md"
        metrics_json = report_dir / "metrics.json"
        mtime = datetime.fromtimestamp(report_dir.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

        line = f"  [{i+1}] {report_dir.name} ({mtime})"
        if report_md.exists():
            line += "  [REPORT.md]"
        if metrics_json.exists():
            try:
                metrics = json.loads(metrics_json.read_text(encoding="utf-8"))
                line += (
                    f"  WR:{metrics.get('winrate_pct', 0):.1f}%"
                    f"  E[R]:{metrics.get('expectancy_R', 0):+.2f}"
                    f"  Trades:{metrics.get('total_trades', 0)}"
                )
            except Exception:
                pass
        print(line)
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# Cleanup
# ═══════════════════════════════════════════════════════════════════════════════

def _cleanup_temp_logs() -> None:
    """Remove all temporary replay logs."""
    from replay_app.report_writer import cleanup_temp_logs
    count = cleanup_temp_logs()
    print(f"\nCleaned {count} temporary log directories.\n")


# ═══════════════════════════════════════════════════════════════════════════════
# Menu loop (rich-based, arrow-key interactive)
# ═══════════════════════════════════════════════════════════════════════════════

if _HAS_RICH:

    def _interactive_menu_rich(status: dict[str, Any]) -> int:
        """Rich-based interactive menu with keyboard navigation.

        Returns exit code (0 = success, 1 = error).
        """
        from rich.live import Live

        selected = 1 if status.get("ready") else 0  # start on first replay option
        status_msg = status.get("message", "")

        def _render():
            header_lines = [
                "[bold cyan]╔══════════════════════════════════════════════════════════════╗[/]",
                "[bold cyan]║[/]                  [bold]GOLD SNIPER REPLAY CENTER[/]                  [bold cyan]║[/]",
                "[bold cyan]║[/]                 [dim]V3.2 — Trading & Optimisation[/]               [bold cyan]║[/]",
                "[bold cyan]╠══════════════════════════════════════════════════════════════╣[/]",
                f"[bold cyan]║[/] Capital initial : [cyan]$100.00 USD[/]                                [bold cyan]║[/]",
                f"[bold cyan]║[/] Data range      : 2025-12-01 -> 2026-06-01                   [bold cyan]║[/]",
                f"[bold cyan]║[/] Symbol          : XAUUSD                                    [bold cyan]║[/]",
                f"[bold cyan]║[/] Mode            : Offline replay / no broker / no future     [bold cyan]║[/]",
                "[bold cyan]╚══════════════════════════════════════════════════════════════╝[/]",
            ]
            menu_lines = []
            for i, (key, label) in enumerate(MENU_OPTIONS):
                if i == selected:
                    menu_lines.append(f"  [bold cyan]▶ {key}.[/] [bold cyan]{label}[/] [bold cyan]◀[/]")
                else:
                    menu_lines.append(f"   [dim]{key}.[/] [dim]{label}[/]")
            footer_lines = [
                "",
                f"  [dim]{status_msg}[/]",
                "",
                "  [yellow]↑/↓[/] select  [green]Enter[/] launch  [red]Esc[/] quit",
            ]
            return Panel(
                Text.from_markup("\n".join(header_lines + menu_lines + footer_lines)),
                border_style="cyan",
            )

        def _handle_input() -> str | None:
            """Get a single keypress. Returns 'up', 'down', 'enter', 'esc', or digit."""
            import msvcrt

            while True:
                if not msvcrt.kbhit():
                    time.sleep(0.05)
                    continue
                ch = msvcrt.getch()
                if ch == b'\xe0':  # special key prefix
                    ch2 = msvcrt.getch()
                    if ch2 == b'H':  # Up arrow
                        return 'up'
                    elif ch2 == b'P':  # Down arrow
                        return 'down'
                elif ch == b'\r':  # Enter
                    return 'enter'
                elif ch == b'\x1b':  # Esc
                    return 'esc'
                elif ch in (b'q', b'Q'):
                    return 'esc'
                elif ch.isdigit():
                    return ch.decode()
                time.sleep(0.01)
                return None

        live = Live(_render(), console=_console, refresh_per_second=10, screen=True)
        live.start()
        try:
            while True:
                key = _handle_input()
                if key == 'up':
                    selected = (selected - 1) % len(MENU_OPTIONS)
                    live.update(_render())
                elif key == 'down':
                    selected = (selected + 1) % len(MENU_OPTIONS)
                    live.update(_render())
                elif key == 'enter':
                    live.stop()
                    return _execute_menu_option(MENU_OPTIONS[selected][0])
                elif key == 'esc':
                    live.stop()
                    print("\nGoodbye.\n")
                    return 0
                elif key and key.isdigit() or (key and key.upper() == 'Q'):
                    live.stop()
                    return _execute_menu_option(key.upper() if key else 'Q')
                else:
                    live.update(_render())
        finally:
            if live.is_started:
                live.stop()
        return 0
else:
    def _interactive_menu_rich(status: dict[str, Any]) -> int:
        """Fallback menu when rich is not available."""
        return _interactive_menu_simple(status)


def _interactive_menu_simple(status: dict[str, Any]) -> int:
    """Simple text-based menu for terminals without rich."""
    _print_banner()
    print(f"  {status.get('message', '')}\n")
    for key, label in MENU_OPTIONS:
        print(f"  {key}. {label}")
    print()
    choice = input("Select option (0-9, Q=quit): ").strip().upper()
    return _execute_menu_option(choice if choice else 'Q')


def _execute_menu_option(choice: str) -> int:
    """Execute the selected menu option. Returns exit code."""
    choice = choice.upper()

    if choice == '0':
        # Generate synthetic data
        print("\n" + "="*60)
        print("GENERATING SYNTHETIC TEST DATA")
        print("="*60)
        print("This will create 6 months of synthetic XAUUSD candles for testing.")
        print("WARNING: Synthetic data is NOT valid for strategy validation!\n")
        confirm = input("Proceed? [y/N]: ").strip().lower()
        if confirm == 'y':
            from replay_app.data_prep import generate_synthetic_candles
            print("Generating... (this may take 1-2 minutes for 6 months of M1 candles)")
            result = generate_synthetic_candles(
                data_root=DEFAULT_DATA_ROOT,
                start_date="2025-12-01",
                end_date="2026-06-01",
            )
            for tf, info in result.get("timeframes", {}).items():
                print(f"  {tf}: {info.get('candles', 0):,} candles ({info.get('source', '?')})")
            print("\nSynthetic data ready. You can now run replays.\n")
        return 0

    elif choice in REPLAY_PRESETS:
        preset = REPLAY_PRESETS[choice]
        run_id = f"replay_{preset['label'].replace(' ', '_')}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        return _run_replay_interactive(
            run_id=run_id,
            start=preset["start"],
            end=preset["end"],
            warmup_start=preset["warmup_start"],
        )

    elif choice == '6':
        # Custom replay
        print("\n" + "="*60)
        print("CUSTOM REPLAY")
        print("="*60)
        start = input("Start date (YYYY-MM-DD) [2026-01-01]: ").strip() or "2026-01-01"
        end = input("End date (YYYY-MM-DD) [2026-02-01]: ").strip() or "2026-02-01"
        warmup = input("Warmup start (YYYY-MM-DD) [2025-12-01]: ").strip() or "2025-12-01"
        equity_str = input("Initial equity (USD) [100.00]: ").strip() or "100.00"
        try:
            equity = float(equity_str)
        except ValueError:
            equity = 100.0

        run_id = f"replay_custom_{start}_{end}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        return _run_replay_interactive(
            run_id=run_id,
            start=start,
            end=end,
            warmup_start=warmup,
            initial_equity=equity,
        )

    elif choice == '7':
        _view_reports()
        input("Press Enter to continue...")
        return 0

    elif choice == '8':
        _cleanup_temp_logs()
        input("Press Enter to continue...")
        return 0

    elif choice == '9':
        print("\nAdvanced options:")
        print("  --profile: Enable per-agent timing profiling")
        print("  --diagnose-agent2: Enable Agent2 POI diagnostics")
        print("  --diagnose-agent5: Enable Agent5 micro diagnostics")
        print("\nUse the command-line mode for advanced options:")
        print("  python -m gold_sniper.replay_app.Gold_Sniper_Replay --start ... --end ... --profile\n")
        input("Press Enter to continue...")
        return 0

    elif choice == 'Q':
        print("\nGoodbye.\n")
        return 0

    else:
        print(f"\nInvalid option: {choice}\n")
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# P4.2 — V2 engine + parity mode
# ═══════════════════════════════════════════════════════════════════════════════

def _run_replay_v2(
    run_id: str,
    start: str,
    end: str,
    warmup_start: str | None = None,
    initial_equity: float = 100.0,
    agent_ids: list[str] | None = None,
    profile: bool = False,
    fast_replay: bool = False,
    minimal_events: bool = False,
    event_buffer_size: int = 1000,
    no_tui: bool = False,
) -> int:
    """Run replay using ReplayEngineV2 (candidate-driven)."""
    import asyncio

    from core.blackboard import BlackBoard
    from replay.decision_pipeline import ReplayDecisionPipeline
    from replay.economic_calendar import load_calendar_result
    from replay.execution_model import build_default_execution_model
    from replay.historical_data import load_csv_candles, parse_timestamp
    from replay.replay_engine_v2 import ReplayEngineV2
    from replay.run_replay import _load_replay_timeframes, _resolve_boundaries
    from replay.simulated_trade_manager import SimulatedTradeConfig, SimulatedTradeManager
    from replay_app.report_writer import write_compact_report, extract_important_trades, build_optimization_findings

    label = f"{start} -> {end}"
    print(f"\n=== P4.2 ReplayEngineV2 ===")
    print(f"Period: {label}")
    print(f"Run ID: {run_id}")
    print(f"Engine: V2 (candidate-driven)")
    if fast_replay:
        print("Mode: FAST")
    if profile:
        print("Profiling: ENABLED (ProfilerV2)")

    data_root = DEFAULT_DATA_ROOT
    output_root = DEFAULT_OUTPUT_ROOT

    # Load data
    load_start = start if warmup_start else start
    boundaries = {
        "load_start": f"{load_start}T00:00:00Z" if len(load_start) == 10 else load_start,
        "eval_start": f"{start}T00:00:00Z" if len(start) == 10 else start,
        "eval_end": f"{end}T23:59:59Z" if len(end) == 10 else end,
    }
    if warmup_start:
        boundaries["load_start"] = f"{warmup_start}T00:00:00Z" if len(warmup_start) == 10 else warmup_start

    loaded, _, _, _ = _load_replay_timeframes(
        data_root,
        symbol="XAUUSD",
        start=boundaries["load_start"],
        end=boundaries["eval_end"],
    )

    if not loaded.get("1m"):
        print("ERROR: No 1m candles available for the requested period.")
        return 1

    m1_candles = loaded["1m"]
    print(f"Loaded {len(m1_candles):,} M1 candles.")

    # Build decision pipeline
    agent_list = agent_ids or ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"]
    calendar_result = load_calendar_result(DEFAULT_NEWS_CALENDAR, start=boundaries["load_start"], end=boundaries["eval_end"])
    decision_hook = ReplayDecisionPipeline.from_agent_ids(
        agent_list,
        use_orchestrator=False,
        news_events=calendar_result.events,
        news_feed_alive=not calendar_result.missing and not calendar_result.empty,
        news_source=calendar_result.source_format if not calendar_result.missing else "REPLAY_EMPTY",
    )

    blackboard = BlackBoard()
    execution_model = build_default_execution_model(initial_equity=initial_equity)
    trade_manager = SimulatedTradeManager(
        blackboard,
        SimulatedTradeConfig(
            equity_initial=initial_equity,
            execution_model=execution_model,
            require_execution_model=True,
        ),
    )

    engine = ReplayEngineV2(
        candles_1m=m1_candles,
        decision_pipeline=decision_hook,
        trade_manager=trade_manager,
        eval_start=boundaries["eval_start"],
        eval_end=boundaries["eval_end"],
        initial_equity=initial_equity,
        run_id=run_id,
        output_root=output_root,
    )

    summary = engine.run(blackboard=blackboard, profile=profile)

    # Write summary to file
    run_dir = output_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (run_dir / "summary_v2.json").write_text(
        _json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # Print summary
    print(f"\n=== ReplayEngineV2 Complete ===")
    state = summary.get("state", summary.get("engine", "OK"))
    print(f"State:        {state}")
    print(f"Runtime:      {summary.get('runtime_ms', 0):.0f}ms")
    print(f"Candles:      {summary.get('candles_total', 0):,}")
    print(f"Candidates:   {summary.get('candidate_count', 0)}")
    print(f"Windows:      {summary.get('window_count', 0)}")
    print(f"Trades:       {summary.get('trade_count', 0)}")
    if summary.get("winrate") is not None:
        print(f"Winrate:      {float(summary['winrate']) * 100:.1f}%")
        print(f"Expectancy:   {float(summary.get('expectancy_R', 0)):+.2f}R")
    print(f"Summary:      {run_dir / 'summary_v2.json'}")

    if profile and "profiler" in summary:
        pr = summary["profiler"]
        print(f"\nProfilerV2:")
        print(f"  Coverage:   {pr.get('coverage_pct', 0)}%")
        print(f"  Accounted:  {pr.get('accounted_ms', 0):.0f}ms")
        print(f"  Unaccounted:{pr.get('unaccounted_ms', 0):.0f}ms")

    return 0


def _run_parity_mode(
    run_id: str,
    start: str,
    end: str,
    warmup_start: str | None = None,
    initial_equity: float = 100.0,
    agent_ids: list[str] | None = None,
    profile: bool = False,
    fast_replay: bool = False,
    minimal_events: bool = False,
    event_buffer_size: int = 1000,
    no_tui: bool = False,
) -> int:
    """Run both legacy (full) and V2 (fast) engines, compare results.

    Parity mode validates that the V2 candidate-driven architecture
    produces identical decisions to the legacy full-scan approach.
    """
    import hashlib
    import json as _json

    print("\n" + "=" * 60)
    print("P4.2 PARITY MODE — Legacy Full vs V2 Fast")
    print("=" * 60)

    # ── Run V2 engine first (fast) ─────────────────────────────────
    print("\n[1/2] Running ReplayEngineV2 (fast, candidate-driven)...")
    v2_rc = _run_replay_v2(
        run_id=f"{run_id}_v2",
        start=start, end=end, warmup_start=warmup_start,
        initial_equity=initial_equity, agent_ids=agent_ids,
        profile=profile, fast_replay=True,
        minimal_events=minimal_events, event_buffer_size=event_buffer_size,
        no_tui=True,  # no TUI during parity
    )

    if v2_rc != 0:
        print("ERROR: V2 engine failed — parity aborted.")
        return 1

    # Read V2 summary
    v2_summary_path = DEFAULT_OUTPUT_ROOT / f"{run_id}_v2" / "summary_v2.json"
    v2_summary = {}
    if v2_summary_path.exists():
        v2_summary = _json.loads(v2_summary_path.read_text(encoding="utf-8"))

    # ── Run legacy engine (full) ───────────────────────────────────
    print("\n[2/2] Running legacy engine (full scan)...")
    legacy_rc = _run_replay_interactive(
        run_id=f"{run_id}_legacy",
        start=start, end=end, warmup_start=warmup_start,
        initial_equity=initial_equity, agent_ids=agent_ids,
        profile=False, fast_replay=False,
        minimal_events=False, event_buffer_size=event_buffer_size,
        no_tui=True,
    )

    if legacy_rc != 0:
        print("WARNING: Legacy engine returned non-zero exit code.")

    # Read legacy summary
    legacy_summary_path = DEFAULT_OUTPUT_ROOT / f"{run_id}_legacy" / "summary.json"
    legacy_summary = {}
    if legacy_summary_path.exists():
        legacy_summary = _json.loads(legacy_summary_path.read_text(encoding="utf-8"))

    # ── Compare ────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("PARITY COMPARISON")
    print("=" * 60)

    v2_trades = v2_summary.get("trade_count", 0)
    leg_trades = legacy_summary.get("parent_trades", legacy_summary.get("trade_count", 0))

    print(f"V2 trades:     {v2_trades}")
    print(f"Legacy trades: {leg_trades}")

    # Compute decision hashes for comparison
    v2_hash = hashlib.sha256(_json.dumps(v2_summary, sort_keys=True, default=str).encode()).hexdigest()[:16]
    leg_hash = hashlib.sha256(_json.dumps(legacy_summary, sort_keys=True, default=str).encode()).hexdigest()[:16]

    print(f"V2 hash:       {v2_hash}")
    print(f"Legacy hash:   {leg_hash}")

    # Runtime comparison
    v2_runtime = v2_summary.get("runtime_ms", 0)
    print(f"V2 runtime:    {v2_runtime:.0f}ms")

    if v2_trades == leg_trades:
        print("\n✅ PARITY: Trade count matches!")
    else:
        print(f"\n⚠️  PARITY MISMATCH: trade count differs ({v2_trades} vs {leg_trades})")

    # Write parity report
    parity_dir = DEFAULT_OUTPUT_ROOT / f"{run_id}_parity"
    parity_dir.mkdir(parents=True, exist_ok=True)
    parity_report = {
        "parity_mode": "P4.2",
        "period": f"{start} -> {end}",
        "v2_trades": v2_trades,
        "legacy_trades": leg_trades,
        "trade_count_match": v2_trades == leg_trades,
        "v2_hash": v2_hash,
        "legacy_hash": leg_hash,
        "v2_runtime_ms": v2_runtime,
    }
    (parity_dir / "parity_report.json").write_text(
        _json.dumps(parity_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Parity report: {parity_dir / 'parity_report.json'}")

    return 0 if v2_trades == leg_trades else 1


# ═══════════════════════════════════════════════════════════════════════════════
# CLI mode (for scripting / direct replay)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gold Sniper Replay Control Center — V3.2",
    )
    parser.add_argument("--menu", action="store_true", default=True,
                        help="Launch interactive menu (default)")
    parser.add_argument("--no-menu", action="store_true",
                        help="Skip menu, run replay directly (requires --start, --end)")
    parser.add_argument("--start", help="Replay start (YYYY-MM-DD)")
    parser.add_argument("--end", help="Replay end (YYYY-MM-DD)")
    parser.add_argument("--warmup-start", help="Warmup start (YYYY-MM-DD)")
    parser.add_argument("--run-id", help="Custom run ID")
    parser.add_argument("--initial-equity", type=float, default=100.0)
    parser.add_argument("--agents", default="1,2,3,4,5,6,7",
                        help="Comma-separated agent numbers")
    parser.add_argument("--profile", action="store_true",
                        help="Enable per-agent timing profiling")
    parser.add_argument("--profile-replay", action="store_true",
                        help="Alias for --profile")
    # ── P4.2: engine selection ─────────────────────────────────────────
    parser.add_argument("--engine", choices=("legacy", "v2"), default="legacy",
                        help="P4.2: Replay engine version (legacy|v2, default: legacy)")
    parser.add_argument("--parity", action="store_true",
                        help="P4.2: Run parity mode (full legacy vs fast v2 comparison, 1 day)")
    parser.add_argument("--fast", action="store_true",
                        help="P4.2: Alias for --fast-replay (fast mode)")
    # ── Legacy P4 flags (still supported) ──────────────────────────────
    parser.add_argument("--fast-replay", action="store_true",
                        help="P4: Fast replay — warmup context-only, minimal events, buffered writes")
    parser.add_argument("--minimal-events", action="store_true",
                        help="P4: Write only trade-lifecycle events")
    parser.add_argument("--event-buffer-size", type=int, default=1000,
                        help="P4: JSONL event buffer size (default 1000)")
    parser.add_argument("--no-tui", action="store_true",
                        help="P4: Disable TUI state updates entirely")
    parser.add_argument("--check-data", action="store_true",
                        help="Check data availability and exit")
    parser.add_argument("--generate-synthetic", action="store_true",
                        help="Generate synthetic test data and exit")
    parser.add_argument("--cleanup", action="store_true",
                        help="Clean temporary logs and exit")
    parser.add_argument("--version", action="version",
                        version=f"Gold Sniper Replay Center V{__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    # --check-data
    if args.check_data:
        from replay_app.data_prep import check_data_availability
        report = check_data_availability()
        print(json.dumps(report, indent=2, default=str))
        return 0

    # --generate-synthetic
    if args.generate_synthetic:
        from replay_app.data_prep import generate_synthetic_candles
        print("Generating synthetic XAUUSD data (6 months, all timeframes)...")
        result = generate_synthetic_candles()
        for tf, info in result.get("timeframes", {}).items():
            print(f"  {tf}: {info.get('candles', 0):,} candles")
        print("Done.")
        return 0

    # --cleanup
    if args.cleanup:
        _cleanup_temp_logs()
        return 0

    # --no-menu (direct replay from CLI)
    if args.no_menu:
        if not args.start or not args.end:
            parser.error("--start and --end are required with --no-menu")

        # ── P4.2: resolve engine ──────────────────────────────────────
        engine = getattr(args, 'engine', 'legacy') or 'legacy'
        parity = getattr(args, 'parity', False)
        fast_mode = getattr(args, 'fast', False) or args.fast_replay

        run_id = args.run_id or f"replay_cli_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        agent_ids = [f"agent_{a.strip()}" for a in args.agents.split(",") if a.strip().isdigit()]
        profile = args.profile or getattr(args, 'profile_replay', False)

        # ── P4.2: parity mode — run both engines and compare ──────────
        if parity:
            return _run_parity_mode(
                run_id=run_id,
                start=args.start,
                end=args.end,
                warmup_start=args.warmup_start,
                initial_equity=args.initial_equity,
                agent_ids=agent_ids or None,
                profile=profile,
                fast_replay=fast_mode,
                minimal_events=args.minimal_events,
                event_buffer_size=args.event_buffer_size,
                no_tui=args.no_tui,
            )

        # ── P4.2: V2 engine path ──────────────────────────────────────
        if engine == 'v2':
            return _run_replay_v2(
                run_id=run_id,
                start=args.start,
                end=args.end,
                warmup_start=args.warmup_start,
                initial_equity=args.initial_equity,
                agent_ids=agent_ids or None,
                profile=profile,
                fast_replay=fast_mode,
                minimal_events=args.minimal_events,
                event_buffer_size=args.event_buffer_size,
                no_tui=args.no_tui,
            )

        return _run_replay_interactive(
            run_id=run_id,
            start=args.start,
            end=args.end,
            warmup_start=args.warmup_start,
            initial_equity=args.initial_equity,
            agent_ids=agent_ids or None,
            profile=profile,
            fast_replay=fast_mode,
            minimal_events=args.minimal_events,
            event_buffer_size=args.event_buffer_size,
            no_tui=args.no_tui,
        )

    # Default: interactive menu
    status = _check_prerequisites()
    if _HAS_RICH:
        return _interactive_menu_rich(status)
    else:
        # Simple menu loop
        while True:
            _interactive_menu_simple(status)
            # _interactive_menu_simple calls _execute_menu_option internally
            # and handles the result.  For the loop, we just continue.
            # Actually, _interactive_menu_simple calls _execute_menu_option,
            # which runs the replay.  After replay, we loop back.
            pass


if __name__ == "__main__":
    sys.exit(main())

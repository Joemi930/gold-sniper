"""Generation des fichiers d'analyse quotidiens : summary.json + report.md.

Ces deux fichiers sont la sortie officielle transmise pour les analyses
quotidiennes/hebdomadaires (!logs) et archivee sur Google Drive (dossiers
mensuels). Aucune donnee sensible (login/serveur MT5, tokens) n'y figure.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT_DIR / "logs" / "reports"
LOCAL_TZ = ZoneInfo("Africa/Kinshasa")


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _trade_view(trade: dict[str, Any]) -> dict[str, Any]:
    """Vue nettoyee d'un trade (pas de ticket broker ni de compte)."""
    return {
        "direction": trade.get("direction") or trade.get("type"),
        "entry": _safe_float(trade.get("entry_price") or trade.get("entry")),
        "sl": _safe_float(trade.get("sl")),
        "tp1": _safe_float(trade.get("tp1") or trade.get("tp")),
        "tp2": _safe_float(trade.get("tp2")),
        "lots": _safe_float(trade.get("lots") or trade.get("volume")),
        "pnl": _safe_float(trade.get("pnl")),
        "grade": trade.get("grade"),
        "scenario": trade.get("scenario") or trade.get("strategy"),
        "state": trade.get("state") or trade.get("management_state"),
        "opened_at": str(trade.get("opened_at") or trade.get("open_time") or ""),
        "closed_at": str(trade.get("closed_at") or trade.get("close_time") or ""),
    }


def build_summary_dict(blackboard, now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    data = blackboard.get_all()
    daily = data.get("daily_stats", {}) or {}
    meta = data.get("meta", {}) or {}
    market = data.get("market", {}) or {}
    orch = data.get("orchestrator", {}) or {}
    control = data.get("control", {}) or {}
    active = data.get("active_trades", {}) or {}
    closed = (data.get("positions", {}) or {}).get("closed_today", []) or []
    account = meta.get("account_info") or {}

    realized = _safe_float(daily.get("realized_pnl"))
    floating = _safe_float(daily.get("floating_pnl"))
    wins = sum(1 for t in closed if _safe_float(t.get("pnl")) > 0)
    losses = sum(1 for t in closed if _safe_float(t.get("pnl")) < 0)

    return {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
        "mode": "PAPER",
        "engine": {
            "session": market.get("session", "UNKNOWN"),
            "regime": market.get("regime", "UNKNOWN"),
            "strategy": orch.get("strategy", "N/A"),
            "last_decision": orch.get("decision", "N/A"),
            "last_score": _safe_float(orch.get("score")),
            "paused": bool(control.get("paused")),
            "network_online": bool(meta.get("network_online", True)),
        },
        "account": {
            "balance": _safe_float(account.get("balance")),
            "equity": _safe_float(account.get("equity")),
            "margin_free": _safe_float(account.get("margin_free")),
            "currency": account.get("currency", "USD"),
        },
        "trading": {
            "trades_opened_today": int(meta.get("daily_trade_count", 0) or 0),
            "active_positions": len(active),
            "trades_closed_today": len(closed),
            "wins": wins,
            "losses": losses,
            "winrate_pct": round(wins / len(closed) * 100.0, 1) if closed else 0.0,
            "realized_pnl": round(realized, 2),
            "floating_pnl": round(floating, 2),
            "total_pnl": round(realized + floating, 2),
        },
        "active_trades": [_trade_view(t) for t in active.values()],
        "closed_trades": [_trade_view(t) for t in closed],
        "pending_setup": (orch.get("pending_setup") or None),
    }


def build_report_md(summary: dict[str, Any]) -> str:
    eng = summary["engine"]
    acc = summary["account"]
    trd = summary["trading"]
    lines = [
        f"# Rapport journalier Gold Sniper — {summary['date']}",
        "",
        f"Genere le {summary['generated_at']} (mode {summary['mode']}).",
        "",
        "## Moteur",
        f"- Session : {eng['session']} | Regime : {eng['regime']}",
        f"- Strategie : {eng['strategy']} | Derniere decision : {eng['last_decision']}"
        f" (score {eng['last_score']})",
        f"- Pause : {'oui' if eng['paused'] else 'non'}"
        f" | Reseau : {'en ligne' if eng['network_online'] else 'HORS LIGNE'}",
        "",
        "## Compte (demo)",
        f"- Balance : {acc['balance']:.2f} {acc['currency']}"
        f" | Equity : {acc['equity']:.2f} | Marge libre : {acc['margin_free']:.2f}",
        "",
        "## Trading du jour",
        f"- Trades ouverts : {trd['trades_opened_today']}"
        f" | Positions actives : {trd['active_positions']}",
        f"- Trades fermes : {trd['trades_closed_today']}"
        f" (W {trd['wins']} / L {trd['losses']} — WR {trd['winrate_pct']}%)",
        f"- PnL realise : {trd['realized_pnl']:+.2f}"
        f" | flottant : {trd['floating_pnl']:+.2f}"
        f" | total : {trd['total_pnl']:+.2f}",
    ]
    pending = summary.get("pending_setup")
    if pending:
        lines += [
            "",
            "## Setup surveille",
            f"- {pending.get('direction', '?')} @ {pending.get('entry', '?')}"
            f" (grade {pending.get('grade', '?')})"
            f" — TP1 {pending.get('tp1', '?')} / TP2 {pending.get('tp2', '?')}"
            f" / SL {pending.get('sl', '?')}",
        ]
    active = summary.get("active_trades") or []
    if active:
        lines += ["", "## Positions actives"]
        for t in active:
            lines.append(
                f"- {t['direction']} @ {t['entry']} | SL {t['sl']}"
                f" | TP1 {t['tp1']} | TP2 {t['tp2']} | PnL {t['pnl']:+.2f}"
                f" | etat {t['state']}"
            )
    closed = summary.get("closed_trades") or []
    if closed:
        lines += ["", "## Trades fermes du jour"]
        for t in closed:
            lines.append(
                f"- {t['direction']} @ {t['entry']} -> PnL {t['pnl']:+.2f}"
                f" ({t['scenario']})"
            )
    lines.append("")
    return "\n".join(lines)


def write_daily_files(blackboard, now: datetime | None = None) -> tuple[Path, Path]:
    """Ecrit YYYY-MM-DD_summary.json + YYYY-MM-DD_report.md dans logs/reports.

    Ecrit aussi les copies stables summary.json / report.md (derniere version),
    utilisees par la commande Discord !logs.
    """
    now = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    summary = build_summary_dict(blackboard, now)
    report_md = build_report_md(summary)
    stamp = now.strftime("%Y-%m-%d")

    dated_json = REPORTS_DIR / f"{stamp}_summary.json"
    dated_md = REPORTS_DIR / f"{stamp}_report.md"
    payload = json.dumps(summary, indent=2, ensure_ascii=False)
    dated_json.write_text(payload, encoding="utf-8")
    dated_md.write_text(report_md, encoding="utf-8")
    (REPORTS_DIR / "summary.json").write_text(payload, encoding="utf-8")
    (REPORTS_DIR / "report.md").write_text(report_md, encoding="utf-8")
    return dated_json, dated_md

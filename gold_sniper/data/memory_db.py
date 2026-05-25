import asyncio
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from utils.logger import get_logger
from utils.telegram_notifier import send_telegram_notification


DB_PATH = Path("data/memory.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    session TEXT,
    regime TEXT,
    strategy_used TEXT,
    ob_score REAL,
    sweep_confirmed INTEGER,
    amd_phase INTEGER,
    fib_precision REAL,
    direction TEXT,
    entry_price REAL,
    sl REAL,
    tp1 REAL,
    result TEXT,
    pnl_pct REAL,
    rr_achieved REAL,
    error_pattern TEXT
);

CREATE TABLE IF NOT EXISTS agent_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    signal_score REAL,
    signal_direction TEXT,
    trade_result TEXT,
    was_correct INTEGER,
    regime TEXT,
    session TEXT
);

CREATE TABLE IF NOT EXISTS error_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    pattern_type TEXT NOT NULL UNIQUE,
    description TEXT,
    frequency INTEGER DEFAULT 1,
    last_seen TEXT,
    flagged_for_review INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS strategy_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT NOT NULL,
    session TEXT,
    regime TEXT,
    trades_count INTEGER DEFAULT 0,
    win_count INTEGER DEFAULT 0,
    avg_rr REAL DEFAULT 0.0,
    last_updated TEXT,
    UNIQUE(strategy_name, session, regime)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_error_patterns_pattern_type
ON error_patterns(pattern_type);
"""


class MemoryDB:
    """SQLite long-term memory for closed trades and learning patterns."""

    def __init__(self, path: str | Path = DB_PATH) -> None:
        self.path = Path(path)

    async def init(self) -> None:
        await asyncio.to_thread(self._init_sync)

    async def close(self) -> None:
        return None

    async def record_closed_trade(self, trade: dict[str, Any]) -> int:
        normalized = self._normalize_trade(trade)
        return await asyncio.to_thread(self._record_closed_trade_sync, normalized)

    async def analyze_losing_trades(self, last_n: int = 5) -> dict[str, Any]:
        return await asyncio.to_thread(self._analyze_losing_trades_sync, last_n)

    async def count_losses(self) -> int:
        return await asyncio.to_thread(self._scalar_sync, "SELECT COUNT(*) FROM trade_patterns WHERE result='LOSS'")

    async def table_names(self) -> list[str]:
        def work() -> list[str]:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            return [row[0] for row in rows]

        return await asyncio.to_thread(work)

    async def latest_trade_pattern(self) -> dict[str, Any] | None:
        def work() -> dict[str, Any] | None:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM trade_patterns ORDER BY id DESC LIMIT 1").fetchone()
            return dict(row) if row else None

        return await asyncio.to_thread(work)

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_sync(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _record_closed_trade_sync(self, trade: dict[str, Any]) -> int:
        self._init_sync()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trade_patterns
                (timestamp, session, regime, strategy_used, ob_score, sweep_confirmed,
                 amd_phase, fib_precision, direction, entry_price, sl, tp1, result,
                 pnl_pct, rr_achieved, error_pattern)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    trade["timestamp"],
                    trade["session"],
                    trade["regime"],
                    trade["strategy"],
                    trade["ob_score"],
                    int(bool(trade["sweep_confirmed"])) if trade["sweep_confirmed"] is not None else None,
                    trade["amd_phase"],
                    trade["fib_precision"],
                    trade["direction"],
                    trade["entry"],
                    trade["sl"],
                    trade["tp1"],
                    trade["result"],
                    trade["pnl_pct"],
                    trade["rr_achieved"],
                    json.dumps(trade["error_patterns"]),
                ),
            )
            trade_id = int(cursor.lastrowid)

            for agent_id, agent in sorted((trade.get("agent_breakdown") or {}).items()):
                conn.execute(
                    """
                    INSERT INTO agent_performance
                    (timestamp, agent_id, signal_score, signal_direction, trade_result,
                     was_correct, regime, session)
                    VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        trade["timestamp"],
                        str(agent_id),
                        _to_float(agent.get("score")),
                        agent.get("direction") or agent.get("bias") or trade["direction"],
                        trade["result"],
                        self._agent_was_correct(agent, trade),
                        trade["regime"],
                        trade["session"],
                    ),
                )

            self._update_strategy_performance(conn, trade)

            for pattern in trade["error_patterns"]:
                self._record_error_pattern_sync(conn, pattern["type"], pattern["description"], trade["timestamp"])

            conn.commit()
        return trade_id

    def _update_strategy_performance(self, conn: sqlite3.Connection, trade: dict[str, Any]) -> None:
        strategy = trade["strategy"] or "UNKNOWN"
        session = trade["session"] or "UNKNOWN"
        regime = trade["regime"] or "UNKNOWN"
        existing = conn.execute(
            """
            SELECT trades_count, win_count, avg_rr
            FROM strategy_performance
            WHERE strategy_name=? AND session=? AND regime=?
            """,
            (strategy, session, regime),
        ).fetchone()
        rr = _to_float(trade["rr_achieved"]) or 0.0
        is_win = 1 if trade["result"] == "WIN" else 0
        if existing:
            trades_count = int(existing[0]) + 1
            win_count = int(existing[1]) + is_win
            avg_rr = ((float(existing[2] or 0.0) * int(existing[0])) + rr) / trades_count
            conn.execute(
                """
                UPDATE strategy_performance
                SET trades_count=?, win_count=?, avg_rr=?, last_updated=?
                WHERE strategy_name=? AND session=? AND regime=?
                """,
                (trades_count, win_count, avg_rr, trade["timestamp"], strategy, session, regime),
            )
        else:
            conn.execute(
                """
                INSERT INTO strategy_performance
                (strategy_name, session, regime, trades_count, win_count, avg_rr, last_updated)
                VALUES (?,?,?,?,?,?,?)
                """,
                (strategy, session, regime, 1, is_win, rr, trade["timestamp"]),
            )

    def _record_error_pattern_sync(
        self,
        conn: sqlite3.Connection,
        pattern_type: str,
        description: str,
        timestamp: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO error_patterns (detected_at, pattern_type, description, last_seen)
            VALUES (?,?,?,?)
            ON CONFLICT(pattern_type) DO UPDATE SET
                frequency = frequency + 1,
                description = excluded.description,
                last_seen = excluded.last_seen
            """,
            (timestamp, pattern_type, description, timestamp),
        )

    def _analyze_losing_trades_sync(self, last_n: int) -> dict[str, Any]:
        self._init_sync()
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT session, regime, strategy_used, ob_score, sweep_confirmed,
                       amd_phase, fib_precision, error_pattern
                FROM trade_patterns
                WHERE result='LOSS'
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (last_n,),
            ).fetchall()

        if len(rows) < last_n:
            return {"insufficient_data": True, "trades_analyzed": len(rows)}

        sessions = [row["session"] or "UNKNOWN" for row in rows]
        regimes = [row["regime"] or "UNKNOWN" for row in rows]
        strategies = [row["strategy_used"] or "UNKNOWN" for row in rows]
        ob_scores = [float(row["ob_score"]) for row in rows if row["ob_score"] is not None]
        sweeps = [int(row["sweep_confirmed"] or 0) for row in rows]
        error_types: list[str] = []
        for row in rows:
            try:
                error_types.extend(item.get("type", "UNKNOWN") for item in json.loads(row["error_pattern"] or "[]"))
            except json.JSONDecodeError:
                continue

        dominant_session = Counter(sessions).most_common(1)[0]
        dominant_regime = Counter(regimes).most_common(1)[0]
        dominant_strategy = Counter(strategies).most_common(1)[0]
        dominant_error = Counter(error_types).most_common(1)[0] if error_types else None
        avg_ob_score = round(sum(ob_scores) / len(ob_scores), 1) if ob_scores else None
        sweep_rate = round(sum(sweeps) / len(sweeps), 2) if sweeps else None

        patterns = []
        if dominant_session[1] >= 3:
            patterns.append(f"{dominant_session[1]}/{last_n} pertes en session {dominant_session[0]}")
        if dominant_regime[1] >= 3:
            patterns.append(f"{dominant_regime[1]}/{last_n} pertes en regime {dominant_regime[0]}")
        if dominant_strategy[1] >= 3:
            patterns.append(f"{dominant_strategy[1]}/{last_n} pertes sur strategie {dominant_strategy[0]}")
        if avg_ob_score is not None and avg_ob_score < 55:
            patterns.append(f"OB trop faible: score moyen {avg_ob_score}/100")
        if sweep_rate is not None and sweep_rate < 0.4:
            patterns.append(f"Sweep insuffisant: {sweep_rate:.0%} de confirmations")
        if dominant_error:
            patterns.append(f"Erreur dominante: {dominant_error[0]} ({dominant_error[1]}/{last_n})")

        return {
            "insufficient_data": False,
            "trades_analyzed": len(rows),
            "dominant_session": dominant_session,
            "dominant_regime": dominant_regime,
            "dominant_strategy": dominant_strategy,
            "dominant_error": dominant_error,
            "avg_ob_score": avg_ob_score,
            "sweep_confirmation_rate": sweep_rate,
            "patterns_identified": patterns,
            "suggestion": _suggest_correction(dominant_session, dominant_regime, dominant_strategy, avg_ob_score, sweep_rate),
        }

    def _scalar_sync(self, query: str) -> int:
        self._init_sync()
        with self._connect() as conn:
            row = conn.execute(query).fetchone()
        return int(row[0] or 0)

    def _normalize_trade(self, trade: dict[str, Any]) -> dict[str, Any]:
        breakdown = _normalize_breakdown(trade.get("agent_breakdown") or {})
        result = _normalize_result(trade)
        normalized = {
            "timestamp": trade.get("closed_at") or trade.get("timestamp") or _utcnow(),
            "session": trade.get("session"),
            "regime": trade.get("regime"),
            "strategy": trade.get("strategy") or trade.get("strategy_used"),
            "ob_score": _coalesce_float(trade.get("ob_score"), _agent_score(breakdown, "agent_2")),
            "sweep_confirmed": _coalesce_bool(trade.get("sweep_confirmed"), _agent_bool(breakdown, "agent_3", "sweep_confirmed")),
            "amd_phase": trade.get("amd_phase") or _agent_value(breakdown, "agent_3", "amd_phase"),
            "fib_precision": _coalesce_float(trade.get("fib_precision"), _agent_score(breakdown, "agent_4")),
            "direction": trade.get("direction") or trade.get("type"),
            "entry": _coalesce_float(trade.get("entry"), trade.get("entry_price")),
            "sl": _to_float(trade.get("sl")),
            "tp1": _coalesce_float(trade.get("tp1"), trade.get("tp")),
            "result": result,
            "pnl_pct": _coalesce_float(trade.get("pnl_pct"), _pnl_pct_from_trade(trade)),
            "rr_achieved": _to_float(trade.get("rr_achieved")),
            "agent_breakdown": breakdown,
        }
        normalized["error_patterns"] = _detect_error_patterns(normalized) if result == "LOSS" else []
        return normalized

    def _agent_was_correct(self, agent: dict[str, Any], trade: dict[str, Any]) -> int | None:
        result = trade["result"]
        if result == "BE":
            return None
        score = _to_float(agent.get("score")) or 0.0
        hard_filter = bool(agent.get("hf", agent.get("hard_filter_pass", True)))
        supported_trade = hard_filter and score >= 50.0
        return int(supported_trade if result == "WIN" else not supported_trade)


async def memory_learning_loop(
    blackboard,
    db: MemoryDB | None = None,
    notifier: Callable[[Any, str], Awaitable[None]] | None = None,
) -> None:
    """Record closed trades, update agent accuracy, and pause after 5 cumulative losses."""
    logger = get_logger()
    memory = db or MemoryDB()
    notify = notifier or send_telegram_notification
    processed_keys: set[str] = set()
    await memory.init()
    logger.info("Memory DB loop demarree")

    while not blackboard.kill_event.is_set():
        try:
            closed_today = blackboard.read_sync("positions.closed_today") or []
            for trade in closed_today:
                key = _trade_key(trade)
                if key in processed_keys:
                    continue
                await memory.record_closed_trade(trade)
                processed_keys.add(key)
                loss_count = await memory.count_losses()
                await _update_memory_state(blackboard, {"last_recorded_trade": key, "loss_count": loss_count})
                await _pause_after_five_losses(blackboard, memory, notify, loss_count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Memory DB loop erreur: {exc}")

        await asyncio.sleep(0.5)


async def _pause_after_five_losses(
    blackboard,
    memory: MemoryDB,
    notify: Callable[[Any, str], Awaitable[None]],
    loss_count: int,
) -> None:
    if loss_count < 5:
        return
    control = blackboard.read_sync("control") or {}
    if int(control.get("memory_loss_count_alerted") or 0) >= loss_count:
        return

    analysis = await memory.analyze_losing_trades(last_n=5)
    message = _format_memory_report(loss_count, analysis)
    now = _utcnow()
    async with blackboard._lock:
        bb_control = blackboard._data.setdefault("control", {})
        bb_control.update(
            {
                "paused": True,
                "pause_reason": "MEMORY_5_LOSS_PATTERN",
                "paused_at": now,
                "memory_pause": True,
                "memory_pause_at": now,
                "memory_loss_count_alerted": loss_count,
            }
        )
        blackboard._data["trade_signals"] = {}
        blackboard._data.setdefault("orchestrator", {})["pending_signal"] = None
        blackboard._data.setdefault("memory", {})["last_loss_analysis"] = analysis
    await notify(blackboard, message)


async def _update_memory_state(blackboard, updates: dict[str, Any]) -> None:
    async with blackboard._lock:
        memory = blackboard._data.setdefault("memory", {})
        memory.update(updates)
        memory["updated_at"] = _utcnow()


def _format_memory_report(loss_count: int, analysis: dict[str, Any]) -> str:
    if analysis.get("insufficient_data"):
        pattern = "Donnees insuffisantes pour isoler un pattern fiable."
        suggestion = "Verifier manuellement les pertes recentes avant reprise."
    else:
        patterns = analysis.get("patterns_identified") or ["Aucun pattern dominant unique, pertes dispersees."]
        pattern = "\n".join(f"- {item}" for item in patterns)
        suggestion = analysis.get("suggestion") or "Revoir les filtres de score avant /resume."

    return (
        "MEMOIRE C1 - PAUSE AUTOMATIQUE\n"
        f"5 pertes cumulees atteintes ({loss_count} pertes en DB).\n\n"
        "Pattern identifie:\n"
        f"{pattern}\n\n"
        f"Suggestion: {suggestion}\n"
        "Nouveaux trades suspendus. Taper /resume apres investigation pour reprendre."
    )


def _suggest_correction(
    dominant_session: tuple[str, int],
    dominant_regime: tuple[str, int],
    dominant_strategy: tuple[str, int],
    avg_ob_score: float | None,
    sweep_rate: float | None,
) -> str:
    if avg_ob_score is not None and avg_ob_score < 55:
        return "Augmenter le filtre qualite OB ou refuser les setups structurels faibles."
    if sweep_rate is not None and sweep_rate < 0.4:
        return "Exiger un sweep confirme avant execution sur ce contexte."
    if dominant_regime[1] >= 3 and dominant_regime[0] == "RANGING":
        return "Durcir le seuil RANGING_BOUNDARY et reduire le risque sur range."
    if dominant_strategy[1] >= 3:
        return f"Recalibrer min_score/risk_pct pour {dominant_strategy[0]} avant reprise."
    if dominant_session[1] >= 3:
        return f"Limiter temporairement les nouveaux trades en session {dominant_session[0]}."
    return "Analyser les 5 pertes puis reprendre avec /resume seulement si le filtre est corrige."


def _normalize_breakdown(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for agent_id, value in raw.items():
        if isinstance(value, dict):
            normalized[str(agent_id)] = dict(value)
        else:
            normalized[str(agent_id)] = {"score": value}
    return normalized


def _normalize_result(trade: dict[str, Any]) -> str:
    raw = str(trade.get("outcome") or trade.get("result") or "").upper()
    if raw in {"WIN", "LOSS"}:
        return raw
    if raw in {"BE", "BREAKEVEN", "BREAK_EVEN"}:
        return "BE"
    pnl = _to_float(trade.get("pnl")) or 0.0
    if pnl > 0:
        return "WIN"
    if pnl < 0:
        return "LOSS"
    return "BE"


def _detect_error_patterns(trade: dict[str, Any]) -> list[dict[str, str]]:
    patterns: list[dict[str, str]] = []
    session = trade.get("session") or "UNKNOWN"
    regime = trade.get("regime") or "UNKNOWN"
    strategy = trade.get("strategy") or "UNKNOWN"
    patterns.append(
        {
            "type": f"LOSS_{session}_{regime}",
            "description": f"Trade perdant en session {session}, regime {regime}, strategie {strategy}",
        }
    )
    if (trade.get("ob_score") or 0) < 55:
        patterns.append({"type": "OB_QUALITY_LOW", "description": "Order block sous le seuil qualite 55/100"})
    if not trade.get("sweep_confirmed"):
        patterns.append({"type": "SWEEP_MISSING", "description": "Perte sans sweep confirme par agent liquidite"})
    if trade.get("fib_precision") is not None and float(trade["fib_precision"]) < 55:
        patterns.append({"type": "FIB_PRECISION_LOW", "description": "Precision Fibonacci insuffisante sur trade perdant"})
    return patterns


def _trade_key(trade: dict[str, Any]) -> str:
    if trade.get("ticket") is not None:
        return f"ticket:{trade.get('ticket')}"
    return f"{trade.get('closed_at')}-{trade.get('entry')}-{trade.get('pnl')}"


def _agent_score(breakdown: dict[str, dict[str, Any]], agent_id: str) -> float | None:
    return _to_float((breakdown.get(agent_id) or {}).get("score"))


def _agent_bool(breakdown: dict[str, dict[str, Any]], agent_id: str, key: str) -> bool | None:
    agent = breakdown.get(agent_id) or {}
    if key not in agent:
        return None
    return bool(agent.get(key))


def _agent_value(breakdown: dict[str, dict[str, Any]], agent_id: str, key: str) -> Any:
    return (breakdown.get(agent_id) or {}).get(key)


def _coalesce_float(*values: Any) -> float | None:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _coalesce_bool(*values: Any) -> bool | None:
    for value in values:
        if value is not None:
            return bool(value)
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pnl_pct_from_trade(trade: dict[str, Any]) -> float | None:
    pnl = _to_float(trade.get("pnl"))
    entry = _coalesce_float(trade.get("entry"), trade.get("entry_price"))
    if pnl is None or not entry:
        return None
    return round((pnl / entry) * 100.0, 4)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

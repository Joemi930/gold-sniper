import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import LOG_BACKUP_COUNT, LOG_MAX_BYTES

LOG_DIR = Path("logs")
DECISION_LOG = LOG_DIR / "decision_log.jsonl"
MISSED_OPPORTUNITIES = LOG_DIR / "missed_opportunities.jsonl"

LOG_DIR.mkdir(exist_ok=True)

_write_lock = asyncio.Lock()


def _json_default(value: Any) -> str:
    """Convertit les objets non JSON natifs en chaîne stable."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def rotate_jsonl_if_needed(
    path: Path,
    max_bytes: int = LOG_MAX_BYTES,
    backup_count: int = LOG_BACKUP_COUNT,
) -> None:
    """Rotation simple des JSONL applicatifs: file, file.1, file.2, etc."""
    if backup_count <= 0 or not path.exists() or path.stat().st_size < max_bytes:
        return

    oldest = Path(f"{path}.{backup_count}")
    if oldest.exists():
        oldest.unlink()

    for index in range(backup_count - 1, 0, -1):
        src = Path(f"{path}.{index}")
        if src.exists():
            src.replace(Path(f"{path}.{index + 1}"))

    path.replace(Path(f"{path}.1"))


def _append_jsonl(path: Path, entry: dict) -> None:
    rotate_jsonl_if_needed(path)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, default=_json_default) + "\n")


def _agent_to_dict(result: Any) -> dict:
    """Normalise un AgentResult V2 pour le Decision Log."""
    return {
        "score": round(float(getattr(result, "score", 0.0)), 1),
        "hf": bool(getattr(result, "hard_filter_pass", False)),
        "reason": getattr(result, "reason", ""),
        "direction": getattr(result, "direction", None),
        "payload": getattr(result, "payload", {}) or {},
        "veto": bool(getattr(result, "veto", False)),
    }


async def log_decision_cycle(orchestrator_result: dict, agent_results: list) -> None:
    """
    Log un cycle de décision complet en JSONL.
    Une ligne JSON par cycle, exploitable ensuite avec pandas ou Script 17.
    """
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "decision": orchestrator_result.get("decision"),
        "score": orchestrator_result.get("score"),
        "raw_score": orchestrator_result.get("raw_score"),
        "stars": orchestrator_result.get("stars"),
        "direction": orchestrator_result.get("direction"),
        "regime": orchestrator_result.get("regime"),
        "reason": orchestrator_result.get("reason"),
        "agents": {
            result.agent_id: _agent_to_dict(result)
            for result in agent_results
            if hasattr(result, "agent_id")
        },
        "trade_result": None,
    }

    async with _write_lock:
        _append_jsonl(DECISION_LOG, entry)


async def update_trade_result(timestamp: str, pnl: float, won: bool, exit_reason: str) -> None:
    """
    Met à jour le résultat d'un trade dans le Decision Log après fermeture.
    La recherche se fait par timestamp exact de l'entrée JSONL.
    """
    if not DECISION_LOG.exists():
        return

    async with _write_lock:
        lines = []
        with open(DECISION_LOG, "r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("ts") == timestamp:
                    entry["trade_result"] = {
                        "pnl": pnl,
                        "won": won,
                        "exit_reason": exit_reason,
                        "closed_at": datetime.utcnow().isoformat(),
                    }
                lines.append(json.dumps(entry, ensure_ascii=False, default=_json_default))

        with open(DECISION_LOG, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + ("\n" if lines else ""))


async def log_missed_opportunity(
    score: float,
    direction: str | None,
    reason: str,
    agent_breakdown: dict,
) -> None:
    """
    Log les setups exceptionnels refusés, notamment par limite de trades.
    Sert à mesurer les opportunités ratées sans changer l'exécution.
    """
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "type": "MISSED_OPPORTUNITY",
        "score": score,
        "direction": direction,
        "reason": reason,
        "agents": agent_breakdown,
    }

    async with _write_lock:
        _append_jsonl(MISSED_OPPORTUNITIES, entry)


async def log_execution_block(reason: str, signal_data: dict | None = None, details: dict | None = None) -> None:
    """Log un blocage post-orchestrateur avant execution broker."""
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "type": "EXECUTION_BLOCKED",
        "decision": "BLOCKED",
        "score": (signal_data or {}).get("score"),
        "direction": (signal_data or {}).get("direction"),
        "reason": reason,
        "details": details or {},
        "trade_result": None,
    }

    async with _write_lock:
        _append_jsonl(DECISION_LOG, entry)


def get_agent_performance_stats(min_trades: int = 50) -> dict | None:
    """
    Analyse le Decision Log pour estimer la précision historique des agents.
    Retourne None si le nombre de trades clôturés est insuffisant.
    """
    if not DECISION_LOG.exists():
        return None

    stats = {f"agent_{i}": {"correct": 0, "total": 0} for i in range(1, 6)}
    trade_count = 0

    with open(DECISION_LOG, "r", encoding="utf-8") as handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            result = entry.get("trade_result")
            if not result:
                continue

            trade_count += 1
            won = bool(result.get("won", False))

            for agent_id, agent_data in entry.get("agents", {}).items():
                if agent_id not in stats:
                    continue
                score = float(agent_data.get("score", 0))
                stats[agent_id]["total"] += 1
                if (score >= 70 and won) or (score < 50 and not won):
                    stats[agent_id]["correct"] += 1

    if trade_count < min_trades:
        return None

    for agent_id, agent_stats in stats.items():
        total = agent_stats["total"]
        agent_stats["accuracy"] = agent_stats["correct"] / total if total else 0.0

    return stats

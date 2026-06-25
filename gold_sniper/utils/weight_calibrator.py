from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.orchestrator import BASE_WEIGHTS
from utils.decision_logger import DECISION_LOG


MIN_TRADES_FOR_CALIBRATION = 50
MAX_WEIGHT_CHANGE_PCT = 0.20
MIN_WEIGHT = 5.0
MIN_AGENT_1_WEIGHT = 20.0
MAX_AGENT_1_WEIGHT = 40.0
CALIBRATION_LOG = Path("logs/calibration_log.jsonl")
CALIBRATED_WEIGHTS_PATH = Path("logs/calibrated_weights.json")


def _read_trade_entries(decision_log_path: Path = DECISION_LOG) -> list[dict]:
    """Retourne uniquement les trades clotures avec resultat exploitable."""
    if not decision_log_path.exists():
        return []

    trades: list[dict] = []
    with open(decision_log_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            trade_result = entry.get("trade_result")
            if not isinstance(trade_result, dict) or "won" not in trade_result:
                continue
            if not isinstance(entry.get("agents"), dict):
                continue
            trades.append(entry)

    return trades


def calculate_agent_accuracy(
    decision_log_path: Path = DECISION_LOG,
    min_trades: int = MIN_TRADES_FOR_CALIBRATION,
    weights: dict[str, float] | None = None,
) -> dict | None:
    """Calcule la precision de chaque agent sur les trades logges."""
    agent_weights = weights or BASE_WEIGHTS
    trades = _read_trade_entries(decision_log_path)
    if len(trades) < min_trades:
        return None

    stats = {
        agent_id: {"correct": 0, "total": 0, "accuracy": 0.0}
        for agent_id in agent_weights
    }

    for entry in trades:
        won = bool(entry["trade_result"].get("won", False))
        agents = entry.get("agents", {})
        for agent_id in agent_weights:
            agent_data = agents.get(agent_id)
            if not isinstance(agent_data, dict):
                continue
            score = float(agent_data.get("score", 0.0) or 0.0)
            predicted_positive = score >= 70.0
            predicted_negative = score < 50.0
            if not predicted_positive and not predicted_negative:
                continue

            stats[agent_id]["total"] += 1
            if (predicted_positive and won) or (predicted_negative and not won):
                stats[agent_id]["correct"] += 1

    for agent_id, agent_stats in stats.items():
        total = agent_stats["total"]
        agent_stats["accuracy"] = round(agent_stats["correct"] / total, 4) if total else 0.0

    return {
        "trade_count": len(trades),
        "agent_stats": stats,
    }


def recalibrate_weights(
    current_weights: dict[str, float] | None = None,
    decision_log_path: Path = DECISION_LOG,
    min_trades: int = MIN_TRADES_FOR_CALIBRATION,
    apply_to_orchestrator: bool = True,
    log_path: Path = CALIBRATION_LOG,
) -> dict | None:
    """
    Recalibre les poids selon les trades reels clotures.

    Retourne None si moins de min_trades trades clotures sont disponibles.
    """
    weights = dict(current_weights or BASE_WEIGHTS)
    analysis = calculate_agent_accuracy(decision_log_path, min_trades=min_trades, weights=weights)
    if analysis is None:
        return None

    accuracy_scores = {
        agent_id: data["accuracy"]
        for agent_id, data in analysis["agent_stats"].items()
        if agent_id in weights
    }
    total_accuracy = sum(accuracy_scores.values())
    if total_accuracy <= 0:
        return None

    total_current_weight = sum(weights.values())
    new_weights: dict[str, float] = {}

    for agent_id, current_weight in weights.items():
        accuracy = accuracy_scores.get(agent_id)
        if accuracy is None:
            new_weights[agent_id] = float(current_weight)
            continue

        target_weight = (accuracy / total_accuracy) * total_current_weight
        max_delta = current_weight * MAX_WEIGHT_CHANGE_PCT
        delta = max(-max_delta, min(max_delta, target_weight - current_weight))
        new_weight = max(MIN_WEIGHT, current_weight + delta)

        if agent_id == "agent_1":
            new_weight = max(MIN_AGENT_1_WEIGHT, min(MAX_AGENT_1_WEIGHT, new_weight))

        new_weights[agent_id] = round(new_weight, 1)

    _log_recalibration(
        old_weights=weights,
        new_weights=new_weights,
        analysis=analysis,
        log_path=log_path,
        applied=apply_to_orchestrator,
    )

    if apply_to_orchestrator:
        BASE_WEIGHTS.clear()
        BASE_WEIGHTS.update(new_weights)
        _save_calibrated_weights(new_weights, analysis)

    return new_weights


def _save_calibrated_weights(new_weights: dict[str, float], analysis: dict) -> None:
    CALIBRATED_WEIGHTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.utcnow().isoformat(),
        "weights": new_weights,
        "trade_count": analysis["trade_count"],
        "source": "decision_log",
    }
    with open(CALIBRATED_WEIGHTS_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def _log_recalibration(
    old_weights: dict[str, float],
    new_weights: dict[str, float],
    analysis: dict[str, Any],
    log_path: Path = CALIBRATION_LOG,
    applied: bool = True,
) -> None:
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "type": "WEIGHT_RECALIBRATION",
        "applied": applied,
        "min_trades_required": MIN_TRADES_FOR_CALIBRATION,
        "trade_count": analysis["trade_count"],
        "old_weights": old_weights,
        "new_weights": new_weights,
        "agent_accuracy": analysis["agent_stats"],
    }

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def print_recalibration_report(old_weights: dict[str, float], new_weights: dict[str, float]) -> None:
    print("\nRECALIBRATION DES POIDS")
    for agent_id in old_weights:
        old = old_weights[agent_id]
        new = new_weights.get(agent_id, old)
        delta = new - old
        sign = "+" if delta >= 0 else ""
        print(f"{agent_id}: {old} -> {new} ({sign}{delta:.1f})")


if __name__ == "__main__":
    old = dict(BASE_WEIGHTS)
    new = recalibrate_weights()
    if new is None:
        print(f"Calibration refusee: minimum {MIN_TRADES_FOR_CALIBRATION} trades clotures requis.")
    else:
        print_recalibration_report(old, new)

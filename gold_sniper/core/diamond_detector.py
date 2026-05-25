import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config import DIAMOND_MIN_RR
from core.blackboard import BLACKBOARD, BlackBoard
from utils.telegram_notifier import send_telegram_notification


MISSED_OPPORTUNITIES = Path("logs/missed_opportunities.jsonl")
DIAMOND_SCORE_THRESHOLD = 92.0
SWEET_SPOT = 0.705
SWEET_SPOT_TOLERANCE = 0.005


def evaluate_diamond_setup(
    blackboard: BlackBoard | None = None,
    agent_data: dict[str, Any] | None = None,
    final_score: float | None = None,
    direction: str | None = None,
) -> dict[str, Any]:
    board = blackboard or BLACKBOARD
    agents = agent_data or {f"agent_{i}": board.get_agent(f"agent_{i}") for i in range(1, 8)}
    a2 = _as_dict(agents.get("agent_2"))
    a3 = _as_dict(agents.get("agent_3"))
    a4 = _as_dict(agents.get("agent_4"))
    a5 = _as_dict(agents.get("agent_5"))
    orch = board.get_all().get("orchestrator", {})

    score = _to_float(final_score)
    if score is None:
        score = _to_float(orch.get("final_score"))
    score = score or 0.0

    if not direction:
        direction = a5.get("direction") or a4.get("direction") or a3.get("direction") or a2.get("direction")

    fib_ratio = _extract_fib_ratio(a4, direction)
    rr = _extract_rr(a5, a4, direction)

    conditions = {
        "poi_confluence": _is_poi_confluence(a2),
        "fib_sweet_spot_705": fib_ratio is not None and abs(fib_ratio - SWEET_SPOT) <= SWEET_SPOT_TOLERANCE,
        "rr_minimum": rr is not None and rr >= DIAMOND_MIN_RR,
        "asian_sweep_confirmed": _asian_sweep_confirmed(a3),
        "orchestrator_score": score >= DIAMOND_SCORE_THRESHOLD,
    }
    met_count = sum(1 for value in conditions.values() if value)

    return {
        "is_diamond": all(conditions.values()),
        "met_count": met_count,
        "conditions": conditions,
        "missing": [name for name, ok in conditions.items() if not ok],
        "details": {
            "poi_type": a2.get("poi_type") or ("CONFLUENCE" if _is_poi_confluence(a2) else a2.get("zone_type")),
            "ob_score": _to_float(a2.get("ob_score")),
            "fib_ratio": round(fib_ratio, 4) if fib_ratio is not None else None,
            "sweet_spot": SWEET_SPOT,
            "sweet_spot_tolerance": SWEET_SPOT_TOLERANCE,
            "rr": round(rr, 2) if rr is not None else None,
            "min_rr": DIAMOND_MIN_RR,
            "asian_sweep": _asian_sweep_label(a3),
            "score": round(score, 1),
            "min_score": DIAMOND_SCORE_THRESHOLD,
            "direction": direction,
        },
    }


async def alert_diamond_setup(
    blackboard: BlackBoard,
    evaluation: dict[str, Any],
    agent_breakdown: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> bool:
    if not evaluation.get("is_diamond"):
        return False

    context = context or {}
    alert_key = _alert_key(evaluation, context)
    orchestrator = blackboard.get_all().get("orchestrator", {})
    if orchestrator.get("diamond_last_alert_key") == alert_key:
        return False

    message = format_diamond_message(evaluation, context)
    await send_telegram_notification(blackboard, message)
    await _log_diamond_opportunity(evaluation, agent_breakdown, context)

    async with blackboard._lock:
        blackboard._data.setdefault("orchestrator", {})["diamond_last_alert_key"] = alert_key
        blackboard._data["orchestrator"]["diamond_last_alert_at"] = datetime.utcnow().isoformat()
        blackboard._data["orchestrator"]["diamond_last_evaluation"] = evaluation
    return True


def format_diamond_message(evaluation: dict[str, Any], context: dict[str, Any] | None = None) -> str:
    context = context or {}
    details = evaluation.get("details", {})
    checks = evaluation.get("conditions", {})
    check_lines = "\n".join(
        f"- {'OK' if ok else 'NO'} {name}"
        for name, ok in checks.items()
    )
    return (
        "💎 DIAMOND SETUP DÉTECTÉ\n"
        f"Direction: {details.get('direction') or context.get('direction')}\n"
        f"Score global: {details.get('score')}/100\n"
        f"Session: {context.get('session')} | Régime: {context.get('regime')}\n"
        f"POI: {details.get('poi_type')} | OB score: {details.get('ob_score')}\n"
        f"Fibo: {details.get('fib_ratio')} (sweet spot 0.705 ± 0.005)\n"
        f"R:R: {details.get('rr')} | Sweep asiatique: {details.get('asian_sweep')}\n"
        "Action: NON exécuté automatiquement — validation manuelle requise pour le 3e trade.\n"
        f"Conditions:\n{check_lines}"
    )


async def _log_diamond_opportunity(
    evaluation: dict[str, Any],
    agent_breakdown: dict[str, Any],
    context: dict[str, Any],
) -> None:
    entry = {
        "ts": datetime.utcnow().isoformat(),
        "type": "DIAMOND_SETUP",
        "decision": "ALERT_ONLY",
        "reason": "DAILY_LIMIT_REACHED_DIAMOND_5_OF_5",
        "evaluation": evaluation,
        "context": context,
        "agents": agent_breakdown,
    }
    MISSED_OPPORTUNITIES.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
    await asyncio.to_thread(_append_line, MISSED_OPPORTUNITIES, line)


def _append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _alert_key(evaluation: dict[str, Any], context: dict[str, Any]) -> str:
    details = evaluation.get("details", {})
    return "|".join(
        str(value)
        for value in (
            context.get("session"),
            context.get("regime"),
            details.get("direction"),
            details.get("fib_ratio"),
            details.get("rr"),
            details.get("score"),
        )
    )


def _is_poi_confluence(a2: dict[str, Any]) -> bool:
    poi_type = str(a2.get("poi_type") or a2.get("type") or "").upper()
    if poi_type in {"CONFLUENCE", "OB_FVG_CONFLUENCE"}:
        return True
    return bool(
        a2.get("fvg_confluence")
        and (a2.get("active_ob") or a2.get("ob_active") or a2.get("has_ob"))
        and (a2.get("active_fvg") or a2.get("fvg_active") or a2.get("has_fvg"))
    )


def _extract_fib_ratio(a4: dict[str, Any], direction: str | None) -> float | None:
    for key in ("fib_ratio", "retracement", "retracement_ratio", "sweet_spot_ratio"):
        ratio = _to_float(a4.get(key))
        if ratio is not None:
            return ratio / 100.0 if ratio > 1 else ratio

    current = _to_float(a4.get("current_price"))
    levels = a4.get("levels") if isinstance(a4.get("levels"), dict) else a4
    swing_low = _to_float(levels.get("swing_low") or levels.get("low"))
    swing_high = _to_float(levels.get("swing_high") or levels.get("high"))
    if current is None or swing_low is None or swing_high is None or swing_high <= swing_low:
        return None

    if direction == "SHORT":
        return (current - swing_low) / (swing_high - swing_low)
    return (swing_high - current) / (swing_high - swing_low)


def _extract_rr(a5: dict[str, Any], a4: dict[str, Any], direction: str | None) -> float | None:
    for data in (a5, a4):
        for key in ("rr", "rr_ratio", "risk_reward", "expected_rr"):
            rr = _to_float(data.get(key))
            if rr is not None:
                return rr

    entry = _first_float(a5, "entry", "entry_price")
    sl = _first_float(a5, "sl", "sl_price", "stop_loss")
    target = (
        _first_float(a5, "diamond_target", "tp3", "tp3_price", "tp2", "tp2_price", "take_profit")
        or _first_float(a4, "tp3", "tp2", "take_profit")
    )
    if entry is None or sl is None or target is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    return abs(target - entry) / risk


def _asian_sweep_confirmed(a3: dict[str, Any]) -> bool:
    asian = a3.get("asian_range") if isinstance(a3.get("asian_range"), dict) else {}
    return bool(
        a3.get("asian_swept")
        or a3.get("sweep_confirmed")
        or a3.get("sweep_detected")
        or a3.get("asian_sweep_confirmed")
        or asian.get("swept_side") is not None
    )


def _asian_sweep_label(a3: dict[str, Any]) -> str:
    asian = a3.get("asian_range") if isinstance(a3.get("asian_range"), dict) else {}
    return str(asian.get("swept_side") or a3.get("sweep_side") or _asian_sweep_confirmed(a3))


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    payload = getattr(value, "payload", {}) or {}
    data = dict(payload)
    for key in ("score", "direction", "hard_filter_pass", "reason", "veto"):
        if hasattr(value, key):
            data[key] = getattr(value, key)
    return data


def _first_float(data: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _to_float(data.get(key))
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

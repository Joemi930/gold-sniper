"""Construit le 'setup surveille' (Pending Setup) pour le dashboard.

Vue LECTURE SEULE du scenario que le moteur surveille en WAIT_FOR_TRIGGER :
sens, entree envisagee, grade, TP1/TP2, SL, confirmations manquantes.
Aucun effet sur les decisions ni sur les trades — purement affichage.
Toutes les donnees proviennent d'agents deja calcules (bougies cloturees) :
aucune connaissance du futur.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _payload(agent: dict[str, Any]) -> dict[str, Any]:
    """Retourne le payload d'un agent, qu'il soit imbrique ou aplati."""
    if not isinstance(agent, dict):
        return {}
    inner = agent.get("payload")
    if isinstance(inner, dict):
        merged = dict(agent)
        merged.update(inner)
        return merged
    return agent


def _grade_from_score(score: float) -> str:
    if score >= 95:
        return "A+"
    if score >= 85:
        return "A"
    if score >= 70:
        return "B"
    if score >= 50:
        return "C"
    return "D"


def _zone_mid(zone: Any) -> float | None:
    if isinstance(zone, (list, tuple)) and len(zone) == 2:
        return _num((float(zone[0]) + float(zone[1])) / 2.0)
    return None


def build_pending_setup(blackboard, decision: dict[str, Any]) -> dict[str, Any] | None:
    """Construit le dict pending_setup, ou None si rien de pertinent a surveiller."""
    dec = (decision.get("decision") or "").upper()
    # On n'affiche un setup surveille que si le moteur attend (pas EXECUTE, pas idle total).
    if dec in {"EXECUTE", "EXCEPTIONAL_OVERRIDE"}:
        return None

    direction = decision.get("direction")
    if not direction or str(direction).upper() in {"NONE", "NEUTRAL"}:
        return None

    score = 0.0
    try:
        score = float(decision.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    grade = decision.get("grade") or _grade_from_score(score)

    a4 = _payload(blackboard.get_agent("agent_4"))
    a5 = _payload(blackboard.get_agent("agent_5"))
    a2 = _payload(blackboard.get_agent("agent_2"))
    levels = a4.get("levels") if isinstance(a4.get("levels"), dict) else {}
    is_long = str(direction).upper() in {"BUY", "LONG"}

    # Entree envisagee : Agent 5 (AMD) sinon zone OTE/POI d'Agent 4/2.
    entry = _num(a5.get("entry_price"))
    if entry is None:
        entry = _num(levels.get("ote_sweet")) or _zone_mid(levels.get("ote_zone"))
    if entry is None:
        poi = a2.get("poi_zone") if isinstance(a2.get("poi_zone"), dict) else {}
        entry = _zone_mid([poi.get("bottom"), poi.get("top")]) if poi else None

    tp1 = _num(a5.get("tp1_price")) or _num(levels.get("tp1"))
    tp2 = _num(a5.get("tp2_price")) or _num(levels.get("tp2"))

    # SL : Agent 5 sinon extreme du swing d'Agent 4 (estimation affichee).
    sl = _num(a5.get("sl_price"))
    sl_is_estimate = False
    if sl is None:
        swing = a4.get("swing_used") if isinstance(a4.get("swing_used"), dict) else {}
        sl = _num(swing.get("low_price") if is_long else swing.get("high_price"))
        sl_is_estimate = sl is not None

    # Confirmations encore manquantes (raisons de readiness des agents).
    missing: list[str] = []
    for key in ("agent_3", "agent_4", "agent_7"):
        ap = _payload(blackboard.get_agent(key))
        reason = ap.get("readiness_reason") or ap.get("not_applicable_reason")
        if reason and str(reason).upper() not in {"NONE", ""}:
            missing.append(str(reason))
    # Deduplication en conservant l'ordre.
    missing = list(dict.fromkeys(missing))

    readiness = (
        a4.get("execution_readiness")
        or a4.get("readiness_state")
        or ("WAIT_FOR_TRIGGER" if dec == "WAIT" else dec)
    )

    if entry is None and tp1 is None and tp2 is None and sl is None:
        return None

    return {
        "active": True,
        "direction": str(direction).upper(),
        "grade": grade,
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "sl": sl,
        "sl_is_estimate": sl_is_estimate,
        "readiness": readiness,
        "missing_confirmations": missing,
        "score": round(score, 1),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

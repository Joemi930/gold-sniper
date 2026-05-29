# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.0 — ORCHESTRATEUR (CERVEAU PRINCIPAL)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Script 01 — Phase 1
#
# Remplace le vote binaire 5/5 par un score pondéré dynamique.
#
# Règles d'exécution (dans l'ordre strict) :
#   1. Veto absolu  → risk_manager ou agent_6 → décision VETOED immédiate
#   2. Hard Filters → agent_1 ou agent_2 score=0 → REJECT
#   3. Conflit directionnel agent_1 vs agent_3 (écart < 10 pts) → REJECT
#   4. Score pondéré avec modificateurs de régime (BLACKBOARD["market"]["regime"])
#   5. Décroissance temporelle du signal : -5 pts/min après 3 min
#   6. Décision finale : EXECUTE si ≥85 | WAIT si ≥70 | REJECT sinon
#   7. EXCEPTIONAL_ALERT si ≥92 mais limite trades atteinte
#
# Seuil d'exécution : 85/100 (vs 90 binaire en V1)
# Modificateurs de régime : ajustent dynamiquement les poids par marché
# Decision Log : chaque cycle est enregistré dans logs/decision_log.jsonl
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.blackboard import BLACKBOARD, BlackBoard
from core.diamond_detector import alert_diamond_setup, evaluate_diamond_setup
from core.strategy_dictionary import check_diamond_conditions, select_active_strategy
from agents.base_agent import AgentResult
from utils.logger import get_logger
from utils.discord_notifier import send_discord_notification, _notifier_from_config
from config import MAX_TRADES_PER_DAY, DRAWDOWN_LIMIT, EVENT_DRIVEN_TIMEOUT, RISK_PCT_PER_TRADE

# ── Decision Logger (Script 02) — import conditionnel pour ne pas bloquer ──────
try:
    from utils.decision_logger import log_decision_cycle, log_missed_opportunity
    _DECISION_LOG_AVAILABLE = True
except ImportError:
    _DECISION_LOG_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# PARAMÈTRES DE L'ORCHESTRATEUR V2.0
# ─────────────────────────────────────────────────────────────────────────────

# Poids de base par agent (total = 100 pts)
BASE_WEIGHTS = {
    "agent_1": 30,   # HARD filter — MTF structure & biais directionnel
    "agent_2": 25,   # HARD filter — POI zones (OB / FVG)
    "agent_3": 20,   # SOFT — Liquidité / Sweep validation
    "agent_4": 15,   # SOFT — Fibonacci / OTE timing
    "agent_5": 10,   # SOFT — Déclencheur CHoCH / AMD
}


def _apply_saved_calibrated_weights() -> None:
    """Charge les poids calibrés si Script 17 les a validés."""
    try:
        import json
        from pathlib import Path

        path = Path("logs/calibrated_weights.json")
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        weights = payload.get("weights", {})
        expected = set(BASE_WEIGHTS)
        if set(weights) != expected:
            return
        BASE_WEIGHTS.update({agent_id: float(weight) for agent_id, weight in weights.items()})
    except Exception:
        return


_apply_saved_calibrated_weights()


def _resolve_active_weights(board: BlackBoard, strategy) -> tuple[dict[str, float], bool]:
    """Apply adaptive session weights first, then strategy overrides."""
    weights = {agent_id: float(weight) for agent_id, weight in BASE_WEIGHTS.items()}
    adaptive_applied = False

    adaptive_weights = (board.get_all().get("orchestrator", {}) or {}).get("adaptive_weights")
    if isinstance(adaptive_weights, dict):
        candidate = {}
        for agent_id in BASE_WEIGHTS:
            try:
                candidate[agent_id] = float(adaptive_weights[agent_id])
            except (KeyError, TypeError, ValueError):
                candidate = {}
                break
        if candidate and sum(candidate.values()) > 0:
            weights.update(candidate)
            adaptive_applied = True

    if strategy.weight_overrides:
        weights.update({agent_id: float(weight) for agent_id, weight in strategy.weight_overrides.items()})

    return weights, adaptive_applied

# Seuils de décision
EXECUTION_THRESHOLD  = 85.0   # EXECUTE si score ≥ 85
WATCH_THRESHOLD      = 70.0   # WAIT si score ≥ 70 (log seulement)
EXCEPTIONAL_THRESHOLD = 92.0  # EXCEPTIONAL_ALERT si trades épuisés mais score ≥ 92

# Décroissance temporelle du signal
SIGNAL_DECAY_START_MIN = 3     # Décroissance commence après 3 minutes
SIGNAL_DECAY_RATE      = 5.0   # Points perdus par minute après le délai

# Limite de drawdown journalier (% de l'equity initiale)
MAX_DAILY_DRAWDOWN_PCT = DRAWDOWN_LIMIT

# Modificateurs de poids selon le régime détecté par le macro_monitor (Script 09)
REGIME_WEIGHT_MODIFIERS = {
    "TRENDING":        {"agent_1": 1.3, "agent_4": 1.1, "agent_3": 0.9},
    "RANGING":         {"agent_2": 1.3, "agent_3": 1.2, "agent_1": 0.8},
    "HIGH_VOLATILITY": {"agent_6": 1.5, "agent_4": 0.7, "agent_5": 0.8},
    "ACCUMULATION":    {"agent_2": 1.2, "agent_3": 1.3, "agent_5": 1.1},
    "UNKNOWN":         {},   # Poids de base inchangés
}


# ─────────────────────────────────────────────────────────────────────────────
# FONCTION PRINCIPALE : run_orchestrator()
# ─────────────────────────────────────────────────────────────────────────────

async def run_orchestrator(agent_results: list, blackboard: Optional[BlackBoard] = None) -> dict:
    """
    Cerveau principal du Gold Sniper V2.0.
    Agrège les scores de tous les agents et retourne la décision finale.

    Args:
        agent_results : Liste d'objets AgentResult provenant des 5 agents analytiques.

    Returns:
        dict avec les clés : decision, score, raw_score, stars, direction,
                             regime, reason, agent_breakdown, timestamp.
    """
    board = blackboard or BLACKBOARD
    market    = board.get_market()
    regime    = market.get("regime", "UNKNOWN")
    results_map = {r.agent_id: r for r in agent_results}

    # ── ÉTAPE 1 : Veto absolu (risk_manager + agent_6) ──────────────────────
    for agent_id in ["risk_manager", "agent_6"]:
        agent_data = board.get_agent(agent_id)
        if agent_data.get("veto", False):
            result = _build_vetoed(
                results_map,
                f"VETO_ABSOLU par {agent_id.upper()} : {agent_data.get('reason', '')}"
            )
            await _log_and_update(result, agent_results)
            return result

    # ── ÉTAPE 2 : Hard Filters (agent_1 + agent_2) ──────────────────────────
    a1 = results_map.get("agent_1")
    a2 = results_map.get("agent_2")

    if not a1 or a1.score == 0 or not a1.hard_filter_pass:
        reason = a1.reason if a1 else "agent_1 non disponible"
        result = _build_reject(results_map, f"HARD_FILTER_FAIL agent_1 : {reason}")
        await _log_and_update(result, agent_results)
        return result

    if not a2 or a2.score == 0 or not a2.hard_filter_pass:
        reason = a2.reason if a2 else "agent_2 non disponible"
        result = _build_reject(results_map, f"HARD_FILTER_FAIL agent_2 : {reason}")
        await _log_and_update(result, agent_results)
        return result

    # ── ÉTAPE 3 : Conflit directionnel agent_1 vs agent_3 ───────────────────
    a3 = results_map.get("agent_3")
    if a1 and a3 and a1.direction and a3.direction:
        directions_conflict = (a1.direction != a3.direction)
        scores_close        = abs(a1.score - a3.score) < 10
        if directions_conflict and scores_close:
            result = _build_reject(
                results_map,
                f"CONFLICT a1({a1.direction}) vs a3({a3.direction}) — écart score < 10 pts"
            )
            await _log_and_update(result, agent_results)
            return result

    # ── ÉTAPE 4 : Score pondéré avec modificateurs de régime ────────────────
    session_context = _extract_session_context(results_map.get("agent_7"), board)
    session_name = session_context["session_name"]
    agent_snapshot = {
        agent_id: {
            **(getattr(result, "payload", {}) or {}),
            "score": getattr(result, "score", None),
            "direction": getattr(result, "direction", None),
            "hard_filter_pass": getattr(result, "hard_filter_pass", None),
        }
        for agent_id, result in results_map.items()
    }
    for agent_id in ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"]:
        agent_snapshot.setdefault(agent_id, board.get_agent(agent_id))

    agent_6_data = agent_snapshot.get("agent_6", {})
    post_news = bool(
        agent_6_data.get("post_news")
        or agent_6_data.get("post_news_mode")
        or agent_6_data.get("after_news_window")
    )
    diamond_ok = check_diamond_conditions(agent_snapshot)
    strategy = select_active_strategy(session_name, regime, post_news, diamond_ok)

    weights, adaptive_weights_applied = _resolve_active_weights(board, strategy)

    regime_mods   = REGIME_WEIGHT_MODIFIERS.get(regime, {})
    weighted_sum  = 0.0
    total_weight  = 0.0

    for agent_id, base_weight in weights.items():
        r = results_map.get(agent_id)
        if r is None:
            continue
        mod              = regime_mods.get(agent_id, 1.0)
        effective_weight = base_weight * mod
        weighted_sum    += r.score * effective_weight
        total_weight    += effective_weight

    raw_score = (weighted_sum / total_weight) if total_weight > 0 else 0.0

    # ── ÉTAPE 5 : Décroissance temporelle du signal ──────────────────────────
    orch_data        = board.get_all().get("orchestrator", {})
    last_signal_time = orch_data.get("last_signal_time")
    decayed_score    = raw_score

    if last_signal_time:
        # last_signal_time peut être datetime ou ISO string
        if isinstance(last_signal_time, str):
            try:
                last_signal_time = datetime.fromisoformat(last_signal_time)
            except ValueError:
                last_signal_time = None

        if last_signal_time:
            age_min = (datetime.utcnow() - last_signal_time).total_seconds() / 60
            if age_min > SIGNAL_DECAY_START_MIN:
                decay        = (age_min - SIGNAL_DECAY_START_MIN) * SIGNAL_DECAY_RATE
                decayed_score = max(0.0, raw_score - decay)

    # ── ÉTAPE 6 : Session Awareness (Agent 7 / Chronos) ──────────────────────
    direction = a1.direction  # Direction validée par le Hard Filter
    risk_modifier = session_context["risk_modifier"]
    session_score = decayed_score

    if session_name == "TOKYO" and decayed_score < session_context["tokyo_override_score"]:
        final_result = {
            "decision":       "REJECT",
            "score":          round(decayed_score, 1),
            "raw_score":      round(raw_score, 1),
            "stars":          0,
            "direction":      None,
            "risk_modifier":  risk_modifier,
            "regime":         regime,
            "session":        session_name,
            "reason":         (
                f"TOKYO_ONLY_BLOCK score={decayed_score:.1f} "
                f"< {session_context['tokyo_override_score']:.1f}"
            ),
            "agent_breakdown": _build_breakdown(results_map),
            "timestamp":      datetime.utcnow().isoformat(),
        }
        await _log_and_update(final_result, agent_results)
        return final_result

    if not session_context["trading_allowed"] and session_name not in {"UNKNOWN", "TOKYO"}:
        final_result = {
            "decision":       "REJECT",
            "score":          round(decayed_score, 1),
            "raw_score":      round(raw_score, 1),
            "stars":          0,
            "direction":      None,
            "risk_modifier":  risk_modifier,
            "regime":         regime,
            "session":        session_name,
            "reason":         f"SESSION_BLOCKED {session_name}",
            "agent_breakdown": _build_breakdown(results_map),
            "timestamp":      datetime.utcnow().isoformat(),
        }
        await _log_and_update(final_result, agent_results)
        return final_result

    if risk_modifier > 1.0:
        session_score = min(100.0, decayed_score * risk_modifier)

    # ── ÉTAPE 7 : Décision finale ────────────────────────────────────────────

    risk_data    = board.get_agent("risk_manager")
    trades_today = risk_data.get("trades_today", 0)
    base_risk_pct = float(RISK_PCT_PER_TRADE or 1.0)
    strategy_risk_modifier = float(strategy.risk_pct) / base_risk_pct
    final_risk_modifier = risk_modifier * strategy_risk_modifier
    diamond_evaluation = None

    if session_score >= strategy.min_score:
        # Vérifier si la limite de trades est atteinte
        if trades_today >= MAX_TRADES_PER_DAY:
            diamond_evaluation = evaluate_diamond_setup(
                board,
                agent_snapshot,
                final_score=session_score,
                direction=direction,
            )
            if diamond_evaluation["is_diamond"]:
                stars    = 5
                decision = "DIAMOND_ALERT"
            else:
                stars    = 4
                decision = "WAIT"
        else:
            stars    = 5
            decision = "EXECUTE"
    elif session_score >= WATCH_THRESHOLD:
        stars    = 4
        decision = "WAIT"
    else:
        stars    = 3
        decision = "REJECT"

    final_result = {
        "decision":       decision,
        "score":          round(session_score, 1),
        "raw_score":      round(raw_score, 1),
        "stars":          stars,
        "direction":      direction,
        "risk_modifier":  final_risk_modifier,
        "regime":         regime,
        "session":        session_name,
        "strategy":       strategy.name,
        "strategy_min_score": strategy.min_score,
        "strategy_exceptional_score": strategy.exceptional_score,
        "strategy_risk_pct": strategy.risk_pct,
        "strategy_weight_overrides": strategy.weight_overrides,
        "adaptive_weights_applied": adaptive_weights_applied,
        "effective_weights": weights,
        "diamond_evaluation": diamond_evaluation,
        "reason":         (
            f"SCORE_{session_score:.1f}/100 | base={decayed_score:.1f} | "
            f"régime={regime} | dir={direction} | "
            f"session={session_name} x{risk_modifier:.2f} | "
            f"trades_today={trades_today}/{MAX_TRADES_PER_DAY}"
        ),
        "agent_breakdown": _build_breakdown(results_map),
        "timestamp":      datetime.utcnow().isoformat(),
    }

    await _log_and_update(final_result, agent_results)

    # Alerte Telegram si setup exceptionnel refusé pour limite atteinte
    if decision == "EXCEPTIONAL_ALERT":
        await send_discord_notification(
            board,
            f"⚡ *SETUP EXCEPTIONNEL DÉTECTÉ* ⚡\n"
            f"Score : `{session_score:.1f}/100` | Direction : `{direction}`\n"
            f"⚠️ Limite de {MAX_TRADES_PER_DAY} trades atteinte — *non exécuté automatiquement*.\n"
            f"Régime : `{regime}`"
        )
        if _DECISION_LOG_AVAILABLE:
            try:
                await log_missed_opportunity(
                    score=round(session_score, 1),
                    direction=direction,
                    reason=final_result["reason"],
                    agent_breakdown=final_result["agent_breakdown"],
                )
            except Exception:
                pass

    if decision == "DIAMOND_ALERT":
        await alert_diamond_setup(
            board,
            final_result["diamond_evaluation"],
            final_result["agent_breakdown"],
            {
                "session": session_name,
                "regime": regime,
                "direction": direction,
                "trades_today": trades_today,
                "max_trades_per_day": MAX_TRADES_PER_DAY,
                "strategy": strategy.name,
            },
        )

    return final_result


# ─────────────────────────────────────────────────────────────────────────────
# BOUCLE ASYNCIO : orchestrator_loop()
# ─────────────────────────────────────────────────────────────────────────────

async def orchestrator_loop(blackboard: BlackBoard) -> None:
    """
    Boucle principale de l'Orchestrateur V2.0.
    Tourne indéfiniment, cadencée sur les événements du Blackboard.

    Responsabilités additionnelles :
      - Reset journalier à minuit UTC (compteurs, equity de référence)
      - Surveillance du drawdown journalier (arrêt si > MAX_DAILY_DRAWDOWN_PCT)
      - Écriture du signal validé dans blackboard["trade_signals"]
      - Mise à jour de blackboard["orchestrator"] pour l'UI
    """
    logger         = get_logger()
    logger.info(
        "Orchestrateur V3 event-driven demarre "
        f"- seuil execution=strategy.min_score | fallback={EVENT_DRIVEN_TIMEOUT:.1f}s"
    )

    last_rejection_reason = ""
    last_reset_day        = datetime.now(timezone.utc).day
    equity_day_start      = 0.0
    last_agent_sequence   = 0
    latency_samples: list[float] = []

    while not blackboard.kill_event.is_set():
        try:
            update_info = await blackboard.wait_for_agent_update(
                last_sequence=last_agent_sequence,
                timeout=EVENT_DRIVEN_TIMEOUT,
            )
            if update_info is not None:
                last_agent_sequence = int(update_info.get("sequence") or last_agent_sequence)

            now = datetime.now(timezone.utc)

            # ── Reset journalier ─────────────────────────────────────────────
            if now.day != last_reset_day:
                last_reset_day   = now.day
                equity_day_start = 0.0
                async with blackboard._lock:
                    blackboard._data["meta"]["daily_trade_count"] = 0
                    blackboard._data.setdefault("daily_stats", {})
                    blackboard._data["daily_stats"]["realized_pnl"]   = 0.0
                    blackboard._data["daily_stats"]["floating_pnl"]   = 0.0
                    blackboard._data["daily_stats"]["trades_closed"]  = 0
                    blackboard._data["daily_stats"]["drawdown_halt"]  = False
                    # Synchroniser aussi le slot risk_manager
                    blackboard._data["agents"]["risk_manager"]["trades_today"] = 0
                    blackboard._data["agents"]["risk_manager"]["daily_loss_pct"] = 0.0
                    blackboard._data["agents"]["risk_manager"]["veto"] = False
                logger.info("🌅 Reset journalier — Compteurs remis à zéro")
                await send_discord_notification(
                    blackboard, "🌅 *Nouvelle journée de trading* — Compteurs réinitialisés"
                )

            # ── Capture equity de début de journée ──────────────────────────
            meta = blackboard._data.get("meta", {})
            account = meta.get("account_info")
            if account and equity_day_start == 0.0:
                equity_day_start = account.get("equity", 0.0)

            # ── Surveillance du drawdown journalier ──────────────────────────
            if equity_day_start > 0:
                daily_stats = blackboard._data.get("daily_stats", {})
                if not daily_stats.get("drawdown_halt", False):
                    realized = daily_stats.get("realized_pnl", 0.0)
                    floating = daily_stats.get("floating_pnl", 0.0)
                    total_loss = realized + floating   # Négatif si en perte

                    max_loss = -(equity_day_start * MAX_DAILY_DRAWDOWN_PCT / 100.0)
                    if total_loss <= max_loss:
                        logger.critical(
                            f"🚨 DRAWDOWN JOURNALIER ATTEINT — "
                            f"Perte: {total_loss:.2f}$ | Limite: {max_loss:.2f}$"
                        )
                        async with blackboard._lock:
                            blackboard._data["daily_stats"]["drawdown_halt"] = True
                            blackboard._data["agents"]["risk_manager"]["veto"]   = True
                            blackboard._data["agents"]["risk_manager"]["reason"] = (
                                f"DRAWDOWN_HALT — perte {total_loss:.2f}$ > limite {max_loss:.2f}$"
                            )
                        await send_discord_notification(
                            blackboard,
                            f"🚨 *DAILY DRAWDOWN DÉCLENCHÉ !*\n"
                            f"💸 Perte : `{total_loss:.2f} USD` | Limite : `{max_loss:.2f} USD`\n"
                            f"🛑 *Trading suspendu pour aujourd'hui.*"
                        )

            # ── Lecture des résultats agents ─────────────────────────────────
            # On collecte les AgentResult depuis blackboard["agent_results"]
            agent_results = []
            for agent_id in ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"]:
                result_obj = blackboard._data.get("agent_results", {}).get(agent_id)
                if result_obj is not None:
                    agent_results.append(result_obj)

            # Si aucun agent n'a encore produit de résultat, attendre
            if not agent_results:
                continue

            # ── Décision V2 ──────────────────────────────────────────────────
            decision = await run_orchestrator(agent_results, blackboard)

            if update_info and update_info.get("published_at_perf") is not None:
                latency_ms = (time.perf_counter() - update_info["published_at_perf"]) * 1000
                latency_samples.append(latency_ms)
                latency_samples = latency_samples[-50:]
                last_ten = latency_samples[-10:]
                avg_latency = sum(last_ten) / len(last_ten)
                min_latency = min(last_ten)
                max_latency = max(last_ten)
                await blackboard.update_dict("orchestrator", {
                    "last_agent_event": update_info.get("agent_id"),
                    "last_agent_event_sequence": last_agent_sequence,
                    "last_latency_ms": round(latency_ms, 3),
                    "latency_avg_ms": round(avg_latency, 3),
                    "latency_min_ms": round(min_latency, 3),
                    "latency_max_ms": round(max_latency, 3),
                    "latency_samples": [round(sample, 3) for sample in last_ten],
                })
                if len(last_ten) == 10 and avg_latency > 50.0:
                    logger.warning(
                        f"Latence orchestrateur elevee : avg={avg_latency:.1f}ms "
                        f"min={min_latency:.1f}ms max={max_latency:.1f}ms"
                    )

            # ── Mise à jour Blackboard pour l'UI ─────────────────────────────
            await blackboard.update_dict("orchestrator", {
                "final_score":  decision["score"],
                "stars":        decision["stars"],
                "decision":     decision["decision"],
                "direction":    decision["direction"],
                "strategy":     decision.get("strategy"),
                "strategy_min_score": decision.get("strategy_min_score"),
                "strategy_risk_pct": decision.get("strategy_risk_pct"),
                "last_updated": now,
            })

            # ── Émettre le signal de trade si EXECUTE ────────────────────────
            if decision["decision"] == "EXECUTE":
                control = blackboard._data.get("control", {})
                if control.get("paused", False):
                    await blackboard.write("trade_signals", {})
                    await blackboard.update_dict("orchestrator", {
                        "pending_signal": None,
                        "paused": True,
                        "pause_reason": control.get("pause_reason", "TELEGRAM_PAUSE"),
                    })
                    logger.info("Signal EXECUTE ignore: trading en pause Telegram.")
                    continue

                # Récupérer les niveaux d'entrée depuis Agent 5 (payload AMD complet)
                a5_data = blackboard.get_agent("agent_5")
                entry   = a5_data.get("entry_price")
                sl      = a5_data.get("sl_price")
                tp1     = a5_data.get("tp1_price")
                tp2     = a5_data.get("tp2_price")

                # Fallback : prix actuel si Agent 5 n'a pas de niveaux
                if not entry:
                    tick  = blackboard._data.get("market_data", {}).get("current_tick", {})
                    entry = tick.get("ask") if decision["direction"] == "LONG" else tick.get("bid")

                if entry and sl and (tp1 or tp2):
                    signal_data = {
                        "signal":      "BUY" if decision["direction"] == "LONG" else "SELL",
                        "direction":   decision["direction"],
                        "entry_price": entry,
                        "stop_loss":   sl,
                        "tp1_price":   tp1,
                        "tp2_price":   tp2 or tp1,
                        "take_profit": tp2 or tp1,
                        "score":       decision["score"],
                        "stars":       decision["stars"],
                        "regime":      decision["regime"],
                        "strategy":    decision.get("strategy"),
                        "risk_pct":    decision.get("strategy_risk_pct"),
                        "risk_modifier": decision.get("risk_modifier", 1.0),
                        "timestamp":   now,
                        "v2_decision": decision,
                    }
                    await blackboard.write("trade_signals", signal_data)
                    await blackboard.update_dict("orchestrator", {
                        "pending_signal":    signal_data,
                        "last_signal_time":  now,
                    })
                    logger.trade(  # type: ignore[attr-defined]
                        f"✅ SIGNAL V2 EXECUTE : {signal_data['signal']} @ {entry:.2f} | "
                        f"Score: {decision['score']} | SL: {sl:.2f} | TP: {tp2 or tp1:.2f}"
                    ) if hasattr(logger, 'trade') else logger.info(
                        f"✅ SIGNAL V2 EXECUTE : {signal_data['signal']} @ {entry:.2f} | "
                        f"Score: {decision['score']} | SL: {sl:.2f} | TP: {tp2 or tp1:.2f}"
                    )
                    last_rejection_reason = ""
                else:
                    logger.warning(
                        "⚠️  Signal EXECUTE mais niveaux incomplets — "
                        f"entry={entry} sl={sl} tp={tp2 or tp1}"
                    )

            elif decision["decision"] in {"EXCEPTIONAL_ALERT", "DIAMOND_ALERT"}:
                # Signal exceptionnel déjà géré (Telegram) dans run_orchestrator
                logger.warning(
                    f"⚡ {decision['decision']} — Score {decision['score']} | "
                    f"Limite trades atteinte, pas d'exécution auto"
                )

            else:
                # REJECT ou WAIT — vider le signal précédent
                current = blackboard._data.get("trade_signals", {})
                if current:
                    await blackboard.write("trade_signals", {})
                    await blackboard.update_dict("orchestrator", {"pending_signal": None})

                reason = decision.get("reason", "")
                if reason and reason != last_rejection_reason:
                    logger.debug(
                        f"❌ Signal rejeté V2 : {decision['decision']} "
                        f"(Score: {decision.get('score', 0):.1f})"
                    )
                    last_rejection_reason = reason

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Erreur critique Orchestrateur V3 event-driven : {e}")


    logger.warning("Orchestrateur V3 event-driven arrete.")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS INTERNES
# ─────────────────────────────────────────────────────────────────────────────

def _extract_session_context(agent_7_result: Optional[AgentResult], blackboard: Optional[BlackBoard] = None) -> dict:
    """Lit le contexte Chronos sans rendre Agent 7 obligatoire au demarrage."""
    board = blackboard or BLACKBOARD
    payload = getattr(agent_7_result, "payload", {}) or {}
    bb_agent_7 = board.get_agent("agent_7")

    if agent_7_result is None and not bb_agent_7.get("last_updated"):
        market_session = board.get_market().get("session") or "UNKNOWN"
        if market_session in {None, "NONE"}:
            market_session = "UNKNOWN"
        return {
            "session_name": market_session,
            "trading_allowed": True,
            "risk_modifier": 1.0,
            "tokyo_override_score": 92.0,
        }

    session_name = (
        payload.get("session_name")
        or bb_agent_7.get("session_name")
        or board.get_market().get("session")
        or "UNKNOWN"
    )
    if session_name in {None, "NONE"}:
        session_name = "UNKNOWN"
    trading_allowed = payload.get("trading_allowed")
    if trading_allowed is None:
        trading_allowed = bb_agent_7.get("trading_allowed", True)

    risk_modifier = getattr(agent_7_result, "risk_modifier", None)
    if risk_modifier is None:
        risk_modifier = payload.get("session_confidence", bb_agent_7.get("risk_modifier", 1.0))

    try:
        risk_modifier = float(risk_modifier)
    except (TypeError, ValueError):
        risk_modifier = 1.0

    return {
        "session_name": session_name,
        "trading_allowed": bool(trading_allowed),
        "risk_modifier": risk_modifier,
        "tokyo_override_score": float(payload.get("tokyo_override_score", 92.0)),
    }


async def _log_and_update(result: dict, agent_results: list) -> None:
    """Log le cycle de décision si le Decision Logger est disponible."""
    if _DECISION_LOG_AVAILABLE:
        try:
            await log_decision_cycle(result, agent_results)
        except Exception:
            pass  # Jamais bloquer l'orchestrateur à cause du log


def _build_breakdown(results_map: dict) -> dict:
    """Construit le résumé par agent pour le log et l'UI."""
    return {
        agent_id: {
            "score": round(r.score, 1),
            "hf":    r.hard_filter_pass,
            "dir":   r.direction,
            "reason": r.reason,
        }
        for agent_id, r in results_map.items()
    }


def _build_reject(results_map: dict, reason: str) -> dict:
    """Construit une réponse de type REJECT standard."""
    return {
        "decision":       "REJECT",
        "score":          0.0,
        "raw_score":      0.0,
        "stars":          0,
        "direction":      None,
        "regime":         "N/A",
        "reason":         reason,
        "agent_breakdown": _build_breakdown(results_map),
        "timestamp":      datetime.utcnow().isoformat(),
    }


def _build_vetoed(results_map: dict, reason: str) -> dict:
    """Construit une réponse de type VETOED (plus sévère que REJECT)."""
    return {
        "decision":       "VETOED",
        "score":          0.0,
        "raw_score":      0.0,
        "stars":          0,
        "direction":      None,
        "regime":         "N/A",
        "reason":         reason,
        "agent_breakdown": _build_breakdown(results_map),
        "timestamp":      datetime.utcnow().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# COMPATIBILITÉ : orchestrate_v2() — garde le point d'entrée de l'ancien code
# (engine.py appelle orchestrator_loop, donc OK ; mais au cas où)
# ─────────────────────────────────────────────────────────────────────────────

def orchestrate_v2(blackboard: BlackBoard) -> dict:
    """
    Wrapper synchrone pour compatibilité avec l'ancien code.
    Préférer run_orchestrator() (async) pour le nouveau code.
    Retourne un résultat REJECT statique si appelé sans boucle asyncio active.
    """
    return {
        "trade":     False,
        "stars":     0,
        "score":     0,
        "direction": None,
        "decision":  "REJECT",
        "reason":    "USE_run_orchestrator_ASYNC_INSTEAD",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

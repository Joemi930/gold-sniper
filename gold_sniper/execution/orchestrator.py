# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.0 — ORCHESTRATEUR (CERVEAU V2)
# ═══════════════════════════════════════════════════════════════════════════════
#
# FIXES V2.0 :
#   - BUG#2 : MAX_TRADES_PER_DAY maintenant vérifiée avant chaque signal
#   - BUG#3 : Daily Drawdown Limit implémentée (arrêt si perte > seuil)
#   - Feature : Vérification du R:R minimum (MIN_RISK_REWARD) avant exécution
#   - Feature : Cooldown journalier reset à minuit UTC
#   - Feature : Notifications Telegram sur drawdown critique
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
from datetime import datetime, timezone

from core.blackboard import BlackBoard
from utils.logger import get_logger
from utils.telegram_notifier import send_telegram_notification
from execution.adaptive_weights import AdaptiveWeightEngine
from config import (
    MAX_TRADES_PER_DAY,
    MIN_RISK_REWARD,
)

# ── Poids des agents (100 points au total) ────────────────────────────────────
POIDS = {
    "agent_1": 30,   # HARD FILTER — Structure & biais directionnel
    "agent_2": 25,   # HARD FILTER — Zone institutionnelle
    "agent_3": 20,   # SOFT — Confirmation sweep de liquidité
    "agent_4": 15,   # SOFT — Timing OTE Fibonacci
    "agent_5": 10,   # SOFT — Déclencheur CHoCH 1M
}

SEUIL_EXECUTION = 90     # Score minimum pour exécuter
SEUIL_LOG_ONLY  = 75     # Score minimum pour logger sans exécuter

# Limite de drawdown journalier (% de l'equity de début de journée)
MAX_DAILY_DRAWDOWN_PERCENT = 5.0   # Arrêt si on perd > 5% dans la journée


def _reject(reason: str, agent_result=None) -> dict:
    return {
        "trade": False,
        "stars": 0,
        "score": 0,
        "direction": None,
        "risk_modifier": 0,
        "decision": "REJECT",
        "reason": reason,
        "agent_detail": agent_result.reason if agent_result else "AGENT_TIMEOUT",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def orchestrate_v2(blackboard: BlackBoard) -> dict:
    """
    Cerveau de décision V2.0.
    NE TRADE QUE si :
      - Gates A6/A7 ouvertes
      - Hard Filters A1/A2 passés
      - Score pondéré ≥ 90
      - MAX_TRADES_PER_DAY non atteint (BUG#2 fix)
      - Daily Drawdown non atteint (BUG#3 fix)
      - R:R minimum respecté (Feature new)
    """
    results = {
        agent: blackboard.read_sync(f"agent_results.{agent}")
        for agent in ["agent_1", "agent_2", "agent_3", "agent_4", "agent_5", "agent_6", "agent_7"]
    }

    # ── GATE 0 : Drawdown journalier (BUG#3 fix) ──────────────────────────
    daily_stats = blackboard._data.get("daily_stats", {})
    if daily_stats.get("drawdown_halt", False):
        return _reject("DAILY_DRAWDOWN_HALT")

    # ── GATE 0b : Limite journalière de trades (BUG#2 fix) ────────────────
    daily_count = blackboard._data.get("meta", {}).get("daily_trade_count", 0)
    if daily_count >= MAX_TRADES_PER_DAY:
        return _reject(f"MAX_DAILY_TRADES_REACHED ({daily_count}/{MAX_TRADES_PER_DAY})")

    # ── GATE 1 : Agent 7 (Sessions / Chronos) ─────────────────────────────
    if results["agent_7"] is None or results["agent_7"].score == 0:
        return _reject("GATE_CHRONOS_BLOCKED", results["agent_7"])

    risk_modifier = results["agent_7"].risk_modifier

    # ── GATE 2 : Agent 6 (Sentinelle / News) ──────────────────────────────
    if results["agent_6"] is None or results["agent_6"].score == 0:
        return _reject("GATE_NEWS_BLACKOUT", results["agent_6"])

    # ── HARD FILTER 1 : Agent 1 (Météo / Structure) ───────────────────────
    if results["agent_1"] is None or results["agent_1"].score == 0:
        return _reject("HARD_FILTER_MTF_FAIL", results["agent_1"])

    direction = results["agent_1"].direction  # "LONG" ou "SHORT"

    # ── HARD FILTER 2 : Agent 2 (Cartographe / Zones) ─────────────────────
    if results["agent_2"] is None or results["agent_2"].score == 0:
        return _reject("HARD_FILTER_NO_POI", results["agent_2"])

    # ── CALCUL DU SCORE PONDÉRÉ ────────────────────────────────────────────
    total_weight = sum(POIDS.values())  # = 100

    weighted_score = sum(
        (results[agent].score if results[agent] else 0) * POIDS[agent]
        for agent in POIDS
    ) / total_weight

    # ── CLASSIFICATION ET DÉCISION ─────────────────────────────────────────
    agent_breakdown = {
        agent: {
            "score": results[agent].score if results[agent] else 0,
            "reason": results[agent].reason if results[agent] else "NOT_COMPUTED",
            "weight": POIDS.get(agent, 0),
            "contribution": (results[agent].score if results[agent] else 0) * POIDS.get(agent, 0) / 100
        }
        for agent in POIDS
    }

    if weighted_score >= SEUIL_EXECUTION:
        stars = 5
        decision = "EXECUTE"
    elif weighted_score >= SEUIL_LOG_ONLY:
        stars = 4
        decision = "LOG_ONLY"
    else:
        stars = 3
        decision = "REJECT"

    return {
        "trade": decision == "EXECUTE",
        "stars": stars,
        "score": round(weighted_score, 2),
        "direction": direction,
        "risk_modifier": risk_modifier,
        "decision": decision,
        "agent_breakdown": agent_breakdown,
        "seuil_utilise": SEUIL_EXECUTION,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": "OK" if decision == "EXECUTE" else "LOW_SCORE"
    }


async def orchestrator_loop(blackboard: BlackBoard) -> None:
    """
    Boucle principale de l'Orchestrateur.
    Gère aussi :
    - Reset journalier du compteur de trades à minuit UTC
    - Surveillance du drawdown (BUG#3 fix)
    - Vérification du R:R minimum avant exécution (Feature new)
    """
    logger = get_logger()
    logger.info("▶️  Orchestrateur démarré — Cerveau V2 (Seuil 90) ACTIF")

    last_rejection_reason = ""
    engine = AdaptiveWeightEngine()
    last_reset_day = datetime.now(timezone.utc).day
    equity_day_start: float = 0.0

    while not blackboard.kill_event.is_set():
        try:
            now = datetime.now(timezone.utc)

            # ── Reset journalier (compteurs + equity de référence) ─────────
            if now.day != last_reset_day:
                last_reset_day = now.day
                async with blackboard._lock:
                    blackboard._data["meta"]["daily_trade_count"] = 0
                    blackboard._data.setdefault("daily_stats", {})
                    blackboard._data["daily_stats"]["realized_pnl"] = 0.0
                    blackboard._data["daily_stats"]["floating_pnl"] = 0.0
                    blackboard._data["daily_stats"]["trades_closed"] = 0
                    blackboard._data["daily_stats"]["drawdown_halt"] = False
                    equity_day_start = 0.0  # Sera re-capturé au prochain cycle
                logger.info("🌅 Reset journalier — Compteurs remis à zéro")
                await send_telegram_notification(blackboard, "🌅 *Nouvelle journée de trading* — Compteurs réinitialisés")

            # ── Capture de l'equity de début de journée ─────────────────────
            account = blackboard.read_sync("meta.account_info") if "account_info" in blackboard._data.get("meta", {}) else None
            if account and equity_day_start == 0.0:
                equity_day_start = account.get("equity", 0.0)

            # ── Vérification du drawdown journalier (BUG#3 fix) ─────────────
            if equity_day_start > 0:
                daily_stats = blackboard._data.get("daily_stats", {})
                realized = daily_stats.get("realized_pnl", 0.0)
                floating = daily_stats.get("floating_pnl", 0.0)
                total_loss = realized + floating  # négatif si en perte

                max_loss_allowed = -(equity_day_start * MAX_DAILY_DRAWDOWN_PERCENT / 100.0)
                if total_loss <= max_loss_allowed and not daily_stats.get("drawdown_halt", False):
                    logger.critical(
                        f"🚨 DAILY DRAWDOWN ATTEINT ! PnL: {total_loss:.2f}$ | "
                        f"Limite: {max_loss_allowed:.2f}$ — Trading SUSPENDU pour aujourd'hui"
                    )
                    async with blackboard._lock:
                        blackboard._data["daily_stats"]["drawdown_halt"] = True
                    await send_telegram_notification(
                        blackboard,
                        f"🚨 *DAILY DRAWDOWN DÉCLENCHÉ !*\n"
                        f"💸 Perte: `{total_loss:.2f} USD` / Limite: `{max_loss_allowed:.2f} USD`\n"
                        f"🛑 *Trading suspendu pour aujourd'hui.*"
                    )

            # ── Décision V2 ─────────────────────────────────────────────────
            decision = orchestrate_v2(blackboard)

            # Mise à jour UI
            await blackboard.update_dict("orchestrator", {
                "star_rating": decision["stars"],
                "all_filters_passed": decision["trade"],
                "last_decision": decision,
                "pipeline_active": True,
            })

            # ── Exécution si validé ──────────────────────────────────────────
            if decision["trade"]:
                action = "BUY" if decision["direction"] == "LONG" else "SELL"
                tick = blackboard.read_sync("market_data.current_tick")
                ote_zone = blackboard.read_sync("market_analysis.ote_zone") or {}

                entry_price = tick.get("ask", 0) if action == "BUY" else tick.get("bid", 0)

                # SL / TP depuis Agent 4 metadata
                agent4_result = blackboard.read_sync("agent_results.agent_4")
                if agent4_result and agent4_result.metadata:
                    sl = (ote_zone.get("swing_low", 0) if action == "BUY"
                          else ote_zone.get("swing_high", 0))
                    if not sl:
                        meta4 = agent4_result.metadata.get("ote_zone", {})
                        sl = meta4.get("bottom", 0) if action == "BUY" else meta4.get("top", 0)
                    tp = agent4_result.metadata.get("tp2", 0) or agent4_result.metadata.get("tp1", 0)
                else:
                    sl = (ote_zone.get("swing_low", 0) if action == "BUY"
                          else ote_zone.get("swing_high", 0))
                    tp = (ote_zone.get("swing_high", 0) if action == "BUY"
                          else ote_zone.get("swing_low", 0))

                # Fallback minimal
                if not sl or not tp:
                    sl = entry_price - 0.005 if action == "BUY" else entry_price + 0.005
                    tp = entry_price + 0.010 if action == "BUY" else entry_price - 0.010

                # ── VÉRIFICATION DU R:R MINIMUM (Feature new) ───────────────
                if sl > 0 and tp > 0 and entry_price > 0:
                    risk_distance = abs(entry_price - sl)
                    reward_distance = abs(tp - entry_price)
                    rr_ratio = reward_distance / risk_distance if risk_distance > 0 else 0

                    if rr_ratio < MIN_RISK_REWARD:
                        logger.debug(
                            f"❌ R:R insuffisant : {rr_ratio:.2f} < {MIN_RISK_REWARD} requis — Signal rejeté"
                        )
                        decision["trade"] = False
                        decision["reason"] = f"RR_TOO_LOW ({rr_ratio:.2f} < {MIN_RISK_REWARD})"
                        # Ne pas émettre le signal
                        await blackboard.write("trade_signals", {})
                        last_rejection_reason = decision["reason"]
                        await asyncio.sleep(1.0)
                        continue

                signal_data = {
                    "signal": action,
                    "entry_price": entry_price,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "timestamp": now,
                    "v2_decision": decision,
                }

                await blackboard.update_dict("trade_signals", signal_data)
                logger.trade(
                    f"✅ SIGNAL V2 : {action} @ {entry_price:.2f} | "
                    f"Score: {decision['score']} | SL: {sl:.2f} | TP: {tp:.2f}"
                )
                await blackboard.update_dict("orchestrator", {"pending_signal": signal_data})
                last_rejection_reason = ""

            else:
                # Rejet
                current_signal = blackboard.read_sync("trade_signals")
                if current_signal:
                    await blackboard.write("trade_signals", {})
                    await blackboard.update_dict("orchestrator", {"pending_signal": None})

                reason = decision.get("reason", "")
                if reason and reason != last_rejection_reason:
                    logger.debug(f"❌ Signal rejeté V2 : {reason} (Score: {decision.get('score', 0)})")
                    last_rejection_reason = reason

        except Exception as e:
            logger.error(f"❌ Erreur critique dans l'Orchestrateur V2 : {e}")

        await asyncio.sleep(1.0)

    logger.warning("🛑 Orchestrateur arrêté.")

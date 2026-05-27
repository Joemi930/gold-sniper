# ═══════════════════════════════════════════════════════════════════════════════
# GOLD SNIPER V2.0 — TRADE MANAGER (L'EXÉCUTEUR)
# ═══════════════════════════════════════════════════════════════════════════════
#
# L'unique agent autorisé à interagir avec le Broker pour modifier le compte.
# - Écoute BLACKBOARD["trade_signals"] pour placer de nouveaux ordres.
# - Surveille BLACKBOARD["active_trades"] pour le BreakEven, Trailing Stop,
#   et la cloture partielle au TP1 reel defini a l'ouverture.
#
# FIXES V2.0 :
#   - BUG#1 : self.FIXED_LOT supprimé → self.risk_calculator.calculate_lot_size()
#   - BUG#4 : vérification tick non-nul avant toute exécution
#   - Feature : Trailing Stop ATR-based après Break-Even
#   - Feature : Tracking du PnL journalier (réalisé + flottant)
#   - Feature : Notifications Telegram sur entrée/sortie/erreur
#
# CONTRAINTES ABSOLUES :
# - TOUS les appels `mt5` passent par `asyncio.to_thread`.
# - Vérification stricte de `mt5.last_error()` pour la sécurité (OPSEC).
#
# ═══════════════════════════════════════════════════════════════════════════════

import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import MetaTrader5 as mt5

from agents.base_agent import BaseAgent
from config import (
    SYMBOL, MAGIC_NUMBER, COOLDOWN_SECONDS,
    PARTIAL_CLOSE_PERCENT, RISK_PCT_PER_TRADE, MT5_SYMBOL
)
from execution.risk_calculator import RiskCalculator
from utils.spread_monitor import SpreadMonitor
from utils.telegram_notifier import send_telegram_notification


class TradeManager(BaseAgent):
    def __init__(self, blackboard):
        super().__init__(blackboard, name="trade_manager")

        # ── Risk Calculator (FIX BUG#1 : remplace self.FIXED_LOT) ──────────
        self.risk_calculator = RiskCalculator(risk_percent=RISK_PCT_PER_TRADE)
        self.spread_monitor = SpreadMonitor(blackboard)

        # ── État interne ────────────────────────────────────────────────────
        self._last_trade_time: Optional[datetime] = None  # Pour cooldown

    # ─────────────────────────────────────────────────────────────────────────
    # CALCUL DU VOLUME (FIX BUG#1)
    # ─────────────────────────────────────────────────────────────────────────

    async def _calculate_volume(self, entry_price: float, stop_loss: float, risk_modifier: float = 1.0) -> float:
        """
        Calcule la taille de lot via le RiskCalculator dynamique (equity-based).
        Applique le risk_modifier de l'Agent 7 (Chronos).
        Fallback à 0.01 lot en cas d'erreur MT5.
        """
        try:
            control = self.blackboard.get_all().get("control", {})
            runtime_risk = control.get("risk_pct_per_trade")
            if runtime_risk is not None:
                self.risk_calculator.risk_percent = float(runtime_risk)
            lot = await asyncio.to_thread(
                self.risk_calculator.calculate_lot_size,
                SYMBOL,
                entry_price,
                stop_loss,
                risk_modifier,
                self.blackboard.get_market().get("atr_14_15m") or self.blackboard.read_sync("market_data.atr_14"),
                self.blackboard.get_market().get("atr_14_4h") or self.blackboard.get_market().get("atr_baseline"),
            )
            return max(0.01, lot)
        except Exception as e:
            self.logger.warning(f"⚠️ Fallback lot 0.01 — RiskCalculator error: {e}")
            return 0.01

    # ─────────────────────────────────────────────────────────────────────────
    # GUARD : VÉRIFICATION DU TICK (FIX BUG#4)
    # ─────────────────────────────────────────────────────────────────────────

    def _get_valid_tick(self) -> Optional[dict]:
        """
        Lit le tick courant et valide qu'il est non-nul et récent.
        Retourne None si le tick est invalide (BUG#4 fix).
        """
        tick = self.blackboard.read_sync("market_data.current_tick")
        if not tick:
            return None
        bid = tick.get("bid", 0.0)
        ask = tick.get("ask", 0.0)
        if bid <= 0.0 or ask <= 0.0:
            return None
        return tick

    # ─────────────────────────────────────────────────────────────────────────
    # COOLDOWN CHECK
    # ─────────────────────────────────────────────────────────────────────────

    def _is_in_cooldown(self) -> bool:
        """Vérifie si on est encore dans la période de cooldown post-trade."""
        if self._last_trade_time is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self._last_trade_time).total_seconds()
        if elapsed < COOLDOWN_SECONDS:
            remaining = int(COOLDOWN_SECONDS - elapsed)
            self.logger.debug(f"⏳ Cooldown actif — {remaining}s restantes avant prochain trade.")
            return True
        return False

    # ─────────────────────────────────────────────────────────────────────────
    # PLACEMENT D'ORDRE
    # ─────────────────────────────────────────────────────────────────────────

    async def place_order(self, signal_data: Dict[str, Any]) -> bool:
        """
        Prépare et envoie la requête de trading au broker.
        Retourne True si succès, False sinon.
        """
        # Guard : cooldown actif ?
        if self._is_in_cooldown():
            await self.blackboard.write("trade_signals", {})
            return False

        # Guard : tick valide ? (BUG#4 fix)
        tick = self._get_valid_tick()
        if tick is None:
            self.logger.warning("⚠️ Tick invalide — ordre annulé (prix = 0).")
            await self.blackboard.write("trade_signals", {})
            return False

        spread_check = await self.spread_monitor.check_before_entry(signal_data=signal_data)
        if not spread_check.get("allow_trade", False):
            self.logger.warning(f"Ordre annulé par SpreadMonitor: {spread_check.get('reason')}")
            await self.blackboard.write("trade_signals", {})
            return False

        action = signal_data["signal"]
        entry = signal_data["entry_price"]
        sl = signal_data["stop_loss"]
        tp = signal_data["take_profit"]
        tp1 = signal_data.get("tp1_price") or signal_data.get("tp1") or tp
        tp2 = signal_data.get("tp2_price") or signal_data.get("tp2") or tp
        broker_tp = tp2 or tp1

        # Guard : SL et TP valides ?
        if sl <= 0 or tp1 <= 0 or broker_tp <= 0:
            self.logger.error(
                f"SL ({sl}) ou TP invalide (TP1={tp1}, TP2={broker_tp}) - ordre annule."
            )
            await self.blackboard.write("trade_signals", {})
            return False

        v2_decision = signal_data.get("v2_decision", {})
        risk_mod = v2_decision.get("risk_modifier", 1.0)
        score = v2_decision.get("score", 0)

        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        volume = await self._calculate_volume(entry, sl, risk_modifier=risk_mod)

        # Construction de la requête MT5
        # Requete atomique: entree + SL + TP broker dans le meme order_send.
        # MT5 n'a qu'un seul champ TP; TP1 est stocke pour le partiel.
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": SYMBOL,
            "volume": float(volume),
            "type": order_type,
            "price": float(entry),
            "sl": float(sl),
            "tp": float(broker_tp),
            "deviation": 20,
            "magic": MAGIC_NUMBER,
            "comment": f"GoldSniper V2 [{score:.0f}pts]",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        self.logger.info(
            f"Envoi ordre atomique {action} | {volume} lots | "
            f"SL={sl:.2f} | TP1={tp1:.2f} | TP2={broker_tp:.2f}"
        )

        result = await asyncio.to_thread(mt5.order_send, request)

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            error = await asyncio.to_thread(mt5.last_error)
            retcode = getattr(result, 'retcode', 'UNKNOWN')
            comment = getattr(result, 'comment', '')
            self.logger.error(f"❌ Échec ordre {action} — Code: {retcode} | {comment} | Erreur: {error}")
            await send_telegram_notification(
                self.blackboard,
                f"❌ *ORDRE REJETÉ* — {action} {MT5_SYMBOL}\nCode: `{retcode}`\nErreur: `{error}`"
            )
            await self.blackboard.write("trade_signals", {})
            return False

        # ── Succès ──────────────────────────────────────────────────────────
        self.logger.trade(
            f"✅ ORDRE EXÉCUTÉ | Ticket: {result.order} | Prix: {result.price:.2f} | Lots: {volume}"
        )

        # Enregistrement dans les trades actifs
        now_utc = datetime.now(timezone.utc)
        trade_record = {
            "ticket": result.order,
            "type": action,
            "entry_price": result.price,
            "original_sl": float(sl),
            "current_sl": float(sl),
            "tp": float(broker_tp),
            "tp1": float(tp1),
            "tp2": float(broker_tp),
            "broker_tp": float(broker_tp),
            "volume_original": float(volume),
            "breakeven_activated": False,
            "partial_closed": False,
            "opened_at": now_utc.isoformat(),
            "score": score,
            "agent_breakdown": v2_decision.get("agent_breakdown", {}),
            "session": v2_decision.get("session"),
            "regime": v2_decision.get("regime"),
            "strategy": v2_decision.get("strategy"),
        }

        async with self.blackboard._lock:
            active_trades = self.blackboard._data.setdefault("active_trades", {})
            active_trades[result.order] = trade_record
            # Incrémenter le compteur journalier
            self.blackboard._data["meta"]["daily_trade_count"] = \
                self.blackboard._data["meta"].get("daily_trade_count", 0) + 1

        # Mise à jour du cooldown
        self._last_trade_time = now_utc

        # Nettoyage du signal
        await self.blackboard.write("trade_signals", {})

        # Notification Telegram d'entrée
        rr_ratio = abs(broker_tp - result.price) / abs(result.price - sl) if abs(result.price - sl) > 0 else 0
        await send_telegram_notification(
            self.blackboard,
            f"🎯 *TRADE OUVERT* — {action} {MT5_SYMBOL}\n"
            f"🔹 Ticket: `{result.order}`\n"
            f"🔹 Entrée: `{result.price:.2f}`\n"
            f"🔹 SL: `{sl:.2f}` | TP1: `{tp1:.2f}` | TP2: `{broker_tp:.2f}`\n"
            f"🔹 Lots: `{volume}` | R:R: `{rr_ratio:.1f}`\n"
            f"🔹 Score V2: `{score:.0f}/100`"
        )

        return True

    # ─────────────────────────────────────────────────────────────────────────
    # GESTION DES TRADES ACTIFS (BreakEven + Partial Close + Trailing Stop)
    # ─────────────────────────────────────────────────────────────────────────

    async def manage_active_trades(self) -> None:
        """
        Surveille les trades ouverts :
        1. Nettoyage des trades clôturés par le broker
        2. Cloture partielle 50% au TP1 reel + Break-Even
        3. Trailing Stop ATR-based après Break-Even (feature new)
        4. Tracking du PnL flottant dans le blackboard
        """
        active_trades = self.blackboard.read_sync("active_trades")
        if not active_trades:
            return

        # Guard tick (BUG#4 fix)
        tick = self._get_valid_tick()
        if tick is None:
            return

        current_bid = tick["bid"]
        current_ask = tick["ask"]
        atr = self.blackboard.read_sync("market_data.atr_14") or 0.0
        total_floating_pnl = 0.0

        for ticket, trade in list(active_trades.items()):
            # ── Vérification si toujours ouvert ──────────────────────────────
            position = await asyncio.to_thread(mt5.positions_get, ticket=ticket)
            if position is None or len(position) == 0:
                # Trade clôturé → calcul du PnL réalisé et notification
                await self._on_trade_closed(ticket, trade)
                async with self.blackboard._lock:
                    self.blackboard._data["active_trades"].pop(ticket, None)
                continue

            pos = position[0]
            current_price = current_bid if trade["type"] == "BUY" else current_ask
            entry = trade["entry_price"]
            original_sl = trade["original_sl"]
            tp = trade["tp"]
            tp1 = trade.get("tp1", tp)

            # PnL flottant de ce trade
            floating_pnl = pos.profit
            total_floating_pnl += floating_pnl

            # LOGIQUE 1 : Partial Close 50% + BreakEven au TP1 reel
            if not trade.get("partial_closed", False):
                tp1_reached = (
                    (trade["type"] == "BUY" and current_price >= tp1) or
                    (trade["type"] == "SELL" and current_price <= tp1)
                )

                if tp1_reached:
                    self.logger.info(
                        f"TP1 atteint sur ticket {ticket} ({current_price:.2f} / {tp1:.2f}) - "
                        "Partial Close + BreakEven"
                    )

                    close_type = mt5.ORDER_TYPE_SELL if trade["type"] == "BUY" else mt5.ORDER_TYPE_BUY
                    half_volume = max(0.01, round(pos.volume * (PARTIAL_CLOSE_PERCENT / 100.0), 2))
                    close_price = tick["bid"] if close_type == mt5.ORDER_TYPE_SELL else tick["ask"]
                    partial_ok = False

                    if close_price > 0:
                        close_req = {
                            "action": mt5.TRADE_ACTION_DEAL,
                            "position": ticket,
                            "symbol": SYMBOL,
                            "volume": half_volume,
                            "type": close_type,
                            "price": close_price,
                            "deviation": 20,
                            "magic": MAGIC_NUMBER,
                            "comment": f"GoldSniper Partial TP1 {PARTIAL_CLOSE_PERCENT}%",
                            "type_time": mt5.ORDER_TIME_GTC,
                            "type_filling": mt5.ORDER_FILLING_IOC,
                        }
                        close_res = await asyncio.to_thread(mt5.order_send, close_req)
                        if close_res and close_res.retcode == mt5.TRADE_RETCODE_DONE:
                            partial_ok = True
                            self.logger.trade(
                                f"Partial Close TP1 {PARTIAL_CLOSE_PERCENT}% ({half_volume} lots) sur {ticket}"
                            )
                            await send_telegram_notification(
                                self.blackboard,
                                f"📊 *CLÔTURE PARTIELLE TP1* — {trade['type']} {MT5_SYMBOL}\n"
                                f"🔹 Ticket: `{ticket}` | {PARTIAL_CLOSE_PERCENT}% = `{half_volume}` lots\n"
                                f"🔹 TP1: `{tp1:.2f}` | Prix: `{close_price:.2f}`\n"
                                f"🔹 PnL partiel: `{floating_pnl/2:.2f} USD`\n"
                                f"🔹 Break-Even active -> Trade Free Risk"
                            )
                        else:
                            err = getattr(close_res, 'retcode', 'UNKNOWN')
                            self.logger.error(f"Partial Close TP1 echoue sur {ticket} - Code: {err}")

                    if partial_ok:
                        be_req = {
                            "action": mt5.TRADE_ACTION_SLTP,
                            "position": ticket,
                            "symbol": SYMBOL,
                            "sl": float(entry),
                            "tp": float(tp),
                        }
                        res = await asyncio.to_thread(mt5.order_send, be_req)
                        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                            self.logger.trade(f"BreakEven active sur ticket {ticket}")
                            async with self.blackboard._lock:
                                t = self.blackboard._data["active_trades"].get(ticket)
                                if t:
                                    t["breakeven_activated"] = True
                                    t["partial_closed"] = True
                                    t["current_sl"] = entry
                                    t["partial_closed_at"] = datetime.now(timezone.utc).isoformat()
                        else:
                            err = getattr(res, 'retcode', 'UNKNOWN')
                            self.logger.error(f"BreakEven echoue sur {ticket} - Code: {err}")

            # ── LOGIQUE 2 : Trailing Stop ATR-based (après BreakEven) ─────────
            elif trade.get("partial_closed", False) and trade.get("breakeven_activated", False) and atr > 0:
                current_sl = trade["current_sl"]
                # On trail à 1× ATR du prix courant
                trailing_distance = atr * 1.0

                if trade["type"] == "BUY":
                    new_sl = current_bid - trailing_distance
                    # Ne déplacer le SL que s'il améliore la position
                    if new_sl > current_sl:
                        await self._modify_sl(ticket, new_sl, tp, trade, "TRAILING_BUY")
                else:  # SELL
                    new_sl = current_ask + trailing_distance
                    if new_sl < current_sl:
                        await self._modify_sl(ticket, new_sl, tp, trade, "TRAILING_SELL")

        # ── Mise à jour PnL flottant dans le Blackboard ─────────────────────
        async with self.blackboard._lock:
            daily_stats = self.blackboard._data.get("daily_stats", {})
            daily_stats["floating_pnl"] = round(total_floating_pnl, 2)
            self.blackboard._data["daily_stats"] = daily_stats

    # ─────────────────────────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    async def _modify_sl(self, ticket: int, new_sl: float, tp: float, trade: dict, reason: str) -> None:
        """Modifie le SL d'une position ouverte."""
        req = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": SYMBOL,
            "sl": float(new_sl),
            "tp": float(tp),
        }
        res = await asyncio.to_thread(mt5.order_send, req)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            self.logger.debug(f"📈 [{reason}] SL déplacé à {new_sl:.2f} sur ticket {ticket}")
            async with self.blackboard._lock:
                t = self.blackboard._data["active_trades"].get(ticket)
                if t:
                    t["current_sl"] = float(new_sl)

    async def _on_trade_closed(self, ticket: int, trade: dict) -> None:
        """Appelé quand un trade est détecté comme clôturé côté broker."""
        # Récupérer l'historique des deals pour ce ticket
        history = await asyncio.to_thread(
            mt5.history_deals_get, 0, int(datetime.now(timezone.utc).timestamp()) + 1
        )
        pnl = 0.0
        if history:
            ticket_deals = [d for d in history if d.position_id == ticket]
            pnl = sum(d.profit for d in ticket_deals)

        direction = trade.get("type", "?")
        entry = trade.get("entry_price", 0)
        sl = trade.get("original_sl", 0)
        tp = trade.get("tp", 0)
        rr_achieved = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0

        self.logger.trade(f"🗑️ Trade {ticket} clôturé — PnL réalisé: {pnl:.2f} USD")

        # Mise à jour du PnL journalier réalisé
        async with self.blackboard._lock:
            daily = self.blackboard._data.setdefault("daily_stats", {})
            daily["realized_pnl"] = daily.get("realized_pnl", 0.0) + pnl
            daily["trades_closed"] = daily.get("trades_closed", 0) + 1
            closed_record = {
                "ticket": ticket,
                "type": direction,
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "pnl": pnl,
                "outcome": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN"),
                "rr_achieved": rr_achieved,
                "agent_breakdown": trade.get("agent_breakdown", {}),
                "session": trade.get("session"),
                "regime": trade.get("regime"),
                "strategy": trade.get("strategy"),
                "closed_at": datetime.now(timezone.utc).isoformat(),
            }
            self.blackboard._data.setdefault("positions", {}).setdefault("closed_today", []).append(closed_record)

            # Enregistrement dans paper_trading si applicable
            paper = self.blackboard._data.get("paper_trading", {})
            if paper.get("enabled"):
                paper.setdefault("simulated_trades", []).append(dict(closed_record))
                paper["simulated_equity"] = paper.get("simulated_equity", 0) + pnl

        emoji = "✅" if pnl >= 0 else "❌"
        await send_telegram_notification(
            self.blackboard,
            f"{emoji} *TRADE CLÔTURÉ* — {direction} {MT5_SYMBOL}\n"
            f"🔹 Ticket: `{ticket}` | PnL: `{pnl:+.2f} USD`\n"
            f"🔹 Entrée: `{entry:.2f}` | SL: `{sl:.2f}` | TP: `{tp:.2f}`"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # BOUCLE PRINCIPALE
    # ─────────────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self.logger.info("▶️  Trade Manager (Exécuteur) démarré")

        while not self.blackboard.kill_event.is_set():
            try:
                # 1. Vérification d'un nouveau signal
                signal_data = self.blackboard.read_sync("trade_signals")
                if signal_data and "signal" in signal_data:
                    if self.blackboard.get_all().get("control", {}).get("paused", False):
                        self.logger.info("Signal ignore: trading en pause Telegram; gestion positions maintenue.")
                        await self.blackboard.write("trade_signals", {})
                    else:
                        await self.place_order(signal_data)

                # 2. Gestion des trades existants (BE, Trailing, Partial Close)
                await self.manage_active_trades()

                # 3. Heartbeat
                await self.heartbeat()

            except Exception as e:
                self.logger.error(f"❌ Erreur critique dans Trade Manager : {e}")

            # Boucle rapide (0.5s)
            await asyncio.sleep(0.5)

        self.logger.warning("🛑 Trade Manager arrêté.")

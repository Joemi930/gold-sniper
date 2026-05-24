import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from agents.base_agent import BaseAgent
from config import (
    CONSECUTIVE_LOSS_LIMIT,
    CONSECUTIVE_LOSS_PAUSE_HOURS,
    DAILY_LOSS_LIMIT,
    DRAWDOWN_LIMIT,
    PAPER_MODE_RECOVERY_PCT,
    PAPER_SIMULATED_EQUITY,
)
from utils.telegram_notifier import TelegramNotifier


class RiskManager(BaseAgent):
    """Surveille l'equity curve et publie un veto absolu si nécessaire."""

    def __init__(self, blackboard, telegram: TelegramNotifier | None = None):
        super().__init__(blackboard, name="risk_manager")
        self.telegram = telegram
        self.initial_equity = PAPER_SIMULATED_EQUITY
        self.daily_start_equity = PAPER_SIMULATED_EQUITY
        self.consecutive_losses = 0
        self.pause_until: datetime | None = None
        self.paper_mode_forced = False
        self.daily_reset_at: datetime | None = None
        self._last_trades_closed = 0
        self._last_realized_pnl = 0.0
        self._paper_alert_sent = False
        self._veto_alert_sent = False

    async def run(self) -> None:
        """Boucle de surveillance du risque."""
        self.logger.info("▶️  Risk Manager V2 démarré — equity protection active")
        while not self.blackboard.kill_event.is_set():
            try:
                await self.check_equity_protection()
                await self.heartbeat()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.logger.error(f"Erreur Risk Manager V2: {exc}")
            await asyncio.sleep(10)

    async def check_equity_protection(self) -> dict:
        """Vérifie drawdown, paper mode et pause après pertes consécutives."""
        now = datetime.now(timezone.utc)
        current_equity = self._get_current_equity()
        self._reset_daily_window_if_needed(now, current_equity)
        self._sync_consecutive_losses_from_daily_stats()

        daily_loss_pct = self._daily_loss_pct(current_equity)
        veto = False
        reason = "CLEAR"
        pause_active = self.pause_until is not None and now < self.pause_until

        if daily_loss_pct >= DAILY_LOSS_LIMIT and not self.paper_mode_forced:
            self.paper_mode_forced = True
            reason = (
                f"EQUITY_PROTECTION — perte journalière {daily_loss_pct:.2f}% "
                f">= {DAILY_LOSS_LIMIT}%"
            )
            await self._notify_risk_once(
                "paper",
                "PAPER MODE ACTIVÉ",
                f"Perte journalière {daily_loss_pct:.2f}%. Passage en paper trading.",
            )

        if self.paper_mode_forced and daily_loss_pct < DAILY_LOSS_LIMIT - PAPER_MODE_RECOVERY_PCT:
            self.paper_mode_forced = False
            self._paper_alert_sent = False
            reason = "LIVE_RESTORED — récupération suffisante"

        if daily_loss_pct >= DRAWDOWN_LIMIT:
            veto = True
            reason = f"VETO — drawdown {daily_loss_pct:.2f}% >= {DRAWDOWN_LIMIT}%"
            await self._notify_risk_once("veto", "DRAWDOWN LIMIT", reason)

        if self.consecutive_losses >= CONSECUTIVE_LOSS_LIMIT and not pause_active:
            self.pause_until = now + timedelta(hours=CONSECUTIVE_LOSS_PAUSE_HOURS)
            pause_active = True
            reason = f"PAUSE_2H — {self.consecutive_losses} pertes consécutives"
            if self.telegram:
                await self.telegram.notify_consecutive_losses(self.consecutive_losses)

        if pause_active:
            veto = True
            if reason == "CLEAR":
                reason = f"PAUSE_ACTIVE jusqu'à {self.pause_until.isoformat()}"

        await self._publish_state(veto, daily_loss_pct, reason, pause_active)
        return self.blackboard.get_agent("risk_manager")

    async def record_trade_result(self, won: bool) -> None:
        """Met à jour les pertes consécutives après fermeture d'un trade."""
        self.consecutive_losses = 0 if won else self.consecutive_losses + 1
        if self.consecutive_losses >= CONSECUTIVE_LOSS_LIMIT:
            await self._generate_diagnostic_report()

    def _get_current_equity(self) -> float:
        """Lit l'equity depuis le Blackboard, puis MT5, puis paper trading."""
        meta = self.blackboard.get_all().get("meta", {})
        account = meta.get("account_info") or {}
        equity = account.get("equity")
        if equity:
            self.initial_equity = self.initial_equity or float(equity)
            return float(equity)

        try:
            import MetaTrader5 as mt5

            info = mt5.account_info()
            if info and getattr(info, "equity", 0):
                self.initial_equity = self.initial_equity or float(info.equity)
                return float(info.equity)
        except Exception:
            pass

        paper = self.blackboard.get_all().get("paper_trading", {})
        return float(paper.get("simulated_equity", PAPER_SIMULATED_EQUITY))

    def _reset_daily_window_if_needed(self, now: datetime, current_equity: float) -> None:
        """Reset quotidien à 22h UTC."""
        if self.daily_reset_at is None:
            reset_today = now.replace(hour=22, minute=0, second=0, microsecond=0)
            self.daily_reset_at = reset_today if now < reset_today else reset_today + timedelta(days=1)
            self.daily_start_equity = current_equity
            return

        if now >= self.daily_reset_at:
            self.daily_start_equity = current_equity
            self.daily_reset_at = self.daily_reset_at + timedelta(days=1)
            self.consecutive_losses = 0
            self.pause_until = None
            self.paper_mode_forced = False
            self._paper_alert_sent = False
            self._veto_alert_sent = False

    def _daily_loss_pct(self, current_equity: float) -> float:
        """Calcule la perte journalière en pourcentage."""
        if self.daily_start_equity <= 0:
            return 0.0
        return max(0.0, (self.daily_start_equity - current_equity) / self.daily_start_equity * 100)

    def _sync_consecutive_losses_from_daily_stats(self) -> None:
        """Déduit les pertes consécutives des clôtures ajoutées à daily_stats."""
        daily = self.blackboard.get_all().get("daily_stats", {})
        trades_closed = int(daily.get("trades_closed", 0) or 0)
        realized = float(daily.get("realized_pnl", 0.0) or 0.0)

        if trades_closed <= self._last_trades_closed:
            return

        pnl_delta = realized - self._last_realized_pnl
        self.consecutive_losses = 0 if pnl_delta >= 0 else self.consecutive_losses + 1
        self._last_trades_closed = trades_closed
        self._last_realized_pnl = realized

    async def _publish_state(
        self,
        veto: bool,
        daily_loss_pct: float,
        reason: str,
        pause_active: bool,
    ) -> None:
        """Publie l'état risk_manager dans le Blackboard."""
        pause_until = self.pause_until.isoformat() if self.pause_until else None
        await self.blackboard.update_agent(
            "risk_manager",
            {
                "score": 0 if veto else 100,
                "veto": veto,
                "equity_protection_active": self.paper_mode_forced or pause_active or veto,
                "paper_mode_forced": self.paper_mode_forced,
                "consecutive_losses": self.consecutive_losses,
                "daily_loss_pct": round(daily_loss_pct, 3),
                "pause_until": pause_until,
                "trades_today": self.blackboard.get_all().get("meta", {}).get("daily_trade_count", 0),
                "reason": reason,
                "last_updated": datetime.utcnow(),
            },
        )

        async with self.blackboard._lock:
            self.blackboard._data.setdefault("paper_trading", {})["enabled"] = (
                self.blackboard._data.get("paper_trading", {}).get("enabled", False)
                or self.paper_mode_forced
            )
            self.blackboard._data.setdefault("daily_stats", {})["drawdown_halt"] = veto

    async def _notify_risk_once(self, key: str, alert_type: str, details: str) -> None:
        """Envoie une alerte Telegram une seule fois par état."""
        if not self.telegram:
            return
        if key == "paper" and self._paper_alert_sent:
            return
        if key == "veto" and self._veto_alert_sent:
            return

        await self.telegram.notify_risk_alert(alert_type, details)
        if key == "paper":
            self._paper_alert_sent = True
        elif key == "veto":
            self._veto_alert_sent = True

    async def _generate_diagnostic_report(self) -> None:
        """Envoie un rapport de diagnostic après plusieurs pertes."""
        if not self.telegram:
            return

        perf = self.blackboard.get_all().get("performance", {})
        agent_acc: dict[str, Any] = perf.get("agent_accuracy", {})
        lines = [
            "RAPPORT DIAGNOSTIC",
            f"Pertes consécutives : {self.consecutive_losses}",
            "",
            "Précision agents:",
        ]
        for agent_id, data in agent_acc.items():
            lines.append(f"  {agent_id}: {float(data.get('accuracy', 0)) * 100:.0f}%")
        lines.append("")
        lines.append("Vérifier les agents avec précision < 60%.")
        await self.telegram.notify_risk_alert("DIAGNOSTIC APRÈS PERTES", "\n".join(lines))

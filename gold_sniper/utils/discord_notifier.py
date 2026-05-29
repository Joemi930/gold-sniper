"""
Notificateur Discord (REST uniquement — pas de gateway).
Compatible avec pc_manager qui détient la connexion gateway unique.
"""
from __future__ import annotations

import asyncio
import json
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

import config
from utils.logger import get_logger

logger = get_logger()

DISCORD_API = "https://discord.com/api/v10"

COLOR_GREEN = 0x3FB950
COLOR_RED = 0xF85149
COLOR_GOLD = 0xD4A843
COLOR_BLUE = 0x58A6FF
COLOR_GREY = 0x8B949E

# Boutons F1 — custom_id gérés par pc_manager
TRADE_BUTTON_PAUSE = "gs_pause"
TRADE_BUTTON_KILL = "gs_kill"


class DiscordNotifier:
    """Envoie des alertes Discord via l'API REST."""

    def __init__(self, token: str | None = None):
        self.token = token or config.DISCORD_TOKEN
        self._session: aiohttp.ClientSession | None = None

    def _build_ssl_context(self, verify_ssl: bool = True) -> ssl.SSLContext | bool:
        if not verify_ssl:
            return False
        from utils.ssl_bundle import configure_ssl_environment, create_ssl_context

        configure_ssl_environment()
        return create_ssl_context()

    async def start(self) -> None:
        if not self._session or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._build_ssl_context(True))
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Authorization": f"Bot {self.token}"},
                connector=connector,
            )

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _channel_id(self, channel: str) -> int:
        mapping = {
            "alerts": config.DISCORD_ALERTS_CHANNEL,
            "reports": config.DISCORD_REPORTS_CHANNEL,
            "commands": config.DISCORD_COMMANDS_CHANNEL,
            "logs": config.DISCORD_LOGS_CHANNEL or config.DISCORD_REPORTS_CHANNEL,
        }
        return mapping.get(channel, config.DISCORD_ALERTS_CHANNEL)

    async def send(self, message: str, channel: str = "alerts") -> bool:
        return await self.send_embed(
            title="Gold Sniper",
            description=message[:4096],
            color=COLOR_BLUE,
            fields=[],
            channel=channel,
        )

    async def send_embed(
        self,
        title: str,
        description: str,
        color: int,
        fields: list[dict[str, Any]] | None = None,
        channel: str = "alerts",
        components: list[dict] | None = None,
        content: str | None = None,
    ) -> bool:
        if not self.token:
            return False
        cid = self._channel_id(channel)
        if not cid:
            logger.warning("Discord: channel ID manquant")
            return False

        embed: dict[str, Any] = {
            "title": title[:256],
            "description": (description or "")[:4096],
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if fields:
            embed["fields"] = [
                {
                    "name": str(f.get("name", ""))[:256],
                    "value": str(f.get("value", ""))[:1024],
                    "inline": bool(f.get("inline", False)),
                }
                for f in fields[:25]
            ]

        payload: dict[str, Any] = {"embeds": [embed]}
        if content:
            payload["content"] = content[:2000]
        if components:
            payload["components"] = components

        return await self._post_message(cid, payload)

    async def _post_message(self, channel_id: int, payload: dict) -> bool:
        url = f"{DISCORD_API}/channels/{channel_id}/messages"
        try:
            await self.start()
            assert self._session is not None
            return await self._post_with_session(self._session, url, payload)
        except (aiohttp.ClientConnectorCertificateError, ssl.SSLError) as exc:
            logger.warning(f"Discord SSL échoué, retry sans vérif: {exc}")
            await self.stop()
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Authorization": f"Bot {self.token}"},
                connector=aiohttp.TCPConnector(ssl=False),
            ) as session:
                return await self._post_with_session(session, url, payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Discord envoi échoué: {exc}")
            return False

    async def _post_with_session(
        self,
        session: aiohttp.ClientSession,
        url: str,
        payload: dict,
    ) -> bool:
        async with session.post(url, json=payload) as resp:
            if resp.status in (200, 201):
                return True
            body = await resp.text()
            logger.warning(f"Discord API {resp.status}: {_safe_body(body)}")
            return False

    async def send_document(
        self,
        filepath: str | Path,
        caption: str = "",
        channel: str = "reports",
    ) -> bool:
        path = Path(filepath)
        if not path.exists():
            logger.warning(f"Discord send_document: fichier introuvable {path}")
            return False

        max_bytes = 8 * 1024 * 1024
        if path.stat().st_size > max_bytes:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            truncated = "\n".join(lines[-1000:])
            tmp = path.parent / f"{path.stem}_truncated{path.suffix}"
            tmp.write_text(truncated, encoding="utf-8")
            path = tmp

        cid = self._channel_id(channel)
        if not self.token or not cid:
            return False

        await self.start()
        assert self._session is not None
        url = f"{DISCORD_API}/channels/{cid}/messages"
        try:
            data = aiohttp.FormData()
            if caption:
                data.add_field("payload_json", json.dumps({"content": caption[:2000]}))
            data.add_field(
                "files[0]",
                path.read_bytes(),
                filename=path.name,
                content_type="application/octet-stream",
            )
            async with self._session.post(url, data=data) as resp:
                if resp.status in (200, 201):
                    return True
                body = await resp.text()
                logger.warning(f"Discord document {resp.status}: {_safe_body(body)}")
                return False
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"Discord send_document échoué: {exc}")
            return False

    async def notify_signal(
        self,
        direction: str,
        score: float,
        strategy: str,
        session: str,
        regime: str,
        agents_breakdown: dict,
        **kwargs,
    ) -> None:
        # Compat signatures variées (score/direction en kwargs)
        if kwargs.get("agent_breakdown"):
            agents_breakdown = kwargs["agent_breakdown"]
        if "score" in kwargs and not score:
            score = float(kwargs["score"])
        if "direction" in kwargs and not direction:
            direction = kwargs["direction"]
        if kwargs.get("regime"):
            regime = kwargs["regime"]

        dir_label = "🟢 LONG" if str(direction).upper() == "LONG" else "🔴 SHORT"
        fields = [
            {"name": "Score", "value": f"{float(score):.1f}/100", "inline": True},
            {"name": "Stratégie", "value": str(strategy or kwargs.get("strategy", "N/A")), "inline": True},
            {"name": "Session", "value": str(session or kwargs.get("session", "N/A")), "inline": True},
            {"name": "Régime", "value": str(regime), "inline": True},
        ]
        for agent, data in (agents_breakdown or {}).items():
            sc = float((data or {}).get("score", 0))
            fields.append({
                "name": str(agent).upper(),
                "value": f"{sc:.0f}/100",
                "inline": True,
            })

        await self.send_embed(
            title=f"SIGNAL — {dir_label}",
            description=f"Signal validé sur {config.MT5_SYMBOL}",
            color=COLOR_GREEN if str(direction).upper() == "LONG" else COLOR_RED,
            fields=fields,
            channel="alerts",
        )

    def _trade_action_buttons(self) -> list[dict]:
        return [{
            "type": 1,
            "components": [
                {
                    "type": 2,
                    "style": 2,
                    "label": "⏸️ Pause",
                    "custom_id": TRADE_BUTTON_PAUSE,
                },
                {
                    "type": 2,
                    "style": 4,
                    "label": "🔴 Kill",
                    "custom_id": TRADE_BUTTON_KILL,
                },
            ],
        }]

    async def notify_trade_opened(
        self,
        ticket: int | str | None = None,
        direction: str = "",
        entry: float = 0,
        sl: float = 0,
        tp1: float = 0,
        tp2: float | None = None,
        lot: float = 0,
        risk_pct: float | None = None,
        **kwargs,
    ) -> None:
        if kwargs.get("direction"):
            direction = kwargs["direction"]
        if kwargs.get("entry"):
            entry = kwargs["entry"]
        if kwargs.get("sl"):
            sl = kwargs["sl"]
        if kwargs.get("tp1"):
            tp1 = kwargs["tp1"]
        lot = lot or kwargs.get("lot", 0)
        ticket = ticket or kwargs.get("ticket", "—")
        tp2 = tp2 if tp2 is not None else kwargs.get("tp2")
        rr = abs(tp1 - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0
        color = COLOR_GREEN if str(direction).upper() == "LONG" else COLOR_RED
        fields = [
            {"name": "Ticket", "value": str(ticket), "inline": True},
            {"name": "Direction", "value": str(direction), "inline": True},
            {"name": "Entrée", "value": f"{entry:.2f}", "inline": True},
            {"name": "SL", "value": f"{sl:.2f}", "inline": True},
            {"name": "TP1", "value": f"{tp1:.2f}", "inline": True},
        ]
        if tp2 is not None:
            fields.append({"name": "TP2", "value": f"{float(tp2):.2f}", "inline": True})
        fields.extend([
            {"name": "Lot", "value": f"{lot:.2f}", "inline": True},
            {"name": "R:R", "value": f"1:{rr:.1f}", "inline": True},
        ])
        if risk_pct is not None:
            fields.append({"name": "Risque", "value": f"{risk_pct:.2f}%", "inline": True})

        await self.send_embed(
            title="TRADE OUVERT",
            description=f"Position ouverte sur {config.MT5_SYMBOL}",
            color=color,
            fields=fields,
            channel="alerts",
            components=self._trade_action_buttons(),
        )

    async def notify_trade_closed(
        self,
        ticket: int | str | None = None,
        direction: str = "",
        entry: float = 0,
        close_price: float = 0,
        pnl: float = 0,
        pnl_pct: float | None = None,
        reason: str = "",
        **kwargs,
    ) -> None:
        if kwargs.get("direction"):
            direction = kwargs["direction"]
        if "pnl" in kwargs:
            pnl = kwargs["pnl"]
        if kwargs.get("reason"):
            reason = kwargs["reason"]
        win = float(pnl) > 0
        await self.send_embed(
            title=f"TRADE FERMÉ — {'WIN' if win else 'LOSS'}",
            description=str(reason)[:500],
            color=COLOR_GREEN if win else COLOR_RED,
            fields=[
                {"name": "Ticket", "value": str(ticket or "—"), "inline": True},
                {"name": "Direction", "value": str(direction), "inline": True},
                {"name": "Entrée", "value": f"{float(entry):.2f}", "inline": True},
                {"name": "Clôture", "value": f"{float(close_price):.2f}", "inline": True},
                {"name": "P&L", "value": f"{'+' if pnl > 0 else ''}{float(pnl):.2f}$", "inline": True},
                *(
                    [{"name": "P&L %", "value": f"{float(pnl_pct):.2f}%", "inline": True}]
                    if pnl_pct is not None
                    else []
                ),
            ],
            channel="alerts",
        )

    async def notify_exceptional_setup(
        self,
        direction: str,
        score: float,
        conditions: list | str | None = None,
        rr: float | None = None,
        **kwargs,
    ) -> None:
        if kwargs.get("agent_breakdown"):
            conditions = kwargs["agent_breakdown"]
        mention = f"<@{config.DISCORD_USER_ID}> " if config.DISCORD_USER_ID else ""
        cond_text = ""
        if isinstance(conditions, dict):
            cond_text = "\n".join(
                f"• {k}: {float((v or {}).get('score', 0)):.0f}/100"
                for k, v in conditions.items()
            )
        elif isinstance(conditions, list):
            cond_text = "\n".join(f"• {c}" for c in conditions)
        elif conditions:
            cond_text = str(conditions)

        await self.send_embed(
            title="💎 Diamond Setup",
            description="Setup exceptionnel — décision manuelle requise",
            color=COLOR_GOLD,
            fields=[
                {"name": "Direction", "value": str(direction), "inline": True},
                {"name": "Score", "value": f"{float(score):.1f}/100", "inline": True},
                *(
                    [{"name": "R:R", "value": f"{float(rr):.1f}", "inline": True}]
                    if rr is not None
                    else []
                ),
                {"name": "Conditions", "value": cond_text[:1024] or "—", "inline": False},
            ],
            channel="alerts",
            content=mention,
        )

    async def notify_news_alert(
        self,
        event_name: str,
        time_until_min: int | str | None = None,
        currency: str | None = None,
        impact: str | None = None,
        gold_impact_direction: str | None = None,
        **kwargs,
    ) -> None:
        # Compat agent_6: (name, impact, minutes_to, gold_impact)
        if time_until_min is None and kwargs.get("minutes_to") is not None:
            time_until_min = kwargs["minutes_to"]
        if impact is None:
            impact = kwargs.get("impact", "HIGH")
        if gold_impact_direction is None:
            gold_impact_direction = kwargs.get("gold_impact")

        await self.send_embed(
            title="NEWS ALERT",
            description=f"Événement à venir — impact {impact}",
            color=COLOR_RED if impact == "HIGH" else COLOR_GOLD,
            fields=[
                {"name": "Événement", "value": str(event_name)[:256], "inline": False},
                {"name": "Dans", "value": f"{time_until_min} min", "inline": True},
                {"name": "Devise", "value": str(currency or "—"), "inline": True},
                {"name": "Impact or", "value": str(gold_impact_direction or "volatilité attendue"), "inline": False},
            ],
            channel="alerts",
        )

    async def notify_news_result(
        self,
        event_name: str,
        actual: str,
        forecast: str,
        gold_reaction_pips: float | str | None = None,
        **kwargs,
    ) -> None:
        if gold_reaction_pips is None:
            gold_reaction_pips = kwargs.get("gold_impact", "—")
        await self.send_embed(
            title="NEWS RESULT",
            description=str(event_name),
            color=COLOR_BLUE,
            fields=[
                {"name": "Réel", "value": str(actual), "inline": True},
                {"name": "Prévision", "value": str(forecast), "inline": True},
                {"name": "Réaction or", "value": str(gold_reaction_pips), "inline": True},
            ],
            channel="alerts",
        )

    async def notify_news_feed_down(self) -> None:
        await self.send_embed(
            title="NEWS FEED DOWN",
            description="Calendrier indisponible — mode ASSUME HOSTILE",
            color=COLOR_RED,
            fields=[],
            channel="alerts",
        )

    async def notify_risk_alert(
        self,
        reason: str,
        daily_loss_pct: float | None = None,
        consecutive_losses: int | None = None,
        mention_user: bool = True,
        **kwargs,
    ) -> None:
        # Compat risk_manager: (alert_type, details)
        if kwargs.get("details"):
            details = kwargs["details"]
            reason = f"{reason}\n{details}"
        content = f"<@{config.DISCORD_USER_ID}> ⚠️ " if mention_user and config.DISCORD_USER_ID else ""
        fields = [{"name": "Raison", "value": str(reason)[:1024], "inline": False}]
        if daily_loss_pct is not None:
            fields.append({"name": "Perte jour", "value": f"{daily_loss_pct:.2f}%", "inline": True})
        if consecutive_losses is not None:
            fields.append({"name": "Pertes consécutives", "value": str(consecutive_losses), "inline": True})

        await self.send_embed(
            title="ALERTE RISQUE",
            description="Risk Manager",
            color=COLOR_RED,
            fields=fields,
            channel="alerts",
            content=content.strip() or None,
        )

    async def notify_consecutive_losses(self, count: int) -> None:
        await self.notify_risk_alert(
            reason=f"{count} trades perdants consécutifs — pause 2h",
            consecutive_losses=count,
            mention_user=True,
        )

    async def notify_system_start(
        self,
        mode: str,
        symbol: str,
        threshold: float,
        cloudflare_url: str,
    ) -> None:
        await self.send_embed(
            title="Gold Sniper opérationnel",
            description=f"Dashboard: {cloudflare_url}",
            color=COLOR_GREEN,
            fields=[
                {"name": "Mode", "value": mode, "inline": True},
                {"name": "Symbole", "value": symbol, "inline": True},
                {"name": "Seuil", "value": f"{threshold:.0f}/100", "inline": True},
            ],
            channel="alerts",
        )

    async def notify_daily_report(
        self,
        date: str | None = None,
        trades_count: int = 0,
        win_count: int = 0,
        winrate: float | None = None,
        pnl_total: float = 0,
        best_trade: str | None = None,
        worst_trade: str | None = None,
        equity: float = 0,
        **kwargs,
    ) -> None:
        if kwargs.get("trades") is not None:
            trades_count = kwargs["trades"]
        if kwargs.get("wins") is not None:
            win_count = kwargs["wins"]
        if kwargs.get("pnl") is not None:
            pnl_total = kwargs["pnl"]
        if winrate is None and trades_count > 0:
            winrate = win_count / trades_count * 100

        await self.send_embed(
            title="Rapport journalier",
            description=date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            color=COLOR_GOLD,
            fields=[
                {"name": "Trades", "value": str(trades_count), "inline": True},
                {"name": "Wins", "value": f"{win_count} ({winrate:.0f}%)" if winrate is not None else str(win_count), "inline": True},
                {"name": "P&L", "value": f"{float(pnl_total):+.2f}$", "inline": True},
                {"name": "Equity", "value": f"{float(equity):.2f}$", "inline": True},
                *(
                    [{"name": "Meilleur", "value": str(best_trade), "inline": False}]
                    if best_trade
                    else []
                ),
                *(
                    [{"name": "Pire", "value": str(worst_trade), "inline": False}]
                    if worst_trade
                    else []
                ),
            ],
            channel="reports",
        )

    async def notify_weekly_report(
        self,
        week_start: str = "",
        week_end: str = "",
        trades: int = 0,
        winrate: float = 0,
        pnl: float = 0,
        error_patterns: str = "",
        best_strategy: str = "",
        worst_strategy: str = "",
        **kwargs,
    ) -> None:
        await self.send_embed(
            title="Rapport hebdomadaire",
            description=f"{week_start} → {week_end}",
            color=COLOR_GOLD,
            fields=[
                {"name": "Trades", "value": str(trades), "inline": True},
                {"name": "Winrate", "value": f"{winrate:.0f}%", "inline": True},
                {"name": "P&L", "value": f"{pnl:+.2f}$", "inline": True},
                {"name": "Erreurs", "value": (error_patterns or "—")[:1024], "inline": False},
                {"name": "Meilleure stratégie", "value": best_strategy or "—", "inline": True},
                {"name": "Pire stratégie", "value": worst_strategy or "—", "inline": True},
            ],
            channel="reports",
        )


def _notifier_from_config() -> DiscordNotifier:
    return DiscordNotifier(config.DISCORD_TOKEN)


def _safe_body(body: str) -> str:
    if config.DISCORD_TOKEN and config.DISCORD_TOKEN in body:
        return body.replace(config.DISCORD_TOKEN, "***")
    return body[:200]


async def send_discord_notification(blackboard, message: str) -> None:
    """Wrapper non-bloquant compatible avec l'ancien send_telegram_notification."""
    try:
        notifications = blackboard.read_sync("notifications") if blackboard else {}
        if notifications and not notifications.get("discord_enabled", config.DISCORD_ENABLED):
            return
        notifier = _notifier_from_config()
        await notifier.send(message, channel="alerts")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"Discord wrapper échoué: {exc}")


async def send_eod_report(blackboard) -> None:
    try:
        daily = blackboard.read_sync("daily_stats") or {}
        meta = blackboard.read_sync("meta") or {}
        realized = float(daily.get("realized_pnl", 0.0) or 0.0)
        floating = float(daily.get("floating_pnl", 0.0) or 0.0)
        trades = int(meta.get("daily_trade_count", 0) or 0)
        wins = int(daily.get("winning_trades", daily.get("wins", 0)) or 0)
        equity = float((meta.get("account_info") or {}).get("equity", 0.0) or 0.0)
        notifier = _notifier_from_config()
        await notifier.notify_daily_report(
            trades_count=trades,
            win_count=wins,
            pnl_total=realized + floating,
            equity=equity,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(f"EOD Discord report échoué: {exc}")

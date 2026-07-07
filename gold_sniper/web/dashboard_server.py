import asyncio
import contextlib
import json
import re
import time
from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from aiohttp import WSMsgType, web

from config import (
    CLOUDFLARE_ENABLED,
    CLOUDFLARE_TUNNEL_TIMEOUT,
    CLOUDFLARED_PATH,
    DASHBOARD_ENABLED,
    DASHBOARD_PUBLIC,
    DASHBOARD_PORT,
    DASHBOARD_TOKEN,
)
from core.blackboard import BLACKBOARD
from core.visual_layers import VISUAL_LAYERS
from utils.logger import get_logger
from utils.discord_notifier import _notifier_from_config


HTML_PATH = Path(__file__).parent / "dashboard.html"
ASSETS_PATH = Path(__file__).parent / "assets"
CLOUDFLARE_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
BEARER_TOKEN_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
SECRET_ASSIGNMENT_RE = re.compile(
    r"\b([A-Z0-9_]*(?:TOKEN|PASSWORD|SECRET|API_KEY|ACCOUNT|SERVER)[A-Z0-9_]*)=([^\s,;]+)",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "token",
    "password",
    "secret",
    "authorization",
    "cookie",
    "credential",
    "api_key",
)
SENSITIVE_KEYS = {
    "account",
    "account_info",
    "account_id",
    "login",
    "server",
    "ticket",
    "cloudflare_url",
    "path",
    "terminal_path",
    "mt5_account",
    "mt5_password",
    "mt5_server",
    "discord_token",
    "finnhub_token",
    "fmp_token",
}
_cloudflare_process = None
_dashboard_runner = None
_dashboard_public_url: str | None = None


@web.middleware
async def dashboard_auth_middleware(request: web.Request, handler) -> web.StreamResponse:
    # Les avatars et logos sont des ressources publiques non sensibles. Les
    # protéger par le token casse les balises <img> lorsque l'URL est nettoyée
    # dans l'historique du navigateur après le premier chargement.
    if request.path.startswith("/assets/"):
        response = await handler(request)
        response.headers.setdefault("Cache-Control", "public, max-age=86400, immutable")
        return response
    denied = _dashboard_access_denied(request)
    if denied is not None:
        return denied
    return await handler(request)


def create_dashboard_app(blackboard=BLACKBOARD) -> web.Application:
    app = web.Application(middlewares=[dashboard_auth_middleware])
    app["blackboard"] = blackboard
    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/trades", handle_trades)
    app.router.add_get("/api/agents", handle_agents)
    app.router.add_get("/api/candles", handle_candles_history)
    app.router.add_get("/ws", websocket_handler)
    if ASSETS_PATH.exists():
        app.router.add_static("/assets", ASSETS_PATH, show_index=False)
    return app


async def handle_dashboard(request: web.Request) -> web.StreamResponse:
    if HTML_PATH.exists():
        return web.FileResponse(HTML_PATH)
    return web.Response(text="Dashboard HTML missing", status=404)


async def handle_state(request: web.Request) -> web.Response:
    blackboard = request.app["blackboard"]
    raw_data = blackboard.get_all()
    data = sanitize_for_json(raw_data)
    data["market"] = build_dashboard_market_state(raw_data)
    data["agents"] = build_dashboard_agents_state(raw_data)
    data["portfolio"] = build_dashboard_portfolio_state(raw_data)
    data["visual_layers"] = VISUAL_LAYERS.get_all_as_dict()
    return json_response(data)


async def handle_trades(request: web.Request) -> web.Response:
    data = request.app["blackboard"].get_all()
    return json_response(build_trades_payload(data))


async def handle_agents(request: web.Request) -> web.Response:
    data = request.app["blackboard"].get_all()
    return json_response(build_agents_payload(data))


def build_dashboard_market_state(data: dict[str, Any]) -> dict[str, Any]:
    market = dict(data.get("market", {}) or {})
    current_tick = dict((data.get("market_data", {}) or {}).get("current_tick", {}) or {})
    bid = _float(current_tick.get("bid", market.get("bid", 0.0)))
    ask = _float(current_tick.get("ask", market.get("ask", 0.0)))

    if market.get("bid") is None and bid:
        market["bid"] = bid
    if market.get("ask") is None and ask:
        market["ask"] = ask
    if not market.get("current_price") and bid and ask:
        market["current_price"] = (bid + ask) / 2
    if not market.get("spread_points"):
        spread = current_tick.get("spread_points")
        if spread is None:
            spread = (market.get("spread_monitor") or {}).get("spread")
        market["spread_points"] = _float(spread)
    return sanitize_for_json(market)


def build_dashboard_agents_state(data: dict[str, Any]) -> dict[str, Any]:
    agents = data.get("agents", {}) or {}
    results = data.get("agent_results", {}) or {}
    merged: dict[str, dict[str, Any]] = {}

    for idx in range(1, 8):
        agent_id = f"agent_{idx}"
        raw = dict(agents.get(agent_id, {}) or {})
        result_data = sanitize_for_json(results.get(agent_id)) if results.get(agent_id) else {}

        if result_data:
            raw["score"] = _float(result_data.get("score", raw.get("score", 0.0)))
            raw["direction"] = result_data.get("direction", raw.get("direction"))
            raw["reason"] = result_data.get("reason", raw.get("reason", ""))
            raw["hard_filter_pass"] = bool(result_data.get("hard_filter_pass", raw.get("hard_filter_pass", True)))
            raw["veto"] = bool(result_data.get("veto", raw.get("veto", False)))
            raw["last_updated"] = result_data.get("timestamp", raw.get("last_updated"))
            if isinstance(result_data.get("payload"), dict):
                raw.setdefault("payload", result_data["payload"])
        merged[agent_id] = sanitize_for_json(raw)

    return merged


def build_dashboard_portfolio_state(data: dict[str, Any]) -> dict[str, float]:
    meta = data.get("meta", {}) or {}
    account = meta.get("account_info", {}) or {}
    performance = data.get("performance", {}) or {}
    daily = data.get("daily_stats", {}) or {}
    return {
        "balance": _float(account.get("balance", performance.get("balance", 0.0))),
        "equity": _float(account.get("equity", performance.get("equity", 0.0))),
        "margin": _float(account.get("margin", 0.0)),
        "free_margin": _float(account.get("margin_free", account.get("free_margin", 0.0))),
        "daily_pnl": _float(
            performance.get(
                "daily_pnl",
                _float(daily.get("realized_pnl", 0.0)) + _float(daily.get("floating_pnl", 0.0)),
            )
        ),
    }


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    blackboard = request.app["blackboard"]
    ws = web.WebSocketResponse(heartbeat=10, compress=True)
    await ws.prepare(request)
    update_event = blackboard.dashboard_update_event
    send_lock = asyncio.Lock()

    async def _send_text(payload: str) -> None:
        async with send_lock:
            if not ws.closed:
                await ws.send_str(payload)

    async def _receive_latency_probes() -> None:
        async for message in ws:
            if message.type == WSMsgType.TEXT:
                try:
                    probe = json.loads(message.data)
                except (TypeError, json.JSONDecodeError):
                    continue
                if probe.get("type") == "ping":
                    await _send_text(
                        json.dumps(
                            {
                                "type": "pong",
                                "nonce": str(probe.get("nonce", "")),
                                "server_ts_ms": int(time.time() * 1000),
                            }
                        )
                    )
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                break

    def _build_payload() -> str:
        raw_data = blackboard.get_all()
        dashboard_market = build_dashboard_market_state(raw_data)
        dashboard_agents = build_dashboard_agents_state(raw_data)
        dashboard_data = {
            "market": dashboard_market,
            "market_data": sanitize_for_json(raw_data.get("market_data", {}).get("current_tick", {})),
            "agents": dashboard_agents,
            "orchestrator": sanitize_for_json(raw_data.get("orchestrator", {})),
            "performance": sanitize_for_json(raw_data.get("performance", {})),
            "portfolio": build_dashboard_portfolio_state(raw_data),
        }
        payload = redact_dashboard_payload(
            sanitize_for_json(
                {
                    "type": "full_state",
                    "data": dashboard_data,
                    "state": build_state_summary(raw_data),
                    "trades": build_trades_payload(raw_data),
                    "agents": build_agents_payload(raw_data),
                    "logs": read_recent_logs(limit=40),
                    "ts": int(time.time()),
                    "ts_ms": int(time.time() * 1000),
                }
            )
        )
        return json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        )

    receiver_task = asyncio.create_task(_receive_latency_probes())
    try:
        # Envoi immediat a la connexion
        await _send_text(_build_payload())

        while not ws.closed:
            # On efface AVANT d'attendre pour ne pas rater les mises a jour
            # qui arrivent pendant l'envoi precedent.
            update_event.clear()
            try:
                await asyncio.wait_for(update_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass  # Fallback 500 ms pour une interface plus reactive.
            if not ws.closed:
                await _send_text(_build_payload())
    except asyncio.CancelledError:
        raise
    except Exception:
        get_logger().warning("Dashboard websocket ferme sur erreur non bloquante.")
    finally:
        receiver_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await receiver_task
    return ws


async def handle_candles_history(request: web.Request) -> web.Response:
    blackboard = request.app["blackboard"]
    candles = _get_recent_candles_1m(blackboard, limit=500)
    return json_response({"candles": candles})


def _get_recent_candles_1m(blackboard=BLACKBOARD, limit: int = 200) -> list[dict[str, Any]]:
    try:
        market_data = blackboard.get_all().get("market_data", {}) or {}
        candles_store = market_data.get("candles", {}) or {}
        candles_raw = list(candles_store.get("1m", []) or [])[-limit:]

        result = []
        for candle in candles_raw:
            if not isinstance(candle, dict):
                continue
            result.append(
                {
                    "time": _to_unix_time(candle.get("time")),
                    "open": _float(candle.get("open", 0.0)),
                    "high": _float(candle.get("high", 0.0)),
                    "low": _float(candle.get("low", 0.0)),
                    "close": _float(candle.get("close", 0.0)),
                    "volume": _float(candle.get("tick_volume", candle.get("volume", 0.0))),
                }
            )
        return [c for c in result if c["time"] > 0]
    except Exception:
        return []


async def start_dashboard_server(
    blackboard=BLACKBOARD,
    discord_notifier=None,
    launch_cloudflare: bool | None = None,
    process_factory: Callable[..., Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    logger = get_logger()
    if not DASHBOARD_ENABLED:
        logger.info("Dashboard desactive par config.")
        return {"enabled": False, "local_url": None, "public_url": None, "runner": None}
    if DASHBOARD_PUBLIC and not _dashboard_token_configured():
        logger.error("Dashboard public refuse: DASHBOARD_TOKEN manquant.")
        return {
            "enabled": False,
            "local_url": None,
            "public_url": None,
            "runner": None,
            "reason": "dashboard_token_missing",
        }

    app = create_dashboard_app(blackboard)
    runner = web.AppRunner(app)
    await runner.setup()
    bind_host = "0.0.0.0" if DASHBOARD_PUBLIC else "127.0.0.1"
    site = web.TCPSite(runner, bind_host, DASHBOARD_PORT)
    await site.start()
    local_url = f"http://localhost:{DASHBOARD_PORT}"
    logger.info(f"Dashboard Web demarre: {local_url}")

    public_url = None
    should_launch_cloudflare = (
        CLOUDFLARE_ENABLED
        and DASHBOARD_PUBLIC
        and (True if launch_cloudflare is None else bool(launch_cloudflare))
    )
    if should_launch_cloudflare:
        public_url = await start_cloudflare_tunnel(
            DASHBOARD_PORT,
            process_factory=process_factory,
        )

    global _dashboard_runner, _dashboard_public_url
    _dashboard_runner = runner
    _dashboard_public_url = public_url

    return {"enabled": True, "local_url": local_url, "public_url": public_url, "runner": runner}


def is_dashboard_running() -> bool:
    return _dashboard_runner is not None


def get_dashboard_session() -> dict[str, Any]:
    return {"runner": _dashboard_runner, "public_url": _dashboard_public_url}


def build_dashboard_access_url(backend_url: str | None) -> str | None:
    """Return the primary Cloudflare dashboard URL with its access token."""
    if not backend_url:
        return None
    if not DASHBOARD_TOKEN:
        return backend_url
    separator = "&" if "?" in backend_url else "?"
    return f"{backend_url}{separator}token={quote(DASHBOARD_TOKEN, safe='')}"


async def bootstrap_dashboard(blackboard, launch_cloudflare: bool = True) -> str | None:
    """Démarre dashboard + tunnel (une seule fois). Retourne l'URL publique si obtenue."""
    if is_dashboard_running():
        return _dashboard_public_url

    info = await start_dashboard_server(
        blackboard=blackboard,
        launch_cloudflare=launch_cloudflare,
    )
    public_url = info.get("public_url")
    if public_url:
        await blackboard.write("meta.cloudflare_url", public_url)
        async with blackboard._lock:
            blackboard._data.setdefault("meta", {})["dashboard_started"] = True
    return public_url


async def dashboard_loop(blackboard) -> None:
    """Maintient le dashboard ; ne relance pas si déjà démarré via bootstrap_dashboard."""
    global _dashboard_runner, _dashboard_public_url
    runner = _dashboard_runner
    if runner is None:
        await bootstrap_dashboard(blackboard, launch_cloudflare=True)
        runner = _dashboard_runner

    try:
        while not blackboard.kill_event.is_set():
            await asyncio.sleep(1.0)
    finally:
        await stop_cloudflare_tunnel()
        if runner:
            await runner.cleanup()
        _dashboard_runner = None
        _dashboard_public_url = None


async def _spawn_cloudflare_process(
    port: int,
    process_factory: Callable[..., Awaitable[Any]] | None,
) -> Any | None:
    logger = get_logger()
    executable = str(CLOUDFLARED_PATH)
    if not Path(executable).exists():
        logger.warning(f"cloudflared introuvable: {executable}")
        return None
    factory = process_factory or asyncio.create_subprocess_exec
    try:
        global _cloudflare_process
        proc = await factory(
            executable,
            "tunnel",
            "--url",
            f"http://localhost:{port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000,
        )
        _cloudflare_process = proc
        return proc
    except FileNotFoundError:
        logger.warning(f"cloudflared introuvable: {executable}")
        return None
    except Exception as exc:
        logger.warning(f"Cloudflare Tunnel impossible a lancer: {exc}")
        return None


async def start_cloudflare_tunnel(
    port: int = DASHBOARD_PORT,
    process_factory: Callable[..., Awaitable[Any]] | None = None,
    timeout_seconds: float | None = None,
) -> str | None:
    logger = get_logger()
    if not (DASHBOARD_PUBLIC and CLOUDFLARE_ENABLED and _dashboard_token_configured()):
        logger.warning("Cloudflare Tunnel refuse: dashboard public/token non configures.")
        return None
    if timeout_seconds is None:
        timeout_seconds = CLOUDFLARE_TUNNEL_TIMEOUT

    from utils.cloudflared_manager import cleanup_before_tunnel

    await asyncio.to_thread(cleanup_before_tunnel, port, 1.0)

    for attempt in range(1, 4):
        if attempt > 1:
            logger.warning("Cloudflare: nouvelle tentative tunnel (%s/3)", attempt)
            await stop_cloudflare_tunnel()
            await asyncio.to_thread(cleanup_before_tunnel, port, 2.0)

        proc = await _spawn_cloudflare_process(port, process_factory)
        if proc is None:
            continue

        url = await _wait_for_cloudflare_url(proc, timeout_seconds=timeout_seconds)
        if url:
            logger.info(f"Cloudflare Tunnel actif: {url}")
            try:
                from utils.bot_ready import PHASE_CLOUDFLARE_READY, write_bot_ready

                write_bot_ready(url, phase=PHASE_CLOUDFLARE_READY)
            except Exception as exc:
                logger.warning(f"Ecriture bot_ready.json impossible: {exc}")
            return url

        logger.warning(
            "Cloudflare tentative %s: URL publique non detectee en %ss",
            attempt,
            int(timeout_seconds),
        )

    try:
        from utils.bot_ready import PHASE_CLOUDFLARE_FAILED, write_bot_ready

        write_bot_ready(None, phase=PHASE_CLOUDFLARE_FAILED)
    except Exception:
        pass
    return None


async def stop_cloudflare_tunnel() -> None:
    global _cloudflare_process
    proc = _cloudflare_process
    _cloudflare_process = None
    if proc and getattr(proc, "returncode", None) is None:
        terminate = getattr(proc, "terminate", None)
        if terminate:
            terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except Exception:
                kill = getattr(proc, "kill", None)
                if kill:
                    kill()
    from utils.cloudflared_manager import stop_cloudflared_processes

    await asyncio.to_thread(stop_cloudflared_processes, DASHBOARD_PORT, include_listeners=True)


async def _wait_for_cloudflare_url(
    proc,
    timeout_seconds: float | None = None,
) -> str | None:
    if timeout_seconds is None:
        timeout_seconds = CLOUDFLARE_TUNNEL_TIMEOUT
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    streams = [stream for stream in (getattr(proc, "stderr", None), getattr(proc, "stdout", None)) if stream]
    while streams and asyncio.get_running_loop().time() < deadline:
        tasks = [asyncio.create_task(stream.readline()) for stream in streams]
        done, pending = await asyncio.wait(tasks, timeout=1.0, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            line = (task.result() or b"").decode("utf-8", errors="ignore")
            match = CLOUDFLARE_URL_RE.search(line)
            if match:
                return match.group(0)
    return None


def build_agents_payload(data: dict[str, Any]) -> dict[str, Any]:
    agents = data.get("agents", {}) or {}
    results = data.get("agent_results", {}) or {}
    payload = []
    for idx in range(1, 8):
        agent_id = f"agent_{idx}"
        raw = agents.get(agent_id) or {}
        result = results.get(agent_id)
        result_data = sanitize_for_json(result) if result else {}
        score = _float(raw.get("score", result_data.get("score", 0.0)))
        reason = raw.get("reason") or result_data.get("reason") or result_data.get("explanation") or ""
        payload.append(
            {
                "id": agent_id,
                "label": f"A{idx}",
                "name": AGENT_NAMES.get(agent_id, agent_id),
                "score": score,
                "status": score_status(score),
                "reason": str(reason)[:180],
                "veto": bool(raw.get("veto", result_data.get("veto", False))),
                "hard_filter_pass": bool(raw.get("hard_filter_pass", result_data.get("hard_filter_pass", True))),
                "updated_at": raw.get("updated_at") or result_data.get("timestamp"),
            }
        )
    return {"agents": payload, "updated_at": datetime.now(timezone.utc).isoformat()}


def build_state_summary(data: dict[str, Any]) -> dict[str, Any]:
    market_data = data.get("market_data", {}) or {}
    return sanitize_for_json(
        {
            "meta": data.get("meta", {}),
            "control": data.get("control", {}),
            "market": data.get("market", {}),
            "market_data": {"current_tick": market_data.get("current_tick", {})},
            "orchestrator": data.get("orchestrator", {}),
            "daily_stats": data.get("daily_stats", {}),
            "memory": data.get("memory", {}),
        }
    )


def build_trades_payload(data: dict[str, Any]) -> dict[str, Any]:
    active = data.get("active_trades", {}) or {}
    market = data.get("market_data", {}) or {}
    tick = market.get("current_tick", {}) or {}
    bid = _float(tick.get("bid", 0.0))
    ask = _float(tick.get("ask", 0.0))
    trades = []
    total_pnl = 0.0
    for ticket, trade in active.items():
        direction = trade.get("type") or trade.get("direction") or "?"
        entry = _float(trade.get("entry_price", trade.get("entry", 0.0)))
        volume = _float(trade.get("volume", trade.get("lot", 0.0)))
        current = bid if direction in {"LONG", "BUY"} else ask
        points = (current - entry) if direction in {"LONG", "BUY"} else (entry - current)
        pnl = _float(trade.get("pnl", trade.get("floating_pnl", points * volume)))
        total_pnl += pnl
        trades.append(
            {
                "ticket": str(ticket),
                "direction": direction,
                "entry": entry,
                "sl": _float(trade.get("current_sl", trade.get("sl", 0.0))),
                "tp1": _float(trade.get("tp1", trade.get("tp", 0.0))),
                "tp2": _float(trade.get("tp2", 0.0)),
                "volume": volume,
                "pnl": pnl,
                "current_price": current,
                "session": trade.get("session"),
                "regime": trade.get("regime"),
                "strategy": trade.get("strategy"),
            }
        )
    return {"open_trades": trades, "open_count": len(trades), "total_pnl": total_pnl}


def read_recent_logs(limit: int = 40) -> list[dict[str, Any]]:
    log_dir = Path("logs")
    if not log_dir.exists():
        return []
    files = sorted(log_dir.glob("gold_sniper_*.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        return []
    lines = files[0].read_text(encoding="utf-8", errors="ignore").splitlines()[-limit:]
    items = []
    for line in lines:
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            items.append({"msg": line})
    return items[-limit:]


def json_response(payload: Any) -> web.Response:
    return web.Response(
        text=json.dumps(redact_dashboard_payload(sanitize_for_json(payload)), ensure_ascii=False, default=str),
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _dashboard_token_configured() -> bool:
    return bool(str(DASHBOARD_TOKEN or "").strip())


def _extract_dashboard_token(request: web.Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return (
        request.headers.get("X-Dashboard-Token", "")
        or request.query.get("token", "")
        or ""
    ).strip()


def _dashboard_access_denied(request: web.Request) -> web.Response | None:
    if not DASHBOARD_PUBLIC:
        return None
    if not _dashboard_token_configured():
        return web.Response(text="Dashboard public mode requires DASHBOARD_TOKEN", status=403)
    if _extract_dashboard_token(request) != DASHBOARD_TOKEN:
        return web.Response(text="Dashboard token required", status=401)
    return None


def redact_dashboard_payload(value: Any, key: str | None = None) -> Any:
    if key and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        return {str(k): redact_dashboard_payload(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_dashboard_payload(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = str(key or "").lower()
    return normalized in SENSITIVE_KEYS or any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _redact_string(value: str) -> str:
    value = CLOUDFLARE_URL_RE.sub(REDACTED, value)
    value = BEARER_TOKEN_RE.sub("Bearer [REDACTED]", value)
    return SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}={REDACTED}", value)


def sanitize_for_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, deque):
        return [sanitize_for_json(item) for item in value]
    if is_dataclass(value):
        return sanitize_for_json(asdict(value))
    if isinstance(value, dict):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(item) for item in value]
    if hasattr(value, "__dict__"):
        return sanitize_for_json(vars(value))
    return str(value)


def score_status(score: float) -> str:
    if score >= 85:
        return "green"
    if score >= 60:
        return "orange"
    return "red"


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _to_unix_time(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp())
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


AGENT_NAMES = {
    "agent_1": "Meteo",
    "agent_2": "Cartographe",
    "agent_3": "Liquidite",
    "agent_4": "Fibonacci",
    "agent_5": "Microscope",
    "agent_6": "Sentinelle",
    "agent_7": "Chronos",
}


async def _main() -> None:
    from core.blackboard import BLACKBOARD as bb

    await start_dashboard_server(blackboard=bb, launch_cloudflare=False)
    print(f"Dashboard local: http://localhost:{DASHBOARD_PORT}")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(_main())

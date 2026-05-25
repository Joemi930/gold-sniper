import asyncio
import json
import re
from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from config import CLOUDFLARE_ENABLED, CLOUDFLARED_PATH, DASHBOARD_ENABLED, DASHBOARD_PORT
from core.blackboard import BLACKBOARD
from utils.logger import get_logger
from utils.telegram_notifier import _notifier_from_config


HTML_PATH = Path(__file__).parent / "dashboard.html"
CLOUDFLARE_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
_cloudflare_process = None


def create_dashboard_app(blackboard=BLACKBOARD) -> web.Application:
    app = web.Application()
    app["blackboard"] = blackboard
    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/trades", handle_trades)
    app.router.add_get("/api/agents", handle_agents)
    app.router.add_get("/ws", websocket_handler)
    return app


async def handle_dashboard(request: web.Request) -> web.StreamResponse:
    if HTML_PATH.exists():
        return web.FileResponse(HTML_PATH)
    return web.Response(text="Dashboard HTML missing", status=404)


async def handle_state(request: web.Request) -> web.Response:
    blackboard = request.app["blackboard"]
    data = sanitize_for_json(blackboard.get_all())
    return json_response(data)


async def handle_trades(request: web.Request) -> web.Response:
    data = request.app["blackboard"].get_all()
    return json_response(build_trades_payload(data))


async def handle_agents(request: web.Request) -> web.Response:
    data = request.app["blackboard"].get_all()
    return json_response(build_agents_payload(data))


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    blackboard = request.app["blackboard"]
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    try:
        while not ws.closed:
            data = blackboard.get_all()
            await ws.send_str(
                json.dumps(
                    {
                        "type": "state",
                        "state": build_state_summary(data),
                        "trades": build_trades_payload(data),
                        "agents": build_agents_payload(data),
                        "logs": read_recent_logs(limit=40),
                        "ts": datetime.utcnow().isoformat(),
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        raise
    except Exception:
        get_logger().warning("Dashboard websocket ferme sur erreur non bloquante.")
    return ws


async def start_dashboard_server(
    blackboard=BLACKBOARD,
    telegram_notifier=None,
    launch_cloudflare: bool | None = None,
    process_factory: Callable[..., Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    logger = get_logger()
    if not DASHBOARD_ENABLED:
        logger.info("Dashboard desactive par config.")
        return {"enabled": False, "local_url": None, "public_url": None, "runner": None}

    app = create_dashboard_app(blackboard)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", DASHBOARD_PORT)
    await site.start()
    local_url = f"http://localhost:{DASHBOARD_PORT}"
    logger.info(f"Dashboard Web demarre: {local_url}")

    public_url = None
    should_launch_cloudflare = CLOUDFLARE_ENABLED if launch_cloudflare is None else launch_cloudflare
    if should_launch_cloudflare:
        notifier = telegram_notifier or _notifier_from_config()
        public_url = await start_cloudflare_tunnel(
            DASHBOARD_PORT,
            telegram_notifier=notifier,
            process_factory=process_factory,
        )

    return {"enabled": True, "local_url": local_url, "public_url": public_url, "runner": runner}


async def dashboard_loop(blackboard) -> None:
    info = await start_dashboard_server(blackboard=blackboard)
    runner = info.get("runner")
    try:
        while not blackboard.kill_event.is_set():
            await asyncio.sleep(1.0)
    finally:
        await stop_cloudflare_tunnel()
        if runner:
            await runner.cleanup()


async def start_cloudflare_tunnel(
    port: int = DASHBOARD_PORT,
    telegram_notifier=None,
    process_factory: Callable[..., Awaitable[Any]] | None = None,
    timeout_seconds: float = 30.0,
) -> str | None:
    logger = get_logger()
    executable = str(CLOUDFLARED_PATH)
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
        )
        _cloudflare_process = proc
    except FileNotFoundError:
        logger.warning(f"cloudflared introuvable: {executable}")
        return None
    except Exception as exc:
        logger.warning(f"Cloudflare Tunnel impossible a lancer: {exc}")
        return None

    url = await _wait_for_cloudflare_url(proc, timeout_seconds=timeout_seconds)
    if url:
        logger.info(f"Cloudflare Tunnel actif: {url}")
        if telegram_notifier:
            await telegram_notifier.send(
                "DASHBOARD GOLD SNIPER EN LIGNE\n"
                f"{url}\n"
                "Acces mobile actif via Cloudflare Tunnel.",
                parse_mode=None,
            )
    else:
        logger.warning("Cloudflare Tunnel lance mais URL publique non detectee.")
    return url


async def stop_cloudflare_tunnel() -> None:
    global _cloudflare_process
    proc = _cloudflare_process
    _cloudflare_process = None
    if not proc or getattr(proc, "returncode", None) is not None:
        return
    terminate = getattr(proc, "terminate", None)
    if terminate is None:
        return
    terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:
        kill = getattr(proc, "kill", None)
        if kill:
            kill()


async def _wait_for_cloudflare_url(proc, timeout_seconds: float = 30.0) -> str | None:
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
    return {"agents": payload, "updated_at": datetime.utcnow().isoformat()}


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
        text=json.dumps(sanitize_for_json(payload), ensure_ascii=False, default=str),
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"},
    )


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

"""Publie automatiquement la coquille Vercel quand l'URL du tunnel change.

Le lien public (DASHBOARD_PERMANENT_URL) ne change jamais ; seul config.js
(window.GS_BACKEND = URL du tunnel courant) est mis a jour, via l'API Vercel.
Ne fait RIEN si VERCEL_TOKEN est absent (fonctionnalite optionnelle, jamais
bloquante pour le moteur).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from utils.logger import get_logger
from utils.ssl_bundle import configure_ssl_environment, create_ssl_context

# SSL Windows (truststore) avant aiohttp — sinon CERTIFICATE_VERIFY_FAILED
# derriere une inspection HTTPS locale (antivirus/proxy).
configure_ssl_environment()

ROOT_DIR = Path(__file__).resolve().parents[1]
SHELL_DIR = ROOT_DIR / "web" / "vercel_shell"
STATE_PATH = ROOT_DIR / "data" / "vercel_publish_state.json"
API_URL = "https://api.vercel.com/v13/deployments"

VERCEL_TOKEN = os.getenv("VERCEL_TOKEN", "").strip()
VERCEL_TEAM_ID = os.getenv("VERCEL_TEAM_ID", "team_SRMsGPZLoZSbu1T4s0RkTxgW").strip()
VERCEL_PROJECT = os.getenv("VERCEL_PROJECT", "gold-sniper-dashboard").strip()


def _last_published_url() -> str:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8")).get("backend_url", "")
    except (OSError, ValueError):
        return ""


def _save_state(url: str) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"backend_url": url}), encoding="utf-8")
    except OSError:
        pass


def _build_files(backend_url: str) -> list[dict] | None:
    index_path = SHELL_DIR / "index.html"
    vjson_path = SHELL_DIR / "vercel.json"
    if not index_path.exists() or not vjson_path.exists():
        return None
    # Token dashboard injecte cote coquille : le backend exige ?token=... (401 sinon).
    from config import DASHBOARD_TOKEN

    config_js = f'window.GS_BACKEND="{backend_url}";\n'
    if DASHBOARD_TOKEN:
        config_js += f'window.GS_TOKEN="{DASHBOARD_TOKEN}";\n'
    return [
        {"file": "index.html", "data": index_path.read_text(encoding="utf-8")},
        {"file": "vercel.json", "data": vjson_path.read_text(encoding="utf-8")},
        {"file": "config.js", "data": config_js},
    ]


async def publish_backend_url_if_changed(backend_url: str | None) -> bool:
    """Redeploie la coquille Vercel si l'URL backend a change. True si publie."""
    logger = get_logger()
    if not VERCEL_TOKEN:
        logger.debug("Vercel publisher inactif (VERCEL_TOKEN absent)")
        return False
    if not backend_url or "trycloudflare.com" not in backend_url:
        return False
    backend_url = backend_url.rstrip("/")
    if backend_url == _last_published_url():
        logger.info("Vercel publisher: URL backend inchangee, pas de redeploiement")
        return False
    files = _build_files(backend_url)
    if files is None:
        logger.warning("Vercel publisher: coquille web/vercel_shell absente")
        return False

    import aiohttp

    def _ssl_ctx():
        # Prefere le magasin de certificats Windows (gere l'inspection HTTPS locale).
        try:
            import ssl

            import truststore

            return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except Exception:
            return create_ssl_context()

    payload = {
        "name": VERCEL_PROJECT,
        "project": VERCEL_PROJECT,
        "target": "production",
        "files": files,
        "projectSettings": {"framework": None},
    }
    params = {"teamId": VERCEL_TEAM_ID, "skipAutoDetectionConfirmation": "1"}
    headers = {"Authorization": f"Bearer {VERCEL_TOKEN}"}
    try:
        connector = aiohttp.TCPConnector(ssl=_ssl_ctx())
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                API_URL, params=params, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                body = await resp.text()
                if resp.status in (200, 201):
                    _save_state(backend_url)
                    logger.info(f"Vercel publisher: config.js mis a jour -> {backend_url}")
                    return True
                logger.warning(
                    f"Vercel publisher: echec HTTP {resp.status} — {body[:200]}"
                )
                return False
    except Exception as exc:
        logger.warning(f"Vercel publisher: erreur reseau — {exc}")
        return False

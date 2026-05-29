"""Contexte SSL pour Python 3.14+ / Windows (Discord, aiohttp)."""
from __future__ import annotations

import os
import ssl


def configure_ssl_environment() -> None:
    """Certificats systeme Windows (truststore) + fallback certifi pour Python 3.14."""
    try:
        import truststore

        truststore.inject_into_ssl()
        return
    except ImportError:
        pass
    try:
        import certifi

        cafile = certifi.where()
        os.environ["SSL_CERT_FILE"] = cafile
        os.environ["REQUESTS_CA_BUNDLE"] = cafile
    except ImportError:
        pass


def ssl_verify_enabled() -> bool:
    return os.getenv("DISCORD_SSL_VERIFY", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def create_ssl_context() -> ssl.SSLContext | bool:
    if not ssl_verify_enabled():
        return False
    try:
        return ssl.create_default_context()
    except Exception:
        try:
            import certifi

            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()


def create_aiohttp_connector():
    import aiohttp

    return aiohttp.TCPConnector(ssl=create_ssl_context())

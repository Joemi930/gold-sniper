"""Tests cibles securite dashboard P0-F."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from core.blackboard import BlackBoard
from web import dashboard_server as dashboard


class TestDashboardSecurity(unittest.IsolatedAsyncioTestCase):
    async def _client(self, blackboard: BlackBoard) -> TestClient:
        client = TestClient(TestServer(dashboard.create_dashboard_app(blackboard)))
        await client.start_server()
        self.addAsyncCleanup(client.close)
        return client

    async def test_dashboard_local_by_default_without_token(self) -> None:
        self.assertFalse(dashboard.DASHBOARD_PUBLIC)
        with patch.object(dashboard, "DASHBOARD_PUBLIC", False), patch.object(dashboard, "DASHBOARD_TOKEN", ""):
            client = await self._client(BlackBoard())
            response = await client.get("/api/state")
            self.assertEqual(response.status, 200)

    async def test_cloudflare_off_by_default(self) -> None:
        self.assertFalse(dashboard.CLOUDFLARE_ENABLED)

    async def test_public_without_token_is_refused_before_bind(self) -> None:
        with (
            patch.object(dashboard, "DASHBOARD_ENABLED", True),
            patch.object(dashboard, "DASHBOARD_PUBLIC", True),
            patch.object(dashboard, "DASHBOARD_TOKEN", ""),
        ):
            info = await dashboard.start_dashboard_server(BlackBoard(), launch_cloudflare=True)
        self.assertFalse(info["enabled"])
        self.assertEqual(info["reason"], "dashboard_token_missing")

    async def test_public_with_token_allows_api(self) -> None:
        with patch.object(dashboard, "DASHBOARD_PUBLIC", True), patch.object(dashboard, "DASHBOARD_TOKEN", "ok"):
            client = await self._client(BlackBoard())
            response = await client.get("/api/state", headers={"X-Dashboard-Token": "ok"})
            self.assertEqual(response.status, 200)

    async def test_public_websocket_requires_token(self) -> None:
        with patch.object(dashboard, "DASHBOARD_PUBLIC", True), patch.object(dashboard, "DASHBOARD_TOKEN", "ok"):
            client = await self._client(BlackBoard())
            with self.assertRaises(WSServerHandshakeError) as ctx:
                await client.ws_connect("/ws")
            self.assertEqual(ctx.exception.status, 401)

    async def test_dashboard_payload_redacts_obvious_secrets(self) -> None:
        blackboard = BlackBoard()
        blackboard._data["meta"] = {
            "discord_token": "SECRET_TOKEN_VALUE",
            "account_info": {"login": 123456, "server": "BrokerLive"},
            "cloudflare_url": "https://abcd.trycloudflare.com",
        }
        with patch.object(dashboard, "DASHBOARD_PUBLIC", False):
            client = await self._client(blackboard)
            response = await client.get("/api/state")
            body = await response.text()
        self.assertNotIn("SECRET_TOKEN_VALUE", body)
        self.assertNotIn("BrokerLive", body)
        self.assertNotIn("123456", body)
        self.assertNotIn("trycloudflare.com", body)
        self.assertIn("[REDACTED]", body)

    async def test_public_assets_do_not_require_dashboard_token(self) -> None:
        with patch.object(dashboard, "DASHBOARD_PUBLIC", True), patch.object(dashboard, "DASHBOARD_TOKEN", "ok"):
            client = await self._client(BlackBoard())
            response = await client.get("/assets/agent_1.webp")
            self.assertEqual(response.status, 200)
            self.assertEqual(response.content_type, "image/webp")

    async def test_portfolio_exposes_balance_without_account_identity(self) -> None:
        blackboard = BlackBoard()
        blackboard._data["meta"] = {
            "account_info": {"login": 123456, "server": "BrokerLive", "balance": 321.45, "equity": 325.67}
        }
        with patch.object(dashboard, "DASHBOARD_PUBLIC", False):
            client = await self._client(blackboard)
            response = await client.get("/api/state")
            body = await response.text()
        self.assertIn('"balance": 321.45', body)
        self.assertIn('"equity": 325.67', body)
        self.assertNotIn("123456", body)
        self.assertNotIn("BrokerLive", body)

    async def test_token_query_sets_refresh_cookie(self) -> None:
        with patch.object(dashboard, "DASHBOARD_PUBLIC", True), patch.object(dashboard, "DASHBOARD_TOKEN", "ok"):
            client = await self._client(BlackBoard())
            first = await client.get("/?token=ok", headers={"X-Forwarded-Proto": "https"})
            self.assertEqual(first.status, 200)
            cookie = first.cookies.get(dashboard.DASHBOARD_COOKIE_NAME)
            self.assertIsNotNone(cookie)
            self.assertEqual(cookie.value, "ok")
            refreshed = await client.get(
                "/",
                headers={"Cookie": f"{dashboard.DASHBOARD_COOKIE_NAME}=ok"},
            )
            self.assertEqual(refreshed.status, 200)

    async def test_dedicated_latency_websocket_returns_pong(self) -> None:
        with patch.object(dashboard, "DASHBOARD_PUBLIC", False):
            client = await self._client(BlackBoard())
            ws = await client.ws_connect("/ws/latency")
            await ws.send_json({"type": "ping", "nonce": "latency"})
            message = await ws.receive_json()
            self.assertEqual(message.get("type"), "pong")
            self.assertEqual(message.get("nonce"), "latency")
            await ws.close()

    async def test_websocket_ping_returns_pong(self) -> None:
        with patch.object(dashboard, "DASHBOARD_PUBLIC", False):
            client = await self._client(BlackBoard())
            ws = await client.ws_connect("/ws")
            await ws.receive_json()
            await ws.send_json({"type": "ping", "nonce": "42"})
            for _ in range(4):
                message = await ws.receive_json()
                if message.get("type") == "pong":
                    self.assertEqual(message.get("nonce"), "42")
                    self.assertIn("server_ts_ms", message)
                    break
            else:
                self.fail("pong websocket non recu")
            await ws.close()


if __name__ == "__main__":
    unittest.main()

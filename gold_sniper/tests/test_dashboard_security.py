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


if __name__ == "__main__":
    unittest.main()

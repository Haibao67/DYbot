import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from dzmm_bot.browser import account_id, account_response
from dzmm_bot.core import create_app
from dzmm_bot.gateway import Gateway, normalize, send_bot
from dzmm_bot.settings import Settings
from dzmm_bot.store import inbox, members, outbox
from dzmm_bot.worker import Worker


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = Settings(database_url="sqlite:///" + str(Path(self.temp.name) / "core.sqlite3"),
                            core_token="c" * 32, admin_token="a" * 32)
        self.app = create_app(self.cfg)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.auth = {"X-Core-Token": self.cfg.core_token}
        self.admin = {"X-Admin-Token": self.cfg.admin_token}
        self.room("g1")

    def room(self, room, kind="group"):
        r = self.client.put("/admin/rooms", headers=self.admin, json={"id": room, "kind": kind})
        self.assertEqual(r.status_code, 200)

    def receive(self, mid="m1", room="g1", text="/加入"):
        return self.client.post("/internal/inbound", headers=self.auth,
                                json={"room": room, "message_id": mid, "sender": "u1", "name": "Alice", "text": text})

    def claim(self):
        return self.client.post("/internal/outbound/claim", headers=self.auth).json()["task"]

    def transition(self, task, action, **kwargs):
        return self.client.post(f"/internal/outbound/{task['id']}/result", headers=self.auth,
                                json={"lease": task["lease"], "action": action, **kwargs})

    def test_auth_isolation_and_atomic_dedupe(self):
        self.assertEqual(self.client.get("/internal/rooms").status_code, 401)
        self.assertEqual(self.client.put("/admin/rooms", headers=self.auth, json={"id": "bad"}).status_code, 401)
        self.assertTrue(self.receive(room="unknown").json()["ignored"])
        self.assertTrue(self.receive().json()["queued"])
        self.assertTrue(self.receive().json()["duplicate"])
        with self.app.state.store.engine.connect() as db:
            self.assertEqual(len(db.execute(select(members)).all()), 1)
            self.assertEqual(len(db.execute(select(inbox)).all()), 1)
            self.assertEqual(len(db.execute(select(outbox)).all()), 1)
        self.room("g2")
        self.receive(room="g2", text="/我的")
        first = self.claim()
        self.transition(first, "begin")
        self.transition(first, "sent", platform_id="platform-1")
        self.assertIn("余额：⨀ 100", self.claim()["text"])

    def test_start_and_help_have_distinct_replies(self):
        self.receive(mid="start", text="/不存在")
        start = self.claim()
        self.transition(start, "begin")
        self.transition(start, "sent", platform_id="start-message")
        self.receive(mid="help", text="/帮助")
        help_task = self.claim()
        self.assertNotEqual(start["text"], help_task["text"])

    def test_lease_recovery_and_uncertain_blocks_only_its_room(self):
        self.receive()
        task = self.claim()
        self.assertIsNone(self.claim())
        with self.app.state.store.engine.begin() as db:
            db.execute(outbox.update().where(outbox.c.id == task["id"]).values(lease_until=time.time() - 1))
        second = self.claim()
        self.assertNotEqual(task["lease"], second["lease"])
        self.assertEqual(self.transition(task, "begin").status_code, 409)
        self.assertEqual(self.transition(second, "begin").status_code, 200)
        with self.app.state.store.engine.begin() as db:
            db.execute(outbox.update().where(outbox.c.id == task["id"]).values(lease_until=time.time() - 1))
        self.receive("m2")
        self.room("g2")
        self.receive("m3", "g2")
        self.assertEqual(self.claim()["room"], "g2")
        status = self.client.get("/admin/status", headers=self.admin).json()
        self.assertEqual(status["outbound"]["uncertain"], 1)
        self.assertEqual(self.client.post(f"/admin/outbound/{task['id']}/resolve", headers=self.admin,
                                         json={"status": "failed"}).status_code, 200)
        self.assertEqual(self.claim()["room"], "g1")

    def test_sent_needs_id_and_no_premature_completion(self):
        self.receive()
        task = self.claim()
        self.assertEqual(self.transition(task, "sent", platform_id="x").status_code, 409)
        self.transition(task, "begin")
        self.assertEqual(self.transition(task, "sent").status_code, 422)
        self.assertEqual(self.transition(task, "sent", platform_id="x").status_code, 200)
        self.assertEqual(self.transition(task, "sent", platform_id="x").status_code, 200)
        self.assertIsNone(self.claim())

    def test_persistence_and_simulated_worker(self):
        self.receive()

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://core",
                                         headers=self.auth) as core:
                async with httpx.AsyncClient() as platform:
                    self.assertTrue(await Worker(self.cfg, core, platform).drain_once())
        asyncio.run(exercise())
        with TestClient(create_app(self.cfg)) as restarted:
            status = restarted.get("/admin/status", headers=self.admin).json()
            self.assertEqual(status["outbound"], {"simulated": 1})
            self.assertTrue(restarted.post("/internal/inbound", headers=self.auth,
                json={"room": "g1", "message_id": "m1", "sender": "u1", "text": "/加入"}).json()["duplicate"])

    def test_private_requires_registration_and_uses_private_kind(self):
        self.assertTrue(self.receive(room="dm1", text="/帮助").json()["ignored"])
        self.room("dm1", "private")
        self.receive(room="dm1", text="/帮助")
        self.assertEqual(self.claim()["kind"], "private")

    def test_live_core_rejects_simulation(self):
        self.cfg.mode = "live"
        self.assertEqual(self.client.post("/admin/simulate", headers=self.admin, json={"text": "/加入"}).status_code, 403)

    def test_retry_is_bounded_and_cooldown_blocks_room(self):
        self.receive()
        for attempt in range(3):
            task = self.claim()
            self.transition(task, "begin")
            self.transition(task, "retry", error="rate_limited")
            self.assertIsNone(self.claim())
            with self.app.state.store.engine.begin() as db:
                db.execute(outbox.update().values(available=time.time() - 1))
        status = self.client.get("/admin/status", headers=self.admin).json()
        self.assertEqual(status["outbound"], {"failed": 1})

    def test_live_worker_private_route_and_uncertain_bot_no_fallback(self):
        self.cfg.mode = "live"
        self.cfg.bot_token = "test-token"
        self.room("dm1", "private")
        self.receive("dm1-message", "dm1", "/帮助")
        self.receive("group-message", "g1", "/帮助")
        gateway = Mock()
        gateway.socket.connected = True
        gateway.send_socket = AsyncMock(return_value={"action": "sent", "platform_id": "dm-message"})

        async def exercise():
            def upstream(request):
                raise httpx.ReadTimeout("unknown result")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://core", headers=self.auth) as core:
                async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as platform:
                    worker = Worker(self.cfg, core, platform, gateway)
                    await worker.drain_once()
                    await worker.drain_once()
        asyncio.run(exercise())
        gateway.send_socket.assert_awaited_once()
        self.assertEqual(gateway.send_socket.call_args.args[0]["kind"], "private")
        self.assertEqual(self.client.get("/admin/status", headers=self.admin).json()["outbound"], {"sent": 1, "uncertain": 1})

    def test_live_worker_explicit_bot_rejection_falls_back(self):
        self.cfg.mode = "live"
        self.cfg.bot_token = "test-token"
        self.receive()
        gateway = Mock()
        gateway.socket.connected = True
        gateway.send_socket = AsyncMock(return_value={"action": "sent", "platform_id": "fallback-message"})

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://core", headers=self.auth) as core:
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={"ok": False}))) as platform:
                    await Worker(self.cfg, core, platform, gateway).drain_once()
        asyncio.run(exercise())
        gateway.send_socket.assert_awaited_once()
        self.assertEqual(self.client.get("/admin/status", headers=self.admin).json()["outbound"], {"sent": 1})


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_room_requires_successful_subscription_ack(self):
        gateway = Gateway(Settings(), None, AsyncMock())
        gateway.socket = Mock(connected=True)
        gateway.socket.call = AsyncMock(return_value={"success": False})
        with self.assertRaises(RuntimeError):
            await gateway.sync_rooms([{"id": "g1"}])
        self.assertNotIn("g1", gateway.joined)
        gateway.socket.call.return_value = {"success": True}
        await gateway.sync_rooms([{"id": "g1"}])
        self.assertIn("g1", gateway.joined)

    async def test_bot_contract_and_ambiguous_responses(self):
        cfg = Settings(bot_token="test-token")
        task = {"room": "g1", "text": "hello"}
        for code, body, expected in [(200, {"ok": True, "result": {"message_id": "m1"}}, "sent"),
                                      (200, {"ok": True}, "uncertain"),
                                      (503, {"ok": False}, "uncertain"),
                                      (403, {"ok": False}, "failed"), (429, {}, "retry")]:
            def handler(request):
                self.assertEqual(request.url.path, "/api/bot/send-message")
                self.assertEqual(request.headers["X-Bot-Token"], "test-token")
                self.assertEqual(json.loads(request.content)["chatroom_id"], "g1")
                return httpx.Response(code, json=body)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                outcome = await send_bot(client, cfg, task)
            self.assertEqual(outcome["action"], expected)
            if expected == "uncertain":
                self.assertNotIn("fallback", outcome)

    async def test_timeout_never_falls_back(self):
        def handler(request):
            raise httpx.ReadTimeout("timeout")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            outcome = await send_bot(client, Settings(), {"room": "g", "text": "hi"})
        self.assertEqual(outcome, {"action": "uncertain", "error": "transport_unknown"})

    async def test_socket_uses_verified_identity_and_ack(self):
        gateway = Gateway(Settings(), None, AsyncMock())
        gateway.own_id = "verified-account"
        gateway.allowed = {"g1"}
        gateway.socket = Mock(connected=True)
        gateway.socket.call = AsyncMock(return_value={"success": True})
        outcome = await gateway.send_socket({"room": "g1", "kind": "private", "text": "hi"})
        self.assertEqual(outcome["action"], "sent")
        payload = gateway.socket.call.call_args.args[1]
        self.assertEqual(payload["message"]["sent_by"], "verified-account")
        self.assertEqual(outcome["platform_id"], payload["message"]["message_id"])

    async def test_event_validation_and_account_parser(self):
        event = {"chatroomId": "g1", "message": {"message_id": "m1", "chatroom_id": "g1",
                 "sent_by": "u1", "sent_at": "2026-09-25T00:00:00Z", "content": {"type": "text", "text": "/加入"}}}
        self.assertIsNotNone(normalize(event, "self", "bot", {"g1"}))
        self.assertIsNone(normalize(event, "u1", "bot", {"g1"}))
        self.assertIsNone(normalize(event, "self", "u1", {"g1"}))
        self.assertIsNone(normalize(event, "self", "bot", {"other"}))
        event["message"]["chatroom_id"] = "different"
        self.assertIsNone(normalize(event, "self", "bot", {"g1"}))
        self.assertEqual(account_id({"result": {"data": {"json": {"id": "u1"}}}}), "u1")
        self.assertIsNone(account_id({"unrelated": {"id": "u1"}}))

    async def test_account_batch_selects_only_current_user(self):
        body = [{"result": {"data": {"json": {"id": "other-record"}}}},
                {"result": {"data": {"json": {"id": "current-user", "isLoggedIn": True}}}}]
        selected = account_response(body, "/api/trpc/chat.listAll,user.getMe")
        self.assertEqual(account_id(selected), "current-user")
        self.assertIsNone(account_response(body, "/api/trpc/user.getMe"))
        session = __import__('dzmm_bot.browser', fromlist=['BrowserSession']).BrowserSession(Settings())
        response = Mock(url="https://www.ivorune.xyz/api/trpc/user.getMe,chat.listAll", status=207)
        response.json = AsyncMock(return_value=[
            {"result": {"data": {"json": {"id": None, "isLoggedIn": False}}}}, body[0]])
        await session.observe_account(response)
        self.assertTrue(session.logged_out)
        self.assertIsNone(session.user_id)


if __name__ == "__main__":
    unittest.main()

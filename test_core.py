import asyncio
import json
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from dzmm_bot.browser import account_id, account_response
from dzmm_bot.core import create_app
from dzmm_bot.gateway import (Gateway, is_length_rejection, normalize,
                              send_bot, split_long_reply)
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
        # Unit tests exercise scheduling in isolation; virtual-time scheduler tests
        # cover the production 1-second global interval without wall-clock sleeps.
        self.app.state.store.outbound_global_interval_seconds = 0
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
        self.assertIn("🪙 余额：100", self.claim()["text"])

    def test_long_reply_is_split_only_by_length_rejection_handler(self):
        text = "\n".join(f"面板行 {i}" for i in range(13))
        parts = split_long_reply(text)
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(part.count("\n") <= 9 for part in parts))
        self.assertEqual("\n".join(parts), text)
        self.assertEqual(split_long_reply("短回复"), ["短回复"])
        self.assertFalse(is_length_rejection({"success": False, "error": "请勿发送重复内容"}))
        self.assertTrue(is_length_rejection({"success": False, "error": "换行太多了"}))

    def test_long_reply_is_enqueued_as_one_original_reply(self):
        panel = "\n".join(f"状态 {i}" for i in range(12))
        with patch("dzmm_bot.store.CommandRouter.dispatch", return_value=panel):
            self.receive(mid="long-panel", text="/牧场")
        first = self.claim()
        self.assertEqual(first["text"], panel)
        self.assertEqual((first["reply_to_message_id"], first["reply_to_text"]), ("long-panel", "/牧场"))
        self.assertIsNone(self.claim())

    def test_start_and_help_have_distinct_replies(self):
        self.receive(mid="start", text="/不存在")
        start = self.claim()
        self.assertEqual((start["reply_to_message_id"], start["reply_to_sender_id"]), ("start", "u1"))
        self.assertEqual(start["reply_to_text"], "/不存在")
        self.transition(start, "begin")
        self.transition(start, "sent", platform_id="start-message")
        self.receive(mid="help", text="/帮助")
        help_task = self.claim()
        self.assertEqual((help_task["reply_to_message_id"], help_task["reply_to_sender_id"]), ("help", "u1"))
        self.assertNotEqual(start["text"], help_task["text"])

    def test_interleaved_players_keep_their_own_reply_context(self):
        self.client.post("/internal/inbound", headers=self.auth, json={
            "room": "g1", "message_id": "alice-1", "sender": "alice", "name": "Alice", "text": "/加入"})
        self.client.post("/internal/inbound", headers=self.auth, json={
            "room": "g1", "message_id": "bob-1", "sender": "bob", "name": "Bob", "text": "/不存在"})
        first, second = self.claim(), None
        self.transition(first, "begin")
        self.transition(first, "sent", platform_id="answer-1")
        second = self.claim()
        self.assertEqual((first["reply_to_message_id"], first["reply_to_sender_id"]), ("alice-1", "alice"))
        self.assertEqual((second["reply_to_message_id"], second["reply_to_sender_id"]), ("bob-1", "bob"))
        self.assertEqual(first["reply_to_text"], "/加入")
        self.assertEqual(second["reply_to_text"], "/不存在")

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

    def test_failure_details_are_persisted_and_exposed_without_message_content(self):
        self.receive(text="/牧场")
        task = self.claim()
        self.transition(task, "begin")
        detail = {"transport": "socket", "platform_code": "LIMIT", "platform_error": "too many lines",
                  "attempted_chars": 12, "attempted_line_breaks": 13}
        response = self.transition(task, "failed", error="rejected", error_detail=detail)
        self.assertEqual(response.status_code, 200)
        failed = self.client.get("/admin/status", headers=self.admin).json()["failed_tasks"][-1]
        self.assertEqual(failed["id"], task["id"])
        recorded = failed["failure_history"][0]
        self.assertEqual(recorded["attempt"], 1)
        self.assertEqual(recorded["task_id"], task["id"])
        self.assertEqual(recorded["category"], "rejected")
        self.assertEqual(recorded["attempted_line_breaks"], 13)
        self.assertEqual(recorded["platform_code"], "LIMIT")
        self.assertNotIn("text", recorded)
        self.assertNotIn("text", failed)
        self.assertNotIn("reply_to_text", failed)

    def test_performance_endpoint_requires_admin_and_bounds_sample(self):
        self.assertEqual(self.client.get("/admin/performance").status_code, 401)
        self.assertEqual(self.client.get("/admin/performance", headers=self.auth).status_code, 401)
        self.assertEqual(self.client.get("/admin/performance?sample_limit=1001", headers=self.admin).status_code, 422)
        empty = self.client.get("/admin/performance", headers=self.admin).json()
        self.assertIsNone(empty["latency"]["sent_end_to_end"]["p50_seconds"])
        self.receive(mid="metric", text="/帮助")
        pending = self.client.get("/admin/performance", headers=self.admin).json()
        self.assertEqual(pending["pending_count"], 1)
        self.assertEqual(pending["channels"]["group"]["pending"], 1)

        self.assertIsNotNone(pending["oldest_pending_seconds"])
        task = self.claim()
        self.transition(task, "begin")
        self.transition(task, "simulated", error="simulation")
        completed = self.client.get("/admin/performance?sample_limit=1", headers=self.admin).json()
        self.assertEqual(completed["latency"]["simulated_end_to_end"]["samples"], 1)
        self.assertEqual(completed["latency"]["sent_end_to_end"]["samples"], 0)
        self.assertNotIn("/帮助", str(completed))

    def test_admin_clear_outbound_requires_auth_and_preserves_audit_rows(self):
        self.receive(mid="queued", text="/帮助")
        self.assertEqual(self.client.post("/admin/outbound/clear").status_code, 401)
        response = self.client.post("/admin/outbound/clear", headers=self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cancelled": {"pending": 1}, "preserved": True})
        with self.app.state.store.engine.connect() as db:
            row = db.execute(select(outbox.c.status, outbox.c.error)).one()
        self.assertEqual(tuple(row), ("cancelled", "operator_queue_cleared"))

    def test_performance_includes_worker_limiter_snapshot_without_message_body(self):
        limiter = {"current_interval_seconds": 4.0, "last_rate_limited_at": 1000.0,
                   "rate_limit_events": 1, "adjustments": 2, "success_streak": 0,
                   "cooldown_remaining_seconds": 35.0}
        response = self.client.post("/internal/heartbeat", headers=self.auth,
                                    json={"state": "connected", "limiter": limiter})
        self.assertEqual(response.status_code, 200)
        data = self.client.get("/admin/performance", headers=self.admin).json()
        self.assertEqual(data["limiter"], limiter)
        self.assertFalse(data["limiter_stale"])
        self.assertNotIn("text", data["limiter"])

    def test_performance_includes_bounded_inbound_latency_without_request_data(self):
        self.receive(mid="latency-private-id", text="/帮助")
        data = self.client.get("/admin/performance", headers=self.admin).json()
        self.assertIn("core_lock_wait", data["inbound_latency"])
        inbound = data["inbound_latency"]["core_inbound"]
        self.assertEqual(inbound["samples"], 1)
        self.assertGreaterEqual(inbound["p50_upper_bound_ms"], 1)
        self.assertEqual(sum(inbound["bucket_counts"]), 1)
        self.assertNotIn("latency-private-id", str(data))
        self.assertNotIn("/帮助", str(data))

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

    def test_retry_outcome_is_terminal_and_queue_continues(self):
        self.receive()
        task = self.claim()
        self.transition(task, "begin")
        self.transition(task, "retry", error="rate_limited")
        self.assertIsNone(self.claim())
        self.receive(mid="next", text="/帮助")
        next_task = self.claim()
        self.assertEqual(next_task["reply_to_message_id"], "next")
        status = self.client.get("/admin/status", headers=self.admin).json()
        self.assertEqual(status["outbound"], {"failed": 1, "leased": 1})

    def test_live_worker_routes_player_replies_through_socket(self):
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
                    for _ in range(2):
                        await worker.drain_once()
        asyncio.run(exercise())
        self.assertEqual(gateway.send_socket.await_count, 2)
        kinds = [call.args[0]["kind"] for call in gateway.send_socket.await_args_list]
        self.assertEqual(kinds.count("group"), 1)
        self.assertEqual(kinds.count("private"), 1)
        self.assertEqual(self.client.get("/admin/status", headers=self.admin).json()["outbound"], {"sent": 2})

    def test_live_worker_routes_player_reply_through_socket(self):
        self.cfg.mode = "live"
        self.cfg.bot_token = "test-token"
        self.receive()
        gateway = Mock()
        gateway.socket.connected = True
        gateway.send_socket = AsyncMock(return_value={"action": "sent", "platform_id": "fallback-message"})

        requests = []
        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://core", headers=self.auth) as core:
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (requests.append(r), httpx.Response(403, json={"ok": False}))[1])) as platform:
                    await Worker(self.cfg, core, platform, gateway).drain_once()
        asyncio.run(exercise())
        gateway.send_socket.assert_awaited_once()
        self.assertEqual(requests, [])
        self.assertEqual(self.client.get("/admin/status", headers=self.admin).json()["outbound"], {"sent": 1})


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_splits_only_after_explicit_length_rejection(self):
        gateway = Gateway(Settings(), None, AsyncMock())
        gateway.limiter.sleeper = AsyncMock()
        gateway.own_id = "bot-user"
        gateway.allowed = {"g1"}
        gateway.socket = Mock(connected=True)
        task = {"room": "g1", "kind": "group", "text": "\n".join(f"line {i}" for i in range(13)),
                "reply_to_message_id": "source", "reply_to_sender_id": "player", "reply_to_text": "/牧场"}
        gateway.socket.call = AsyncMock(side_effect=[
            {"success": False, "error": "换行太多了"},
            {"success": True}, {"success": True},
        ])
        outcome = await gateway.send_socket(task)
        self.assertEqual(outcome["action"], "sent")
        self.assertEqual(gateway.socket.call.await_count, 3)
        for index, call in enumerate(gateway.socket.call.await_args_list):
            content = call.args[1]["message"]["content"]
            if index:
                self.assertLessEqual(content["text"].count("\n"), 9)
            if index == 0:
                self.assertEqual(content["text"], task["text"])
            else:
                self.assertIn(content["text"], split_long_reply(task["text"]))
            self.assertEqual(content["reference"]["id"], "source")

    async def test_long_duplicate_rejection_splits_after_failed_send(self):
        gateway = Gateway(Settings(), None, AsyncMock())
        gateway.limiter.sleeper = AsyncMock()
        gateway.own_id = "bot-user"
        gateway.allowed = {"g1"}
        gateway.socket = Mock(connected=True)
        gateway.socket.call = AsyncMock(side_effect=[
            {"success": False, "error": "请勿发送重复内容"},
            {"success": True}, {"success": True},
        ])
        task = {"room": "g1", "kind": "group", "text": "\n".join(f"line {i}" for i in range(13)),
                "reply_to_message_id": "source", "reply_to_sender_id": "player", "reply_to_text": "/牧场"}
        outcome = await gateway.send_socket(task)
        self.assertEqual(outcome["action"], "sent")
        self.assertEqual(gateway.socket.call.await_count, 3)
        sent_parts = [call.args[1]["message"]["content"] for call in gateway.socket.call.await_args_list[1:]]
        self.assertTrue(all(part["text"].count("\n") <= 9 for part in sent_parts))
        self.assertEqual([part["text"] for part in sent_parts], split_long_reply(task["text"]))
        self.assertTrue(all(part["reference"]["id"] == "source" for part in sent_parts))

    async def test_duplicate_content_is_sent_unchanged_and_not_retried(self):
        gateway = Gateway(Settings(), None, AsyncMock())
        gateway.own_id = "bot-user"
        gateway.allowed = {"g1"}
        gateway.socket = Mock(connected=True)
        gateway.socket.call = AsyncMock(return_value={"success": False, "error": "请勿发送重复内容"})
        task = {"room": "g1", "kind": "group", "text": "same content"}
        outcome = await gateway.send_socket(task)
        self.assertEqual((outcome["action"], outcome["error"]), ("failed", "rejected"))
        self.assertEqual(outcome["error_detail"]["platform_error"], "请勿发送重复内容")
        self.assertNotIn("text", outcome["error_detail"])
        gateway.socket.call.assert_awaited_once()
        sent_text = gateway.socket.call.await_args.args[1]["message"]["content"]["text"]
        self.assertEqual(sent_text, task["text"])
        self.assertEqual(outcome["error_detail"]["attempted_chars"], len(sent_text))

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
                payload = json.loads(request.content)
                self.assertEqual(payload["chatroom_id"], "g1")
                self.assertEqual(payload["content"], task["text"])
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
        self.assertEqual((outcome["action"], outcome["error"]), ("uncertain", "transport_unknown"))
        self.assertEqual(outcome["error_detail"]["transport"], "bot_http")
        self.assertEqual(outcome["error_detail"]["exception_type"], "ReadTimeout")
        self.assertNotIn("fallback", outcome)

    async def test_socket_uses_verified_identity_and_ack(self):
        gateway = Gateway(Settings(), None, AsyncMock())
        gateway.own_id = "verified-account"
        gateway.allowed = {"g1"}
        gateway.socket = Mock(connected=True)
        gateway.socket.call = AsyncMock(return_value={"success": True})
        outcome = await gateway.send_socket({"room": "g1", "kind": "private", "text": "hi",
            "reply_to_message_id": "original-1", "reply_to_sender_id": "player-1", "reply_to_text": "/帮助"})
        self.assertEqual(outcome["action"], "sent")
        payload = gateway.socket.call.call_args.args[1]
        self.assertEqual(payload["message"]["sent_by"], "verified-account")
        self.assertEqual(payload["message"]["content"]["reference"], {
            "id": "original-1", "sentBy": "player-1", "content": {"type": "text", "text": "/帮助"}})
        self.assertEqual(payload["message"]["content"]["text"], "hi")
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

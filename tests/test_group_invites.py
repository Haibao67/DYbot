import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from sqlalchemy import select

from dzmm_bot.core import create_app
from dzmm_bot.gateway import Gateway, normalize_invite_card
from dzmm_bot.invitations import invite_code, private_room_ids
from dzmm_bot.persistence.schema import pending_group_invites
from dzmm_bot.settings import Settings
from dzmm_bot.store import rooms


class InviteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cfg = Settings(database_url="sqlite:///" + str(Path(self.temp.name) / "core.sqlite3"),
                       core_token="c" * 32, admin_token="a" * 32)
        app = create_app(cfg)
        self.client = TestClient(app)
        self.client.__enter__()
        app.state.store.outbound_global_interval_seconds = 0
        self.addCleanup(self.client.__exit__, None, None, None)
        self.core = {"X-Core-Token": cfg.core_token}
        self.admin = {"X-Admin-Token": cfg.admin_token}

    def candidate(self, mid="m1", room="private-1"):
        return {"room": room, "message_id": mid, "sender": "inviter-1", "name": "玩家",
                "text": "https://www.dzmm.io/invite/C66OxXko", "group_name": "冬宴马场"}

    def test_official_link_and_private_room_shape(self):
        self.assertEqual(invite_code(self.candidate()["text"]), "C66OxXko")
        for text in ("https://evil.example/invite/C66OxXko",
                     "https://www.dzmm.io/invite/C66OxXko.evil", "/invite/C66OxXko"):
            self.assertIsNone(invite_code(text))
        body = {"result": {"data": {"json": [
            {"type": "user", "data": {"chatType": "one_on_one", "chatroomId": "dm1"}},
            {"type": "group", "data": {"chatType": "group", "chatroomId": "g1"}}]}}}
        self.assertEqual(private_room_ids(body), {"dm1"})

    def test_record_approve_join_and_native_reply(self):
        self.assertEqual(self.client.post("/internal/invites", json=self.candidate()).status_code, 401)
        first = self.client.post("/internal/invites", headers=self.core, json=self.candidate())
        self.assertTrue(first.json()["pending"])
        again = self.client.post("/internal/invites", headers=self.core, json=self.candidate())
        self.assertTrue(again.json()["duplicate"])
        task = self.client.post("/internal/outbound/claim", headers=self.core).json()["task"]
        self.assertEqual(task["kind"], "private")
        self.assertEqual(task["reply_to_message_id"], "m1")
        self.assertEqual(task["reply_to_text"], self.candidate()["text"])
        self.assertIn("等待管理员同意", task["text"])
        self.assertEqual(self.client.post("/internal/invites/claim", headers=self.core).json()["invite"], None)
        self.assertEqual(self.client.get("/admin/invites").status_code, 401)
        invitation = self.client.get("/admin/invites", headers=self.admin).json()[0]
        invite_id = invitation["id"]
        self.assertEqual(self.client.post(f"/admin/invites/{invite_id}/approve").status_code, 401)
        approval = self.client.post(f"/admin/invites/{invite_id}/approve", headers=self.admin)
        self.assertEqual(approval.json()["status"], "approved")
        self.assertEqual(self.client.post(f"/admin/invites/{invite_id}/approve",
                                          headers=self.admin).json()["status"], "approved")
        joining = self.client.post("/internal/invites/claim", headers=self.core).json()["invite"]
        self.assertEqual(joining["invite_code"], "C66OxXko")
        self.assertIsNone(self.client.post("/internal/invites/claim", headers=self.core).json()["invite"])
        result = self.client.post(f"/internal/invites/{invite_id}/result", headers=self.core,
                                  json={"status": "joined", "room_id": "new-group"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.post(f"/internal/invites/{invite_id}/result", headers=self.core,
                                          json={"status": "joined", "room_id": "new-group"}).status_code, 409)
        self.client.post(f"/internal/outbound/{task['id']}/result", headers=self.core,
                         json={"lease": task["lease"], "action": "begin"})
        self.client.post(f"/internal/outbound/{task['id']}/result", headers=self.core,
                         json={"lease": task["lease"], "action": "sent", "platform_id": "ack-one"})
        confirmation = self.client.post("/internal/outbound/claim", headers=self.core).json()["task"]
        self.assertEqual(confirmation["kind"], "private")
        self.assertEqual(confirmation["reply_to_message_id"], "m1")
        self.assertEqual(confirmation["reply_to_text"], self.candidate()["text"])
        self.assertIn("已接受邀请", confirmation["text"])
        self.assertIn("冬宴马场", confirmation["text"])
        with self.client.app.state.store.engine.connect() as db:
            self.assertEqual(db.execute(select(rooms.c.kind).where(rooms.c.id == "new-group")).scalar(), "group")
            self.assertEqual(db.execute(select(pending_group_invites.c.status)).scalar(), "joined")

    def test_failed_join_does_not_register_group(self):
        self.client.post("/internal/invites", headers=self.core, json=self.candidate())
        invite_id = self.client.get("/admin/invites", headers=self.admin).json()[0]["id"]
        self.client.post(f"/admin/invites/{invite_id}/approve", headers=self.admin)
        self.client.post("/internal/invites/claim", headers=self.core)
        self.client.post(f"/internal/invites/{invite_id}/result", headers=self.core,
                         json={"status": "failed", "error": "join_result_unconfirmed"})
        with self.client.app.state.store.engine.connect() as db:
            self.assertEqual(db.execute(select(rooms.c.id).where(rooms.c.kind == "group")).all(), [])
            self.assertEqual(db.execute(select(pending_group_invites.c.status)).scalar(), "failed")

    def test_existing_share_card_keeps_native_reference(self):
        card = {"type": "share", "shareType": "group_invite", "resourceId": "C66OxXko"}
        response = self.client.post("/internal/invites", headers=self.core,
            json={**self.candidate(), "source_content": card})
        self.assertEqual(response.status_code, 200)
        task = self.client.post("/internal/outbound/claim", headers=self.core).json()["task"]
        self.assertEqual(json.loads(task["reply_to_content_json"]), card)
        invitation = self.client.get("/admin/invites", headers=self.admin).json()[0]
        self.assertEqual(json.loads(invitation["source_content_json"]), card)
        self.client.post(f"/admin/invites/{invitation['id']}/approve", headers=self.admin)
        self.client.post("/internal/invites/claim", headers=self.core)
        self.client.post(f"/internal/invites/{invitation['id']}/result", headers=self.core,
                         json={"status": "joined", "room_id": "new-group"})
        self.client.post(f"/internal/outbound/{task['id']}/result", headers=self.core,
                         json={"lease": task["lease"], "action": "begin"})
        self.client.post(f"/internal/outbound/{task['id']}/result", headers=self.core,
                         json={"lease": task["lease"], "action": "sent", "platform_id": "ack-one"})
        confirmation = self.client.post("/internal/outbound/claim", headers=self.core).json()["task"]
        self.assertEqual(json.loads(confirmation["reply_to_content_json"]), card)


class InviteGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_announcement_command_is_forwarded_to_core(self):
        browser = SimpleNamespace()
        inbound = AsyncMock()
        gateway = Gateway(SimpleNamespace(bot_id="bot"), browser, inbound)
        gateway.own_id = "self"
        gateway.allowed = {"dm1"}
        gateway.private_rooms = {"dm1"}
        gateway.room_kinds = {"dm1": "private"}
        event = {"chatroomId": "dm1", "message": {"chatroom_id": "dm1", "message_id": "m-announcement",
                 "sent_by": "admin", "sent_at": "2026-09-27T00:00:00Z",
                 "content": {"type": "text", "text": "/公告 今晚活动开始"}}}

        await gateway.on_message(event)

        forwarded = inbound.await_args.args[0]
        self.assertEqual(forwarded["text"], "/公告 今晚活动开始")
        self.assertEqual(forwarded["kind"], "private")
        self.assertEqual(forwarded["message_id"], "m-announcement")

    async def test_private_force_delivery_command_is_forwarded_to_core(self):
        for text in ("/强制接生", "/強制接生"):
            with self.subTest(text=text):
                browser = SimpleNamespace()
                inbound = AsyncMock()
                gateway = Gateway(SimpleNamespace(bot_id="bot"), browser, inbound)
                gateway.own_id = "self"
                gateway.allowed = {"dm1"}
                gateway.private_rooms = {"dm1"}
                gateway.room_kinds = {"dm1": "private"}
                event = {"chatroomId": "dm1", "message": {
                    "chatroom_id": "dm1", "message_id": "m-force-delivery",
                    "sent_by": "admin", "sent_at": "2026-09-30T00:00:00Z",
                    "content": {"type": "text", "text": text}}}

                await gateway.on_message(event)

                forwarded = inbound.await_args.args[0]
                self.assertEqual(forwarded["text"], text)
                self.assertEqual(forwarded["kind"], "private")
                self.assertEqual(forwarded["message_id"], "m-force-delivery")

    async def test_only_verified_private_room_forwards_invitation(self):
        browser = SimpleNamespace(invite_info=AsyncMock(return_value={
            "groupName": "冬宴马场", "isMember": False}))
        inbound = AsyncMock()
        gateway = Gateway(SimpleNamespace(bot_id="bot"), browser, inbound)
        gateway.own_id = "self"
        gateway.allowed = {"dm1", "group1"}
        gateway.private_rooms = {"dm1"}
        gateway.room_kinds = {"group1": "group", "dm1": "private"}

        def event(room, mid, text):
            return {"chatroomId": room, "message": {"chatroom_id": room, "message_id": mid,
                    "sent_by": "other", "sent_at": "2026-09-27T00:00:00Z",
                    "content": {"type": "text", "text": text}}}

        await gateway.on_message(event("dm1", "m1", "https://www.dzmm.io/invite/C66OxXko"))
        self.assertTrue(inbound.await_args.args[0]["invitation"])
        self.assertEqual(inbound.await_args.args[0]["group_name"], "冬宴马场")
        inbound.reset_mock()
        await gateway.on_message(event("dm1", "m2", "/牧场"))
        inbound.assert_not_awaited()
        gateway.private_rooms.clear()
        await gateway.on_message(event("dm1", "m3", "https://www.dzmm.io/invite/C66OxXko"))
        inbound.assert_not_awaited()

    async def test_share_card_is_normalized_only_with_confirmed_shape(self):
        card = {"type": "share", "shareType": "group_invite", "resourceId": "C66OxXko"}
        event = {"chatroomId": "dm1", "message": {"chatroom_id": "dm1", "message_id": "m1",
                 "sent_by": "other", "sent_at": "2026-09-27T00:00:00Z", "content": card}}
        normalized = normalize_invite_card(event, "self", "bot", {"dm1"})
        self.assertEqual(normalized["source_content"], card)
        self.assertEqual(normalized["text"], "https://www.dzmm.io/invite/C66OxXko")
        self.assertIsNone(normalize_invite_card({**event, "message": {**event["message"],
                          "content": {**card, "shareType": "other"}}}, "self", "bot", {"dm1"}))

    async def test_private_confirmation_references_original_share_card(self):
        card = {"type": "share", "shareType": "group_invite", "resourceId": "C66OxXko"}
        gateway = Gateway(SimpleNamespace(bot_id="bot"), SimpleNamespace(), AsyncMock())
        gateway.socket = SimpleNamespace(connected=True, call=AsyncMock(return_value={"success": True}))
        gateway.allowed = {"dm1"}
        gateway.own_id = "self"
        task = {"id": "out1", "room": "dm1", "kind": "private", "text": "已接受邀请",
                "reply_to_message_id": "m1", "reply_to_sender_id": "other",
                "reply_to_text": "https://www.dzmm.io/invite/C66OxXko",
                "reply_to_content_json": json.dumps(card)}
        result = await gateway.send_socket(task)
        self.assertEqual(result["action"], "sent")
        payload = gateway.socket.call.await_args.args[1]
        self.assertEqual(payload["message"]["content"]["reference"]["content"], card)
        self.assertEqual(payload["message"]["content"]["reference"]["id"], "m1")


if __name__ == "__main__":
    unittest.main()

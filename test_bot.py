import json
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from legacy_webhook import create_app


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "test.sqlite3"
        self.headers = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}

    def client(self, **kwargs):
        return TestClient(create_app(db_path=self.path, secret="test-secret", **kwargs))

    def send(self, client, command, mid="m1", chat="g1", bot=False):
        return client.post("/webhook", headers=self.headers, json={"message": {
            "message_id": mid, "chat": {"id": chat},
            "from": {"id": "u1", "first_name": "Alice", "is_bot": bot}, "text": command}})

    def test_auth_and_invalid_input(self):
        with self.client(dry_run=True) as c:
            self.assertEqual(c.post("/webhook", json={}).status_code, 401)
            self.assertEqual(c.post("/webhook", headers=self.headers, content="{").status_code, 400)
            self.assertTrue(self.send(c, "/join", bot=True).json()["ignored"])
            self.assertTrue(self.send(c, "hello").json()["ignored"])

    def test_commands_persistence_and_group_isolation(self):
        with self.client(dry_run=True) as c:
            for i, cmd in enumerate(["/start", "/help"]):
                self.assertIn("/join", self.send(c, cmd, str(i)).json()["reply"])
            self.assertIn("请先", self.send(c, "/profile", "p0").json()["reply"])
            self.assertIn("成功", self.send(c, "/join").json()["reply"])
            self.assertTrue(self.send(c, "/join").json()["duplicate"])
            self.assertIn("已经", self.send(c, "/join", "m2").json()["reply"])
        with self.client(dry_run=True) as c:
            self.assertIn("Alice", self.send(c, "/profile", "p1").json()["reply"])
            self.assertIn("请先", self.send(c, "/profile", "p1", "g2").json()["reply"])

    def test_platform_contract_and_failed_send_retry(self):
        calls = []

        def upstream(request):
            calls.append(request)
            self.assertEqual(request.url.path, "/api/bot/send-message")
            self.assertEqual(request.headers["X-Bot-Token"], "test-token")
            self.assertEqual(json.loads(request.content)["chatroom_id"], "g1")
            return httpx.Response(403 if len(calls) == 1 else 200, json={"ok": len(calls) > 1})

        with self.client(dry_run=False, token="test-token", transport=httpx.MockTransport(upstream)) as c:
            self.assertEqual(self.send(c, "/join").status_code, 502)
            self.assertEqual(self.send(c, "/join").status_code, 200)
            self.assertEqual(json.loads(calls[0].content), json.loads(calls[1].content))
            self.assertTrue(self.send(c, "/join").json()["duplicate"])
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()

import asyncio
import time
import uuid
from datetime import datetime, timezone

import httpx
import socketio
from .browser_socket import BrowserSocket


def normalize(event, own_id, bot_id, allowed):
    if not isinstance(event, dict) or not isinstance(event.get("message"), dict):
        return None
    msg = event["message"]
    room, mid, sender = event.get("chatroomId"), msg.get("message_id"), msg.get("sent_by")
    if not all(isinstance(v, str) and 0 < len(v) <= 200 for v in (room, mid, sender)):
        return None
    if room not in allowed or msg.get("chatroom_id") != room or sender in {own_id, bot_id}:
        return None
    try:
        stamp = datetime.fromisoformat(msg["sent_at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    content = msg.get("content")
    # Test gameplay only consumes text. Images require a separate gameplay handler.
    if not isinstance(content, dict) or content.get("type") != "text":
        return None
    text = content.get("text")
    if not isinstance(text, str) or not text or len(text) > 10000:
        return None
    return {"room": room, "message_id": mid, "sender": sender, "name": "玩家", "text": text}


class Gateway:
    def __init__(self, cfg, browser, inbound):
        self.cfg, self.browser, self.inbound = cfg, browser, inbound
        # Our outer loop reconnects so credentials are refreshed each time.
        self.socket = BrowserSocket(browser)
        self.socket.on("message:new", self.on_message)
        self.allowed, self.joined = set(), set()
        self.own_id = None
        self.socket_lock = asyncio.Lock()
        self.next_send = 0.0
        self.last_private = {}

    async def connect(self):
        token, self.own_id = await self.browser.credentials()
        await self.socket.connect(self.cfg.origin, socketio_path="ws/matching",
                                  auth={"token": token}, wait_timeout=20)
        self.joined.clear()

    async def sync_rooms(self, rooms):
        allowed = {r["id"] for r in rooms}
        # Reconnect to leave removed rooms without inventing an undocumented leave event.
        removed = self.allowed - allowed
        self.allowed = allowed
        if removed and self.socket.connected:
            await self.socket.disconnect()
            self.joined.clear()
            return
        if not self.socket.connected:
            return
        async with self.socket_lock:
            for room in sorted(allowed - self.joined):
                ack = await self.socket.call("message:join-room", {"chatroomId": room}, timeout=10)
                if not isinstance(ack, dict) or ack.get("success") is not True:
                    raise RuntimeError("group_subscription_rejected")
                self.joined.add(room)

    async def on_message(self, event):
        data = normalize(event, self.own_id, self.cfg.bot_id, self.allowed)
        if data:
            await self.inbound(data)

    async def send_socket(self, task):
        if not self.socket.connected or task["room"] not in self.allowed:
            return {"action": "retry", "error": "rejected"}
        async with self.socket_lock:
            # Conservative ordinary-account pacing; platform limits are not specified.
            delay = max(self.next_send, self.last_private.get(task["room"], 0)) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            message_id = str(uuid.uuid4())
            content = {"type": "text", "text": task["text"]}
            if task.get("reply_to_message_id"):
                content["reference"] = {
                    "id": task["reply_to_message_id"],
                    "sentBy": task["reply_to_sender_id"],
                    "content": {"type": "text", "text": task["reply_to_text"]},
                }
            payload = {"chatroomId": task["room"], "message": {
                "message_id": message_id, "sent_by": self.own_id,
                "chatroom_id": task["room"], "sent_at": datetime.now(timezone.utc).isoformat(),
                "content": content}}
            try:
                ack = await self.socket.call("message:send", payload, timeout=15)
            except (socketio.exceptions.SocketIOError, OSError):
                if self.socket.connected:
                    await self.socket.disconnect()
                return {"action": "uncertain", "error": "transport_unknown"}
            finally:
                self.next_send = time.monotonic() + 2
                if task["kind"] == "private":
                    self.last_private[task["room"]] = time.monotonic() + 5
            if isinstance(ack, dict) and ack.get("success") is True:
                return {"action": "sent", "platform_id": message_id}
            if isinstance(ack, dict) and ack.get("success") is False:
                if "请稍后再试" in str(ack.get("error", "")):
                    self.next_send = time.monotonic() + 60
                    return {"action": "retry", "error": "rate_limited"}
                return {"action": "failed", "error": "rejected"}
            return {"action": "uncertain", "error": "invalid_ack"}


async def send_bot(client, cfg, task):
    try:
        response = await client.post(cfg.origin + "/api/bot/send-message",
                                     headers={"X-Bot-Token": cfg.bot_token},
                                     json={"chatroom_id": task["room"], "content": task["text"]})
    except httpx.HTTPError:
        return {"action": "uncertain", "error": "transport_unknown"}
    if response.status_code == 429:
        return {"action": "retry", "error": "rate_limited"}
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
        result = body.get("result")
        mid = result.get("message_id") if isinstance(result, dict) else None
        if isinstance(mid, str) and mid.strip():
            return {"action": "sent", "platform_id": mid}
        return {"action": "uncertain", "error": "invalid_ack"}
    # Fallback only for explicit application rejection, never timeout or 5xx.
    if isinstance(body, dict) and body.get("ok") is False and response.status_code < 500:
        return {"action": "failed", "error": "rejected", "fallback": True}
    if 400 <= response.status_code < 500:
        return {"action": "failed", "error": "rejected"}
    return {"action": "uncertain", "error": "invalid_ack"}

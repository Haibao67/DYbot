import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone

import httpx
import socketio
from .browser_socket import BrowserSocket
from .invitations import invite_code, invite_card_content
from .socket_limiter import SocketLimiter


_CREDENTIAL_PATTERN = re.compile(
    r"(?i)\b(?:authorization|x-bot-token|cookie|set-cookie|token|secret)\b\s*[:=]\s*[^,\s;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^,\s;]+")
timing_log = logging.getLogger("dzmm.gateway.timing")


def log_platform_timing(channel, task, started_at, outcome, split_part=None, phase_ms=None):
    phases = phase_ms or {}
    elapsed_ms = phases.get("socket_call", (time.monotonic() - started_at) * 1000)
    timing_log.info("%s", json.dumps({"event": "platform_ack", "channel": channel,
        "task_id": task.get("id"), "attempt": task.get("attempts", 0) + 1,
        "split_part": split_part, "elapsed_ms": round(elapsed_ms, 1),
        "room_lock_wait_ms": round(phases["room_lock_wait"], 1) if "room_lock_wait" in phases else None,
        "limiter_wait_ms": round(phases["limiter_wait"], 1) if "limiter_wait" in phases else None,
        "socket_lock_wait_ms": round(phases["socket_lock_wait"], 1) if "socket_lock_wait" in phases else None,
        "socket_call_ms": round(phases["socket_call"], 1) if "socket_call" in phases else None,
        "outcome": outcome}, sort_keys=True))


def failure_detail(channel, task, ack=None, http_status=None, exception=None,
                   attempted_text=None, local_reason=None):
    """Create bounded diagnostic metadata without persisting reply/reference bodies."""
    text = task.get("text", "")
    attempted = text if attempted_text is None else attempted_text
    details = {
        "transport": channel,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "attempted_chars": len(attempted),
        "attempted_line_breaks": attempted.count("\n"),
    }
    if http_status is not None:
        details["http_status"] = int(http_status)
    if local_reason:
        details["local_reason"] = local_reason[:120]
    if exception is not None:
        details["exception_type"] = type(exception).__name__
    if isinstance(ack, dict):
        details["ack_type"] = "object"
        if isinstance(ack.get("success"), bool):
            details["ack_success"] = ack["success"]
        for key in ("code", "error", "reason"):
            value = ack.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                value = str(value)
                # A platform may echo submitted content in its rejection text.
                for private_text in (text, task.get("reply_to_text", "")):
                    if private_text:
                        value = value.replace(private_text, "[message content redacted]")
                        for line in private_text.splitlines():
                            if len(line) >= 16:
                                value = value.replace(line, "[message content redacted]")
                value = _BEARER_PATTERN.sub("Bearer [redacted]", value)
                value = _CREDENTIAL_PATTERN.sub("[credential redacted]", value)
                value = " ".join(value.split())[:300]
                if value:
                    details[{"code": "platform_code", "error": "platform_error",
                             "reason": "platform_reason"}[key]] = value
    elif ack is not None:
        details["ack_type"] = type(ack).__name__[:80]
    return details


def split_long_reply(text, max_lines=10):
    """Split only after a platform length rejection, at display-line boundaries."""
    lines = text.splitlines()
    chunks, current = [], []
    for line in lines:
        if current and len(current) >= max_lines:
            chunks.append("\n".join(current))
            current = []
        current.append(line)
    if current:
        chunks.append("\n".join(current))
    return chunks if len(chunks) > 1 else [text]


def is_length_rejection(ack):
    if not isinstance(ack, dict) or ack.get("success") is not False:
        return False
    reason = " ".join(str(ack.get(key, "")) for key in ("error", "code", "reason")).casefold()
    return any(marker in reason for marker in (
        "too-long", "too_many_lines", "too-many-lines", "message_too_long",
        "这条太长", "换行太多", "超过群聊限制", "超出长度限制",
    ))


def is_duplicate_rejection(ack):
    if not isinstance(ack, dict) or ack.get("success") is not False:
        return False
    reason = " ".join(str(ack.get(key, "")) for key in ("error", "code", "reason")).casefold()
    return any(marker in reason for marker in (
        "duplicate", "重复内容", "重复发送",
    ))


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
    # Only sender metadata can provide identity; quoted content is never a name.
    user = msg.get('sent_by_user')
    user = user if isinstance(user, dict) else {}
    actor = msg.get('sender')
    actor = actor if isinstance(actor, dict) else {}
    candidates = (user.get('fullName'), user.get('name'), user.get('nickname'), user.get('display_name'),
                  msg.get('sent_by_name'), msg.get('sender_name'), msg.get('nickname'),
                  actor.get('name'), actor.get('nickname'))
    name = next((value.strip() for value in candidates
                 if isinstance(value, str) and 0 < len(value.strip()) <= 100), '')
    data = {"room": room, "message_id": mid, "sender": sender, "name": name, "text": text}
    reference = content.get("reference")
    if isinstance(reference, dict):
        ref_id, ref_sender = reference.get("id"), reference.get("sentBy")
        if all(isinstance(value, str) and 0 < len(value) <= 200 for value in (ref_id, ref_sender)):
            data.update(referenced_message_id=ref_id, referenced_sender_id=ref_sender)
    return data


def normalize_invite_card(event, own_id, bot_id, allowed):
    if not isinstance(event, dict) or not isinstance(event.get("message"), dict):
        return None
    content = invite_card_content(event["message"].get("content"))
    if not content:
        return None
    text = f"https://www.dzmm.io/invite/{content['resourceId']}"
    message = {**event["message"], "content": {"type": "text", "text": text}}
    data = normalize({**event, "message": message}, own_id, bot_id, allowed)
    return {**data, "source_content": content} if data else None


class Gateway:
    def __init__(self, cfg, browser, inbound, limiter=None):
        self.cfg, self.browser, self.inbound = cfg, browser, inbound
        # Our outer loop reconnects so credentials are refreshed each time.
        self.socket = BrowserSocket(browser)
        self.socket.on("message:new", self.on_message)
        self.allowed, self.joined, self.private_rooms = set(), set(), set()
        self.room_kinds = {}
        self.own_id = None
        self.sender_names = {}
        self.room_locks = {}
        self.socket_lock = asyncio.Lock()
        self.limiter = limiter or SocketLimiter.from_settings(cfg)

    async def connect(self):
        token, self.own_id = await self.browser.credentials()
        await self.socket.connect(self.cfg.origin, socketio_path="ws/matching",
                                  auth={"token": token}, wait_timeout=20)
        self.joined.clear()

    async def sync_rooms(self, rooms, private_rooms=None):
        self.room_kinds = {r["id"]: r.get("kind", "group") for r in rooms}
        if private_rooms is not None:
            self.private_rooms = set(private_rooms)
        allowed = {r["id"] for r in rooms} | self.private_rooms
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
                try:
                    ack = await self.socket.call("message:join-room", {"chatroomId": room}, timeout=10)
                except Exception:
                    if room in self.private_rooms:
                        continue
                    raise
                if not isinstance(ack, dict) or ack.get("success") is not True:
                    if room in self.private_rooms:
                        continue
                    raise RuntimeError("group_subscription_rejected")
                self.joined.add(room)

    async def on_message(self, event):
        data = normalize(event, self.own_id, self.cfg.bot_id, self.allowed)
        room_id = event.get("chatroomId") if isinstance(event, dict) else None
        if data is None and room_id in self.private_rooms:
            data = normalize_invite_card(event, self.own_id, self.cfg.bot_id, self.allowed)
        if data:
            if data['name']:
                self.sender_names[data['sender']] = data['name']
                if len(self.sender_names) > 10000:
                    self.sender_names.pop(next(iter(self.sender_names)))
            else:
                data['name'] = self.sender_names.get(data['sender'], '')
                if not data['name']:
                    from .domain.command_aliases import normalize_command_parts
                    parts = normalize_command_parts(data['text'].strip().split())
                    if parts in (['/注册'], ['/加入']):
                        try:
                            resolved = await self.browser.chatroom_user_name(data['sender'], data['room'])
                        except Exception as exc:
                            reason=str(exc) if isinstance(exc,RuntimeError) and str(exc).startswith('nickname_') else type(exc).__name__
                            log.warning('registration_name_lookup_failed reason=%s',reason)
                            resolved = None
                        if resolved:
                            data['name'] = resolved
                            self.sender_names[data['sender']] = resolved
                            if len(self.sender_names) > 10000:
                                self.sender_names.pop(next(iter(self.sender_names)))
            if data["room"] in self.private_rooms or self.room_kinds.get(data["room"]) == "private":
                if data["room"] not in self.private_rooms:
                    return
                words = data["text"].strip().split(maxsplit=1)
                from .domain.command_aliases import normalize_command_parts
                command = normalize_command_parts(words[:1])[0] if words else None
                if command in {"/公告", "/管理员登陆", "/强制接生", "/成马", "/开赛", "/强制开赛",
                               "/邀请列表", "/同意邀请", "/管理员发币", "/管理员发道具"}:
                    await self.inbound({**data, "kind": "private",
                                        "_received_monotonic": time.monotonic()})
                    return
                code = invite_code(data["text"])
                if not code:
                    return
                try:
                    info = await self.browser.invite_info(code)
                except Exception:
                    return
                if info.get("isMember") is True:
                    return
                await self.inbound({**data, "invitation": True,
                                    "_received_monotonic": time.monotonic(),
                                    "group_name": info["groupName"][:160]})
            else:
                await self.inbound({**data, "_received_monotonic": time.monotonic()})

    async def _call_socket(self, payload):
        lock_started = time.monotonic()
        await self.socket_lock.acquire()
        socket_lock_wait_ms = (time.monotonic() - lock_started) * 1000
        call_started = time.monotonic()
        try:
            try:
                ack = await self.socket.call("message:send", payload, timeout=15)
                error = None
            except (socketio.exceptions.SocketIOError, OSError) as exc:
                ack, error = None, exc
            phases = {"socket_lock_wait": socket_lock_wait_ms,
                      "socket_call": (time.monotonic() - call_started) * 1000}
            return ack, phases, error
        finally:
            self.socket_lock.release()

    async def send_socket(self, task):
        if not self.socket.connected or task["room"] not in self.allowed:
            return {"action": "retry", "error": "rejected",
                    "error_detail": failure_detail("socket", task, local_reason="connection_or_room_unavailable")}
        reference_content = {"type": "text", "text": task.get("reply_to_text")}
        if task.get("reply_to_content_json"):
            try:
                reference_content = invite_card_content(json.loads(task["reply_to_content_json"]))
            except (TypeError, ValueError):
                reference_content = None
            if not reference_content:
                return {"action": "failed", "error": "rejected",
                        "error_detail": failure_detail("socket", task, local_reason="invalid_reference_content")}
        room_lock = self.room_locks.setdefault(task["room"], asyncio.Lock())
        room_lock_started = time.monotonic()
        async with room_lock:
            room_lock_wait_ms = (time.monotonic() - room_lock_started) * 1000
            limiter_started = time.monotonic()
            await self.limiter.wait(task["room"], task["kind"] == "private")
            limiter_wait_ms = (time.monotonic() - limiter_started) * 1000
            def payload_for(text):
                message_id = str(uuid.uuid4())
                attempted_text = text
                content = {"type": "text", "text": attempted_text}
                if task.get("reply_to_message_id"):
                    content["reference"] = {
                        "id": task["reply_to_message_id"],
                        "sentBy": task["reply_to_sender_id"],
                        "content": reference_content,
                    }
                payload = {"chatroomId": task["room"], "message": {
                    "message_id": message_id, "sent_by": self.own_id,
                    "chatroom_id": task["room"], "sent_at": datetime.now(timezone.utc).isoformat(),
                    "content": content}}
                return message_id, payload, attempted_text

            message_id, payload, attempted_text = payload_for(task["text"])
            try:
                call_started = time.monotonic()
                ack, phase_ms, call_error = await self._call_socket(payload)
                phase_ms.update({"room_lock_wait": room_lock_wait_ms,
                                 "limiter_wait": limiter_wait_ms})
                log_platform_timing("private_socket" if task["kind"] == "private" else "group_socket_reply",
                                    task, call_started,
                                    "transport_unknown" if call_error else
                                    "accepted" if isinstance(ack, dict) and ack.get("success") is True else "rejected_or_invalid_ack",
                                    phase_ms=phase_ms)
            finally:
                self.limiter.complete_attempt(task["room"], task["kind"] == "private")
            if call_error:
                if self.socket.connected:
                    await self.socket.disconnect()
                return {"action": "uncertain", "error": "transport_unknown",
                        "error_detail": failure_detail("socket", task, exception=call_error,
                                                        attempted_text=attempted_text)}
            if isinstance(ack, dict) and ack.get("success") is True:
                self.limiter.accepted(task["room"])
                return {"action": "sent", "platform_id": message_id}
            if isinstance(ack, dict) and ack.get("success") is False:
                if "请稍后再试" in str(ack.get("error", "")):
                    self.limiter.rate_limited(task["room"])
                    return {"action": "retry", "error": "rate_limited",
                            "error_detail": failure_detail("socket", task, ack,
                                                            attempted_text=attempted_text)}
                # A long reply rejected by the platform is split into separate replies;
                # short duplicate-content rejections remain terminal and are never padded.
                if is_length_rejection(ack) or is_duplicate_rejection(ack):
                    parts = split_long_reply(task["text"])
                    if len(parts) > 1:
                        sent_ids = []
                        for part in parts:
                            limiter_started = time.monotonic()
                            await self.limiter.wait(task["room"], task["kind"] == "private")
                            part_limiter_wait_ms = (time.monotonic() - limiter_started) * 1000
                            part_id, part_payload, part_text = payload_for(part)
                            try:
                                call_started = time.monotonic()
                                part_ack, phase_ms, call_error = await self._call_socket(part_payload)
                                phase_ms.update({"room_lock_wait": 0.0,
                                                 "limiter_wait": part_limiter_wait_ms})
                                log_platform_timing("private_socket" if task["kind"] == "private" else "group_socket_reply",
                                                    task, call_started,
                                                    "transport_unknown" if call_error else
                                                    "accepted" if isinstance(part_ack, dict) and part_ack.get("success") is True else "rejected_or_invalid_ack",
                                                    len(sent_ids) + 1, phase_ms)
                            finally:
                                self.limiter.complete_attempt(task["room"], task["kind"] == "private")
                            if call_error:
                                if self.socket.connected:
                                    await self.socket.disconnect()
                                return {"action": "uncertain", "error": "transport_unknown",
                                        "error_detail": failure_detail("socket", task, exception=call_error,
                                                                        attempted_text=part_text)}
                            if isinstance(part_ack, dict) and part_ack.get("success") is False and "请稍后再试" in str(part_ack.get("error", "")):
                                self.limiter.rate_limited(task["room"])
                                if not sent_ids:
                                    return {"action": "retry", "error": "rate_limited",
                                            "error_detail": failure_detail("socket", task, part_ack,
                                                                            attempted_text=part_text)}
                            if not isinstance(part_ack, dict) or part_ack.get("success") is not True:
                                if sent_ids:
                                    return {"action": "uncertain", "error": "invalid_ack",
                                            "error_detail": failure_detail("socket", task, part_ack,
                                                                            attempted_text=part_text,
                                                                            local_reason="partial_split_send")}
                                return {"action": "failed", "error": "rejected",
                                        "error_detail": failure_detail("socket", task, part_ack,
                                                                        attempted_text=part_text,
                                                                        local_reason="length_split_part_rejected")}
                            self.limiter.accepted(task["room"])
                            sent_ids.append(part_id)
                        return {"action": "sent", "platform_id": sent_ids[0]}
                return {"action": "failed", "error": "rejected",
                        "error_detail": failure_detail("socket", task, ack,
                                                        attempted_text=attempted_text)}
            return {"action": "uncertain", "error": "invalid_ack",
                    "error_detail": failure_detail("socket", task, ack,
                                                    attempted_text=attempted_text,
                                                    local_reason="unrecognized_ack_shape")}


async def send_bot(client, cfg, task):
    attempted_text = task["text"]
    call_started = time.monotonic()
    try:
        response = await client.post(cfg.origin + "/api/bot/send-message",
                                     headers={"X-Bot-Token": cfg.bot_token},
                                     json={"chatroom_id": task["room"], "content": attempted_text})
        log_platform_timing("bot_http_broadcast", task, call_started,
                            "http_" + str(response.status_code))
    except httpx.HTTPError as exc:
        log_platform_timing("bot_http_broadcast", task, call_started, "transport_unknown")
        return {"action": "uncertain", "error": "transport_unknown",
                "error_detail": failure_detail("bot_http", task, exception=exc,
                                               attempted_text=attempted_text)}
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code == 429:
        return {"action": "retry", "error": "rate_limited",
                "error_detail": failure_detail("bot_http", task, body, response.status_code,
                                               attempted_text=attempted_text)}
    if response.status_code == 200 and isinstance(body, dict) and body.get("ok") is True:
        result = body.get("result")
        mid = result.get("message_id") if isinstance(result, dict) else None
        if isinstance(mid, str) and mid.strip():
            return {"action": "sent", "platform_id": mid}
        return {"action": "uncertain", "error": "invalid_ack",
                "error_detail": failure_detail("bot_http", task, body, response.status_code,
                                                attempted_text=attempted_text,
                                                local_reason="missing_message_id")}
    # Fallback only for explicit application rejection, never timeout or 5xx.
    if isinstance(body, dict) and body.get("ok") is False and response.status_code < 500:
        return {"action": "failed", "error": "rejected", "fallback": True,
                "error_detail": failure_detail("bot_http", task, body, response.status_code,
                                               attempted_text=attempted_text)}
    if 400 <= response.status_code < 500:
        return {"action": "failed", "error": "rejected",
                "error_detail": failure_detail("bot_http", task, body, response.status_code,
                                               attempted_text=attempted_text)}
    return {"action": "uncertain", "error": "invalid_ack",
            "error_detail": failure_detail("bot_http", task, body, response.status_code,
                                            attempted_text=attempted_text,
                                            local_reason="unexpected_http_response")}

import asyncio
import json
import logging
import sys
import time

import httpx

from .browser import BrowserSession
from .gateway import Gateway, send_bot
from .settings import Settings

log = logging.getLogger("dzmm.worker")


class Worker:
    def __init__(self, cfg, core, platform, gateway=None):
        self.cfg, self.core, self.platform, self.gateway = cfg, core, platform, gateway

    async def inbound(self, data):
        # Core commits inbound + gameplay + outbox atomically before acknowledging.
        # Bounded retries; no history-backfill contract exists in the supplied guide.
        event_started_at = data.pop("_received_monotonic", None)
        for attempt in range(3):
            started_at = time.monotonic()
            try:
                path = "/internal/invites" if data.get("invitation") else "/internal/inbound"
                payload = {key: value for key, value in data.items() if key != "invitation"}
                r = await self.core.post(path, json=payload)
                r.raise_for_status()
                log.info("%s", json.dumps({"event": "inbound_core_ack", "route": path,
                    "elapsed_ms": round((time.monotonic() - started_at) * 1000, 1),
                    "event_processing_ms": round((started_at - event_started_at) * 1000, 1)
                        if isinstance(event_started_at, (int, float)) else None,
                    "attempt": attempt + 1, "outcome": "ack"}))
                return
            except httpx.HTTPError:
                log.warning("%s", json.dumps({"event": "inbound_core_ack", "route": path,
                    "elapsed_ms": round((time.monotonic() - started_at) * 1000, 1),
                    "event_processing_ms": round((started_at - event_started_at) * 1000, 1)
                        if isinstance(event_started_at, (int, float)) else None,
                    "attempt": attempt + 1, "outcome": "error"}))
                if attempt == 2:
                    log.error("inbound_delivery_failed: Core unavailable; message may be lost")
                    return
                await asyncio.sleep(2)

    async def drain_invite_once(self):
        if self.cfg.mode != "live" or not self.gateway or not self.gateway.socket.connected:
            return False
        response = await self.core.post("/internal/invites/claim")
        response.raise_for_status()
        invitation = response.json()["invite"]
        if not invitation:
            return False
        try:
            async with self.gateway.socket_lock:
                room_id = await self.gateway.browser.join_by_invite(invitation["invite_code"])
            outcome = {"status": "joined", "room_id": room_id}
        except Exception as exc:
            # The mutation may have succeeded before a timeout. Never auto-resend it.
            outcome = {"status": "failed", "error": type(exc).__name__}
        result = await self.core.post(f"/internal/invites/{invitation['id']}/result", json=outcome)
        result.raise_for_status()
        return True

    async def drain_once(self):
        health = await self.core.get("/healthz")
        health.raise_for_status()
        if health.json().get("mode") != self.cfg.mode:
            raise RuntimeError("Core 与 Worker 模式不同，请同时重启")
        if self.cfg.mode == "live" and (not self.gateway or not self.gateway.socket.connected):
            return False
        response = await self.core.post("/internal/outbound/claim")
        response.raise_for_status()
        task = response.json()["task"]
        if not task:
            return False
        path = f"/internal/outbound/{task['id']}/result"
        started = await self.core.post(path, json={"lease": task["lease"], "action": "begin"})
        started.raise_for_status()
        send_started_at = time.monotonic()
        used_fallback = False
        if self.cfg.mode == "simulate":
            outcome = {"action": "simulated", "error": "simulation"}
            message = f"[模拟回复] {task['text']}"
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(message.encode(encoding, errors="replace").decode(encoding), flush=True)
        elif task["kind"] == "group" and self.cfg.bot_token and not task.get("reply_to_message_id"):
            outcome = await send_bot(self.platform, self.cfg, task)
            if outcome.pop("fallback", False):
                used_fallback = True
                bot_failure = outcome.get("error_detail") or {}
                outcome = await self.gateway.send_socket(task)
                if outcome.get("error_detail") is not None:
                    outcome["error_detail"].update({
                        "bot_http_" + key: value for key, value in bot_failure.items()
                    })
        else:
            outcome = await self.gateway.send_socket(task)
        channel = ("private_socket" if task["kind"] == "private" else
                   "group_socket_reply" if task.get("reply_to_message_id") else "group_broadcast")
        log.info("%s", json.dumps({"event": "outbound_worker_send", "task_id": task["id"],
            "channel": channel, "attempt": task.get("attempts", 0) + 1,
            "elapsed_ms": round((time.monotonic() - send_started_at) * 1000, 1),
            "outcome": outcome.get("action"), "fallback": used_fallback}))
        if outcome.get("action") in {"retry", "failed", "uncertain"}:
            failure = outcome.get("error_detail") or {}
            details = {
                "event": "outbound_delivery_result",
                "task_id": task["id"],
                "attempt": task.get("attempts", 0) + 1,
                "channel": task["kind"],
                "reply_chars": failure.get("attempted_chars", len(task["text"])),
                "reply_line_breaks": failure.get("attempted_line_breaks", task["text"].count("\n")),
                "action": outcome.get("action"),
                "category": outcome.get("error"),
                "details": failure,
            }
            log.warning("%s", json.dumps(details, ensure_ascii=False, sort_keys=True))
        # Retry reporting only; never repeat the platform send in this loop.
        for attempt in range(3):
            try:
                result = await self.core.post(path, json={"lease": task["lease"], **outcome})
                result.raise_for_status()
                return True
            except httpx.HTTPError:
                if attempt == 2:
                    log.error("outbound_result_unconfirmed: task will become uncertain")
                    return True
                await asyncio.sleep(1)

    async def outbound_loop(self):
        active = set()
        try:
            while True:
                try:
                    state = "simulating" if self.cfg.mode == "simulate" else (
                        "connected" if self.gateway and self.gateway.socket.connected else "disconnected")
                    limiter = getattr(self.gateway, "limiter", None)
                    heartbeat_payload = {"state": state}
                    if limiter is not None:
                        heartbeat_payload["limiter"] = limiter.snapshot()
                    heartbeat = await self.core.post("/internal/heartbeat", json=heartbeat_payload)
                    heartbeat.raise_for_status()
                    auction_tick = await self.core.post("/internal/auctions/finalize")
                    auction_tick.raise_for_status()
                    invited = await self.drain_invite_once()
                    if not invited:
                        # The real Browser gateway serializes Socket calls and invite joins.
                        # Do not lease extra platform messages behind that lock.
                        capacity = (1 if self.cfg.mode == "live" and isinstance(self.gateway, Gateway)
                                    else self.cfg.outbound_inflight_limit)
                        for _ in range(capacity - len(active)):
                            active.add(asyncio.create_task(self.drain_once()))
                    if active:
                        done, active = await asyncio.wait(active, timeout=0.5,
                                                          return_when=asyncio.FIRST_COMPLETED)
                        sent = invited
                        for task in done:
                            sent = task.result() or sent
                    else:
                        sent = invited
                except httpx.HTTPError:
                    log.warning("core_unavailable_or_lease_conflict")
                    sent = False
                await asyncio.sleep(0.5 if sent else 2)
        finally:
            # Do not lease new work while stopping. A send that outlives this bound
            # expires through the existing uncertain-result path, never an auto-replay.
            if active:
                done, pending = await asyncio.wait(active, timeout=30)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    if not task.cancelled():
                        task.exception()

    async def connection_loop(self):
        while True:
            try:
                response = await self.core.get("/internal/rooms")
                response.raise_for_status()
                rooms = response.json()
                try:
                    private_rooms = await self.gateway.browser.private_rooms()
                except Exception as exc:
                    log.warning("private_chat_list_unavailable: %s", type(exc).__name__)
                    private_rooms = self.gateway.private_rooms
                if not self.gateway.socket.connected:
                    self.gateway.allowed = {r["id"] for r in rooms} | private_rooms
                    await self.gateway.connect()
                    log.info("socket_connected")
                await self.gateway.sync_rooms(rooms, private_rooms)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Exception messages may contain URLs/cookies; log only a safe classification.
                reason = "login_or_identity_required" if isinstance(exc, RuntimeError) else type(exc).__name__
                log.warning("connection_not_ready: %s", reason)
                if self.gateway.socket.connected:
                    await self.gateway.socket.disconnect()
                await asyncio.sleep(15)


async def run(cfg):
    if cfg.mode not in {"simulate", "live"}:
        raise RuntimeError("DZMM_WORKER_MODE must be simulate or live")
    async with httpx.AsyncClient(base_url=cfg.core_url, headers={"X-Core-Token": cfg.core_token}, timeout=20) as core:
        async with httpx.AsyncClient(timeout=20) as platform:
            worker = Worker(cfg, core, platform)
            if cfg.mode == "simulate":
                print("模拟 Worker 已启动：不连接平台，待发消息将标记为 simulated。", flush=True)
                await worker.outbound_loop()
            else:
                async with BrowserSession(cfg) as browser:
                    worker.gateway = Gateway(cfg, browser, worker.inbound)
                    try:
                        async with asyncio.TaskGroup() as group:
                            group.create_task(worker.connection_loop())
                            group.create_task(worker.outbound_loop())
                    finally:
                        if worker.gateway.socket.connected:
                            await worker.gateway.socket.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run(Settings.load()))
    except KeyboardInterrupt:
        pass

import asyncio
import logging
import sys

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
        for attempt in range(3):
            try:
                r = await self.core.post("/internal/inbound", json=data)
                r.raise_for_status()
                return
            except httpx.HTTPError:
                if attempt == 2:
                    log.error("inbound_delivery_failed: Core unavailable; message may be lost")
                    return
                await asyncio.sleep(2)

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
        if self.cfg.mode == "simulate":
            outcome = {"action": "simulated", "error": "simulation"}
            message = f"[模拟回复] {task['text']}"
            encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(message.encode(encoding, errors="replace").decode(encoding), flush=True)
        elif task["kind"] == "group" and self.cfg.bot_token and not task.get("reply_to_message_id"):
            outcome = await send_bot(self.platform, self.cfg, task)
            if outcome.pop("fallback", False):
                outcome = await self.gateway.send_socket(task)
        else:
            outcome = await self.gateway.send_socket(task)
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
        while True:
            try:
                state = "simulating" if self.cfg.mode == "simulate" else (
                    "connected" if self.gateway and self.gateway.socket.connected else "disconnected")
                heartbeat = await self.core.post("/internal/heartbeat", json={"state": state})
                heartbeat.raise_for_status()
                sent = await self.drain_once()
            except httpx.HTTPError:
                log.warning("core_unavailable_or_lease_conflict")
                sent = False
            await asyncio.sleep(0.5 if sent else 2)

    async def connection_loop(self):
        while True:
            try:
                response = await self.core.get("/internal/rooms")
                response.raise_for_status()
                rooms = response.json()
                if not self.gateway.socket.connected:
                    self.gateway.allowed = {r["id"] for r in rooms}
                    await self.gateway.connect()
                    log.info("socket_connected")
                await self.gateway.sync_rooms(rooms)
                await asyncio.sleep(5)
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

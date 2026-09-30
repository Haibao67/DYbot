import asyncio
import logging
import hmac
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from .settings import Settings
from .store import Store, outbox, rooms
from .application.outbound_metrics import outbound_performance
from .application.latency_metrics import LatencyMetrics
from .application.services import GameService, metrics, uid
from .domain.economy import GameError, from_minor
from .persistence.schema import currency, audit, animals, transactions
from .persistence.schema import pending_group_invites
from .invitations import invite_code
import json


class Inbound(BaseModel):
    room: str = Field(min_length=1, max_length=200)
    kind: Literal["group", "private"] = "group"
    message_id: str = Field(min_length=1, max_length=200)
    sender: str = Field(min_length=1, max_length=200)
    name: str = Field(default="", max_length=100)
    text: str = Field(max_length=10000)
    referenced_message_id: str | None = Field(default=None, max_length=200)
    referenced_sender_id: str | None = Field(default=None, max_length=200)


class InviteCard(BaseModel):
    type: Literal["share"]
    shareType: Literal["group_invite"]
    resourceId: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9]+$")


class InviteCandidate(Inbound):
    group_name: str = Field(min_length=1, max_length=160)
    source_content: InviteCard | None = None


class InviteResult(BaseModel):
    status: Literal["joined", "failed"]
    room_id: str | None = Field(default=None, max_length=200)
    error: str | None = Field(default=None, max_length=200)


class Room(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    kind: Literal["group", "private"] = "group"
    enabled: bool = True


class Result(BaseModel):
    lease: str
    action: Literal["begin", "sent", "simulated", "retry", "uncertain", "failed"]
    platform_id: str | None = Field(default=None, max_length=200)
    error: Literal["rate_limited", "rejected", "transport_unknown", "simulation", "invalid_ack"] | None = None
    error_detail: dict[str, str | int | float | bool | None] | None = None

    @field_validator("error_detail")
    @classmethod
    def bound_error_detail(cls, value):
        if value is not None and len(json.dumps(value, ensure_ascii=False)) > 3500:
            raise ValueError("error_detail is too large")
        return value


class Resolution(BaseModel):
    status: Literal["sent", "failed"]
    platform_id: str | None = Field(default=None, max_length=200)


class Simulation(BaseModel):
    text: str = Field(max_length=10000)


class LimiterHeartbeat(BaseModel):
    current_interval_seconds: float = Field(ge=0, le=60)
    last_rate_limited_at: float | None = Field(default=None, ge=0)
    rate_limit_events: int = Field(ge=0)
    adjustments: int = Field(ge=0)
    success_streak: int = Field(ge=0)
    cooldown_remaining_seconds: float = Field(ge=0, le=300)


class Heartbeat(BaseModel):
    state: Literal["simulating", "connected", "disconnected"]
    limiter: LimiterHeartbeat | None = None


class Compensation(BaseModel):
    amount: int = Field(strict=True, ge=-10**12, le=10**12)
    reason: str = Field(min_length=1, max_length=500)
    ticket: str = Field(min_length=1, max_length=120)


def create_app(settings=None):
    cfg = settings or Settings.load()
    lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if len(cfg.core_token) < 24 or len(cfg.admin_token) < 24 or cfg.core_token == cfg.admin_token:
            raise RuntimeError("请运行 python -m dzmm_bot.manage init 生成独立的内部密钥")
        app.state.store = Store(cfg.database_url, secret=cfg.core_token, game_stage=cfg.game_stage,
                               whitelist={p.strip() for p in cfg.game_whitelist.split(",") if p.strip()},
                               admins={p.strip() for p in cfg.game_admins.split(",") if p.strip()},
                               admin_login_password_hash=cfg.game_admin_login_password_hash,
                               auction_cooldown_seconds=cfg.auction_start_cooldown_seconds,
                               race_auto_enabled=cfg.race_auto_enabled,race_timezone=cfg.race_timezone,
                               weather_enabled=cfg.weather_enabled,
                               world_event_policy={"harvest_threshold": int(cfg.world_harvest_threshold),
                                   "player_cooldown": int(cfg.world_event_player_cooldown),
                                   "global_cooldown": int(cfg.world_event_global_cooldown)},
                               race_commentary_options={"enabled": cfg.race_commentary_ai_enabled,
                                   "api_key": cfg.deepseek_api_key, "base_url": cfg.deepseek_base_url,
                                   "model": cfg.deepseek_model,
                                   "timeout_seconds": cfg.race_commentary_timeout_seconds,
                                   "max_chars": cfg.race_commentary_max_chars,
                                   "max_calls_per_race": cfg.race_commentary_max_calls_per_race})
        app.state.heartbeat = None
        app.state.inbound_latency = LatencyMetrics()
        app.state.race_commentary_tasks = set()
        if cfg.race_commentary_ai_enabled:
            schedule_race_commentary(app.state.store)
        async def race_scheduler():
            while True:
                await asyncio.sleep(60)
                try:
                    tick=asyncio.create_task(asyncio.to_thread(app.state.store.reconcile_races))
                    try:
                        await asyncio.shield(tick)
                    except asyncio.CancelledError:
                        # Finish the database transaction before disposing its engine.
                        await tick
                        raise
                except Exception:
                    logging.getLogger(__name__).exception('daily race scheduler failed')
                if cfg.race_commentary_ai_enabled:
                    try:
                        await asyncio.to_thread(app.state.store.process_pending_race_commentary)
                    except Exception:
                        logging.getLogger(__name__).error('race commentary background processing failed')
                try:
                    tick=asyncio.create_task(asyncio.to_thread(app.state.store.reconcile_weather))
                    try:
                        await asyncio.shield(tick)
                    except asyncio.CancelledError:
                        await tick
                        raise
                except Exception:
                    logging.getLogger(__name__).exception('daily weather scheduler failed')
        task=asyncio.create_task(race_scheduler())
        try:
            yield
        finally:
            if task:
                task.cancel()
                try: await task
                except asyncio.CancelledError: pass
            pending=list(app.state.race_commentary_tasks)
            if pending:
                await asyncio.gather(*pending,return_exceptions=True)
            app.state.store.engine.dispose()

    app = FastAPI(title="DZMM Game Core", lifespan=lifespan)

    def schedule_race_commentary(store):
        if any(not task.done() for task in app.state.race_commentary_tasks):
            return None
        async def run():
            try:
                await asyncio.to_thread(store.process_pending_race_commentary)
            except Exception:
                logging.getLogger(__name__).error('race commentary background processing failed')
        task=asyncio.create_task(run(),name="race-commentary-background")
        app.state.race_commentary_tasks.add(task)
        task.add_done_callback(app.state.race_commentary_tasks.discard)
        return task

    def auth(x_core_token: str = Header(default="")):
        if not cfg.core_token or not hmac.compare_digest(x_core_token.encode(), cfg.core_token.encode()):
            raise HTTPException(401, "unauthorized")

    def admin(x_admin_token: str = Header(default="")):
        if not cfg.admin_token or not hmac.compare_digest(x_admin_token.encode(), cfg.admin_token.encode()):
            raise HTTPException(401, "unauthorized")

    @app.get("/healthz")
    def health():
        return {"ok": True, "architecture": "core-worker", "mode": cfg.mode}

    @app.get("/internal/rooms", dependencies=[Depends(auth)])
    def list_rooms():
        with app.state.store.engine.connect() as db:
            return [dict(row) for row in db.execute(select(rooms).where(rooms.c.enabled == 1)).mappings()]

    @app.put("/admin/rooms", dependencies=[Depends(admin)])
    async def put_room(room: Room):
        async with lock:
            with app.state.store.engine.begin() as db:
                existing = db.execute(select(rooms.c.id).where(rooms.c.id == room.id)).first()
                if existing:
                    db.execute(rooms.update().where(rooms.c.id == room.id).values(kind=room.kind, enabled=int(room.enabled)))
                else:
                    db.execute(rooms.insert().values(id=room.id, kind=room.kind, enabled=int(room.enabled)))
                db.execute(audit.insert().values(id=uid(), actor="admin_token", action="room_settings",
                    target_type="room", target_id=room.id, payload_json=json.dumps(room.model_dump()), created_at=time.time()))
        return {"ok": True}

    @app.post("/internal/inbound", dependencies=[Depends(auth)])
    async def inbound(event: Inbound):
        request_started = time.monotonic()
        lock_started = time.monotonic()
        async with lock:
            locked_at = time.monotonic()
            app.state.inbound_latency.observe("core_lock_wait", (locked_at - lock_started) * 1000)
            store_started = time.monotonic()
            try:
                result=app.state.store.receive(event)
            finally:
                app.state.inbound_latency.observe("core_inbound", (time.monotonic() - store_started) * 1000)
                app.state.inbound_latency.observe("core_inbound_total", (time.monotonic() - request_started) * 1000)
        if result.pop("commentary_pending",False):
            schedule_race_commentary(app.state.store)
        return result

    @app.post("/internal/invites", dependencies=[Depends(auth)])
    async def receive_invite(event: InviteCandidate):
        code = invite_code(event.text)
        if not code or (event.source_content and event.source_content.resourceId != code):
            raise HTTPException(422, "invalid invitation link")
        request_started = time.monotonic()
        lock_started = time.monotonic()
        async with lock:
            locked_at = time.monotonic()
            app.state.inbound_latency.observe("core_lock_wait", (locked_at - lock_started) * 1000)
            store_started = time.monotonic()
            try:
                return app.state.store.record_invite(event, code, event.group_name,
                    event.source_content.model_dump() if event.source_content else None)
            finally:
                app.state.inbound_latency.observe("core_invite", (time.monotonic() - store_started) * 1000)
                app.state.inbound_latency.observe("core_inbound_total", (time.monotonic() - request_started) * 1000)

    @app.get("/admin/invites", dependencies=[Depends(admin)])
    def list_invites():
        with app.state.store.engine.connect() as db:
            return [dict(row) for row in db.execute(select(pending_group_invites).order_by(
                pending_group_invites.c.created_at.desc()).limit(50)).mappings()]

    @app.post("/admin/invites/{invite_id}/approve", dependencies=[Depends(admin)])
    async def approve_invite(invite_id: str):
        async with lock:
            status = app.state.store.approve_invite(invite_id)
            if status is None:
                raise HTTPException(404, "invitation not found")
            return {"ok": status == "approved", "status": status}

    @app.post("/internal/invites/claim", dependencies=[Depends(auth)])
    async def claim_invite():
        async with lock:
            return {"invite": app.state.store.claim_invite()}

    @app.post("/internal/invites/{invite_id}/result", dependencies=[Depends(auth)])
    async def finish_invite(invite_id: str, result: InviteResult):
        async with lock:
            if not app.state.store.finish_invite(invite_id, result.status, result.room_id, result.error):
                raise HTTPException(409, "invitation not joining or invalid room")
            return {"ok": True}

    @app.post("/internal/heartbeat", dependencies=[Depends(auth)])
    def heartbeat(body: Heartbeat):
        app.state.heartbeat = {"state": body.state, "time": time.time(),
                               "limiter": body.limiter.model_dump() if body.limiter else None}
        return {"ok": True}

    @app.post("/internal/auctions/finalize", dependencies=[Depends(auth)])
    async def finalize_auction():
        async with lock:
            settled=app.state.store.finalize_auction()
        if cfg.race_commentary_ai_enabled:
            schedule_race_commentary(app.state.store)
        return {"settled": settled}

    @app.post("/admin/simulate", dependencies=[Depends(admin)])
    async def simulate(body: Simulation):
        if cfg.mode != "simulate":
            raise HTTPException(403, "simulation disabled in live mode")
        async with lock:
            with app.state.store.engine.begin() as db:
                if not db.execute(select(rooms.c.id).where(rooms.c.id == "local-test-room")).first():
                    db.execute(rooms.insert().values(id="local-test-room", kind="group", enabled=1))
            result=app.state.store.receive(Inbound(room="local-test-room", message_id=str(uuid.uuid4()),
                                                   sender="local-test-player", name="测试玩家", text=body.text))
        if result.pop("commentary_pending",False):
            schedule_race_commentary(app.state.store)
        return result

    @app.post("/admin/outbound/{task_id}/resolve", dependencies=[Depends(admin)])
    async def resolve(task_id: str, body: Resolution):
        if body.status == "sent" and not body.platform_id:
            raise HTTPException(422, "sent requires platform_id")
        async with lock:
            with app.state.store.engine.connect() as db:
                task = db.execute(select(outbox).where(outbox.c.id == task_id,
                    outbox.c.status == "uncertain")).mappings().first()
            if not task or not app.state.store.transition(task_id, task["lease"], body.status,
                    platform_id=body.platform_id, error="manual_resolution"):
                raise HTTPException(409, "task is not uncertain")
            with app.state.store.engine.begin() as db:
                db.execute(audit.insert().values(id=uid(), actor="admin_token", action="resolve_outbound",
                    target_type="outbound", target_id=task_id, payload_json=json.dumps(body.model_dump()), created_at=time.time()))
        return {"ok": True}

    @app.post("/admin/outbound/clear", dependencies=[Depends(admin)])
    async def clear_outbound():
        async with lock:
            counts = app.state.store.clear_outbound_queue()
            with app.state.store.engine.begin() as db:
                db.execute(audit.insert().values(id=uid(), actor="admin_token", action="clear_outbound_queue",
                    target_type="outbound", target_id="active", payload_json=json.dumps(counts), created_at=time.time()))
        return {"cancelled": counts, "preserved": True}

    @app.post("/internal/outbound/claim", dependencies=[Depends(auth)])
    async def claim():
        async with lock:
            return {"task": app.state.store.claim()}

    @app.post("/internal/outbound/{task_id}/result", dependencies=[Depends(auth)])
    async def result(task_id: str, result: Result):
        if result.action == "sent" and not result.platform_id:
            raise HTTPException(422, "sent requires platform_id")
        async with lock:
            if not app.state.store.transition(task_id, **result.model_dump()):
                raise HTTPException(409, "stale lease or invalid transition")
        return {"ok": True}

    @app.get("/admin/status", dependencies=[Depends(admin)])
    def status():
        with app.state.store.lock, app.state.store.engine.connect() as db:
            counts = {}
            for state in db.execute(select(outbox.c.status)).scalars():
                counts[state] = counts.get(state, 0) + 1
            uncertain = [dict(row) for row in db.execute(select(outbox.c.id, outbox.c.error,
                         outbox.c.error_detail).where(outbox.c.status == "uncertain")
                         .order_by(outbox.c.created.desc()).limit(20)).mappings()]
            failed = [dict(row) for row in db.execute(select(outbox.c.id, outbox.c.created,
                      outbox.c.attempts, outbox.c.error, outbox.c.error_detail)
                      .where(outbox.c.status == "failed").order_by(outbox.c.created.desc()).limit(20)).mappings()]
            for row in [*uncertain, *failed]:
                row["failure_history"] = json.loads(row.pop("error_detail")) if row.get("error_detail") else []
            heartbeat = app.state.heartbeat
            return {"outbound": counts, "uncertain_tasks": uncertain, "failed_tasks": failed,
                    "game": metrics(db),
                    "worker": heartbeat, "worker_stale": not heartbeat or time.time() - heartbeat["time"] > 30}

    @app.get("/admin/performance", dependencies=[Depends(admin)])
    def performance(sample_limit: int = 200):
        if not 1 <= sample_limit <= 1000:
            raise HTTPException(422, "sample_limit must be between 1 and 1000")
        with app.state.store.engine.connect() as db:
            result = outbound_performance(db, app.state.store.clock(), sample_limit)
        heartbeat = app.state.heartbeat
        result["limiter"] = heartbeat.get("limiter") if heartbeat else None
        result["limiter_stale"] = not heartbeat or time.time() - heartbeat["time"] > 30
        result["inbound_latency"] = app.state.inbound_latency.snapshot()
        return result

    async def game_admin(player_id, action, body=None):
        async with lock:
            store = app.state.store
            with store.lock, store.engine.begin() as db:
                if store.engine.dialect.name == "sqlite":
                    db.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    from .persistence.schema import world
                    db.execute(select(world).where(world.c.id == 1).with_for_update()).first()
                try:
                    return GameService(db, store.clock(), store.secret, player_id).admin_action(
                        action, "admin_token", **(body.model_dump() if body else {}))
                except GameError as exc:
                    raise HTTPException(404 if exc.code == "not_joined" else 409, exc.code) from exc

    @app.post("/admin/players/{player_id}/freeze", dependencies=[Depends(admin)])
    async def freeze(player_id: str):
        return await game_admin(player_id, "freeze")

    @app.post("/admin/players/{player_id}/unfreeze", dependencies=[Depends(admin)])
    async def unfreeze(player_id: str):
        return await game_admin(player_id, "unfreeze")

    @app.post("/admin/players/{player_id}/compensate", dependencies=[Depends(admin)])
    async def compensate(player_id: str, body: Compensation):
        if body.amount == 0 or not body.reason.strip() or not body.ticket.strip():
            raise HTTPException(422, "nonzero amount, reason and ticket required")
        return await game_admin(player_id, "compensate", body)

    @app.get("/admin/players/{player_id}/ledger", dependencies=[Depends(admin)])
    def ledger(player_id: str, page: int = 1):
        if page < 1:
            raise HTTPException(422, "invalid page")
        with app.state.store.engine.connect() as db:
            rows = [dict(row) for row in db.execute(select(currency).where(currency.c.player_id == player_id)
                .order_by(currency.c.created_at, currency.c.id).offset((page - 1) * 100).limit(100)).mappings()]
            return [{**row, "amount": from_minor(row["amount"])} for row in rows]

    @app.get("/admin/players/{player_id}/ranch", dependencies=[Depends(admin)])
    def ranch_admin(player_id: str, page: int = 1):
        if page < 1:
            raise HTTPException(422, "invalid page")
        with app.state.store.engine.connect() as db:
            tx_rows = [dict(row) for row in db.execute(select(transactions).where(transactions.c.player_id == player_id)
                .order_by(transactions.c.created_at.desc()).offset((page - 1) * 100).limit(100)).mappings()]
            tx_rows = [{**row, "gross_amount": from_minor(row["gross_amount"]),
                        "tax_amount": from_minor(row["tax_amount"]), "net_amount": from_minor(row["net_amount"])} for row in tx_rows]
            return {"animals": [dict(row) for row in db.execute(select(animals).where(animals.c.player_id == player_id)
                .order_by(animals.c.id).offset((page - 1) * 100).limit(100)).mappings()],
                "transactions": tx_rows}

    @app.get("/admin/players/{player_id}/verify-production", dependencies=[Depends(admin)])
    def verify_player_production(player_id: str):
        from .application.verification import verify_production
        with app.state.store.lock, app.state.store.engine.connect() as db:
            return verify_production(db, app.state.store.clock(), player_id)

    return app


app = create_app()

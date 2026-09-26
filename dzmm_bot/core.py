import asyncio
import hmac
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .settings import Settings
from .store import Store, outbox, rooms
from .application.services import GameService, metrics, uid
from .domain.economy import GameError
from .persistence.schema import currency, audit, animals, transactions
import json


class Inbound(BaseModel):
    room: str = Field(min_length=1, max_length=200)
    message_id: str = Field(min_length=1, max_length=200)
    sender: str = Field(min_length=1, max_length=200)
    name: str = Field(default="玩家", min_length=1, max_length=100)
    text: str = Field(max_length=10000)


class Room(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    kind: Literal["group", "private"] = "group"
    enabled: bool = True


class Result(BaseModel):
    lease: str
    action: Literal["begin", "sent", "simulated", "retry", "uncertain", "failed"]
    platform_id: str | None = Field(default=None, max_length=200)
    error: Literal["rate_limited", "rejected", "transport_unknown", "simulation", "invalid_ack"] | None = None


class Resolution(BaseModel):
    status: Literal["sent", "failed"]
    platform_id: str | None = Field(default=None, max_length=200)


class Simulation(BaseModel):
    text: str = Field(max_length=10000)


class Heartbeat(BaseModel):
    state: Literal["simulating", "connected", "disconnected"]


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
                               whitelist={p.strip() for p in cfg.game_whitelist.split(",") if p.strip()})
        app.state.heartbeat = None
        yield
        app.state.store.engine.dispose()

    app = FastAPI(title="DZMM Game Core", lifespan=lifespan)

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
        async with lock:
            return app.state.store.receive(event)

    @app.post("/internal/heartbeat", dependencies=[Depends(auth)])
    def heartbeat(body: Heartbeat):
        app.state.heartbeat = {"state": body.state, "time": time.time()}
        return {"ok": True}

    @app.post("/admin/simulate", dependencies=[Depends(admin)])
    async def simulate(body: Simulation):
        if cfg.mode != "simulate":
            raise HTTPException(403, "simulation disabled in live mode")
        async with lock:
            with app.state.store.engine.begin() as db:
                if not db.execute(select(rooms.c.id).where(rooms.c.id == "local-test-room")).first():
                    db.execute(rooms.insert().values(id="local-test-room", kind="group", enabled=1))
            return app.state.store.receive(Inbound(room="local-test-room", message_id=str(uuid.uuid4()),
                                                   sender="local-test-player", name="测试玩家", text=body.text))

    @app.post("/admin/outbound/{task_id}/resolve", dependencies=[Depends(admin)])
    async def resolve(task_id: str, body: Resolution):
        if body.status == "sent" and not body.platform_id:
            raise HTTPException(422, "sent requires platform_id")
        async with lock:
            with app.state.store.engine.begin() as db:
                result = db.execute(outbox.update().where(outbox.c.id == task_id, outbox.c.status == "uncertain")
                                    .values(status=body.status, platform_id=body.platform_id, error="manual_resolution", lease=None))
                if not result.rowcount:
                    raise HTTPException(409, "task is not uncertain")
                db.execute(audit.insert().values(id=uid(), actor="admin_token", action="resolve_outbound",
                    target_type="outbound", target_id=task_id, payload_json=json.dumps(body.model_dump()), created_at=time.time()))
        return {"ok": True}

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
            uncertain = [dict(row) for row in db.execute(select(outbox.c.id, outbox.c.error)
                         .where(outbox.c.status == "uncertain")).mappings()]
            heartbeat = app.state.heartbeat
            return {"outbound": counts, "uncertain_tasks": uncertain, "game": metrics(db),
                    "worker": heartbeat, "worker_stale": not heartbeat or time.time() - heartbeat["time"] > 30}

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
            return [dict(row) for row in db.execute(select(currency).where(currency.c.player_id == player_id)
                .order_by(currency.c.created_at, currency.c.id).offset((page - 1) * 100).limit(100)).mappings()]

    @app.get("/admin/players/{player_id}/ranch", dependencies=[Depends(admin)])
    def ranch_admin(player_id: str, page: int = 1):
        if page < 1:
            raise HTTPException(422, "invalid page")
        with app.state.store.engine.connect() as db:
            return {"animals": [dict(row) for row in db.execute(select(animals).where(animals.c.player_id == player_id)
                .order_by(animals.c.id).offset((page - 1) * 100).limit(100)).mappings()],
                "transactions": [dict(row) for row in db.execute(select(transactions).where(transactions.c.player_id == player_id)
                .order_by(transactions.c.created_at.desc()).offset((page - 1) * 100).limit(100)).mappings()]}

    @app.get("/admin/players/{player_id}/verify-production", dependencies=[Depends(admin)])
    def verify_player_production(player_id: str):
        from .application.verification import verify_production
        with app.state.store.lock, app.state.store.engine.connect() as db:
            return verify_production(db, app.state.store.clock(), player_id)

    return app


app = create_app()

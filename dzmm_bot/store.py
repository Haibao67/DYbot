"""Transactional inbox/outbox. One Core process; PostgreSQL or local SQLite."""
import time
import uuid
from pathlib import Path

from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import make_url

from threading import RLock
from .persistence.transport import meta, rooms, players, members, inbox, outbox
from .persistence.migrate import migrate
from .application.command_router import CommandRouter


class Store:
    def __init__(self, url, secret="local-development-only", clock=None, game_stage="full", whitelist=None):
        parsed = make_url(url)
        if parsed.drivername.startswith("sqlite") and parsed.database not in (None, ":memory:"):
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url)
        self.lock = RLock()
        self.clock = clock or time.time
        self.secret = secret
        if game_stage not in ("m0", "ranch", "full"):
            raise ValueError("DZMM_GAME_STAGE must be m0, ranch or full")
        self.game_stage, self.whitelist = game_stage, set(whitelist or [])
        migrate(self.engine)

    def receive(self, event):
        now = self.clock()
        with self.lock, self.engine.begin() as db:
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                from .persistence.schema import world
                db.execute(select(world).where(world.c.id == 1).with_for_update()).first()
            room = db.execute(select(rooms).where(rooms.c.id == event.room, rooms.c.enabled == 1)).mappings().first()
            if not room:
                return {"ok": True, "ignored": True}
            if db.execute(select(inbox.c.id).where(inbox.c.room == event.room, inbox.c.message_id == event.message_id)).first():
                return {"ok": True, "duplicate": True}
            db.execute(inbox.insert().values(id=str(uuid.uuid4()), room=event.room, message_id=event.message_id,
                                            sender=event.sender, text=event.text, created=now))
            reply = CommandRouter(db, now, self.secret, self.game_stage, self.whitelist).dispatch(event, room)
            if reply:
                db.execute(outbox.insert().values(id=str(uuid.uuid4()), room=event.room, kind=room["kind"],
                           text=reply, status="pending", created=now, available=now, attempts=0))
            return {"ok": True, "queued": reply is not None}

    def claim(self):
        now = time.time()
        with self.engine.begin() as db:
            # Never automatically resend a task whose previous worker may have sent it.
            db.execute(update(outbox).where(outbox.c.status == "sending", outbox.c.lease_until < now)
                       .values(status="uncertain", error="worker_lost_after_send_started"))
            db.execute(update(outbox).where(outbox.c.status == "leased", outbox.c.lease_until < now)
                       .values(status="pending", lease=None))
            enabled = set(db.execute(select(rooms.c.id).where(rooms.c.enabled == 1)).scalars())
            blocked = set()
            for task in db.execute(select(outbox).where(outbox.c.status.in_(["pending", "leased", "sending", "uncertain"]))
                                   .order_by(outbox.c.created, outbox.c.id)).mappings().all():
                if task["room"] in blocked or task["room"] not in enabled:
                    continue
                blocked.add(task["room"])
                if task["status"] != "pending" or task["available"] > now:
                    continue
                lease = str(uuid.uuid4())
                db.execute(outbox.update().where(outbox.c.id == task["id"]).values(
                    status="leased", lease=lease, lease_until=now + 60, attempts=task["attempts"] + 1))
                return {**dict(task), "lease": lease, "status": "leased"}
            return None

    def transition(self, task_id, lease, action, platform_id=None, error=None):
        now = time.time()
        with self.engine.begin() as db:
            task = db.execute(select(outbox).where(outbox.c.id == task_id)).mappings().first()
            if not task or task["lease"] != lease:
                return False
            if action == "begin":
                if task["status"] != "leased" or task["lease_until"] < now:
                    return False
                enabled = db.execute(select(rooms.c.id).where(rooms.c.id == task["room"], rooms.c.enabled == 1)).first()
                if not enabled:
                    return False
                values = {"status": "sending", "lease_until": now + 60}
            else:
                if task["status"] not in ("sending", "uncertain"):
                    return task["status"] == action and task["platform_id"] == platform_id
                status = action
                if status == "retry":
                    status = "pending" if task["attempts"] < 3 else "failed"
                values = {"status": status, "platform_id": platform_id, "error": error,
                          "available": now + 60, "lease_until": None}
            db.execute(outbox.update().where(outbox.c.id == task_id).values(**values))
            return True

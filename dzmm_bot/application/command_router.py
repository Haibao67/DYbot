import logging
from sqlalchemy import select
from dzmm_bot.domain.economy import GameError, quantity
from dzmm_bot.persistence.transport import players
from dzmm_bot.presentation.messages import help_text, error, render
from .services import GameService, uid

log = logging.getLogger(__name__)
ANIMALS = {"鸡": "chicken", "羊": "sheep", "牛": "cow", "饲料": "feed"}
PRODUCTS = {"鸡蛋": "egg", "羊毛": "wool", "牛奶": "milk", "全部": "all"}


class CommandRouter:
    def __init__(self, db, now, secret, stage="full", whitelist=None):
        self.db, self.now, self.secret = db, now, secret
        self.stage, self.whitelist = stage, set(whitelist or [])

    def dispatch(self, event, room):
        parts = event.text.strip().split()
        if not parts or not parts[0].startswith("/"):
            return None
        if parts == ["/帮助"]:
            return help_text(self.stage)
        reference = uid()
        try:
            # A rejected command rolls back *all* tentative changes, while the outer
            # transaction retains the inbox and its failure reply for idempotency.
            with self.db.begin_nested():
                service = GameService(self.db, self.now, self.secret, event.sender, event.room, reference)
                service.command_context = {"room": event.room, "message_id": event.message_id, "text": event.text}
                if self.db.execute(select(players.c.id).where(players.c.id == event.sender)).first():
                    self.db.execute(players.update().where(players.c.id == event.sender).values(name=event.name))
                result = self.execute(service, parts, event, room)
                return render(result, reference, self.stage)
        except GameError as exc:
            return error(exc.code, **exc.details)
        except Exception:
            # Unexpected errors must roll back the outer inbox too, allowing a safe retry.
            log.exception("Game command failed before commit")
            raise

    def execute(self, service, p, event, room):
        if p[0] in ("/牧场", "/排行"):
            if self.stage == "m0" or (self.stage == "ranch" and len(p) > 1 and p[1] in ("出售", "升级")):
                raise GameError("unavailable")
            if self.whitelist and event.sender not in self.whitelist:
                raise GameError("whitelist")
        if p == ["/加入"]:
            return service.join(event.name, room["kind"])
        if p == ["/我的"]:
            return service.profile()
        if p == ["/救济"]:
            return service.relief()
        if p[0] == "/排行":
            if len(p) not in (2, 3) or p[1] != "总榜":
                raise GameError("syntax")
            service.account()
            return service.ranking(quantity(p[2]) if len(p) == 3 else 1)
        if p[0] != "/牧场":
            raise GameError("unknown")
        if len(p) in (2, 3) and p[1] == "查看":
            return service.view(quantity(p[2]) if len(p) == 3 else 1)
        if len(p) in (3, 4) and p[1] == "购买" and p[2] in ANIMALS:
            return service.buy(ANIMALS[p[2]], quantity(p[3]) if len(p) == 4 else 1)
        if len(p) == 3 and p[1] == "喂食":
            return service.feed(p[2])
        if p == ["/牧场", "收获"]:
            return service.harvest()
        if p == ["/牧场", "升级"]:
            return service.upgrade()
        if len(p) in (3, 4) and p[1] == "出售" and p[2] in PRODUCTS:
            if p[2] == "全部" and len(p) == 4:
                raise GameError("syntax")
            return service.sell(PRODUCTS[p[2]], quantity(p[3]) if len(p) == 4 else None)
        raise GameError("syntax")

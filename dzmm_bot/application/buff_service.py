"""Common clock-aware Buff policy and minimal P5 craft/use operations."""
import json
import uuid
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from dzmm_bot.domain.buff_rules import BUFFS, calculate_failure_rate
from dzmm_bot.domain.economy import GameError, WEATHER_MULTIPLIERS
from dzmm_bot.persistence.schema import buffs


class BuffService:
    def __init__(self, game):
        self.game = game

    def active_rows(self, player=None, at=None):
        player = player or self.game.player
        at = self.game.now if at is None else at
        return self.game.db.execute(select(buffs).where(buffs.c.player_id == player,
            buffs.c.started_at <= at, buffs.c.expires_at > at)
            .order_by(buffs.c.started_at, buffs.c.buff_type)).mappings().all()

    def is_active(self, buff_type, player=None, at=None):
        player = player or self.game.player
        at = self.game.now if at is None else at
        return self.game.db.execute(select(buffs.c.id).where(buffs.c.player_id == player,
            buffs.c.buff_type == buff_type, buffs.c.started_at <= at, buffs.c.expires_at > at)).first() is not None

    def remaining_time(self, buff_type, player=None):
        player = player or self.game.player
        expires_at = self.game.db.execute(select(buffs.c.expires_at).where(buffs.c.player_id == player,
            buffs.c.buff_type == buff_type, buffs.c.started_at <= self.game.now,
            buffs.c.expires_at > self.game.now)).scalar()
        return max(0, expires_at - self.game.now) if expires_at is not None else 0

    def get_multiplier(self, effect, player=None, at=None):
        player = player or self.game.player
        multiplier = Decimal("1")
        active_types = {row["buff_type"] for row in self.active_rows(player, at)}
        for buff_type in active_types:
            multiplier *= BUFFS.get(buff_type, {}).get("effects", {}).get(effect, Decimal("1"))
        return multiplier

    def effective_multiplier(self, effect, player=None, at=None):
        return self.get_multiplier(effect, player, at)

    def weather_multiplier(self, weather, product, player=None, at=None):
        base = Decimal(str(WEATHER_MULTIPLIERS.get(weather, {}).get(product, 1.0)))
        if base < 1 and self.is_active("greenhouse", player, at):
            return Decimal("1")
        return base

    def calculate_sell_price(self, base_price, channel, player=None, at=None):
        multiplier = self.get_multiplier(f"sell_price:{channel}", player, at)
        return (Decimal(str(base_price)) * multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def calculate_failure_rate(self, base_rate, factory_level, tier, player=None, at=None):
        return calculate_failure_rate(base_rate, factory_level, tier,
                                      master_meal=self.is_active("master_meal", player, at))

    def craft_buff_item(self, item):
        rule = BUFFS.get(item)
        if not rule:
            raise GameError("buff_item", name=item)
        self.game.account(True)
        for material, amount in rule["inputs"].items():
            self.game.inventory_change(material, -amount, "buff_craft", f"{self.game.reference}:{material}")
        self.game.inventory_change(item, 1, "buff_craft_output", self.game.reference)
        self.game.event("buff_item_crafted", {"item": item, "inputs": rule["inputs"]})
        return {"kind": "buff_craft", "item": item, "rule": rule,
                "stocks": {material: self.game.stock(material) for material in rule["inputs"]},
                "quantity": self.game.stock(item)}

    def activate(self, item):
        rule = BUFFS.get(item)
        if not rule:
            raise GameError("buff_item", name=item)
        self.game.account(True)
        self.game.inventory_change(item, -1, "buff_use", self.game.reference)
        row = self.game.db.execute(select(buffs).where(buffs.c.player_id == self.game.player,
            buffs.c.buff_type == item).with_for_update()).mappings().first()
        if row and row["expires_at"] > self.game.now and rule["policy"] == "extend":
            started_at = row["started_at"]
            expires_at = row["expires_at"] + rule["duration"]
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["activations"] = int(metadata.get("activations", 1)) + 1
            self.game.db.execute(buffs.update().where(buffs.c.id == row["id"]).values(
                source=rule["source"], expires_at=expires_at, magnitude=rule["magnitude"],
                metadata_json=json.dumps(metadata, ensure_ascii=False)))
        else:
            started_at, expires_at = self.game.now, self.game.now + rule["duration"]
            values = dict(source=rule["source"], started_at=started_at, expires_at=expires_at,
                          magnitude=rule["magnitude"], metadata_json=json.dumps({"activations": 1}, ensure_ascii=False))
            if row:
                self.game.db.execute(buffs.update().where(buffs.c.id == row["id"]).values(**values))
            else:
                self.game.db.execute(buffs.insert().values(id=str(uuid.uuid4()), player_id=self.game.player,
                    buff_type=item, **values))
        self.game.event("buff_activated", {"item": item, "expires_at": expires_at})
        return {"kind": "buff_activated", "item": item, "rule": rule,
                "started_at": started_at, "expires_at": expires_at, "remaining": expires_at - self.game.now}

    def profile_buffs(self, player=None):
        result = []
        for row in self.active_rows(player):
            rule = BUFFS.get(row["buff_type"])
            if not rule:
                continue
            result.append({"item": row["buff_type"], "name": rule["name"], "emoji": rule["emoji"],
                "description": rule["description"], "magnitude": row["magnitude"], "started_at": row["started_at"],
                "expires_at": row["expires_at"], "remaining": row["expires_at"] - self.game.now})
        return result

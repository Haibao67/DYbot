"""Audited player transfers and self-directed game-admin grants."""
import json
import re
from decimal import Decimal

from sqlalchemy import select

from dzmm_bot.domain.buff_rules import BUFF_NAMES
from dzmm_bot.domain.economy import GameError
from dzmm_bot.domain.farm_rules import CROP_NAMES, FEED_NAMES
from dzmm_bot.domain.factory_rules import RECIPE_NAMES
from dzmm_bot.persistence.schema import accounts, audit
from dzmm_bot.persistence.transport import inbox, players
from .services import GameService, uid


def coin_amount(raw):
    if not isinstance(raw, str) or not re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", raw):
        raise GameError("transfer_amount")
    value = Decimal(raw)
    if not Decimal("0") < value <= Decimal("1000000"):
        raise GameError("transfer_amount")
    return value


GRANT_ITEMS = {
    "饲料": "feed", "精饲料": "premium_feed",
    "鸡蛋": "egg", "羊毛": "wool", "牛奶": "milk",
    "蛋糕": "cake", "毛衣": "sweater", "奶酪": "cheese",
    "礼盒": "gift_box", "羽绒服": "down_coat", "奶酪拼盘": "cheese_platter",
    "大礼包": "grand_gift",
    **BUFF_NAMES, **CROP_NAMES, **FEED_NAMES,
    **RECIPE_NAMES,
    **{name + "种子": "seed_" + code for name, code in CROP_NAMES.items()},
}


class AssetService(GameService):
    def transfer(self, target_id, referenced_message_id, amount):
        self.account(True)
        if not target_id or not referenced_message_id:
            raise GameError("transfer_reference")
        if target_id == self.player:
            raise GameError("transfer_self")
        source_sender = self.db.execute(select(inbox.c.sender).where(
            inbox.c.room == self.room, inbox.c.message_id == referenced_message_id)).scalar_one_or_none()
        if source_sender != target_id:
            raise GameError("transfer_reference")
        target_account = self.db.execute(select(accounts).where(
            accounts.c.player_id == target_id).with_for_update()).mappings().first()
        if target_account is None or target_account["status"] != "active":
            raise GameError("transfer_target")
        self.ledger(-amount, "player_transfer_out", "player_transfer", self.reference)
        recipient = GameService(self.db, self.now, self.secret, target_id, self.room, self.reference)
        recipient.ledger(amount, "player_transfer_in", "player_transfer", self.reference)
        self.event("player_transfer", {"recipient": target_id, "amount": str(amount)})
        name = self.db.execute(select(players.c.name).where(players.c.id == target_id)).scalar_one()
        return {"kind": "player_transfer", "recipient": name, "amount": amount,
                "balance": self.balance()}

    def grant_coins(self, amount):
        self.account(True)
        self.ledger(amount, "admin_self_grant", "admin_coin_grant", self.reference)
        self._audit_grant("admin_coin_grant", {"amount": str(amount)})
        return {"kind": "admin_coin_grant", "amount": amount, "balance": self.balance()}

    def grant_item(self, item_name, quantity):
        self.account(True)
        item_code = GRANT_ITEMS.get(item_name)
        if item_code is None:
            raise GameError("admin_item")
        if not 1 <= quantity <= 1000:
            raise GameError("admin_item_quantity")
        self.inventory_change(item_code, quantity, "admin_item_grant", self.reference)
        self._audit_grant("admin_item_grant", {"item": item_code, "quantity": quantity})
        return {"kind": "admin_item_grant", "name": item_name, "quantity": quantity,
                "stock": self.stock(item_code)}

    def _audit_grant(self, action, payload):
        self.db.execute(audit.insert().values(id=uid(), actor=self.player, action=action,
            target_type="player", target_id=self.player,
            payload_json=json.dumps(payload), created_at=self.now))

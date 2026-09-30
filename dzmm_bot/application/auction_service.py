"""Database-backed, single-slot live auctions. All mutations share the caller's transaction."""
import json
import math
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select

from dzmm_bot.domain.economy import GameError, from_minor
from dzmm_bot.domain.horse_rules import CAPACITIES, TRAITS, stage
from dzmm_bot.persistence.schema import (auction_bids, auctions,
    fund_reservations, horse_auction_custody, horse_pregnancies, horse_stables, horses)
from dzmm_bot.persistence.transport import outbox, players, rooms
from dzmm_bot.presentation.formatters import name_escape
from dzmm_bot.presentation.compact import compact_text, decorate
from .services import GameService, uid

AUCTION_SECONDS = 60
EXTENSION_SECONDS = 10
QUICK_BID = 10000  # cents
COMMISSION = Decimal("0.08")
START_COOLDOWN_SECONDS = 300
MAX_PRICE_CENTS = 100_000_000_00
FORBIDDEN_TITLES = ("人民币", "支付宝", "微信转账", "赌博", "赌注", "密码", "验证码", "账号", "色情")


def price_cents(raw):
    if not isinstance(raw, str) or not raw.isdecimal() or len(raw) > 10:
        raise GameError("auction_price")
    value = int(raw) * 100
    if value <= 0 or value > MAX_PRICE_CENTS:
        raise GameError("auction_price")
    return value


def _player_name(db, player_id):
    return db.execute(select(players.c.name).where(players.c.id == player_id)).scalar() or "玩家"


def _auction_text(db, row, now, *, heading="—— 🔨 当前拍卖 ——"):
    from dzmm_bot.presentation.formatters import money, name_escape
    remaining = max(0, math.ceil(row["ends_at"] - now))
    title = name_escape(row["title"])
    highest = _player_name(db, row["highest_bidder_id"]) if row["highest_bidder_id"] else "暂无"
    lines = [heading, f"【{title}】", f"当前价：{money(from_minor(row['current_price']))} 币",
             f"最高出价：{name_escape(highest)}", f"出价次数：{row['bid_count']}",
             f"剩余：{remaining} 秒"]
    if row["auction_type"] == "horse":
        horse = json.loads(row["asset_snapshot_json"])
        lines.insert(2, f"🐎 G{horse['generation']}｜{'公' if horse['sex'] == 'male' else '母'}｜{horse['age_stage']}")
        from dzmm_bot.presentation.horses import TRAIT_NAMES
        lines.insert(3, "｜".join(f"{TRAIT_NAMES[key]} {horse[key]}（{horse['growth_' + key]}）" for key in TRAITS))
        lines.insert(4, f"繁育：{horse['breeding_count']}/5")
        from dzmm_bot.presentation.racing import affinity_lines
        lines[5:5] = affinity_lines(horse)
        record = horse.get('race_record_summary')
        if record:
            lines.append(f"🏆 {record['starts']}战{record['wins']}胜｜奖金 {from_minor(record['total_prize'])} 币")
    lines.append("/拍 | /跟：加 100 币 | /拍 金额：指定出价")
    return compact_text(decorate("\n".join(lines)))


def _broadcast(db, now, text, source_room=None):
    text = compact_text(decorate(text))
    for room in db.execute(select(rooms).where(rooms.c.kind == "group", rooms.c.enabled == 1)).mappings():
        if room["id"] == source_room:
            continue
        db.execute(outbox.insert().values(id=uid(), room=room["id"], kind="group",
            text=text, status="pending", created=now, available=now, attempts=0))


class AuctionService(GameService):
    def current(self, lock=False):
        statement = select(auctions).where(auctions.c.status == "active")
        if lock:
            statement = statement.with_for_update()
        return self.db.execute(statement).mappings().first()

    def _slot(self):
        # Store.receive locks world_state before calling a router. The partial index
        # also protects direct callers and concurrent PostgreSQL transactions.
        from dzmm_bot.persistence.schema import world
        self.db.execute(select(world.c.id).where(world.c.id == 1).with_for_update()).first()

    def _cooldown(self, admin=False):
        if admin:
            return
        latest = self.db.execute(select(func.max(auctions.c.created_at)).where(
            auctions.c.seller_id == self.player)).scalar()
        cooldown = getattr(self, "start_cooldown_seconds", START_COOLDOWN_SECONDS)
        if latest is not None and latest + cooldown > self.now:
            raise GameError("auction_cooldown", remaining=math.ceil(latest + cooldown - self.now))

    def start_special(self, title, starting_price, *, admin=False):
        self.account(True)
        if not 1 <= len(title) <= 80 or any(ord(c) < 32 for c in title) or any(
                bad in title for bad in FORBIDDEN_TITLES):
            raise GameError("auction_title")
        return self._start("special", title, starting_price, admin=admin)

    def start_horse(self, identifier, starting_price, *, admin=False):
        from .horse_service import HorseService
        self.account(True)
        horse = HorseService(self.db, self.now, self.secret, self.player, self.room,
                             self.reference).resolve_horse(identifier)
        from .horse_asset_lock import assert_horse_mutable
        assert_horse_mutable(self.db, horse['id'])
        if self.db.execute(select(horse_pregnancies.c.id).where(
                horse_pregnancies.c.mother_id == horse["id"],
                horse_pregnancies.c.status == "pending")).first():
            raise GameError("horse_pregnant")
        snapshot = {key: horse[key] for key in ("id", "name", "sex", "generation",
            "born_at", "father_id", "mother_id", "breeding_count", "birth_traits_json",
            *(TRAITS), *("growth_" + trait for trait in TRAITS))}
        snapshot["age_stage"] = stage(horse["born_at"], self.now)
        from .horse_racing_service import HorseRacingService
        from .race_queries import record_summary
        profile = HorseRacingService(self.db,self.now).profile(horse['id'])
        snapshot.update(affinities=profile['affinities'],skills=profile['skills'],
            skills_state=profile['skills_state'],race_record_summary=record_summary(self.db,horse['id']))
        return self._start("horse", horse["name"] or horse["id"][:8], starting_price,
                           horse=horse, snapshot=snapshot, admin=admin)

    def item_code(self, name):
        from .asset_service import GRANT_ITEMS
        from dzmm_bot.domain.animal_rarity import SHINY_ANIMAL_ITEMS
        from dzmm_bot.persistence.schema import stocks
        code=SHINY_ANIMAL_ITEMS.get(name) or GRANT_ITEMS.get(name)
        if code:
            return code
        # Inventory codes remain an escape hatch for future registered materials.
        if self.db.execute(select(stocks.c.item_code).where(
                stocks.c.player_id==self.player,stocks.c.item_code==name)).first():
            return name
        return None

    def start_asset(self, name, count, starting_price, *, admin=False, horse=False):
        self.account(True)
        if horse:
            if count!=1:
                raise GameError('auction_item')
            return self.start_horse(name,starting_price,admin=admin)
        if self.item_code(name):
            return self.start_item(name,count,starting_price,admin=admin)
        if count!=1:
            raise GameError('auction_item')
        return self.start_horse(name,starting_price,admin=admin)

    def start_item(self, name, count, starting_price, *, admin=False):
        from dzmm_bot.domain.animal_rarity import SHINY_ANIMAL_ITEMS
        self.account(True)
        code = self.item_code(name)
        if not code or not 1 <= count <= 100000:
            raise GameError("auction_item")
        if code in SHINY_ANIMAL_ITEMS.values() and self.stock(code) < count:
            self.package_shiny_animals(code, count - self.stock(code))
        if self.stock(code) < count:
            raise GameError("stock", item=code, missing=count - self.stock(code))
        return self._start("item", f"{name}×{count}", starting_price,
            item={"code": code, "name": name, "quantity": count}, admin=admin)

    def _start(self, auction_type, title, starting_price, *, horse=None, item=None, snapshot=None, admin=False):
        self._slot()
        if self.current():
            raise GameError("auction_busy")
        self._cooldown(admin)
        auction_id = uid()
        self.db.execute(auctions.insert().values(id=auction_id, auction_type=auction_type,
            seller_id=self.player, room_id=self.room, title=title, description=None,
            asset_type="horse" if horse else "item" if item else None,
            asset_id=horse["id"] if horse else item["code"] if item else None,
            asset_snapshot_json=json.dumps(snapshot or item, ensure_ascii=False) if (snapshot or item) else None,
            starting_price=starting_price, current_price=starting_price,
            highest_bidder_id=None, starts_at=self.now, ends_at=self.now + AUCTION_SECONDS,
            status="active", bid_count=0, created_at=self.now, settled_at=None))
        if horse:
            self.db.execute(horse_auction_custody.insert().values(horse_id=horse["id"],
                auction_id=auction_id, owner_id=self.player, status="locked", created_at=self.now))
        if item:
            self.inventory_change(item["code"], -item["quantity"], "auction_escrow", auction_id)
        row = self.current()
        text = _auction_text(self.db, row, self.now, heading="🔨 拍卖开始！")
        _broadcast(self.db, self.now, text, self.room)
        self.event("auction_start", {"auction_id": auction_id, "type": auction_type})
        return text

    def panel(self):
        row = self.current()
        return _auction_text(self.db, row, self.now) if row else (
            "—— 🔨 铃露拍卖场 ——\n当前没有正在进行的拍卖。\n发起：/拍卖 名称 起拍价")

    def detail(self):
        return self.panel()

    def bid(self, amount=None):
        self.account(True)
        self._slot()
        row = self.current(lock=True)
        if not row:
            raise GameError("auction_none")
        if row["ends_at"] <= self.now:
            raise GameError("auction_ended")
        if row["seller_id"] == self.player:
            raise GameError("auction_self")
        if row["highest_bidder_id"] == self.player:
            raise GameError("auction_leading")
        bid_amount = amount if amount is not None else row["current_price"] + QUICK_BID
        if bid_amount <= row["current_price"] or bid_amount > MAX_PRICE_CENTS:
            raise GameError("auction_low", current=from_minor(row["current_price"]))
        available = self.available_minor()
        if available < bid_amount:
            raise GameError("auction_balance", required=from_minor(bid_amount), available=from_minor(available))
        prior = self.db.execute(select(fund_reservations).where(
            fund_reservations.c.reference_type == "auction_bid",
            fund_reservations.c.reference_id.in_(select(auction_bids.c.id).where(
                auction_bids.c.auction_id == row["id"], auction_bids.c.status == "active")),
            fund_reservations.c.status == "active").with_for_update()).mappings().first()
        if row["highest_bidder_id"] and prior is None:
            raise GameError("system")
        if prior:
            self.db.execute(fund_reservations.update().where(fund_reservations.c.id == prior["id"])
                            .values(status="released", released_at=self.now))
            self.db.execute(auction_bids.update().where(auction_bids.c.auction_id == row["id"],
                auction_bids.c.status == "active").values(status="outbid"))
        bid_id = uid()
        self.db.execute(fund_reservations.insert().values(id=uid(), player_id=self.player,
            amount=bid_amount, reason="auction_bid", reference_type="auction_bid",
            reference_id=bid_id, status="active", created_at=self.now, released_at=None))
        self.db.execute(auction_bids.insert().values(id=bid_id, auction_id=row["id"],
            bidder_id=self.player, amount=bid_amount, created_at=self.now, status="active"))
        remaining = row["ends_at"] - self.now
        ends_at = self.now + EXTENSION_SECONDS if remaining <= EXTENSION_SECONDS else row["ends_at"]
        self.db.execute(auctions.update().where(auctions.c.id == row["id"],
            auctions.c.status == "active").values(current_price=bid_amount,
            highest_bidder_id=self.player, bid_count=row["bid_count"] + 1, ends_at=ends_at))
        updated = self.current()
        text = _auction_text(self.db, updated, self.now, heading="🔥 竞价成功！")
        _broadcast(self.db, self.now, text, self.room)
        return text

    def mine(self):
        rows = self.db.execute(select(auctions).where(auctions.c.seller_id == self.player)
            .order_by(auctions.c.created_at.desc()).limit(10)).mappings().all()
        if not rows:
            return "你还没有发起过拍卖。"
        return "—— 🔨 我的拍卖 ——\n" + "\n".join(
            f"【{name_escape(row['title'])}】｜{row['status']}｜{from_minor(row['current_price'])} 币" for row in rows)

    def my_bids(self):
        rows = self.db.execute(select(auction_bids, auctions.c.title, auctions.c.status.label("auction_status"))
            .join(auctions, auctions.c.id == auction_bids.c.auction_id)
            .where(auction_bids.c.bidder_id == self.player)
            .order_by(auction_bids.c.created_at.desc()).limit(10)).mappings().all()
        if not rows:
            return "你还没有参与竞拍。"
        return "—— 💰 我的竞拍 ——\n" + "\n".join(
            f"【{name_escape(row['title'])}】｜{from_minor(row['amount'])} 币｜{row['status']}" for row in rows)

    def history(self):
        rows = self.db.execute(select(auctions).where(auctions.c.status.in_(["sold", "expired"]))
            .order_by(auctions.c.settled_at.desc()).limit(10)).mappings().all()
        if not rows:
            return "暂无拍卖记录。"
        return "—— 📜 铃露拍卖记录 ——\n" + "\n".join(
            f"【{name_escape(row['title'])}】｜{from_minor(row['current_price'])} 币｜{'成交' if row['status'] == 'sold' else '流拍'}"
            for row in rows)

    def claim_horse(self, identifier=None):
        self.account(True)
        statement = select(horse_auction_custody, horses.c.name).join(
            horses, horses.c.id == horse_auction_custody.c.horse_id).where(
            horse_auction_custody.c.owner_id == self.player,
            horse_auction_custody.c.status == "holding")
        if identifier:
            statement = statement.where((horses.c.name == identifier) | horses.c.id.startswith(identifier))
        held = self.db.execute(statement.limit(2)).mappings().all()
        if not held:
            raise GameError("auction_holding_none")
        if len(held) > 1:
            raise GameError("horse_ambiguous")
        from .horse_service import HorseService
        stable = HorseService(self.db, self.now, self.secret, self.player, self.room, self.reference)
        level = stable.stable()["level"]
        if stable.occupied_slots() >= CAPACITIES[level - 1]:
            raise GameError("horse_capacity")
        self.db.execute(horse_auction_custody.delete().where(
            horse_auction_custody.c.horse_id == held[0]["horse_id"],
            horse_auction_custody.c.status == "holding"))
        return f"✅ 已领取拍卖马【{held[0]['name'] or held[0]['horse_id'][:8]}】。"

    def cancel(self):
        self._slot()
        row = self.current(lock=True)
        if not row:
            raise GameError("auction_none")
        _release_reservation(self.db, row["id"], self.now)
        if row["asset_type"] == "horse":
            self.db.execute(horse_auction_custody.delete().where(
                horse_auction_custody.c.horse_id == row["asset_id"]))
        elif row["asset_type"] == "item":
            item = json.loads(row["asset_snapshot_json"])
            seller = GameService(self.db, self.now, self.secret, row["seller_id"], self.room, row["id"])
            seller.inventory_change(item["code"], item["quantity"], "auction_return", row["id"])
        self.db.execute(auction_bids.update().where(auction_bids.c.auction_id == row["id"],
            auction_bids.c.status == "active").values(status="outbid"))
        self.db.execute(auctions.update().where(auctions.c.id == row["id"]).values(
            status="cancelled", settled_at=self.now))
        _broadcast(self.db, self.now, f"🔨 拍卖已由管理员下架：【{name_escape(row['title'])}】")
        return f"✅ 已下架拍卖【{name_escape(row['title'])}】。"


def _release_reservation(db, auction_id, now, status="released"):
    bid = db.execute(select(auction_bids).where(auction_bids.c.auction_id == auction_id,
        auction_bids.c.status == "active").with_for_update()).mappings().first()
    if bid:
        released = db.execute(fund_reservations.update().where(
            fund_reservations.c.reference_type == "auction_bid",
            fund_reservations.c.reference_id == bid["id"],
            fund_reservations.c.status == "active").values(status=status, released_at=now))
        if released.rowcount != 1:
            raise GameError("system")
    return bid


def finalize_due(db, now, secret):
    """Settle once; caller holds a transaction and the global world lock."""
    row = db.execute(select(auctions).where(auctions.c.status == "active",
        auctions.c.ends_at <= now).with_for_update()).mappings().first()
    if not row:
        return None
    bid = _release_reservation(db, row["id"], now, "settled")
    if bid:
        horse_held = False
        winner = GameService(db, now, secret, row["highest_bidder_id"], row["room_id"], row["id"])
        winner.ledger(-from_minor(row["current_price"]), "auction_purchase", "auction_purchase", row["id"])
        fee = int((Decimal(row["current_price"]) * COMMISSION).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        seller = GameService(db, now, secret, row["seller_id"], row["room_id"], row["id"])
        seller.ledger(from_minor(row["current_price"] - fee), "auction_sale", "auction_sale", row["id"])
        seller.ledger(from_minor(fee), "auction_fee", "auction_fee", row["id"], system=True)
        db.execute(auction_bids.update().where(auction_bids.c.id == bid["id"]).values(status="won"))
        if row["asset_type"] == "horse":
            from .horse_service import HorseService
            stable = HorseService(db, now, secret, row["highest_bidder_id"], row["room_id"], row["id"])
            level = stable.stable(create=True)["level"]
            full = stable.occupied_slots() >= CAPACITIES[level - 1]
            original_name = json.loads(row["asset_snapshot_json"])["name"]
            collision = original_name and db.execute(select(horses.c.id).where(
                horses.c.player_id == row["highest_bidder_id"], horses.c.status == "active",
                horses.c.name == original_name)).first()
            transferred_name = ((original_name[:70] + "·" + row["asset_id"][:6])
                                if collision else original_name)
            db.execute(horses.update().where(horses.c.id == row["asset_id"],
                horses.c.player_id == row["seller_id"]).values(player_id=row["highest_bidder_id"],
                name=transferred_name, version=horses.c.version + 1))
            if full:
                horse_held = True
                db.execute(horse_auction_custody.update().where(
                    horse_auction_custody.c.horse_id == row["asset_id"]).values(
                    owner_id=row["highest_bidder_id"], status="holding"))
            else:
                db.execute(horse_auction_custody.delete().where(
                    horse_auction_custody.c.horse_id == row["asset_id"]))
        elif row["asset_type"] == "item":
            item = json.loads(row["asset_snapshot_json"])
            winner.inventory_change(item["code"], item["quantity"], "auction_receive", row["id"])
        status = "sold"
        text = (f"🔨 拍卖结束！\n【{name_escape(row['title'])}】\n成交价：{from_minor(row['current_price'])} 币\n"
                f"得主：{name_escape(_player_name(db, row['highest_bidder_id']))}\n"
                f"发起者：{name_escape(_player_name(db, row['seller_id']))}")
        if horse_held:
            text += "\n🐎 得主马厩已满，马匹暂存拍卖场；腾出马位后输入 /领取拍卖马。"
        if row["asset_type"] == "item":
            from dzmm_bot.domain.animal_rarity import SHINY_ANIMAL_ITEMS
            item = json.loads(row["asset_snapshot_json"])
            if item["code"] in SHINY_ANIMAL_ITEMS.values():
                text += f"\n已存入库存；放养：/放养 {item['name']} {item['quantity']}"
    else:
        status = "expired"
        if row["asset_type"] == "horse":
            db.execute(horse_auction_custody.delete().where(
                horse_auction_custody.c.horse_id == row["asset_id"]))
        elif row["asset_type"] == "item":
            item = json.loads(row["asset_snapshot_json"])
            seller = GameService(db, now, secret, row["seller_id"], row["room_id"], row["id"])
            seller.inventory_change(item["code"], item["quantity"], "auction_return", row["id"])
        text = f"🔨 拍卖结束\n【{name_escape(row['title'])}】\n本场无人出价。"
    db.execute(auctions.update().where(auctions.c.id == row["id"],
        auctions.c.status == "active").values(status=status, settled_at=now))
    _broadcast(db, now, text)
    return text

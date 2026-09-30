import hashlib
import json
import uuid
from decimal import Decimal
from sqlalchemy import select, func
from dzmm_bot.domain.economy import (GameError, tax, capacity, upgrade_price, local_date,
                                    market_price, market_window, market_trend, market_change_percent, market_fee,
                                    to_minor, from_minor, seed_for, interval, RUIHE_RANCH_VERSION,
                                    production_multiplier, production_quantity, affection_speed, ranch_duration_multiplier)
from dzmm_bot.persistence.schema import (accounts, currency, stocks, inventory, world,
    configs, audit, events, ranches, animals, products, transactions, fund_reservations)
from dzmm_bot.persistence.transport import players, members
from dzmm_bot.domain.factory_rules import RECIPES
from dzmm_bot.domain.market_catalog import with_tradeable_products
from dzmm_bot.domain.animal_rarity import (SHINY_ANIMAL_ITEMS, RARITY_VERSION, RARITY_RULESETS, animal_production_base,
    purchase_rarity, rarity_draw, harvest_base_bonus, increase_probability, PREMIUM_PROBABILITY)


def uid():
    return str(uuid.uuid4())


class GameService:
    def __init__(self, db, now, secret, player=None, room=None, reference=None):
        self.db, self.now, self.secret = db, now, secret
        self.player, self.room, self.reference = player, room, reference or uid()
        self.version = db.execute(select(world.c.config_version).where(world.c.id == 1)).scalar_one()
        self.config = self.get_config(self.version)
        self.command_context = {}
        self.weather_enabled = False
        from .buff_service import BuffService
        self.buffs = BuffService(self)

    def get_config(self, version):
        config = json.loads(self.db.execute(select(configs.c.payload_json).where(
            configs.c.version == version, configs.c.status == "published")).scalar_one())
        return with_tradeable_products(config)

    def account(self, mutate=False):
        row = self.db.execute(select(accounts).where(accounts.c.player_id == self.player).with_for_update()).mappings().first()
        if not row:
            raise GameError("not_joined")
        if mutate and row["status"] != "active":
            raise GameError("frozen")
        return row

    def event(self, kind, payload=None, reference=None):
        self.db.execute(events.insert().values(id=reference or self.reference, player_id=self.player,
            kind=kind, config_version=self.version,
            payload_json=json.dumps({**(payload or {}), "command": self.command_context}, default=str), created_at=self.now))

    def balance(self):
        return from_minor(self.account()["balance"])

    def available_minor(self):
        account = self.account()
        reserved = self.db.execute(select(func.coalesce(func.sum(fund_reservations.c.amount), 0)).where(
            fund_reservations.c.player_id == self.player,
            fund_reservations.c.status == "active")).scalar_one()
        return account["balance"] - reserved

    def wallet(self):
        return {"kind": "wallet", "balance": self.balance(),
                "available": from_minor(self.available_minor())}

    def check_in(self):
        self.account(True)
        claimed = self.ledger(30, "daily_check_in", "daily_check_in", local_date(self.now))
        if claimed:
            self.event("daily_check_in", {"amount": 30})
        return {"kind": "check_in", "claimed": claimed, "amount": 30,
                "balance": self.balance()}

    def ledger(self, amount, reason, ref_type, ref_id, system=False):
        minor = to_minor(amount)
        player = None if system else self.player
        prior = self.db.execute(select(currency).where(currency.c.player_id == player,
            currency.c.reference_type == ref_type, currency.c.reference_id == ref_id)).mappings().first()
        if prior:
            if prior["amount"] != minor or prior["reason"] != reason:
                raise GameError("reference_conflict")
            return False
        if system:
            balance = self.db.execute(select(world.c.lottery_pool_balance).where(world.c.id == 1).with_for_update()).scalar_one()
        else:
            balance = self.account()["balance"]
        if balance + minor < 0 or (not system and minor < 0 and self.available_minor() + minor < 0):
            shortfall = -(balance + minor) if system else -(self.available_minor() + minor)
            raise GameError("balance", missing=from_minor(shortfall), required=from_minor(-minor),
                            available=from_minor(balance if system else self.available_minor()),
                            balance=from_minor(balance))
        self.db.execute(currency.insert().values(id=uid(), player_id=player, amount=minor, reason=reason,
            reference_type=ref_type, reference_id=ref_id, source_room_id=self.room, created_at=self.now))
        if system:
            self.db.execute(world.update().where(world.c.id == 1).values(lottery_pool_balance=balance + minor, updated_at=self.now))
        else:
            self.db.execute(accounts.update().where(accounts.c.player_id == player).values(balance=balance + minor,
                version=accounts.c.version + 1))
        return True

    def stock(self, item):
        return self.db.execute(select(stocks.c.quantity).where(stocks.c.player_id == self.player, stocks.c.item_code == item)).scalar() or 0

    def inventory_change(self, item, delta, kind, ref, config_version=None):
        if type(delta) is not int or abs(delta) > 10**15:
            raise GameError("quantity")
        prior = self.db.execute(select(inventory).where(inventory.c.player_id == self.player,
            inventory.c.item_code == item, inventory.c.reference_type == kind, inventory.c.reference_id == ref)).mappings().first()
        if prior:
            if prior["delta"] != delta:
                raise GameError("reference_conflict")
            return
        old = self.stock(item)
        if old + delta < 0:
            raise GameError("stock", item=item, missing=-(old + delta), required=-delta, available=old)
        self.db.execute(inventory.insert().values(id=uid(), player_id=self.player, item_code=item, delta=delta,
            reference_type=kind, reference_id=ref, config_version=config_version or self.version, created_at=self.now))
        exists = self.db.execute(select(stocks.c.player_id).where(stocks.c.player_id == self.player, stocks.c.item_code == item)).first()
        if exists:
            self.db.execute(stocks.update().where(stocks.c.player_id == self.player, stocks.c.item_code == item)
                .values(quantity=old + delta, version=stocks.c.version + 1))
        else:
            self.db.execute(stocks.insert().values(player_id=self.player, item_code=item, quantity=old + delta, version=1))

    def join(self, name, kind):
        if self.db.execute(select(accounts.c.player_id).where(accounts.c.player_id == self.player)).first():
            raise GameError("joined")
        if kind != "group":
            raise GameError("group_join")
        existing = self.db.execute(select(players.c.id).where(players.c.id == self.player)).first()
        if existing:
            self.db.execute(players.update().where(players.c.id == self.player).values(name=name))
        else:
            self.db.execute(players.insert().values(id=self.player, name=name, created=self.now))
        if not self.db.execute(select(members).where(members.c.room == self.room, members.c.player == self.player)).first():
            self.db.execute(members.insert().values(room=self.room, player=self.player, joined=self.now))
        self.db.execute(accounts.insert().values(player_id=self.player, balance=0, joined_at=self.now, status="active", version=1))
        self.ledger(self.config["starting_balance"], "join_reward", "join", f"join:{self.player}")
        self.create_ranch()
        self.event("join")
        return {"kind": "join", "amount": self.config["starting_balance"], "balance": self.balance()}

    def profile(self):
        account = self.account()
        return {"kind": "profile", **account, "balance": from_minor(account["balance"]),
                "name": self.db.execute(select(players.c.name).where(players.c.id == self.player)).scalar_one(),
                "buffs": self.buffs.profile_buffs()}

    def relief(self):
        account = self.account(True)
        if account["relief_claimed_on"] == local_date(self.now):
            raise GameError("relief_used")
        missing = to_minor(self.config["relief_floor"]) - account["balance"]
        if missing <= 0:
            raise GameError("relief_balance", floor=self.config["relief_floor"])
        amount = from_minor(missing)
        self.ledger(amount, "daily_relief", "relief", local_date(self.now))
        self.db.execute(accounts.update().where(accounts.c.player_id == self.player).values(relief_claimed_on=local_date(self.now)))
        self.event("relief")
        return {"kind": "relief", "amount": amount, "balance": self.balance()}

    def create_ranch(self):
        row = self.db.execute(select(ranches).where(ranches.c.player_id == self.player)).mappings().first()
        if not row:
            self.db.execute(ranches.insert().values(player_id=self.player, level=1, used_capacity=0,
                config_version=self.version, version=1, created_at=self.now))
        return self.db.execute(select(ranches).where(ranches.c.player_id == self.player)).mappings().one()

    def trade(self, kind, item, count, gross, ref, market_window_id=None, unit_price=None):
        gross = from_minor(to_minor(gross))
        fee = market_fee(gross) if market_window_id is not None else Decimal(tax(int(gross), self.config))
        net = gross - fee if kind == "sell" else -(gross + fee)
        self.ledger(net, kind, "market", ref)
        self.ledger(fee, "transaction_tax", "market_tax", ref, system=True)
        self.db.execute(transactions.insert().values(id=ref, player_id=self.player, kind=kind,
            gross_amount=to_minor(gross), tax_amount=to_minor(fee), net_amount=to_minor(net), item_code=item, quantity=count,
            source_room_id=self.room, config_version=self.version, created_at=self.now,
            market_window_id=market_window_id, unit_price_cents=to_minor(unit_price) if unit_price is not None else None))
        market_trade = market_window_id is not None
        return {"item": item, "quantity": count, "gross": gross, "tax": fee, "net": net, "price": unit_price,
                "fee_label": "手续费" if market_trade else "税额",
                "pool_label": "奖池手续费累计" if market_trade else "奖池累计税额"}

    def buy(self, item, count):
        self.account(True)
        if item in self.config["products"]:
            return self.market_buy(item, count)
        ranch = self.create_ranch()
        snapshots = []
        if item == "feed":
            receipt = self.trade("buy", item, count, count * self.config["feed_price"], self.reference)
            self.inventory_change(item, count, "buy", self.reference)
        elif item == "premium_feed":
            receipt = self.trade("buy", item, count, count * 10, self.reference)
            self.inventory_change(item, count, "buy", self.reference)
        else:
            if self.settle():
                raise GameError("settlement_pending")
            rule = self.config["animals"][item]
            ranch_rules = self.get_config(RUIHE_RANCH_VERSION)
            excess = ranch["used_capacity"] + count * rule["space"] - capacity(ranch["level"], self.config)
            if excess > 0:
                raise GameError("capacity", missing=excess)
            receipt = self.trade("buy", item, count, count * rule["price"], self.reference)
            for _ in range(count):
                animal_id, nonce = uid(), uuid.uuid4().hex
                seed = seed_for(self.secret, animal_id, RUIHE_RANCH_VERSION, nonce)
                rarity = purchase_rarity(rarity_draw(seed, "purchase"))
                base, delay = interval(seed, 0, item, ranch["level"], ranch_rules)
                self.db.execute(animals.insert().values(id=animal_id, display_id=animal_id.replace("-", "")[:12],
                    player_id=self.player, animal_type=item, space=rule["space"], feed_cost=rule["feed"],
                    rarity=rarity,
                    base_interval_hours=base, interval_level=ranch["level"], production_until=self.now + ranch_rules["production_hours"] * 3600,
                    next_production_at=self.now + delay, last_settled_at=self.now, status="active", rule_version=RUIHE_RANCH_VERSION,
                    last_feed_reward_at=self.now,
                    nonce=nonce, seed=seed, seed_commitment=hashlib.sha256(seed.encode()).hexdigest(), sequence=0))
                snapshots.append(dict(self.db.execute(select(animals).where(animals.c.id == animal_id)).mappings().one()))
            self._synchronize_ranch_group(self._ranch_group(item))
            # Persist the post-grouping state in the immutable purchase event.
            # The first grouped cycle may start at a different sequence/deadline
            # than the pre-group individual row inserted above.
            purchased_ids = {snapshot["id"] for snapshot in snapshots}
            snapshots = [dict(animal) for animal in self._ranch_group(item)
                         if animal["id"] in purchased_ids]
            self.db.execute(ranches.update().where(ranches.c.player_id == self.player).values(
                used_capacity=ranch["used_capacity"] + count * rule["space"], version=ranches.c.version + 1))
        self.event("buy", {**receipt, "animals": snapshots if item != "feed" else []})
        return {"kind": "buy", "lines": [receipt], "balance": self.balance(), "pool": self.pool(),
                "rarities": {code: sum(a["rarity"] == code for a in snapshots)
                             for code in RARITY_RULESETS[RARITY_VERSION]}}

    def recycle_animal(self, item, count):
        self.account(True)
        if item not in self.config["animals"]:
            raise GameError("animal")
        if type(count) is not int or not 1 <= count <= 100000:
            raise GameError("quantity")
        group = self._ranch_group(item)
        if len(group) < count:
            raise GameError("stock", item=item, required=count, available=len(group), missing=count-len(group))
        if self.settle():
            raise GameError("settlement_pending")
        # Keep settled products and retired rows for historical batch references.
        group = self._ranch_group(item)
        selected = sorted(group, key=lambda a: (a["rarity"] != "normal", a["id"]))[:count]
        ranch = self.create_ranch()
        gross = Decimal(str(self.config["animals"][item]["price"])) * Decimal("0.50") * count
        self.ledger(gross, "animal_recycle", "animal_recycle", self.reference)
        self.db.execute(animals.update().where(animals.c.id.in_([a["id"] for a in selected]))
                        .values(status="recycled"))
        self.db.execute(ranches.update().where(ranches.c.player_id == self.player).values(
            used_capacity=ranch["used_capacity"]-sum(a["space"] for a in selected),
            version=ranches.c.version+1))
        self.event("animal_recycle", {"animals": [dict(a) for a in selected], "amount": gross})
        return {"kind": "animal_recycle", "item": item, "quantity": count,
                "shiny": sum(a["rarity"] == "shiny" for a in selected),
                "amount": gross, "balance": self.balance()}

    def package_shiny_animals(self, code, count):
        """Move ranch animals into transferable inventory without moving earned batches."""
        self.account(True)
        item = code.removeprefix("shiny_")
        if code not in SHINY_ANIMAL_ITEMS.values():
            raise GameError("animal")
        if self.settle():
            raise GameError("settlement_pending")
        selected = [a for a in self._ranch_group(item) if a["rarity"] == "shiny"][:count]
        if len(selected) != count:
            raise GameError("stock", item=code, required=count, available=len(selected), missing=count-len(selected))
        ranch = self.create_ranch()
        self.db.execute(animals.update().where(animals.c.id.in_([a["id"] for a in selected]))
                        .values(status="packaged"))
        self.db.execute(ranches.update().where(ranches.c.player_id == self.player).values(
            used_capacity=ranch["used_capacity"]-sum(a["space"] for a in selected),
            version=ranches.c.version+1))
        self.inventory_change(code, count, "animal_package", self.reference)
        self.event("animal_package", {"animals": [dict(a) for a in selected], "item": code},
                   reference=self.reference + ":package")

    def release_shiny_animals(self, code, count):
        self.account(True)
        if code not in SHINY_ANIMAL_ITEMS.values():
            raise GameError("animal")
        if type(count) is not int or not 1 <= count <= 100000:
            raise GameError("quantity")
        if self.stock(code) < count:
            raise GameError("stock", item=code, required=count, available=self.stock(code), missing=count-self.stock(code))
        if self.settle():
            raise GameError("settlement_pending")
        item = code.removeprefix("shiny_")
        rule = self.config["animals"][item]
        ranch = self.create_ranch()
        excess = ranch["used_capacity"] + count * rule["space"] - capacity(ranch["level"], self.config)
        if excess > 0:
            raise GameError("capacity", missing=excess)
        self.inventory_change(code, -count, "animal_release", self.reference)
        rules = self.get_config(RUIHE_RANCH_VERSION)
        for _ in range(count):
            animal_id, nonce = uid(), uuid.uuid4().hex
            seed = seed_for(self.secret, animal_id, RUIHE_RANCH_VERSION, nonce)
            base, delay = interval(seed, 0, item, ranch["level"], rules)
            self.db.execute(animals.insert().values(id=animal_id, display_id=animal_id.replace("-", "")[:12],
                player_id=self.player, animal_type=item, space=rule["space"], feed_cost=rule["feed"], rarity="shiny",
                base_interval_hours=base, interval_level=ranch["level"], production_until=self.now,
                next_production_at=self.now+delay, last_settled_at=self.now, status="active",
                rule_version=RUIHE_RANCH_VERSION, last_feed_reward_at=self.now, nonce=nonce, seed=seed,
                seed_commitment=hashlib.sha256(seed.encode()).hexdigest(), sequence=0))
        self._synchronize_ranch_group(self._ranch_group(item))
        self.db.execute(ranches.update().where(ranches.c.player_id == self.player).values(
            used_capacity=ranch["used_capacity"]+count*rule["space"], version=ranches.c.version+1))
        self.event("animal_release", {"item": code, "quantity": count})
        return {"kind": "animal_release", "item": code, "quantity": count}

    def pool(self):
        return from_minor(self.db.execute(select(world.c.lottery_pool_balance).where(world.c.id == 1)).scalar_one())

    def market_buy(self, item, count):
        if item not in self.config["products"]:
            raise GameError("market_item", item=item)
        if type(count) is not int or not 1 <= count <= 100000:
            raise GameError("quantity")
        account = self.account(True)
        window_id, _ = market_window(self.now)
        spent_minor = self.db.execute(select(func.coalesce(func.sum(-transactions.c.net_amount), 0)).where(
            transactions.c.player_id == self.player, transactions.c.kind == "buy",
            transactions.c.market_window_id == window_id)).scalar_one()
        price = market_price(item, self.now, self.config)
        gross = price * count
        fee = market_fee(gross)
        total_minor = to_minor(gross + fee)
        if spent_minor + total_minor > 20000:
            raise GameError("market_limit", remaining=from_minor(max(0, 20000 - spent_minor)))
        if self.available_minor() < total_minor:
            raise GameError("balance", missing=from_minor(total_minor - self.available_minor()),
                            required=from_minor(total_minor), available=from_minor(self.available_minor()),
                            balance=from_minor(account['balance']))
        receipt = self.trade("buy", item, count, gross, self.reference, window_id, price)
        self.inventory_change(item, count, "market_buy", self.reference)
        self.event("market_buy", receipt)
        return {"kind": "buy", "lines": [receipt], "balance": self.balance(), "pool": self.pool()}

    def market(self):
        self.account()
        window_id, seconds = market_window(self.now)
        rows = []
        for code, rule in self.config["products"].items():
            price = market_price(code, self.now, self.config)
            previous = market_price(code, (window_id - 1) * 1800, self.config)
            arrow, label = market_trend(price, previous, rule["price"])
            rows.append({"item": code, "price": price, "base_price": rule["price"],
                         "change_percent": market_change_percent(price, rule["price"]),
                         "trend": arrow, "label": label})
        return {"kind": "market", "rows": rows, "refresh_seconds": seconds}

    def _ranch_group(self, animal_type):
        return self.db.execute(select(animals).where(animals.c.player_id == self.player,
            animals.c.animal_type == animal_type, animals.c.status == "active")
            .order_by(animals.c.grouped_production.desc(), animals.c.id).with_for_update()).mappings().all()

    def _synchronize_ranch_group(self, group):
        """Retain earned product rows and merge remaining care/progress once."""
        if not group:
            return
        leader = group[0]
        established = bool(leader["grouped_production"])
        progress = leader["cycle_progress"] if established else sum(a["cycle_progress"] for a in group) / len(group)
        sequence = leader["sequence"] if established else max(a["sequence"] for a in group) + 1
        affection = leader["affection"] if established else max(a["affection"] for a in group)
        until = sum(a["production_until"] for a in group) / len(group)
        base, delay = interval(leader["seed"], sequence, leader["animal_type"], leader["interval_level"],
                               self.get_config(RUIHE_RANCH_VERSION))
        delay /= affection_speed(affection)
        values = {"grouped_production": True, "rule_version": RUIHE_RANCH_VERSION,
            "seed": leader["seed"], "nonce": leader["nonce"], "seed_commitment": leader["seed_commitment"],
            "sequence": sequence, "cycle_progress": progress, "affection": affection,
            "feed_streak": leader["feed_streak"] if established else min(a["feed_streak"] for a in group),
            "premium_feed_active": leader["premium_feed_active"] if established else all(a["premium_feed_active"] for a in group),
            "last_feed_reward_at": leader["last_feed_reward_at"], "interval_level": leader["interval_level"],
            "base_interval_hours": base, "production_until": until,
            "last_settled_at": self.now, "next_production_at": self.now + delay * (1 - progress)}
        self.db.execute(animals.update().where(animals.c.id.in_([a["id"] for a in group])).values(**values))

    def settle(self):
        count = 0
        due = self.db.execute(select(animals).where(animals.c.player_id == self.player,
            animals.c.status == "active").order_by(animals.c.next_production_at, animals.c.id).with_for_update()).mappings().all()
        for animal_type in sorted({animal["animal_type"] for animal in due}):
            group = self._ranch_group(animal_type)
            if not all(a["grouped_production"] for a in group):
                for member in group:
                    if count >= 1000:
                        return True
                    if member["rule_version"] == RUIHE_RANCH_VERSION:
                        count += self._settle_ruihe_animal(member, 1000 - count)
                    else:
                        count += self._settle_legacy_animal(member, 1000 - count)
                if count >= 1000:
                    return True
                self._synchronize_ranch_group(self._ranch_group(animal_type))
                group = self._ranch_group(animal_type)
            count += self._settle_ruihe_animal(group[0], 1000 - count, group)
        return any(self.db.execute(select(animals.c.id).where(animals.c.player_id == self.player,
            animals.c.status == "active", animals.c.next_production_at <= self.now,
            animals.c.next_production_at <= animals.c.production_until)).all())

    def _settle_legacy_animal(self, animal, cap):
        end = min(self.now, animal["production_until"])
        next_at, seq, base = animal["next_production_at"], animal["sequence"], animal["base_interval_hours"]
        config = self.get_config(animal["rule_version"])
        count = 0
        product = config["animals"][animal["animal_type"]]["product"]
        while next_at <= end and count < cap:
            from .weather_service import WeatherService
            from dzmm_bot.domain.weather_rules import game_date
            production_base, weather_adjustment, weather_code = WeatherService(
                self.db,next_at,enabled=self.weather_enabled).adjust(day=game_date(next_at),base=1,
                    key=animal["animal_type"],product=product)
            self.db.execute(products.insert().values(id=uid(), player_id=self.player,
                product_type=product, quantity=production_base, production_base=production_base,
                source_animal_id=animal["id"], produced_at=next_at, sequence=seq,
                config_version=animal["rule_version"],weather_code=weather_code,
                weather_adjustment=weather_adjustment if weather_code else None,
                weather_rule_version="ruihe-weather-v1" if weather_code else None))
            seq += 1
            count += 1
            base, delay = interval(animal["seed"], seq, animal["animal_type"], animal["interval_level"], config)
            next_at += delay
        from dzmm_bot.domain.economy import ranch_duration_multiplier
        progress = max(0, min(1, 1 - (next_at - end) / (base * 3600 * ranch_duration_multiplier(animal['interval_level']))))
        self.db.execute(animals.update().where(animals.c.id == animal["id"]).values(next_production_at=next_at,
            sequence=seq, base_interval_hours=base, last_settled_at=end, cycle_progress=progress))
        return count

    def _settle_ruihe_animal(self, animal, cap, group=None):
        rules = self.get_config(RUIHE_RANCH_VERSION)
        start = animal["last_settled_at"]
        end = min(self.now, animal["production_until"])
        progress = animal["cycle_progress"]
        sequence = animal["sequence"]
        base = animal["base_interval_hours"]
        _, delay = interval(animal["seed"], sequence, animal["animal_type"], animal["interval_level"], rules)
        if group:
            delay /= affection_speed(animal["affection"])
        cursor = start
        produced = 0
        product = rules["animals"][animal["animal_type"]]["product"]
        while cursor < end and produced < cap:
            needed = delay * (1 - progress)
            if cursor + needed > end:
                progress += (end - cursor) / delay
                cursor = end
                break
            cursor += needed
            multiplier = production_multiplier(animal["affection"], animal["feed_streak"],
                                               animal["premium_feed_active"], "breeze", product)
            if not group:
                multiplier *= 1 + 0.02 * max(0, min(9, animal["affection"]))
                multiplier *= 1.15 if animal["premium_feed_active"] else 1
            multiplier *= float(self.buffs.get_multiplier("ranch_output", at=cursor))
            multiplier *= float(self.buffs.weather_multiplier("breeze", product, at=cursor))
            multiplier = Decimal(str(multiplier)).quantize(Decimal("0.00000001"))
            animal_count = len(group) if group else 1
            base_quantity = animal_production_base(animal["animal_type"])
            from .weather_service import WeatherService
            from dzmm_bot.domain.weather_rules import game_date
            base_quantity, weather_adjustment, weather_code = WeatherService(
                self.db,cursor,enabled=self.weather_enabled).adjust(day=game_date(cursor),base=base_quantity,
                    key=animal["animal_type"],product=product)
            rarity_counts = {code: sum(a["rarity"] == code for a in group or [])
                             for code in RARITY_RULESETS[RARITY_VERSION]}
            product_id = uid()
            rarity_version = RARITY_VERSION if group else "ranch-rarity-v1"
            premium_probability = PREMIUM_PROBABILITY if group and animal["premium_feed_active"] else Decimal(0)
            draws = {}
            if group and (any(rarity_counts.values()) or premium_probability):
                batch_seed = seed_for(self.secret, self.player, rarity_version, product_id)
                draws = {code: rarity_draw(batch_seed, code) for code in RARITY_RULESETS[rarity_version]}
                draws["increase"] = rarity_draw(batch_seed, "increase")
            bonus = harvest_base_bonus(rarity_counts, draws, rarity_version, premium_probability) if draws else 0
            quantity = production_quantity((base_quantity + bonus) * animal_count, multiplier)
            self.db.execute(products.insert().values(id=product_id, player_id=self.player,
                product_type=product, quantity=quantity, source_animal_id=animal["id"],
                production_base=base_quantity, animal_count=animal_count,
                output_multiplier=Decimal(str(multiplier)), rarity_counts_json=json.dumps(rarity_counts),
                rarity_rule_version=rarity_version, harvest_base_bonus=bonus,
                premium_probability=premium_probability,
                weather_code=weather_code,
                weather_adjustment=weather_adjustment if weather_code else None,
                weather_rule_version="ruihe-weather-v1" if weather_code else None,
                produced_at=cursor, sequence=sequence, config_version=RUIHE_RANCH_VERSION))
            sequence += 1
            produced += 1
            progress = 0
            base, delay = interval(animal["seed"], sequence, animal["animal_type"], animal["interval_level"], rules)
            if group:
                delay /= affection_speed(animal["affection"])
        # A long outage checkpoints at feed expiry; feeding later resumes this exact partial cycle.
        checkpoint = max(start, cursor if produced >= cap and cursor < end else end)
        overdue_reset = self.now > animal["production_until"] + 10 * 3600
        values = {"sequence": sequence, "base_interval_hours": base, "cycle_progress": progress,
            "last_settled_at": checkpoint, "next_production_at": checkpoint + delay * (1 - progress)}
        if overdue_reset:
            values.update(affection=0, feed_streak=0)
        self.db.execute(animals.update().where(animals.c.id.in_([a["id"] for a in group] if group else [animal["id"]])).values(**values))
        return produced

    def collect(self):
        totals = {}
        self.harvest_rarity_bonuses = {}
        rows = self.db.execute(select(products).where(products.c.player_id == self.player,
            products.c.collected_at.is_(None)).order_by(products.c.produced_at, products.c.id)
            .with_for_update()).mappings().all()
        for product in rows:
            quantity = product["quantity"]
            bonus = max(0, product["harvest_base_bonus"])
            if bonus:
                self.harvest_rarity_bonuses[product["product_type"]] = (
                    self.harvest_rarity_bonuses.get(product["product_type"], 0) + bonus)
            self.inventory_change(product["product_type"], quantity, "harvest", product["id"], product["config_version"])
            self.db.execute(products.update().where(products.c.id == product["id"],
                products.c.collected_at.is_(None)).values(collected_at=self.now))
            totals[product["product_type"]] = totals.get(product["product_type"], 0) + quantity
        return totals

    def harvest(self):
        self.account(True)
        more = self.settle()
        totals = self.collect()
        self.event("harvest", totals)
        return {"kind": "harvest", "totals": totals, "balance": self.balance(), "more": more,
                "rarity_bonuses": self.harvest_rarity_bonuses}

    def feed(self, display_id=None, premium=False):
        self.account(True)
        query = select(animals).where(animals.c.player_id == self.player, animals.c.status == "active")
        if display_id:
            animal_type = self.db.execute(select(animals.c.animal_type).where(
                animals.c.player_id == self.player, animals.c.status == "active",
                animals.c.display_id == display_id)).scalar()
            if animal_type is None:
                raise GameError("animal")
            query = query.where(animals.c.animal_type == animal_type)
        targets = self.db.execute(query.order_by(animals.c.id).with_for_update()).mappings().all()
        if not targets:
            raise GameError("animal")
        more = self.settle()
        if more:
            raise GameError("settlement_pending")
        targets = self.db.execute(query).mappings().all()
        feed_count = sum(a["feed_cost"] for a in targets)
        premium_needed = 0
        if premium:
            premium_needed = sum(a["space"] for a in targets
                                 if not (a["premium_feed_active"] and a["production_until"] > self.now))
        auto_purchased = {}
        for item, required, unit_price in (("feed", feed_count, self.config["feed_price"]),
                                           ("premium_feed", premium_needed, 10)):
            missing = max(0, required - self.stock(item))
            if missing:
                purchase_ref = f"{self.reference}:auto_feed:{item}"
                self.trade("buy", item, missing, missing * unit_price, purchase_ref)
                self.inventory_change(item, missing, "feed_auto_buy", purchase_ref)
                auto_purchased[item] = missing
        self.inventory_change("feed", -feed_count, "feed", self.reference)
        premium_count = 0
        ranch = self.create_ranch()
        cfg = self.get_config(RUIHE_RANCH_VERSION)
        for animal in targets:
            timely = self.now <= animal["production_until"]
            premium_was_active = animal["premium_feed_active"] and animal["production_until"] > self.now
            premium_applied = premium and not premium_was_active
            if premium_applied:
                self.inventory_change("premium_feed", -animal["space"], "premium_feed", f"{self.reference}:{animal['id']}")
                premium_count += animal["space"]
            affection = animal["affection"]
            streak = animal["feed_streak"]
            if self.now > animal["production_until"] + 10 * 3600:
                affection, streak = 0, 0
            elif timely and self.now - animal["last_feed_reward_at"] >= 12 * 3600:
                streak += 1
            else:
                if not timely:
                    streak = 0
            affection = min(10, affection + 1)
            if premium_applied:
                affection = min(10, affection + 1)
            current = self.db.execute(select(animals).where(animals.c.id == animal["id"])).mappings().one()
            # A feed refill resumes the already chosen random cycle; it cannot reroll its deadline.
            if current["rule_version"] == RUIHE_RANCH_VERSION:
                nonce, seed = current["nonce"], current["seed"]
                level = current["interval_level"]
                base = current["base_interval_hours"]
            else:
                nonce = uuid.uuid4().hex
                seed = seed_for(self.secret, self.reference + animal["id"], RUIHE_RANCH_VERSION, nonce)
                level = ranch["level"]
                base, _ = interval(seed, current["sequence"], animal["animal_type"], level, cfg)
            _, delay = interval(seed, current["sequence"], animal["animal_type"], level, cfg)
            delay /= affection_speed(affection)
            until = self.now + cfg["production_hours"] * 3600
            next_at = self.now + delay * (1 - current["cycle_progress"])
            self.db.execute(animals.update().where(animals.c.id == animal["id"]).values(
                production_until=until, next_production_at=next_at, base_interval_hours=base,
                interval_level=level,
                rule_version=RUIHE_RANCH_VERSION, nonce=nonce, seed=seed,
                seed_commitment=hashlib.sha256(seed.encode()).hexdigest(), last_settled_at=self.now,
                affection=affection, feed_streak=streak, premium_feed_active=bool(premium or premium_was_active),
                cycle_progress=current["cycle_progress"],
                last_feed_reward_at=self.now if timely and self.now - animal["last_feed_reward_at"] >= 12 * 3600 else animal["last_feed_reward_at"]))
            self.event("feed", {"animal": animal["id"], "previous": dict(current), "nonce": nonce,
                "seed": seed, "until": until, "level": level,
                "rule_version": RUIHE_RANCH_VERSION}, reference=f"{self.reference}:{animal['id']}")
        auto_collected = self.collect()
        auto_harvest_bonuses = self.harvest_rarity_bonuses
        return {"kind": "feed", "quantity": feed_count, "premium_quantity": premium_count,
            "remaining": self.stock("feed"), "premium_remaining": self.stock("premium_feed"),
            "balance": self.balance(), "animals": len(targets), "until": self.now + cfg["production_hours"] * 3600,
            "auto_purchased": auto_purchased, "auto_collected": auto_collected,
            "auto_harvest_bonuses": auto_harvest_bonuses}

    def sell(self, item, count):
        self.account(True)
        if item not in self.config["products"]:
            raise GameError("market_item", item=item)
        if type(count) is not int or not 1 <= count <= 100000:
            raise GameError("quantity")
        more = self.settle()
        self.collect()
        price = market_price(item, self.now, self.config)
        ref = f"{self.reference}:{item}"
        self.inventory_change(item, -count, "sell", ref)
        window_id, _ = market_window(self.now)
        lines = [self.trade("sell", item, count, price * count, ref, window_id, price)]
        self.event("sell", {"lines": lines})
        return {"kind": "sell", "lines": lines, "balance": self.balance(), "pool": self.pool(), "more": more,
                "rarity_bonuses": self.harvest_rarity_bonuses}

    def upgrade(self):
        self.account(True)
        ranch = self.create_ranch()
        if ranch["level"] >= self.config["max_level"]:
            raise GameError("max_level")
        more = self.settle()
        if more:
            raise GameError("settlement_pending")
        receipt = self.trade("upgrade", "ranch", 1, upgrade_price(ranch["level"], self.config), self.reference)
        new_level = ranch["level"] + 1
        active = self.db.execute(select(animals).where(animals.c.player_id == self.player,
            animals.c.status == "active")).mappings().all()
        for animal in active:
            rules = self.get_config(animal["rule_version"])
            base, delay = interval(animal["seed"], animal["sequence"], animal["animal_type"], new_level, rules)
            if animal["grouped_production"]:
                delay /= affection_speed(animal["affection"])
            values = {"interval_level": new_level}
            if animal["rule_version"] == RUIHE_RANCH_VERSION:
                values.update(base_interval_hours=base,
                    next_production_at=animal["last_settled_at"] + delay * (1 - animal["cycle_progress"]))
            self.db.execute(animals.update().where(animals.c.id == animal["id"]).values(**values))
        self.db.execute(ranches.update().where(ranches.c.player_id == self.player).values(level=ranch["level"] + 1,
            version=ranches.c.version + 1, config_version=self.version))
        self.event("upgrade", receipt)
        return {"kind": "upgrade", "level": new_level, "capacity": capacity(new_level, self.config),
                "lines": [receipt], "balance": self.balance(), "pool": self.pool()}

    def view(self, page=1):
        account = self.account()
        more = self.settle() if account["status"] == "active" else False
        ranch = self.create_ranch()
        rows = self.db.execute(select(animals).where(animals.c.player_id == self.player, animals.c.status == "active")
            .order_by(animals.c.production_until, animals.c.id)).mappings().all()
        pending = self.db.execute(select(func.coalesce(func.sum(products.c.quantity), 0)).where(
            products.c.player_id == self.player, products.c.collected_at.is_(None))).scalar_one()
        pending_rows = self.db.execute(select(products.c.source_animal_id,
            func.sum(products.c.quantity)).where(products.c.player_id == self.player,
            products.c.collected_at.is_(None)).group_by(products.c.source_animal_id)).all()
        pending_by_animal = {animal_id: total for animal_id, total in pending_rows}
        current_window, _ = market_window(self.now)
        price_info = {}
        for code, rule in self.config["products"].items():
            price = market_price(code, self.now, self.config)
            previous = market_price(code, (current_window - 1) * 1800, self.config)
            arrow, label = market_trend(price, previous, rule["price"])
            price_info[code] = {"price": price, "trend": arrow, "label": label,
                                "value": price * self.stock(code)}
        from dzmm_bot.domain.farm_rules import CROP_SELL_PRICES
        for code, price in CROP_SELL_PRICES.items():
            if code not in price_info:
                price_info[code] = {"price": price, "value": price * self.stock(code)}
        animal_rows = []
        probabilities = {}
        for row in rows:
            animal = dict(row)
            animal["running"] = animal["status"] == "active" and animal["production_until"] > self.now
            animal["progress"] = max(0.0, min(1.0, animal["cycle_progress"])) if animal["rule_version"] == RUIHE_RANCH_VERSION else max(0.0, min(1.0, (self.now - animal["last_settled_at"]) / max(1, animal["next_production_at"] - animal["last_settled_at"])))
            animal["next_in"] = max(0, animal["next_production_at"] - self.now)
            animal["feed_in"] = max(0, animal["production_until"] - self.now)
            animal_rows.append(animal)
        for animal_type in self.config["animals"]:
            group = [a for a in animal_rows if a["animal_type"] == animal_type]
            if group:
                counts = {code: sum(a["rarity"] == code for a in group) for code in RARITY_RULESETS[RARITY_VERSION]}
                probabilities[animal_type] = increase_probability(counts, any(
                    a["premium_feed_active"] and a["running"] for a in group)) * 100
        from .weather_service import WeatherService
        weather = WeatherService(self.db,self.now,enabled=self.weather_enabled).player_summary()
        return {"kind": "ranch", **ranch, "capacity": capacity(ranch["level"], self.config),
            'level_duration_multiplier': ranch_duration_multiplier(ranch['level']),
            "upgrade_cost": (upgrade_price(ranch["level"], self.config)
                             + tax(upgrade_price(ranch["level"], self.config), self.config))
                            if ranch["level"] < self.config["max_level"] else None,
            "name": self.db.execute(select(players.c.name).where(players.c.id == self.player)).scalar_one(),
            "animals": animal_rows, "stocks": dict(self.db.execute(select(stocks.c.item_code, stocks.c.quantity)
                .where(stocks.c.player_id == self.player)).all()),
            "prices": {code: info["price"] for code, info in price_info.items()},
            "price_info": price_info,
            "increase_probabilities": probabilities,
            "pending": pending, "more": more, "page": 1, "pages": 1, "now": self.now,
            "pending_by_animal": pending_by_animal, "weather": weather}

    def ranking(self, page):
        ranked = select(players.c.id, players.c.name, accounts.c.balance,
            func.row_number().over(order_by=(accounts.c.balance.desc(), accounts.c.player_id)).label('rank'))\
            .join(accounts, accounts.c.player_id == players.c.id).subquery()
        own = self.db.execute(select(ranked.c.rank).where(ranked.c.id == self.player)).scalar_one_or_none()
        condition = ranked.c.rank <= 10
        if own is not None:
            condition = condition | ranked.c.rank.between(max(1, own - 5), own + 5)
        rows = self.db.execute(select(ranked).where(condition).order_by(ranked.c.rank)).mappings().all()
        return {'kind': 'ranking', 'rows': [{**dict(r), 'balance': from_minor(r['balance']),
            'own': r['id'] == self.player} for r in rows], 'own_rank': own}

    def admin_action(self, action, actor, amount=None, reason=None, ticket=None):
        self.account()
        if action == "compensate":
            prior = self.db.execute(select(audit.c.payload_json).where(audit.c.action == action,
                audit.c.target_id == self.player)).scalars().all()
            for payload in map(json.loads, prior):
                if payload["ticket"] == ticket and (payload["amount"] != amount or payload["reason"] != reason):
                    raise GameError("reference_conflict")
            changed = self.ledger(amount, "admin_compensation", "compensation", ticket)
            if not changed:
                return {"ok": True, "duplicate": True, "balance": self.balance()}
        else:
            self.db.execute(accounts.update().where(accounts.c.player_id == self.player).values(
                status="frozen" if action == "freeze" else "active", version=accounts.c.version + 1))
        self.db.execute(audit.insert().values(id=uid(), actor=actor, action=action, target_type="player", target_id=self.player,
            payload_json=json.dumps({"amount": amount, "reason": reason, "ticket": ticket}), created_at=self.now))
        return {"ok": True, "balance": self.balance()}


def metrics(db):
    account_total = db.execute(select(func.coalesce(func.sum(accounts.c.balance), 0))).scalar_one()
    ledger_total = db.execute(select(func.coalesce(func.sum(currency.c.amount), 0)).where(currency.c.player_id.is_not(None))).scalar_one()
    pool = db.execute(select(world.c.lottery_pool_balance)).scalar_one()
    taxes = db.execute(select(func.coalesce(func.sum(transactions.c.tax_amount), 0))).scalar_one()
    system = db.execute(select(func.coalesce(func.sum(currency.c.amount), 0)).where(currency.c.player_id.is_(None))).scalar_one()
    mismatches = []
    for account in db.execute(select(accounts)).mappings():
        total = db.execute(select(func.coalesce(func.sum(currency.c.amount), 0)).where(currency.c.player_id == account["player_id"])).scalar_one()
        if total != account["balance"]:
            mismatches.append(account["player_id"])
    return {"accounts": db.execute(select(func.count()).select_from(accounts)).scalar_one(),
        "ledger_entries": db.execute(select(func.count()).select_from(currency)).scalar_one(),
        "config_version": db.execute(select(world.c.config_version)).scalar_one(),
        "balance_total": from_minor(account_total), "balance_difference": from_minor(account_total - ledger_total),
        "account_mismatches": mismatches, "pool": from_minor(pool), "tax_total": from_minor(taxes),
        "pool_tax_difference": from_minor(pool - taxes), "pool_ledger_difference": from_minor(pool - system)}

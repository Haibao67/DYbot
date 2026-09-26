import hashlib
import json
import uuid
from sqlalchemy import select, func
from dzmm_bot.domain.economy import (GameError, tax, capacity, upgrade_price, local_date,
                                    market_price, seed_for, interval, RUIHE_RANCH_VERSION,
                                    production_multiplier, production_quantity)
from dzmm_bot.persistence.schema import (accounts, currency, stocks, inventory, world,
    configs, audit, events, ranches, animals, products, transactions)
from dzmm_bot.persistence.transport import players, members


def uid():
    return str(uuid.uuid4())


class GameService:
    def __init__(self, db, now, secret, player=None, room=None, reference=None):
        self.db, self.now, self.secret = db, now, secret
        self.player, self.room, self.reference = player, room, reference or uid()
        self.version = db.execute(select(world.c.config_version).where(world.c.id == 1)).scalar_one()
        self.config = self.get_config(self.version)
        self.command_context = {}

    def get_config(self, version):
        return json.loads(self.db.execute(select(configs.c.payload_json).where(
            configs.c.version == version, configs.c.status == "published")).scalar_one())

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
            payload_json=json.dumps({**(payload or {}), "command": self.command_context}), created_at=self.now))

    def balance(self):
        return self.account()["balance"]

    def ledger(self, amount, reason, ref_type, ref_id, system=False):
        if type(amount) is not int or abs(amount) > 10**15:
            raise GameError("amount")
        player = None if system else self.player
        prior = self.db.execute(select(currency).where(currency.c.player_id == player,
            currency.c.reference_type == ref_type, currency.c.reference_id == ref_id)).mappings().first()
        if prior:
            if prior["amount"] != amount or prior["reason"] != reason:
                raise GameError("reference_conflict")
            return False
        if system:
            balance = self.db.execute(select(world.c.lottery_pool_balance).where(world.c.id == 1).with_for_update()).scalar_one()
        else:
            balance = self.balance()
        if balance + amount < 0:
            raise GameError("balance", missing=-(balance + amount))
        self.db.execute(currency.insert().values(id=uid(), player_id=player, amount=amount, reason=reason,
            reference_type=ref_type, reference_id=ref_id, source_room_id=self.room, created_at=self.now))
        if system:
            self.db.execute(world.update().where(world.c.id == 1).values(lottery_pool_balance=balance + amount, updated_at=self.now))
        else:
            self.db.execute(accounts.update().where(accounts.c.player_id == player).values(balance=balance + amount,
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
            raise GameError("stock", item=item, missing=-(old + delta))
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
        return {"kind": "profile", **account, "name": self.db.execute(select(players.c.name).where(players.c.id == self.player)).scalar_one()}

    def relief(self):
        account = self.account(True)
        if account["relief_claimed_on"] == local_date(self.now):
            raise GameError("relief_used")
        amount = self.config["relief_floor"] - account["balance"]
        if amount <= 0:
            raise GameError("relief_balance", floor=self.config["relief_floor"])
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

    def trade(self, kind, item, count, gross, ref):
        fee = tax(gross, self.config)
        net = gross - fee if kind == "sell" else -(gross + fee)
        self.ledger(net, kind, "market", ref)
        self.ledger(fee, "transaction_tax", "market_tax", ref, system=True)
        self.db.execute(transactions.insert().values(id=ref, player_id=self.player, kind=kind,
            gross_amount=gross, tax_amount=fee, net_amount=net, item_code=item, quantity=count,
            source_room_id=self.room, config_version=self.version, created_at=self.now))
        return {"item": item, "quantity": count, "gross": gross, "tax": fee, "net": net}

    def buy(self, item, count):
        self.account(True)
        ranch = self.create_ranch()
        snapshots = []
        if item == "feed":
            receipt = self.trade("buy", item, count, count * self.config["feed_price"], self.reference)
            self.inventory_change(item, count, "buy", self.reference)
        elif item == "premium_feed":
            receipt = self.trade("buy", item, count, count * 10, self.reference)
            self.inventory_change(item, count, "buy", self.reference)
        else:
            rule = self.config["animals"][item]
            ranch_rules = self.get_config(RUIHE_RANCH_VERSION)
            excess = ranch["used_capacity"] + count * rule["space"] - capacity(ranch["level"], self.config)
            if excess > 0:
                raise GameError("capacity", missing=excess)
            receipt = self.trade("buy", item, count, count * rule["price"], self.reference)
            for _ in range(count):
                animal_id, nonce = uid(), uuid.uuid4().hex
                seed = seed_for(self.secret, animal_id, RUIHE_RANCH_VERSION, nonce)
                base, delay = interval(seed, 0, item, ranch["level"], ranch_rules)
                self.db.execute(animals.insert().values(id=animal_id, display_id=animal_id.replace("-", "")[:12],
                    player_id=self.player, animal_type=item, space=rule["space"], feed_cost=rule["feed"],
                    base_interval_hours=base, interval_level=ranch["level"], production_until=self.now + ranch_rules["production_hours"] * 3600,
                    next_production_at=self.now + delay, last_settled_at=self.now, status="active", rule_version=RUIHE_RANCH_VERSION,
                    last_feed_reward_at=self.now,
                    nonce=nonce, seed=seed, seed_commitment=hashlib.sha256(seed.encode()).hexdigest(), sequence=0))
                snapshots.append(dict(self.db.execute(select(animals).where(animals.c.id == animal_id)).mappings().one()))
            self.db.execute(ranches.update().where(ranches.c.player_id == self.player).values(
                used_capacity=ranch["used_capacity"] + count * rule["space"], version=ranches.c.version + 1))
        self.event("buy", {**receipt, "animals": snapshots if item != "feed" else []})
        return {"kind": "buy", "lines": [receipt], "balance": self.balance(), "pool": self.pool()}

    def pool(self):
        return self.db.execute(select(world.c.lottery_pool_balance).where(world.c.id == 1)).scalar_one()

    def settle(self):
        count = 0
        due = self.db.execute(select(animals).where(animals.c.player_id == self.player,
            animals.c.status == "active").order_by(animals.c.next_production_at, animals.c.id).with_for_update()).mappings().all()
        for animal in due:
            if animal["rule_version"] == RUIHE_RANCH_VERSION:
                count += self._settle_ruihe_animal(animal, 1000 - count)
                continue
            end = min(self.now, animal["production_until"])
            next_at, seq, base = animal["next_production_at"], animal["sequence"], animal["base_interval_hours"]
            config = self.get_config(animal["rule_version"])
            while next_at <= end and count < 1000:
                self.db.execute(products.insert().values(id=uid(), player_id=self.player,
                    product_type=config["animals"][animal["animal_type"]]["product"], quantity=1,
                    source_animal_id=animal["id"], produced_at=next_at, sequence=seq, config_version=animal["rule_version"]))
                seq += 1
                count += 1
                base, delay = interval(animal["seed"], seq, animal["animal_type"], animal["interval_level"], config)
                next_at += delay
            self.db.execute(animals.update().where(animals.c.id == animal["id"]).values(next_production_at=next_at,
                sequence=seq, base_interval_hours=base, last_settled_at=min(end, next_at)))
        return any(self.db.execute(select(animals.c.id).where(animals.c.player_id == self.player,
            animals.c.status == "active", animals.c.next_production_at <= self.now,
            animals.c.next_production_at <= animals.c.production_until)).all())

    def _settle_ruihe_animal(self, animal, cap):
        rules = self.get_config(RUIHE_RANCH_VERSION)
        start = animal["last_settled_at"]
        end = min(self.now, animal["production_until"])
        progress = animal["cycle_progress"]
        sequence = animal["sequence"]
        base = animal["base_interval_hours"]
        _, delay = interval(animal["seed"], sequence, animal["animal_type"], animal["interval_level"], rules)
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
            quantity = production_quantity(1, multiplier)
            self.db.execute(products.insert().values(id=uid(), player_id=self.player,
                product_type=product, quantity=quantity, source_animal_id=animal["id"],
                produced_at=cursor, sequence=sequence, config_version=RUIHE_RANCH_VERSION))
            sequence += 1
            produced += 1
            progress = 0
            base, delay = interval(animal["seed"], sequence, animal["animal_type"], animal["interval_level"], rules)
        # A long outage checkpoints at feed expiry; feeding later resumes this exact partial cycle.
        checkpoint = max(start, cursor if produced >= cap and cursor < end else end)
        overdue_reset = self.now > animal["production_until"] + 10 * 3600
        values = {"sequence": sequence, "base_interval_hours": base, "cycle_progress": progress,
            "last_settled_at": checkpoint, "next_production_at": checkpoint + delay * (1 - progress)}
        if overdue_reset:
            values.update(affection=0, feed_streak=0)
        self.db.execute(animals.update().where(animals.c.id == animal["id"]).values(**values))
        return produced

    def collect(self):
        totals = {}
        rows = self.db.execute(select(products).where(products.c.player_id == self.player,
            products.c.collected_at.is_(None)).with_for_update()).mappings().all()
        for product in rows:
            self.inventory_change(product["product_type"], product["quantity"], "harvest", product["id"], product["config_version"])
            self.db.execute(products.update().where(products.c.id == product["id"]).values(collected_at=self.now))
            totals[product["product_type"]] = totals.get(product["product_type"], 0) + product["quantity"]
        return totals

    def harvest(self):
        self.account(True)
        more = self.settle()
        totals = self.collect()
        self.event("harvest", totals)
        return {"kind": "harvest", "totals": totals, "balance": self.balance(), "more": more}

    def feed(self, display_id=None, premium=False):
        self.account(True)
        query = select(animals).where(animals.c.player_id == self.player, animals.c.status == "active")
        if display_id:
            query = query.where(animals.c.display_id == display_id)
        targets = self.db.execute(query.order_by(animals.c.id).with_for_update()).mappings().all()
        if not targets:
            raise GameError("animal")
        more = self.settle()
        if more:
            raise GameError("settlement_pending")
        targets = self.db.execute(query).mappings().all()
        feed_count = sum(a["feed_cost"] for a in targets)
        if self.stock("feed") < feed_count:
            raise GameError("stock", item="feed", missing=feed_count - self.stock("feed"))
        if premium:
            premium_count = sum(not (a["premium_feed_active"] and a["production_until"] > self.now) for a in targets)
            if self.stock("premium_feed") < premium_count:
                raise GameError("stock", item="premium_feed", missing=premium_count - self.stock("premium_feed"))
        self.inventory_change("feed", -feed_count, "feed", self.reference)
        premium_count = 0
        ranch = self.create_ranch()
        cfg = self.get_config(RUIHE_RANCH_VERSION)
        for animal in targets:
            timely = self.now <= animal["production_until"]
            premium_was_active = animal["premium_feed_active"] and animal["production_until"] > self.now
            premium_applied = premium and not premium_was_active
            if premium_applied:
                self.inventory_change("premium_feed", -1, "premium_feed", f"{self.reference}:{animal['id']}")
                premium_count += 1
            affection = animal["affection"]
            streak = animal["feed_streak"]
            if self.now > animal["production_until"] + 10 * 3600:
                affection, streak = 0, 0
            elif timely and self.now - animal["last_feed_reward_at"] >= 12 * 3600:
                affection = min(9, affection + 1)
                streak += 1
            else:
                if not timely:
                    streak = 0
            if premium_applied:
                affection = min(9, affection + 1)
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
            until = self.now + cfg["production_hours"] * 3600
            next_at = self.now + delay * (1 - current["cycle_progress"])
            self.db.execute(animals.update().where(animals.c.id == animal["id"]).values(
                production_until=until, next_production_at=next_at, base_interval_hours=base,
                interval_level=level,
                rule_version=RUIHE_RANCH_VERSION, nonce=nonce, seed=seed,
                seed_commitment=hashlib.sha256(seed.encode()).hexdigest(), last_settled_at=self.now,
                affection=affection, feed_streak=streak, premium_feed_active=bool(premium),
                cycle_progress=current["cycle_progress"],
                last_feed_reward_at=self.now if timely and self.now - animal["last_feed_reward_at"] >= 12 * 3600 else animal["last_feed_reward_at"]))
            self.event("feed", {"animal": animal["id"], "previous": dict(current), "nonce": nonce,
                "seed": seed, "until": until, "level": level,
                "rule_version": RUIHE_RANCH_VERSION}, reference=f"{self.reference}:{animal['id']}")
        return {"kind": "feed", "quantity": feed_count, "premium_quantity": premium_count,
            "remaining": self.stock("feed"), "premium_remaining": self.stock("premium_feed"),
            "balance": self.balance(), "animals": len(targets), "until": self.now + cfg["production_hours"] * 3600}

    def sell(self, item, count=None):
        self.account(True)
        more = self.settle()
        self.collect()
        lines = []
        for code in self.config["products"] if item == "all" else [item]:
            amount = self.stock(code) if count is None else count
            if amount == 0:
                continue
            price = market_price(code, self.now, self.config)
            ref = f"{self.reference}:{code}"
            self.inventory_change(code, -amount, "sell", ref)
            lines.append({**self.trade("sell", code, amount, price * amount, ref), "price": price})
        if not lines:
            raise GameError("no_products")
        self.event("sell", {"lines": lines})
        return {"kind": "sell", "lines": lines, "balance": self.balance(), "pool": self.pool(), "more": more}

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
        total = self.db.execute(select(func.count()).select_from(animals).where(animals.c.player_id == self.player)).scalar_one()
        rows = self.db.execute(select(animals).where(animals.c.player_id == self.player)
            .order_by(animals.c.production_until, animals.c.id).offset((page - 1) * 5).limit(5)).mappings().all()
        pending = self.db.execute(select(func.coalesce(func.sum(products.c.quantity), 0)).where(
            products.c.player_id == self.player, products.c.collected_at.is_(None))).scalar_one()
        pending_rows = self.db.execute(select(products.c.source_animal_id,
            func.sum(products.c.quantity)).where(products.c.player_id == self.player,
            products.c.collected_at.is_(None)).group_by(products.c.source_animal_id)).all()
        pending_by_animal = {animal_id: total for animal_id, total in pending_rows}
        animal_rows = []
        for row in rows:
            animal = dict(row)
            animal["running"] = animal["status"] == "active" and animal["production_until"] > self.now
            animal["progress"] = max(0.0, min(1.0, animal["cycle_progress"])) if animal["rule_version"] == RUIHE_RANCH_VERSION else max(0.0, min(1.0, (self.now - animal["last_settled_at"]) / max(1, animal["next_production_at"] - animal["last_settled_at"])))
            animal["next_in"] = max(0, animal["next_production_at"] - self.now)
            animal["feed_in"] = max(0, animal["production_until"] - self.now)
            animal_rows.append(animal)
        return {"kind": "ranch", **ranch, "capacity": capacity(ranch["level"], self.config),
            "name": self.db.execute(select(players.c.name).where(players.c.id == self.player)).scalar_one(),
            "animals": animal_rows, "stocks": {code: self.stock(code) for code in ["feed", "premium_feed", *self.config["products"]]},
            "prices": {code: market_price(code, self.now, self.config) for code in self.config["products"]},
            "pending": pending, "more": more, "page": page, "pages": max(1, (total + 4) // 5), "now": self.now,
            "pending_by_animal": pending_by_animal, "weather_enabled": False}

    def ranking(self, page):
        rows = self.db.execute(select(players.c.name, accounts.c.balance).join(accounts, accounts.c.player_id == players.c.id)
            .order_by(accounts.c.balance.desc(), accounts.c.player_id).offset((page - 1) * 5).limit(5)).mappings().all()
        return {"kind": "ranking", "rows": [dict(r) for r in rows], "page": page}

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
        "balance_total": account_total, "balance_difference": account_total - ledger_total,
        "account_mismatches": mismatches, "pool": pool, "tax_total": taxes,
        "pool_tax_difference": pool - taxes, "pool_ledger_difference": pool - system}

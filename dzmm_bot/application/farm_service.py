"""Transactional P7 planting, harvesting and horse-feed supply."""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from sqlalchemy import select, func
from dzmm_bot.domain.economy import GameError, local_date, market_fee, ranch_duration_multiplier
from dzmm_bot.domain.farm_rules import (RULE_VERSION, ECONOMIC_RULE_VERSION, CROPS, ECONOMIC_CROPS,
    FEEDS, CROP_NAMES, FEED_NAMES, PLOT_CAPACITY, UPGRADE_COSTS, CROP_SELL_PRICES,
    crop_yield, crop_weather_snapshot, feed_shop_price)
from dzmm_bot.persistence.schema import farms, farm_plots, horse_feed_purchases
from .services import GameService, uid


class FarmService(GameService):
    def farm(self, create=False):
        row = self.db.execute(select(farms).where(farms.c.player_id == self.player).with_for_update()).mappings().first()
        if row is None and create:
            self.db.execute(farms.insert().values(player_id=self.player, level=1, version=1,
                created_at=self.now, updated_at=self.now))
            for number in range(1, PLOT_CAPACITY[0] + 1):
                self.db.execute(farm_plots.insert().values(id=uid(), player_id=self.player,
                    plot_no=number, version=1))
            row = self.db.execute(select(farms).where(farms.c.player_id == self.player)).mappings().one()
        return row

    def plots(self, lock=False):
        query = select(farm_plots).where(farm_plots.c.player_id == self.player).order_by(farm_plots.c.plot_no)
        return self.db.execute(query.with_for_update() if lock else query).mappings().all()

    def panel(self):
        self.account(True)
        farm = self.farm(create=True)
        plots = self.plots()
        crops = {code: self.stock(code) for code in CROPS}
        from .weather_service import WeatherService
        return {"kind": "farm_panel", "level": farm["level"], "plots": plots,
                'level_duration_multiplier': ranch_duration_multiplier(farm['level']),
                "upgrade_cost": UPGRADE_COSTS.get(farm["level"]) if UPGRADE_COSTS else None,
                "crops": crops, "now": self.now,
                "weather": WeatherService(self.db,self.now,enabled=self.weather_enabled).player_summary()}

    def seed_shop(self):
        self.account()
        return {"kind": "farm_seed_shop"}

    def buy_seeds(self, crop_code, quantity):
        self.account(True)
        rule = CROPS[crop_code]
        cost = self._purchase_seeds(crop_code, quantity)
        self.event("farm_seed_buy", {"crop": crop_code, "quantity": quantity, "cost": cost})
        return {"kind": "farm_seed_buy", "name": rule["name"], "quantity": quantity,
                "cost": cost, "balance": self.balance()}

    def _purchase_seeds(self, crop_code, quantity):
        cost = CROPS[crop_code]["seed_price"] * quantity
        self.ledger(-cost, "farm_seed_buy", "farm_seed_buy", self.reference)
        self.inventory_change(f"seed_{crop_code}", quantity, "farm_seed_buy", self.reference)
        return cost

    def plant(self, crop_code, quantity):
        if type(quantity) is not int or not 1 <= quantity <= 100000:
            raise GameError("quantity")
        self.account(True)
        farm=self.farm(create=True)
        rule = CROPS[crop_code]
        plots = [row for row in self.plots(lock=True) if row["crop_code"] is None]
        if not plots:
            raise GameError("farm_space", available=0)
        requested = quantity
        quantity = min(quantity, len(plots))
        seed_code = f"seed_{crop_code}"
        missing = max(0, quantity - self.stock(seed_code))
        auto_cost = 0
        if missing:
            auto_cost = self._purchase_seeds(crop_code, missing)
            self.event("farm_seed_buy", {"crop": crop_code, "quantity": missing,
                                         "cost": auto_cost, "automatic": True}, reference=uid())
        self.inventory_change(seed_code, -quantity, "farm_plant", self.reference)
        planted = []
        for plot in plots[:quantity]:
            batch_id = uid()
            rule_version = (ECONOMIC_RULE_VERSION if crop_code in ECONOMIC_CROPS else RULE_VERSION) + ':level-v2'
            weather = "disabled"
            duration_factor, yield_factor = crop_weather_snapshot(crop_code, weather)
            duration_factor *= Decimal(str(ranch_duration_multiplier(farm['level'])))
            base_yield = crop_yield(batch_id, crop_code, *rule["yield"], version=rule_version)
            yield_quantity = int((Decimal(base_yield) * yield_factor).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP))
            ready_at = self.now + float(rule["hours"] * 3600 * duration_factor)
            self.db.execute(farm_plots.update().where(farm_plots.c.id == plot["id"],
                farm_plots.c.crop_code.is_(None)).values(crop_code=crop_code, batch_id=batch_id,
                planted_at=self.now, ready_at=ready_at, yield_quantity=yield_quantity,
                yield_min=rule["yield"][0], yield_max=rule["yield"][1],
                harvest_weather_code=None,harvest_weather_adjustment=None,
                harvest_weather_rule_version=None,
                base_duration_seconds=int(rule["hours"] * 3600),
                weather_snapshot=weather, duration_multiplier=duration_factor,
                rule_version=rule_version, version=farm_plots.c.version + 1))
            planted.append(plot["plot_no"])
        self.event("farm_plant", {"crop": crop_code, "plots": planted})
        auto_harvest = self.harvest(event_reference=uid())
        return {"kind": "farm_plant", "name": rule["name"], "plots": planted,
                "requested": requested, "quantity": quantity,
                "ready_at": ready_at, "now": self.now, "auto_bought": missing,
                "auto_cost": auto_cost, "balance": self.balance(),
                "auto_harvest": auto_harvest["totals"],
                "auto_harvest_growing": auto_harvest["growing"]}

    def harvest(self, event_reference=None):
        self.account(True)
        self.farm(create=True)
        ready = [row for row in self.plots(lock=True)
                 if row["crop_code"] is not None and row["ready_at"] <= self.now]
        totals = Counter()
        from .weather_service import WeatherService
        from dzmm_bot.domain.weather_rules import game_date
        weather_service=WeatherService(self.db,self.now,enabled=self.weather_enabled)
        for plot in ready:
            quantity,adjustment,weather_code=weather_service.adjust(day=game_date(self.now),
                base=plot["yield_quantity"],key=plot["batch_id"],crop_code=plot["crop_code"])
            self.inventory_change(plot["crop_code"], quantity, "farm_harvest",
                                  plot["batch_id"], config_version=plot["rule_version"])
            totals[plot["crop_code"]] += quantity
            self.db.execute(farm_plots.update().where(farm_plots.c.id == plot["id"],
                farm_plots.c.batch_id == plot["batch_id"]).values(crop_code=None, batch_id=None,
                harvest_weather_code=weather_code,harvest_weather_adjustment=adjustment if weather_code else None,
                harvest_weather_rule_version="ruihe-weather-v1" if weather_code else None,
                planted_at=None, ready_at=None, yield_quantity=None, yield_min=None, yield_max=None,
                base_duration_seconds=None, weather_snapshot=None,
                duration_multiplier=None, rule_version=None, version=farm_plots.c.version + 1))
        if ready:
            self.event("farm_harvest", {"totals": dict(totals), "plots": [r["plot_no"] for r in ready]},
                       reference=event_reference)
        growing = sum(row["crop_code"] is not None and row["ready_at"] > self.now for row in self.plots())
        return {"kind": "farm_harvest", "totals": totals, "growing": growing}

    def upgrade_farm(self):
        self.account(True)
        farm = self.farm(create=True)
        if farm["level"] >= len(PLOT_CAPACITY):
            raise GameError("farm_max")
        if UPGRADE_COSTS is None:
            raise GameError("farm_upgrade_unavailable")
        level, cost = farm["level"], UPGRADE_COSTS[farm["level"]]
        self.ledger(-cost, "farm_upgrade", "farm_upgrade", self.reference)
        for plot in self.plots(lock=True):
            if plot['crop_code'] is None or plot['ready_at']<=self.now:
                continue
            old_factor=ranch_duration_multiplier(level) if plot['rule_version'].endswith(':level-v2') else 1.0
            ratio=ranch_duration_multiplier(level+1)/old_factor
            self.db.execute(farm_plots.update().where(farm_plots.c.id==plot['id']).values(
                planted_at=self.now-(self.now-plot['planted_at'])*ratio,
                ready_at=self.now+(plot['ready_at']-self.now)*ratio,
                duration_multiplier=Decimal(str(plot['duration_multiplier']))*Decimal(str(ratio)),
                rule_version=plot['rule_version'].removesuffix(':level-v2')+':level-v2',
                version=farm_plots.c.version+1))
        for number in range(PLOT_CAPACITY[level - 1] + 1, PLOT_CAPACITY[level] + 1):
            self.db.execute(farm_plots.insert().values(id=uid(), player_id=self.player,
                plot_no=number, version=1))
        self.db.execute(farms.update().where(farms.c.player_id == self.player).values(
            level=level + 1, version=farms.c.version + 1, updated_at=self.now))
        self.event("farm_upgrade", {"level": level + 1, "cost": str(cost)})
        return {"kind": "farm_upgrade", "level": level + 1, "capacity": PLOT_CAPACITY[level],
                "cost": cost, "balance": self.balance()}

    def recipes(self):
        self.account()
        return {"kind": "feed_recipes"}

    def mix(self, feed_code, quantity):
        self.account(True)
        rule = FEEDS[feed_code]
        requirements = {crop: amount * quantity for crop, amount in rule["ingredients"].items()}
        for crop, amount in requirements.items():
            if self.stock(crop) < amount:
                raise GameError("farm_ingredient_short", name=CROPS[crop]["name"],
                                missing=amount - self.stock(crop), required=amount, available=self.stock(crop))
        for crop, amount in requirements.items():
            self.inventory_change(crop, -amount, "feed_mix", self.reference)
        self.inventory_change(feed_code, quantity, "feed_mix", self.reference)
        self.event("feed_mix", {"feed": feed_code, "quantity": quantity, "ingredients": requirements})
        return {"kind": "feed_mix", "name": rule["name"], "quantity": quantity,
                "ingredients": requirements}

    def feed_shop(self):
        self.account()
        return {"kind": "feed_shop"}

    def buy_feed(self, feed_code, quantity):
        self.account(True)
        rule = FEEDS[feed_code]
        if "daily_limit" in rule:
            spent = self.db.execute(select(func.coalesce(func.sum(horse_feed_purchases.c.quantity), 0)).where(
                horse_feed_purchases.c.player_id == self.player,
                horse_feed_purchases.c.feed_code == feed_code,
                horse_feed_purchases.c.local_day == local_date(self.now))).scalar_one()
            if spent + quantity > rule["daily_limit"]:
                raise GameError("horse_feed_limit", remaining=max(0, rule["daily_limit"] - spent))
        cost = feed_shop_price(feed_code) * quantity
        self.ledger(-cost, "horse_feed_buy", "horse_feed_buy", self.reference)
        self.inventory_change(feed_code, quantity, "horse_feed_buy", self.reference)
        self.db.execute(horse_feed_purchases.insert().values(id=self.reference, player_id=self.player,
            feed_code=feed_code, quantity=quantity, local_day=local_date(self.now), created_at=self.now))
        self.event("horse_feed_buy", {"feed": feed_code, "quantity": quantity, "cost": cost})
        return {"kind": "horse_feed_buy", "name": rule["name"], "quantity": quantity,
                "cost": cost, "balance": self.balance()}

    def sell_crop(self, crop_code, quantity):
        self.account(True)
        if crop_code in ECONOMIC_CROPS:
            return self.sell(crop_code, quantity)
        if CROP_SELL_PRICES is None:
            raise GameError("farm_sell_unavailable")
        if self.stock(crop_code) < quantity:
            raise GameError("farm_ingredient_short", name=CROPS[crop_code]["name"],
                            missing=quantity - self.stock(crop_code), required=quantity, available=self.stock(crop_code))
        gross = CROP_SELL_PRICES[crop_code] * quantity
        fee = market_fee(gross)
        net = gross - fee
        self.inventory_change(crop_code, -quantity, "farm_sell", self.reference)
        self.ledger(net, "farm_sell", "farm_sell", self.reference)
        self.ledger(fee, "transaction_tax", "farm_sell_tax", self.reference, system=True)
        self.event("farm_sell", {"crop": crop_code, "quantity": quantity, "gross": str(gross),
                                 "fee": str(fee), "net": str(net)})
        return {"kind": "farm_sell", "name": CROPS[crop_code]["name"], "quantity": quantity,
                "gross": gross, "fee": fee, "net": net, "balance": self.balance()}

    def sell_any(self, item_code, quantity):
        """One player-facing sale entry point; keep each item's established pricing."""
        return self.sell_crop(item_code, quantity) if item_code in CROP_SELL_PRICES else self.sell(item_code, quantity)

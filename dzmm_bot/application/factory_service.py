"""Persistent processing-factory use cases."""
import json
import uuid
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

from sqlalchemy import select

from dzmm_bot.application.services import GameService
from dzmm_bot.domain.economy import GameError, market_window, market_price
from dzmm_bot.domain.factory_rules import (FACTORY_LEVELS, FACTORY_UPGRADE_REQUIREMENTS,
    RECIPES, RECIPE_VERSION, seeded_roll)
from dzmm_bot.domain.buff_rules import BUFF_REFUND_RATES, EXPEDITE_COSTS, MAX_EXPEDITES_PER_DAY
from dzmm_bot.domain.economy import local_date
from dzmm_bot.persistence.schema import factories, factory_jobs


class FactoryService(GameService):
    def __init__(self, *args, roll_provider=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.roll_provider = roll_provider

    def _roll(self, job_id, purpose):
        return Decimal(self.roll_provider(job_id, RECIPE_VERSION, purpose) if self.roll_provider
                       else seeded_roll(self.secret, job_id, RECIPE_VERSION, purpose))

    def _factory(self):
        row = self.db.execute(select(factories).where(factories.c.player_id == self.player)
                              .with_for_update()).mappings().first()
        if not row:
            self.db.execute(factories.insert().values(player_id=self.player, level=1, line_count=1,
                                                      created_at=self.now, updated_at=self.now))
            row = self.db.execute(select(factories).where(factories.c.player_id == self.player)).mappings().one()
        return row

    def panel(self):
        self.account()
        factory = self._factory()
        jobs = self.db.execute(select(factory_jobs).where(factory_jobs.c.player_id == self.player,
            factory_jobs.c.status.in_(["processing", "completed_pending_collect"])).order_by(factory_jobs.c.line_no)).mappings().all()
        today = local_date(self.now)
        rush_count = factory["rush_count"] if factory["rush_used_on"] == today else 0
        failure_rates = {key: self.buffs.calculate_failure_rate(rule["failure_rate"], factory["level"],
            rule["tier"]) for key, rule in RECIPES.items()}
        next_job_multiplier = FACTORY_LEVELS[factory["level"]]["duration_multiplier"] * self.buffs.get_multiplier("processing_duration")
        return {"kind": "factory", "level": factory["level"], "line_count": factory["line_count"],
                "lines": [dict(job) for job in jobs], "now": self.now, "recipes": RECIPES,
                "upgrade": FACTORY_UPGRADE_REQUIREMENTS.get(factory["level"] + 1),
                "duration_multiplier": FACTORY_LEVELS[factory["level"]]["duration_multiplier"],
                "new_job_duration_multiplier": next_job_multiplier,
                "failure_rates": failure_rates, "rush_count": rush_count,
                "current_failure_rate": self.buffs.calculate_failure_rate(Decimal("0.04"), factory["level"], 1),
                "automation_level": factory["automation_level"],
                "automation_core_stock": self.stock("automation_core"), "balance": self.balance()}

    def start(self, recipe_id, count=1):
        self.account(True)
        if type(count) is not int or not 1 <= count <= 1000:
            raise GameError("quantity")
        recipe = RECIPES.get(recipe_id)
        if not recipe:
            raise GameError("factory_recipe", name=recipe_id)
        factory = self._factory()
        if factory["level"] < recipe["factory_level_required"]:
            raise GameError("factory_locked", level=recipe["factory_level_required"])
        active = self.db.execute(select(factory_jobs.c.line_no).where(factory_jobs.c.player_id == self.player,
            factory_jobs.c.status == "processing")).scalars().all()
        line = next((i for i in range(1, factory["line_count"] + 1) if i not in active), None)
        if line is None:
            raise GameError("factory_full")
        inputs = {item: amount * count for item, amount in recipe["ingredients"].items()}
        for item, amount in inputs.items():
            self.inventory_change(item, -amount, "factory_input", f"{self.reference}:{item}")
        job_id = self.reference
        rolls = {purpose: self._roll(job_id, purpose) for purpose in ("failure", "critical")}
        factory_multiplier = FACTORY_LEVELS[factory["level"]]["duration_multiplier"]
        buff_duration_multiplier = self.buffs.get_multiplier("processing_duration")
        total_duration_multiplier = factory_multiplier * buff_duration_multiplier
        duration = int((Decimal(recipe["base_duration"]) * total_duration_multiplier)
                       .quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        failure_rate = self.buffs.calculate_failure_rate(recipe["failure_rate"], factory["level"], recipe["tier"])
        window_id, _ = market_window(self.now)
        output = {"item": recipe["output_item"], "quantity": recipe["output_quantity"] * count,
                  "market_mode": recipe["market_mode"], "base_price": recipe["base_price"]}
        price_snapshot = {"market_window_id": window_id}
        if recipe["base_price"] is not None:
            price_snapshot["unit_price"] = str(market_price(recipe["output_item"], self.now, self.config))
        self.db.execute(factory_jobs.insert().values(id=job_id, player_id=self.player, line_no=line,
            recipe_id=recipe_id, recipe_version=RECIPE_VERSION, batch_quantity=count, started_at=self.now,
            finish_at=self.now + duration, status="processing", input_snapshot_json=json.dumps(inputs),
            output_snapshot_json=json.dumps(output), failure_roll=str(rolls["failure"]),
            critical_roll=str(rolls["critical"]), failed=rolls["failure"] < failure_rate,
            critical=rolls["critical"] < recipe["critical_rate"], market_window_id=window_id,
            price_snapshot_json=json.dumps(price_snapshot, default=str), collected_at=None,
            failure_rate=failure_rate, critical_rate=recipe["critical_rate"],
            duration_multiplier=total_duration_multiplier))
        self.event("factory_start", {"job": job_id, "recipe": recipe_id, "line": line, "inputs": inputs,
            "failure_rate": failure_rate, "critical_rate": recipe["critical_rate"],
            "duration_multiplier": total_duration_multiplier})
        return {"kind": "factory_start", "recipe": recipe, "count": count, "line": line,
                "finish_at": self.now + duration, "inputs": inputs}

    def start_many(self, recipe_ids):
        """Start one batch of each recipe on its own available line atomically."""
        if not recipe_ids:
            raise GameError("syntax")
        if len(recipe_ids) > 1000:
            raise GameError("quantity")
        factory = self._factory()
        active = self.db.execute(select(factory_jobs.c.line_no).where(
            factory_jobs.c.player_id == self.player,
            factory_jobs.c.status == "processing")).scalars().all()
        if len(recipe_ids) > factory["line_count"] - len(set(active)):
            raise GameError("factory_full")
        original_reference = self.reference
        results = []
        try:
            for index, recipe_id in enumerate(recipe_ids, start=1):
                self.reference = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                                f"factory-batch:{original_reference}:{index}"))
                results.append(self.start(recipe_id, 1))
        finally:
            self.reference = original_reference
        return {"kind": "factory_start_batch", "results": results}

    def start_repeated(self, recipe_id, count):
        if type(count) is not int or not 1 <= count <= 1000:
            raise GameError("quantity")
        return self.start_many([recipe_id] * count)

    def collect_factory(self):
        self.account(True)
        jobs = self.db.execute(select(factory_jobs).where(factory_jobs.c.player_id == self.player,
            factory_jobs.c.status.in_(["processing", "completed_pending_collect"]), factory_jobs.c.finish_at <= self.now)
            .order_by(factory_jobs.c.line_no).with_for_update()).mappings().all()
        if not jobs:
            pending = self.db.execute(select(factory_jobs.c.finish_at).where(factory_jobs.c.player_id == self.player,
                factory_jobs.c.status.in_(["processing", "completed_pending_collect"])).order_by(factory_jobs.c.finish_at)).scalar()
            raise GameError("factory_not_ready", remaining=max(0, (pending or self.now) - self.now))
        totals, results = {}, []
        for job in jobs:
            output = json.loads(job["output_snapshot_json"])
            amount = 0 if job["failed"] else output["quantity"] * (2 if job["critical"] else 1)
            if amount:
                self.inventory_change(output["item"], amount, "factory_collect", job["id"])
                totals[output["item"]] = totals.get(output["item"], 0) + amount
            status = "failed" if job["failed"] else "collected"
            self.db.execute(factory_jobs.update().where(factory_jobs.c.id == job["id"],
                factory_jobs.c.status.in_(["processing", "completed_pending_collect"])).values(
                    status=status, collected_at=self.now))
            results.append({"recipe": RECIPES[job["recipe_id"]], "failed": bool(job["failed"]),
                            "critical": bool(job["critical"]), "quantity": amount, "line": job["line_no"]})
        self.event("factory_collect", {"totals": totals})
        return {"kind": "factory_collect", "results": results, "totals": totals}

    def upgrade_factory(self):
        self.account(True)
        factory = self._factory()
        next_level = factory["level"] + 1
        requirement = FACTORY_UPGRADE_REQUIREMENTS.get(next_level)
        if not requirement:
            raise GameError("factory_max")
        self.ledger(-requirement["coins"], "factory_upgrade", "factory_upgrade", self.reference)
        config = FACTORY_LEVELS[next_level]
        self.db.execute(factories.update().where(factories.c.player_id == self.player).values(
            level=next_level, line_count=config["line_count"], updated_at=self.now))
        self.event("factory_upgrade", {"level": next_level, "cost": requirement["coins"]})
        return {"kind": "factory_upgrade", "level": next_level, "line_count": config["line_count"],
                "duration_multiplier": config["duration_multiplier"], "cost": requirement["coins"],
                "balance": self.balance()}

    def upgrade_with_core(self):
        self.account(True)
        factory = self._factory()
        self.inventory_change("automation_core", -1, "factory_core_upgrade", self.reference)
        new_level = factory["automation_level"] + 1
        self.db.execute(factories.update().where(factories.c.player_id == self.player).values(
            automation_level=new_level, updated_at=self.now))
        self.event("factory_core_upgrade", {"automation_level": new_level,
            "effect": "pending_rules"})
        return {"kind": "factory_core_upgrade", "automation_level": new_level}

    def expedite(self, line_no=None):
        self.account(True)
        factory = self._factory()
        query = select(factory_jobs).where(factory_jobs.c.player_id == self.player,
            factory_jobs.c.status == "processing", factory_jobs.c.finish_at > self.now)
        if line_no is not None:
            query = query.where(factory_jobs.c.line_no == line_no)
        job = self.db.execute(query.order_by(factory_jobs.c.line_no).limit(1).with_for_update()).mappings().first()
        if not job:
            raise GameError("factory_line")
        today = local_date(self.now)
        used = factory["rush_count"] if factory["rush_used_on"] == today else 0
        if used >= MAX_EXPEDITES_PER_DAY:
            raise GameError("factory_expedite_limit")
        cost = EXPEDITE_COSTS[used]
        self.ledger(-cost, "factory_expedite", "factory_expedite", self.reference)
        changed = self.db.execute(factory_jobs.update().where(factory_jobs.c.id == job["id"],
            factory_jobs.c.status == "processing", factory_jobs.c.finish_at > self.now).values(
                status="completed_pending_collect", finish_at=self.now, expedited_at=self.now)).rowcount
        if changed != 1:
            raise GameError("factory_line")
        self.db.execute(factories.update().where(factories.c.player_id == self.player).values(
            rush_used_on=today, rush_count=used + 1, updated_at=self.now))
        self.event("factory_expedite", {"job": job["id"], "line": job["line_no"], "cost": cost,
            "daily_count": used + 1})
        return {"kind": "factory_expedite", "line": job["line_no"], "cost": cost,
                "daily_count": used + 1, "balance": self.balance()}

    def cancel(self, line_no=None):
        self.account(True)
        self._factory()
        query = select(factory_jobs).where(factory_jobs.c.player_id == self.player,
            factory_jobs.c.status == "processing", factory_jobs.c.finish_at > self.now)
        if line_no is not None:
            query = query.where(factory_jobs.c.line_no == line_no)
        job = self.db.execute(query.order_by(factory_jobs.c.line_no).limit(1).with_for_update()).mappings().first()
        if not job:
            raise GameError("factory_line")
        ratio = Decimal(str(max(0, min(1, (self.now - job["started_at"]) /
                                     max(1, job["finish_at"] - job["started_at"])))))
        refund_rate = next(rate for threshold, rate in BUFF_REFUND_RATES if ratio < threshold or threshold == 1)
        original = json.loads(job["input_snapshot_json"])
        refund = {item: int((Decimal(amount) * refund_rate).to_integral_value(rounding=ROUND_FLOOR))
                  for item, amount in original.items()}
        refund = {item: amount for item, amount in refund.items() if amount}
        for item, amount in refund.items():
            self.inventory_change(item, amount, "factory_cancel", f"{job['id']}:{item}")
        changed = self.db.execute(factory_jobs.update().where(factory_jobs.c.id == job["id"],
            factory_jobs.c.status == "processing", factory_jobs.c.finish_at > self.now).values(
                status="cancelled", cancelled_at=self.now,
                cancel_refund_json=json.dumps({"rate": str(refund_rate), "items": refund}, ensure_ascii=False))).rowcount
        if changed != 1:
            raise GameError("factory_line")
        self.event("factory_cancel", {"job": job["id"], "line": job["line_no"], "refund": refund,
            "rate": refund_rate})
        return {"kind": "factory_cancel", "line": job["line_no"], "refund": refund,
                "refund_rate": refund_rate}


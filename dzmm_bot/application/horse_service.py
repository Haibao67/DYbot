"""Persistent P6 stable operations. Feed and breeding entry points are gated."""
import json
import re
import uuid
from sqlalchemy import select, func
from dzmm_bot.domain.economy import GameError, seed_for, local_date
from dzmm_bot.domain.horse_rules import (RULE_VERSION, CAPACITIES, BUY_PRICE, TRAITS, FOAL_SECONDS,
    BREEDING_LIMIT, BREEDING_PRICE, BREEDING_GRASS, GESTATION_SECONDS, MALE_COOLDOWN_SECONDS,
    FEMALE_COOLDOWN_SECONDS, upgrade_cost, training_roll, foal_snapshot, stage)
from dzmm_bot.domain.horse_catalog import (catalog_snapshot, foal_catalog,
                                           VERSION as CATALOG_VERSION)
from dzmm_bot.domain.farm_rules import FEEDS
from dzmm_bot.persistence.schema import horse_stables, horses, horse_pregnancies, horse_training_events, horse_auction_custody
from .services import GameService, uid
from .horse_racing_service import HorseRacingService


class HorseService(GameService):
    def __init__(self, *args, racing_options=None, **kwargs):
        super().__init__(*args, **kwargs)
        from dzmm_bot.domain.race_rules_r1 import generation_policy, inheritance_policy
        from dzmm_bot.domain.skill_catalog import RANDOM_SKILL_LEVEL_WEIGHTS
        self.racing_options = {'affinity_policy':generation_policy(),
                               'level_weights':RANDOM_SKILL_LEVEL_WEIGHTS,
                               'affinity_inheritance_policy':inheritance_policy(),
                               **dict(racing_options or {})}

    def racing(self):
        if hasattr(self,'_racing_service'):
            return self._racing_service
        from dzmm_bot.domain.race_runtime import RaceRuntimeFactory
        runtime=RaceRuntimeFactory.build()
        racing=HorseRacingService(self.db,self.now,registry=runtime.skill_registry,
            **{k:v for k,v in self.racing_options.items() if k != 'registry'})
        racing.register_definitions(runtime.skill_registry)
        racing.runtime_pools=runtime.skill_pools
        self._racing_service=racing
        return racing

    def racing_horse(self, horse, finalize=False):
        racing = self.racing()
        racing.initialize(horse['id'])
        if finalize and racing.affinity_policy is not None and not racing.profile(horse['id'])['affinity_inheritance_pending']:
            racing.finalize_affinity(horse['id'])
        if finalize and not any(grade=='U' for values in racing.profile(horse['id'])['affinities'].values() for grade in values.values()):
            racing.initialize_runtime_skills(horse['id'],racing.runtime_pools)
        return {**horse, **racing.profile(horse['id'])}

    def _catalog_name(self, base):
        used=set(self.db.execute(select(horses.c.name).where(
            horses.c.player_id == self.player, horses.c.status == 'active')).scalars())
        if base not in used:
            return base
        def chinese_number(number):
            digits='零一二三四五六七八九'
            if number < 10: return digits[number]
            if number < 20: return '十'+(digits[number%10] if number%10 else '')
            if number < 100: return digits[number//10]+'十'+(digits[number%10] if number%10 else '')
            return ''.join(digits[int(c)] for c in str(number))
        number=2
        while base+'·'+chinese_number(number)+'号' in used:
            number+=1
        return base+'·'+chinese_number(number)+'号'

    def _automatic_name(self):
        used = set(self.db.execute(select(horses.c.name).where(
            horses.c.player_id == self.player)).scalars())
        number = 1
        while f"追风{number}" in used:
            number += 1
        return f"追风{number}"

    def _name_existing_horses(self):
        self.account()
        unnamed = self.db.execute(select(horses.c.id).where(
            horses.c.player_id == self.player, horses.c.name.is_(None))
            .order_by(horses.c.created_at, horses.c.id).with_for_update()).scalars().all()
        for horse_id in unnamed:
            self.db.execute(horses.update().where(horses.c.id == horse_id,
                horses.c.name.is_(None)).values(name=self._automatic_name(),
                                               version=horses.c.version + 1))

    def stable(self, create=False):
        row = self.db.execute(select(horse_stables).where(horse_stables.c.player_id == self.player)
                              .with_for_update()).mappings().first()
        if row is None and create:
            self.db.execute(horse_stables.insert().values(player_id=self.player, level=1, version=1,
                            created_at=self.now, updated_at=self.now))
            row = self.db.execute(select(horse_stables).where(horse_stables.c.player_id == self.player)).mappings().one()
        return row or {"level": 1}

    def active_horses(self):
        return self.db.execute(select(horses).where(horses.c.player_id == self.player,
                               horses.c.status == "active",
                               ~horses.c.id.in_(select(horse_auction_custody.c.horse_id)))
                               .order_by(horses.c.created_at, horses.c.id)).mappings().all()

    def panel(self):
        self.account()
        self._name_existing_horses()
        stable = self.stable()
        pending = self.db.execute(select(horse_pregnancies).where(
            horse_pregnancies.c.player_id == self.player,
            horse_pregnancies.c.status == "pending").order_by(horse_pregnancies.c.due_at)).mappings().all()
        locked = self.db.execute(select(func.count()).select_from(horse_auction_custody).where(
            horse_auction_custody.c.owner_id == self.player,
            horse_auction_custody.c.status == "locked")).scalar_one()
        holding = self.db.execute(select(func.count()).select_from(horse_auction_custody).where(
            horse_auction_custody.c.owner_id == self.player,
            horse_auction_custody.c.status == "holding")).scalar_one()
        return {"kind": "horse_stable", "level": stable["level"],
                "upgrade_cost": upgrade_cost(stable["level"]) if stable["level"] < len(CAPACITIES) else None,
                "capacity": CAPACITIES[stable["level"] - 1],
                "horses": [self.racing_horse(horse) for horse in self.active_horses()],
                "pending": pending, "locked": locked, "holding": holding, "now": self.now}

    def buy_horse(self):
        self.account(True)
        stable = self.stable(create=True)
        if self.occupied_slots() >= CAPACITIES[stable["level"] - 1]:
            raise GameError("horse_capacity")
        horse_id = uid()
        seed = seed_for(self.secret, horse_id, RULE_VERSION, self.reference)
        snapshot = catalog_snapshot(seed)
        name = self._catalog_name(snapshot["catalog"]["name"])
        self.ledger(-BUY_PRICE, "horse_buy", "horse_buy", self.reference)
        self.db.execute(horses.insert().values(id=horse_id, player_id=self.player, name=name,
            sex=snapshot["sex"], born_at=self.now - FOAL_SECONDS, generation=0,
            father_id=None, mother_id=None, **snapshot["traits"],
            **{f"growth_{key}": value for key, value in snapshot["growth"].items()},
            breeding_count=0, breeding_cooldown_until=0, status="active", retired_at=None,
            seed=seed, birth_traits_json=json.dumps(snapshot, ensure_ascii=False),
            feed_count=0, feed_charges=5, feed_recovered_at=self.now,
            rule_version=CATALOG_VERSION, version=1, created_at=self.now))
        self.event("horse_buy", {"horse_id": horse_id, "cost": str(BUY_PRICE), "rule_version": CATALOG_VERSION,
            "template_id":snapshot["catalog"]["template_id"]})
        row = self.db.execute(select(horses).where(horses.c.id == horse_id)).mappings().one()
        self.racing().initialize(horse_id, 'G0')
        if self.racing().affinity_policy is not None:
            self.racing().finalize_affinity(horse_id, 'G0')
            racing=self.racing()
            racing.initialize_runtime_skills(horse_id,racing.runtime_pools)
        return {"kind": "horse_buy", "horse": row, "cost": BUY_PRICE, "balance": self.balance()}

    def resolve_horse(self, identifier, include_retired=False):
        self.account()
        self._name_existing_horses()
        condition = horses.c.name == identifier
        if re.fullmatch(r"[0-9a-fA-F]{8,36}", identifier):
            condition = condition | horses.c.id.startswith(identifier.lower(), autoescape=True)
        statement = select(horses).where(horses.c.player_id == self.player, condition)
        if not include_retired:
            statement = statement.where(horses.c.status == "active",
                ~horses.c.id.in_(select(horse_auction_custody.c.horse_id)))
        rows = self.db.execute(statement.limit(2)).mappings().all()
        if not rows:
            raise GameError("horse_missing")
        if len(rows) > 1:
            raise GameError("horse_ambiguous")
        return rows[0]

    def admin_mature(self, identifier, admins=None, password_hash=''):
        from .admin_login_service import AdminLoginService
        from .horse_asset_lock import assert_horse_mutable
        from dzmm_bot.domain.horse_rules import YOUTH_SECONDS
        if not AdminLoginService(self.db,self.now,password_hash,admins or set()).is_admin(self.player):
            raise GameError('admin_only')
        with self.db.begin_nested():
            horse=self.resolve_horse(identifier)
            assert_horse_mutable(self.db,horse['id'])
            # Re-read after acquiring the shared mutation lock.
            horse=self.db.execute(select(horses).where(horses.c.id==horse['id'])).mappings().one()
            if stage(horse['born_at'],self.now)=='成年':
                return f"🐎 {horse['name']} 已经成年。｜/马 {horse['name']}"
            born_at=self.now-FOAL_SECONDS-YOUTH_SECONDS
            self.db.execute(horses.update().where(horses.c.id==horse['id']).values(
                born_at=born_at,version=horses.c.version+1))
            self.event('admin_horse_mature',{'actor':self.player,'horse_id':horse['id'],
                'old_born_at':horse['born_at'],'new_born_at':born_at})
            return f"✅ 🐎 {horse['name']} 已变为成年。｜/马 {horse['name']} | /繁育"

    def occupied_slots(self):
        pending = self.db.execute(select(func.count()).select_from(horse_pregnancies).where(
            horse_pregnancies.c.player_id == self.player,
            horse_pregnancies.c.status == "pending")).scalar_one()
        locked = self.db.execute(select(func.count()).select_from(horse_auction_custody).where(
            horse_auction_custody.c.owner_id == self.player,
            horse_auction_custody.c.status == "locked")).scalar_one()
        return len(self.active_horses()) + pending + locked

    def detail(self, identifier):
        return {"kind": "horse_detail", "horse": self.racing_horse(self.resolve_horse(identifier, True),True), "now": self.now}

    def rename(self, name, identifier=None):
        self.account(True)
        if not 1 <= len(name) <= 20 or any(ord(char) < 32 for char in name) or "/" in name:
            raise GameError("horse_name")
        if identifier:
            horse = self.resolve_horse(identifier)
        else:
            available = self.active_horses()
            if len(available) != 1:
                raise GameError("horse_rename_target")
            horse = available[0]
        from .horse_asset_lock import assert_horse_mutable
        assert_horse_mutable(self.db, horse['id'])
        existing = self.db.execute(select(horses.c.id).where(horses.c.player_id == self.player,
            horses.c.status == "active", horses.c.name == name, horses.c.id != horse["id"])).first()
        if existing:
            raise GameError("horse_name_taken")
        self.db.execute(horses.update().where(horses.c.id == horse["id"], horses.c.player_id == self.player,
            horses.c.status == "active").values(name=name, version=horses.c.version + 1))
        self.event("horse_rename", {"horse_id": horse["id"], "name": name})
        return {"kind": "horse_rename", "horse": {**horse, "name": name}}

    def upgrade_stable(self):
        self.account(True)
        stable = self.stable(create=True)
        level = stable["level"]
        if level >= len(CAPACITIES):
            raise GameError("horse_stable_max")
        cost = upgrade_cost(level)
        self.ledger(-cost, "horse_stable_upgrade", "horse_stable_upgrade", self.reference)
        self.db.execute(horse_stables.update().where(horse_stables.c.player_id == self.player,
            horse_stables.c.level == level).values(level=level + 1, version=horse_stables.c.version + 1,
            updated_at=self.now))
        self.event("horse_stable_upgrade", {"level": level + 1, "cost": str(cost)})
        return {"kind": "horse_upgrade", "level": level + 1, "capacity": CAPACITIES[level],
                "cost": cost, "balance": self.balance()}

    def retire(self, identifier):
        self.account(True)
        horse = self.resolve_horse(identifier)
        from .horse_asset_lock import assert_horse_mutable
        assert_horse_mutable(self.db, horse['id'])
        pending = self.db.execute(select(horse_pregnancies.c.id).where(
            horse_pregnancies.c.mother_id == horse["id"], horse_pregnancies.c.status == "pending")).first()
        if pending:
            raise GameError("horse_pregnant")
        self.db.execute(horses.update().where(horses.c.id == horse["id"], horses.c.status == "active")
                        .values(status="retired", retired_at=self.now, version=horses.c.version + 1))
        self.event("horse_retire", {"horse_id": horse["id"]})
        return {"kind": "horse_retire", "horse": horse}

    def studbook(self):
        self.account()
        self._name_existing_horses()
        rows = self.db.execute(select(horses).where(horses.c.player_id == self.player)
                               .order_by(horses.c.created_at.desc()).limit(30)).mappings().all()
        return {"kind": "horse_studbook", "horses": rows}

    def lineage(self, identifier):
        horse = self.racing_horse(self.resolve_horse(identifier, True))
        def ancestor(horse_id):
            return self.db.execute(select(horses).where(horses.c.id == horse_id)).mappings().first() if horse_id else None
        mother, father = ancestor(horse["mother_id"]), ancestor(horse["father_id"])
        return {"kind": "horse_lineage", "horse": horse,
                "parents": [("母马", mother), ("公马", father)],
                "grandparents": [("外祖母", ancestor(mother["mother_id"] if mother else None)),
                                  ("外祖父", ancestor(mother["father_id"] if mother else None)),
                                  ("祖母", ancestor(father["mother_id"] if father else None)),
                                  ("祖父", ancestor(father["father_id"] if father else None))]}

    def feed_horse(self, identifier, feed_code):
        self.account(True)
        if feed_code not in FEEDS or feed_code == "premium_grass":
            raise GameError("horse_feed_type")
        horse = self.resolve_horse(identifier)
        from .horse_asset_lock import assert_horse_mutable
        assert_horse_mutable(self.db, horse['id'])
        if stage(horse["born_at"], self.now) == "幼驹":
            raise GameError("horse_training_age")
        if self.stock(feed_code) < 1:
            raise GameError("horse_feed_short", name=FEEDS[feed_code]["name"],
                            required=1, available=self.stock(feed_code), missing=1 - self.stock(feed_code))
        day = local_date(self.now)
        from dzmm_bot.domain.horse_rules import FEED_LIFETIME_LIMIT, feed_budget
        used = horse['feed_count']
        charges, recovered_at = feed_budget(horse, self.now)
        if used >= FEED_LIFETIME_LIMIT:
            raise GameError("horse_training_limit")
        if charges < 1:
            raise GameError('horse_feed_recover', minutes=max(1, int((recovered_at + 3600 - self.now + 59) // 60)))
        seed = seed_for(self.secret, horse["id"], RULE_VERSION, self.reference)
        gains = training_roll(seed, feed_code, horse, used)
        self.inventory_change(feed_code, -1, "horse_feed", self.reference)
        self.db.execute(horses.update().where(horses.c.id == horse["id"], horses.c.status == "active")
                        .values(**{trait: horse[trait] + gains.get(trait, 0) for trait in TRAITS},
                                feed_count=used + 1, feed_charges=charges - 1,
                                feed_recovered_at=recovered_at, version=horses.c.version + 1))
        self.db.execute(horse_training_events.insert().values(id=uid(), player_id=self.player,
            horse_id=horse["id"], local_day=day, feed_code=feed_code,
            gains_json=json.dumps(gains), reference_id=self.reference, created_at=self.now))
        self.event("horse_feed", {"horse_id": horse["id"], "feed": feed_code, "gains": gains})
        return {"kind": "horse_feed", "horse": horse, "feed": FEEDS[feed_code]["name"],
                "gains": gains, "remaining": charges - 1, 'feed_count': used + 1}

    def breed_horses(self, mother, father):
        self.account(True)
        mare, stallion = self.resolve_horse(mother), self.resolve_horse(father)
        from .horse_asset_lock import assert_horse_mutable
        for horse_id in sorted({mare['id'],stallion['id']}):
            assert_horse_mutable(self.db, horse_id)
        if mare["sex"] == "male" and stallion["sex"] == "female":
            mare, stallion = stallion, mare
        if mare["id"] == stallion["id"] or mare["sex"] != "female" or stallion["sex"] != "male":
            raise GameError("horse_breeding_pair")
        if stage(mare["born_at"], self.now) != "成年" or stage(stallion["born_at"], self.now) != "成年":
            raise GameError("horse_breeding_age")
        if mare["breeding_count"] >= BREEDING_LIMIT or stallion["breeding_count"] >= BREEDING_LIMIT:
            raise GameError("horse_breeding_limit")
        if mare["breeding_cooldown_until"] > self.now or stallion["breeding_cooldown_until"] > self.now:
            raise GameError("horse_breeding_cooldown")
        if self._kinship(mare, stallion):
            raise GameError("horse_kinship")
        stable = self.stable(create=True)
        if self.occupied_slots() >= CAPACITIES[stable["level"] - 1]:
            raise GameError("horse_capacity")
        if self.stock("premium_grass") < BREEDING_GRASS:
            raise GameError("horse_feed_short", name="优质牧草", required=BREEDING_GRASS,
                            available=self.stock("premium_grass"), missing=BREEDING_GRASS - self.stock("premium_grass"))
        pregnancy_id = uid()
        seed = seed_for(self.secret, pregnancy_id, RULE_VERSION, self.reference)
        snapshot = foal_snapshot(seed, mare, stallion)
        child_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'dybot:birth:' + pregnancy_id))
        racing = self.racing()
        racing.initialize(mare['id'])
        racing.initialize(stallion['id'])
        if racing.affinity_policy is not None:
            for parent in (mare,stallion):
                if not racing.profile(parent['id'])['affinity_inheritance_pending']:
                    racing.finalize_affinity(parent['id'])
        for parent in (mare,stallion):
            racing.initialize_runtime_skills(parent['id'],racing.runtime_pools)
        father_profile, mother_profile = racing.profile(stallion['id']), racing.profile(mare['id'])
        def parent_catalog(parent_profile):
            origin = parent_profile.get('catalog')
            if origin:
                return origin
            affinities = parent_profile['affinities']
            def preferred(category):
                return min(affinities[category], key=lambda key: (
                    'SABCD'.index(affinities[category][key]),
                    list(affinities[category]).index(key)))
            return {'recommended_distance': preferred('distance'),
                    'recommended_style': preferred('running_style'),
                    'recommended_surface': preferred('surface')}
        snapshot['catalog'] = foal_catalog(seed, parent_catalog(father_profile),
                                           parent_catalog(mother_profile))
        if racing.level_weights is not None:
            snapshot['p8_skill_inheritance'] = racing.inheritance_snapshot(
                stallion['id'], mare['id'], pregnancy_id, child_id)
        if racing.affinity_inheritance_policy is not None:
            snapshot['p8_affinity_inheritance'] = racing.affinity_inheritance_snapshot(
                stallion['id'],mare['id'],max(mare['generation'],stallion['generation'])+1,pregnancy_id,child_id)
        snapshot['p8_child_id'] = child_id
        snapshot['p8_affinity_state'] = 'FROZEN' if snapshot.get('p8_affinity_inheritance') else 'U'
        self.ledger(-BREEDING_PRICE, "horse_breeding", "horse_breeding", self.reference)
        self.inventory_change("premium_grass", -BREEDING_GRASS, "horse_breeding", self.reference)
        self.db.execute(horse_pregnancies.insert().values(id=pregnancy_id, player_id=self.player,
            mother_id=mare["id"], father_id=stallion["id"], conceived_at=self.now,
            due_at=self.now + GESTATION_SECONDS, status="pending",
            foal_snapshot_json=json.dumps({**snapshot, "seed": seed}, ensure_ascii=False),
            rule_version=RULE_VERSION, foal_id=None, delivered_at=None, created_at=self.now))
        for horse, cooldown in ((mare, FEMALE_COOLDOWN_SECONDS), (stallion, MALE_COOLDOWN_SECONDS)):
            self.db.execute(horses.update().where(horses.c.id == horse["id"]).values(
                breeding_count=horse["breeding_count"] + 1,
                breeding_cooldown_until=self.now + cooldown, version=horses.c.version + 1))
        self.event("horse_breed", {"mother_id": mare["id"], "father_id": stallion["id"],
                                   "pregnancy_id": pregnancy_id, "cost": str(BREEDING_PRICE)})
        return {"kind": "horse_breed", "mother": mare, "father": stallion,
                "due_at": self.now + GESTATION_SECONDS, "cost": BREEDING_PRICE}

    def admin_deliver_foals(self, admins=None, password_hash=''):
        from .admin_login_service import AdminLoginService
        if not AdminLoginService(self.db,self.now,password_hash,admins or set()).is_admin(self.player):
            raise GameError('admin_only')
        with self.db.begin_nested():
            self.account(True)
            pending=self.db.execute(select(horse_pregnancies).where(
                horse_pregnancies.c.player_id==self.player,horse_pregnancies.c.status=='pending')
                .order_by(horse_pregnancies.c.id).with_for_update()).mappings().all()
            if not pending:
                raise GameError('horse_no_foal')
            for pregnancy in pending:
                self.db.execute(horse_pregnancies.update().where(horse_pregnancies.c.id==pregnancy['id'],
                    horse_pregnancies.c.status=='pending').values(due_at=min(self.now,pregnancy['due_at'])))
            result=self.deliver_foals()
            self.event('admin_horse_delivery',{'actor':self.player,'pregnancies':[
                {'id':p['id'],'original_due_at':p['due_at']} for p in pending]},reference=uid())
            return result

    def deliver_foals(self):
        self.account(True)
        due = self.db.execute(select(horse_pregnancies).where(
            horse_pregnancies.c.player_id == self.player, horse_pregnancies.c.status == "pending",
            horse_pregnancies.c.due_at <= self.now).order_by(horse_pregnancies.c.due_at)
            .with_for_update()).mappings().all()
        if not due:
            raise GameError("horse_no_foal")
        born = []
        for pregnancy in due:
            snapshot = json.loads(pregnancy["foal_snapshot_json"])
            horse_id = snapshot.get('p8_child_id') or str(uuid.uuid5(uuid.NAMESPACE_URL, 'dybot:birth:' + pregnancy['id']))
            mother = self.db.execute(select(horses).where(horses.c.id == pregnancy["mother_id"])).mappings().one()
            father = self.db.execute(select(horses).where(horses.c.id == pregnancy["father_id"])).mappings().one()
            generation = max(mother["generation"], father["generation"]) + 1
            name = self._catalog_name(snapshot.get('name') or self._automatic_name())
            snapshot = {**snapshot, 'name': name}
            self.db.execute(horses.insert().values(id=horse_id, player_id=self.player, name=name,
                sex=snapshot["sex"], born_at=self.now, generation=generation,
                father_id=father["id"], mother_id=mother["id"], **snapshot["traits"],
                **{f"growth_{trait}": rank for trait, rank in snapshot["growth"].items()},
                breeding_count=0, breeding_cooldown_until=0, status="active", retired_at=None,
                seed=snapshot["seed"], birth_traits_json=pregnancy["foal_snapshot_json"],
                feed_count=0, feed_charges=5, feed_recovered_at=self.now,
                rule_version=RULE_VERSION, version=1, created_at=self.now))
            racing = self.racing()
            racing.initialize(horse_id, 'BIRTH')
            if snapshot.get('p8_skill_inheritance'):
                racing.apply_inheritance(horse_id, pregnancy['id'], snapshot['p8_skill_inheritance'])
            if snapshot.get('p8_affinity_inheritance'):
                racing.apply_affinity_inheritance(horse_id,pregnancy['id'],snapshot['p8_affinity_inheritance'])
            self.db.execute(horse_pregnancies.update().where(horse_pregnancies.c.id == pregnancy["id"],
                horse_pregnancies.c.status == "pending").values(status="delivered", foal_id=horse_id,
                                                               delivered_at=self.now))
            born.append({"id": horse_id, "name": name, "generation": generation, "snapshot": snapshot})
        self.event("horse_delivery", {"foals": [horse["id"] for horse in born]})
        return {"kind": "horse_delivery", "foals": born}

    def _kinship(self, first, second):
        def family(row):
            relatives = {row["id"]}
            frontier = [row]
            for _ in range(2):
                ids = {parent_id for child in frontier for parent_id in
                       (child["mother_id"], child["father_id"]) if parent_id}
                if not ids:
                    break
                relatives.update(ids)
                frontier = self.db.execute(select(horses).where(horses.c.id.in_(ids))).mappings().all()
            return relatives
        return bool(family(first) & family(second))

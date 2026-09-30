"""Transactional P8 horse extensions; callers own the existing game transaction."""
from dataclasses import asdict
import json
import uuid
from sqlalchemy import select
from dzmm_bot.persistence.schema import (horses, horse_affinities, horse_racing_profiles,
    horse_skills, skill_definitions, horse_skill_inheritance)
from dzmm_bot.domain.horse_racing import (AFFINITY_KEYS, SKILL_SOURCES, SkillDefinition,
    SkillRegistry, RacingConfigurationError, generate_affinity, inherit_skills, validate_skills)


class HorseRacingService:
    def __init__(self, db, now, affinity_policy=None, registry=None, level_weights=None, affinity_inheritance_policy=None):
        self.db, self.now = db, now
        self.affinity_policy = affinity_policy
        self.registry = registry if registry is not None else self.load_registry()
        self.level_weights = level_weights
        self.affinity_inheritance_policy = affinity_inheritance_policy

    def affinity_inheritance_snapshot(self, father_id, mother_id, generation, birth_event_id, horse_id):
        from dzmm_bot.domain.affinity_inheritance import inherit_foal_affinities
        return inherit_foal_affinities(self.profile(father_id)['affinities'],
            self.profile(mother_id)['affinities'], birth_event_id, horse_id, father_id, mother_id)

    def apply_affinity_inheritance(self, horse_id, birth_event_id, snapshot):
        from dzmm_bot.persistence.schema import horse_affinity_inheritance
        from .horse_asset_lock import assert_horse_mutable
        assert_horse_mutable(self.db,horse_id)
        body = json.dumps(snapshot,ensure_ascii=False,sort_keys=True)
        row = self.db.execute(select(horse_affinity_inheritance).where(
            horse_affinity_inheritance.c.horse_id == horse_id)).mappings().first()
        if row:
            if row['birth_event_id'] != birth_event_id or row['snapshot_json'] != body:
                raise ValueError('Affinity inheritance snapshot conflict')
            return
        if snapshot['horse_id'] != horse_id or snapshot['birth_event_id'] != birth_event_id:
            raise ValueError('Affinity inheritance identity mismatch')
        horse = self.db.execute(select(horses).where(horses.c.id == horse_id)).mappings().one()
        if horse['father_id'] != snapshot['father_id'] or horse['mother_id'] != snapshot['mother_id']:
            raise ValueError('Affinity inheritance parent mismatch')
        with self.db.begin_nested():
            self.initialize(horse_id,'BIRTH')
            if any(grade != 'U' for values in self.profile(horse_id)['affinities'].values() for grade in values.values()):
                raise ValueError('Child affinity already finalized')
            for category, keys in AFFINITY_KEYS.items():
                if set(snapshot['result'][category]) != set(keys):
                    raise ValueError('Incomplete child affinities')
                for key in keys:
                    grade = snapshot['result'][category][key]
                    if grade not in 'SABCD' or len(grade) != 1:
                        raise ValueError('Invalid child affinity grade')
                    self.db.execute(horse_affinities.update().where(horse_affinities.c.horse_id == horse_id,
                        horse_affinities.c.category == category,horse_affinities.c.key == key).values(
                            grade=grade,source='INHERITANCE',generation_version=snapshot['algorithm_version'],finalized_at=self.now))
            self.db.execute(horse_affinity_inheritance.insert().values(horse_id=horse_id,birth_event_id=birth_event_id,
                snapshot_json=body,algorithm_version=snapshot['algorithm_version'],created_at=self.now))

    def lock_horse(self, horse_id):
        if self.db.dialect.name == 'sqlite':
            # Acquire SQLite's write reservation before reading missing/new extension rows.
            self.db.execute(horses.update().where(horses.c.id == horse_id).values(version=horses.c.version))
        row = self.db.execute(select(horses.c.id).where(horses.c.id == horse_id).with_for_update()).first()
        if not row:
            raise ValueError('Horse does not exist')

    def load_registry(self):
        definitions = []
        for body in self.db.execute(select(skill_definitions.c.definition_json)).scalars():
            data = json.loads(body)
            data['level_scaling'] = {int(k): v for k, v in data['level_scaling'].items()}
            definitions.append(SkillDefinition(**data))
        return SkillRegistry(definitions)

    def register_definitions(self, registry):
        for definition in registry.definitions():
            body = json.dumps(asdict(definition), ensure_ascii=False, sort_keys=True)
            row = self.db.execute(select(skill_definitions).where(
                skill_definitions.c.skill_id == definition.skill_id).with_for_update()).mappings().first()
            if row:
                old=json.loads(row['definition_json'])
                old['level_scaling']={int(k):v for k,v in old['level_scaling'].items()}
                old_body=json.dumps(asdict(SkillDefinition(**old)),ensure_ascii=False,sort_keys=True)
            if row and old_body != body:
                raise RacingConfigurationError('Existing skill definitions are immutable; use a new identity/version')
            if not row:
                self.db.execute(skill_definitions.insert().values(skill_id=definition.skill_id,
                    code=definition.code, version=definition.version, definition_json=body,
                    created_at=definition.created_at, updated_at=definition.updated_at))
        self.registry = registry

    def initialize(self, horse_id, source='LEGACY'):
        self.lock_horse(horse_id)
        current = {(r['category'], r['key']) for r in self.db.execute(select(horse_affinities).where(
            horse_affinities.c.horse_id == horse_id)).mappings()}
        for category, keys in AFFINITY_KEYS.items():
            for key in keys:
                if (category, key) not in current:
                    self.db.execute(horse_affinities.insert().values(horse_id=horse_id, category=category,
                        key=key, grade='U', source=source, generation_version=None, finalized_at=None))
        if not self.db.execute(select(horse_racing_profiles).where(
                horse_racing_profiles.c.horse_id == horse_id)).first():
            self.db.execute(horse_racing_profiles.insert().values(horse_id=horse_id,
                skills_state='UNINITIALIZED', updated_at=self.now))

    def finalize_affinity(self, horse_id, source='LEGACY_INITIALIZER'):
        self.initialize(horse_id, source)
        rows = self.db.execute(select(horse_affinities).where(horse_affinities.c.horse_id == horse_id)).mappings().all()
        if not any(row['grade'] == 'U' for row in rows):
            return self.profile(horse_id)
        from .horse_asset_lock import assert_horse_mutable
        assert_horse_mutable(self.db, horse_id)
        if self.affinity_policy is None:
            raise RacingConfigurationError('Affinity generation is disabled: approved policy missing')
        if any(row['source'] == 'BIRTH' for row in rows):
            raise RacingConfigurationError('Foal affinity inheritance awaits approved P8-9 policy')
        versioned = {row['generation_version'] for row in rows if row['generation_version']}
        if versioned and versioned != {self.affinity_policy.version}:
            raise RacingConfigurationError('Cannot mix affinity generation versions')
        generated = generate_affinity(horse_id, self.affinity_policy)
        for row in rows:
            if row['grade'] == 'U':
                self.db.execute(horse_affinities.update().where(horse_affinities.c.horse_id == horse_id,
                    horse_affinities.c.category == row['category'], horse_affinities.c.key == row['key'],
                    horse_affinities.c.grade == 'U').values(grade=generated[row['category']][row['key']],
                        source=source, generation_version=self.affinity_policy.version, finalized_at=self.now))
        return self.profile(horse_id)

    def profile(self, horse_id):
        affinities = {category: {key: 'U' for key in keys} for category, keys in AFFINITY_KEYS.items()}
        affinity_sources = set()
        for row in self.db.execute(select(horse_affinities).where(horse_affinities.c.horse_id == horse_id)).mappings():
            affinities[row['category']][row['key']] = row['grade']
            affinity_sources.add(row['source'])
        skills = []
        for row in self.db.execute(select(horse_skills).where(horse_skills.c.horse_id == horse_id)
                                   .order_by(horse_skills.c.slot)).mappings():
            try:
                definition = self.registry.get(row['skill_id'])
            except KeyError:
                # Historical immutable identities remain readable without changing their skills.
                body=self.db.execute(select(skill_definitions.c.definition_json).where(
                    skill_definitions.c.skill_id==row['skill_id'])).scalar_one()
                data=json.loads(body); data['level_scaling']={int(k):v for k,v in data['level_scaling'].items()}
                definition=SkillDefinition(**data)
            skills.append({**dict(row), 'display_name': definition.display_name, 'type': definition.type})
        state = self.db.execute(select(horse_racing_profiles.c.skills_state).where(
            horse_racing_profiles.c.horse_id == horse_id)).scalar() or 'UNINITIALIZED'
        inheritance = self.db.execute(select(horse_skill_inheritance.c.snapshot_json).where(
            horse_skill_inheritance.c.horse_id == horse_id)).scalar()
        from dzmm_bot.persistence.schema import horse_affinity_inheritance
        affinity_inheritance = self.db.execute(select(horse_affinity_inheritance.c.snapshot_json).where(
            horse_affinity_inheritance.c.horse_id == horse_id)).scalar()
        recommendation = []
        for category in ('distance', 'running_style'):
            finalized = [(key, grade) for key, grade in affinities[category].items() if grade != 'U']
            recommendation.append(min(finalized, key=lambda pair: ('SABCD'.index(pair[1]),
                AFFINITY_KEYS[category].index(pair[0])))[0] if finalized else None)
        from dzmm_bot.domain.horse_catalog import birth_catalog
        origin=self.db.execute(select(horses.c.birth_traits_json).where(horses.c.id == horse_id)).scalar()
        catalog=birth_catalog({'birth_traits_json':origin})
        if catalog:
            recommendation=[catalog['recommended_distance'],catalog['recommended_style']]
        return {'catalog':catalog, 'affinities': affinities, 'skills': skills, 'skills_state': state,
                'affinity_inheritance_pending': 'BIRTH' in affinity_sources,
                'recommendation': recommendation, 'skill_inheritance': json.loads(inheritance) if inheritance else None,
                'affinity_inheritance': json.loads(affinity_inheritance) if affinity_inheritance else None}

    def initialize_legacy_skills(self, horse_id, skills):
        from .horse_asset_lock import assert_horse_mutable
        assert_horse_mutable(self.db, horse_id)
        """Explicit supplied legacy assignments only; no hidden random backfill."""
        self.initialize(horse_id)
        if self.profile(horse_id)['skills_state'] == 'FINALIZED':
            return self.profile(horse_id)
        validate_skills(skills)
        if len(skills) != 6 or self.profile(horse_id)['skills']:
            raise ValueError('Legacy finalization requires exactly six supplied skills and empty slots')
        with self.db.begin_nested():
            for row in skills:
                result = SkillGrantService(self).grant_skill(horse_id, row['skill_id'], row['level'], 'LEGACY_INITIALIZER')
                if result not in {'GRANTED', 'UPGRADED'}:
                    raise ValueError('Legacy assignment failed')
            self.mark_finalized(horse_id)
        return self.profile(horse_id)

    def initialize_runtime_skills(self, horse_id, pools):
        """Persist exactly once, keeping already finalized and inherited assignments."""
        self.initialize(horse_id)
        profile=self.profile(horse_id)
        if profile['skills_state']=='FINALIZED': return profile
        if profile['skills']:
            raise RacingConfigurationError('Partial legacy skill slots need explicit repair')
        return self.initialize_legacy_skills(horse_id,pools.starter(horse_id,profile['affinities']))

    def mark_finalized(self, horse_id):
        if len(self.profile(horse_id)['skills']) != 6:
            raise ValueError('Six skills required to finalize')
        self.db.execute(horse_racing_profiles.update().where(horse_racing_profiles.c.horse_id == horse_id)
                        .values(skills_state='FINALIZED', updated_at=self.now))

    def inheritance_snapshot(self, father_id, mother_id, birth_event_id, horse_id):
        if self.level_weights is None:
            raise RacingConfigurationError('Skill inheritance disabled: random level weights missing')
        father, mother = self.profile(father_id), self.profile(mother_id)
        if father['skills_state'] != 'FINALIZED' or mother['skills_state'] != 'FINALIZED':
            raise RacingConfigurationError('Parent skills are uninitialized')
        return inherit_skills(father['skills'], mother['skills'], self.registry, self.level_weights,
            birth_event_id, horse_id, father_id, mother_id, self.now)

    def apply_inheritance(self, horse_id, birth_event_id, snapshot):
        self.initialize(horse_id, 'BIRTH')
        prior = self.db.execute(select(horse_skill_inheritance).where(
            horse_skill_inheritance.c.horse_id == horse_id)).mappings().first()
        body = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        if prior:
            if prior['birth_event_id'] != birth_event_id or prior['snapshot_json'] != body:
                raise ValueError('Inheritance snapshot conflict')
            return
        validate_skills(snapshot['final_child_skills'])
        child = self.db.execute(select(horses).where(horses.c.id == horse_id)).mappings().one()
        if child['father_id'] != snapshot['father_id'] or child['mother_id'] != snapshot['mother_id']:
            raise ValueError('Skill inheritance parents conflict')
        if len(snapshot['final_child_skills']) != 6 or self.profile(horse_id)['skills']:
            raise ValueError('Invalid child skills')
        with self.db.begin_nested():
            for skill in snapshot['final_child_skills']:
                result = SkillGrantService(self).grant_skill(horse_id, skill['skill_id'], skill['level'],
                    skill['source'], skill.get('source_parent_id'))
                if result != 'GRANTED':
                    raise ValueError('Failed inherited skill grant')
            self.db.execute(horse_skill_inheritance.insert().values(horse_id=horse_id, birth_event_id=birth_event_id,
                snapshot_json=body, algorithm_version=snapshot['algorithm_version'], created_at=self.now))
            self.mark_finalized(horse_id)


class SkillGrantService:
    """Internal interface; chat callers must enforce permissions before invoking."""
    def __init__(self, racing):
        self.racing = racing

    def grant_skill(self, horse_id, skill_id, level, source, source_parent_id=None, replace_slot=None):
        racing, db = self.racing, self.racing.db
        from .horse_asset_lock import assert_horse_mutable
        assert_horse_mutable(db, horse_id)
        if type(level) is not int or not 1 <= level <= 10 or source not in SKILL_SOURCES:
            raise ValueError('Invalid skill level/source')
        if source in {'INHERITANCE_FATHER','INHERITANCE_MOTHER'} and not source_parent_id:
            raise ValueError('Parent source requires parent id')
        definition = racing.registry.get(skill_id)
        if not definition.enabled:
            raise RacingConfigurationError('Skill is disabled')
        if not db.execute(select(skill_definitions.c.skill_id).where(skill_definitions.c.skill_id == skill_id)).first():
            raise RacingConfigurationError('Register skill definitions before granting')
        racing.initialize(horse_id)
        rows = db.execute(select(horse_skills).where(horse_skills.c.horse_id == horse_id)).mappings().all()
        existing = next((row for row in rows if row['skill_id'] == skill_id), None)
        if existing:
            if existing['level'] == 10:
                return 'MAX_LEVEL'
            if level <= existing['level']:
                return 'REPLACE_REQUIRED'
            db.execute(horse_skills.update().where(horse_skills.c.horse_skill_id == existing['horse_skill_id'])
                .values(level=level, source=source, source_parent_id=source_parent_id, updated_at=racing.now))
            return 'UPGRADED'
        used = {row['slot'] for row in rows}
        if len(rows) == 6 and replace_slot is None:
            return 'SLOT_FULL'
        if replace_slot is not None:
            if type(replace_slot) is not int or replace_slot not in used:
                return 'REPLACE_REQUIRED'
            db.execute(horse_skills.delete().where(horse_skills.c.horse_id == horse_id,
                                                   horse_skills.c.slot == replace_slot))
            slot = replace_slot
        else:
            slot = next(index for index in range(1, 7) if index not in used)
        db.execute(horse_skills.insert().values(horse_skill_id=str(uuid.uuid4()), horse_id=horse_id,
            skill_id=skill_id, slot=slot, level=level, source=source, source_parent_id=source_parent_id,
            created_at=racing.now, updated_at=racing.now))
        return 'GRANTED'

"""P8 versioned affinity and skill contracts. No invented live random policy."""
from dataclasses import dataclass, field
from copy import deepcopy
from decimal import Decimal
import hashlib
import random

AFFINITY_KEYS = {
    'distance': ('short', 'mile', 'medium', 'long'),
    'surface': ('turf', 'dirt'),
    'running_style': ('front', 'stalker', 'mid', 'closer'),
}
GRADES = ('S', 'A', 'B', 'C', 'D')
SKILL_TYPES = {'PASSIVE', 'CONDITIONAL', 'WISDOM_TRIGGER'}
SKILL_SOURCES = {'INHERITANCE_FATHER', 'INHERITANCE_MOTHER', 'INHERITANCE_MERGE',
    'INHERITANCE_RANDOM', 'RACE_REWARD', 'LEGACY_INITIALIZER', 'ADMIN'}
EFFECT_TYPES = {'MODIFY_SPEED', 'MODIFY_ACCELERATION', 'MODIFY_STAMINA_CONSUMPTION',
    'RECOVER_STAMINA', 'MODIFY_POWER', 'MODIFY_WISDOM', 'MODIFY_GRIT',
    'CHANGE_LANE_PREFERENCE', 'REDUCE_BLOCK_PENALTY', 'IMPROVE_OVERTAKE', 'MODIFY_PACE',
    'APPLY_TEMPORARY_MODIFIER', 'APPLY_RACE_MODIFIER'}
ACTIVATION_POLICIES = {'ONCE_PER_RACE', 'ONCE_PER_PHASE', 'COOLDOWN', 'MULTIPLE'}


class RacingConfigurationError(ValueError):
    pass


def stable_rng(*parts):
    reference = hashlib.sha256(':'.join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(reference, 16)), reference


def validated_weights(weights, choices):
    if not weights or set(weights) != set(choices):
        raise RacingConfigurationError('Missing or incomplete random weights')
    values = [Decimal(str(weights[key])) for key in choices]
    if any(not value.is_finite() or value < 0 for value in values) or sum(values) <= 0:
        raise RacingConfigurationError('Invalid random weights')
    return values


def weighted_choice(rng, weights, choices):
    values = validated_weights(weights, choices)
    draw = Decimal(str(rng.random())) * sum(values)
    for key, value in zip(choices, values):
        draw -= value
        if draw < 0:
            return key
    return choices[-1]


@dataclass(frozen=True)
class AffinityPolicy:
    version: str
    weights: dict
    modifiers: dict
    guarantees: tuple = ('distance', 'surface', 'running_style')
    guarantee_weights: dict = field(default_factory=dict)

    def validate(self):
        if not self.version or set(self.modifiers) != set(GRADES):
            raise RacingConfigurationError('Affinity version/modifiers missing')
        if any(not Decimal(str(x)).is_finite() or Decimal(str(x)) <= 0 for x in self.modifiers.values()):
            raise RacingConfigurationError('Invalid affinity modifiers')
        if any(category not in AFFINITY_KEYS for category in self.guarantees):
            raise RacingConfigurationError('Invalid affinity guarantee')
        for category in AFFINITY_KEYS:
            values = validated_weights(self.weights.get(category), GRADES)
            if category in self.guarantees and sum(values[:3]) <= 0:
                raise RacingConfigurationError('Guarantee has no eligible grade')
            if category in self.guarantee_weights:
                validated_weights(self.guarantee_weights[category], GRADES[:3])
        if set(self.guarantee_weights) - set(self.guarantees):
            raise RacingConfigurationError('Guarantee weights require a guaranteed category')


def generate_affinity(horse_id, policy):
    policy.validate()
    rng, _ = stable_rng(horse_id, policy.version)
    result = {}
    for category, keys in AFFINITY_KEYS.items():
        result[category] = {key: weighted_choice(rng, policy.weights[category], GRADES) for key in keys}
        if category in policy.guarantees and not any(grade in GRADES[:3] for grade in result[category].values()):
            eligible = policy.guarantee_weights.get(category) or {key: policy.weights[category][key] for key in GRADES[:3]}
            result[category][rng.choice(keys)] = weighted_choice(rng, eligible, GRADES[:3])
    return result


class AffinityResolver:
    @staticmethod
    def resolve(affinities, distance_type, surface, running_style, modifiers):
        result = {}
        for category, key, label in (('distance', distance_type, 'distance'),
                ('surface', surface, 'surface'), ('running_style', running_style, 'running_style')):
            if key not in AFFINITY_KEYS[category]:
                raise ValueError('Invalid race affinity key')
            grade = affinities.get(category, {}).get(key, 'U')
            if grade == 'U' or grade not in modifiers:
                raise RacingConfigurationError('Horse affinity not finalized')
            value = Decimal(str(modifiers[grade]))
            if not value.is_finite() or value <= 0:
                raise RacingConfigurationError('Invalid affinity modifier')
            result[label + '_grade'] = grade
            result[label + '_modifier'] = value
        return result


@dataclass(frozen=True)
class SkillDefinition:
    skill_id: str
    code: str
    display_name: str
    type: str
    description: str
    trigger_definition: dict
    effect_definition: tuple
    level_scaling: dict
    activation_policy: str
    max_activations: int
    cooldown_checkpoints: int
    tags: tuple = ()
    pools: tuple = ()
    enabled: bool = True
    version: str = ''
    created_at: float = 0
    updated_at: float = 0
    max_attempts: int | None = None
    max_attempts_per_phase: int | None = None
    retry_on_fail: bool = True
    implemented: bool = True
    random_pool_eligible: bool | None = None

    def validate(self):
        if not all((self.skill_id, self.code, self.display_name, self.version)) or self.type not in SKILL_TYPES:
            raise RacingConfigurationError('Invalid skill identity/type')
        for limit in (self.max_attempts, self.max_attempts_per_phase):
            if limit is not None and (type(limit) is not int or limit < 1):
                raise RacingConfigurationError('Invalid skill attempt limit')
        if self.activation_policy not in ACTIVATION_POLICIES or self.max_activations < 1 or self.cooldown_checkpoints < 0:
            raise RacingConfigurationError('Invalid activation policy')
        if self.activation_policy == 'COOLDOWN' and self.cooldown_checkpoints < 1:
            raise RacingConfigurationError('Cooldown needs checkpoints')
        if set(map(int, self.level_scaling)) != set(range(1, 11)):
            raise RacingConfigurationError('Explicit Lv1-Lv10 scaling required')
        if not self.effect_definition or any(effect.get('type') not in EFFECT_TYPES for effect in self.effect_definition):
            raise RacingConfigurationError('Invalid effect definition')


class SkillRegistry:
    def __init__(self, definitions=()):
        self._definitions = {}
        codes = set()
        names = set()
        for definition in definitions:
            definition.validate()
            if definition.skill_id in self._definitions or definition.code in codes or (definition.enabled and (definition.version,definition.display_name) in names):
                raise RacingConfigurationError('Duplicate skill definition')
            self._definitions[definition.skill_id] = deepcopy(definition)
            codes.add(definition.code)
            if definition.enabled:
                names.add((definition.version,definition.display_name))

    def get(self, skill_id):
        return deepcopy(self._definitions[skill_id])

    def resolve_catalog_code(self, code):
        """Map a document/NPC code to one unambiguous immutable runtime identity."""
        if code in self._definitions: return self.get(code)
        matches=[row for row in self._definitions.values() if row.code.removeprefix('R13_').removeprefix('R12_') == code]
        if len(matches) != 1: raise RacingConfigurationError('Ambiguous or missing catalog skill: '+code)
        return deepcopy(matches[0])

    def list_by_tag(self, tag):
        return [self.get(key) for key, value in self._definitions.items() if value.enabled and value.implemented and tag in value.tags]

    def list_by_pool(self, pool):
        return [self.get(key) for key, value in self._definitions.items()
                if value.enabled and value.implemented and value.random_pool_eligible is not False and pool in value.pools]

    def definitions(self):
        return [self.get(key) for key in sorted(self._definitions)]


def validate_skills(skills):
    if len(skills) > 6 or len({skill['skill_id'] for skill in skills}) != len(skills):
        raise ValueError('Skills must be unique and at most six')
    if any(type(skill['level']) is not int or not 1 <= skill['level'] <= 10 for skill in skills):
        raise ValueError('Skill level must be 1-10')
    if any('slot' in skill for skill in skills):
        slots = [skill.get('slot') for skill in skills]
        if len(set(slots)) != len(slots) or any(type(slot) is not int or not 1 <= slot <= 6 for slot in slots):
            raise ValueError('Skill slots must be unique and 1-6')


def inherit_skills(father, mother, registry, level_weights, birth_event_id, horse_id,
                   father_id, mother_id, created_at, version='skill-inheritance-v1'):
    father, mother = deepcopy(father), deepcopy(mother)
    validate_skills(father)
    validate_skills(mother)
    if len(father) != 6 or len(mother) != 6:
        raise RacingConfigurationError('Parents need six finalized skills')
    validated_weights(level_weights, (1, 2, 3, 4, 5))
    rng, seed_reference = stable_rng(birth_event_id, horse_id, version)
    selected_f = rng.sample(sorted(father, key=lambda x: x['skill_id']), 3)
    selected_m = rng.sample(sorted(mother, key=lambda x: x['skill_id']), 3)
    merged, duplicates = {}, []
    for selection, source, parent in ((selected_f, 'INHERITANCE_FATHER', father_id),
                                      (selected_m, 'INHERITANCE_MOTHER', mother_id)):
        for skill in selection:
            registry.get(skill['skill_id'])
            existing = merged.get(skill['skill_id'])
            if existing:
                level = min(max(existing['level'], skill['level']) + 1, 10)
                duplicates.append({'skill_id': skill['skill_id'], 'parent_levels': [existing['level'], skill['level']], 'level': level})
                existing.update(level=level, source='INHERITANCE_MERGE', source_parent_id=None)
            else:
                merged[skill['skill_id']] = {'skill_id': skill['skill_id'], 'level': skill['level'],
                                            'source': source, 'source_parent_id': parent}
    available = sorted((skill.skill_id for skill in registry.list_by_pool('inheritance_random_pool')
                        if skill.skill_id not in merged))
    needed = 6 - len(merged)
    if len(available) < needed:
        raise RacingConfigurationError('Random fill pool is too small')
    fills = []
    for skill_id in rng.sample(available, needed):
        row = {'skill_id': skill_id, 'level': weighted_choice(rng, level_weights, (1, 2, 3, 4, 5)),
               'source': 'INHERITANCE_RANDOM', 'source_parent_id': None}
        fills.append(row)
        merged[skill_id] = row
    final = [dict(row, slot=index) for index, row in enumerate(merged.values(), 1)]
    return {'father_skill_snapshot': father, 'mother_skill_snapshot': mother,
        'father_selected_3': selected_f, 'mother_selected_3': selected_m,
        'duplicate_groups': duplicates, 'merged_levels': {r['skill_id']: r['level'] for r in duplicates},
        'random_fill_skills': fills, 'random_fill_levels': {r['skill_id']: r['level'] for r in fills},
        'final_child_skills': final, 'father_id': father_id, 'mother_id': mother_id,
        'algorithm_version': version, 'rng_seed_reference': seed_reference, 'created_at': created_at}

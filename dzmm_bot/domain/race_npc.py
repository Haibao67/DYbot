"""P8-10 deterministic NPC templates, disabled without approved template configuration."""
from dataclasses import dataclass
from copy import deepcopy
from .horse_racing import AFFINITY_KEYS, GRADES, RacingConfigurationError, stable_rng, validate_skills, AffinityResolver


@dataclass(frozen=True)
class NpcHorseTemplate:
    template_id: str
    display_name_pool: tuple
    stat_ranges: dict
    growth_profile: dict
    affinities: dict
    running_style: str
    skills: tuple
    difficulty: str
    enabled: bool
    skill_pool: tuple = ()
    skill_level_range: tuple | None = None

    def validate(self):
        if not self.template_id or not self.display_name_pool or self.difficulty not in ('EASY','NORMAL','HARD','ELITE'):
            raise RacingConfigurationError('Incomplete NPC template')
        for trait in ('speed','stamina','power','wisdom','grit'):
            low,high = self.stat_ranges[trait]
            if type(low) is not int or type(high) is not int or not 0 <= low <= high:
                raise RacingConfigurationError('Invalid NPC stat range')
            if self.growth_profile[trait] not in GRADES:
                raise RacingConfigurationError('NPC growth profile missing')
        for category, keys in AFFINITY_KEYS.items():
            if set(self.affinities[category]) != set(keys) or any(self.affinities[category][key] not in GRADES for key in keys):
                raise RacingConfigurationError('NPC affinity profile missing')
        if self.running_style not in AFFINITY_KEYS['running_style']:
            raise RacingConfigurationError('NPC style and six explicit skills required')
        if self.skill_pool:
            if len(set(self.skill_pool)) < 6 or len(set(self.skill_pool)) != len(self.skill_pool) or not self.skill_level_range:
                raise RacingConfigurationError('NPC skill pool requires six unique definitions and explicit level range')
            low,high=self.skill_level_range
            if type(low) is not int or type(high) is not int or not 1 <= low <= high <= 10:
                raise RacingConfigurationError('Invalid NPC skill level range')
        elif len(self.skills) == 6:
            validate_skills(self.skills)
        else:
            raise RacingConfigurationError('NPC six skills missing')


class NpcFactory:
    def __init__(self, *, version, templates, difficulty_pool, affinity_modifiers, registry, affinity_resolver=None):
        self.version,self.templates = version,tuple(templates)
        self.difficulty_pool,self.affinity_modifiers,self.registry = tuple(difficulty_pool),affinity_modifiers,registry
        self.affinity_resolver=affinity_resolver

    def __call__(self, definition, count):
        templates=sorted((t for t in self.templates if t.enabled and t.difficulty in self.difficulty_pool),key=lambda t:t.template_id)
        if not self.version or not templates:
            raise RacingConfigurationError('NPC fill disabled: approved templates missing')
        if type(count) is not int or count < 0 or count > definition['max_horses']:
            raise RacingConfigurationError('Invalid NPC fill count')
        result=[]
        for index in range(count):
            rng,ref=stable_rng(definition['rng_seed'],definition['race_id'],self.version,index)
            template=templates[rng.randrange(len(templates))]
            template.validate()
            skills = deepcopy(template.skills)
            if template.skill_pool:
                skills = [{'skill_id':skill_id,'level':rng.randint(*template.skill_level_range),'slot':slot}
                    for slot,skill_id in enumerate(rng.sample(sorted(template.skill_pool),6),1)]
            for skill in skills:
                resolved=self.registry.resolve_catalog_code(skill['skill_id'])
                skill['skill_id']=resolved.skill_id
                if not resolved.enabled:
                    raise RacingConfigurationError('NPC skill disabled')
            result.append({'horse_id':f"npc:{definition['race_id']}:{index}",'is_npc':True,
                'name':rng.choice(template.display_name_pool),'template_id':template.template_id,
                'difficulty':template.difficulty,'template_version':self.version,'rng_reference':ref,
                **{key:rng.randint(*template.stat_ranges[key]) for key in template.stat_ranges},
                **{'growth_'+key:grade for key,grade in template.growth_profile.items()},
                'running_style':template.running_style,'skills':skills,
                'affinities':deepcopy(template.affinities),
                'affinity':(self.affinity_resolver(template.affinities,definition['distance_type'],definition['surface'],template.running_style)
                    if self.affinity_resolver else AffinityResolver.resolve(template.affinities,definition['distance_type'],
                    definition['surface'],template.running_style,self.affinity_modifiers))})
        return result

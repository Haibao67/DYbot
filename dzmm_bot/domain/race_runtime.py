"""Approved R2 runtime: one versioned assembly for registration, NPCs and execution."""
from dataclasses import dataclass, asdict
from copy import deepcopy
from datetime import datetime, time
from zoneinfo import ZoneInfo
from .horse_racing import RacingConfigurationError, AFFINITY_KEYS, stable_rng, weighted_choice
from .race_setup_r1 import npc_templates, NPC_VERSION
from .race_rules_r1 import generation_policy, race_affinity
from .race_rules_r11 import WISDOM_DIFFICULTY
from .skill_execution import EFFECT_MECHANICS

VERSIONS = {'race_rule':'1.3', 'race_engine':'1.3', 'skill_registry':'1.3',
            'npc_template':'horse_catalog_v1', 'affinity_generation':'affinity_generation_v1'}
INITIALIZER_VERSION = 'skill_initializer_v1'


def analyze_performance(ranking):
    events=ranking['key_events']
    counts={kind:sum(e['type']==kind for e in events) for kind in
        ('OVERTAKE_ATTEMPT','OVERTAKE_SUCCESS','BLOCKED','BLOCK_AVOIDED','SKILL_ACTIVATED','SKILL_FAILED','STAMINA_LOW')}
    reasons=[]
    if counts['BLOCKED']: reasons.append(f"发生 {counts['BLOCKED']} 次堵塞")
    if counts['STAMINA_LOW']: reasons.append('比赛中触及体力警戒线')
    if counts['SKILL_ACTIVATED']: reasons.append(f"成功发动 {counts['SKILL_ACTIVATED']} 次技能")
    return {**ranking,'counts':counts,'analysis':reasons or ['暂无额外关键事件']}


class SkillPoolRegistry:
    def __init__(self, registry):
        eligible = [d for d in registry.definitions() if d.implemented and d.enabled
                    and d.random_pool_eligible is not False and 'inheritance_random_pool' in d.pools]
        self.pools = {'inheritance_random_pool': tuple(d.skill_id for d in eligible)}
        self.pools['starter.general'] = self.pools['inheritance_random_pool']
        for key, kind in [('passive','PASSIVE'),('conditional','CONDITIONAL'),('wisdom','WISDOM_TRIGGER')]:
            self.pools['starter.'+key] = tuple(d.skill_id for d in eligible if d.type == kind)
        for key, code in [('front','P08'),('pace','P09'),('late','P10'),('closer','P11'),('turf','P12')]:
            skill_id=registry.resolve_catalog_code(code).skill_id
            self.pools['starter.'+key] = ((skill_id,) if skill_id in self.pools['starter.general']
                                        else self.pools['starter.general'])
        # There is no exclusive dirt skill in V1; use the approved general pool.
        self.pools['starter.dirt'] = self.pools['starter.general']

    def get(self, key):
        if not self.pools.get(key):
            raise RacingConfigurationError('Missing/empty skill pool: '+key)
        return self.pools[key]

    def starter(self, horse_id, affinities):
        rng, _ = stable_rng(horse_id, INITIALIZER_VERSION)
        def highest(category):
            values=affinities[category]
            if any(grade not in 'SABCD' or len(grade) != 1 for grade in values.values()):
                raise RacingConfigurationError('Affinity must be finalized before skill initialization')
            best=min('SABCD'.index(grade) for grade in values.values())
            return rng.choice(sorted(key for key, grade in values.items() if 'SABCD'.index(grade)==best))
        style={'front':'front','stalker':'pace','mid':'late','closer':'closer'}[highest('running_style')]
        keys=['starter.'+style, 'starter.'+highest('surface'), 'starter.passive',
              'starter.conditional', 'starter.wisdom', 'starter.general']
        chosen=[]
        for key in keys:
            candidates=sorted(set(self.get(key))-set(chosen))
            if not candidates:
                candidates=sorted(set(self.get('starter.general'))-set(chosen))
            if not candidates:
                raise RacingConfigurationError('Not enough unique starter skills')
            chosen.append(rng.choice(candidates))
        from .skill_catalog import RANDOM_SKILL_LEVEL_WEIGHTS
        return [{'skill_id':code,'level':weighted_choice(rng,RANDOM_SKILL_LEVEL_WEIGHTS,(1,2,3,4,5)),
                 'slot':slot} for slot,code in enumerate(chosen,1)]


class NpcTemplateRegistry:
    version='1'
    def __init__(self):
        self.templates={t.template_id: t for t in npc_templates()}
    def enabled(self, difficulty='NORMAL'):
        return tuple(t for t in self.templates.values() if t.enabled and t.difficulty==difficulty)


class NpcHorseFactory:
    def __init__(self, registry, pools, templates, catalog=True):
        self.registry,self.pools,self.templates=registry,pools,templates
        self.catalog=catalog

    def fill(self, definition, participants):
        from .horse_catalog import catalog_snapshot, VERSION
        from .horse_racing import generate_affinity
        count=max(0,definition['min_horses']-len(participants))
        result=[]
        for slot in range(count):
            horse_id=f'npc:{definition["race_id"]}:{slot}'
            _,seed=stable_rng(definition['rng_seed'],horse_id,VERSION)
            snapshot=catalog_snapshot(seed)
            origin=snapshot['catalog']
            affinities=generate_affinity(horse_id,generation_policy())
            style=origin['recommended_style']
            result.append({'horse_id':horse_id,'owner_id':None,'player_id':None,'is_npc':True,
                'npc_template_id':origin['template_id'],'template_id':origin['template_id'],
                'template_version':VERSION,'rng_reference':seed,'name':origin['name'],
                'sex':snapshot['sex'],'generation':0,'condition':'NORMAL',
                'lane_initial_state':{'lane':slot%3},**snapshot['traits'],
                **{'growth_'+key:value for key,value in snapshot['growth'].items()},
                'catalog':origin,'birth_traits_json':__import__('json').dumps(snapshot,ensure_ascii=False),
                'running_style':style,'chosen_running_style':style,
                'skills':self.pools.starter(horse_id,affinities),'affinities':affinities,
                'affinity':race_affinity(affinities,definition['distance_type'],definition['surface'],style)})
        return result

    def _fill_legacy(self, definition, participants):
        count=max(0,definition['min_horses']-len(participants))
        if count > len(self.templates.enabled()):
            raise RacingConfigurationError('Not enough approved NPC templates')
        remaining=sorted(self.templates.enabled(),key=lambda t:t.template_id)
        rng,_=stable_rng(definition['race_id'],'npc-template-order','1')
        styles={p['running_style'] for p in participants}
        chosen=[]
        for style in AFFINITY_KEYS['running_style']:
            if len(chosen)>=count: break
            matches=[t for t in remaining if t.running_style==style]
            if style not in styles and matches:
                template=rng.choice(matches); chosen.append(template); remaining.remove(template)
        rng.shuffle(remaining); chosen += remaining[:count-len(chosen)]
        result=[]
        for slot,template in enumerate(chosen):
            rng,seed=stable_rng(definition['race_id'],slot,self.templates.version)
            pool=[self.registry.resolve_catalog_code(code).skill_id for code in template.skill_pool]
            pool=sorted(set(pool))
            selected=rng.sample(pool,min(6,len(pool)))
            if len(selected)<6:
                selected+=rng.sample(sorted(set(self.pools.get('starter.general'))-set(selected)),6-len(selected))
            skills=[{'skill_id':code,'level':weighted_choice(rng,{1:35,2:30,3:25,4:10},(1,2,3,4)),
                     'slot':i} for i,code in enumerate(selected,1)]
            result.append({'horse_id':f"npc:{definition['race_id']}:{slot}", 'owner_id':None,
                'player_id':None,'is_npc':True,'npc_template_id':template.template_id,'template_id':template.template_id,
                'template_version':'1','rng_reference':seed,'name':rng.choice(template.display_name_pool),
                'sex':None,'generation':0,'condition':'NORMAL','lane_initial_state':{'lane':slot%3},
                **{key:rng.randint(*bounds) for key,bounds in template.stat_ranges.items()},
                **{'growth_'+key:grade for key,grade in template.growth_profile.items()},
                'running_style':template.running_style,'chosen_running_style':template.running_style,
                'skills':skills,'affinities':deepcopy(template.affinities),
                'affinity':race_affinity(template.affinities,definition['distance_type'],definition['surface'],template.running_style)})
        return result

    def __call__(self, definition, count):
        # Legacy interface: formal R2 lock always supplies actual participants to fill().
        return self.fill({**definition,'min_horses':count},[])


@dataclass
class RaceRuntime:
    versions: dict
    engine: object
    skill_registry: object
    skill_pools: SkillPoolRegistry
    npc_templates: NpcTemplateRegistry
    npc_factory: NpcHorseFactory
    affinity_policy: object

    @property
    def components(self):
        from . import race_rules_r1 as r1, race_rules_r11 as r11, race_rules_r12 as r12, race_rules_r13 as r13
        return {'RaceEngine':self.engine,'AffinityResolver':race_affinity,'StartErrorResolver':r12.resolve_start,
            'StaminaResolver':r1.stamina_factor,'PaceResolver':r11.pace_state,
            'PositionResolver':r11.position_adjustment,'LaneResolver':r11.lane_probability,
            'BlockResolver':r11.congestion,'OvertakeResolver':r11.overtake_probability,
            'SkillRegistry':self.skill_registry,'SkillPoolRegistry':self.skill_pools,
            'SkillTriggerResolver':self.engine.skill_runtime,'WisdomTriggerResolver':self.engine.skill_runtime.wisdom_resolver,
            'SkillEffectExecutor':self.engine.skill_runtime.executor,'NpcTemplateRegistry':self.npc_templates,
            'NpcHorseFactory':self.npc_factory,'RaceEventRecorder':self.engine.event,
            'PostRaceAnalysisService':analyze_performance}

    def validate_snapshot(self, context):
        from hashlib import sha256
        if context.rule_metadata.get('versions') != self.versions or context.simulation_version!=self.engine.policy.version:
            raise RacingConfigurationError('Snapshot runtime versions mismatch')
        if context.rng_seed!=sha256((context.race_id+':1.3').encode()).hexdigest():
            raise RacingConfigurationError('Snapshot race seed mismatch')
        participants=context.participants
        if not 8<=len(participants)<=20 or len({h['horse_id'] for h in participants})!=len(participants):
            raise RacingConfigurationError('Incomplete snapshot participants')
        if len({h.get('player_id') for h in participants if not h['is_npc']})<2:
            raise RacingConfigurationError('Snapshot needs two real players')
        frozen=context.rule_metadata.get('skill_definitions',{})
        for horse in participants:
            if len(horse['skills'])!=6 or len({s['skill_id'] for s in horse['skills']})!=6:
                raise RacingConfigurationError('Snapshot needs six unique skills')
            if horse['is_npc']:
                from .horse_catalog import load_catalog
                catalog_ids={item['id'] for item in load_catalog()}
                if horse.get('npc_template_id') not in catalog_ids | set(self.npc_templates.templates):
                    raise RacingConfigurationError('Snapshot NPC template missing')
            for skill in horse['skills']:
                definition=self.skill_registry.get(skill['skill_id'])
                if not definition.implemented or not definition.enabled or skill['skill_id'] not in frozen:
                    raise RacingConfigurationError('Snapshot skill definition missing or disabled')


class RaceRuntimeValidator:
    @staticmethod
    def validate(runtime):
        definitions=runtime.skill_registry.definitions()
        if len({d.skill_id for d in definitions}) != len(definitions):
            raise RacingConfigurationError('Duplicate skill ID')
        for definition in definitions:
            definition.validate()
            if not definition.implemented: continue
            code=definition.code.removeprefix('R13_')
            if definition.type=='WISDOM_TRIGGER' and code not in WISDOM_DIFFICULTY:
                raise RacingConfigurationError('Missing TriggerDifficulty: '+code)
            for effect in definition.effect_definition:
                if effect['type'] != 'RECOVER_STAMINA' and effect['type'] not in EFFECT_MECHANICS and not effect.get('stat_or_mechanic'):
                    raise RacingConfigurationError('Missing effect executor: '+code)
        for pool in runtime.skill_pools.pools.values():
            for code in pool:
                row=runtime.skill_registry.get(code)
                if not row.enabled or not row.implemented or row.random_pool_eligible is False:
                    raise RacingConfigurationError('Invalid skill in pool: '+code)
        if len(runtime.npc_templates.enabled()) != 6:
            raise RacingConfigurationError('Missing NORMAL NPC template')
        for template in runtime.npc_templates.enabled():
            template.validate()
            for code in template.skill_pool:
                row=runtime.skill_registry.resolve_catalog_code(code)
                if not row.enabled or not row.implemented:
                    raise RacingConfigurationError('Invalid NPC skill: '+code)
        return runtime


class RaceRuntimeFactory:
    @staticmethod
    def build(versions=None, *, strategy_config=None):
        versions=dict(VERSIONS if versions is None else versions)
        legacy_versions={**VERSIONS,'npc_template':'1'}
        if versions not in (VERSIONS,legacy_versions):
            raise RacingConfigurationError('Unsupported runtime versions: '+str(versions))
        from .race_rules_r13 import build_engine
        engine=build_engine(strategy_config=strategy_config)
        registry=engine.skill_runtime.registry
        pools=SkillPoolRegistry(registry); templates=NpcTemplateRegistry()
        return RaceRuntimeValidator.validate(RaceRuntime(versions,engine,registry,pools,templates,
            NpcHorseFactory(registry,pools,templates,catalog=versions['npc_template']=='horse_catalog_v1'),generation_policy()))


@dataclass(frozen=True)
class DailyRaceTemplate:
    template_id: str='ruihe_medium_open_v1'
    name: str='澄露杯·中距公开赛'
    enabled: bool=True
    timezone: str='Asia/Shanghai'
    registration_open_time: str='12:00'
    registration_close_time: str='19:45'
    start_time: str='20:00'

    def definition(self, date):
        from hashlib import sha256
        zone=ZoneInfo(self.timezone)
        stamps=[datetime.combine(date,time.fromisoformat(value),zone).timestamp() for value in
                (self.registration_open_time,self.registration_close_time,self.start_time)]
        if not stamps[0]<stamps[1]<=stamps[2]: raise RacingConfigurationError('Invalid daily race times')
        race_id=self.template_id+':'+date.isoformat()
        return {'race_id':race_id,'template_id':self.template_id,'race_date':date.isoformat(),'name':self.name,
            'distance':2000,'distance_type':'medium','surface':'turf','track_condition':'良','weather':'晴',
            'min_horses':8,'max_horses':20,'min_real_players':2,'max_entries_per_player':1,
            'registration_open_at':stamps[0],'registration_close_at':stamps[1],'starts_at':stamps[2],
            'entry_fee':'50','prizes':['200','100','50'],'refund_ratio':'.90','full_refund_ratio':'1',
            'season_id':'UNSEASONED','rng_seed':sha256((race_id+':1.3').encode()).hexdigest(),
            'simulation_version':'race_engine_v1.3','npc_fill':True,'versions':dict(VERSIONS),
            'race_rule_version':'1.3','skill_registry_version':'1.3','npc_template_version':'1',
            'affinity_version':'affinity_generation_v1','timezone':self.timezone}

"""Approved V1 anchors plus explicitly incomplete drafts. No import-time DB writes."""
import json
from pathlib import Path
from decimal import Decimal
from copy import deepcopy
from .horse_racing import SkillDefinition, SkillRegistry, RacingConfigurationError

CATALOG = json.loads(Path(__file__).with_name('skill_catalog_v1.json').read_text(encoding='utf-8'))
RANDOM_SKILL_LEVEL_WEIGHTS = {int(key): value for key, value in CATALOG['random_skill_level_weights'].items()}


def list_skill_drafts(first_batch_only=False):
    definitions={row.code.removeprefix('R13_').removeprefix('R12_'):row for row in execution_registry().definitions()}
    rows=deepcopy([row for row in CATALOG['skills'] if not first_batch_only or row['first_batch']])
    for row in rows:
        row['runtime_enabled']=row['skill_id'] in definitions and definitions[row['skill_id']].enabled
        row['pending_rules']=[]
        row['execution_skill_id']=definitions[row['skill_id']].skill_id if row['runtime_enabled'] else None
    return rows


def linear_curve(anchors):
    """The V1 library explicitly specifies linear interpolation at Lv2-4 and Lv6-9."""
    if set(anchors) != {1, 5, 10}:
        raise RacingConfigurationError('Lv1/Lv5/Lv10 anchors required')
    result = {}
    for level in range(1, 11):
        low, high = (1, 5) if level <= 5 else (5, 10)
        result[level] = str(Decimal(str(anchors[low])) + (Decimal(str(anchors[high])) - Decimal(str(anchors[low])))
                            * Decimal(level - low) / Decimal(high - low))
    return result


def first_batch_registry():
    """Only the four fully specified execution definitions. No automatic grant/enable."""
    definitions = []
    specs = (
        ('P13', 'PASSIVE', {}, 'MODIFY_STAMINA_CONSUMPTION', {1: '-.01', 5: '-.025', 10: '-.04'}, {}),
        ('C01', 'CONDITIONAL', {'phase': 'START', 'hook': 'on_checkpoint', 'flags': {'severe_start_error': False}},
         'MODIFY_ACCELERATION', {1: '.04', 5: '.08', 10: '.13'}, {'expires_phase': 'START'}),
        ('C06', 'CONDITIONAL', {'phase': 'MID_RACE', 'hook': 'on_checkpoint', 'consecutive_clean_checkpoints': 2},
         'RECOVER_STAMINA', {1: '.015', 5: '.035', 10: '.055'}, {}),
        ('C14', 'CONDITIONAL', {'phase': 'FINAL_STRAIGHT', 'hook': 'on_final_straight', 'stamina_above': '.10'},
         'MODIFY_SPEED', {1: '.03', 5: '.06', 10: '.10'}, {'expires_phase': 'FINAL_STRAIGHT'}),
    )
    drafts = {row['skill_id']: row for row in CATALOG['skills']}
    for code, kind, trigger, effect, anchors, lifetime in specs:
        row = drafts[code]
        definitions.append(SkillDefinition(skill_id=code, code=code, display_name=row['display_name'], type=kind,
            description=row['source_rules'], trigger_definition=trigger,
            effect_definition=({'type': effect, **lifetime},), level_scaling=linear_curve(anchors),
            activation_policy='ONCE_PER_RACE', max_activations=1, cooldown_checkpoints=0,
            tags=tuple(row['tags']), pools=('inheritance_random_pool',), enabled=True, version=CATALOG['version']))
    return SkillRegistry(definitions)


FIRST_BATCH_PENDING = {
    'P08': ('front-half phase/checkpoint mapping',),
    'C07': ('activation policy/max activations',),
    'W01': ('wisdom probability', 'activation policy/max activations', 'start-risk mechanic'),
    'W05': ('wisdom probability', 'activation policy/max activations', 'route decision mechanic'),
    'W06': ('wisdom probability', 'activation policy/max activations', 'route decision mechanic'),
    'W07': ('wisdom probability', 'activation policy/max activations', 'overtake mechanic'),
    'W10': ('wisdom probability', 'activation policy/max activations', 'distance safety line', 'speed cost curve'),
    'W15': ('wisdom probability', 'activation policy/max activations', 'duration'),
}


def r12_registry():
    """Versioned immutable IDs/codes; existing persisted definitions stay intact."""
    drafts = {row['skill_id']: row for row in CATALOG['skills']}
    specs = (
        ('C01', 'CONDITIONAL', 'START', 'on_checkpoint', 'severe_start_error', False, 'MODIFY_ACCELERATION', {1:'.04',5:'.08',10:'.13'}, 1),
        ('C03', 'CONDITIONAL', 'MID_RACE', 'on_checkpoint', 'r12_C03', True, 'MODIFY_SPEED', {1:'.02',5:'.045',10:'.07'}, 1),
        ('C04', 'CONDITIONAL', 'MID_RACE', 'on_checkpoint', 'r12_C04', True, 'MODIFY_SPEED', {1:'.02',5:'.04',10:'.065'}, 1),
        ('W01', 'WISDOM_TRIGGER', 'START', 'on_phase_start', 'r12_W01', True, 'APPLY_TEMPORARY_MODIFIER', {1:'-.30',5:'-.55',10:'-.85'}, 1),
        ('W08', 'WISDOM_TRIGGER', 'FINAL_CORNER', 'on_checkpoint', 'r12_W08', True, 'MODIFY_STAMINA_CONSUMPTION', {1:'-.03',5:'-.07',10:'-.12'}, 1),
        ('W09', 'WISDOM_TRIGGER', 'FINAL_CORNER', 'on_checkpoint', 'r12_W09', True, 'MODIFY_ACCELERATION', {1:'.03',5:'.07',10:'.11'}, 1),
        ('W10', 'WISDOM_TRIGGER', 'MID_RACE', 'on_checkpoint', 'r12_W10', True, 'MODIFY_STAMINA_CONSUMPTION', {1:'-.06',5:'-.12',10:'-.20'}, 2),
    )
    definitions=[]
    for code, kind, phase, hook, flag, expected, effect, anchors, maximum in specs:
        lifetime = {'expires_phase':'START'} if code in ('C01','W01') else {'duration_checkpoints':1}
        effects = [{'type':effect, **lifetime}]
        scaling = linear_curve(anchors)
        if code == 'W01': effects[0]['stat_or_mechanic']='start_penalty'
        if code == 'W10':
            speed=linear_curve({1:'-.03',5:'-.02',10:'-.01'})
            scaling={level:{'cost':scaling[level], 'speed':speed[level]} for level in scaling}
            effects[0]['scaling_key']='cost'
            effects.append({'type':'MODIFY_SPEED', 'scaling_key':'speed', **lifetime})
        row=drafts[code]
        definitions.append(SkillDefinition(skill_id='R12_'+code, code='R12_'+code, display_name=row['display_name'],
            type=kind, description=row['source_rules'], trigger_definition={'phase':phase,'hook':hook,'flags':{flag:expected}},
            effect_definition=tuple(effects), level_scaling=scaling, activation_policy='ONCE_PER_RACE' if maximum == 1 else 'MULTIPLE',
            max_activations=maximum,cooldown_checkpoints=0,tags=tuple(row['tags']),pools=('r12_conditions',),enabled=True,version='1.2'))
    return SkillRegistry(definitions)


# No guessed defaults: unresolved entries never enter enabled/random pools.
LEGACY_R12_PENDING = {
    'P08': ('前半程范围、位置控制收益如何进入公式',),
    'P09': ('前集团范围、位置控制收益公式',),
    'P10': ('前中盘/后程边界',), 'P11': ('前半程/最终阶段边界',),
    'P14': ('出弯阶段范围、力量/加速度修正应用对象',),
    'C02': ('次数、持续、位置推进公式',), 'C05': ('中盘后半边界、发动次数',),
    'C08': ('出弯checkpoint、次数、持续',), 'C09': ('次数、持续、外道范围',),
    'C10': ('首次解堵事件接入',), 'C11': ('后半程边界、成功超车事件接入',),
    'C12': ('次数、持续、被超事件接入',), 'C13': ('中游/后方百分位、次数、持续',),
    'C16': ('最后100m速度效果在终点hook的消费顺序',),
    'W02': ('偏离阈值、修正目标、次数、持续',),
    'W03': ('主动降速幅度、次数、持续',),
    'W04': ('下checkpoint堵塞预测公式、路线收益、次数、持续',),
    'W05': ('前方密度阈值、次数、持续',),
    'W06': ('即时换道效果、次数、持续',),
    'W07': ('后半程边界、次数、持续',),
    'W11': ('身边竞争距离范围、路线判断收益、次数',),
    'W12': ('决策收益模型、次数、持续',),
    'W13': ('路线综合损失模型、次数、持续',),
    'W14': ('内切/外绕/保持期望收益模型、次数、持续',),
    'W15': ('速度效率修正对象、次数、持续',),
    'W16': ('路线抓地模型、次数、持续',),
}


def _r12_execution_registry():
    """One definition per name: preserve old P13/C06/C14, use R1.2 replacements.

    Explicit registration is required; calling this does not mutate player skills.
    """
    definitions=[r for r in first_batch_registry().definitions() if r.code != 'C01']
    definitions.extend(r12_registry().definitions())
    drafts={r['skill_id']:r for r in CATALOG['skills']}
    def add(code,trigger,effects,curves):
        scaling={level:{key:linear_curve(anchors)[level] for key,anchors in curves.items()} for level in range(1,11)}
        row=drafts[code]
        definitions.append(SkillDefinition(skill_id=code,code=code,display_name=row['display_name'],type=row['type'],
            description=row['source_rules'],trigger_definition=trigger,effect_definition=tuple(effects),level_scaling=scaling,
            activation_policy='ONCE_PER_RACE',max_activations=1,cooldown_checkpoints=0,tags=tuple(row['tags']),
            pools=('inheritance_random_pool',) if row['inheritance_random_pool'] else (),enabled=True,version='execution_v1.2'))
    def effect(mechanic,key,**extra):
        return {'type':'APPLY_RACE_MODIFIER','stat_or_mechanic':mechanic,'scaling_key':key,**extra}
    for code,distance,anchors in [('P01','short',('.015','.030','.050')),
            ('P03','medium',('-.020','-.040','-.065')),('P04','long',('-.025','-.050','-.080'))]:
        add(code,{'distance_type':distance},[effect('speed' if code == 'P01' else 'stamina_consumption','value')],
            {'value':dict(zip((1,5,10),anchors))})
    add('P02',{'distance_type':'mile'},[effect('speed','speed'),effect('stamina_consumption','cost')],
        {'speed':{1:'.01',5:'.025',10:'.04'},'cost':{1:'-.01',5:'-.025',10:'-.04'}})
    add('P05',{'surface':'turf'},[effect('acceleration','value')],{'value':{1:'.015',5:'.035',10:'.055'}})
    add('P06',{'surface':'dirt'},[effect('acceleration','accel'),effect('overtake','overtake')],
        {'accel':{1:'.015',5:'.035',10:'.055'},'overtake':{1:'.01',5:'.03',10:'.05'}})
    add('P07',{'track_condition':['重','不良']},[effect('track_extra_cost','value')],{'value':{1:'-.04',5:'-.08',10:'-.12'}})
    add('P12',{},[effect('stamina_consumption','value',conditions={'phases':['CORNER_BATTLE','FINAL_CORNER']})],
        {'value':{1:'-.03',5:'-.06',10:'-.10'}})
    add('P15',{},[effect('fatigue_penalty','value',conditions={'stamina_below':'.20'})],
        {'value':{1:'-.05',5:'-.10',10:'-.16'}})
    add('P16',{},[effect('grit','value',conditions={'head_to_head':True})],{'value':{1:'.03',5:'.07',10:'.12'}})
    # C07's activation policy is explicitly supplied in the library YAML example.
    add('C07',{'phase':'CORNER_BATTLE','hook':'on_phase_start','position_not_first':True},
        [effect('acceleration','value',duration_checkpoints=1)],{'value':{1:'.04',5:'.08',10:'.12'}})
    add('C15',{'phase':'FINAL_STRAIGHT','hook':'on_checkpoint','stamina_at_most':'.10'},
        [effect('fatigue_penalty','value',expires_phase='FINAL_STRAIGHT')],{'value':{1:'-.08',5:'-.16',10:'-.25'}})
    # R1.2 ordinary skills remain eligible under the same source library pool rule.
    from dataclasses import replace
    definitions=[replace(row,pools=('inheritance_random_pool',)) if row.code.startswith('R12_') else row for row in definitions]
    return SkillRegistry(definitions)


def execution_registry(*, version='1.3', strategy_config=None):
    if version == '1.2': return _r12_execution_registry()
    if version != '1.3': raise RacingConfigurationError('Unknown execution registry version')
    from .skill_registry_r13 import registry
    return registry(strategy_costs=strategy_config)


SKILL_PENDING = {}

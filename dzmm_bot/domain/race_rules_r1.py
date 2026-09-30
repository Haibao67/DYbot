"""Frozen P8-R1 V1 rules. These factories never create races or write databases."""
from copy import deepcopy
from decimal import Decimal, localcontext
from .horse_racing import AffinityPolicy, GRADES, AFFINITY_KEYS, RacingConfigurationError
from .affinity_inheritance import AffinityInheritancePolicy

VERSION = 'p8-r1-v1'
GENERATION_VERSION = 'affinity_generation_v1'
INHERITANCE_VERSION = 'affinity_inheritance_v1'

GENERATION_WEIGHTS = {'distance':dict(zip(GRADES,(2,10,28,40,20))),
    'running_style':dict(zip(GRADES,(2,10,28,40,20))),
    'surface':dict(zip(GRADES,(2,12,36,35,15)))}
GUARANTEE_WEIGHTS = {'distance':dict(zip(GRADES[:3],(1,13,86))),
    'running_style':dict(zip(GRADES[:3],(1,13,86))),
    'surface':dict(zip(GRADES[:3],(1,17,82)))}

# Categories act on individual mechanisms, never on a hidden total power score.
DISTANCE_FACTORS = {g:dict(zip(('speed','cost'),v)) for g,v in zip(GRADES,
    (('1.040','.94'),('1.020','.97'),('1','1'),('.970','1.06'),('.920','1.14')))}
STYLE_FACTORS = {g:dict(zip(('position','cost','lane_bonus','wisdom_bonus'),v)) for g,v in zip(GRADES,
    (('1.10','.98','.06','.05'),('1.05','.99','.03','.03'),('1','1','0','0'),
     ('.94','1.04','-.04','-.03'),('.85','1.09','-.08','-.06')))}
SURFACE_FACTORS = {g:dict(zip(('speed','acceleration','cost'),v)) for g,v in zip(GRADES,
    (('1.025','1.050','.98'),('1.0125','1.025','.99'),('1','1','1'),
     ('.975','.960','1.04'),('.930','.900','1.10')))}
TRACK_FACTORS = {g:dict(zip(('speed','acceleration','cost'),v)) for g,v in
    zip(('良','稍重','重','不良'),(('1','1','1'),('.995','.985','1.03'),('.985','.960','1.08'),('.970','.920','1.15')))}

PAIR_ROWS = {'SS':(65,30,5,0,0),'SA':(35,50,13,2,0),'SB':(20,45,30,5,0),
    'SC':(10,30,45,13,2),'SD':(5,20,40,30,5),'AA':(15,60,22,3,0),
    'AB':(8,35,50,7,0),'AC':(3,20,50,24,3),'AD':(1,10,40,40,9),
    'BB':(3,17,60,18,2),'BC':(1,8,45,40,6),'BD':(0,3,30,50,17),
    'CC':(0,2,18,65,15),'CD':(0,1,10,55,34),'DD':(0,0,5,30,65)}
MUTATION_SHIFTS = {-2:Decimal('.5'),-1:Decimal('4.5'),0:Decimal(90),1:Decimal('4.5'),2:Decimal('.5')}

STYLE_SPEED = {'early':('1.025','1.010','.985','.970'),'middle':('1.015','1.005','.995','.985'),
    'corner':('1.005','1.005','1.010','1.015'),'final_corner':('1','1.010','1.025','1.035'),
    'straight':('.995','1.015','1.035','1.050')}
STYLE_COST = {'early':('1.15','1.07','.94','.90'),'middle':('1.08','1.02','.96','.93'),
    'corner':('1.08','1.03','1.02','1.03'),'final_corner':('1.05','1.05','1.08','1.12'),
    'straight':('1.04','1.06','1.12','1.18')}
STYLE_TARGETS = {'front':('0','.15'),'stalker':('.15','.40'),'mid':('.40','.70'),'closer':('.70','1')}
PACE_COST = {'SLOW':'.92','NORMAL':'1','FAST':'1.08','OVERPACE':'1.18'}
CHECKPOINTS = (('START',0),('OPENING_POSITION',200),('MID_RACE',500),('MID_RACE',900),
    ('CORNER_BATTLE',1300),('FINAL_CORNER',1600),('FINAL_STRAIGHT',1800),('FINISH',1900),('FINISH',2000))
GEOMETRY = {'lanes':3,'soft_block':Decimal('4.0'),'hard_block':Decimal('2.2'),
    'hard_speed_advantage':Decimal('.25'),'hard_speed_factor':Decimal('.995'),
    'safe_ahead':Decimal('2'),'safe_behind':Decimal('1.5'),'overtake_speed_advantage':Decimal('.15'),
    'head_to_head':Decimal('1.5'),'photo_finish':Decimal('.25')}
FIXED_RACE = {'name':'澄露杯·中距公开赛','distance':2000,'distance_type':'medium','surface':'turf',
    'track_condition':'良','weather':'晴','open_time':'12:00','close_time':'19:45','start_time':'20:00',
    'max_horses':10,'target_horses':8,'min_real_players':2,'max_entries_per_player':1,
    'entry_fee':'50','prizes':('200','100','50'),'refund_ratio':'.90','full_refund_ratio':'1'}
FORMAL_SKILLS = ('P03','P05','P06','P08','P09','P10','P11','P13','P14','P15',
    'C01','C03','C04','C05','C06','C07','C09','C10','C13','C14',
    'W01','W02','W03','W05','W06','W07','W08','W09','W10','W13')


def generation_policy():
    # Legacy AffinityPolicy modifiers are only an interface adapter; actual races
    # must use resolve_factors() to retain speed/cost/acceleration distinctions.
    return AffinityPolicy(GENERATION_VERSION,deepcopy(GENERATION_WEIGHTS),
        {g:DISTANCE_FACTORS[g]['speed'] for g in GRADES},guarantee_weights=deepcopy(GUARANTEE_WEIGHTS))


def inheritance_policy():
    pairs={f:{m:dict(zip(GRADES,PAIR_ROWS[''.join(sorted((f,m),key=GRADES.index))]))
        for m in GRADES} for f in GRADES}
    mutation={g:{target:Decimal(0) for target in GRADES} for g in GRADES}
    for grade in GRADES:
        for shift,weight in MUTATION_SHIFTS.items():
            target=GRADES[max(0,min(4,GRADES.index(grade)-shift))]
            mutation[grade][target]+=weight
    return AffinityInheritancePolicy(INHERITANCE_VERSION,{category:deepcopy(pairs) for category in AFFINITY_KEYS},
        Decimal(1),mutation,deepcopy(MUTATION_SHIFTS))


def resolve_factors(affinities,distance,surface,style):
    result={}
    for category,key,table in (('distance',distance,DISTANCE_FACTORS),('surface',surface,SURFACE_FACTORS),
                              ('running_style',style,STYLE_FACTORS)):
        if key not in AFFINITY_KEYS[category]:
            raise RacingConfigurationError('Invalid R1 affinity key')
        grade=affinities.get(category,{}).get(key,'U')
        if grade not in GRADES:
            raise RacingConfigurationError('Horse affinity not finalized')
        result[category]={'grade':grade,**{k:Decimal(v) for k,v in table[grade].items()}}
    return result


def race_affinity(affinities,distance,surface,style):
    factors=resolve_factors(affinities,distance,surface,style)
    return {'distance_grade':factors['distance']['grade'],'distance_modifier':factors['distance']['speed'],
        'surface_grade':factors['surface']['grade'],'surface_modifier':factors['surface']['speed'],
        'running_style_grade':factors['running_style']['grade'],'running_style_modifier':factors['running_style']['position'],
        'mechanism_factors':factors}


def stat_norm(value):
    value=Decimal(str(value))
    if not value.is_finite() or not 0 <= value <= 1000:
        raise RacingConfigurationError('R1 race stats must be within 0..1000')
    return value/1000


def power(value,exponent):
    with localcontext() as context:
        context.prec=28
        return Decimal(value) ** Decimal(exponent)


def base_speed(speed):
    return Decimal('12.5')+Decimal('5.5')*power(stat_norm(speed),'.72')


def base_acceleration(strength):
    return Decimal('1.4')+Decimal('2.8')*power(stat_norm(strength),'.75')


def max_stamina(stamina):
    return Decimal(60)+Decimal(140)*stat_norm(stamina)


def race_cost(distance):
    if type(distance) is not int or distance <= 0:
        raise RacingConfigurationError('Race distance must be positive integer metres')
    return Decimal(50)+Decimal('.04')*distance


def wisdom_cost(wisdom):
    return 1-Decimal('.08')*power(stat_norm(wisdom),'.8')


def stamina_factor(ratio,grit):
    ratio=Decimal(str(ratio))
    if not ratio.is_finite() or not 0 <= ratio <= 1:
        raise RacingConfigurationError('Stamina ratio must be 0..1')
    guard=Decimal('.10')+Decimal('.40')*power(stat_norm(grit),'.8')
    penalty=Decimal(0) if ratio >= Decimal('.20') else Decimal('.35')*(1-ratio/Decimal('.20'))
    return 1-penalty*(1-guard)


def segment_time(distance,current_speed,target_speed,acceleration):
    distance,current,target,accel=map(lambda value:Decimal(str(value)),(distance,current_speed,target_speed,acceleration))
    if any(not v.is_finite() for v in (distance,current,target,accel)) or distance < 0 or current < 0 or target <= 0 or accel <= 0:
        raise RacingConfigurationError('Invalid segment physics inputs')
    if distance == 0:
        return Decimal(0),current
    if current >= target:
        return distance/target,target
    d_acc=(target*target-current*current)/(2*accel)
    if d_acc < distance:
        return (target-current)/accel+(distance-d_acc)/target,target
    final=(current*current+2*accel*distance).sqrt()
    return (final-current)/accel,final


def wisdom_probability(wisdom,level,style_grade,difficulty):
    if type(level) is not int or not 1 <= level <= 10 or style_grade not in GRADES or difficulty not in ('EASY','NORMAL','HARD'):
        raise RacingConfigurationError('Wisdom trigger requires approved grade/level/difficulty')
    value=(Decimal('.10')+Decimal('.55')*power(stat_norm(wisdom),'.85')+Decimal('.02')*(level-1)
        +Decimal(STYLE_FACTORS[style_grade]['wisdom_bonus'])
        -{'EASY':Decimal(0),'NORMAL':Decimal('.05'),'HARD':Decimal('.10')}[difficulty])
    return min(Decimal('.92'),max(Decimal('.15'),value))

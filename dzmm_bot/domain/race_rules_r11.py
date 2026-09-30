"""Approved P8-R1.1 tactical equations; no database or platform side effects."""
from decimal import Decimal
from .race_rules_r1 import stat_norm, power, STYLE_FACTORS
from .horse_racing import RacingConfigurationError

VERSION = 'race_engine_v1.1'
TARGET_CENTER = {'front':'.075','stalker':'.275','mid':'.550','closer':'.850'}
PHASE_STYLE_BONUS = {'early':('.02','.01','-.02','-.03'),'middle':('0','.01','.01','0'),
    'corner':('-.01','0','.02','.03'),'final_corner':('-.02','0','.03','.04'),
    'straight':('-.02','.01','.04','.05')}
AFFINITY_OVERTAKE = {'S':'.025','A':'.0125','B':'0','C':'-.02','D':'-.04'}
LANE_BONUS = {0:'-.02',1:'0',2:'.03'}
TRACK_PENALTY = {'良':'0','稍重':'.015','重':'.04','不良':'.07'}
WISDOM_DIFFICULTY = {'W01':'NORMAL','W02':'EASY','W03':'EASY','W04':'NORMAL','W05':'NORMAL',
    'W06':'HARD','W07':'NORMAL','W08':'NORMAL','W09':'HARD','W10':'EASY','W11':'NORMAL',
    'W12':'HARD','W13':'HARD','W14':'HARD','W15':'NORMAL','W16':'NORMAL'}


def d(value):
    value=Decimal(str(value))
    if not value.is_finite():
        raise RacingConfigurationError('Nonfinite tactical value')
    return value


def clamp(value,low,high):
    return min(d(high),max(d(low),d(value)))


def position_adjustment(rank,count,wisdom,style,grade):
    if type(rank) is not int or type(count) is not int or not 1 <= rank <= count or style not in TARGET_CENTER or grade not in STYLE_FACTORS:
        raise RacingConfigurationError('Invalid positioning inputs')
    # A single runner has no relative rank; treat it as the leading runner.
    percentile=Decimal(0) if count == 1 else Decimal(rank-1)/Decimal(count-1)
    error=percentile-d(TARGET_CENTER[style])
    control=(d('.55')+d('.45')*stat_norm(wisdom))*d(STYLE_FACTORS[grade]['position'])
    return {'percentile':percentile,'error':error,'control':control,
            'adjustment':clamp(d('.06')*error*control,'-.020','.025')}


def pace_state(adjustment):
    ratio=1+d(adjustment)
    return 'SLOW' if ratio < d('.985') else 'NORMAL' if ratio <= d('1.010') else 'FAST' if ratio <= d('1.020') else 'OVERPACE'


def congestion(state,lane,states):
    if lane not in LANE_BONUS:
        raise RacingConfigurationError('Invalid lane')
    distances=[abs(other.progress-state.progress) for other in states.values()
               if other.horse_id != state.horse_id and other.lane == lane]
    close=sum(distance <= 4 for distance in distances)
    medium=sum(4 < distance <= 8 for distance in distances)
    return min(d('.20'),d('.05')*close+d('.02')*medium)


def lane_safe(state,lane,states):
    if lane not in LANE_BONUS:
        raise RacingConfigurationError('Invalid lane')
    for other in states.values():
        if other.horse_id == state.horse_id or other.lane != lane:
            continue
        delta=other.progress-state.progress
        if -d('1.5') <= delta <= d('2'):
            return False
    return True


def lane_probability(wisdom,grade,penalty,skill_bonus=0):
    if grade not in STYLE_FACTORS:
        raise RacingConfigurationError('Unknown style affinity')
    return clamp(d('.30')+d('.50')*power(stat_norm(wisdom),'.8')
        +d(STYLE_FACTORS[grade]['lane_bonus'])+d(skill_bonus)-d(penalty),'.10','.95')


def overtake_probability(self_horse,target_horse,self_speed,target_speed,phase,lane,track,skill_bonus=0):
    style=self_horse['running_style']; grade=self_horse['affinity']['running_style_grade']
    if style not in TARGET_CENTER or phase not in PHASE_STYLE_BONUS or lane not in LANE_BONUS or track not in TRACK_PENALTY or grade not in AFFINITY_OVERTAKE:
        raise RacingConfigurationError('Invalid overtake conditions')
    index=tuple(TARGET_CENTER).index(style)
    power_diff=clamp(stat_norm(self_horse['power'])-stat_norm(target_horse['power']),-1,1)
    wisdom_diff=clamp(stat_norm(self_horse['wisdom'])-stat_norm(target_horse['wisdom']),-1,1)
    speed_advantage=clamp((d(self_speed)-d(target_speed))/d('1.5'),-1,1)
    style_bonus=clamp(d(PHASE_STYLE_BONUS[phase][index])+d(AFFINITY_OVERTAKE[grade]),'-.06','.08')
    skill_bonus=clamp(skill_bonus,'-.15','.20')
    probability=clamp(d('.40')+d('.18')*power_diff+d('.10')*wisdom_diff+d('.20')*speed_advantage
        +style_bonus+skill_bonus+d(LANE_BONUS[lane])-d(TRACK_PENALTY[track]),'.08','.92')
    return {'probability':probability,'power_diff':power_diff,'wisdom_diff':wisdom_diff,
        'speed_advantage':speed_advantage,'style_bonus':style_bonus,'skill_bonus':skill_bonus}


def wisdom_trigger(context,horse,state,skill,level):
    from .race_rules_r1 import wisdom_probability
    code = skill.code.removeprefix('R12_').removeprefix('R13_')
    if code not in WISDOM_DIFFICULTY:
        raise RacingConfigurationError('Wisdom skill difficulty not registered')
    return wisdom_probability(horse['wisdom'],level,horse['affinity']['running_style_grade'],WISDOM_DIFFICULTY[code])


def build_engine(registry):
    from .race_engine_r11 import R11RaceEngine
    from .skill_execution import SkillRuntime, WisdomTriggerResolver
    return R11RaceEngine(SkillRuntime(registry,WisdomTriggerResolver(wisdom_trigger)))

"""Approved R1.3 distance boundaries and auditable route decisions."""
from dataclasses import dataclass, asdict
from decimal import Decimal
from .race_rules_r11 import d, clamp, congestion, lane_safe, overtake_probability, position_adjustment, pace_state
from .race_rules_r1 import STYLE_TARGETS, STYLE_COST, TRACK_FACTORS, race_cost, wisdom_cost
from .skill_execution import ModifierStore
from .horse_racing import RacingConfigurationError

VERSION='race_engine_v1.3'
W14_CONFIG={'version':'w14_v1','costs':{'inner':'.005','hold':'0','outer':'.012'},
    'stamina_ev_scale':'4','tie_epsilon':'.005','tie_priority':['hold','inner','outer']}

def w14_config(config=None):
    from copy import deepcopy
    chosen=deepcopy(W14_CONFIG if config is None else config)
    # This patch freezes approved numbers; incompatible snapshots must use another version.
    if chosen != W14_CONFIG:
        raise RacingConfigurationError('W14 configuration differs from approved w14_v1')
    return chosen

def w14_decision(route,bonus,config=None):
    config=w14_config(config)
    current=route.current_lane
    candidates={'hold':current}
    if current-1 in route.candidate_lanes: candidates['inner']=current-1
    if current+1 in route.candidate_lanes: candidates['outer']=current+1
    base={}
    for name,lane in candidates.items():
        penalty=-route.components[lane]['congestion']
        cost=d(config['costs'][name])*d(config['stamina_ev_scale'])
        if name == 'inner': value=d('.040')-penalty*d('1.20')-cost
        elif name == 'outer': value=-d('.030')+route.probabilities[lane]*d('.08')+d('.060')-penalty*d('.70')-cost
        else: value=-penalty-cost
        base[name]=value
    def select(values):
        best=max(values.values())
        tied=[name for name,value in values.items() if best-value <= d(config['tie_epsilon'])]
        return min(tied,key=lambda name:config['tie_priority'].index(name))
    best=select(base)
    adjusted=dict(base); adjusted[best]+=d(bonus)
    selected=select(adjusted)
    return {'base_ev':base,'adjusted_ev':adjusted,'base_best_strategy':best,
        'chosen_strategy':selected,'chosen_lane':candidates[selected],
        'stamina_cost_ratio':d(config['costs'][selected]),'configuration_version':config['version']}

def execute_w14(state,decision,view,already_executed=False):
    # Fallback never chooses the second-best lane and never retries wisdom.
    if already_executed: return None
    selected=decision['chosen_strategy']; lane=decision['chosen_lane']
    if lane != state.lane and not lane_safe(state,lane,view):
        selected='hold'; lane=state.lane
    cost=state.stamina_max*d(W14_CONFIG['costs'][selected])
    old=state.lane; before=state.stamina_remaining
    state.lane=lane
    state.stamina_remaining=max(d(0),before-cost)
    if old != lane: state.blocked=False
    result={**decision,'executed_strategy':selected,'executed_lane':lane,'old_lane':old,
        'fallback':selected != decision['chosen_strategy'],'extra_stamina_cost':cost,
        'actual_stamina_deducted':before-state.stamina_remaining}
    state.mechanic_flags['w14_executed']=True
    state.mechanic_flags['w14_decision']=result
    return result
# Segments consume the phase at their completed START distance, not future end marker.
CHECKPOINTS=(('START',0),('OPENING_POSITION',200),('OPENING_POSITION',500),('MID_RACE',900),
    ('MID_RACE',1300),('CORNER_BATTLE',1600),('FINAL_CORNER',1800),('FINAL_STRAIGHT',1900),('FINISH',2000))
DISTANCE_SCORE=(d('.030'),d('.015'),d(0))
STYLE_SCORE={'front':('.015','0','-.010'),'stalker':('.010','.005','0'),
             'mid':('0','.005','.010'),'closer':('-.005','.005','.015')}
GRIP={'稍重':('.98','1','1.01'),'重':('.94','.98','1'),'不良':('.90','.95','.98')}


@dataclass(frozen=True)
class RouteDecisionContext:
    current_lane:int
    candidate_lanes:tuple
    components:dict
    risks:dict
    losses:dict
    probabilities:dict


def front(state,lane,view,limit=None):
    values=[other for other in view.values() if other.horse_id != state.horse_id and other.lane == lane
        and (other.progress > state.progress or (other.progress == state.progress and other.position < state.position))]
    if limit is not None: values=[other for other in values if other.progress-state.progress <= d(limit)]
    return sorted(values,key=lambda other:(other.progress-state.progress,other.position,other.horse_id))


def safe_space(state,lane,view,ahead,behind):
    return all(other.horse_id == state.horse_id or other.lane != lane or
        not -d(behind) <= other.progress-state.progress <= d(ahead) for other in view.values())


def route_context(context,horse,state,view,horses,targets,*,include_skill_bonus=False):
    candidates=tuple(lane for lane in range(3) if lane == state.lane or lane_safe(view[state.horse_id],lane,view))
    components={}; risks={}; losses={}; probabilities={}
    from .race_engine_r11 import PHASE_BUCKET
    for lane in candidates:
        ahead=front(view[state.horse_id],lane,view)
        close=sum(0 <= other.progress-view[state.horse_id].progress <= 4 for other in ahead)
        medium=sum(4 < other.progress-view[state.horse_id].progress <= 8 for other in ahead)
        slower=bool(ahead and targets[ahead[0].horse_id] < targets[state.horse_id]-d('.15'))
        risk=min(d(1),d('.15')*close+d('.08')*medium+d('.20')*int(slower))
        probability=d(0)
        if ahead and targets[state.horse_id] > targets[ahead[0].horse_id]+d('.15'):
            probability=overtake_probability(horse,horses[ahead[0].horse_id],targets[state.horse_id],targets[ahead[0].horse_id],
                PHASE_BUCKET[context.phase],lane,context.track_condition,
                ModifierStore.apply(state,'overtake',1,context.phase)-1 if include_skill_bonus else 0)['probability']
        penalty=congestion(view[state.horse_id],lane,view)*ModifierStore.apply(state,'congestion_penalty',1,context.phase)
        surface=d(GRIP[context.track_condition][lane])-1 if context.surface == 'dirt' and context.track_condition in GRIP else d(0)
        values={'distance':DISTANCE_SCORE[lane],'congestion':-penalty,'overtake':probability*d('.08'),
            'surface':surface,'style':d(STYLE_SCORE[horse['running_style']][lane])}
        components[lane]=values; risks[lane]=risk; probabilities[lane]=probability
        # R1.1 close/medium bands; loss counts neighbours in that candidate lane.
        distances=[abs(other.progress-view[state.horse_id].progress) for other in view.values()
            if other.horse_id != state.horse_id and other.lane == lane]
        losses[lane]=d('.015')*lane+penalty+d('.10')*sum(x <= 4 for x in distances)+d('.05')*sum(4 < x <= 8 for x in distances)-probability*d('.08')
    return RouteDecisionContext(state.lane,candidates,components,risks,losses,probabilities)


def checkpoint_flags(context,horse,state,view,start,end,exit_corner):
    ratio=max(d(0),view[state.horse_id].progress)/context.distance
    percentile=d(state.position-1)/d(len(view)-1) if len(view)>1 else d(0)
    stamina=state.stamina_remaining/state.stamina_max
    maximum=d(STYLE_TARGETS[horse['running_style']][1])
    competitors=sum(other.horse_id != state.horse_id and
        ((other.lane == state.lane and abs(other.progress-view[state.horse_id].progress) <= 5) or
         (abs(other.lane-state.lane) == 1 and abs(other.progress-view[state.horse_id].progress) <= 3)) for other in view.values())
    recent=state.mechanic_flags.get('last_overtaken_checkpoint',-100)
    return dict(checkpoint_index=context.checkpoint_index,progress_ratio=ratio,position_percentile=percentile,
        segment_start_ratio=d(start)/context.distance,segment_end_ratio=d(end)/context.distance,exit_corner=exit_corner,
        r13_C02=context.phase == 'OPENING_POSITION' and horse['running_style'] in ('front','stalker') and percentile > maximum,
        r13_C05=context.phase == 'MID_RACE' and d('.45') <= ratio < d('.65') and stamina >= d('.55'),
        r13_C08=exit_corner, r13_C12=context.phase not in ('START','OPENING_POSITION') and 0 <= context.checkpoint_index-recent <= 1,
        r13_C13=context.phase == 'FINAL_CORNER' and percentile >= d('.40'),
        r13_W02=state.pace_state != 'NORMAL',r13_W03=state.pace_state == 'OVERPACE',
        r13_W11=context.phase == 'MID_RACE' and competitors >= 2,
        r13_W15=context.phase == 'FINAL_STRAIGHT' and d('.05') <= stamina <= d('.25'))


def route_flags(context,horse,state,view,route):
    key=state.horse_id; original=view[key]
    outside=state.lane+1
    density=len(front(original,state.lane,view,6)) >= 2 or congestion(original,state.lane,view) >= d('.10')
    return dict(r13_W04=0 in route.candidate_lanes and state.lane != 0 and route.risks[0] < d('.35'),
        r13_W05=density and outside < 3 and safe_space(original,outside,view,'2.5','2.0'),
        r13_W06=state.blocked and any(lane != state.lane for lane in route.candidate_lanes),
        r13_W12=0 <= context.checkpoint_index-state.mechanic_flags.get('last_overtaken_checkpoint',-100) <= 1,
        r13_W13=context.phase in ('CORNER_BATTLE','FINAL_CORNER') and len(route.candidate_lanes)>=2,
        r13_W14=context.phase == 'FINAL_CORNER' and len(route.candidate_lanes)>=2,
        r13_W16=context.surface == 'dirt' and context.track_condition in GRIP)


def choose_route(context,state,route,strategy_config=None):
    phase=context.phase
    score={lane:sum(values.values()) for lane,values in route.components.items()}
    inner=ModifierStore.apply(state,'route_inner',0,phase)
    if 0 in score: score[0]+=inner
    grip=ModifierStore.apply(state,'route_grip',0,phase)
    if grip:
        best=max(route.candidate_lanes,key=lambda lane:(route.components[lane]['surface'],-lane)); score[best]+=grip
    reduction=ModifierStore.apply(state,'route_loss_reduction',0,phase)
    if reduction:
        losses=dict(route.losses); best=min(losses,key=lambda lane:(losses[lane],lane))
        losses[best]*=1-reduction
        lane=min(losses,key=lambda lane:(losses[lane],lane))
    else: lane=max(score,key=lambda lane:(score[lane],-lane))
    strategy=ModifierStore.apply(state,'route_strategy',0,phase)
    if strategy:
        decision=w14_decision(route,strategy,strategy_config)
        state.mechanic_flags['w14_pending_decision']=decision
        lane=decision['chosen_lane']
    preempt=ModifierStore.apply(state,'preemptive_lane',0,phase)
    if not strategy and preempt and state.lane+1 in route.candidate_lanes: lane=state.lane+1
    return lane,{'scores':score,'losses':route.losses,'hard_block_risk':route.risks,'preemptive_bonus':preempt}


def adjust_position(position,state,phase):
    bonus=clamp(ModifierStore.apply(state,'position_control',0,phase),'-.20','.25')
    control=position['control']*(1+bonus)
    adjustment=clamp(d('.06')*position['error']*control,'-.020','.025')
    if adjustment > 0: adjustment*=ModifierStore.apply(state,'position_push',1,phase)
    correction=ModifierStore.apply(state,'pace_correction',0,phase)
    target=clamp(adjustment,'-.015','.010')
    adjustment+=(target-adjustment)*correction
    if ModifierStore.apply(state,'overpace_reduction',0,phase): adjustment=d('.015')
    return adjustment


def counter_attack(probability,penalty,bonus):
    p=d(probability); partial=min(d('.10'),1-p)
    extra=p*d('.005')+partial*d('.010')+(1-p-partial)*d('.015')
    ev=p*d('.60')-extra*d('.25')-d(penalty)*d('.15')
    return {'base_ev':ev,'adjusted_ev':ev+d(bonus),'favorable':ev+d(bonus)>=d('.20'),'expected_extra_stamina_ratio':extra}


def projected_cost(context,horse,factors,index,start):
    from .race_engine_r11 import PHASE_BUCKET
    total=d(0); style=('front','stalker','mid','closer').index(horse['running_style'])
    for phase,end in CHECKPOINTS[index:]:
        total+=race_cost(context.distance)*(end-start)/context.distance*d(STYLE_COST[PHASE_BUCKET[phase]][style]); start=end
    return total*factors['distance']['cost']*factors['surface']['cost']*d(TRACK_FACTORS[context.track_condition]['cost'])*wisdom_cost(horse['wisdom'])


def build_engine(*, strategy_config=None):
    from .skill_registry_r13 import registry
    from .race_engine_r13 import R13RaceEngine
    from .skill_execution import SkillRuntime,WisdomTriggerResolver
    from .race_rules_r11 import wisdom_trigger
    strategy_config=w14_config(strategy_config)
    return R13RaceEngine(SkillRuntime(registry(strategy_costs=strategy_config),WisdomTriggerResolver(wisdom_trigger)),strategy_config=strategy_config)

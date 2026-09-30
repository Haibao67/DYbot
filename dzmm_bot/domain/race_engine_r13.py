"""P8-R1.3 distance-based, checkpoint-synchronous simulation.

Every runner completes the same segment, accumulating its own time. At the next
checkpoint, time gaps are converted to longitudinal metres at the leader's speed.
Routing reads a single immutable checkpoint view. No wall-clock waits or DB access.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
import json
from .horse_racing import stable_rng, validate_skills, RacingConfigurationError, SkillDefinition
from .race_engine import RaceContext, RaceEngine, HorseRaceState
from .skill_execution import ModifierStore
from .race_rules_r1 import (resolve_factors,base_speed,base_acceleration,max_stamina,race_cost,
    wisdom_cost,stamina_factor,segment_time,stat_norm,STYLE_SPEED,STYLE_COST,PACE_COST,
    TRACK_FACTORS,GEOMETRY)
from .race_rules_r11 import (d,clamp,position_adjustment,pace_state,congestion,
    lane_safe,lane_probability,overtake_probability)

from . import race_rules_r13 as r13
VERSION=r13.VERSION
CHECKPOINTS=r13.CHECKPOINTS

PHASE_BUCKET={'START':'early','OPENING_POSITION':'early','MID_RACE':'middle',
    'CORNER_BATTLE':'corner','FINAL_CORNER':'final_corner','FINAL_STRAIGHT':'straight','FINISH':'straight'}


@dataclass(frozen=True)
class R13Policy:
    version: str = VERSION

    def validate(self,distance):
        if self.version != VERSION or distance != 2000:
            raise RacingConfigurationError('R1.1 supports only the approved 2000m race')


class R13RaceEngine(RaceEngine):
    def __init__(self,skill_runtime,strategy_config=None):
        super().__init__(R13Policy(),skill_runtime=skill_runtime)
        self.strategy_config=r13.w14_config(strategy_config)

    def simulate(self,context):
        self.policy.validate(context.distance)
        if context.simulation_version != self.policy.version or context.distance_type != 'medium' or context.surface != 'turf' or context.track_condition != '良':
            raise RacingConfigurationError('R1.1 fixed race conditions required')
        context=deepcopy(context)
        frozen=context.rule_metadata.get('skill_definitions',{})
        for code,definition in frozen.items():
            current=asdict(self.skill_runtime.registry.get(code))
            definition={**definition,'level_scaling':{int(k):v for k,v in definition['level_scaling'].items()}}
            canonical=lambda value:json.dumps(value,sort_keys=True,default=str,ensure_ascii=False)
            if canonical(current) != canonical(asdict(SkillDefinition(**definition))):
                raise RacingConfigurationError('Frozen skill definition differs from runtime: '+code)
        if context.rng is None:
            context.rng,_=stable_rng(context.rng_seed,self.policy.version)
        participants=sorted(context.participants,key=lambda h:h['horse_id'])
        if not participants or len({h['horse_id'] for h in participants}) != len(participants):
            raise RacingConfigurationError('Unique runners required')
        horses={h['horse_id']:h for h in participants}
        states,times,factors={}, {}, {}
        grid=list(horses); context.rng.shuffle(grid)
        for h in participants:
            for trait in ('speed','stamina','power','wisdom','grit'):
                stat_norm(h[trait])
            validate_skills(h.get('skills',[]))
            h['affinity']={**h.get('affinity',{}),
                'running_style_grade':h['affinities']['running_style'][h['running_style']]}
            factors[h['horse_id']]=resolve_factors(h['affinities'],'medium','turf',h['running_style'])
            pool=max_stamina(h['stamina']); rank=grid.index(h['horse_id'])
            states[h['horse_id']]=HorseRaceState(h['horse_id'],position=rank+1,lane=rank%3,
                stamina_max=pool,stamina_remaining=pool)
            times[h['horse_id']]=Decimal(0)
        context.phase='START'; context.checkpoint_index=0
        if self.skill_runtime:
            for h in participants:
                self.skill_runtime.initialize(context,h,states[h['horse_id']],states)
        from . import race_rules_r12 as r12
        use_r12=True
        checkpoints=[]; start_accel={}; duel={}; previous_marker=0
        if context.rule_metadata.get('route_strategy_config',self.strategy_config) != self.strategy_config:
            raise RacingConfigurationError('Frozen route strategy configuration mismatch')
        for index,(phase,marker) in enumerate(CHECKPOINTS):
            context.phase,context.checkpoint_index=phase,index
            bucket=PHASE_BUCKET[phase]; segment=marker-previous_marker
            view=deepcopy(states); targets={}; accelerations={}; effective={}
            for h in participants:
                key=h['horse_id']; state=states[key]; state.race_phase=phase
                state.mechanic_flags['checkpoint_index']=index
                state.mechanic_flags['progress_ratio']=max(d(0),view[key].progress)/context.distance
                state.mechanic_flags['position_percentile']=d(state.position-1)/d(len(states)-1) if len(states)>1 else d(0)
                self.emit('before_phase',context,h,state,view)
                effective[key]={**h,**{trait:clamp(ModifierStore.apply(state,trait,h[trait],phase),0,1000)
                    for trait in ('power','wisdom','grit')}}
                eh=effective[key]; f=factors[key]
                style_index=('front','stalker','mid','closer').index(h['running_style'])
                position=position_adjustment(state.position,len(states),eh['wisdom'],h['running_style'],f['running_style']['grade'])
                state.pace_state=pace_state(position['adjustment'])
                state.mechanic_flags.update(position_error=position['error'],position_adjustment=position['adjustment'],
                    overtaking=False,block_avoided=False)
                if use_r12:
                    if index == 0:
                        state.mechanic_flags.update(r12.resolve_start(h,context.rng))
                    projected=r13.projected_cost(context,h,f,index,previous_marker)
                    state.mechanic_flags.update(r12.condition_flags(context,h,state,view,projected))
                exit_corner=index>0 and CHECKPOINTS[index-1][0] in ('CORNER_BATTLE','FINAL_CORNER') and phase not in ('CORNER_BATTLE','FINAL_CORNER')
                state.mechanic_flags.update(r13.checkpoint_flags(context,h,state,view,previous_marker,marker,exit_corner))
                fatigue=stamina_factor(state.stamina_remaining/state.stamina_max,eh['grit'])
                fatigue=1-(1-fatigue)*ModifierStore.apply(state,'fatigue_penalty',1,phase)
                neutral=(base_speed(h['speed'])*f['distance']['speed']*f['surface']['speed']
                    *d(STYLE_SPEED[bucket][style_index])*d(TRACK_FACTORS[context.track_condition]['speed'])
                    *fatigue)
                tactical=neutral*(1+position['adjustment'])
                state.mechanic_flags.update(neutral_target_speed=neutral,tactical_target_speed=tactical,base_pace=state.pace_state,
                    base_target_speed=tactical,r13_C08=exit_corner and state.current_speed<tactical)
                if index == 0 or CHECKPOINTS[index-1][0] != phase:
                    self.emit('on_phase_start',context,h,state,view)
                if use_r12 and index == 0:
                    flags=state.mechanic_flags
                    reduction=ModifierStore.apply(state,'start_penalty',1,phase)
                    flags['start_delay']*=reduction
                    flags['start_acceleration_multiplier']=1-(1-flags['start_acceleration_multiplier'])*reduction
                    times[key]+=flags['start_delay']
                    self.event(state,'START_RESULT',context,**{name:flags[name] for name in
                        ('start_result','start_roll','start_error_probability','start_delay','start_acceleration_multiplier','start_penalty_distance')})
                if phase == 'FINAL_STRAIGHT'  and CHECKPOINTS[index-1][0] != phase:
                    self.emit('on_final_straight',context,h,state,view)
                # Base pace is calculated exactly once; skills cannot recursively rescan it.
                self.emit('on_checkpoint',context,h,state,view)
                updated_fatigue=stamina_factor(state.stamina_remaining/state.stamina_max,eh['grit'])
                updated_fatigue=1-(1-updated_fatigue)*ModifierStore.apply(state,'fatigue_penalty',1,phase)
                adjustment=r13.adjust_position(position,state,phase)
                state.mechanic_flags['effective_position_adjustment']=adjustment
                state.pace_state=pace_state(adjustment)
                tactical=neutral*(1+adjustment)
                tactical*=updated_fatigue/fatigue
                tactical=ModifierStore.apply(state,'pace',tactical,phase)
                target=ModifierStore.apply(state,'speed',tactical,phase)
                target*=1+d(context.rng.triangular(-.025,.025,0)) if segment else 1
                if state.mechanic_flags.pop('failed_overtake_next_segment',False):
                    target*=d('.98')
                targets[key]=target
                accel=base_acceleration(eh['power'])*f['surface']['acceleration']*d(TRACK_FACTORS[context.track_condition]['acceleration'])
                accel=ModifierStore.apply(state,'acceleration',accel,phase)
                if index == 0:
                    start_accel[key]=accel*(1+d(context.rng.triangular(-.04,.04,0)))
                    self.event(state,'START',context)
                accelerations[key]=start_accel[key] if index == 1 else accel
                state.target_speed=target
            swaps=[]; restrictions=[]; previous_times=dict(times)
            extra={key:Decimal(0) for key in states}
            if segment:
                # All target speeds are ready before any runner chooses a route.
                for key,state in states.items():
                    view[key].target_speed=targets[key]
                route_targets=dict(targets)
                for h in participants:
                    key=h['horse_id']; state=states[key]; eh=effective[key]; f=factors[key]
                    ahead=[o for o in view.values() if o.horse_id != key and o.lane == state.lane
                        and (o.progress > view[key].progress or (o.progress == view[key].progress and o.position < state.position))]
                    target=min(ahead,key=lambda o:abs(o.progress-view[key].progress)) if ahead else None
                    gap=abs(target.progress-view[key].progress) if target else None
                    soft=target is not None and gap <= GEOMETRY['soft_block']
                    was_blocked=state.blocked; state.blocked=soft
                    route=r13.route_context(context,eh,state,view,effective,route_targets)
                    state.mechanic_flags.update(r13.route_flags(context,eh,state,view,route))
                    self.emit('on_route_decision',context,h,state,view)
                    counter_bonus=ModifierStore.apply(state,'counter_decision',0,phase)
                    if counter_bonus:
                        probability=route.probabilities.get(state.lane,d(0))
                        decision=r13.counter_attack(probability,congestion(view[key],state.lane,view),counter_bonus)
                        state.mechanic_flags['counter_attack_decision']=decision
                        self.event(state,'COUNTER_ATTACK_DECISION',context,**decision)
                        if not decision['favorable']:
                            targets[key]*=d('.985')
                    else:
                        state.mechanic_flags.pop('counter_attack_decision',None)
                    if soft:
                        self.event(state,'BLOCKED',context,target.horse_id,lane=state.lane,block_kind='SOFT')
                        self.emit('on_blocked',context,h,state,view)
                    preferred,route_details=r13.choose_route(context,state,route,self.strategy_config)
                    active=any(ModifierStore.apply(state,mechanic,0,phase) for mechanic in
                        ('route_inner','route_grip','route_loss_reduction','route_strategy','preemptive_lane'))
                    pending=state.mechanic_flags.pop('w14_pending_decision',None)
                    strategy_active=ModifierStore.apply(state,'route_strategy',0,phase)>0
                    if pending and not state.mechanic_flags.get('w14_executed',False):
                        w14_route=r13.route_context(context,eh,state,view,effective,route_targets,include_skill_bonus=True)
                        pending=r13.w14_decision(w14_route,ModifierStore.apply(state,'route_strategy',0,phase),self.strategy_config)
                        outcome=r13.execute_w14(state,pending,states)
                        self.event(state,'W14_STRATEGY_EXECUTED',context,**outcome)
                        if outcome['old_lane'] != state.lane:
                            self.event(state,'LANE_CHANGE',context,old_lane=outcome['old_lane'],lane=state.lane,source='W14')
                            if soft: self.event(state,'BLOCK_AVOIDED',context,lane=state.lane)
                    safe=[lane for lane in route.candidate_lanes if lane != state.lane]
                    if not strategy_active and safe and (soft or active):
                        lane=preferred if active else min(safe,key=lambda lane:(congestion(view[key],lane,view),lane))
                        if lane != state.lane:
                            penalty=congestion(view[key],lane,view)*ModifierStore.apply(state,'congestion_penalty',1,phase)
                            preempt=ModifierStore.apply(state,'preemptive_lane',0,phase) if lane == state.lane+1 else d(0)
                            probability=lane_probability(eh['wisdom'],f['running_style']['grade'],penalty,preempt)
                            roll=d(context.rng.random())
                            self.event(state,'ROUTE_DECISION',context,lane=lane,probability=probability,roll=roll,**route_details)
                            if roll < probability:
                                old=state.lane; state.lane=lane; state.blocked=False
                                self.event(state,'LANE_CHANGE',context,old_lane=old,lane=lane,probability=probability,roll=roll)
                                if soft: self.event(state,'BLOCK_AVOIDED',context,lane=lane)
                    cleared=(was_blocked or soft) and not state.blocked
                    state.mechanic_flags['block_released']=cleared
                    if cleared:
                        self.event(state,'BLOCK_CLEARED',context)
                        first=not state.mechanic_flags.get('block_cleared_skill_consumed',False)
                        state.mechanic_flags['r13_C10']=first
                        self.emit('on_block_cleared',context,h,state,view)
                        state.mechanic_flags['block_cleared_skill_consumed']=True
                    if state.lane != view[key].lane:
                        ahead=r13.front(view[key],state.lane,view)
                        target=ahead[0] if ahead else None
                        gap=abs(target.progress-view[key].progress) if target else None
                        soft=target is not None and gap <= GEOMETRY['soft_block']
                    state.mechanic_flags['r13_C09']=state.lane == 2 and phase in ('CORNER_BATTLE','FINAL_CORNER')
                    state.mechanic_flags['r13_W07']=h['running_style'] in ('mid','closer') and state.mechanic_flags['progress_ratio']>=d('.50')
                    counter=state.mechanic_flags.get('counter_attack_decision')
                    if soft and targets[key] > targets[target.horse_id]+GEOMETRY['overtake_speed_advantage'] and not (counter and not counter['favorable']):
                        state.mechanic_flags['overtaking']=True
                        self.emit('on_overtake_attempt',context,h,state,view)
                        bonus=ModifierStore.apply(state,'overtake',1,phase)-1
                        counter=state.mechanic_flags.get('counter_attack_decision')
                        if counter and counter['favorable']: bonus+=d('.05')
                        decision=overtake_probability(eh,effective[target.horse_id],targets[key],targets[target.horse_id],
                            bucket,state.lane,context.track_condition,bonus)
                        roll=d(context.rng.random()); probability=decision['probability']
                        outcome='SUCCESS' if roll < probability else 'PARTIAL' if roll < probability+d('.10') else 'FAILED'
                        self.event(state,'OVERTAKE_ATTEMPT',context,target.horse_id,outcome=outcome,roll=roll,**decision)
                        extra[key]=state.stamina_max*{'SUCCESS':d('.005'),'PARTIAL':d('.010'),'FAILED':d('.015')}[outcome]
                        if outcome == 'SUCCESS':
                            state.blocked=False; swaps.append((key,target.horse_id))
                            self.event(state,'OVERTAKE_SUCCESS',context,target.horse_id,previous=state.position,position=target.position)
                            state.mechanic_flags['r13_C11']=state.mechanic_flags['progress_ratio']>=d('.50')
                            self.emit('on_overtake_success',context,h,state,view)
                            if not state.mechanic_flags.get('block_cleared_skill_consumed',False):
                                state.mechanic_flags['r13_C10']=True
                                self.event(state,'BLOCK_CLEARED',context)
                                self.emit('on_block_cleared',context,h,state,view)
                                state.mechanic_flags['block_cleared_skill_consumed']=True
                        elif outcome == 'FAILED':
                            restrictions.append((key,target.horse_id,outcome))
                            state.mechanic_flags['failed_overtake_next_segment']=True
                            self.event(state,'OVERTAKE_FAILED',context,target.horse_id)
                        else:
                            restrictions.append((key,target.horse_id,outcome))
                            state.blocked=False
                            self.event(state,'OVERTAKE_PARTIAL',context,target.horse_id)
                    hard=state.blocked and gap <= GEOMETRY['hard_block'] and targets[key] >= targets[target.horse_id]+GEOMETRY['hard_speed_advantage']
                    if hard:
                        cap=target.current_speed*GEOMETRY['hard_speed_factor']
                        # At the opening checkpoint nobody has moved yet; an initial
                        # zero speed must not create a permanent zero-speed deadlock.
                        if target.current_speed > 0:
                            loss=max(Decimal(0),targets[key]-cap)
                            penalty=clamp(ModifierStore.apply(state,'block_penalty',1,phase),0,1)
                            targets[key]-=loss*penalty
                for h in participants:
                    key=h['horse_id']; state=states[key]
                    if index != 1:
                        base=base_acceleration(effective[key]['power'])*factors[key]['surface']['acceleration']*d(TRACK_FACTORS[context.track_condition]['acceleration'])
                        accelerations[key]=ModifierStore.apply(state,'acceleration',base,phase)
                if previous_marker == 1900:
                    for h in participants:
                        key=h['horse_id']; state=states[key]
                        close=[o for o in view.values() if o.horse_id != key and abs(o.progress-view[key].progress) <= GEOMETRY['head_to_head']]
                        if close:
                            state.head_to_head=True; self.emit('on_head_to_head',context,h,state,view)
                            score=(d('.45')*stat_norm(h['speed'])+d('.35')*stat_norm(clamp(ModifierStore.apply(state,'grit',h['grit'],phase),0,1000))
                                +d('.20')*(state.stamina_remaining/state.stamina_max).sqrt()
                                +ModifierStore.apply(state,'duel',1,phase)-1+d(context.rng.uniform(-.02,.02)))
                            state.mechanic_flags['r13_C16']=True
                            self.emit('on_duel_computed',context,h,state,view)
                            base_grit=stat_norm(clamp(ModifierStore.apply(state,'grit',h['grit'],phase),0,1000))
                            enhanced_grit=clamp(ModifierStore.apply(state,'duel_grit',base_grit,phase),0,'1.20')
                            score+=d('.35')*(enhanced_grit-base_grit)
                            duel[key]=score
                            factor=(d('.98')+d('.04')*score)*ModifierStore.apply(state,'duel_speed',1,phase)
                            targets[key]*=factor
                            self.event(state,'HEAD_TO_HEAD',context,close[0].horse_id,duel_score=score)
                for h in participants:
                    key=h['horse_id']; state=states[key]; f=factors[key]
                    if use_r12 and index == 1:
                        flags=state.mechanic_flags
                        penalty_distance=min(d(segment),flags['start_penalty_distance'])
                        dt,speed=segment_time(penalty_distance,state.current_speed,targets[key],
                            accelerations[key]*flags['start_acceleration_multiplier'])
                        rest_time,speed=segment_time(d(segment)-penalty_distance,speed,targets[key],accelerations[key])
                        dt+=rest_time
                    else:
                        dt,speed=exit_segment_time(state,segment,targets[key],accelerations[key],phase)
                    times[key]+=dt; state.current_speed=speed; state.target_speed=targets[key]
                    style_index=('front','stalker','mid','closer').index(h['running_style'])
                    cost=(race_cost(context.distance)*d(segment)/context.distance*d(STYLE_COST[bucket][style_index])
                        *effective_pace_cost(state,phase)*f['distance']['cost']*f['surface']['cost']
                        *(1+(d(TRACK_FACTORS[context.track_condition]['cost'])-1)*ModifierStore.apply(state,'track_extra_cost',1,phase))
                        *wisdom_cost(effective[key]['wisdom']))
                    cost=max(Decimal(0),ModifierStore.apply(state,'stamina_consumption',cost,phase))
                    counter=state.mechanic_flags.get('counter_attack_decision')
                    if counter and not counter['favorable']: cost*=d('.92')
                    cost+=extra[key]
                    before=state.stamina_remaining/state.stamina_max
                    state.stamina_remaining=max(Decimal(0),state.stamina_remaining-cost)
                    after=state.stamina_remaining/state.stamina_max
                    for threshold in (d('.30'),d('.20'),d('.10'),d(0)):
                        if before > threshold >= after:
                            self.event(state,'STAMINA_LOW',context,threshold=threshold)
                            self.emit('on_stamina_threshold',context,h,state,view)
                # Successful discrete passes exchange relative timing only when
                # acceleration has not already naturally completed the pass.
                for key,target_key in swaps:
                    if times[key] > times[target_key]:
                        times[key],times[target_key]=times[target_key],times[key]
                for key,target_key,outcome in restrictions:
                    prior_gap=max(Decimal(0),previous_times[key]-previous_times[target_key])
                    floor=times[target_key]+(prior_gap if outcome == 'FAILED' else 0)
                    times[key]=max(times[key],floor)
                ordered=sorted(states.values(),key=lambda s:(times[s.horse_id],-duel.get(s.horse_id,0),s.position,s.horse_id))
                # Rank inversions after movement are natural/active overtakes, never initial grid changes.
                if previous_marker>0:
                    for victim in ordered:
                        overtakers=[other for other in ordered if other.position>victim.position and times[other.horse_id]<times[victim.horse_id]]
                        if overtakers:
                            victim.mechanic_flags['last_overtaken_checkpoint']=index
                            for other in overtakers:
                                self.event(victim,'OVERTAKEN_BY_OTHER',context,other.horse_id)
                            self.emit('on_overtaken',context,horses[victim.horse_id],victim,view)
                leader=ordered[0]; leader_time=times[leader.horse_id]
                for rank,state in enumerate(ordered,1):
                    state.position=rank
                    state.gap_to_leader=(times[state.horse_id]-leader_time)*leader.current_speed
                    state.progress=d(marker)-state.gap_to_leader
                    state.mechanic_flags['elapsed_time']=times[state.horse_id]
            else:
                ordered=sorted(states.values(),key=lambda s:s.position)
            for h in participants:
                self.emit('after_phase',context,h,states[h['horse_id']],view)
            checkpoints.append({'phase':phase,'checkpoint_index':index,'distance_marker':marker,'states':[asdict(s) for s in ordered]})
            previous_marker=marker
        ordered=sorted(states.values(),key=lambda s:(times[s.horse_id].quantize(d('.001'),rounding=ROUND_HALF_UP),
            -duel.get(s.horse_id,0),s.horse_id))
        for rank,state in enumerate(ordered,1):
            state.position=rank
            state.gap_to_leader=max(Decimal(0),(times[state.horse_id]-times[ordered[0].horse_id])*ordered[0].current_speed)
            self.event(state,'FINISH',context,finish_time=times[state.horse_id])
            for other in ordered:
                if state.horse_id != other.horse_id and abs(times[state.horse_id]-times[other.horse_id])*state.current_speed <= GEOMETRY['photo_finish']:
                    self.event(state,'PHOTO_FINISH',context,other.horse_id,finish_time=times[state.horse_id])
        checkpoints[-1]['states']=[asdict(state) for state in ordered]
        return {'race_id':context.race_id,'simulation_version':self.policy.version,'rng_seed':context.rng_seed,
            'started_at':context.started_at,'checkpoints':checkpoints,
            'tie_break':{'reason':'FINISH_TIME_0.001_THEN_DUEL_THEN_HORSE_ID'},
            'rankings':[{'rank':s.position,'horse_id':s.horse_id,'finish_time':times[s.horse_id],
                'finish_gap':s.gap_to_leader,'remaining_stamina':s.stamina_remaining,'stamina_start':s.stamina_max,
                'skill_performance':deepcopy(s.skill_runtime_state),'key_events':[asdict(e) for e in s.events]} for s in ordered]}


def effective_pace_cost(state,phase):
    reduction=ModifierStore.apply(state,'overpace_reduction',0,phase)
    if reduction: return 1+d('.18')*(1-reduction)
    return d(PACE_COST[state.pace_state])


def exit_segment_time(state,distance,target,acceleration,phase):
    """Physical acceleration splits: P14 quarter segment, C08 pre-skill target."""
    distance=d(distance); remaining=distance; speed=state.current_speed; total=d(0); traveled=d(0)
    exit_bonus=ModifierStore.apply(state,'exit_acceleration',1,phase)
    recovery=ModifierStore.apply(state,'exit_recovery_acceleration',1,phase)
    base_target=state.mechanic_flags.get('base_target_speed',target)
    quarter=distance*d('.25')
    while remaining>0:
        accel=acceleration
        if traveled < quarter: accel*=exit_bonus
        if speed < base_target: accel*=recovery
        length=remaining
        if traveled < quarter and exit_bonus != 1: length=min(length,quarter-traveled)
        if speed < base_target and recovery != 1 and base_target < target:
            to_base=max(d(0),(base_target**2-speed**2)/(2*accel))
            if to_base>0: length=min(length,to_base)
        elapsed,new_speed=segment_time(length,speed,target,accel)
        total+=elapsed; traveled+=length; remaining-=length; speed=new_speed
        if recovery != 1 and abs(speed-base_target)<d('1e-20'): speed=base_target
    return total,speed

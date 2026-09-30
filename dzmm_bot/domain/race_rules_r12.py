"""Approved R1.2 conditions; no automatic race activation."""
from decimal import Decimal
from .race_rules_r11 import d, clamp, wisdom_trigger
from .race_rules_r1 import stat_norm, STYLE_TARGETS, CHECKPOINTS, STYLE_COST, TRACK_FACTORS, race_cost, wisdom_cost

VERSION = 'race_engine_v1.2'
START_BONUS = {'S': '.030', 'A': '.015', 'B': '0', 'C': '-.015', 'D': '-.030'}
CONDITIONS = {'leader_gap': d('2.5'), 'following_gap': d('4'), 'following_percentile': d('.40'),
    'delayed_min': d('.10'), 'delayed_max': d('.28'), 'early_min': d('.35'), 'safe_factor': d('1.10')}
START_PENALTIES = {'NORMAL_START': (d(0), d(1), d(0)),
    'MINOR_ERROR': (d('.12'), d('.92'), d(50)), 'SEVERE_ERROR': (d('.28'), d('.82'), d(80))}


def resolve_start(horse, rng):
    probability = clamp(d('.20')-d('.08')*stat_norm(horse['power'])-d('.08')*stat_norm(horse['wisdom'])
        -d(START_BONUS[horse['affinity']['running_style_grade']]), '.03', '.18')
    roll = d(rng.random())
    result = 'NORMAL_START' if roll >= probability else 'SEVERE_ERROR' if roll < probability*d('.25') else 'MINOR_ERROR'
    delay, multiplier, distance = START_PENALTIES[result]
    return dict(start_result=result, start_roll=roll, start_error_probability=probability,
        start_delay=delay, start_acceleration_multiplier=multiplier, start_penalty_distance=distance,
        severe_start_error=result == 'SEVERE_ERROR', r12_W01=result == 'MINOR_ERROR')


def condition_flags(context, horse, state, view, projected_cost):
    style = horse['running_style']; n = len(view)
    percentile = d(state.position-1)/d(n-1) if n > 1 else d(0)
    low, high = map(d, STYLE_TARGETS[style])
    ratio = state.stamina_remaining/state.stamina_max
    others = sorted((s for s in view.values() if s.horse_id != state.horse_id), key=lambda s:s.position)
    second = next((s for s in others if s.position == 2), None)
    safe_line = min(state.stamina_max, projected_cost*CONDITIONS['safe_factor'])
    return dict(position_percentile=percentile, projected_remaining_cost=projected_cost, stamina_safe_line=safe_line,
        r12_C03=style == 'front' and state.position == 1 and second is not None and
            view[state.horse_id].progress-second.progress >= CONDITIONS['leader_gap'],
        r12_C04=style == 'stalker' and state.position > 1 and percentile <= CONDITIONS['following_percentile']
            and state.gap_to_leader <= CONDITIONS['following_gap'],
        r12_W08=CONDITIONS['delayed_min'] < ratio <= CONDITIONS['delayed_max'] and state.pace_state in ('SLOW','NORMAL'),
        r12_W09=ratio >= CONDITIONS['early_min'] and percentile > high,
        r12_W10=state.stamina_remaining < safe_line and low <= percentile <= high)


def projected_cost(context, horse, factors, index, previous_marker):
    from .race_engine_r11 import PHASE_BUCKET
    total = d(0); style_index = ('front','stalker','mid','closer').index(horse['running_style'])
    for phase, marker in CHECKPOINTS[index:]:
        distance = marker-previous_marker; previous_marker = marker
        total += race_cost(context.distance)*distance/context.distance*d(STYLE_COST[PHASE_BUCKET[phase]][style_index])
    return total*factors['distance']['cost']*factors['surface']['cost']*d(TRACK_FACTORS[context.track_condition]['cost'])*wisdom_cost(horse['wisdom'])


def build_engine(registry):
    from .race_engine_r11 import R11RaceEngine
    from .skill_execution import SkillRuntime, WisdomTriggerResolver
    return R11RaceEngine(SkillRuntime(registry, WisdomTriggerResolver(wisdom_trigger)), version=VERSION)

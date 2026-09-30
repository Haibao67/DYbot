"""Seven-phase discrete race orchestration; numerical mechanics require approved injection."""
from dataclasses import dataclass, field, asdict
from copy import deepcopy
from decimal import Decimal
from enum import Enum
import hashlib
from .horse_racing import RacingConfigurationError, AFFINITY_KEYS, stable_rng


class RacePhase(str, Enum):
    START = 'START'
    OPENING_POSITION = 'OPENING_POSITION'
    MID_RACE = 'MID_RACE'
    CORNER_BATTLE = 'CORNER_BATTLE'
    FINAL_CORNER = 'FINAL_CORNER'
    FINAL_STRAIGHT = 'FINAL_STRAIGHT'
    FINISH = 'FINISH'


HOOKS = ('before_phase', 'on_phase_start', 'on_checkpoint', 'on_overtake_attempt', 'on_blocked',
         'on_stamina_threshold', 'on_final_straight', 'on_head_to_head', 'after_phase',
         'on_route_decision', 'on_block_cleared', 'on_overtake_success', 'on_overtaken', 'on_duel_computed')


@dataclass
class RaceEvent:
    type: str
    phase: str
    horse_id: str
    target_horse_id: str | None = None
    importance: int = 1
    data: dict = field(default_factory=dict)
    message_key: str = ''
    race_id: str | None = None
    checkpoint: int | None = None
    event_id: str | None = None


@dataclass
class HorseRaceState:
    horse_id: str
    position: int = 0
    gap_to_leader: Decimal = Decimal(0)
    current_speed: Decimal = Decimal(0)
    target_speed: Decimal = Decimal(0)
    stamina_max: Decimal = Decimal(0)
    stamina_remaining: Decimal = Decimal(0)
    lane: int = 0
    blocked: bool = False
    pace_state: str = 'NORMAL'
    race_phase: str = ''
    progress: Decimal = Decimal(0)
    head_to_head: bool = False
    temporary_modifiers: dict = field(default_factory=dict)
    skill_runtime_state: dict = field(default_factory=dict)
    mechanic_flags: dict = field(default_factory=dict)
    events: list = field(default_factory=list)


@dataclass
class RaceContext:
    race_id: str
    distance: int
    distance_type: str
    surface: str
    track_condition: str
    participants: list
    started_at: float
    simulation_version: str
    rng_seed: str
    rng: object = None
    phase: str = ''
    checkpoint_index: int = 0
    rule_metadata: dict = field(default_factory=dict)


class RaceMechanics:
    """Implement these functions with approved versioned coefficients, outside Presentation.

    stamina_pool: endurance, distance affinity and condition.
    phase_targets: speed, power acceleration, style positioning and surface grip.
    stamina_consumption: phase/distance, pace, wisdom efficiency and track condition.
    stamina_factor: stamina bands and grit in the finish phases.
    update_route: wisdom, power, lanes, blocking and overtake intent.
    """
    def stamina_pool(self, context, horse):
        raise RacingConfigurationError('Approved stamina formula missing')

    def phase_targets(self, context, horse, state):
        """Return (target_speed, acceleration_per_second)."""
        raise RacingConfigurationError('Approved phase speed/acceleration formula missing')

    def stamina_consumption(self, context, horse, state, elapsed):
        raise RacingConfigurationError('Approved stamina consumption formula missing')

    def stamina_factor(self, context, horse, state):
        raise RacingConfigurationError('Approved low-stamina/grit formula missing')

    def update_route(self, context, horse, state, states):
        raise RacingConfigurationError('Approved positioning/blocking formula missing')


@dataclass(frozen=True)
class SimulationPolicy:
    version: str
    checkpoints: tuple  # (RacePhase value, distance marker, elapsed simulation seconds)
    variance: Decimal
    head_to_head_gap: Decimal
    mechanics: RaceMechanics

    def validate(self, distance):
        if not self.version or not 7 <= len(self.checkpoints) <= 10:
            raise RacingConfigurationError('Version and 7-10 checkpoints required')
        phases = [RacePhase(row[0]) for row in self.checkpoints]
        unique_phases = list(dict.fromkeys(phases))
        if unique_phases != list(RacePhase) or phases != sorted(phases, key=lambda p: list(RacePhase).index(p)):
            raise RacingConfigurationError('Checkpoints must cover all seven ordered phases')
        last = -1
        for phase, marker, elapsed in self.checkpoints:
            if not 0 <= marker <= distance or marker < last or Decimal(str(elapsed)) <= 0:
                raise RacingConfigurationError('Invalid checkpoint distance/duration')
            last = marker
        if last != distance:
            raise RacingConfigurationError('Finish checkpoint must match race distance')
        variance, gap = Decimal(str(self.variance)), Decimal(str(self.head_to_head_gap))
        if not variance.is_finite() or not gap.is_finite() or not 0 <= variance <= Decimal('.05') or gap < 0:
            raise RacingConfigurationError('Variance must be <=5%; gap must be nonnegative')
        if not isinstance(self.mechanics, RaceMechanics):
            raise RacingConfigurationError('Explicit race mechanics required')
        if getattr(self.mechanics,'version',self.version) != self.version:
            raise RacingConfigurationError('Mechanics and simulation versions must match')


def number(value, minimum=Decimal(0)):
    value = Decimal(str(value))
    if not value.is_finite() or value < minimum:
        raise RacingConfigurationError('Invalid numerical mechanic output')
    return value


class RaceEngine:
    def __init__(self, policy=None, hooks=None, skill_runtime=None):
        self.policy = policy
        self.skill_runtime = skill_runtime
        self.hooks = {key: tuple(value) for key, value in (hooks or {}).items()}
        if skill_runtime is not None:
            for key, callbacks in skill_runtime.hooks().items():
                self.hooks[key] = self.hooks.get(key, ()) + callbacks
        if set(self.hooks) - set(HOOKS):
            raise ValueError('Unknown race hook')

    def emit(self, name, context, horse, state, states):
        for callback in self.hooks.get(name, ()):
            callback(context, horse, state, states)

    @staticmethod
    def event(state, kind, context, target=None, **data):
        event_id=hashlib.sha256(f'{context.race_id}:{state.horse_id}:{context.checkpoint_index}:{kind}:{len(state.events)}'.encode()).hexdigest()
        state.events.append(RaceEvent(kind, context.phase, state.horse_id, target,
            data=data, message_key=kind.lower(),race_id=context.race_id,
            checkpoint=context.checkpoint_index,event_id=event_id))

    def simulate(self, context):
        from .skill_execution import ModifierStore
        if self.policy is None:
            raise RacingConfigurationError('Race simulation disabled: approved policy missing')
        if context.simulation_version != self.policy.version:
            raise RacingConfigurationError('Simulation version mismatch')
        self.policy.validate(context.distance)
        if context.distance_type not in AFFINITY_KEYS['distance'] or context.surface not in AFFINITY_KEYS['surface']:
            raise ValueError('Invalid race conditions')
        if context.track_condition not in ('良', '稍重', '重', '不良'):
            raise ValueError('Invalid track condition')
        context = deepcopy(context)
        if context.rng is None:
            context.rng, _ = stable_rng(context.rng_seed, self.policy.version)
        participants = sorted(context.participants, key=lambda horse: horse['horse_id'])
        if not participants or len({h['horse_id'] for h in participants}) != len(participants):
            raise ValueError('Unique participants required')
        tie_order = [horse['horse_id'] for horse in participants]
        context.rng.shuffle(tie_order)
        tie_rank = {horse_id: rank for rank, horse_id in enumerate(tie_order)}
        states, horse_map = {}, {}
        mechanics = self.policy.mechanics
        context.phase = RacePhase.START.value
        for horse in participants:
            if horse['running_style'] not in AFFINITY_KEYS['running_style']:
                raise ValueError('Invalid running style')
            for trait in ('speed', 'stamina', 'power', 'wisdom', 'grit'):
                number(horse[trait])
            for key in ('distance', 'surface', 'running_style'):
                number(horse['affinity'][key + '_modifier'], Decimal('.000001'))
            pool = number(mechanics.stamina_pool(context, horse), Decimal('.000001'))
            states[horse['horse_id']] = HorseRaceState(horse['horse_id'], position=tie_rank[horse['horse_id']]+1,
                stamina_max=pool, stamina_remaining=pool)
            horse_map[horse['horse_id']] = horse
        if self.skill_runtime is not None:
            for horse in participants:
                self.skill_runtime.initialize(context, horse, states[horse['horse_id']], states)
        def effective_horse(horse, state):
            return {**horse, **{trait: number(ModifierStore.apply(state, trait, horse[trait], context.phase))
                               for trait in ('power', 'wisdom', 'grit')}}
        checkpoints = []
        # Tie order is seeded once and remains stable between checkpoints.
        for index, (phase, marker, elapsed) in enumerate(self.policy.checkpoints):
            context.phase, context.checkpoint_index = RacePhase(phase).value, index
            previous = {key: state.position for key, state in states.items()}
            route_snapshot = deepcopy(states)
            for horse in participants:
                state = states[horse['horse_id']]
                state.race_phase = context.phase
                self.emit('before_phase', context, horse, state, states)
                if context.phase == RacePhase.FINISH.value:
                    close = [other for other in states.values() if other.horse_id != state.horse_id
                             and abs(other.progress - state.progress) <= number(self.policy.head_to_head_gap)]
                    state.head_to_head = bool(close)
                    if close:
                        self.emit('on_head_to_head', context, horse, state, states)
                if index == 0 or self.policy.checkpoints[index - 1][0] != phase:
                    self.emit('on_phase_start', context, horse, state, states)
                if context.phase == RacePhase.FINAL_STRAIGHT.value:
                    self.emit('on_final_straight', context, horse, state, states)
                self.emit('on_checkpoint', context, horse, state, states)
                mechanics.update_route(context, effective_horse(horse, state), state, route_snapshot)
                if state.mechanic_flags.get('overtaking'):
                    self.emit('on_overtake_attempt', context, horse, state, states)
                    self.event(state, 'OVERTAKE_ATTEMPT', context,
                        outcome=state.mechanic_flags.get('overtake_result'))
                if state.blocked:
                    self.emit('on_blocked', context, horse, state, states)
                    self.event(state, 'BLOCKED', context, lane=state.lane)
                elif state.mechanic_flags.get('block_avoided'):
                    self.event(state, 'BLOCK_AVOIDED', context, lane=state.lane)
                old_ratio = state.stamina_remaining / state.stamina_max
                consumption = number(ModifierStore.apply(state, 'stamina_consumption',
                    mechanics.stamina_consumption(context, effective_horse(horse, state), state, elapsed), context.phase))
                state.stamina_remaining = max(Decimal(0), state.stamina_remaining - consumption)
                ratio = state.stamina_remaining / state.stamina_max
                for threshold in (Decimal('.30'), Decimal('.10'), Decimal(0)):
                    if old_ratio > threshold >= ratio:
                        self.event(state, 'STAMINA_LOW', context, threshold=str(threshold))
                        self.emit('on_stamina_threshold', context, horse, state, states)
                target, acceleration = mechanics.phase_targets(context, effective_horse(horse, state), state)
                target = number(ModifierStore.apply(state, 'speed', target, context.phase))
                acceleration = number(ModifierStore.apply(state, 'acceleration', acceleration, context.phase))
                factor = number(ModifierStore.apply(state, 'stamina_factor',
                    mechanics.stamina_factor(context, effective_horse(horse, state), state), context.phase))
                variance = Decimal(str(self.policy.variance)) * (Decimal(str(context.rng.random())) * 2 - 1)
                state.target_speed = number(target) * factor * (1 + variance)
                state.current_speed = min(state.target_speed, state.current_speed + number(acceleration) * number(elapsed))
                state.progress += state.current_speed * number(elapsed)
            ordered = sorted(states.values(), key=lambda state: (-state.progress, tie_rank[state.horse_id]))
            leader = ordered[0].progress
            for rank, state in enumerate(ordered, 1):
                state.position, state.gap_to_leader = rank, leader - state.progress
                horse = horse_map[state.horse_id]
                if previous[state.horse_id] and rank < previous[state.horse_id]:
                    self.event(state, 'OVERTAKE_SUCCESS', context, previous=previous[state.horse_id], position=rank)
                if context.phase == RacePhase.FINISH.value:
                    close = [other for other in ordered if other.horse_id != state.horse_id
                             and abs(other.progress - state.progress) <= number(self.policy.head_to_head_gap)]
                    state.head_to_head = bool(close)
                    for other in close:
                        self.event(state, 'HEAD_TO_HEAD', context, other.horse_id)
                self.emit('after_phase', context, horse, state, states)
            checkpoints.append({'phase': context.phase, 'checkpoint_index': index, 'distance_marker': marker,
                'states': [asdict(state) for state in ordered]})
        return {'race_id': context.race_id, 'simulation_version': self.policy.version,
            'rng_seed': context.rng_seed, 'started_at': context.started_at,
            'tie_break': {'reason': 'SEEDED_RNG_FOR_EQUAL_PROGRESS', 'order': tie_order,
                'tied_horses': [state.horse_id for state in ordered if any(
                    other.horse_id != state.horse_id and other.progress == state.progress for other in ordered)]},
            'checkpoints': checkpoints, 'rankings': [{'rank': state.position, 'horse_id': state.horse_id,
                'finish_gap': state.gap_to_leader, 'remaining_stamina': state.stamina_remaining,
                'stamina_start': state.stamina_max,
                'skill_performance': deepcopy(state.skill_runtime_state),
                'key_events': [asdict(event) for event in state.events]} for state in ordered]}

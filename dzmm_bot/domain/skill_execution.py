"""P8-5 generic triggers, wisdom resolution, modifiers and runtime hooks."""
from dataclasses import dataclass, asdict, field
from decimal import Decimal
from copy import deepcopy
from .horse_racing import RacingConfigurationError, validate_skills
from .race_engine import RaceEvent, RacePhase, RaceEngine, number

PHASE_ORDER = tuple(phase.value for phase in RacePhase)
STACK_POLICIES = {'STACK', 'REFRESH_DURATION', 'KEEP_STRONGEST', 'REPLACE', 'UNIQUE'}
EFFECT_MECHANICS = {
    'MODIFY_SPEED': 'speed', 'MODIFY_ACCELERATION': 'acceleration',
    'MODIFY_STAMINA_CONSUMPTION': 'stamina_consumption', 'MODIFY_POWER': 'power',
    'MODIFY_WISDOM': 'wisdom', 'MODIFY_GRIT': 'grit',
    'CHANGE_LANE_PREFERENCE': 'lane_preference', 'REDUCE_BLOCK_PENALTY': 'block_penalty',
    'IMPROVE_OVERTAKE': 'overtake', 'MODIFY_PACE': 'pace',
}


@dataclass
class Modifier:
    modifier_id: str
    source_skill_id: str
    stat_or_mechanic: str
    operation: str
    value: Decimal
    start_phase: str
    expires_phase: str | None
    remaining_checkpoints: int | None
    stack_policy: str
    applied_checkpoint: int
    conditions: dict = field(default_factory=dict)

    def validate(self):
        if set(self.conditions)-{'phases','stamina_below','head_to_head','percentile_max','progress_min','progress_max','distance_before','exit_corner'}:
            raise RacingConfigurationError('Unsupported modifier condition')
        if any(p not in PHASE_ORDER for p in self.conditions.get('phases',[])):
            raise RacingConfigurationError('Invalid modifier condition phase')
        if 'stamina_below' in self.conditions and not 0 <= Decimal(str(self.conditions['stamina_below'])) <= 1:
            raise RacingConfigurationError('Invalid modifier stamina condition')
        if self.operation not in {'ADD', 'MULTIPLY', 'SET'} or self.stack_policy not in STACK_POLICIES:
            raise RacingConfigurationError('Invalid modifier operation/stack policy')
        if self.start_phase not in PHASE_ORDER or (self.expires_phase is not None and
                self.expires_phase not in PHASE_ORDER):
            raise RacingConfigurationError('Invalid modifier phase')
        if not self.value.is_finite() or (self.operation == 'MULTIPLY' and self.value < 0):
            raise RacingConfigurationError('Invalid modifier value')
        if self.remaining_checkpoints is not None and (type(self.remaining_checkpoints) is not int or self.remaining_checkpoints < 1):
            raise RacingConfigurationError('Invalid modifier lifetime')


class ModifierStore:
    @staticmethod
    def add(state, modifier):
        modifier.validate()
        values = state.temporary_modifiers
        matching = [key for key, row in values.items() if row['source_skill_id'] == modifier.source_skill_id
                    and row['stat_or_mechanic'] == modifier.stat_or_mechanic]
        if matching and modifier.stack_policy == 'UNIQUE':
            return False
        if matching and modifier.stack_policy == 'REFRESH_DURATION':
            row = values[matching[0]]
            row.update(remaining_checkpoints=modifier.remaining_checkpoints, expires_phase=modifier.expires_phase,
                       applied_checkpoint=modifier.applied_checkpoint)
            return True
        if matching and modifier.stack_policy == 'KEEP_STRONGEST':
            def strength(row):
                value = Decimal(str(row['value']))
                return abs(value - 1) if row['operation'] == 'MULTIPLY' else abs(value)
            if max(strength(values[key]) for key in matching) >= strength(asdict(modifier)):
                return False
        if modifier.stack_policy in {'KEEP_STRONGEST', 'REPLACE'}:
            for key in matching:
                del values[key]
        values[modifier.modifier_id] = asdict(modifier)
        return True

    @staticmethod
    def before_checkpoint(state, phase):
        phase_index = PHASE_ORDER.index(phase)
        for key, row in list(state.temporary_modifiers.items()):
            if row['expires_phase'] is not None and phase_index > PHASE_ORDER.index(row['expires_phase']):
                del state.temporary_modifiers[key]

    @staticmethod
    def after_checkpoint(state, checkpoint):
        for key, row in list(state.temporary_modifiers.items()):
            if row['remaining_checkpoints'] is not None and row['applied_checkpoint'] <= checkpoint:
                row['remaining_checkpoints'] -= 1
                if row['remaining_checkpoints'] == 0:
                    del state.temporary_modifiers[key]

    @staticmethod
    def apply(state, mechanic, base, phase):
        result = Decimal(str(base))
        phase_index = PHASE_ORDER.index(phase)
        def eligible(row):
            conditions=row.get('conditions',{})
            if 'phases' in conditions and phase not in conditions['phases']: return False
            if 'stamina_below' in conditions and state.stamina_remaining/state.stamina_max >= Decimal(str(conditions['stamina_below'])): return False
            if row['applied_checkpoint'] > state.mechanic_flags.get('checkpoint_index',row['applied_checkpoint']): return False
            flags=state.mechanic_flags
            if 'percentile_max' in conditions and flags.get('position_percentile',Decimal(1)) > Decimal(str(conditions['percentile_max'])): return False
            if 'progress_min' in conditions and flags.get('progress_ratio',Decimal(0)) < Decimal(str(conditions['progress_min'])): return False
            if 'progress_max' in conditions and flags.get('progress_ratio',Decimal(0)) >= Decimal(str(conditions['progress_max'])): return False
            if conditions.get('exit_corner') and not flags.get('exit_corner',False): return False
            if 'head_to_head' in conditions and state.head_to_head != conditions['head_to_head']: return False
            return True
        rows = [row for row in state.temporary_modifiers.values()
                if row['stat_or_mechanic'] == mechanic and eligible(row) and PHASE_ORDER.index(row['start_phase']) <= phase_index
                and (row['expires_phase'] is None or phase_index <= PHASE_ORDER.index(row['expires_phase']))]
        # Defined deterministic order: SET, ADD, MULTIPLY. No skill-name branches.
        for operation in ('SET', 'ADD', 'MULTIPLY'):
            for row in sorted(rows, key=lambda item: item['modifier_id']):
                if row['operation'] != operation:
                    continue
                value = Decimal(str(row['value']))
                if 'distance_before' in row.get('conditions',{}):
                    flags=state.mechanic_flags; start=Decimal(str(flags.get('segment_start_ratio',0))); end=Decimal(str(flags.get('segment_end_ratio',0)))
                    edge=Decimal(str(row['conditions']['distance_before']))
                    fraction=max(Decimal(0),min(end,edge)-start)/(end-start) if end > start else Decimal(0)
                    value=1+(value-1)*fraction if operation == 'MULTIPLY' else value*fraction
                result = value if operation == 'SET' else result + value if operation == 'ADD' else result * value
        if not result.is_finite():
            raise RacingConfigurationError('Invalid combined modifier')
        return result


class TriggerEvaluator:
    KEYS = {'phase', 'position_min', 'position_max', 'position_not_first', 'stamina_below', 'stamina_above',
        'distance_type', 'surface', 'track_condition', 'running_style', 'blocked', 'overtaking', 'head_to_head',
        'gap_to_leader_min', 'gap_to_leader_max', 'stamina_at_most', 'flags', 'consecutive_clean_checkpoints', 'hook'}

    @classmethod
    def evaluate(cls, trigger, context, horse, state, hook):
        unknown = set(trigger) - cls.KEYS
        if unknown:
            raise RacingConfigurationError('Unsupported trigger keys: ' + ','.join(sorted(unknown)))
        def contains(value, expected):
            return value in expected if isinstance(expected, (list, tuple)) else value == expected
        for key in ('phase', 'distance_type', 'surface', 'track_condition'):
            if key in trigger and not contains(getattr(context, key), trigger[key]):
                return False
        if 'hook' in trigger and not contains(hook, trigger['hook']):
            return False
        if 'running_style' in trigger and not contains(horse['running_style'], trigger['running_style']):
            return False
        if trigger.get('position_not_first') and state.position <= 1:
            return False
        if 'position_min' in trigger and state.position < trigger['position_min']:
            return False
        if 'position_max' in trigger and (state.position == 0 or state.position > trigger['position_max']):
            return False
        fraction = state.stamina_remaining / state.stamina_max if state.stamina_max else Decimal(0)
        if 'stamina_below' in trigger and fraction >= Decimal(str(trigger['stamina_below'])):
            return False
        if 'stamina_at_most' in trigger and fraction > Decimal(str(trigger['stamina_at_most'])):
            return False
        if 'stamina_above' in trigger and fraction <= Decimal(str(trigger['stamina_above'])):
            return False
        for key in ('blocked', 'head_to_head'):
            if key in trigger and getattr(state, key) != trigger[key]:
                return False
        if 'overtaking' in trigger and state.mechanic_flags.get('overtaking') != trigger['overtaking']:
            return False
        if 'gap_to_leader_min' in trigger and state.gap_to_leader < Decimal(str(trigger['gap_to_leader_min'])):
            return False
        if 'gap_to_leader_max' in trigger and state.gap_to_leader > Decimal(str(trigger['gap_to_leader_max'])):
            return False
        for key, expected in trigger.get('flags', {}).items():
            if key not in state.mechanic_flags or not contains(state.mechanic_flags[key], expected):
                return False
        if state.mechanic_flags.get('clean_checkpoints', 0) < trigger.get('consecutive_clean_checkpoints', 0):
            return False
        return True


class WisdomTriggerResolver:
    def __init__(self, probability_formula=None):
        self.probability_formula = probability_formula

    def resolve(self, context, horse, state, skill, level):
        if self.probability_formula is None:
            raise RacingConfigurationError('Wisdom activation disabled: approved formula missing')
        probability = Decimal(str(self.probability_formula(context, horse, state, skill, level)))
        if not probability.is_finite() or not 0 <= probability <= 1:
            raise RacingConfigurationError('Wisdom probability must be 0-1')
        roll = Decimal(str(context.rng.random()))
        return {'activation_probability': str(probability), 'roll': str(roll), 'success': roll < probability}


class SkillEffectExecutor:
    def execute(self, context, horse, state, skill, level, activation):
        curve = skill.level_scaling.get(level, skill.level_scaling.get(str(level)))
        if curve is None:
            raise RacingConfigurationError('Missing skill level scaling')
        prepared = []
        for index, effect in enumerate(skill.effect_definition):
            if effect['type'] == 'RECOVER_STAMINA':
                value = Decimal(str(curve[effect.get('scaling_key', 'value')] if isinstance(curve, dict) else curve))
                if not value.is_finite() or not 0 <= value <= 1:
                    raise RacingConfigurationError('Recovery must be fraction of stamina_max')
                prepared.append(('recover', value))
                continue
            mechanic = EFFECT_MECHANICS.get(effect['type']) or effect.get('stat_or_mechanic')
            if not mechanic:
                raise RacingConfigurationError('Missing effect mechanic')
            raw = curve[effect.get('scaling_key', 'value')] if isinstance(curve, dict) else curve
            raw = Decimal(str(raw))
            operation = effect.get('operation', 'MULTIPLY')
            value = 1 + raw if operation == 'MULTIPLY' else raw
            modifier = Modifier(f"{skill.skill_id}:{activation}:{index}", skill.skill_id, mechanic, operation,
                value, effect.get('start_phase', context.phase), effect.get('expires_phase'),
                effect.get('duration_checkpoints'), effect.get('stack_policy', 'UNIQUE'), context.checkpoint_index+effect.get('delay_checkpoints',0),
                deepcopy(effect.get('conditions',{})))
            modifier.validate()
            prepared.append(('modifier', modifier))
        # Validate all effects before mutation, including compound skills.
        summaries = []
        for kind, value in prepared:
            if kind == 'recover':
                before = state.stamina_remaining
                state.stamina_remaining = min(state.stamina_max, before + state.stamina_max * value)
                summaries.append({'mechanic': 'recover_stamina', 'actual': str(state.stamina_remaining - before)})
            elif ModifierStore.add(state, value):
                summaries.append({'mechanic': value.stat_or_mechanic, 'operation': value.operation, 'value': str(value.value)})
        return summaries


class SkillRuntime:
    def __init__(self, registry, wisdom_resolver=None, executor=None):
        self.registry = registry
        self.wisdom_resolver = wisdom_resolver or WisdomTriggerResolver()
        self.executor = executor or SkillEffectExecutor()

    def initialize(self, context, horse, state, states):
        validate_skills(horse.get('skills', []))
        for entry in horse.get('skills', []):
            definition = self.registry.get(entry['skill_id'])
            level = entry['level']
            if type(level) is not int or not 1 <= level <= 10:
                raise ValueError('Invalid runtime skill level')
            if not definition.enabled:
                continue
            state.skill_runtime_state[definition.skill_id] = {'level': level, 'activations': 0,
                'attempts': 0, 'failures': 0, 'last_activation_checkpoint': None,
                'last_attempt_checkpoint': None, 'cooldown_remaining': 0, 'activated_phases': [], 'phase_attempts': {}}
        self.trigger('race_initialize', context, horse, state, states, passive_only=True)

    def trigger(self, hook, context, horse, state, states, passive_only=False):
        for skill_id, runtime in state.skill_runtime_state.items():
            definition = self.registry.get(skill_id)
            if definition.type == 'PASSIVE' and runtime['activations']:
                continue
            if passive_only and definition.type != 'PASSIVE':
                continue
            last = runtime['last_activation_checkpoint']
            runtime['cooldown_remaining'] = max(0, definition.cooldown_checkpoints - (context.checkpoint_index - last)) if last is not None else 0
            if definition.max_attempts is not None and runtime['attempts'] >= definition.max_attempts:
                continue
            if definition.max_attempts_per_phase is not None and runtime.get('phase_attempts',{}).get(context.phase,0) >= definition.max_attempts_per_phase:
                continue
            if not definition.retry_on_fail and runtime['failures']:
                continue
            if definition.version == '1.3' and last is not None and context.checkpoint_index-last <= definition.cooldown_checkpoints and definition.cooldown_checkpoints:
                continue
            if runtime['activations'] >= definition.max_activations or runtime['cooldown_remaining']:
                continue
            if definition.activation_policy == 'ONCE_PER_RACE' and runtime['activations']:
                continue
            if definition.activation_policy == 'ONCE_PER_PHASE' and context.phase in runtime['activated_phases']:
                continue
            if runtime['last_attempt_checkpoint'] == context.checkpoint_index:
                continue
            if not TriggerEvaluator.evaluate(definition.trigger_definition, context, horse, state, hook):
                continue
            outcome = None
            if definition.type == 'WISDOM_TRIGGER':
                if self.wisdom_resolver.probability_formula is None:
                    # Missing formula is an explicit gate, not a fake chance or failure.
                    runtime['configuration_gate'] = 'wisdom_probability_missing'
                    continue
                outcome = self.wisdom_resolver.resolve(context, horse, state, definition, runtime['level'])
            runtime['last_attempt_checkpoint'] = context.checkpoint_index
            runtime['attempts'] += 1
            runtime.setdefault('phase_attempts',{})[context.phase]=runtime['phase_attempts'].get(context.phase,0)+1
            if outcome and not outcome['success']:
                runtime['failures'] += 1
                self.event(context, state, definition, runtime, 'SKILL_FAILED', outcome)
                continue
            effects = self.executor.execute(context, horse, state, definition, runtime['level'], runtime['activations'] + 1)
            runtime['activations'] += 1
            runtime['last_activation_checkpoint'] = context.checkpoint_index
            runtime['activated_phases'].append(context.phase)
            self.event(context, state, definition, runtime, 'SKILL_ACTIVATED', {'effects': effects, **(outcome or {})})

    @staticmethod
    def event(context, state, definition, runtime, kind, details):
        RaceEngine.event(state,kind,context,skill_id=definition.skill_id,display_name=definition.display_name,
            level=runtime['level'],type=definition.type,**details)

    def before_checkpoint(self, context, horse, state, states):
        ModifierStore.before_checkpoint(state, context.phase)
        self.trigger('before_phase', context, horse, state, states)

    def after_checkpoint(self, context, horse, state, states):
        clean = not state.blocked and state.pace_state != 'OVERPACE' and not state.mechanic_flags.get('overtaking', False)
        state.mechanic_flags['clean_checkpoints'] = state.mechanic_flags.get('clean_checkpoints', 0) + 1 if clean else 0
        ModifierStore.after_checkpoint(state, context.checkpoint_index)
        self.trigger('after_phase', context, horse, state, states)

    def hooks(self):
        result = {'before_phase': (self.before_checkpoint,), 'after_phase': (self.after_checkpoint,)}
        for name in ('on_phase_start', 'on_checkpoint', 'on_overtake_attempt', 'on_blocked',
                     'on_stamina_threshold', 'on_final_straight', 'on_head_to_head',
                     'on_route_decision','on_block_cleared','on_overtake_success','on_overtaken','on_duel_computed'):
            def callback(context, horse, state, states, hook=name):
                self.trigger(hook, context, horse, state, states)
            result[name] = (callback,)
        return result

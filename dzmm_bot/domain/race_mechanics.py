"""P8-6 routing orchestration. Numerical formulas are supplied by approved policy."""
from decimal import Decimal
from copy import deepcopy
from .race_engine import RaceMechanics, number
from .horse_racing import RacingConfigurationError
from .skill_execution import ModifierStore

STYLE_TARGETS = {'front': (Decimal(0), Decimal('.15')),
    'stalker': (Decimal('.15'), Decimal('.40')),
    'mid': (Decimal('.40'), Decimal('.75')), 'closer': (Decimal('.75'), Decimal(1))}
PACE_STATES = ('SLOW', 'NORMAL', 'FAST', 'OVERPACE')
OVERTAKE_RESULTS = ('SUCCESS', 'PARTIAL', 'BLOCKED', 'FAILED')


class ConfiguredRaceMechanics(RaceMechanics):
    """Callbacks own coefficients; route decisions cannot mutate other horses.

    route returns lane, pace, blocked, overtake_result and extra_stamina_cost.
    It receives a checkpoint snapshot plus all active tactical modifiers.
    """
    def __init__(self, *, version, stamina_pool, phase_targets, consumption,
                 stamina_factor, route, lanes):
        if not version or type(lanes) is not int or lanes < 1 or not all(callable(f)
                for f in (stamina_pool, phase_targets, consumption, stamina_factor, route)):
            raise RacingConfigurationError('Complete approved mechanics policy required')
        self.version, self.lanes = version, lanes
        self.pool_fn, self.targets_fn = stamina_pool, phase_targets
        self.cost_fn, self.factor_fn, self.route_fn = consumption, stamina_factor, route

    def stamina_pool(self, context, horse):
        return self.pool_fn(context, horse)

    def phase_targets(self, context, horse, state):
        return self.targets_fn(context, horse, state, STYLE_TARGETS[horse['running_style']])

    def stamina_consumption(self, context, horse, state, elapsed):
        return number(self.cost_fn(context, horse, state, elapsed)) + number(
            state.mechanic_flags.get('extra_stamina_cost', 0))

    def stamina_factor(self, context, horse, state):
        return self.factor_fn(context, horse, state)

    def update_route(self, context, horse, state, states):
        modifiers = {key: ModifierStore.apply(state, key, 1, context.phase)
            for key in ('lane_preference', 'block_penalty', 'overtake', 'pace')}
        result = self.route_fn(context, horse, deepcopy(state), deepcopy(states),
                               STYLE_TARGETS[horse['running_style']], modifiers)
        lane, pace = result['lane'], result['pace']
        outcome = result.get('overtake_result')
        if type(lane) is not int or not 0 <= lane < self.lanes or pace not in PACE_STATES or type(result['blocked']) is not bool:
            raise RacingConfigurationError('Invalid route decision')
        if outcome is not None and outcome not in OVERTAKE_RESULTS:
            raise RacingConfigurationError('Invalid overtake outcome')
        cost = number(result.get('extra_stamina_cost', 0))
        state.lane, state.pace_state, state.blocked = lane, pace, bool(result['blocked'])
        state.mechanic_flags.update(overtaking=outcome is not None,
            overtake_result=outcome, extra_stamina_cost=cost,
            block_avoided=bool(result.get('block_avoided',False)))

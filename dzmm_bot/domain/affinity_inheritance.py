"""P8-9 versioned probability matrices, with no guessed inheritance defaults."""
from dataclasses import dataclass
from decimal import Decimal
from copy import deepcopy
from .horse_racing import AFFINITY_KEYS, GRADES, RacingConfigurationError, stable_rng, validated_weights, weighted_choice


FOAL_AFFINITY_ALGORITHM = 'ruihe-foal-affinity-v2'


def inherit_foal_affinities(father, mother, birth_event_id, horse_id, father_id, mother_id,
                            upgrade_percent=10):
    """Each affinity grade comes from one parent, with a 10% one-grade improvement roll."""
    if type(upgrade_percent) is not int or not 0 <= upgrade_percent <= 100:
        raise RacingConfigurationError('Invalid foal affinity improvement chance')
    rng, reference = stable_rng(birth_event_id, horse_id, FOAL_AFFINITY_ALGORITHM)
    result, rolls, mutations = {}, {}, []
    for category, keys in AFFINITY_KEYS.items():
        result[category], rolls[category] = {}, {}
        for key in keys:
            father_grade, mother_grade = father[category][key], mother[category][key]
            if father_grade not in GRADES or mother_grade not in GRADES:
                raise RacingConfigurationError('Parent affinities must be finalized')
            parent = 'father' if rng.randrange(2) == 0 else 'mother'
            inherited = father_grade if parent == 'father' else mother_grade
            roll = rng.randrange(100)
            grade = GRADES[max(0, GRADES.index(inherited) - 1)] if roll < upgrade_percent else inherited
            result[category][key] = grade
            rolls[category][key] = {'parent': parent, 'inherited_grade': inherited,
                                    'upgrade_roll': roll, 'result': grade}
            if grade != inherited:
                mutations.append({'category': category, 'key': key, 'from': inherited, 'to': grade})
    return {'father_affinities': deepcopy(father), 'mother_affinities': deepcopy(mother),
        'father_id': father_id, 'mother_id': mother_id, 'generation': None,
        'result': result, 'mutation': mutations, 'inheritance_rolls': rolls,
        'algorithm_version': FOAL_AFFINITY_ALGORITHM, 'rng_reference': reference,
        'birth_event_id': birth_event_id, 'horse_id': horse_id}


@dataclass(frozen=True)
class AffinityInheritancePolicy:
    version: str
    pair_weights: dict  # category -> father_grade -> mother_grade -> child_grade weights
    mutation_probability: Decimal
    mutation_weights: dict  # initial grade -> final grade weights
    mutation_shifts: dict | None = None  # R1 records the signed mutation roll before clamping

    def validate(self):
        probability = Decimal(str(self.mutation_probability))
        if not self.version or not probability.is_finite() or not 0 <= probability <= 1:
            raise RacingConfigurationError('Approved affinity inheritance configuration required')
        for category in AFFINITY_KEYS:
            for father in GRADES:
                for mother in GRADES:
                    validated_weights(self.pair_weights[category][father][mother], GRADES)
        for grade in GRADES:
            validated_weights(self.mutation_weights[grade], GRADES)
        if self.mutation_shifts is not None:
            if probability != 1:
                raise RacingConfigurationError('Shift distribution must include its unchanged probability')
            validated_weights(self.mutation_shifts, (-2,-1,0,1,2))


def recorded_choice(rng, weights, choices):
    values = validated_weights(weights, choices)
    roll = Decimal(str(rng.random()))
    draw = roll * sum(values)
    for key, value in zip(choices, values):
        draw -= value
        if draw < 0:
            return key, str(roll)
    return choices[-1], str(roll)


def inherit_affinities(father, mother, generation, birth_event_id, horse_id, policy):
    if policy is None:
        raise RacingConfigurationError('Affinity inheritance disabled: approved matrices missing')
    policy.validate()
    if type(generation) is not int or generation < 1:
        raise ValueError('Invalid child generation')
    rng, reference = stable_rng(birth_event_id,horse_id,policy.version)
    result, mutations, base_results, base_rolls, mutation_rolls = {}, [], {}, {}, {}
    for category, keys in AFFINITY_KEYS.items():
        result[category] = {}
        base_results[category],base_rolls[category],mutation_rolls[category] = {},{},{}
        for key in keys:
            f,m = father[category][key],mother[category][key]
            if f not in GRADES or m not in GRADES:
                raise RacingConfigurationError('Parent affinities must be finalized')
            initial, base_roll = recorded_choice(rng,policy.pair_weights[category][f][m],GRADES)
            base_results[category][key],base_rolls[category][key] = initial,base_roll
            grade = initial
            if policy.mutation_shifts is not None:
                shift, mutation_roll = recorded_choice(rng,policy.mutation_shifts,(-2,-1,0,1,2))
                grade = GRADES[max(0,min(len(GRADES)-1,GRADES.index(initial)-shift))]
                mutation_rolls[category][key] = {'roll':mutation_roll,'shift':shift,'result':grade}
            else:
                mutation_roll = Decimal(str(rng.random()))
                mutation_rolls[category][key] = {'roll':str(mutation_roll),'result':initial}
                if mutation_roll < Decimal(str(policy.mutation_probability)):
                    grade = weighted_choice(rng,policy.mutation_weights[initial],GRADES)
                    mutation_rolls[category][key]['result'] = grade
            if grade != initial:
                mutations.append({'category':category,'key':key,'from':initial,'to':grade})
            result[category][key] = grade
    return {'father_affinities':deepcopy(father),'mother_affinities':deepcopy(mother),
        'generation':generation,'result':result,'mutation':mutations,
        'base_result':base_results,'base_inheritance_roll':base_rolls,'mutation_roll':mutation_rolls,
        'mutation_result':deepcopy(result),'final_result':deepcopy(result),
        'algorithm_version':policy.version,'rng_reference':reference,
        'birth_event_id':birth_event_id,'horse_id':horse_id}

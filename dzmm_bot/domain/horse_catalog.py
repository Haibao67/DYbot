"""Versioned Chinese horse templates, frozen in birth_traits_json on purchase."""
import json
from pathlib import Path
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from .horse_racing import stable_rng
from .horse_rules import TRAITS, growth_for_seed

VERSION = 'horse_catalog_v1'
STAT_MULTIPLIER = Decimal('15')
STAT_VARIATION = Decimal('.20')
DISTANCES = {'短距':'short', '英里':'mile', '中距':'medium', '长距':'long'}
STYLES = {'逃':'front', '先行':'stalker', '差':'mid', '追':'closer'}


def load_catalog():
    data=json.loads(Path(__file__).with_name('horse_catalog_v1.json').read_text(encoding='utf-8'))
    rows=data['horses']
    if data['version'] != VERSION or not rows or len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Invalid horse catalog identity/version')
    for row in rows:
        if not row['name'] or row['name'] == '—' or any(c.isascii() and c.isalpha() for c in row['name']):
            raise ValueError('Horse catalog requires Chinese names')
        if set(row['scores']) != set(TRAITS) or any(type(v) is not int or not 1 <= v <= 10 for v in row['scores'].values()):
            raise ValueError('Horse catalog requires five integer scores 1..10')
        if not row['race_options'] or not set(row['race_options']) <= set(DISTANCES)|{'泥地'} or not any(x in DISTANCES for x in row['race_options']):
            raise ValueError('Invalid recommended race options')
        if not row['style_options'] or not set(row['style_options']) <= set(STYLES):
            raise ValueError('Invalid recommended styles')
    return rows


def catalog_snapshot(seed, multiplier=STAT_MULTIPLIER, variation=STAT_VARIATION, rng=None):
    """Each stat is uniform integer within score*multiplier*(1±variation).

    Selection and all draws happen once at purchase. Explicit RNG allows deterministic
    simulations; production uses a versioned seed. Growth probabilities remain P6.
    """
    multiplier=Decimal(str(multiplier)); variation=Decimal(str(variation))
    if not multiplier.is_finite() or multiplier <= 0 or not variation.is_finite() or not 0 <= variation < 1:
        raise ValueError('Invalid horse stat generation parameters')
    if multiplier*10*(1+variation) > 1000:
        raise ValueError('Horse catalog stat bounds exceed 1000')
    rng=rng if rng is not None else stable_rng(seed,VERSION)[0]
    template=rng.choice(load_catalog())
    traits={}
    for trait in TRAITS:
        center=Decimal(template['scores'][trait])*multiplier
        low=max(1,int((center*(1-variation)).to_integral_value(rounding=ROUND_CEILING)))
        high=int((center*(1+variation)).to_integral_value(rounding=ROUND_FLOOR))
        if low > high: raise ValueError('Horse stat range contains no integer')
        traits[trait]=rng.randint(low,high)
    distance=rng.choice([DISTANCES[x] for x in template['race_options'] if x in DISTANCES])
    style=rng.choice([STYLES[x] for x in template['style_options']])
    return {'sex':rng.choice(('male','female')), 'traits':traits,
        'growth':{trait:growth_for_seed(seed,trait) for trait in TRAITS},
        'catalog':{'version':VERSION,'template_id':template['id'],'name':template['name'],
            'scores':dict(template['scores']), 'multiplier':str(multiplier),'variation':str(variation),
            'recommended_distance':distance,'recommended_distance_options':[distance],
            'recommended_style':style,'recommended_style_options':[style],
            'recommended_surface':'dirt' if '泥地' in template['race_options'] else 'turf',
            'recommended_surface_options':['dirt' if '泥地' in template['race_options'] else 'turf']}}


def foal_catalog(seed, father_catalog, mother_catalog):
    """Freeze inherited distance, surface and running-style categories at conception."""
    rng, reference = stable_rng(seed, 'foal-categories', VERSION)
    selected, rolls = {}, {}
    for field in ('distance', 'surface', 'style'):
        key = 'recommended_' + field
        options_key = key + '_options'
        father_options = list(dict.fromkeys(father_catalog.get(options_key) or [father_catalog[key]]))
        mother_options = list(dict.fromkeys(mother_catalog.get(options_key) or [mother_catalog[key]]))
        if not father_options or not mother_options:
            raise ValueError('Both parents need an inherited ' + field + ' category')
        father_value, mother_value = rng.choice(father_options), rng.choice(mother_options)
        dual_roll = rng.randrange(100)
        if dual_roll < 5:
            inherited = list(dict.fromkeys((father_value, mother_value)))
            source = 'both' if len(inherited) > 1 else 'shared'
        else:
            inherited = [rng.choice((father_value, mother_value))]
            source = 'father_or_mother'
        selected[key] = inherited
        rolls[field] = {'father': father_value, 'mother': mother_value,
                        'dual_inheritance_roll': dual_roll, 'source': source}
    return {'version': 'ruihe-foal-categories-v2',
        'recommended_distance': selected['recommended_distance'][0],
        'recommended_distance_options': selected['recommended_distance'],
        'recommended_surface': selected['recommended_surface'][0],
        'recommended_surface_options': selected['recommended_surface'],
        'recommended_style': selected['recommended_style'][0],
        'recommended_style_options': selected['recommended_style'],
        'inheritance_rolls': rolls, 'rng_reference': reference}


def birth_catalog(horse):
    """Read immutable origin, including after rename/training/auction. Old horses have none."""
    return json.loads(horse.get('birth_traits_json') or '{}').get('catalog')

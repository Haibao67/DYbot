"""Versioned P6 horse growth and breeding rules."""
import hashlib
import random
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP

RULE_VERSION = "ruihe-horse-v1"
TRAITS = ("speed", "stamina", "power", "wisdom", "grit")
CAPACITIES = (3, 4, 5, 6, 7)
BUY_PRICE = Decimal("1000")
FEED_ENABLED = True
BREEDING_ENABLED = True
GROWTH_RANKS = ("S", "A", "B", "C", "D")
G0_GROWTH_WEIGHTS = (3, 12, 35, 35, 15)
GROWTH_MULTIPLIER = {"S": Decimal("1.30"), "A": Decimal("1.20"), "B": Decimal("1.00"),
                     "C": Decimal("0.90"), "D": Decimal("0.80")}
FEED_LIFETIME_LIMIT = 30
FEED_CAPACITY = 5
FOAL_SECONDS = 3 * 3600
YOUTH_SECONDS = 3 * 3600


def feed_budget(horse, now):
    anchor = horse.get('feed_recovered_at')
    anchor = horse['created_at'] if anchor is None else anchor
    charges = horse.get('feed_charges', FEED_CAPACITY)
    hours = max(0, int((now - anchor) // 3600))
    available = min(FEED_CAPACITY, charges + hours)
    # A full pool cannot bank recovery for an immediate second refill.
    next_anchor = now if available == FEED_CAPACITY else anchor + hours * 3600
    return available, next_anchor
BREEDING_LIMIT = 5
BREEDING_PRICE = Decimal(150)
BREEDING_GRASS = 2
GESTATION_SECONDS = 6 * 3600
MALE_COOLDOWN_SECONDS = 12 * 3600
FEMALE_COOLDOWN_SECONDS = 24 * 3600
INHERITANCE_FACTOR = Decimal("0.35")
INHERITANCE_VARIATION = Decimal("0.15")
MAIN_PARENT_WEIGHT = 65
QUALITY_WEIGHTS = (80, 17, 3)
QUALITY_MULTIPLIERS = (Decimal("1"), Decimal("1.1"), Decimal("1.2"))
GROWTH_SOURCE_WEIGHTS = (40, 40, 20)
PLUS_TWO_MUTATION_PERCENT = 1
FOAL_STAT_FACTOR = Decimal("1.2")
FOAL_AFFINITY_UPGRADE_PERCENT = 10
FOAL_CATEGORY_DUAL_INHERIT_PERCENT = 5
FOAL_INHERITANCE_VERSION = "ruihe-foal-inheritance-v2"


def upgrade_cost(level):
    if not 1 <= level < len(CAPACITIES):
        raise ValueError("stable already at maximum level")
    return (Decimal(400) * Decimal("1.5") ** (level - 1)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def stage(born_at, now):
    age = max(0, now - born_at)
    return "幼驹" if age < FOAL_SECONDS else "青年" if age < FOAL_SECONDS + YOUTH_SECONDS else "成年"


def g0_snapshot(seed):
    """Roll once at purchase and persist the result; no panel-time randomness."""
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest(), "big"))
    target = rng.randint(550, 700)
    values = {trait: 80 for trait in TRAITS}
    for _ in range(target - 400):
        choices = [trait for trait in TRAITS if values[trait] < 160]
        values[rng.choice(choices)] += 1
    return {"sex": rng.choice(("male", "female")), "traits": values,
            "growth": {trait: growth_for_seed(seed, trait) for trait in TRAITS}}


def growth_for_seed(seed, trait):
    """Stable distribution also used to backfill pre-rule U ranks."""
    digest = hashlib.sha256(f"{RULE_VERSION}:growth:{seed}:{trait}".encode()).digest()
    roll = int.from_bytes(digest[:8], "big") % 100
    for rank, weight in zip(GROWTH_RANKS, G0_GROWTH_WEIGHTS):
        if roll < weight:
            return rank
        roll -= weight
    return "D"


def training_roll(seed, feed_code, horse, index):
    """Deterministic base gains; the caller persists the result once."""
    rng = random.Random(int.from_bytes(hashlib.sha256(f"{seed}:{feed_code}:{index}".encode()).digest(), "big"))
    if feed_code == "hay":
        chosen = rng.sample(TRAITS, 2)
        base = {trait: rng.randint(3, 5) for trait in chosen}
    else:
        primary = {"swift_oats": "speed", "energy_hay": "stamina", "strong_carrot": "power",
                   "wise_apple": "wisdom", "tough_grain": "grit"}[feed_code]
        secondary = {"swift_oats": "power", "energy_hay": "grit", "strong_carrot": "speed",
                     "tough_grain": "stamina"}.get(feed_code)
        if secondary is None:
            secondary = rng.choice([trait for trait in TRAITS if trait != primary])
        base = {primary: rng.randint(8, 12),
                secondary: 2 if feed_code == "wise_apple" else rng.randint(2, 4)}
    gains = {}
    for trait, amount in base.items():
        current = horse[trait]
        soft = Decimal("1") if current < 600 else Decimal("0.7") if current < 800 else (
            Decimal("0.4") if current < 900 else Decimal("0.2"))
        growth = GROWTH_MULTIPLIER[horse[f"growth_{trait}"]]
        gain = int((Decimal(amount) * growth * soft).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        gains[trait] = min(max(0, 1000 - current), max(1, gain))
    return gains


def foal_snapshot(seed, mother, father):
    """Calculate and freeze foal stats, growth ranks and name at conception."""
    rng = random.Random(int.from_bytes(hashlib.sha256(seed.encode()).digest(), "big"))
    traits, growth, sources, ranges = {}, {}, {}, {}
    for trait in TRAITS:
        low = min(mother[trait], father[trait])
        high = int((Decimal(max(mother[trait], father[trait])) * FOAL_STAT_FACTOR)
                   .to_integral_value(rounding=ROUND_FLOOR))
        high = max(low, high)
        traits[trait] = rng.randint(low, high)
        ranges[trait] = [low, high]
        sources[trait] = "parent_range"
        source_kind = rng.choices(("father", "mother", "mutation"), weights=GROWTH_SOURCE_WEIGHTS, k=1)[0]
        if source_kind == "mutation":
            parents = (GROWTH_RANKS.index(mother[f"growth_{trait}"]),
                       GROWTH_RANKS.index(father[f"growth_{trait}"]))
            baseline = rng.choice(parents)
            shift = -2 if rng.randrange(100) < PLUS_TWO_MUTATION_PERCENT else rng.choice((-1, 1))
            growth[trait] = GROWTH_RANKS[max(0, min(4, baseline + shift))]
        else:
            growth[trait] = (father if source_kind == "father" else mother)[f"growth_{trait}"]
    name, name_pattern = inherited_foal_name(rng, mother.get("name"), father.get("name"))
    return {"sex": rng.choice(("male", "female")), "traits": traits, "growth": growth,
            "trait_ranges": ranges, "sources": sources, "name": name,
            "name_pattern": name_pattern}


def inherited_foal_name(rng, mother_name, father_name):
    """Combine one parent's first half with the other parent's second half."""
    def halves(value):
        name = "".join(str(value or "").split())
        midpoint = (len(name) + 1) // 2
        return name[:midpoint], name[midpoint:]

    mother_front, mother_back = halves(mother_name)
    father_front, father_back = halves(father_name)
    candidates = [(father_front + mother_back, "father_front+mother_back"),
                  (mother_front + father_back, "mother_front+father_back")]
    candidates = [candidate for candidate in candidates if candidate[0]]
    if not candidates:
        return "新驹", "fallback"
    return rng.choice(candidates)

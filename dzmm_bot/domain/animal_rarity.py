"""Versioned purchase rarity and harvest-base rules for ranch animals."""
import hashlib
from decimal import Decimal

RARITY_VERSION = "ranch-rarity-v2"
PREMIUM_PROBABILITY = Decimal("0.20")
BASE_PRODUCTION_QUANTITY = 1
SHINY_ANIMAL_ITEMS = {"闪光鸡": "shiny_chicken", "闪光羊": "shiny_sheep", "闪光牛": "shiny_cow"}
ANIMAL_PRODUCTION_BASES = {}
RARITY_RULESETS = {
    "ranch-rarity-v1": {
        "shiny": {"name": "闪光", "emoji": "✨", "purchase_probability": Decimal("0.10"),
                  "harvest_probability": Decimal("0.05"), "base_bonus": 1},
    },
}
RARITY_RULESETS[RARITY_VERSION] = {code: dict(rule) for code, rule in RARITY_RULESETS["ranch-rarity-v1"].items()}


def animal_production_base(animal_type):
    return ANIMAL_PRODUCTION_BASES.get(animal_type, BASE_PRODUCTION_QUANTITY)


def rarity_draw(seed, context):
    """Stable draw in [0, 1); caller seeds with its secret and immutable IDs."""
    number = int(hashlib.sha256(f"{seed}:{context}".encode()).hexdigest()[:16], 16)
    return Decimal(number) / Decimal(16 ** 16)


def purchase_rarity(draw, version=RARITY_VERSION):
    threshold = Decimal(0)
    for code, rule in RARITY_RULESETS[version].items():
        threshold += rule["purchase_probability"]
        if Decimal(str(draw)) < threshold:
            return code
    return "normal"


def increase_probability(counts, premium=False, version=RARITY_VERSION):
    return sum((max(0, counts.get(code, 0)) * rule["harvest_probability"]
                for code, rule in RARITY_RULESETS[version].items()), Decimal(0)) + (PREMIUM_PROBABILITY if premium else Decimal(0))


def harvest_base_bonus(counts, draws, version=RARITY_VERSION, premium_probability=Decimal(0)):
    """Stack probability linearly: guaranteed hits plus one remainder roll."""
    bonus = 0
    if version == RARITY_VERSION:
        probability = increase_probability(counts, version=version) + Decimal(premium_probability)
        guaranteed = int(probability)
        return guaranteed + int(probability > guaranteed and Decimal(str(draws["increase"])) < probability - guaranteed)
    for code, rule in RARITY_RULESETS[version].items():
        probability = max(0, counts.get(code, 0)) * rule["harvest_probability"]
        guaranteed = int(probability)
        remainder = probability - guaranteed
        extra = int(remainder > 0 and Decimal(str(draws[code])) < remainder)
        bonus += (guaranteed + extra) * rule["base_bonus"]
    return bonus

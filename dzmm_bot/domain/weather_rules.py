"""Versioned daily world weather and deterministic production adjustments."""
from datetime import date, datetime
from hashlib import sha256
import random
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Asia/Hong_Kong")
WEATHER_RULE_VERSION = "ruihe-weather-v1"
PROBABILITY_CONFIG_VERSION = "ruihe-weather-odds-v1"
WEATHER_ENABLED_DEFAULT = False
WEATHER = {
    "sunny": {"name": "晴天", "emoji": "☀️", "weight": 1900},
    "rainy": {"name": "雨季", "emoji": "🌧️", "weight": 1900},
    "drought": {"name": "旱季", "emoji": "🏜️", "weight": 1900},
    "humid": {"name": "闷热", "emoji": "🥵", "weight": 1900},
    "breeze": {"name": "微风", "emoji": "🍃", "weight": 1900},
    "harvest_festival": {"name": "丰收祭", "emoji": "🎊", "weight": 500},
}
FODDER_CROPS = frozenset({"grass", "oats", "carrot", "barley", "alfalfa", "apple"})
ECONOMIC_CROPS = frozenset({"wheat", "sunflower", "cotton_crop", "grape"})
ANIMAL_PRODUCTS = frozenset({"egg", "wool", "milk"})


def validate_weather_config():
    if set(WEATHER) != {"sunny", "rainy", "drought", "humid", "breeze", "harvest_festival"}:
        raise ValueError("weather codes are incomplete")
    if any(type(row["weight"]) is not int or row["weight"] < 0 for row in WEATHER.values()):
        raise ValueError("weather weights must be non-negative integers")
    if sum(row["weight"] for row in WEATHER.values()) != 10000:
        raise ValueError("weather weights must total 10000 basis points")


def game_date(timestamp):
    return datetime.fromtimestamp(timestamp, TIMEZONE).date()


def date_bounds(day):
    start = datetime.combine(day, datetime.min.time(), TIMEZONE).timestamp()
    end = datetime.combine(date.fromordinal(day.toordinal() + 1), datetime.min.time(), TIMEZONE).timestamp()
    return start, end


def daily_seed(day):
    return sha256(f"ruihe-weather:{PROBABILITY_CONFIG_VERSION}:{day.isoformat()}".encode()).hexdigest()


def choose_weather(day):
    validate_weather_config()
    rng = random.Random(int(daily_seed(day), 16))
    draw = rng.randrange(10000)
    for code, rule in WEATHER.items():
        if draw < rule["weight"]:
            return code
        draw -= rule["weight"]
    raise AssertionError("validated weather distribution did not select a result")


def effect_percent(weather_code, *, product=None, crop_code=None):
    if weather_code not in WEATHER:
        return 0
    if weather_code == "sunny":
        return 50 if product in ANIMAL_PRODUCTS or crop_code in FODDER_CROPS else 0
    if weather_code == "rainy":
        return 80 if product == "wool" else 50 if crop_code in ECONOMIC_CROPS else 0
    if weather_code == "drought":
        return -30 if product == "egg" else -50 if crop_code in FODDER_CROPS | ECONOMIC_CROPS else 0
    if weather_code == "humid":
        return -30 if product == "milk" else 20 if crop_code in FODDER_CROPS else 0
    if weather_code == "breeze":
        return 20 if crop_code in ECONOMIC_CROPS else 0
    if weather_code == "harvest_festival":
        return 150 if product in ANIMAL_PRODUCTS or crop_code in FODDER_CROPS | ECONOMIC_CROPS else 0
    return 0


def adjust_base(base, percent, *, seed, key):
    """Apply guaranteed 100% increments and one deterministic remainder draw.

    Positive weather adds units; negative weather removes at most one, never below zero.
    """
    if type(base) is not int or base < 0:
        raise ValueError("production base must be a non-negative integer")
    if type(percent) is not int or abs(percent) > 10000:
        raise ValueError("weather probability is invalid")
    if not base or not percent:
        return base, 0
    rng_seed = int.from_bytes(sha256(f"{seed}:{key}:{WEATHER_RULE_VERSION}".encode()).digest()[:8], "big")
    rng = random.Random(rng_seed)
    if percent > 0:
        guaranteed, remainder = divmod(percent, 100)
        adjustment = guaranteed + int(rng.randrange(100) < remainder)
        return base + adjustment, adjustment
    reduction = int(rng.randrange(100) < -percent)
    adjustment = -min(base, reduction)
    return base + adjustment, adjustment



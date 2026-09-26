import hashlib
import hmac
import math
from datetime import datetime, timedelta, timezone

HK = timezone(timedelta(hours=8), "Asia/Hong_Kong")
BASE_CONFIG = {"starting_balance": 100, "relief_floor": 100,
               "timezone": "Asia/Hong_Kong", "currency": "winter_coin"}
RANCH_CONFIG = {**BASE_CONFIG, "tax_bps": 500, "feed_price": 2,
    "production_hours": 48, "max_level": 9, "capacity_base": 3,
    "capacity_per_level": 2, "upgrade_base": 200, "upgrade_multiplier": 1.5,
    "interval_level_bonus": 0.1, "market_amplitude": 0.15,
    "animals": {"chicken": {"space": 1, "price": 50, "feed": 1, "hours": [2, 4], "product": "egg"},
                "sheep": {"space": 2, "price": 120, "feed": 2, "hours": [10, 14], "product": "wool"},
                "cow": {"space": 3, "price": 300, "feed": 3, "hours": [6, 10], "product": "milk"}},
    "products": {"egg": {"price": 5, "phase": 0}, "wool": {"price": 20, "phase": math.pi / 3},
                 "milk": {"price": 15, "phase": 2 * math.pi / 3}}}


class GameError(Exception):
    def __init__(self, code, **details):
        self.code, self.details = code, details
        super().__init__(code)


def quantity(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 6:
        raise GameError("quantity")
    result = int(value)
    if not 1 <= result <= 100000:
        raise GameError("quantity")
    return result


def tax(gross, config):
    return (gross * config["tax_bps"] + 9999) // 10000


def capacity(level, config):
    return config["capacity_base"] + config["capacity_per_level"] * level


def upgrade_price(level, config):
    return round(config["upgrade_base"] * config["upgrade_multiplier"] ** (level - 1))


def local_date(now):
    return datetime.fromtimestamp(now, HK).date().isoformat()


def market_price(product, now, config):
    dt = datetime.fromtimestamp(now, HK)
    hour = dt.hour + dt.minute / 60 + dt.second / 3600 + dt.microsecond / 3600000000
    rule = config["products"][product]
    return max(1, math.floor(rule["price"] * (1 + config["market_amplitude"] * math.sin(2 * math.pi * hour / 24 + rule["phase"]))))


def seed_for(secret, business_id, version, nonce):
    return hmac.new(secret.encode(), f"{business_id}:{version}:{nonce}".encode(), hashlib.sha256).hexdigest()


def interval(seed, sequence, animal, level, config):
    fraction = int(hashlib.sha256(f"{seed}:{sequence}".encode()).hexdigest()[:13], 16) / (16 ** 13 - 1)
    lo, hi = config["animals"][animal]["hours"]
    base = lo + (hi - lo) * fraction
    return base, base * 3600 / (1 + config["interval_level_bonus"] * (level - 1))

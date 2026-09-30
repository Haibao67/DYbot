import hashlib
import hmac
import math
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING, ROUND_FLOOR
from statistics import NormalDist
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
                 "milk": {"price": 18, "phase": 2 * math.pi / 3}}}
RUIHE_RANCH_VERSION = "ruihe-ranch-v1"
RUIHE_RANCH_CONFIG = {**RANCH_CONFIG, "production_hours": 12,
    "animals": {**RANCH_CONFIG["animals"],
        "chicken": {**RANCH_CONFIG["animals"]["chicken"], "hours": [3, 5]},
        "sheep": {**RANCH_CONFIG["animals"]["sheep"], "hours": [9, 12]},
        "cow": {**RANCH_CONFIG["animals"]["cow"], "hours": [6, 10]}}}
WEATHER_MULTIPLIERS = {
    "sunny": {"egg": 1.2, "wool": 1.2, "milk": 1.2},
    "rainy": {"wool": 1.5}, "drought": {"egg": 0.7},
    "humid": {"milk": 0.8}, "breeze": {},
    "harvest_festival": {"egg": 1.5, "wool": 1.5, "milk": 1.5},
}


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


MARKET_STDDEV = Decimal('0.1667')
MARKET_BOUND = Decimal('0.50')
MARKET_SMOOTH_WINDOWS = 12


def market_deviation(product, window):
    """Correlated normal samples; reject tails outside ±50%.

    Twelve overlapping unit-normal samples retain unit marginal variance after
    normalization. Adjacent untruncated windows have correlation 11/12.
    All seeds are local to the product/window/retry, requiring no mutable state.
    """
    normal = NormalDist()
    scale = Decimal(MARKET_SMOOTH_WINDOWS).sqrt()
    for attempt in range(100):
        total = Decimal(0)
        for tick in range(window - MARKET_SMOOTH_WINDOWS + 1, window + 1):
            digest = hashlib.sha256(f'ruihe-market-v3-normal:{product}:{tick}:{attempt}'.encode()).digest()
            # Midpoints of 52-bit bins stay strictly inside (0, 1).
            uniform = ((int.from_bytes(digest[:8], 'big') >> 12) + 0.5) / (2 ** 52)
            total += Decimal(str(normal.inv_cdf(uniform)))
        deviation = total / scale * MARKET_STDDEV
        if -MARKET_BOUND <= deviation <= MARKET_BOUND:
            return deviation
    raise GameError('unavailable')


def market_price(product, now, config):
    """Global thirty-minute prices with smooth, bounded normal deviations."""
    rule = config["products"].get(product)
    if rule is None:
        raise GameError("market_item", item=product)
    if rule.get("fixed"):
        return Decimal(str(rule["price"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    window = int(Decimal(str(now)) // 1800)
    base = Decimal(str(rule['price']))
    price = (base * (1 + market_deviation(product, window))).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    lower = (base * (1 - MARKET_BOUND)).quantize(Decimal('0.01'), rounding=ROUND_CEILING)
    upper = (base * (1 + MARKET_BOUND)).quantize(Decimal('0.01'), rounding=ROUND_FLOOR)
    return min(upper, max(lower, price))


def market_window(now):
    """Return the UTC epoch tick id and seconds until the next tick."""
    seconds = Decimal(str(now))
    window = int(seconds // 1800)
    return window, Decimal((window + 1) * 1800) - Decimal(str(now))


def market_trend(current, previous, base_price):
    if current > previous:
        arrow = "📈"
    elif current < previous:
        arrow = "📉"
    else:
        arrow = "➡️"
    ratio = Decimal(current) / Decimal(base_price)
    if ratio <= Decimal("0.65"):
        label = "低迷"
    elif ratio <= Decimal("0.85"):
        label = "偏低"
    elif ratio <= Decimal("1.15"):
        label = "平稳"
    elif ratio <= Decimal("1.35"):
        label = "走强"
    else:
        label = "高涨"
    return arrow, label


def market_change_percent(current, base_price):
    """Signed change from the configured base price, rounded to one decimal."""
    change = ((Decimal(current) / Decimal(str(base_price)) - 1) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP)
    return Decimal("0.0") if change == 0 else change


def to_minor(amount):
    """Convert coin units to persisted cents using an explicit half-up rule."""
    try:
        value = Decimal(str(amount))
    except Exception as exc:
        raise GameError("amount") from exc
    if not value.is_finite() or abs(value) > Decimal("10000000000000"):
        raise GameError("amount")
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_minor(amount):
    return (Decimal(int(amount)) / 100).quantize(Decimal("0.01"))


def market_fee(gross):
    return (Decimal(str(gross)) * Decimal("0.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def seed_for(secret, business_id, version, nonce):
    return hmac.new(secret.encode(), f"{business_id}:{version}:{nonce}".encode(), hashlib.sha256).hexdigest()


def interval(seed, sequence, animal, level, config):
    fraction = int(hashlib.sha256(f"{seed}:{sequence}".encode()).hexdigest()[:13], 16) / (16 ** 13 - 1)
    lo, hi = config["animals"][animal]["hours"]
    base = lo + (hi - lo) * fraction
    return base, base * 3600 * ranch_duration_multiplier(level)


def ranch_duration_multiplier(level):
    return 1 - 0.05 * (max(1, min(9, int(level))) - 1)


def production_multiplier(affection=0, feed_streak=0, premium_feed=False,
                          weather="breeze", product=None, future_buff=1.0):
    weather_factor = WEATHER_MULTIPLIERS.get(weather, {}).get(product, 1.0)
    return (1.1 if feed_streak >= 3 else 1.0) \
        * weather_factor * future_buff


def affection_speed(affection):
    return 1 + 0.01 * max(0, min(10, int(affection)))


def production_quantity(base_quantity, multiplier):
    """Use one consistent half-up integer rule for every animal batch."""
    return max(0, int((Decimal(str(base_quantity)) * Decimal(str(multiplier))).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP)))

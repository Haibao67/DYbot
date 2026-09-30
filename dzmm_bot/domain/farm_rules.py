"""Versioned P7 farm and horse-feed economy rules."""
import hashlib
from decimal import Decimal, ROUND_HALF_UP

RULE_VERSION = "ruihe-farm-v1"
ECONOMIC_RULE_VERSION = "ruihe-farm-economy-v1"
PLOT_CAPACITY = (4, 6, 8, 10, 12, 15, 18, 21, 24)
UPGRADE_COSTS = {level: (Decimal(300) * Decimal("1.5") ** (level - 1)).quantize(
    Decimal("0.01"), rounding=ROUND_HALF_UP) for level in range(1, 9)}
CROPS = {
    "grass": {"name": "牧草", "seed_price": 3, "hours": Decimal("1.5"), "yield": (3, 5)},
    "oats": {"name": "燕麦", "seed_price": 4, "hours": Decimal("2"), "yield": (3, 5)},
    "carrot": {"name": "胡萝卜", "seed_price": 5, "hours": Decimal("3"), "yield": (3, 4)},
    "barley": {"name": "大麦", "seed_price": 5, "hours": Decimal("4"), "yield": (3, 5)},
    "alfalfa": {"name": "苜蓿", "seed_price": 7, "hours": Decimal("5"), "yield": (2, 4)},
    "apple": {"name": "苹果", "seed_price": 8, "hours": Decimal("6"), "yield": (2, 3)},
    "wheat": {"name": "小麦", "emoji": "🌾", "seed_price": 5, "hours": Decimal("2.5"), "yield": (3, 5),
              "market_base_price": 3},
    "sunflower": {"name": "向日葵", "emoji": "🌻", "seed_price": 7, "hours": Decimal("4"), "yield": (3, 4),
                  "market_base_price": 5},
    "cotton_crop": {"name": "棉花", "emoji": "🧵", "seed_price": 9, "hours": Decimal("6"), "yield": (2, 4),
                    "market_base_price": 7},
    "grape": {"name": "葡萄", "emoji": "🍇", "seed_name": "葡萄苗", "seed_price": 10, "hours": Decimal("7"), "yield": (2, 4),
              "market_base_price": 8},
}
ECONOMIC_CROPS = {code: rule for code, rule in CROPS.items() if "market_base_price" in rule}
CROP_SELL_PRICES = {code: Decimal(rule["seed_price"]) * Decimal("1.5")
                    for code, rule in CROPS.items() if code not in ECONOMIC_CROPS}
FEEDS = {
    "hay": {"name": "普通草料", "price": 12, "ingredients": {"grass": 2}},
    "swift_oats": {"name": "疾风燕麦", "price": 28, "ingredients": {"oats": 2, "alfalfa": 1}},
    "energy_hay": {"name": "高能牧草", "price": 28, "ingredients": {"grass": 2, "alfalfa": 1}},
    "strong_carrot": {"name": "强壮胡萝卜", "price": 28, "ingredients": {"carrot": 2, "alfalfa": 1}},
    "wise_apple": {"name": "聪慧苹果", "price": 30, "ingredients": {"apple": 2, "alfalfa": 1}},
    "tough_grain": {"name": "坚韧谷物", "price": 28, "ingredients": {"barley": 2, "alfalfa": 1}},
    "premium_grass": {"name": "优质牧草", "price": 55, "ingredients": {"grass": 4, "alfalfa": 2, "oats": 1},
                      "daily_limit": 4},
}

DIRECT_FEED_BUY_MULTIPLIER = 100


def feed_shop_price(feed_code):
    """Direct shop purchase costs one hundred times the feed's reference price."""
    return FEEDS[feed_code]["price"] * DIRECT_FEED_BUY_MULTIPLIER


CROP_NAMES = {v["name"]: k for k, v in CROPS.items()}
FEED_NAMES = {v["name"]: k for k, v in FEEDS.items()}


def crop_yield(batch_id, crop_code, low, high, version=RULE_VERSION):
    digest = hashlib.sha256(f"{version}:{batch_id}:{crop_code}".encode()).digest()
    return low + int.from_bytes(digest[:8], "big") % (high - low + 1)


def crop_weather_snapshot(crop_code, weather="disabled"):
    """Sowing keeps maturity weather-neutral; yield weather is resolved on harvest."""
    if crop_code not in CROPS:
        raise ValueError("unknown crop code")
    return Decimal("1"), Decimal("1")

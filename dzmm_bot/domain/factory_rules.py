"""Versioned processing recipes and factory progression policy."""
from decimal import Decimal

RECIPE_VERSION = "ruihe-factory-v1"
MAX_FACTORY_LEVEL = 5

# The plan specifies the Lv5 endpoint (five lines at ×0.60). Intermediate steps
# use a single progression rule so the table is centralized and independently testable.
FACTORY_LEVELS = {
    level: {
        "line_count": level,
        "duration_multiplier": Decimal("1.0") - Decimal("0.1") * (level - 1),
    }
    for level in range(1, MAX_FACTORY_LEVEL + 1)
}

FACTORY_UPGRADE_REQUIREMENTS = {
    new_level: {"coins": Decimal(200) * Decimal("1.5") ** (new_level - 2), "materials": {}}
    for new_level in range(2, MAX_FACTORY_LEVEL + 1)
}

RECIPES = {
    "cake": {"display_name": "蛋糕", "tier": 1, "ingredients": {"egg": 3},
             "output_item": "cake", "output_quantity": 1, "base_duration": 3600,
             "factory_level_required": 1, "market_mode": "fixed", "base_price": 18,
             "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0")},
    "sweater": {"display_name": "毛衣", "tier": 1, "ingredients": {"wool": 2},
                "output_item": "sweater", "output_quantity": 1, "base_duration": 3600,
                "factory_level_required": 1, "market_mode": "fixed", "base_price": 50,
                "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0")},
    "cheese": {"display_name": "奶酪", "tier": 1, "ingredients": {"milk": 2},
               "output_item": "cheese", "output_quantity": 1, "base_duration": 3600,
               "factory_level_required": 1, "market_mode": "fixed", "base_price": 40,
               "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0")},
    "bread": {"display_name": "面包", "tier": 1, "ingredients": {"wheat": 3},
              "output_item": "bread", "output_quantity": 1, "base_duration": 3600,
              "factory_level_required": 1, "market_mode": "dynamic", "base_price": 22,
              "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0")},
    "sunflower_oil": {"display_name": "葵花油", "tier": 1, "ingredients": {"sunflower": 3},
                      "output_item": "sunflower_oil", "output_quantity": 1, "base_duration": 5400,
                      "factory_level_required": 1, "market_mode": "dynamic", "base_price": 32,
                      "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0")},
    "grape_jam": {"display_name": "葡萄果酱", "tier": 1, "ingredients": {"grape": 3},
                  "output_item": "grape_jam", "output_quantity": 1, "base_duration": 7200,
                  "factory_level_required": 1, "market_mode": "dynamic", "base_price": 48,
                  "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0")},
    "cotton_cloth": {"display_name": "棉布", "tier": 1, "ingredients": {"cotton_crop": 3},
                     "output_item": "cotton_cloth", "output_quantity": 1, "base_duration": 7200,
                     "factory_level_required": 1, "market_mode": "dynamic", "base_price": 42,
                     "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0")},
    "gift_box": {"display_name": "礼盒", "tier": 2, "ingredients": {"cake": 2, "cheese": 1},
                 "output_item": "gift_box", "output_quantity": 1, "base_duration": 7200,
                 "factory_level_required": 2, "market_mode": "dynamic", "base_price": 120,
                 "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0.05")},
    "down_coat": {"display_name": "羽绒服", "tier": 2, "ingredients": {"sweater": 2, "wool": 1},
                  "output_item": "down_coat", "output_quantity": 1, "base_duration": 7200,
                  "factory_level_required": 2, "market_mode": "dynamic", "base_price": 160,
                  "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0.05")},
    "cheese_platter": {"display_name": "奶酪拼盘", "tier": 2, "ingredients": {"cheese": 2, "egg": 1},
                       "output_item": "cheese_platter", "output_quantity": 1, "base_duration": 7200,
                       "factory_level_required": 2, "market_mode": "dynamic", "base_price": 120,
                       "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0.05")},
    "sandwich": {"display_name": "精致三明治", "tier": 2,
                 "ingredients": {"bread": 2, "egg": 1, "cheese": 1},
                 "output_item": "sandwich", "output_quantity": 1, "base_duration": 7200,
                 "factory_level_required": 2, "market_mode": "dynamic", "base_price": 95,
                 "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0.05")},
    "salad": {"display_name": "调味沙拉", "tier": 2,
              "ingredients": {"sunflower_oil": 1, "carrot": 2, "apple": 1},
              "output_item": "salad", "output_quantity": 1, "base_duration": 7200,
              "factory_level_required": 2, "market_mode": "dynamic", "base_price": 85,
              "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0.05")},
    "fruit_cake": {"display_name": "水果蛋糕", "tier": 2,
                   "ingredients": {"cake": 1, "grape_jam": 1, "apple": 1},
                   "output_item": "fruit_cake", "output_quantity": 1, "base_duration": 9000,
                   "factory_level_required": 2, "market_mode": "dynamic", "base_price": 105,
                   "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0.05")},
    "cotton_workwear": {"display_name": "棉质工装", "tier": 2,
                        "ingredients": {"cotton_cloth": 2, "wool": 1},
                        "output_item": "cotton_workwear", "output_quantity": 1, "base_duration": 9000,
                        "factory_level_required": 2, "market_mode": "dynamic", "base_price": 110,
                        "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0.05")},
    "grand_gift": {"display_name": "大礼包", "tier": 3,
                   "ingredients": {"gift_box": 1, "down_coat": 1, "cheese_platter": 1},
                   "output_item": "grand_gift", "output_quantity": 1, "base_duration": 14400,
                   "factory_level_required": 4, "market_mode": "dynamic", "base_price": 500,
                   "failure_rate": Decimal("0.04"), "critical_rate": Decimal("0.10")},
}

RECIPE_NAMES = {recipe["display_name"]: recipe_id for recipe_id, recipe in RECIPES.items()}


def dynamic_market_bases():
    return {recipe["output_item"]: recipe["base_price"] for recipe in RECIPES.values()
            if recipe["market_mode"] == "dynamic" and recipe["base_price"] is not None}


def seeded_roll(secret, job_id, recipe_version, purpose):
    import hashlib
    import hmac

    raw = hmac.new(secret.encode(), f"{job_id}:{recipe_version}:{purpose}".encode(), hashlib.sha256).digest()
    return Decimal(int.from_bytes(raw[:8], "big")) / Decimal(2**64)


def progress(started_at, finish_at, now):
    span = max(0, finish_at - started_at)
    if span == 0:
        return 1.0 if now >= finish_at else 0.0
    return max(0.0, min(1.0, (now - started_at) / span))

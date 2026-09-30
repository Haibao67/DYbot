"""Versioned Buff and minimal shop-crafting policies for Ruihe P5."""
from decimal import Decimal

BUFF_VERSION = "ruihe-buffs-v1"

BUFFS = {
    "brush": {"name": "毛刷", "emoji": "🔨", "description": "亲密增速 ×2", "source": "blacksmith", "duration": 3 * 86400,
              "magnitude": Decimal("2"), "effects": {"affection_growth": Decimal("2")}, "policy": "extend",
              "inputs": {"wool": 2}},
    "copper_bell": {"name": "铜铃", "emoji": "🔔", "description": "牧场产出 +12%", "source": "blacksmith", "duration": 3 * 86400,
                     "magnitude": Decimal("1.12"), "effects": {"ranch_output": Decimal("1.12")}, "policy": "extend",
                     "inputs": {"egg": 3, "milk": 2}},
    "greenhouse": {"name": "暖棚", "emoji": "🏡", "description": "免疫负面天气", "source": "blacksmith", "duration": 7 * 86400,
                   "magnitude": Decimal("1"), "effects": {"negative_weather_immunity": Decimal("1")}, "policy": "extend",
                   "inputs": {"wool": 2, "milk": 2}},
    "opening_feast": {"name": "开工宴", "emoji": "🍳", "description": "牧场产出 +30%", "source": "restaurant", "duration": 86400,
                      "magnitude": Decimal("1.30"), "effects": {"ranch_output": Decimal("1.30")}, "policy": "extend",
                      "inputs": {"cake": 1, "cheese": 1}},
    "master_meal": {"name": "大师餐", "emoji": "👨‍🍳", "description": "加工不失败", "source": "restaurant", "duration": 86400,
                    "magnitude": Decimal("0"), "effects": {"processing_failure_immunity": Decimal("1")}, "policy": "extend",
                    "inputs": {"cheese_platter": 1}},
    "dress": {"name": "礼服", "emoji": "👗", "description": "指定销售渠道卖价 +10%", "source": "tailor", "duration": 3 * 86400,
              "magnitude": Decimal("1.10"), "effects": {"sell_price:caravan": Decimal("1.10"),
                  "sell_price:restaurant": Decimal("1.10"), "sell_price:foreign_trade": Decimal("1.10")},
              "policy": "extend", "inputs": {"down_coat": 1}},
    "work_apron": {"name": "工装围裙", "emoji": "🧥", "description": "加工提速 25%", "source": "tailor", "duration": 3 * 86400,
                   "magnitude": Decimal("0.75"), "effects": {"processing_duration": Decimal("0.75")},
                   "policy": "extend", "inputs": {"wool": 2}},
}

BUFF_NAMES = {rule["name"]: item for item, rule in BUFFS.items()}
BUFF_REFUND_RATES = ((Decimal("0.33"), Decimal("0.80")),
                     (Decimal("0.66"), Decimal("0.50")),
                     (Decimal("1"), Decimal("0.20")))
EXPEDITE_COSTS = (Decimal("10"), Decimal("20"), Decimal("40"))
MAX_EXPEDITES_PER_DAY = 3
FACTORY_FAILURE_LEVEL_MULTIPLIERS = {1: Decimal("1"), 2: Decimal("1"), 3: Decimal("1"),
                                    4: Decimal("1"), 5: Decimal("1"), 6: Decimal("1")}


def calculate_failure_rate(base_rate, factory_level, tier, master_meal=False):
    level_multiplier = FACTORY_FAILURE_LEVEL_MULTIPLIERS.get(int(factory_level), Decimal("1"))
    tier_multiplier = Decimal("0.5") if int(tier) >= 3 else Decimal("1")
    if master_meal:
        return Decimal("0")
    rate = Decimal(str(base_rate)) * level_multiplier * tier_multiplier
    return max(Decimal("0"), min(Decimal("1"), rate))

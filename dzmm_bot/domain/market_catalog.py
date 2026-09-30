"""One source for recipe and economic-crop market registration."""
from dzmm_bot.domain.factory_rules import RECIPES
from dzmm_bot.domain.farm_rules import ECONOMIC_CROPS


def with_tradeable_products(config):
    products = config["products"]
    for recipe in RECIPES.values():
        base = recipe["base_price"]
        if base is not None:
            products.setdefault(recipe["output_item"], {
                "price": base, "phase": 0, "fixed": recipe["market_mode"] == "fixed"})
    for code, rule in ECONOMIC_CROPS.items():
        products.setdefault(code, {"price": rule["market_base_price"],
                                   "phase": 0, "fixed": False})
    return config

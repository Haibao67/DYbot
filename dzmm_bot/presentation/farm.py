from dzmm_bot.domain.farm_rules import CROPS, FEEDS, PLOT_CAPACITY, feed_shop_price
from dzmm_bot.presentation.formatters import bar, duration, money

CROP_EMOJI = {
    "grass": "🌿", "oats": "🌾", "carrot": "🥕", "barley": "🌾",
    "alfalfa": "🍀", "apple": "🍎", "wheat": "🌾", "sunflower": "🌻",
    "cotton_crop": "🧵", "grape": "🍇",
}
FEED_EMOJI = {
    "hay": "🌿", "swift_oats": "🌾", "energy_hay": "🌿",
    "strong_carrot": "🥕", "wise_apple": "🍎", "tough_grain": "🌾",
    "premium_grass": "🌿",
}


def render_farm(result):
    kind = result["kind"]
    if kind == "farm_details":
        lines = ["🌱 农田详情"]
        for row in result["plots"]:
            code = row["crop_code"]
            state = "空闲" if code is None else ("已成熟" if row["ready_at"] <= result["now"] else duration(row["ready_at"] - result["now"]))
            lines.append(f"{row['plot_no']}号 {CROPS[code]['name'] if code else ''}｜{state}")
        lines.append("/农场 | /收获 | /播种")
        return "\n".join(lines)
    if kind == "farm_panel":
        occupied = sum(row["crop_code"] is not None for row in result["plots"])
        upgrade = f"升级{money(result['upgrade_cost'])}币" if result.get("upgrade_cost") is not None else "已满级"
        lines = [f"🌱 我的农场｜Lv.{result['level']}｜工时×{result.get('level_duration_multiplier',1):.2f}｜{upgrade}｜土地{occupied}/{PLOT_CAPACITY[result['level'] - 1]}"]
        weather=result.get("weather",{"enabled":False})
        if weather.get("enabled"):
            from dzmm_bot.domain.weather_rules import effect_percent
            crops={row["crop_code"] for row in result["plots"] if row["crop_code"]}
            affected=[f"{CROPS[code]['name']}{'增产' if effect_percent(weather['code'],crop_code=code)>0 else '减产'}概率{abs(effect_percent(weather['code'],crop_code=code))}%"
                      for code in sorted(crops) if effect_percent(weather['code'],crop_code=code)]
            lines.append(f"{weather['emoji']} 今日天气：{weather['name']}｜{'｜'.join(affected) if affected else '当前作物无受影响项'}")
        else:
            lines.append("🌦️ 每日天气暂未启用")
        groups = {}
        for row in result["plots"]:
            if row["crop_code"]:
                groups.setdefault((row["crop_code"], row["ready_at"] <= result["now"]), []).append(row)
        for (code, ready), plots in groups.items():
            if ready:
                state = "▓▓▓▓▓▓ 已成熟"
            else:
                first = min(plots, key=lambda row: row["ready_at"])
                progress = bar(result["now"] - first["planted_at"], first["ready_at"] - first["planted_at"])
                split = "分批成熟，最近" if len({row["ready_at"] for row in plots}) > 1 else "约"
                state = f"{progress} {split}{duration(first['ready_at'] - result['now'])}"
            lines.append(f"{CROPS[code]['name']}×{len(plots)}块｜{state}")
        ready_count = sum(len(plots) for (_, ready), plots in groups.items() if ready)
        if not occupied:
            lines.append("起步：/播种 燕麦 1 → 等成熟 → /收获（种子不足自动补买）")
        lines.append(f"空闲{len(result['plots']) - occupied}块｜成熟{ready_count}块｜生长{occupied - ready_count}块")
        stored = [f"{rule['name']}×{result['crops'][code]}" for code, rule in CROPS.items() if result["crops"][code]]
        lines.append("仓库：" + ("｜".join(stored) if stored else "空"))
        lines.append("/收获 | /播种 | /配制 | /农田详情 | /库存 | /农场升级")
        return "\n".join(lines)
    if kind == "farm_seed_shop":
        lines = ["—— 🌱 种子商店 ——"]
        order = ("grass", "oats", "carrot", "barley", "alfalfa", "apple",
                 "wheat", "sunflower", "cotton_crop", "grape")
        items = [
            f"{'' if 'market_base_price' in rule else '🌱 '}{CROP_EMOJI[code]}"
            f"{' ' if 'market_base_price' in rule else ''}{rule.get('seed_name', rule['name'] + '种子')} "
            f"{rule['seed_price']}币｜成熟 {rule['hours']}小时｜产量 {rule['yield'][0]}–{rule['yield'][1]}"
            for code in order if (rule := CROPS[code])
        ]
        lines.extend("｜".join(items[index:index + 2]) for index in range(0, len(items), 2))
        return "\n".join([*lines, "/买种子 燕麦 5"])
    if kind == "farm_seed_buy":
        return f"✅ 已购买{result['name']}种子 ×{result['quantity']}｜花费 {money(result['cost'])}币\n余额：{money(result['balance'])}币｜/播种 {result['name']}"
    if kind == "farm_plant":
        numbers = "、".join(str(number) for number in result["plots"])
        lines = [f"🌱 已种下{result['name']} ×{len(result['plots'])}｜土地：{numbers}号"]
        if result.get("requested", len(result["plots"])) > len(result["plots"]):
            lines.append(f"空地不足，本次申请 {result['requested']} 块，实际种植 {len(result['plots'])} 块")
        if result["auto_bought"]:
            lines.append(f"种子不足，已自动购买 ×{result['auto_bought']}｜花费 {money(result['auto_cost'])}币"
                         f"｜余额 {money(result['balance'])}币")
        harvested = result.get("auto_harvest") or {}
        lines.append("🧺 自动收获：" + ("｜".join(
            f"{CROPS[code]['name']}×{amount}" for code, amount in harvested.items()) or "暂无成熟作物"))
        lines.append(f"约 {duration(result['ready_at'] - result['now'])}后成熟｜/农场")
        return "\n".join(lines)
    if kind == "farm_harvest":
        lines = ["🌾 农场收获完成"]
        lines.extend(f"{CROPS[code]['name']} ×{quantity}" for code, quantity in result["totals"].items())
        if not result["totals"]:
            lines.append("暂无成熟作物")
        lines.append(f"仍在生长：{result['growing']} 块｜/农场")
        return "\n".join(lines)
    if kind == "farm_upgrade":
        return f"✅ 农场升级至 Lv.{result['level']}｜土地 {result['capacity']} 块\n花费 {money(result['cost'])}币｜余额 {money(result['balance'])}币"
    if kind == "feed_recipes":
        lines = ["—— 🥣 马粮配方 ——"]
        recipes = []
        for code, rule in FEEDS.items():
            ingredients = " + ".join(
                f"{CROP_EMOJI[crop]}{CROPS[crop]['name']}×{count}"
                for crop, count in rule["ingredients"].items()
            )
            recipes.append(f"{FEED_EMOJI[code]}{rule['name']}：{ingredients}")
        lines.append("｜".join(recipes[:2]))
        lines.extend(recipes[2:])
        return "\n".join([*lines, "用法：/配制 马粮 [数量]｜默认1份"])
    if kind == "feed_mix":
        ingredients = "｜".join(f"{CROPS[code]['name']}×{amount}" for code, amount in result["ingredients"].items())
        return f"🥣 已配制{result['name']} ×{result['quantity']}\n消耗：{ingredients}\n下一步：/马厩 | /喂马 马名 {result['name']}"
    if kind == "feed_shop":
        lines = ["—— 🐎 马粮商店 ——"]
        lines.extend(f"{rule['name']} {feed_shop_price(code)}币" +
                     (f"｜每日限购 {rule['daily_limit']}" if rule.get('daily_limit') else "")
                     for code, rule in FEEDS.items())
        return "\n".join([*lines, "/买马粮 疾风燕麦 1"])
    if kind == "horse_feed_buy":
        return f"✅ 已购买{result['name']} ×{result['quantity']}｜花费 {money(result['cost'])}币\n余额：{money(result['balance'])}币\n下一步：/马厩 | /喂马 马名 {result['name']}"
    if kind == "farm_sell":
        return (f"✅ 已出售{result['name']} ×{result['quantity']}｜成交 {money(result['gross'])}币\n"
                f"手续费 {money(result['fee'])}币｜到账 {money(result['net'])}币\n余额：{money(result['balance'])}币")
    raise ValueError(f"unknown farm result: {kind}")

from .formatters import bar, duration, money, name_escape, quantity
from .messages import NAMES
from dzmm_bot.domain.factory_rules import progress
from dzmm_bot.domain.animal_rarity import RARITY_RULESETS, RARITY_VERSION

ANIMAL_ORDER = ("chicken", "sheep", "cow")
ANIMAL_EMOJI = {"chicken": "🐔", "sheep": "🐑", "cow": "🐄"}
PRODUCT_EMOJI = {"egg": "🥚", "wool": "🧶", "milk": "🥛"}


def render_factory(result):
    upgrade = f"升级需 {money(result['upgrade']['coins'])} 币" if result.get("upgrade") else "已满级"
    lines = [f"🏭 加工厂 Lv.{result['level']}｜{upgrade}｜{result['line_count']}条线｜工时×{result['duration_multiplier']}"]
    jobs = {job["line_no"]: job for job in result["lines"]}
    if not jobs:
        lines.append("起步：/配方 蛋糕 → 收集材料 → /投产 蛋糕 1 → /取货")
    for line_no in range(1, result["line_count"] + 1):
        job = jobs.get(line_no)
        if not job:
            lines.append(f"生产线 {line_no}｜空闲")
            continue
        recipe = result["recipes"][job["recipe_id"]]
        ratio = progress(job["started_at"], job["finish_at"], result["now"])
        left = max(0, job["finish_at"] - result["now"])
        if job["status"] == "completed_pending_collect":
            lines.append(f"{line_no}号线：{bar(1, 1)} {recipe['display_name']}｜已完成待取货")
        else:
            lines.append(f"{line_no}号线：{bar(ratio, 1)} {recipe['display_name']}｜剩 {duration(left)}")
    lines.append(f"⚡ 加急今日 {result['rush_count']}/3 次（10/20/40币）｜💥 当前失败率 {result['current_failure_rate'] * 100}%（T3减半）")
    lines.append(f"自动化核心 ×{result['automation_core_stock']}｜已记录接口调用 {result['automation_level']} 次（效果待定）")
    lines.append("指令：/加工配方 | /投产 配方 [数量] | /取货 | /加急 [线号] | /取消投产 [线号] | /加工厂升级")
    return "\n".join(lines)


def render_factory_recipes(result, tier=None):
    label = f"T{tier} " if tier else ""
    lines = [f"—— 📖 {label}加工配方（加工厂 Lv.{result['level']}）——"]
    for recipe_id, recipe in result["recipes"].items():
        if result.get("recipe_id") and recipe_id != result["recipe_id"]:
            continue
        if tier is not None and recipe["tier"] != tier:
            continue
        unlocked = result["level"] >= recipe["factory_level_required"]
        ingredients = " + ".join(f"{NAMES[item]}×{amount}" for item, amount in recipe["ingredients"].items())
        price = f"基准价 {money(recipe['base_price'])}" if recipe["base_price"] is not None else "行情基准待配置"
        unlock_text = "✅" if unlocked else "🔒Lv." + str(recipe["factory_level_required"])
        work_seconds = recipe["base_duration"] * float(result["new_job_duration_multiplier"])
        failure_rate = result["failure_rates"][recipe_id]
        lines.append(f"{unlock_text} {recipe['display_name']}：{ingredients} → {NAMES[recipe['output_item']]}｜约 {duration(work_seconds)}｜失败 {failure_rate * 100}%｜暴击 {recipe['critical_rate'] * 100}%｜{price}")
    lines.append("页码：/配方 T1 | /配方 T2 | /配方 T3；投产：/投产 配方 [数量] | /加工厂")
    return "\n".join(lines)


def menu_text(stage="full", category=None):
    if category and category not in {"牧场", "交易"}:
        return f"「{name_escape(category)}」分类暂未开放。输入 /铃露玩法 查看当前开放功能。"
    if stage == "m0":
        return "🌾 当前开放：/注册 | /我的（/资料） | /救济 | /帮助\n牧场玩法暂未开放。"
    if category == "交易":
        if stage == "ranch":
            return "💰 当前开放的交易指令：/行情 | /买入 鸡蛋|羊毛|牛奶 [数量]\n牧场测试阶段暂不开放出售。"
        return "💰 当前开放：/行情 | /买入 商品 [数量] | /出售 商品 数量"
    commands = [
        "🌾 铃露玩法｜当前开放",
        "/牧场｜查看牧场、库存和当前单价",
        "/行情｜查看当前全服统一行情",
        "/买动物 鸡|羊|牛 [数量]",
        "/收取｜收获已到期产出",
        "/喂食｜全牧场补充 12 小时饲料",
        "/喂食 精 | /买精饲料 [数量]（不足时自动补买）",
        "/资料｜查看玩家资料",
    ]
    if stage == "full":
        commands.extend(["/加工厂 | /加工配方 | /配方 | /投产 配方 [数量] | /取货",
                         "/加急 [线号] | /取消投产 [线号] | /加工厂升级",
                         "/加工厂升级 核心 | /补货 道具 | /使用 道具",
                         "/马厩 | /买马 | /喂马 | /繁育 | /接生",
                         "/农场 | /种子商店 | /播种 | /收获 | /配制"])
    if stage == "ranch":
        commands.append("/买入 鸡蛋|羊毛|牛奶 [数量]｜尚未开放：/出售（测试阶段限制）、/牧场升级（测试阶段限制）、/回收、加工厂玩法")
    else:
        commands.extend([
            "/出售 商品 数量 | /牧场升级",
            "/买入 商品 [数量]｜尚未开放：/回收",
        ])
    commands.append("旧版 /牧场 查看、/牧场 购买、/牧场 收获 等指令继续可用。")
    return "\n".join(commands)


def render_ranch(result, stage="full"):
    from decimal import Decimal
    title = name_escape(result.get("name"))
    upgrade = f"升级{money(result['upgrade_cost'])}币（含税）" if result.get("upgrade_cost") is not None else "已满级"
    lines = [f"🏡 {title}的牧场｜Lv.{result['level']}｜工时×{result.get('level_duration_multiplier',1):.2f}｜{upgrade}｜容量{quantity(result['used_capacity'])}/{quantity(result['capacity'])}"]
    groups = {}
    for animal in result["animals"]:
        groups.setdefault(animal["animal_type"], []).append(animal)
    for animal_type in ANIMAL_ORDER:
        group = groups.get(animal_type, [])
        if not group:
            continue
        leader = group[0]
        affection = max(0, min(10, leader.get("affection", 0)))
        tags = (["💗" * affection] if affection else []) + [f"🏃速度+{affection}%"]
        if leader.get("feed_streak", 0) >= 3:
            tags.append("🔥连喂+10%")
        if leader.get("premium_feed_active") and leader.get("running"):
            tags.append("🌾✨精料中")
        rare = "".join(f"{rule['emoji']}{sum(a.get('rarity') == code for a in group)}"
                       for code, rule in RARITY_RULESETS[RARITY_VERSION].items()
                       if any(a.get("rarity") == code for a in group))
        product = {"chicken": "egg", "sheep": "wool", "cow": "milk"}[animal_type]
        waiting = sum(result.get("pending_by_animal", {}).get(a["id"], 0) for a in group)
        state = (f"下一批{duration(leader.get('next_in', 0))}｜⏰饲料{duration(leader.get('feed_in', 0))}"
                 if leader.get("running") else "停产｜🌾饲料耗尽，保留进度")
        lines.append(f"{ANIMAL_EMOJI[animal_type]}{NAMES[animal_type]}×{len(group)}{rare}｜" + "｜".join(tags) +
                     f"｜{bar(leader.get('progress', 0), 1)} 待收{waiting}{PRODUCT_EMOJI[product]}{NAMES[product]}｜{state}")
    weather=result.get("weather",{"enabled":False})
    if weather.get("enabled"):
        from dzmm_bot.domain.weather_rules import effect_percent
        affected=[]
        for animal_type,group in groups.items():
            product={"chicken":"egg","sheep":"wool","cow":"milk"}[animal_type]
            pct=effect_percent(weather["code"],product=product)
            if pct: affected.append(f"{PRODUCT_EMOJI[product]}{NAMES[product]}{'增产' if pct>0 else '减产'}概率{abs(pct)}%")
        effect="｜".join(affected) if affected else "当前动物无受影响项"
        lines[0] += f"｜{weather['emoji']} 今日天气：{weather['name']}｜{effect}"
    elif stage == "full":
        lines[0] += "｜🌦️ 每日天气暂未启用"
    if result.get("more"):
        lines.append("⏳ 仍有产出待结算｜继续执行 /收取")
    if not groups:
        lines.append("暂无动物｜起步：/买动物 鸡 1 → /喂食 → /收取")
    from .compact import ICONS
    stored = [f"{ICONS.get(NAMES[code], '📦')}{NAMES[code]}{amount}" for code, amount in result["stocks"].items()
              if code in result["prices"] and amount > 0]
    value = sum((info["value"] for info in result.get("price_info", {}).values()), Decimal(0))
    summary = "｜".join(stored[:3]) if len(stored) <= 3 else f"{len(stored)}种商品"
    lines.append(f"库存：{summary or '空'}｜估值{money(value)}币")
    probabilities = "｜".join(f"{ANIMAL_EMOJI.get(code, '')}{NAMES[code]}{money(value)}%"
                              for code, value in result.get("increase_probabilities", {}).items())
    lines.append(f"🌾饲料{result['stocks'].get('feed', 0)}｜✨精料{result['stocks'].get('premium_feed', 0)}｜增产概率：{probabilities or '0%'}")
    commands = ["/喂食", "/收取", "/库存", "/行情"]
    if stage != "ranch":
        commands.extend(["/牧场升级", "/回收", "/寄拍"])
    lines.append(" | ".join(commands))
    return "\n".join(lines)


def render_inventory(result):
    lines = ["📦 我的库存｜估值按当前行情计算"]
    for code, amount in result["stocks"].items():
        if amount <= 0:
            continue
        info = result.get("price_info", {}).get(code)
        detail = f"（现价{money(info['price'])}币｜值{money(info['value'])}）" if info else ""
        lines.append(f"{NAMES.get(code, code)}×{amount}{detail}")
    if len(lines) == 1:
        lines.append("库存为空｜起步：/买动物 鸡 1 | /播种 燕麦 1；成熟后 /收取 | /收获")
    lines.append("/行情 | /出售 商品 数量 | /牧场")
    return "\n".join(lines)

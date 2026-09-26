from .formatters import bar, duration, money, name_escape, quantity
from .messages import NAMES

ANIMAL_ORDER = ("chicken", "sheep", "cow")
PRODUCT_ORDER = ("egg", "wool", "milk")


def menu_text(stage="full", category=None):
    if category and category not in {"牧场", "交易"}:
        return f"「{name_escape(category)}」分类暂未开放。输入 /瑞禾玩法 查看当前开放功能。"
    if stage == "m0":
        return "🌾 当前开放：/加入｜/我的（/资料）｜/救济｜/帮助\n牧场玩法暂未开放。"
    if category == "交易":
        if stage == "ranch":
            return "💰 当前开放的交易指令：牧场测试阶段暂不开放出售。\n/牧场 查看 当前库存与单价"
        return "💰 当前开放：/出售 鸡蛋|羊毛|牛奶|全部 [数量]\n/买入 尚未开放。"
    commands = [
        "🌾 瑞禾玩法｜当前开放",
        "/牧场｜查看牧场、库存和当前单价",
        "/行情｜查看牧场与当前单价",
        "/买动物 鸡|羊|牛 [数量]",
        "/收取｜收获已到期产出",
        "/喂食｜全牧场补充 12 小时饲料",
        "/喂食 精｜精饲料喂食｜/买精饲料 [数量]",
        "/资料｜查看玩家资料",
    ]
    if stage == "ranch":
        commands.append("尚未开放：/出售（测试阶段限制）、/牧场升级（测试阶段限制）、/买入、/回收、加工厂玩法")
    else:
        commands.extend([
            "/出售 鸡蛋|羊毛|牛奶|全部 [数量]｜/牧场升级",
            "尚未开放：/买入、/回收、加工厂玩法",
        ])
    commands.append("旧版 /牧场 查看、/牧场 购买、/牧场 收获 等指令继续可用。")
    return "\n".join(commands)


def render_ranch(result, stage="full"):
    title = name_escape(result.get("name"))
    if result.get("title"):
        title += f" 【{name_escape(result['title'])}】"
    lines = [
        f"—— 🏡 {title} 的牧场 ——",
        f"等级：Lv.{result['level']}｜容量：{quantity(result['used_capacity'])}/{quantity(result['capacity'])}",
        "🌤️ 今日天气：暂未启用（天气概率待配置）" if not result.get("weather_enabled") else result["weather_text"],
    ]
    animals = result["animals"]
    if not animals:
        lines.append("　暂无动物")
    else:
        groups = {}
        for animal in animals:
            status = "生产中" if animal.get("running") else "停产中"
            groups.setdefault((animal["animal_type"], status), []).append(animal)
        for animal_type in ANIMAL_ORDER:
            for status in ("生产中", "停产中"):
                grouped = groups.get((animal_type, status), [])
                if not grouped:
                    continue
                lines.append(f"{NAMES[animal_type]}×{quantity(len(grouped))}｜{status}")
                for animal in grouped:
                    hearts = "💗" * max(0, min(9, animal.get("affection", 0))) or "无心"
                    tags = [f"亲密度 {hearts}"]
                    if animal.get("feed_streak", 0) >= 3:
                        tags.append("连喂+10%")
                    if animal.get("premium_feed_active") and animal.get("running"):
                        tags.append("🌾✨精料中")
                    product = {"chicken": "egg", "sheep": "wool", "cow": "milk"}[animal_type]
                    waiting = result.get("pending_by_animal", {}).get(animal["id"], 0)
                    if animal.get("running"):
                        batch_text = f"约 {duration(animal.get('next_in', 0))} 后新一批"
                    else:
                        batch_text = "断喂停产，保留当前进度"
                    lines.append(f"　{animal['display_id']}（{'｜'.join(tags)}）")
                    lines.append(f"　{bar(animal.get('progress', 0), 1)} 待收 {quantity(waiting)} {NAMES[product]}｜{batch_text}")
                    lines.append(f"　⏰ 饲料还能撑 {duration(animal.get('feed_in', 0))}")
    lines.append("—— 库存 ——")
    products = result["prices"]
    for product in PRODUCT_ORDER:
        if product not in products:
            continue
        amount = result["stocks"].get(product, 0)
        price = products[product]
        lines.append(f"{NAMES[product]} ×{quantity(amount)}｜现价 ⨀ {money(price)}｜估值 ⨀ {money(amount * price)}")
    lines.append(f"饲料 ×{quantity(result['stocks'].get('feed', 0))}")
    lines.append(f"精饲料 ×{quantity(result['stocks'].get('premium_feed', 0))}")
    if result.get("more"):
        lines.append("💡 仍有待结算产出，请使用 /收取")
    commands = ["/喂食", "/喂食 精", "/收取", "/行情", "/买动物"]
    if stage != "ranch":
        commands.extend(["/出售", "/牧场升级"])
    commands.append("/瑞禾玩法")
    lines.append("指令：" + "｜".join(commands))
    if result["pages"] > 1:
        target_page = result["page"] + 1 if result["page"] < result["pages"] else result["page"] - 1
        lines.append(f"翻页：/牧场 {target_page}")
    return "\n".join(lines)

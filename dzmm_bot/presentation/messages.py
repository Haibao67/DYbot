from datetime import datetime
from dzmm_bot.domain.economy import HK

NAMES = {"feed": "饲料", "premium_feed": "精饲料", "egg": "鸡蛋", "wool": "羊毛", "milk": "牛奶",
         "chicken": "鸡", "sheep": "羊", "cow": "牛", "ranch": "牧场"}
HELP = ("🌾 冬宴游戏帮助\n/加入｜/我的｜/救济\n/牧场 查看 [页码]\n"
        "/牧场 购买 鸡|羊|牛|饲料 [数量]\n/喂食 [精]｜/买精饲料 [数量]\n"
        "/牧场 收获\n/牧场 出售 鸡蛋|羊毛|牛奶|全部 [数量]\n/牧场 升级\n/排行 总榜 [页码]")


def help_text(stage):
    if stage == "m0":
        return "👤 冬宴游戏帮助\n/加入｜/我的（/资料）｜/救济｜/帮助\n输入 /瑞禾玩法 查看当前开放功能"
    lines = HELP.splitlines()
    if stage == "ranch":
        lines = [line for line in lines if not line.startswith(("/牧场 出售", "/牧场 升级"))]
    lines.extend([
        "/瑞禾玩法｜/菜单 [牧场|交易]｜查看当前开放功能",
        "/牧场｜/行情｜/资料｜/买动物 鸡|羊|牛 [数量]｜/收取",
        "未开放玩法会明确提示；旧指令继续可用。",
    ])
    if stage == "ranch":
        lines.append("牧场测试阶段暂不开放出售与升级。")
    else:
        lines.append("/出售 鸡蛋|羊毛|牛奶|全部 [数量]｜/牧场升级")
    return "\n".join(lines)


def error(code, **d):
    messages = {
        "not_joined": ("尚未加入游戏", "/加入"), "joined": ("你已加入全局游戏", "/我的"),
        "group_join": ("请在已启用的游戏群首次加入", "/加入"),
        "frozen": ("账户已冻结，暂不能变更资产；请联系管理员", "/我的"),
        "relief_used": ("今天已领取救济，香港时间次日零点恢复资格", "/我的"),
        "relief_balance": (f"余额不低于 ⨀ {d.get('floor', 100)}，无需领取救济", "/我的"),
        "balance": (f"余额不足，还缺 ⨀ {d.get('missing', 0)}", "/救济"),
        "capacity": (f"牧场容量不足，还缺 {d.get('missing', 0)} 格空间", "/牧场 升级"),
        "stock": (f"{NAMES.get(d.get('item'), '物品')}不足，还缺 {d.get('missing', 0)} 个", "/牧场 查看"),
        "animal": ("找不到属于你的可喂食动物，请核对编号", "/牧场 查看"),
        "expired": ("该动物已停产，当前规则不允许再次喂食", "/牧场 查看"),
        "settlement_pending": ("待结算产出较多，请先收获后再喂食", "/牧场 收获"),
        "no_products": ("暂无可出售产品", "/牧场 查看"),
        "max_level": ("牧场已达最高等级九级", "/牧场 查看"),
        "quantity": ("数量或页码必须为 1 至 100000 的正整数", "/帮助"),
        "unknown": ("未识别的指令", "/帮助"),
        "syntax": ("指令格式不正确，请查看用法", "/帮助"),
        "reference_conflict": ("操作编号与已有记录不一致，请联系管理员", "/我的"),
        "amount": ("金额超出允许范围", "/帮助"),
        "system": ("操作未完成，未扣除资产；请稍后重试", "/帮助"),
        "unavailable": ("该玩法暂未开放，请等待开放通知", "/帮助"),
        "whitelist": ("当前牧场处于白名单测试阶段", "/我的"),
    }
    message, next_command = messages[code]
    return f"⚠️ {message}\n\n输入 {next_command}"


def timestamp(value):
    return datetime.fromtimestamp(value, HK).strftime("%Y-%m-%d %H:%M")


def render(result, reference=None, stage="full"):
    kind = result["kind"]
    next_command = "/牧场 查看"
    if kind in ("join", "relief"):
        heading = "✅ 已加入冬宴游戏" if kind == "join" else "✅ 救济领取成功"
        lines = [heading, f"🪙 {'获得' if kind == 'join' else '补足'}：⨀ {result['amount']}", f"当前余额：⨀ {result['balance']}"]
        next_command = "/我的"
    elif kind == "profile":
        return (f"👤 我的资料\n昵称：{result['name']}\n🪙 余额：⨀ {result['balance']}\n"
                f"加入时间：{timestamp(result['joined_at'])}（香港）\n账户状态：{'正常' if result['status'] == 'active' else '冻结'}\n"
                "所属世界：冬宴全局世界\n\n输入 " + ("/帮助" if stage == "m0" else "/牧场 查看"))
    elif kind in ("buy", "sell", "upgrade"):
        lines = [{"buy": "✅ 购买完成", "sell": "✅ 出售完成 💰", "upgrade": "✅ 牧场升级完成"}[kind]]
        if kind == "upgrade":
            lines.append(f"当前等级：{result['level']}｜总容量：{result['capacity']}")
        for row in result["lines"]:
            lines.append(f"{NAMES[row['item']]} ×{row['quantity']}" + (f"｜单价：⨀ {row['price']}" if "price" in row else ""))
            lines.append(f"{'毛收入' if kind == 'sell' else '成交额'}：⨀ {row['gross']}｜税额：⨀ {row['tax']}")
            lines.append(f"{'实际到账' if kind == 'sell' else '实际支付'}：⨀ {abs(row['net'])}")
        lines.extend([f"🪙 余额：⨀ {result['balance']}", f"奖池累计税额：⨀ {result['pool']}"])
    elif kind == "feed":
        lines = ["✅ 喂食完成", f"喂养动物：{result.get('animals', 1)} 只｜消耗饲料：{result['quantity']}｜剩余：{result['remaining']}"]
        if result.get("premium_quantity"):
            lines.append(f"消耗精饲料：{result['premium_quantity']}｜剩余：{result['premium_remaining']}")
        lines.extend(["饲料有效期：12 小时", f"🪙 余额：⨀ {result['balance']}"])
    elif kind == "harvest":
        lines = ["📦 收获完成", "｜".join(f"{NAMES[k]} ×{v}" for k, v in result["totals"].items()) or "暂无到期产出",
                 f"🪙 余额：⨀ {result['balance']}"]
        next_command = "/牧场 出售 全部" if result["totals"] else "/牧场 查看"
    elif kind == "ranch":
        lines = ["🌾 我的牧场", f"等级：{result['level']}｜容量：{result['used_capacity']}/{result['capacity']}",
                 f"可收获：{result['pending']} 个", "库存：" + "｜".join(f"{NAMES[k]} {v}" for k, v in result["stocks"].items()),
                 "当前单价：" + "｜".join(f"{NAMES[k]} ⨀ {v}" for k, v in result["prices"].items())]
        for animal in result["animals"]:
            status = "已停产" if animal["production_until"] <= result["now"] else (
                "即将停产" if animal["production_until"] - result["now"] <= 6 * 3600 else "生产中")
            lines.append(f"{NAMES[animal['animal_type']]} {animal['display_id']}｜{status}｜截止 {timestamp(animal['production_until'])}")
        lines.append(f"动物列表：{result['page']}/{result['pages']} 页")
        next_command = f"/牧场 查看 {result['page'] + 1}" if result["page"] < result["pages"] else "/牧场 收获"
    elif kind == "ranking":
        lines = ["🪙 全局余额总榜"]
        lines.extend(f"{(result['page'] - 1) * 5 + i}. {r['name']}｜⨀ {r['balance']}" for i, r in enumerate(result["rows"], 1))
        lines.append(f"第 {result['page']} 页" if result["rows"] else "本页暂无玩家")
        next_command = f"/排行 总榜 {result['page'] + 1}" if len(result["rows"]) == 5 else "/我的"
    else:
        raise ValueError(kind)
    if result.get("more"):
        lines.append("仍有待结算产出，请继续收获")
        next_command = "/牧场 收获"
    if reference and kind not in ("profile", "ranch", "ranking"):
        lines.append(f"操作编号：{reference[:8]}")
    if stage == "ranch" and next_command.startswith("/牧场 出售"):
        next_command = "/牧场 查看"
    return "\n".join(lines) + f"\n\n输入 {next_command}"

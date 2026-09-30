from datetime import datetime
from dzmm_bot.domain.economy import HK
from dzmm_bot.presentation.formatters import money, long_duration, name_escape

NAMES = {"feed": "饲料", "premium_feed": "精饲料", "egg": "鸡蛋", "wool": "羊毛", "milk": "牛奶",
         "chicken": "鸡", "sheep": "羊", "cow": "牛", "ranch": "牧场",
         "cake": "蛋糕", "sweater": "毛衣", "cheese": "奶酪", "gift_box": "礼盒",
         "down_coat": "羽绒服", "cheese_platter": "奶酪拼盘", "grand_gift": "大礼包",
         "brush": "毛刷", "copper_bell": "铜铃", "greenhouse": "暖棚", "opening_feast": "开工宴",
         "master_meal": "大师餐", "dress": "礼服", "work_apron": "工装围裙", "automation_core": "自动化核心",
         "grass": "牧草", "oats": "燕麦", "carrot": "胡萝卜", "barley": "大麦",
         "alfalfa": "苜蓿", "apple": "苹果", "wheat": "小麦", "sunflower": "向日葵",
         "cotton_crop": "棉花", "grape": "葡萄",
         "bread": "面包", "sunflower_oil": "葵花油", "grape_jam": "葡萄果酱", "cotton_cloth": "棉布",
         "sandwich": "精致三明治", "salad": "调味沙拉", "fruit_cake": "水果蛋糕",
         "cotton_workwear": "棉质工装"}
from dzmm_bot.domain.farm_rules import CROPS, FEEDS
NAMES.update({code: rule["name"] for code, rule in FEEDS.items()})
NAMES.update({"shiny_chicken": "闪光鸡", "shiny_sheep": "闪光羊", "shiny_cow": "闪光牛"})
NAMES.update({f"seed_{code}": rule.get("seed_name", rule["name"] + "种子") for code, rule in CROPS.items()})


def render_weather(summary):
    if not summary.get("enabled"):
        return f"🌦️ 每日天气暂未启用｜游戏日 {summary.get('game_date', '')}｜天气表按香港时间 00:00 刷新"
    from datetime import datetime
    from dzmm_bot.domain.weather_rules import TIMEZONE
    next_update=datetime.fromtimestamp(summary["effective_until"],TIMEZONE).strftime("%m-%d %H:%M")
    return (f"🌦️ 铃露今日天气｜{summary['game_date']}\n"
            f"{summary['emoji']} {summary['name']}｜{summary['description']}\n"
            f"下次更新：香港时间 {next_update}")
def help_text(stage, module=None):
    if module:
        aliases = {"账号": "账号与资讯", "资讯": "账号与资讯", "牧场": "牧场", "交易": "交易",
                   "加工厂": "加工厂", "加工": "加工厂", "增益": "增益", "马厩": "马厩",
                   "农场": "农场与马粮", "马粮": "农场与马粮", "拍卖": "拍卖场", "拍卖场": "拍卖场"}
        target = aliases.get(module)
        full = _help_full(stage)
        marker = f"—— {target} ——"
        if target and marker in full:
            tail = full.split(marker, 1)[1].split("——", 1)[0].strip()
            return f"📚 {target}帮助｜P7.5\n{tail}"
        return "暂无该模块帮助，请用 /帮助 查看已开放模块。"
    lines = ["📚 铃露帮助｜P7.5｜详细语法：/帮助 模块",
             "账号：/注册 | /钱包 | /签到 | /资料 | /排行榜",
             "资讯：/怎么玩 | /今日 | /天气 | /待办 | /更新"]
    if stage != "m0":
        lines.extend(["牧场：/牧场 | /买动物 | /喂食 | /收取 | /库存",
                      "交易：/行情 | /买入 | /出售"])
    if stage == "full":
        lines.extend(["农场：/农场 | /播种 | /收获 | /配制",
                      "加工：/加工厂 | /配方 | /投产 | /取货",
                      "马厩：/马厩 | /买马 | /喂马 | /繁育",
                      "拍卖：/拍卖场 | /拍 | /寄拍"])
    return "\n".join(lines)


def _help_full(stage):
    lines = ["🌾 铃露指令帮助", "—— 账号与资讯 ——",
             "/注册 | /资料 | /钱包 | /签到 | /救济",
             "/转账 金额（引用收款人的群消息）",
             "/今日 | /待办 | /更新 | /更新日志",
             "/怎么玩 [玩法] | /铃露玩法"]
    if stage == "m0":
        return "\n".join(lines)
    lines.extend(["—— 牧场 ——", "/牧场 [页码] | /买动物 鸡|羊|牛 [数量]",
                  "/喂食 [精] | /买精饲料 [数量] | /收取"])
    if stage == "full":
        lines.append('🧧 /发红包 金额 [份数] [分钟] | /抢红包 | /抢 | 管理员：/结束红包')
        lines.append("/牧场升级 | /回收 动物 数量 | /放养 闪光动物 数量 | /排行 总榜 [页码]")
    lines.extend(["—— 交易 ——", "/行情 | /买入 商品 [数量]"])
    if stage == "full":
        lines.append("/出售 商品 数量（数量必填，商品见 /行情）")
        lines.extend(["—— 加工厂 ——", "/加工厂 | /配方 | /投产 配方 [数量] | /取货",
                      "/加工配方 [T1|T2|T3] | /加急 [线号] | /取消投产 [线号] | /加工厂升级 [核心]",
                      "—— 增益 ——", "/补货 道具 | /使用 道具",
                      "—— 马厩 ——", "/马厩 | /买马 | /马 名字 | /马匹命名 [编号] 新名",
                      "/马厩升级 | /马册 | /血统 名字 | /退役 名字",
                      "/喂马 名字 马粮 | /繁育 母马 公马 | /接生"])
        lines.extend(["—— 农场与马粮 ——", "/农场 | /种子商店 | /买种子 作物 数量",
                      "/播种 作物 [数量] | /收获 | /农场升级",
                      "/配制 [马粮 数量] | /买马粮 马粮 数量"])
        lines.extend(["—— 拍卖场 ——", "/拍卖场 | /拍卖 名称 起拍价 | /寄拍 马 名字 起拍价",
                      "/寄拍 道具 名称 数量 起拍价（支持闪光鸡、闪光羊、闪光牛）",
                      "/拍 | /跟 | /拍 金额 | /拍卖详情 | /拍卖记录",
                      "/我的拍卖 | /我的出价 | /领取拍卖马 [名字]"])
        lines.extend(["—— 每日赛马 ——", "/赛程 | /比赛详情 赛事 | /报名 赛事 马名 跑法 | /取消报名 赛事 马名",
                      "/观赛 赛事 | /赛果 赛事 | /战绩 马名",
                      "每日12:00报名、19:45截止、20:00开赛｜跑法：逃、先行、差、追｜报名50币"])
    return "\n".join(lines)


def error(code, **d):
    messages = {
        'red_packet_group': ('红包只能在游戏群内使用','/帮助'),
        'red_packet_syntax': ('用法：/发红包 金额 [份数] [持续分钟]｜默认10份、2分钟；领取：/抢红包 | /抢','/帮助'),
        'red_packet_amount': ('红包金额须大于0，最多1000000币且最多两位小数','/发红包 10 10'),
        'red_packet_count': ('红包份数须为1～100','/发红包 10 10'),
        'red_packet_duration': ('红包持续时间须为1～100000分钟','/发红包 10 10 5'),
        'red_packet_precision': ('金额过小，无法按分满足平均值上下50%；请增加金额或减少份数','/发红包 10 10'),
        'red_packet_active': ('本群还有未领完红包，领完或到期后才能再发','/抢红包'),
        'red_packet_none': ('本群暂无可领取红包，红包可能已领完或到期','/发红包 10'),
        'red_packet_self': ('不能领取自己发出的红包','/钱包'),
        'red_packet_claimed': ('这个红包你已经领取过，每人限领一次','/钱包'),
        "not_joined": ("尚未注册游戏，系统将自动使用你的平台昵称", "/注册"),
        "register_syntax": ("请直接发送 /注册，用户名由系统获取，不接受自行填写", "/注册"),
        "register_name_unavailable": ("暂未获取到你的平台昵称，请稍后重试注册", "/注册"),
        "joined": ("你已注册全局游戏", "/我的"),
        "group_join": ("请在已启用的游戏群使用 /注册", "/帮助"),
        "frozen": ("账户已冻结，暂不能变更资产；请联系管理员", "/我的"),
        "relief_used": ("今天已领取救济，香港时间次日零点恢复资格", "/我的"),
        "relief_balance": (f"余额不低于 {d.get('floor', 100)}，无需领取救济", "/我的"),
        "balance": (f"余额不足，还缺 {d.get('missing', 0)}", "/救济"),
        "capacity": (f"牧场容量不足，还缺 {d.get('missing', 0)} 格空间", "/牧场 升级"),
        "stock": (f"{NAMES.get(d.get('item'), '物品')}不足，还缺 {d.get('missing', 0)} 个", "/牧场 查看"),
        "animal": ("找不到可喂食动物，请先查看牧场状态", "/牧场 查看"),
        "expired": ("该动物已停产，当前规则不允许再次喂食", "/牧场 查看"),
        "settlement_pending": ("待结算产出较多，请先收获后再喂食", "/牧场 收获"),
        "no_products": ("暂无可出售产品", "/牧场 查看"),
        "market_item": (f"{d.get('name', NAMES.get(d.get('item'), '该商品'))}不是可交易的基础商品", "/行情"),
        "market_limit": (f"本行情周期买入限额为 200 币，本次最多还可花 {d.get('remaining', 0)} 币", "/行情"),
        "max_level": ("牧场已达最高等级九级", "/牧场 查看"),
        "quantity": ("数量或页码必须为 1 至 100000 的正整数", "/帮助"),
        "sell_syntax": ("出售时必须填写商品和数量，不能使用“全部”", "/出售 鸡蛋 1"),
        "unknown": ("未识别的指令", "/帮助"),
        "syntax": ("指令格式不正确，请查看用法", "/帮助"),
        "reference_conflict": ("该请求与已有记录不一致，请联系管理员", "/我的"),
        "amount": ("金额超出允许范围", "/帮助"),
        "system": ("操作未完成，未扣除资产；请稍后重试", "/帮助"),
        "unavailable": ("该玩法暂未开放，请等待开放通知", "/帮助"),
        "whitelist": ("当前牧场处于白名单测试阶段", "/我的"),
        "factory_recipe": (f"没有可用配方：{d.get('name', '未知')}", "/配方"),
        "factory_locked": (f"该配方需加工厂达到 Lv.{d.get('level')}", "/加工厂升级"),
        "factory_full": ("所有加工线都在生产中，请稍后再投产", "/加工厂"),
        "factory_not_ready": (f"产品尚未完成，约需 {max(1, int(d.get('remaining', 0) / 60))} 分钟", "/加工厂"),
        "factory_max": ("加工厂已达最高等级 Lv.5", "/加工厂"),
        "buff_item": (f"没有可制作或使用的增益道具：{d.get('name', '未知')}", "/补货 毛刷"),
        "factory_line": ("找不到可加急或取消的进行中生产线", "/加工厂"),
        "factory_expedite_limit": ("今天的加急次数已用完（每日最多 3 次）", "/加工厂"),
        "horse_capacity": ("马厩已满，请先升级或退役马匹", "/马厩"),
        "horse_stable_max": ("马厩已达到最高等级 Lv.5", "/马厩"),
        "horse_missing": ("找不到这匹马，请核对名字或编号", "/马厩"),
        "race_locked": ("这匹马已被赛事锁定，结束或取消报名后再操作", "/赛程 | /待办"),
        "horse_ambiguous": ("编号匹配了多匹马，请输入更长的编号", "/马册"),
        "horse_name": ("马名需为 1 至 20 字，且不能包含斜杠或控制字符", "/马厩"),
        "horse_name_taken": ("已有同名的在厩马匹，请换一个名字", "/马厩"),
        "horse_rename_target": ("请指定马名或编号；仅有一匹在厩马时可直接填写新名", "/马厩"),
        "horse_pregnant": ("这匹母马尚有待接生的小马，暂不能退役", "/马厩"),
        "horse_feed_unavailable": ("马粮及优质牧草规则待定，喂马暂未开放", "/马厩"),
        "horse_breeding_unavailable": ("繁育规则与材料待定，繁育和接生暂未开放", "/马厩"),
        "horse_feed_type": ("请选择已开放的基础马粮；优质牧草只用于繁育", "/配制"),
        "horse_feed_short": (f"{d.get('name', '马粮')}不足，请先配制或购买", "/买马粮 马粮 数量"),
        "horse_training_limit": ("这匹马已达到累计 30 次喂食上限", "/马厩"),
        "horse_feed_recover": (f"喂食次数不足，约 {d.get('minutes', 60)} 分钟后恢复 1 次", "/马厩"),
        "horse_training_age": ("幼驹尚不能培养，成长至青年马后再喂", "/马厩"),
        "horse_breeding_pair": ("繁育需指定不同的母马和公马", "/马厩"),
        "horse_breeding_age": ("双方必须成年后才能繁育", "/马厩"),
        "horse_breeding_limit": ("有马匹已达到终身 5 次繁育上限", "/马厩"),
        "horse_breeding_cooldown": ("有马匹仍在繁育冷却中", "/马厩"),
        "horse_kinship": ("两匹马存在近两代亲缘关系，不能繁育", "/血统"),
        "horse_no_foal": ("暂无到期且待接生的幼驹", "/马厩"),
        "farm_space": (f"农场空地不足，当前只有 {d.get('available', 0)} 块空地", "/农场"),
        "farm_seed_short": (f"{d.get('name', '该作物')}种子不足，请先购买", "/种子商店"),
        "farm_ingredient_short": (f"{d.get('name', '材料')}不足，还缺 {d.get('missing', 0)}", "/农场"),
        "farm_max": ("农场已达最高等级 Lv.9", "/农场"),
        "farm_upgrade_unavailable": ("农场升级费用尚未配置", "/农场"),
        "farm_sell_unavailable": ("农作物固定回收价尚未配置", "/农场"),
        "horse_feed_limit": (f"优质牧草今日限购 4 份，还可购买 {d.get('remaining', 0)} 份", "/买马粮 优质牧草 1"),
        "transfer_group": ("请在游戏群内引用收款人的消息后转账", "/转账 10"),
        "transfer_syntax": ("格式：引用收款人的消息并输入 /转账 金额", "/转账 10"),
        "transfer_reference": ("无法核对被引用玩家；请引用对方在本群新发送的消息", "/转账 10"),
        "transfer_self": ("不能向自己转账", "/钱包"),
        "transfer_target": ("收款人尚未注册或账户不可用", "/帮助"),
        "transfer_amount": ("金额须大于 0，最多 1000000 币，且最多两位小数", "/钱包"),
        "admin_only": ("该指令仅限游戏管理员使用", "/帮助"),
        "admin_private": ("请在 Bot 私聊中使用管理员发放指令", "/帮助"),
        "admin_item": ("未识别该库存道具名称", "/帮助"),
        "admin_item_quantity": ("单次最多发放 1000 件道具", "/帮助"),
        "auction_syntax": ("物品：/拍卖 物品 数量 起拍价；马匹：/拍卖 马 马名 起拍价", "/库存 | /马厩"),
        "auction_price": ("起拍价和指定出价须为正整数币", "/拍卖场"),
        "auction_title": ("拍品名称不符合发布要求，请修改后重试", "/拍卖 名称 起拍价"),
        "auction_busy": ("当前已有拍卖正在进行，请先查看", "/拍卖场"),
        "auction_none": ("当前没有正在进行的拍卖", "/拍卖场"),
        "auction_ended": ("这场拍卖已经结束", "/拍卖场"),
        "auction_cooldown": (f"发起拍卖还需等待 {d.get('remaining', 0)} 秒", "/拍卖场"),
        "auction_self": ("不能参与自己发起的拍卖", "/拍卖场"),
        "auction_leading": ("你已经是当前最高出价者", "/拍卖场"),
        "auction_low": (f"出价必须高于当前价 {d.get('current', 0)} 币", "/拍"),
        "auction_balance": (f"本次需要 {d.get('required', 0)} 币，可用余额 {d.get('available', 0)} 币", "/钱包"),
        "auction_group": ("请在已启用的游戏群发起或参与竞拍", "/拍卖场"),
        "auction_holding_none": ("没有可领取的托管马匹", "/马厩"),
        "auction_item": ("未识别该库存物品或数量不符合要求，马匹每次只能拍卖一匹", "/拍卖 物品 数量 起拍价 | /库存"),
    }
    message, next_command = messages[code]
    if code == "balance" and "required" in d:
        message = (f"余额不足｜费用 {money(d['required'])}币｜余额 {money(d['balance'])}币"
                   f"｜差额 {money(d['missing'])}币")
        if d['available'] != d['balance']:
            message += f"｜可用余额 {money(d['available'])}币"
    if code in {"stock", "farm_ingredient_short", "horse_feed_short"} and "required" in d:
        label = d.get("name") or NAMES.get(d.get("item"), "物品")
        message = f"{label}不足｜需要 {d['required']}｜已有 {d['available']}｜缺少 {d['missing']}"
    return f"⚠️ {message}\n\n输入 {next_command}"


def timestamp(value):
    return datetime.fromtimestamp(value, HK).strftime("%Y-%m-%d %H:%M")


def render(result, reference=None, stage="full"):
    kind = result["kind"]
    if kind == "wallet":
        balance = f"🪙 钱包余额：{money(result['balance'])} 冬宴币"
        if result.get("available", result["balance"]) != result["balance"]:
            balance += f"\n可用余额：{money(result['available'])} 冬宴币"
        return balance
    if kind == "check_in":
        heading = (f"✅ 签到成功｜获得 {money(result['amount'])} 冬宴币"
                   if result["claimed"] else "📅 今天已经签到过了")
        todo = "｜".join(result["todo"].splitlines()[1:4]) or "暂无待办"
        return f"{heading}｜余额{money(result['balance'])}币\n待办：{todo}\n/待办 | /钱包"
    if kind == "player_transfer":
        return (f"✅ 转账成功｜收款人：{name_escape(result['recipient'])}\n"
                f"金额：{money(result['amount'])} 冬宴币｜剩余余额：{money(result['balance'])} 冬宴币")
    if kind == "admin_coin_grant":
        return (f"✅ 已发放 {money(result['amount'])} 冬宴币\n"
                f"🪙 当前余额：{money(result['balance'])} 冬宴币")
    if kind == "admin_item_grant":
        return (f"✅ 已发放 {result['name']} ×{result['quantity']}\n"
                f"当前库存：{result['stock']}")
    next_command = "/牧场 查看"
    if kind in ("join", "relief"):
        heading = "✅ 注册成功" if kind == "join" else "✅ 救济领取成功"
        lines = [heading, f"🪙 {'获得' if kind == 'join' else '补足'}：{money(result['amount'])}", f"当前余额：{money(result['balance'])}"]
        next_command = "/我的"
    elif kind == 'red_packet_send':
        return (f"🧧 红包已发出｜{money(result['amount'])}币｜{result['count']}份\n"
                f"{result['duration_minutes']}分钟无人领取自动到期，余款退回｜余额 {money(result['balance'])}币\n/抢红包 | /抢")
    elif kind == 'red_packet_claim':
        return (f"🧧 抢到{name_escape(result['sender'])}的红包｜{money(result['amount'])}币\n"
                f"剩余 {result['remaining']}份｜余额 {money(result['balance'])}币\n下一步：/钱包")
    elif kind == 'red_packet_end':
        return (f"🧧 管理员已结束红包｜发起人：{name_escape(result['sender'])}\n"
                f"未领取余额已退回：{money(result['refund'])}币")
    elif kind == "profile":
        lines = [f"👤 我的资料", f"昵称：{result['name']}", f"🪙 余额：{money(result['balance'])}",
                 f"加入时间：{timestamp(result['joined_at'])}（香港）",
                 f"账户状态：{'正常' if result['status'] == 'active' else '冻结'}", "所属世界：冬宴全局世界"]
        active_buffs = result.get("buffs", [])
        if active_buffs:
            lines.append("—— 当前增益 ——")
            for buff in active_buffs:
                lines.extend([f"{buff['emoji']} {buff['name']}", buff["description"],
                              f"剩余 {long_duration(buff['remaining'])}"])
        else:
            lines.append("—— 当前增益 ——\n暂无生效中的 Buff")
        lines.extend(["", "输入 " + ("/帮助" if stage == "m0" else "/牧场 查看")])
        return "\n".join(lines)
    elif kind == "animal_release":
        return f"✅ 放养完成｜{NAMES[result['item']]} ×{result['quantity']}\n下一步：/牧场 | /喂食 精 | /收取"
    elif kind == "animal_recycle":
        return (f"✅ 回收完成｜{NAMES[result['item']]} ×{result['quantity']}｜其中✨闪光 ×{result['shiny']}\n"
                f"到账 {money(result['amount'])}币｜余额 {money(result['balance'])}币\n"
                "下一步：/牧场 | /买动物 鸡 1 | /收取")
    elif kind in ("buy", "sell", "upgrade"):
        lines = [{"buy": "✅ 购买完成", "sell": "✅ 出售完成 💰", "upgrade": "✅ 牧场升级完成"}[kind]]
        if kind == "upgrade":
            lines.append(f"当前等级：{result['level']}｜总容量：{result['capacity']}")
        for row in result["lines"]:
            lines.append(f"{NAMES.get(row['item'], row['item'])} ×{row['quantity']}" + (f"｜单价：{money(row['price'])}" if row.get("price") is not None else ""))
            if kind == "buy" and result.get("rarities"):
                from dzmm_bot.domain.animal_rarity import RARITY_RULESETS, RARITY_VERSION
                rare = [f"{rule['emoji']}{rule['name']} ×{result['rarities'].get(code, 0)}"
                        for code, rule in RARITY_RULESETS[RARITY_VERSION].items()
                        if result["rarities"].get(code, 0)]
                if rare:
                    lines.append("本次获得：" + "｜".join(rare))
            lines.append(f"{'毛收入' if kind == 'sell' else '商品额'}{money(row['gross'])}币｜{row.get('fee_label', '税额')}{money(row['tax'])}币｜{'到账' if kind == 'sell' else '实付'}{money(abs(row['net']))}币")
        lines.append(f"🪙 余额：{money(result['balance'])}币")
        if result.get("rarity_bonuses"):
            lines.append("✨ 增产触发：" + "｜".join(
                f"{NAMES[code]}产量基数 +{bonus}" for code, bonus in result["rarity_bonuses"].items()))
    elif kind == "market":
        lines = ["📈 铃露行情｜括号：基准｜相对基准涨幅"]
        entries = [f"{NAMES[row['item']]}{money(row['price'])}币（{money(row['base_price'])}｜{row['change_percent']:+.1f}%）{row['trend']}" for row in result["rows"]]
        lines.extend(entries)
        minutes = max(1, int((result["refresh_seconds"] + 59) // 60))
        lines.extend([f"下次刷新：{minutes}分钟后", "买入加价 5%｜出售手续费 5%｜每 30 分钟限购 200 币"])
        next_command = "/买入 鸡蛋 1"
    elif kind == "factory_start":
        recipe = result["recipe"]
        lines = [f"✅ 已投产：{recipe['display_name']} ×{result['count']}",
                 f"生产线：{result['line']}｜预计完成：{timestamp(result['finish_at'])}（香港）"]
        next_command = ""
    elif kind == "factory_start_batch":
        lines = ["✅ 已安排加工"]
        for item in result["results"]:
            recipe = item["recipe"]
            inputs = "｜".join(f"{NAMES[name]}×{count}" for name, count in item["inputs"].items())
            lines.append(f"{item['line']}号线：{recipe['display_name']}×1｜完成 {timestamp(item['finish_at'])}｜材料 {inputs}")
        next_command = "/加工厂"
    elif kind == "factory_collect":
        lines = ["📦 加工收货"]
        lines.extend(f"{row['recipe']['display_name']}：{'加工失败' if row['failed'] else ('暴击 ×2｜' if row['critical'] else '') + '获得 ' + NAMES[row['recipe']['output_item']] + ' ×' + str(row['quantity'])}" for row in result["results"])
        next_command = "/加工厂"
    elif kind == "factory_upgrade":
        lines = [f"✅ 加工厂升级至 Lv.{result['level']}", f"生产线：{result['line_count']}｜效率：×{result['duration_multiplier']}",
                 f"花费：{money(result['cost'])}币｜余额：{money(result['balance'])}"]
        next_command = "/加工厂"
    elif kind == "factory_core_upgrade":
        lines = ["✅ 自动化核心已消耗并记录", f"接口计数：{result['automation_level']} 次", "核心效果规则待配置"]
        next_command = "/加工厂"
    elif kind == "factory_expedite":
        lines = [f"⚡ {result['line']}号线已完成，等待取货", f"加急费用：{money(result['cost'])}｜今日 {result['daily_count']}/3 次",
                 f"🪙 余额：{money(result['balance'])}"]
        next_command = "/取货"
    elif kind == "factory_cancel":
        refunded = "｜".join(f"{NAMES[item]}×{amount}" for item, amount in result["refund"].items()) or "无材料返还（按整数向下取整）"
        lines = [f"已取消 {result['line']}号线生产", f"材料返还 {result['refund_rate'] * 100}%：{refunded}"]
        next_command = "/加工厂"
    elif kind == "buff_craft":
        lines = [f"✅ 补货完成：{NAMES[result['item']]}", "消耗材料：" + "｜".join(
            f"{NAMES[item]}×{amount}" for item, amount in result["rule"]["inputs"].items()),
            f"当前库存：{result['quantity']} 件"]
        next_command = f"/使用 {result['rule']['name']}"
    elif kind == "buff_activated":
        lines = [f"✅ 已启用 {result['rule']['emoji']} {result['rule']['name']}",
                 result["rule"]["description"], f"剩余 {long_duration(result['remaining'])}"]
        next_command = "/资料"
    elif kind == "feed":
        lines = ["✅ 喂食完成", f"喂养动物：{result.get('animals', 1)} 只｜消耗饲料：{result['quantity']}｜剩余：{result['remaining']}"]
        bought = result.get("auto_purchased") or {}
        if bought:
            lines.append("库存不足，已自动购买：" + "、".join(
                f"{NAMES[item]}×{amount}" for item, amount in bought.items()))
        if result.get("premium_quantity"):
            lines.append(f"消耗精饲料：{result['premium_quantity']}｜剩余：{result['premium_remaining']}")
        collected = result.get("auto_collected") or {}
        lines.append("🧺 自动收取：" + ("｜".join(
            f"{NAMES[item]}×{amount}" for item, amount in collected.items()) or "暂无成熟产出"))
        if result.get("auto_harvest_bonuses"):
            lines.append("✨ 增产触发：" + "｜".join(
                f"{NAMES[item]}产量基数 +{amount}"
                for item, amount in result["auto_harvest_bonuses"].items()))
        lines.extend(["饲料有效期：12 小时", f"🪙 余额：{money(result['balance'])}"])
    elif kind == "harvest":
        lines = ["📦 收获完成", "｜".join(f"{NAMES[k]} ×{v}" for k, v in result["totals"].items()) or "暂无到期产出",
                 f"🪙 余额：{money(result['balance'])}"]
        if 'farm_totals' in result:
            lines.insert(2,'🌾 农场：'+('｜'.join(f'{NAMES[k]} ×{v}' for k,v in result['farm_totals'].items()) or '暂无成熟作物'))
        if result.get("rarity_bonuses"):
            lines.append("✨ 增产触发：" + "｜".join(
                f"{NAMES[code]}产量基数 +{bonus}" for code, bonus in result["rarity_bonuses"].items()))
        harvests={**result.get('farm_totals',{}),**result['totals']}
        if harvests:
            item, amount = next(iter(harvests.items()))
            next_command = f"/出售 {NAMES[item]} {amount}"
        else:
            next_command = "/牧场 查看"
    elif kind == "ranch":
        lines = ["🌾 我的牧场", f"等级：{result['level']}｜容量：{result['used_capacity']}/{result['capacity']}",
                 f"可收获：{result['pending']} 个", "库存：" + "｜".join(f"{NAMES[k]} {v}" for k, v in result["stocks"].items()),
                 "当前单价：" + "｜".join(f"{NAMES[k]} {money(v)}" for k, v in result["prices"].items())]
        groups = {}
        for animal in result["animals"]:
            groups.setdefault(animal["animal_type"], []).append(animal)
        for animal_type in ("chicken", "sheep", "cow"):
            grouped = groups.get(animal_type, [])
            if grouped:
                running = sum(animal["production_until"] > result["now"] for animal in grouped)
                stopped = len(grouped) - running
                status = f"生产中 {running}"
                if stopped:
                    status += f"｜已停产 {stopped}"
                lines.append(f"{NAMES[animal_type]}×{len(grouped)}｜{status}")
        lines.append(f"动物列表：{result['page']}/{result['pages']} 页")
        next_command = f"/牧场 查看 {result['page'] + 1}" if result["page"] < result["pages"] else "/牧场 收获"
    elif kind == "ranking":
        lines = ["🏆 财富排行榜｜按钱包余额排名"]
        previous = 0
        for row in result['rows']:
            if row['rank'] > previous + 1:
                lines.append('……')
            lines.append(f"{row['rank']}. {name_escape(row['name'])}｜{money(row['balance'])}币" + ('｜你' if row['own'] else ''))
            previous = row['rank']
        if result['own_rank'] is None:
            lines.append('你尚未进入排行榜｜/注册')
        next_command = '/钱包'
    else:
        raise ValueError(kind)
    if result.get("more"):
        lines.append("仍有待结算产出，请继续收获")
        next_command = "/牧场 收获"
    if stage == "ranch" and next_command.startswith("/牧场 出售"):
        next_command = "/牧场 查看"
    return "\n".join(lines) + (f"\n\n输入 {next_command}" if next_command else "")

"""Traditional Chinese aliases for fixed command vocabulary only.

Player-defined names and announcement bodies are intentionally left untouched.
"""

COMMAND_ALIASES = {
    '/強制接生': '/强制接生',
    '/成馬': '/成马',
    '/管理員開賽': '/开赛', '/管理员开赛': '/开赛',
    '/開賽': '/开赛', '/強制開賽': '/强制开赛',
    '/抢': '/抢红包', '/搶': '/抢红包', '/搶紅包': '/抢红包', '/發紅包': '/发红包',
    '/收取': '/收获',
    '/天氣': '/天气',
    "/排行榜": "/排行", "/富豪榜": "/排行", "/財富榜": "/排行",
    "/財富排行榜": "/排行", "/財富排行": "/排行",
    "/庫存": "/库存", "/回覆頁": "/回复页",
    "/農田詳情": "/农田详情",
    "/註冊": "/注册", "/資料": "/资料", "/錢包": "/钱包", "/簽到": "/签到",
    "/救濟": "/救济", "/幫助": "/帮助",
    "/管理员登录": "/管理员登陆", "/管理員登錄": "/管理员登陆",
    "/管理員登入": "/管理员登陆",
    "/邀請列表": "/邀请列表", "/同意邀請": "/同意邀请",
    "/轉帳": "/转账", "/管理員發幣": "/管理员发币",
    "/管理員發道具": "/管理员发道具",
    "/菜單": "/菜单", "/怎麼玩": "/怎么玩", "/待辦": "/待办", "/總榜": "/排行",
    "/牧場": "/牧场", "/買動物": "/买动物", "/餵食": "/喂食",
    "/買精飼料": "/买精饲料", "/牧場升級": "/牧场升级",
    "/買入": "/买入", "/賣出": "/出售", "/加工廠": "/加工厂",
    "/投產": "/投产", "/取貨": "/取货", "/播種": "/播种", "/收穫": "/收获",
    "/加工廠升級": "/加工厂升级", "/取消投產": "/取消投产",
    "/補貨": "/补货", "/馬廄": "/马厩", "/馬厩": "/马厩",
    "/買馬": "/买马", "/馬": "/马", "/馬匹命名": "/马匹命名",
    "/餵馬": "/喂马", "/血統": "/血统", "/馬廄升級": "/马厩升级",
    "/馬厩升級": "/马厩升级", "/馬冊": "/马册",
    "/農場": "/农场", "/種子商店": "/种子商店", "/買種子": "/买种子",
    "/農場升級": "/农场升级", "/配製": "/配制",
    "/馬糧商店": "/马粮商店", "/買馬糧": "/买马粮",
    "/更新日誌": "/更新日志", "/創建更新": "/创建更新",
    "/預覽更新": "/预览更新", "/發布更新": "/发布更新", "/發佈更新": "/发布更新",
    "/公告狀態": "/公告状态", "/公告失敗": "/公告失败",
    "/重試公告": "/重试公告", "/市場買入": "/市场买入",
    "/回收": "/回收",
    "/放養": "/放养",
}

VALUE_ALIASES = {
    "閃光雞": "闪光鸡", "閃光羊": "闪光羊", "閃光牛": "闪光牛",
    "牧場": "牧场", "農場": "农场", "交易": "交易", "總榜": "总榜",
    "查看": "查看", "購買": "购买", "餵食": "喂食", "收穫": "收获",
    "升級": "升级", "賣出": "出售", "出售": "出售", "全部": "全部",
    "雞": "鸡", "雞蛋": "鸡蛋", "飼料": "饲料", "精飼料": "精饲料",
    "禮盒": "礼盒", "羽絨服": "羽绒服", "奶酪拼盤": "奶酪拼盘",
    "大禮包": "大礼包", "銅鈴": "铜铃", "開工宴": "开工宴",
    "大師餐": "大师餐", "工裝圍裙": "工装围裙", "禮服": "礼服",
    "羊毛": "羊毛", "牛奶": "牛奶",
    "馬廄": "马厩", "馬厩": "马厩", "馬糧": "马粮",
    "燕麥": "燕麦", "胡蘿蔔": "胡萝卜", "蘋果": "苹果",
    "苜蓿": "苜蓿", "大麥": "大麦", "牧草": "牧草",
    "疾風燕麥": "疾风燕麦", "強壯胡蘿蔔": "强壮胡萝卜",
    "聰慧蘋果": "聪慧苹果", "堅韌穀物": "坚韧谷物",
    "優質牧草": "优质牧草", "普通草料": "普通草料",
    "高能牧草": "高能牧草", "自動化核心": "自动化核心",
}


def normalize_command_parts(parts):
    if not parts:
        return parts
    normalized = list(parts)
    # Split only documented numeric-only commands; never split player names/passwords.
    import re
    numeric_commands = {"/转账", "/买精饲料", "/回复页", "/加急", "/取消投产", "/拍", '/发红包'}
    for spelling in sorted(numeric_commands | {key for key, value in COMMAND_ALIASES.items()
                                              if value in numeric_commands}, key=len, reverse=True):
        match = re.fullmatch(re.escape(spelling) + r"([+-]?[0-9]+(?:\.[0-9]+)?)", normalized[0])
        if match:
            normalized = [spelling, match[1], *normalized[1:]]
            break
    normalized[0] = COMMAND_ALIASES.get(normalized[0], normalized[0])
    from dzmm_bot.domain.farm_rules import CROP_NAMES, FEED_NAMES
    from dzmm_bot.domain.factory_rules import RECIPE_NAMES
    vocabularies = {
        "/播种": set(CROP_NAMES), "/买种子": set(CROP_NAMES),
        "/配制": set(FEED_NAMES), "/买马粮": set(FEED_NAMES),
        "/投产": set(RECIPE_NAMES), "/配方": set(RECIPE_NAMES),
        "/买动物": {"鸡", "羊", "牛"},
        "/回收": {"鸡", "羊", "牛"},
        "/放养": {"闪光鸡", "闪光羊", "闪光牛"},
        "/买入": set(RECIPE_NAMES) | set(CROP_NAMES) | {"鸡蛋", "羊毛", "牛奶"},
        "/出售": set(RECIPE_NAMES) | set(CROP_NAMES) | {"鸡蛋", "羊毛", "牛奶"},
    }
    # Longest command and item first, with a complete suffix match.
    spellings = {key: key for key in vocabularies}
    spellings.update({key: value for key, value in COMMAND_ALIASES.items() if value in vocabularies})
    for spelling in sorted(spellings, key=len, reverse=True):
        command_name = spellings[spelling]
        if not normalized[0].startswith(spelling):
            continue
        tail = normalized[0][len(spelling):]
        if not tail:
            break
        options = vocabularies[command_name]
        options = options | {key for key, value in VALUE_ALIASES.items() if value in options}
        for item in sorted(options, key=len, reverse=True):
            match = re.fullmatch(re.escape(item) + r"([+-]?[0-9]+)?", tail)
            if match:
                normalized = [command_name, item, *([match[1]] if match[1] else []), *normalized[1:]]
                break
        break
    command = normalized[0]
    if command in vocabularies and len(normalized) == 2:
        options = vocabularies[command] | {key for key, value in VALUE_ALIASES.items()
                                         if value in vocabularies[command]}
        for item in sorted(options, key=len, reverse=True):
            match = re.fullmatch(re.escape(item) + r"([+-]?[0-9]+)", normalized[1])
            if match:
                normalized = [command, item, match[1]]
                break
    # Only slots with a fixed game vocabulary are converted. Names and free text stay raw.
    fixed_slots = {
        "/铃露玩法": (1,), "/菜单": (1,), "/怎么玩": (1,), "/排行": (1,),
        "/牧场": (1, 2), "/买动物": (1,), "/喂食": (1,),
        "/买入": (1,), "/出售": (1,), "/投产": (1,), "/加工": (1,),
        "/加工厂升级": (1,), "/补货": (1,), "/使用": (1,),
        "/买种子": (1,), "/播种": (1,), "/配制": (1,),
        "/买马粮": (1,), "/喂马": (2,),
        "/管理员发道具": (1,),
    }.get(command, ())
    for index in fixed_slots:
        if index < len(normalized):
            normalized[index] = VALUE_ALIASES.get(normalized[index], normalized[index])
    return normalized

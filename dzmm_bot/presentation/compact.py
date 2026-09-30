"""P7.5 reply formatting: preserve content and paginate only at hard limits."""
import re

RELEASE = "P7.5"
ICONS = {"鸡蛋": "🥚", "羊毛": "🧶", "牛奶": "🥛", "牧草": "🌿", "燕麦": "🌾",
         "胡萝卜": "🥕", "大麦": "🌾", "苜蓿": "🍀", "苹果": "🍎", "小麦": "🌾",
         "向日葵": "🌻", "棉花": "🧵", "葡萄": "🍇", "速度": "🏃", "耐力": "🔋",
         "力量": "💪", "智慧": "🧠", "毅力": "🔥"}
ICONS.update({"鸡": "🐔", "羊": "🐑", "牛": "🐄", "饲料": "🌾", "精饲料": "✨",
    "闪光鸡": "✨🐔", "闪光羊": "✨🐑", "闪光牛": "✨🐄",
    "普通草料": "🌿", "疾风燕麦": "🌾", "高能牧草": "🌿", "强壮胡萝卜": "🥕",
    "聪慧苹果": "🍎", "坚韧谷物": "🌾", "优质牧草": "🌿",
    "蛋糕": "🍰", "毛衣": "🧥", "奶酪": "🧀", "礼盒": "🎁", "羽绒服": "🧥",
    "奶酪拼盘": "🧀", "大礼包": "🎁", "面包": "🍞", "葵花油": "🌻",
    "葡萄果酱": "🍇", "棉布": "🧵", "精致三明治": "🥪", "调味沙拉": "🥗",
    "水果蛋糕": "🍰", "棉质工装": "🧥", "自动化核心": "⚙️"})
from dzmm_bot.domain.farm_rules import CROPS, FEEDS
from dzmm_bot.domain.factory_rules import RECIPES
from dzmm_bot.domain.buff_rules import BUFFS
for _rule in CROPS.values():
    _name = _rule['name']
    ICONS.setdefault(_name, _rule.get('emoji', '🌱'))
    ICONS[_rule.get('seed_name', _name + '种子')] = ICONS[_name]
for _rule in FEEDS.values():
    ICONS.setdefault(_rule['name'], '🌾')
for _rule in RECIPES.values():
    ICONS.setdefault(_rule['display_name'], '📦')
for _rule in BUFFS.values():
    ICONS[_rule['name']] = _rule['emoji']
# A single longest-name substitution avoids inserting icons inside compound item names.
_LABEL_PATTERN = re.compile('|'.join(re.escape(word) for word in sorted(ICONS, key=len, reverse=True)))


def text_size(text):
    return len(text.encode("utf-16-le")) // 2


def fits(text, reserve=0):
    return text_size(text) + reserve <= 1000 and text.count("\n") <= 10


def decorate(text):
    def labels(part):
        def prefix(match):
            word = match.group()
            emoji = ICONS[word]
            before = part[:match.start()].rstrip()
            return word if before.endswith(emoji) else emoji + word
        return _LABEL_PATTERN.sub(prefix, part)
    return "".join(part if part.startswith("/") else labels(part)
                   for part in re.split(r"(/[^|｜\n]+)", text))


_COMPACT_PHRASES = (
    ("当前余额：", "余额："),
    ("当前余额:", "余额："),
    ("当前等级：", "等级："),
    ("当前等级:", "等级："),
    ("当前库存：", "库存："),
    ("当前库存:", "库存："),
    ("今天已经签到过了", "今日已签到"),
    ("你已注册全局游戏", "已注册"),
    ("当前没有待处理的群聊邀请", "暂无待处理邀请"),
    ("暂无到期且待接生的幼驹", "暂无待接生幼驹"),
    ("该配方需加工厂达到", "需加工厂达到"),
    ("所有加工线都在生产中", "加工线均在生产"),
    ("产品尚未完成，约需", "尚需约"),
    ("农场空地不足，当前只有", "农场空地仅剩"),
    ("输入 /", "/"),
    ("下一步：", ""),
    ("下一步:", ""),
    ("请使用 ", "用法："),
)


def _simplify_phrases(text):
    for old, new in _COMPACT_PHRASES:
        text = text.replace(old, new)
    return text


def compact_text(text, simplify=False):
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    if simplify:
        lines = [_simplify_phrases(line).strip() for line in lines]
    if len(lines) <= 10:
        return "\n".join(lines)
    for width in (90, 140, 200, 300, 950):
        packed = []
        for line in lines:
            separator = " | " if line.startswith("/") and packed and packed[-1].startswith("/") else "｜"
            if packed and text_size(packed[-1] + separator + line) <= width:
                packed[-1] += separator + line
            else:
                packed.append(line)
        if len(packed) <= 10:
            return "\n".join(packed)
    return "\n".join(packed)


def reply_pages(text, preserve_lines=False, simplify=False):
    decorated = decorate(text)
    if preserve_lines:
        body = _simplify_phrases(decorated) if simplify else decorated
    else:
        body = compact_text(decorated, simplify=simplify)
    if fits(body, reserve=5):
        return [body]
    pages, page = [], ""
    for line in body.splitlines():
        fragments, fragment = [], ""
        for char in line:
            if text_size(fragment + char) > 900:
                fragments.append(fragment)
                fragment = ""
            fragment += char
        if fragment:
            fragments.append(fragment)
        for fragment in fragments:
            candidate = page + ("\n" if page else "") + fragment
            if not fits(candidate, reserve=80) or candidate.count("\n") > 8:
                if page:
                    pages.append(page)
                page = fragment
            else:
                page = candidate
    if page:
        pages.append(page)
    return pages

"""Versioned, non-secret player-facing update manifests."""
import json
import re
from pathlib import Path

VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MANIFEST_DIR = Path(__file__).with_name("updates")

BUILTIN_FEATURES = (
    {"id": "ranch", "name": "牧场", "emoji": "🏡", "short_description": "饲养动物、喂食并收取农产品",
     "details": "购买鸡、羊或牛，保持饲料充足，收取产品后可查看行情。",
     "commands": ["/牧场", "/买动物 鸡 1", "/喂食", "/收取"],
     "tips": ["断喂会暂停生产；已经产出的库存不会消失。"]},
    {"id": "market", "name": "行情与交易", "emoji": "📈", "short_description": "查看全服行情并买入商品",
     "details": "行情按半小时刷新；买入有加价，并受周期限额约束。",
     "commands": ["/行情", "/买入 鸡蛋 1", "/出售 鸡蛋 1"],
     "tips": ["比较当前价与基准价，再决定买入或出售。"]},
    {"id": "factory", "name": "加工厂", "emoji": "🏭", "short_description": "把农产品加工成其他商品",
     "details": "查看生产线与 T1/T2 配方，将牧场或农场原料投产，完成后取货并查看行情。",
     "commands": ["/加工厂", "/加工配方 T1", "/加工配方 T2", "/投产 面包", "/取货"],
     "tips": ["投产前检查配方材料和生产线状态。"]},
    {"id": "buffs", "name": "道具与增益", "emoji": "🧴", "short_description": "制作并使用道具增益",
     "details": "先查看可制作的增益道具，再补货制作并使用。",
     "commands": ["/补货 毛刷", "/使用 毛刷"],
     "tips": ["每种增益的效果和持续时间以游戏内说明为准。"]},
    {"id": "horses", "name": "马厩", "emoji": "🐴", "short_description": "买马、培养、繁育并查看血统",
     "details": "用 1000 币买马，使用马粮培养五维属性。成年母马与公马可消耗优质牧草和冬宴币繁育，6 小时后接生。",
     "commands": ["/马厩", "/买马", "/喂马 名字 疾风燕麦", "/繁育 母马 公马", "/接生", "/血统 名字"],
     "tips": ["每匹马每天最多培养 5 次；近亲马匹不能繁育。"]},
    {"id": "farm", "name": "农场", "emoji": "🌱", "short_description": "种植十种作物，制作马粮或加工商品",
     "details": "六种马粮作物可配制马粮；小麦、向日葵、棉花、葡萄可看行情出售，或交给加工厂制作商品。",
     "commands": ["/农场", "/种子商店", "/播种 小麦", "/收获", "/行情", "/配制 疾风燕麦 1"],
     "tips": ["优质牧草每天最多直接购买 4 份；自产配制不受商店限购影响。"],
     "trigger_commands": ["/牧场", "/马厩"],
     "onboarding_tip": "六种作物已经可以种植，收获后能配制马粮。"},
    {"id": "auction", "name": "拍卖场", "emoji": "🔨", "short_description": "全服同一时间竞拍一件拍品",
     "details": "拍卖持续 60 秒。/拍 或 /跟 每次加价 100 币；/拍 金额可直接出价。最后 10 秒有新出价会重新倒数 10 秒。",
     "commands": ["/拍卖场", "/拍卖 名称 起拍价", "/寄拍 马 名字 起拍价", "/拍", "/跟", "/拍 金额"],
     "tips": ["竞拍价会暂时冻结；被超价后自动释放。成交收取 8% 佣金。"]},
)


def load_manifest(version):
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        return None
    path = MANIFEST_DIR / f"{version}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("version") != version:
        return None
    required = ("title", "theme", "summary", "features", "commands", "tips", "changelog")
    if any(not value.get(key) for key in required):
        return None
    if any(not isinstance(value[key], str) or len(value[key]) > limit for key, limit in
           (("title", 160), ("theme", 40), ("summary", 500))):
        return None
    if "closing" in value and (not isinstance(value["closing"], str) or len(value["closing"]) > 200):
        return None
    if "published_date" in value and not isinstance(value["published_date"], str):
        return None
    if not isinstance(value["features"], list) or not value["features"] or not isinstance(value["commands"], list) or not isinstance(value["tips"], list):
        return None
    for feature in value["features"]:
        if not isinstance(feature, dict) or any(not isinstance(feature.get(k), str) or not feature[k]
                for k in ("id", "name", "short_description", "details")):
            return None
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,49}", feature["id"]):
            return None
        if any(key in feature and not isinstance(feature[key], str) for key in ("emoji", "onboarding_tip")):
            return None
        if any(key in feature and not isinstance(feature[key], list)
               for key in ("commands", "tips", "trigger_commands")):
            return None
        if any(not isinstance(text, str) for key in ("commands", "tips", "trigger_commands")
               for text in feature.get(key, [])):
            return None
    if not all(isinstance(item, str) or (isinstance(item, dict) and isinstance(item.get("command"), str)
            and isinstance(item.get("description", ""), str)) for item in value["commands"]):
        return None
    if not all(isinstance(item, str) for item in value["tips"]):
        return None
    changelog = value["changelog"]
    if not isinstance(changelog, dict) or any(key in changelog and
            (not isinstance(changelog[key], (str, list)) or
             (isinstance(changelog[key], list) and not all(isinstance(item, str) for item in changelog[key])))
            for key in ("added", "changed", "fixed", "migration", "known_issues")):
        return None
    return value

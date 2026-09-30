from dzmm_bot.domain.horse_rules import TRAITS, stage, feed_budget
from dzmm_bot.presentation.formatters import money, name_escape
from .racing import affinity_lines, recommendation, inheritance_lines, AFFINITY_NAMES
from dzmm_bot.domain.horse_catalog import birth_catalog

TRAIT_NAMES = {"speed": "速度", "stamina": "耐力", "power": "力量", "wisdom": "智慧", "grit": "毅力"}


def horse_label(row):
    return f"🐴{name_escape(row['name']) if row['name'] else '未命名'}"


def _catalog_category(catalog, category):
    key = 'recommended_' + category
    options = catalog.get(key + '_options') or [catalog[key]]
    return '/'.join(AFFINITY_NAMES[value] for value in options)


def render_horse(result):
    kind = result["kind"]
    if kind == "horse_stable":
        upgrade = f"升级需 {money(result['upgrade_cost'])} 币" if result.get("upgrade_cost") is not None else "已满级"
        lines = [f"🐴 我的马厩｜Lv.{result['level']}/5｜{upgrade}｜占用{len(result['horses']) + len(result['pending']) + result.get('locked', 0)}/{result['capacity']}"]
        for row in result["horses"]:
            lines.append(f"{horse_label(row)}｜{'公' if row['sex'] == 'male' else '母'}｜G{row['generation']}｜{stage(row['born_at'], result['now'])}｜总属性 {sum(row[t] for t in TRAITS)}｜{recommendation(row)}")
        if not result["horses"]:
            lines.append("暂无马匹｜起步：/买马 → /马厩 → /配方")
        if result["pending"]:
            ready = sum(row["due_at"] <= result["now"] for row in result["pending"])
            lines.append(f"待出生 {len(result['pending'])}｜可接生 {ready}")
        if result.get("locked"):
            lines.append(f"拍卖锁定 {result['locked']} 匹｜/拍卖场")
        if result.get("holding"):
            lines.append(f"拍卖场托管 {result['holding']} 匹｜/领取拍卖马")
        lines.append("/买马 | /马 名字 | /马册 | /马厩升级")
        return "\n".join(lines)
    if kind == "horse_detail":
        row = result["horse"]
        lines = [f"{horse_label(row)}｜{'公' if row['sex'] == 'male' else '母'}｜G{row['generation']}｜#{row['id'][:8]}",
                 f"年龄阶段：{stage(row['born_at'], result['now'])}｜状态：{'在厩' if row['status'] == 'active' else '已退役'}",
                 "｜".join(f"{TRAIT_NAMES[t]} {row[t]}（成长{row['growth_' + t]}）" for t in TRAITS),
                 f"总属性：{sum(row[t] for t in TRAITS)}｜繁育次数：{row['breeding_count']}/5",
                 f"喂食次数：{feed_budget(row, result['now'])[0]}/5｜累计 {row['feed_count']}/30｜每小时恢复1次",
                 "/喂马 名字 马粮 | /血统 名字 | /马匹命名 编号 新名"]
        catalog=row.get('catalog') or birth_catalog(row)
        if catalog:
            lines.insert(2, '🏁适合赛程：'+_catalog_category(catalog, 'distance')
                +'｜🏇推荐跑法：'+_catalog_category(catalog, 'style')
                +'｜🌱推荐场地：'+_catalog_category(catalog, 'surface'))
        return "\n".join([*lines[:-1], *affinity_lines(row), lines[-1]])
    if kind == "horse_lineage":
        row = result["horse"]
        lines = [f"📜 {horse_label(row)} 的血统｜G{row['generation']}"]
        for label, parent in result["parents"]:
            lines.append(f"{label}：{horse_label(parent) if parent else '始祖马'}")
        for label, grandparent in result["grandparents"]:
            lines.append(f"{label}：{horse_label(grandparent) if grandparent else '无记录'}")
        return "\n".join([*lines, *inheritance_lines(row)])
    if kind == "horse_studbook":
        lines = ["📚 我的马册"]
        for row in result["horses"]:
            lines.append(f"{horse_label(row)}｜G{row['generation']}｜{'在厩' if row['status'] == 'active' else '已退役'}")
        return "\n".join(lines) if result["horses"] else "📚 马册暂无记录。"
    if kind == "horse_buy":
        row=result['horse']; catalog=birth_catalog(row)
        lines=[f"✅ 已购买 {horse_label(row)}"]
        if catalog:
            lines.append('🏁赛程：'+_catalog_category(catalog, 'distance')
                +'｜🏇跑法：'+_catalog_category(catalog, 'style')
                +'｜🌱场地：'+_catalog_category(catalog, 'surface'))
        lines.extend([f"花费：{money(result['cost'])} 币｜余额：{money(result['balance'])} 币",
            f"/马 {row['name']} | /马匹命名 {row['name']} 新名"])
        return '\n'.join(lines)
    if kind == "horse_upgrade":
        return f"✅ 马厩升级至 Lv.{result['level']}｜容量 {result['capacity']}\n花费：{money(result['cost'])} 币｜余额：{money(result['balance'])} 币"
    if kind == "horse_rename":
        return f"✅ 马匹已命名为 {horse_label(result['horse'])}"
    if kind == "horse_retire":
        return f"✅ {horse_label(result['horse'])} 已退役，血统记录仍保留。"
    if kind == "horse_feed":
        gains = "｜".join(f"{TRAIT_NAMES[trait]} +{amount}" for trait, amount in result["gains"].items())
        return (f"🐴 {horse_label(result['horse'])}吃下了{result['feed']}\n{gains}\n"
                f"喂食次数 {result['remaining']}/5｜累计 {result['feed_count']}/30｜每小时恢复1次")
    if kind == "horse_breed":
        return (f"💞 {horse_label(result['mother'])}与{horse_label(result['father'])}配种成功\n"
                f"消耗优质牧草×2 和 {money(result['cost'])}币\n6小时后可 /接生")
    if kind == "horse_delivery":
        lines = ["🐴 幼驹出生！"]
        for foal in result["foals"]:
            snapshot = foal["snapshot"]
            qualities = snapshot.get("qualities") or {}
            excellent = [TRAIT_NAMES[t] for t, quality in qualities.items()
                         if t in TRAIT_NAMES and quality == 2]
            lines.append(f"{name_escape(foal['name'])}（#{foal['id'][:8]}）｜G{foal['generation']}｜{'公' if snapshot['sex'] == 'male' else '母'}｜总属性 {sum(snapshot['traits'].values())}")
            if excellent:
                lines.append("✨ 卓越遗传：" + "、".join(excellent))
        lines.append("/马匹命名 编号 新名 | /马厩")
        return "\n".join(lines)
    raise ValueError(f"unknown horse result: {kind}")

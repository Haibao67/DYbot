from datetime import datetime

from dzmm_bot.domain.economy import HK
from dzmm_bot.domain.update_manifest import BUILTIN_FEATURES
from .formatters import name_escape


def _date(value):
    return datetime.fromtimestamp(value, HK).strftime("%Y-%m-%d") if value else "待发布"


def render_announcement(manifest):
    lines = [f"🎉 铃露更新公告 · v{manifest['version']}",
             f"{manifest['theme']} {name_escape(manifest['title'])}",
             name_escape(manifest["summary"])]
    for feature in manifest["features"]:
        emoji = feature.get("emoji", "✨")
        lines.append(f"{emoji} {name_escape(feature['name'])}：{name_escape(feature['short_description'])}")
    if manifest.get("commands"):
        command_lines = []
        for command in manifest["commands"]:
            if isinstance(command, dict):
                command_lines.append(f"{name_escape(command.get('command', ''))}：{name_escape(command.get('description', ''))}".rstrip("："))
            elif isinstance(command, str):
                command_lines.append(command)
        lines.extend(" | ".join(command_lines[index:index + 2]) for index in range(0, len(command_lines), 2))
    if manifest.get("tips"):
        lines.append("💡 " + "｜".join(name_escape(tip) for tip in manifest["tips"]))
    lines.append(manifest.get("closing", "祝各位老板玩得开心！"))
    if manifest.get("published_date"):
        lines.append(f"发布时间：{name_escape(manifest['published_date'])}")
    from .compact import compact_text, decorate
    return compact_text(decorate("\n".join(lines)))


def render_update(update, admin=False):
    manifest = update["manifest"]
    lines = [render_announcement(manifest),
             f"发布时间：{_date(update.get('published_at') or update['created_at'])}"]
    if admin:
        changelog = update.get("changelog", {})
        lines.extend(["", "—— 管理员技术记录 ——"])
        for key, label in (("added", "Added"), ("changed", "Changed"), ("fixed", "Fixed"),
                           ("migration", "Migration"), ("known_issues", "Known Issues")):
            values = changelog.get(key, [])
            if isinstance(values, str):
                values = [values] if values else []
            lines.append(f"{label}：" + ("；".join(str(v) for v in values) if values else "无"))
    return "\n".join(lines)


def render_update_history(rows):
    if not rows:
        return "📜 暂无已发布的更新记录。"
    lines = ["📜 铃露更新记录"]
    lines.extend(f"v{row['version']} {name_escape(row['theme'])} {name_escape(row['title'])}｜{_date(row['published_at'])}" for row in rows)
    lines.append("查看详情：/更新 版本号")
    return "\n".join(lines)


def render_features(features):
    lines = ["🌾 铃露玩法指南"]
    entries = [f"{feature.get('emoji', '✨')} {feature['name']}：/怎么玩 {feature['id']}" for feature in features]
    lines.extend(" | ".join(entries[index:index + 3]) for index in range(0, len(entries), 3))
    return "\n".join(lines) if len(lines) > 1 else "目前没有已开放的玩法指南。"


def render_feature(feature):
    lines = [f"{feature.get('emoji', '✨')} {feature['name']}怎么玩？", "", feature["details"], "", "常用指令："]
    lines.append(" | ".join(feature.get("commands", [])[:6]))
    if feature.get("tips"):
        lines.extend(["", "💡 " + feature["tips"][0]])
    return "\n".join(lines)


def render_daily(market_rows, latest_update, recommendations):
    lines = ["📅 铃露今日情报"]
    if market_rows:
        lines.extend(["📈 当前行情", *[f"{r['item']} {r['price']}币 {r['trend']}" for r in market_rows[:5]]])
    if latest_update:
        lines.extend([f"🆕 最新更新：v{latest_update['version']} {name_escape(latest_update['title'])}", "查看：/更新"])
    if recommendations:
        lines.extend(["💡 今日建议", *[f"· {x}" for x in recommendations[:3]]])
    if len(lines) == 1:
        lines.append("今天暂无已启用的天气、活动或新版本情报。")
    return "\n".join(lines)


def render_todos(items):
    if not items:
        return "📋 你的铃露待办\n目前没有待处理事项。"
    return "📋 你的铃露待办\n" + "\n".join(f"{item['text']}｜{item['action_command']}" for item in items)


def render_broadcast_status(version, job, deliveries):
    if not job:
        return f"v{version} 尚无广播记录。"
    counts = {state: sum(row["status"] == state for row in deliveries)
              for state in ("success", "queued", "sending", "failed")}
    pending = counts["queued"] + counts["sending"]
    lines = [f"📢 v{version} 广播状态", f"目标群：{job['target_count']}",
             f"✅ 成功：{counts['success']}", f"⏳ 等待/发送中：{pending}", f"❌ 失败：{counts['failed']}"]
    if job.get("finished_at"):
        lines.append(f"完成时间：{_date(job['finished_at'])}")
    return "\n".join(lines)


def render_world_event(event_type, player, quantity=None, item=None, product=None):
    if event_type == "large_harvest":
        label = {"egg": "鸡蛋", "wool": "羊毛", "milk": "牛奶"}.get(item, item or "农产品")
        return (f"🌾 铃露快讯\n\n{name_escape(player)}刚刚一次收获了 {int(quantity)} 个农产品！\n"
                f"{label}收获最多，行情可以查看 /行情。")
    if event_type == "factory_critical":
        return (f"✨ 铃露快讯\n\n【{name_escape(player)}】的加工厂出现了暴击！\n"
                f"一批【{name_escape(product)}】获得双倍产出！")
    raise ValueError("unsupported world event type")


def feature_catalog(dynamic_features=(), stage="full"):
    enabled_ids = {"ranch", "market", "factory", "buffs", "horses", "farm", "auction"} if stage == "full" else (
        {"ranch", "market"} if stage == "ranch" else set())
    features = [dict(item) for item in BUILTIN_FEATURES if item["id"] in enabled_ids]
    indexes = {item["id"]: index for index, item in enumerate(features)}
    disabled_dynamic = {"factory"} if stage != "full" else set()
    for feature in dynamic_features:
        feature_id = feature.get("id")
        if stage == "m0" or feature_id in disabled_dynamic:
            continue
        if feature_id in indexes:
            features[indexes[feature_id]] = feature
        else:
            indexes[feature_id] = len(features)
            features.append(feature)
    return features

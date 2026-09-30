"""Feature discovery, one-time onboarding tips, and provider-based personal todos."""
import json

from sqlalchemy import func, select

from dzmm_bot.domain.update_manifest import BUILTIN_FEATURES
from dzmm_bot.persistence.schema import (game_updates, player_feature_tips, animals, products,
                                         factory_jobs, farm_plots, horse_pregnancies, auctions)
from dzmm_bot.presentation.information import feature_catalog, render_feature, render_features, render_todos
from .race_todo_provider import RaceTodoProvider


class RanchTodoProvider:
    def __init__(self, db, player, now):
        self.db, self.player, self.now = db, player, now

    def items(self):
        rows = self.db.execute(select(animals).where(animals.c.player_id == self.player,
            animals.c.status == "active")).mappings().all()
        result = []
        pending = self.db.execute(select(func.coalesce(func.sum(products.c.quantity), 0)).where(
            products.c.player_id == self.player, products.c.collected_at.is_(None))).scalar_one()
        if pending:
            result.append({"priority": 0, "due_at": self.now, "text": f"📦 有 {pending} 件牧场产品待收取",
                           "action_command": "/收取"})
        ready = [a for a in rows if a["next_production_at"] <= self.now and a["production_until"] >= self.now]
        if ready and not pending:
            result.append({"priority": 0, "due_at": self.now, "text": "🌾 牧场有新一批产品可结算",
                           "action_command": "/收取"})
        expired = [a for a in rows if a["production_until"] <= self.now]
        if expired:
            result.append({"priority": 0, "due_at": self.now,
                           "text": f"🌾 {len(expired)} 只动物饲料已耗尽，生产暂停",
                           "action_command": "/喂食"})
        soon = [a for a in rows if self.now < a["production_until"] <= self.now + 6 * 3600]
        if soon:
            hours = min(a["production_until"] for a in soon) - self.now
            result.append({"priority": 1, "due_at": self.now + hours,
                           "text": f"🌾 {len(soon)} 只动物的饲料将在约 {max(1, int(hours / 60))} 分钟后到期",
                           "action_command": "/喂食"})
        return result


class FactoryTodoProvider:
    def __init__(self, db, player, now):
        self.db, self.player, self.now = db, player, now

    def items(self):
        jobs = self.db.execute(select(factory_jobs).where(factory_jobs.c.player_id == self.player,
            factory_jobs.c.status.in_(["processing", "completed_pending_collect"])).order_by(factory_jobs.c.finish_at)).mappings().all()
        ready = [job for job in jobs if job["status"] == "completed_pending_collect" or job["finish_at"] <= self.now]
        result = []
        if ready:
            result.append({"priority": 0, "due_at": self.now, "text": f"🏭 {len(ready)} 条生产线可以取货",
                           "action_command": "/取货"})
        pending = [job for job in jobs if job not in ready]
        if pending:
            minutes = max(1, int((min(j["finish_at"] for j in pending) - self.now + 59) / 60))
            result.append({"priority": 1, "due_at": min(j["finish_at"] for j in pending),
                           "text": f"🏭 最近一条生产线约 {minutes} 分钟后完成",
                           "action_command": "/加工厂"})
        return result


class FarmTodoProvider:
    def __init__(self, db, player, now):
        self.db, self.player, self.now = db, player, now

    def items(self):
        plots = self.db.execute(select(farm_plots).where(farm_plots.c.player_id == self.player,
            farm_plots.c.crop_code.is_not(None))).mappings().all()
        ready = [plot for plot in plots if plot["ready_at"] <= self.now]
        growing = [plot for plot in plots if plot["ready_at"] > self.now]
        items = []
        if ready:
            from collections import Counter
            from dzmm_bot.domain.farm_rules import ECONOMIC_CROPS
            names = Counter(plot["crop_code"] for plot in ready if plot["crop_code"] in ECONOMIC_CROPS)
            summary = "｜".join(f"{ECONOMIC_CROPS[code]['name']}×{count}" for code, count in names.items())
            items.append({"priority": 0, "due_at": self.now,
                          "text": f"🌱 {len(ready)} 块土地可收获" + (f"（{summary}）" if summary else ""),
                          "action_command": "/收获"})
        if growing:
            due = min(plot["ready_at"] for plot in growing)
            items.append({"priority": 1, "due_at": due,
                          "text": f"🌱 最近一块土地约 {max(1, int((due - self.now + 59) / 60))} 分钟后成熟",
                          "action_command": "/农场"})
        return items


class HorseTodoProvider:
    def __init__(self, db, player, now):
        self.db, self.player, self.now = db, player, now

    def items(self):
        pregnancies = self.db.execute(select(horse_pregnancies).where(
            horse_pregnancies.c.player_id == self.player,
            horse_pregnancies.c.status == "pending")).mappings().all()
        ready = [row for row in pregnancies if row["due_at"] <= self.now]
        waiting = [row for row in pregnancies if row["due_at"] > self.now]
        items = []
        if ready:
            items.append({"priority": 0, "due_at": self.now,
                          "text": f"🐴 {len(ready)} 匹幼驹可以接生", "action_command": "/接生"})
        if waiting:
            due = min(row["due_at"] for row in waiting)
            items.append({"priority": 1, "due_at": due,
                          "text": f"🐴 幼驹约 {max(1, int((due - self.now + 59) / 60))} 分钟后出生",
                          "action_command": "/马厩"})
        return items


class AuctionTodoProvider:
    def __init__(self, db, player, now):
        self.db, self.player, self.now = db, player, now

    def items(self):
        from dzmm_bot.presentation.formatters import name_escape
        row = self.db.execute(select(auctions).where(auctions.c.status == "active",
            (auctions.c.seller_id == self.player) | (auctions.c.highest_bidder_id == self.player)
            )).mappings().first()
        if not row:
            return []
        role = "你当前领先" if row["highest_bidder_id"] == self.player else "你发起的拍卖正在进行"
        return [{"priority": 1, "due_at": row["ends_at"],
                 "text": f"🔨【{name_escape(row['title'])}】{role}，约 {max(0, int(row['ends_at'] - self.now))} 秒结束",
                 "action_command": "/拍卖场"}]


class TodoService:
    PROVIDER_TYPES = [RanchTodoProvider, FactoryTodoProvider, FarmTodoProvider,
                      HorseTodoProvider, AuctionTodoProvider, RaceTodoProvider]

    def __init__(self, db, player, now, providers=None):
        self.providers = list(providers) if providers is not None else [
            provider(db, player, now) for provider in self.PROVIDER_TYPES]

    def register_provider(self, provider):
        if not callable(getattr(provider, "items", None)):
            raise TypeError("todo provider must implement items()")
        self.providers.append(provider)

    @classmethod
    def register_provider_type(cls, provider_type):
        if not callable(provider_type):
            raise TypeError("todo provider type must be callable")
        if provider_type not in cls.PROVIDER_TYPES:
            cls.PROVIDER_TYPES.append(provider_type)

    def render(self):
        items = [item for provider in self.providers for item in provider.items()]
        items.sort(key=lambda item: (item["priority"], item["due_at"], item["text"]))
        return render_todos(items)


class GuidanceService:
    def __init__(self, db, stage="full", now=0):
        self.db, self.stage, self.now = db, stage, now

    def features(self):
        rows = self.db.execute(select(game_updates.c.manifest_json).where(
            game_updates.c.status == "published").order_by(game_updates.c.published_at)).scalars().all()
        by_id = {}
        for raw in rows:
            for feature in json.loads(raw).get("features", []):
                by_id[feature["id"]] = feature
        return feature_catalog(list(by_id.values()), self.stage)

    def how_to(self, query=None):
        features = self.features()
        if not query:
            return render_features(features)
        q = query.strip().casefold().lstrip("/")
        feature = next((f for f in features if q in {str(f.get("id", "")).casefold(),
            str(f.get("name", "")).casefold()} or q in [str(c).split()[0].lstrip("/").casefold()
                for c in f.get("commands", []) if isinstance(c, str)]), None)
        if not feature:
            return f"暂时找不到「{query}」的已开放玩法指南。\n输入 /怎么玩 查看目录。"
        return render_feature(feature)

    def maybe_tip(self, player, command):
        if not player:
            return None
        for feature in self.features():
            tip = feature.get("onboarding_tip")
            triggers = feature.get("trigger_commands", [])
            if not tip or command not in triggers:
                continue
            exists = self.db.execute(select(player_feature_tips.c.player_id).where(
                player_feature_tips.c.player_id == player, player_feature_tips.c.feature_id == feature["id"])).first()
            if exists:
                continue
            self.db.execute(player_feature_tips.insert().values(player_id=player, feature_id=feature["id"],
                shown_at=self.now, dismissed=False, completed=False))
            command_text = feature.get("commands", ["/怎么玩"])[0]
            return f"🆕 {feature['name']}：{feature.get('short_description', '')}｜{command_text}"
        return None


class DailyBriefService:
    def __init__(self, db, now, player=None, weather_enabled=False):
        self.db, self.now, self.player = db, now, player
        self.weather_enabled = bool(weather_enabled)

    def render(self):
        import json
        from dzmm_bot.domain.economy import market_price, market_window, market_trend
        from dzmm_bot.persistence.schema import configs, world, game_updates
        from dzmm_bot.presentation.messages import NAMES
        market_rows, recommendations = [], []
        config_version = self.db.execute(select(world.c.config_version).where(world.c.id == 1)).scalar()
        raw = self.db.execute(select(configs.c.payload_json).where(configs.c.version == config_version,
            configs.c.status == "published")).scalar() if config_version else None
        if raw:
            from dzmm_bot.domain.market_catalog import with_tradeable_products
            config = with_tradeable_products(json.loads(raw))
            window, _ = market_window(self.now)
            for code, rule in config["products"].items():
                price = market_price(code, self.now, config)
                previous = market_price(code, (window - 1) * 1800, config)
                trend, label = market_trend(price, previous, rule["price"])
                item = NAMES.get(code, code)
                market_rows.append({"item": item, "price": price, "trend": trend, "label": label})
                if label in ("走强", "高涨") and len(recommendations) < 2:
                    recommendations.append(f"{item}行情走强，可查看 /行情")
        row = self.db.execute(select(game_updates.c.version, game_updates.c.title, game_updates.c.published_at).where(
            game_updates.c.status == "published", game_updates.c.published_at >= self.now - 7 * 86400)
            .order_by(game_updates.c.published_at.desc()).limit(1)).mappings().first()
        from dzmm_bot.presentation.information import render_daily
        farm_ready = self.db.execute(select(func.count()).select_from(farm_plots).where(
            farm_plots.c.player_id == self.player, farm_plots.c.crop_code.is_not(None),
            farm_plots.c.ready_at <= self.now)).scalar_one() if self.player else 0
        daily = render_daily(market_rows, dict(row) if row else None, recommendations)
        from .weather_service import WeatherService
        weather = WeatherService(self.db,self.now,enabled=self.weather_enabled).player_summary()
        if weather["enabled"]:
            daily += f"\n{weather['emoji']} 今日天气：{weather['name']}｜{weather['description']}"
        auction = self.db.execute(select(auctions.c.title, auctions.c.current_price,
            auctions.c.ends_at, auctions.c.highest_bidder_id).where(
            auctions.c.status == "active")).mappings().first()
        if auction:
            from dzmm_bot.domain.economy import from_minor
            from dzmm_bot.presentation.formatters import name_escape
            daily += (f"\n🔨 当前拍卖：【{name_escape(auction['title'])}】"
                      f"｜{from_minor(auction['current_price'])} 币"
                      f"｜约 {max(0, int(auction['ends_at'] - self.now))} 秒"
                      f"｜{'你当前领先' if auction['highest_bidder_id'] == self.player else '/拍卖场'}")
        from dzmm_bot.domain.farm_rules import ECONOMIC_CROPS
        economic = [row for row in market_rows if row["item"] in
                    {rule["name"] for rule in ECONOMIC_CROPS.values()}]
        if economic:
            daily += "\n🌱 经济作物行情：" + "｜".join(
                f"{row['item']} {row['label']}" for row in economic[:4])
        from dzmm_bot.persistence.schema import race_definitions
        race = self.db.execute(select(race_definitions).where(
            race_definitions.c.status.in_(['REGISTRATION','LOCKED','RUNNING'])).order_by(
            race_definitions.c.starts_at).limit(1)).mappings().first()
        if race:
            from dzmm_bot.presentation.formatters import name_escape
            from datetime import datetime
            from zoneinfo import ZoneInfo
            from .race_queries import STATUS_NAMES
            definition=json.loads(race['definition_json'])
            zone=ZoneInfo(definition.get('timezone','Asia/Shanghai'))
            close=datetime.fromtimestamp(race['registration_close_at'],zone).strftime('%H:%M')
            start=datetime.fromtimestamp(race['starts_at'],zone).strftime('%H:%M')
            daily += f"\n🏇 {name_escape(race['name'])}｜{STATUS_NAMES[race['status']]}｜{close}截止/{start}开赛｜/赛程"
        return daily + (f"\n🌱 农场：{farm_ready} 块土地已成熟｜/收获" if farm_ready else "")

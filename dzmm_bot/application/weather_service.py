"""Persistent server-wide daily weather, deterministic effects and outbox notice."""
import json
import uuid
from datetime import timedelta

from sqlalchemy import select

from dzmm_bot.domain.weather_rules import (WEATHER, WEATHER_RULE_VERSION,
    PROBABILITY_CONFIG_VERSION, adjust_base, choose_weather, date_bounds, daily_seed,
    effect_percent, game_date)
from dzmm_bot.persistence.schema import daily_weather
from dzmm_bot.persistence.transport import rooms, outbox


class WeatherService:
    def __init__(self, db, now, enabled=False):
        self.db, self.now, self.enabled = db, now, bool(enabled)

    def today(self):
        if not self.enabled:
            return None
        return self.for_date(game_date(self.now))

    def for_date(self, day):
        if not self.enabled:
            return None
        return self.db.execute(select(daily_weather).where(
            daily_weather.c.game_date == day.isoformat())).mappings().first()

    def reconcile(self):
        """Backfill missed dates through tomorrow; only today's record is broadcast."""
        if not self.enabled:
            return {"created": 0, "broadcasts": 0}
        from datetime import date
        current = game_date(self.now)
        latest = self.db.execute(select(daily_weather.c.game_date).order_by(
            daily_weather.c.game_date.desc()).limit(1)).scalar()
        first = date.fromisoformat(latest) + timedelta(days=1) if latest else current
        created = 0
        target = current + timedelta(days=1)
        while first <= target:
            self._create(first)
            first += timedelta(days=1)
            created += 1
        broadcasts = self._announce_today(current)
        return {"created": created, "broadcasts": broadcasts}

    def _create(self, day):
        key = day.isoformat()
        if self.db.execute(select(daily_weather.c.game_date).where(
                daily_weather.c.game_date == key)).first():
            return
        seed = daily_seed(day)
        code = choose_weather(day)
        start, end = date_bounds(day)
        definition = {"weather_code": code, "weather_rule_version": WEATHER_RULE_VERSION,
                      "probability_config_version": PROBABILITY_CONFIG_VERSION,
                      "effects": {"sunny": {"ranch": 50, "fodder": 50},
                                  "rainy": {"wool": 80, "economic_crops": 50},
                                  "drought": {"egg": -30, "crops": -50},
                                  "humid": {"milk": -30, "fodder": 20},
                                  "breeze": {"economic_crops": 20},
                                  "harvest_festival": {"all": 150}}.get(code, {})}
        self.db.execute(daily_weather.insert().values(game_date=key, weather_code=code,
            weather_rule_version=WEATHER_RULE_VERSION,
            probability_config_version=PROBABILITY_CONFIG_VERSION, seed=seed,
            generated_at=self.now, effective_from=start, effective_until=end,
            definition_snapshot_json=json.dumps(definition, ensure_ascii=False, sort_keys=True),
            broadcast_enqueued_at=None))

    def _announce_today(self, day):
        row = self.for_date(day)
        if not row or row["broadcast_enqueued_at"] is not None:
            return 0
        if row["weather_code"] not in WEATHER or row["weather_rule_version"] != WEATHER_RULE_VERSION:
            return 0
        targets = self.db.execute(select(rooms.c.id).where(rooms.c.enabled == 1,
            rooms.c.kind == "group").order_by(rooms.c.id)).scalars().all()
        if not targets:
            return 0
        weather = WEATHER[row["weather_code"]]
        body = f"🌦️ 铃露今日天气｜{day.isoformat()}\n{weather['emoji']} {weather['name']}｜{self._description(row['weather_code'])}\n天气按香港时间每日 00:00 更新。"
        for room_id in targets:
            task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ruihe-weather:{day.isoformat()}:{room_id}"))
            exists = self.db.execute(select(outbox.c.id).where(outbox.c.id == task_id)).first()
            if not exists:
                self.db.execute(outbox.insert().values(id=task_id, room=room_id, kind="group",
                    reply_to_message_id=None, reply_to_sender_id=None, reply_to_text=None,
                    text=body, status="pending", created=self.now, available=self.now, attempts=0))
        self.db.execute(daily_weather.update().where(daily_weather.c.game_date == day.isoformat())
            .values(broadcast_enqueued_at=self.now))
        return len(targets)

    def adjust(self, *, day, base, key, product=None, crop_code=None):
        row = self.for_date(day)
        if not row or row["weather_rule_version"] != WEATHER_RULE_VERSION:
            return base, 0, None
        percent = effect_percent(row["weather_code"], product=product, crop_code=crop_code)
        result, delta = adjust_base(base, percent, seed=row["seed"], key=key)
        return result, delta, row["weather_code"]

    def player_summary(self):
        row = self.today()
        if not row:
            try:
                from dzmm_bot.domain.weather_rules import validate_weather_config
                validate_weather_config()
            except ValueError:
                return {"enabled": False, "invalid_config": True,
                        "game_date": game_date(self.now).isoformat()}
            return {"enabled": False, "game_date": game_date(self.now).isoformat()}
        code = row["weather_code"]
        if code not in WEATHER or row["weather_rule_version"] != WEATHER_RULE_VERSION:
            return {"enabled": False, "invalid_config": True, "game_date": row["game_date"]}
        return {"enabled": True, "game_date": row["game_date"], "code": code,
                "name": WEATHER[code]["name"], "emoji": WEATHER[code]["emoji"],
                "description": self._description(code), "effective_until": row["effective_until"]}

    @staticmethod
    def _description(code):
        return {"sunny": "牧场动物、农场马粮作物增产概率 +50%",
                "rainy": "羊毛增产概率 +80%；经济作物 +50%",
                "drought": "鸡蛋减产概率 30%；所有作物减产概率 50%",
                "humid": "牛奶减产概率 30%；马粮作物增产概率 +20%",
                "breeze": "经济作物增产概率 +20%",
                "harvest_festival": "牧场动物及所有作物增产概率 +150%"}.get(code, "无增减产效果")

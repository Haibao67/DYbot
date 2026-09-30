"""M0/M1 integration tests run only against isolated temporary databases."""
import json
import tempfile
import unittest
from decimal import Decimal, ROUND_HALF_UP
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.exc import IntegrityError
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient
from dzmm_bot.core import Inbound, create_app
from dzmm_bot.settings import Settings
from dzmm_bot.store import Store
from dzmm_bot.persistence.transport import meta as transport, rooms, players, inbox, outbox
from dzmm_bot.persistence.schema import (accounts, currency, stocks, inventory, world, animals,
    products, transactions, ranches, audit, configs, events)
from dzmm_bot.application.services import GameService, metrics
from dzmm_bot.domain.economy import (RANCH_CONFIG, RUIHE_RANCH_CONFIG, RUIHE_RANCH_VERSION,
    GameError, tax, capacity, upgrade_price, local_date, interval, market_price, seed_for,
    production_multiplier, production_quantity, market_trend, market_change_percent, to_minor, from_minor)
from dzmm_bot.presentation.messages import error, render, help_text
from dzmm_bot.presentation.formatters import money
from dzmm_bot.application.verification import verify_production


class GameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.url = "sqlite:///" + str(Path(self.temp.name) / "game.db")
        self.now = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
        self.store = Store(self.url, secret="test-secret", clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert(), [{"id": "g1", "kind": "group", "enabled": 1},
                {"id": "g2", "kind": "group", "enabled": 1}, {"id": "dm", "kind": "private", "enabled": 1},
                {"id": "off", "kind": "group", "enabled": 0}])
        self.number = 0
        self.last_message_id = None

    def send(self, command, player="u", room="g1", mid=None):
        self.number += 1
        self.last_message_id = mid or str(self.number)
        result = self.store.receive(Inbound(room=room, sender=player, name="玩家" + player,
            message_id=self.last_message_id, text=command))
        return result

    def rows(self, table):
        with self.store.engine.connect() as db:
            return [dict(r) for r in db.execute(select(table)).mappings()]

    def reply(self):
        with self.store.engine.connect() as db:
            parts = db.execute(select(outbox.c.text).where(
                outbox.c.reply_to_message_id == self.last_message_id).order_by(outbox.c.id)).scalars().all()
        return "\n".join(parts)

    def compensate(self, amount, player="u", ticket=None):
        with self.store.engine.begin() as db:
            return GameService(db, self.now, "test-secret", player).admin_action(
                "compensate", "test", amount=amount, reason="test", ticket=ticket or str(self.number) + str(amount))

    def assert_reconciled(self):
        with self.store.engine.connect() as db:
            result = metrics(db)
        self.assertEqual(result["balance_difference"], 0)
        self.assertEqual(result["account_mismatches"], [])
        self.assertEqual(result["pool_tax_difference"], 0)
        self.assertEqual(result["pool_ledger_difference"], 0)
        for stock in self.rows(stocks):
            total = sum(r["delta"] for r in self.rows(inventory) if r["player_id"] == stock["player_id"] and r["item_code"] == stock["item_code"])
            self.assertEqual(total, stock["quantity"])

    def test_hundred_concurrent_retries_cross_room_and_restart(self):
        event = Inbound(room="g1", sender="u", name="平台昵称", message_id="same", text="/加入")
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.store.receive(event), range(100)))
        self.assertEqual(sum(r.get("queued", False) for r in results), 1)
        self.send("/加入", room="g2")
        self.assertIn("已注册", self.reply())
        self.send("/我的", room="dm")
        self.assertIn("余额：100", self.reply())
        restarted = Store(self.url)
        self.addCleanup(restarted.engine.dispose)
        self.assertTrue(restarted.receive(event)["duplicate"])
        self.assertEqual(len(self.rows(accounts)), 1)
        self.assertEqual(len(self.rows(currency)), 1)
        self.assert_reconciled()

    def test_multiple_store_connections_serialize(self):
        other = Store(self.url, clock=lambda: self.now)
        self.addCleanup(other.engine.dispose)
        def join(i):
            return (self.store if i % 2 else other).receive(Inbound(
                room="g1" if i % 2 else "g2", sender="u", name="平台昵称",
                message_id=str(i), text="/加入"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(join, range(100)))
        self.assertEqual(len(self.rows(currency)), 1)

    def test_relief_edges_and_hong_kong_midnight(self):
        for balance in [0, 30, 99, 100, 101]:
            player = str(balance)
            self.send("/加入", player=player)
            if balance != 100:
                self.compensate(balance - 100, player)
            self.send("/救济", player=player)
            found = next(r for r in self.rows(accounts) if r["player_id"] == player)
            self.assertEqual(found["balance"], max(balance, 100) * 100)
            self.assertEqual(found["relief_claimed_on"], local_date(self.now) if balance < 100 else None)
        self.compensate(-50, "0", "spend")
        self.send("/救济", player="0")
        self.assertIn("今天已领取", self.reply())
        self.now += 16 * 3600
        self.send("/救济", player="0")
        self.assertIn("补足：50", self.reply())
        self.assert_reconciled()

    def test_permissions_unknown_and_english(self):
        self.send("/加入", room="off")
        self.assertEqual(self.rows(inbox), [])
        self.send("/加入", room="dm")
        self.assertIn("游戏群", self.reply())
        for command in ["/join", "/help", "/profile", "/start", "/不存在"]:
            self.send(command)
            self.assertIn("未识别的指令", self.reply())
        self.assertEqual(self.rows(accounts), [])
        self.send("/加入")
        with self.store.engine.begin() as db:
            GameService(db, self.now, "test", "u").admin_action("freeze", "test")
        for command in ["/救济", "/牧场 购买 鸡", "/牧场 收获", "/牧场 升级"]:
            self.send(command)
            self.assertIn("账户已冻结", self.reply())
        self.send("/我的")
        self.assertIn("账户状态：冻结", self.reply())
        self.assertEqual(len(self.rows(currency)), 1)

    def test_purchase_tax_capacity_failure_and_idempotency(self):
        self.send("/加入")
        self.send("/牧场 购买 鸡", mid="buy")
        self.assertEqual(self.rows(accounts)[0]["balance"], 4700)
        self.assertEqual(self.rows(world)[0]["lottery_pool_balance"], 300)
        self.assertTrue(self.send("/牧场 购买 鸡", mid="buy")["duplicate"])
        self.send("/牧场 购买 牛")
        self.assertIn("余额不足", self.reply())
        self.assertEqual(len(self.rows(animals)), 1)
        self.compensate(10000)
        self.send("/牧场 购买 牛")
        self.send("/牧场 购买 羊")
        self.assertIn("还缺 1 格", self.reply())
        self.send("/牧场 购买 鸡 1")
        self.assertEqual(self.rows(ranches)[0]["used_capacity"], 5)
        before = self.rows(animals)
        self.send("/牧场 升级")
        self.assertEqual(len(before), len(self.rows(animals)))
        self.assertEqual(self.rows(animals)[0]["interval_level"], 2)
        self.send("/牧场 购买 羊")
        self.assertEqual(self.rows(ranches)[0]["used_capacity"], 7)
        self.assert_reconciled()

    def test_production_restart_feed_harvest_sale(self):
        self.send("/加入")
        self.send("/牧场 购买 鸡")
        self.send("/牧场 购买 饲料 2")
        animal = self.rows(animals)[0]
        self.now = animal["next_production_at"]
        self.send("/牧场 查看")
        self.assertEqual(len(self.rows(products)), 1)
        self.send("/牧场 查看")
        self.assertEqual(len(self.rows(products)), 1)
        self.send("/牧场 喂食 " + animal["display_id"])
        fed = self.rows(animals)[0]
        self.assertEqual(fed["production_until"], self.now + 12 * 3600)
        self.assertGreater(fed["next_production_at"], self.now)
        collected = self.rows(products)[0]
        self.assertEqual(collected["collected_at"], self.now)
        eggs = next(r for r in self.rows(stocks) if r["item_code"] == "egg")
        self.assertEqual(eggs["quantity"], collected["quantity"])
        self.send("/收取")
        self.assertEqual(next(r for r in self.rows(stocks) if r["item_code"] == "egg")["quantity"], eggs["quantity"])
        self.now += 100 * 3600
        old = self.store
        self.store = Store(self.url, clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)
        old.engine.dispose()
        self.send("/牧场 收获")
        count = len(self.rows(products))
        self.assertGreater(count, 0)
        self.assertTrue(all(p["produced_at"] <= fed["production_until"] for p in self.rows(products)))
        self.send("/牧场 收获")
        self.assertEqual(len(self.rows(products)), count)
        egg_count = next(r for r in self.rows(stocks) if r["item_code"] == "egg")["quantity"]
        self.send(f"/牧场 出售 鸡蛋 {egg_count}", mid="sale")
        self.assertIn("到账", self.reply())
        self.assertTrue(self.send(f"/牧场 出售 鸡蛋 {egg_count}", mid="sale")["duplicate"])
        self.assertEqual(next(r for r in self.rows(stocks) if r["item_code"] == "egg")["quantity"], 0)
        self.send("/牧场 喂食 " + animal["display_id"])
        self.assertIn("喂食完成", self.reply())
        self.assert_reconciled()

    def _buy_test_chicken(self, feed=0, premium=0):
        self.send("/加入")
        self.send("/买动物 鸡")
        if feed:
            self.send(f"/牧场 购买 饲料 {feed}")
        if premium:
            self.send(f"/买精饲料 {premium}")
        return self.rows(animals)[0]

    def test_new_animal_has_12h_feed(self):
        animal = self._buy_test_chicken()
        self.assertEqual(animal["production_until"], self.now + 12 * 3600)
        self.assertEqual(animal["rule_version"], RUIHE_RANCH_VERSION)

    def test_feed_extends_12h(self):
        animal = self._buy_test_chicken(feed=1)
        self.now += 12 * 3600
        self.send("/喂食")
        animal = self.rows(animals)[0]
        self.assertEqual(animal["production_until"], self.now + 12 * 3600)

    def test_feed_does_not_reroll_cycle(self):
        animal = self._buy_test_chicken(feed=2)
        self.now += 3600
        self.send("/喂食")
        refed = self.rows(animals)[0]
        self.assertEqual(refed["seed"], animal["seed"])
        self.assertGreater(refed["cycle_progress"], 0)

    def test_unfed_animal_stops_production(self):
        animal = self._buy_test_chicken()
        self.now = animal["production_until"] + 8 * 3600
        self.send("/收取")
        first_count = len(self.rows(products))
        self.assertTrue(all(p["produced_at"] <= animal["production_until"] for p in self.rows(products)))
        self.now += 2 * 3600
        self.send("/收取")
        self.assertEqual(len(self.rows(products)), first_count)

    def test_products_do_not_spoil(self):
        animal = self._buy_test_chicken()
        self.now = animal["next_production_at"]
        self.send("/牧场")
        product = self.rows(products)[0]
        self.now += 30 * 24 * 3600
        self.send("/牧场")
        current = next(p for p in self.rows(products) if p["id"] == product["id"])
        self.assertIsNone(current["collected_at"])

    def test_affection_increases_on_timely_feed(self):
        self._buy_test_chicken(feed=1)
        self.now += 12 * 3600
        self.send("/喂食")
        self.assertEqual(self.rows(animals)[0]["affection"], 1)

    def test_affection_cap_10(self):
        animal = self._buy_test_chicken(feed=1)
        with self.store.engine.begin() as db:
            db.execute(animals.update().where(animals.c.id == animal["id"]).values(
                affection=9, last_feed_reward_at=self.now - 12 * 3600))
        self.send("/喂食")
        self.assertEqual(self.rows(animals)[0]["affection"], 10)

    def test_affection_resets_after_10h_overdue(self):
        animal = self._buy_test_chicken()
        with self.store.engine.begin() as db:
            db.execute(animals.update().where(animals.c.id == animal["id"]).values(affection=5, feed_streak=3))
        self.now = animal["production_until"] + 10 * 3600 + 1
        self.send("/牧场")
        row = self.rows(animals)[0]
        self.assertEqual((row["affection"], row["feed_streak"]), (0, 0))

    def test_feed_streak_three(self):
        self._buy_test_chicken(feed=3)
        for _ in range(3):
            self.now = self.rows(animals)[0]["production_until"]
            self.send("/喂食")
        self.assertEqual(self.rows(animals)[0]["feed_streak"], 3)

    def test_feed_streak_reset(self):
        animal = self._buy_test_chicken(feed=1)
        with self.store.engine.begin() as db:
            db.execute(animals.update().where(animals.c.id == animal["id"]).values(feed_streak=3))
        self.now = animal["production_until"] + 1
        self.send("/喂食")
        self.assertEqual(self.rows(animals)[0]["feed_streak"], 0)

    def test_premium_feed_consumption(self):
        animal = self._buy_test_chicken(feed=1, premium=1)
        self.send("/喂食 精")
        self.assertEqual(self.rows(stocks)[0]["quantity"], 0)
        self.assertEqual(next(r["quantity"] for r in self.rows(stocks) if r["item_code"] == "premium_feed"), 0)
        self.assertTrue(self.rows(animals)[0]["premium_feed_active"])

    def test_premium_feed_affection_bonus(self):
        animal = self._buy_test_chicken(feed=1, premium=1)
        self.send("/喂食 精")
        self.assertEqual(self.rows(animals)[0]["affection"], min(10, animal["affection"] + 2))

    def test_premium_feed_not_stackable_same_cycle(self):
        self._buy_test_chicken(feed=2, premium=2)
        self.send("/喂食 精")
        self.send("/喂食 精")
        stock = {r["item_code"]: r["quantity"] for r in self.rows(stocks)}
        self.assertEqual(stock["premium_feed"], 1)
        self.assertEqual(self.rows(animals)[0]["affection"], 3)

    def test_weather_panel_shows_disabled_until_odds_are_configured(self):
        self._buy_test_chicken()
        self.send("/牧场")
        first = self.reply()
        first_weather = next(line for line in first.splitlines() if "每日天气" in line)
        self.send("/牧场")
        second_weather = next(line for line in self.reply().splitlines() if "每日天气" in line)
        self.assertEqual(second_weather, first_weather)
        self.assertIn("暂未启用", first_weather)

    def test_weather_multiplier(self):
        self.assertAlmostEqual(production_multiplier(weather="sunny", product="egg"), 1.2)
        self.assertAlmostEqual(production_multiplier(weather="rainy", product="wool"), 1.5)
        self.assertAlmostEqual(production_multiplier(weather="drought", product="egg"), 0.7)
        self.assertAlmostEqual(production_multiplier(weather="humid", product="milk"), 0.8)
        self.assertAlmostEqual(production_multiplier(weather="breeze", product="milk"), 1.0)
        self.assertAlmostEqual(production_multiplier(weather="harvest_festival", product="egg"), 1.5)

    def test_ranch_upgrade_speed(self):
        self._buy_test_chicken()
        before = self.rows(animals)[0]
        self.compensate(1000)
        self.send("/牧场升级")
        after = self.rows(animals)[0]
        self.assertEqual(after["interval_level"], before["interval_level"] + 1)
        self.assertLess(after["next_production_at"] - after["last_settled_at"],
                        before["next_production_at"] - before["last_settled_at"])

    def test_collect_idempotent(self):
        animal = self._buy_test_chicken()
        self.now = animal["next_production_at"]
        self.send("/收取")
        first = {r["item_code"]: r["quantity"] for r in self.rows(stocks)}
        self.send("/收取")
        second = {r["item_code"]: r["quantity"] for r in self.rows(stocks)}
        self.assertEqual(second, first)

    def test_cycle_random_range_chicken(self):
        for sequence in range(100):
            base, _ = interval("fixed-seed", sequence, "chicken", 1, RUIHE_RANCH_CONFIG)
            self.assertGreaterEqual(base, 3)
            self.assertLessEqual(base, 5)

    def test_cycle_random_range_sheep(self):
        for sequence in range(100):
            base, _ = interval("fixed-seed", sequence, "sheep", 1, RUIHE_RANCH_CONFIG)
            self.assertGreaterEqual(base, 9)
            self.assertLessEqual(base, 12)

    def test_cycle_random_range_cow(self):
        for sequence in range(100):
            base, _ = interval("fixed-seed", sequence, "cow", 1, RUIHE_RANCH_CONFIG)
            self.assertGreaterEqual(base, 6)
            self.assertLessEqual(base, 10)

    def test_ranch_panel(self):
        self._buy_test_chicken()
        self.send("/牧场")
        panel = self.reply()
        self.assertIn("每日天气暂未启用", panel)
        self.assertNotIn("亲密0/10", panel)
        self.assertNotIn("无心", panel)
        self.assertNotIn("💗", panel)
        self.assertIn("饲料", panel)
        self.assertNotIn("精饲料 ×0", panel)
        self.assertEqual(panel.count("▓") + panel.count("░") > 0, True)

    def test_bad_quantities_and_cross_player_access(self):
        self.send("/加入")
        self.send("/牧场 购买 鸡")
        animal = self.rows(animals)[0]
        for qty in ["0", "-1", "1.5", "99999999999999999", "一", "１"]:
            self.send("/牧场 购买 饲料 " + qty)
            self.assertIn("正整数", self.reply())
        self.send("/加入", player="v")
        self.send("/牧场 喂食 " + animal["display_id"], player="v")
        self.assertIn("找不到可喂食动物", self.reply())
        self.assertEqual(len(self.rows(animals)), 1)
        self.assert_reconciled()

    def test_failed_sale_rolls_back_implicit_harvest(self):
        self.send("/加入")
        self.send("/牧场 购买 鸡")
        self.now = self.rows(animals)[0]["next_production_at"]
        before = self.rows(animals)
        self.send("/牧场 出售 鸡蛋 100")
        self.assertIn("不足", self.reply())
        self.assertEqual(self.rows(products), [])
        self.assertEqual(self.rows(stocks), [])
        self.assertEqual(self.rows(animals), before)
        self.assert_reconciled()

    def test_injected_crash_rolls_back_inbox_assets_and_outbox(self):
        with patch.object(GameService, "event", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.send("/加入", mid="crash")
        for table in [accounts, currency, inbox, outbox, ranches, players]:
            self.assertEqual(self.rows(table), [])
        self.send("/加入", mid="crash")
        self.assertEqual(self.rows(accounts)[0]["balance"], 10000)

    def test_ledger_idempotency_immutability_and_compensation(self):
        self.send("/加入")
        self.compensate(50, ticket="T1")
        self.assertTrue(self.compensate(50, ticket="T1")["duplicate"])
        with self.assertRaises(GameError):
            self.compensate(51, ticket="T1")
        with self.assertRaises(GameError):
            self.compensate(-151, ticket="T2")
        self.assertEqual(self.rows(accounts)[0]["balance"], 15000)
        with self.assertRaises(IntegrityError):
            with self.store.engine.begin() as db:
                db.execute(currency.update().values(amount=999))
        with self.assertRaises(IntegrityError):
            with self.store.engine.begin() as db:
                db.execute(configs.delete())
        self.assert_reconciled()

    def test_all_product_taxes_and_max_level(self):
        self.send("/加入")
        self.compensate(100000)
        for _ in range(8):
            self.send("/牧场 升级")
        self.send("/牧场 升级")
        self.assertIn("最高等级", self.reply())
        for name in ["鸡", "羊", "牛"]:
            self.send("/牧场 购买 " + name)
        self.now += 49 * 3600
        self.send("/收取")
        for name, code in (("鸡蛋", "egg"), ("羊毛", "wool"), ("牛奶", "milk")):
            count = next(row["quantity"] for row in self.rows(stocks) if row["item_code"] == code)
            self.send(f"/出售 {name} {count}")
        sales = [r for r in self.rows(transactions) if r["kind"] == "sell"]
        self.assertEqual(len(sales), 3)
        for sale in sales:
            self.assertEqual(sale["tax_amount"], int((Decimal(sale["gross_amount"]) * Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
            self.assertEqual(sale["net_amount"], sale["gross_amount"] - sale["tax_amount"])
        self.assert_reconciled()

    def test_system_reference_unique_and_business_replay(self):
        self.send("/加入")
        with self.store.engine.begin() as db:
            s = GameService(db, self.now, "s", "u")
            self.assertTrue(s.ledger(2, "tax", "test", "T", system=True))
            self.assertFalse(s.ledger(2, "tax", "test", "T", system=True))
        with self.assertRaises(IntegrityError):
            with self.store.engine.begin() as db:
                row = next(r for r in self.rows(currency) if r["player_id"] is None)
                db.execute(currency.insert().values(**{**row, "id": "different"}))

    def test_production_cap_and_replay_verification(self):
        config = json.loads(json.dumps(RUIHE_RANCH_CONFIG))
        config["animals"]["chicken"]["hours"] = [0.04, 0.04]
        config["production_hours"] = 48
        with self.store.engine.begin() as db:
            db.execute(configs.insert().values(version="fast-ruihe", payload_json=json.dumps(config),
                status="published", published_at=self.now))
        with patch("dzmm_bot.application.services.RUIHE_RANCH_VERSION", "fast-ruihe"), \
             patch("dzmm_bot.application.verification.RUIHE_RANCH_VERSION", "fast-ruihe"):
            self.send("/加入")
            with patch("dzmm_bot.application.services.purchase_rarity", return_value="shiny"):
                self.send("/牧场 购买 鸡")
            self.now += 48 * 3600
            self.send("/牧场 查看")
            self.assertEqual(len(self.rows(products)), 1000)
            self.assertIn("仍有产出待结算", self.reply())
            self.send("/牧场 收获")
            self.assertEqual(len(self.rows(products)), 1200)
            self.assertTrue(any(row["harvest_base_bonus"] > 0 for row in self.rows(products)))
            self.assertEqual(self.rows(animals)[0]["sequence"], 1201)
            self.send("/牧场 收获")
            self.assertEqual(len(self.rows(products)), 1200)
            with self.store.engine.connect() as db:
                verified = verify_production(db, self.now, "u")
            self.assertEqual(verified["products_verified"], 1200)
            self.assertEqual(verified["mismatches"], [])
            self.assert_reconciled()

    def test_concurrent_harvest_and_sell_once(self):
        self.send("/加入")
        self.send("/牧场 购买 鸡")
        self.now += 47 * 3600
        with self.store.engine.begin() as db:
            GameService(db, self.now, "test-secret", "u").settle()
        count = sum(row["quantity"] for row in self.rows(products) if row["product_type"] == "egg")
        self.assertGreater(count, 0)
        def command(i):
            return self.store.receive(Inbound(room="g1", sender="u", message_id=f"parallel-{i}",
                text="/牧场 收获" if i % 2 else f"/牧场 出售 鸡蛋 {count}"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(command, range(20)))
        self.assertEqual(len([r for r in self.rows(transactions) if r["kind"] == "sell"]), 1)
        self.assert_reconciled()

    def test_feed_failure_and_frozen_view_do_not_produce(self):
        self.send("/加入")
        self.send("/牧场 购买 鸡")
        animal = self.rows(animals)[0]
        self.now = animal["next_production_at"]
        self.send("/牧场 喂食 " + animal["display_id"])
        self.assertIn("喂食完成", self.reply())
        self.assertIn("已自动购买", self.reply())
        settled_products = len(self.rows(products))
        with self.store.engine.begin() as db:
            GameService(db, self.now, "s", "u").admin_action("freeze", "test")
        self.now += 3600
        self.send("/牧场 查看")
        self.assertEqual(len(self.rows(products)), settled_products)

    def test_pagination_and_balance_ranking(self):
        self.send("/加入")
        self.compensate(10000)
        self.send("/牧场 升级")
        self.send("/牧场 购买 鸡 7")
        self.send("/牧场 查看")
        self.assertIn("鸡×7", self.reply())
        self.send("/排行 总榜")
        self.assertIn("玩家u", self.reply())

    def test_ruihe_aliases_reuse_implemented_gameplay(self):
        self.send("/加入")
        self.send("/买动物 鸡 1")
        self.assertEqual(len(self.rows(animals)), 1)
        self.assertIn("✅ 购买完成", self.reply())
        self.send("/资料")
        self.assertIn("我的资料", self.reply())
        self.send("/收取")
        self.assertIn("收获完成", self.reply())
        self.send("/出售 鸡蛋 1")
        self.assertIn("缺少 1", self.reply())
        self.send("/牧场升级")
        self.assertIn("余额不足", self.reply())
        self.send("/牧场")
        self.assertIn("🏡", self.reply())
        self.send("/行情")
        self.assertIn("鸡蛋", self.reply())

    def test_factory_is_available_while_unimplemented_commands_remain_closed(self):
        self.send("/加入")
        self.send("/铃露玩法")
        menu = self.reply()
        self.assertIn("/牧场", menu)
        self.assertIn("尚未开放", menu)
        self.assertIn("/加工厂", menu)
        self.send("/买入 鸡蛋 1")
        self.assertIn("购买完成", self.reply())
        self.send("/加工厂")
        self.assertIn("加工厂 Lv.1", self.reply())
        self.send("/回收 鸡 1")
        self.assertIn("鸡不足", self.reply())
        self.assertIn("需要 1｜已有 0｜缺少 1", self.reply())
        self.assertLess(self.rows(accounts)[0]["balance"], 10000)
        self.send("/喂食")
        self.assertIn("找不到", self.reply())
        self.send("/买精饲料 1")
        self.assertIn("购买完成", self.reply())

    def test_staged_rollout_and_whitelist(self):
        self.send("/加入")
        self.store.game_stage = "m0"
        self.send("/帮助")
        self.assertNotIn("/牧场", self.reply())
        self.send("/牧场 购买 鸡")
        self.assertIn("暂未开放", self.reply())
        self.store.game_stage = "ranch"
        self.store.whitelist = {"v"}
        self.send("/牧场 购买 鸡")
        self.assertIn("白名单", self.reply())
        self.store.whitelist = {"u"}
        self.send("/牧场 购买 鸡")
        self.assertIn("购买完成", self.reply())
        self.send("/帮助")
        self.assertNotIn("/牧场 出售", self.reply())
        self.send("/牧场 出售 全部")
        self.assertIn("暂未开放", self.reply())
        self.assertEqual(len(self.rows(transactions)), 1)

    def market_stock(self, item="egg", amount=10):
        with self.store.engine.begin() as db:
            service = GameService(db, self.now, "test-secret", "u")
            service.inventory_change(item, amount, "test_seed", "market-test:" + item)

    def test_market_same_window_same_price(self):
        price = market_price("egg", self.now, RANCH_CONFIG)
        self.assertEqual(price, market_price("egg", self.now + 1799, RANCH_CONFIG))

    def test_market_changes_by_window(self):
        prices = [market_price("egg", self.now + tick * 1800, RANCH_CONFIG) for tick in range(12)]
        self.assertGreater(len(set(prices)), 1)

    def test_market_price_min_bound(self):
        for tick in range(100):
            for item in ("egg", "wool", "milk"):
                self.assertGreaterEqual(market_price(item, self.now + tick * 1800, RANCH_CONFIG),
                                        Decimal(RANCH_CONFIG["products"][item]["price"]) * Decimal("0.5"))

    def test_market_price_max_bound(self):
        for tick in range(100):
            for item in ("egg", "wool", "milk"):
                self.assertLessEqual(market_price(item, self.now + tick * 1800, RANCH_CONFIG),
                                     Decimal(RANCH_CONFIG["products"][item]["price"]) * Decimal("1.6"))

    def test_market_global_price(self):
        self.assertEqual(market_price("wool", self.now, RANCH_CONFIG),
                         market_price("wool", self.now, RUIHE_RANCH_CONFIG))

    def test_market_trend(self):
        self.assertEqual(market_trend(Decimal("6"), Decimal("5"), 5)[0], "📈")
        self.assertEqual(market_trend(Decimal("4"), Decimal("5"), 5)[0], "📉")
        self.assertEqual(market_trend(Decimal("5"), Decimal("5"), 5)[0], "➡️")
        self.assertEqual(market_trend(Decimal("3"), Decimal("5"), 5)[1], "低迷")
        self.assertEqual(market_change_percent(Decimal("4.47"), 5), Decimal("-10.6"))
        self.assertEqual(market_change_percent(Decimal("5.25"), 5), Decimal("5.0"))
        self.assertEqual(market_change_percent(Decimal("4.999"), 5), Decimal("0.0"))

    def test_market_command_and_same_window_repeat(self):
        self.send("/加入")
        self.send("/行情")
        first = self.reply()
        self.send("/行情")
        self.assertEqual(first, self.reply())
        self.assertIn("下次刷新", first)
        with self.store.engine.begin() as db:
            market = GameService(db, self.now, "test-secret", "u").market()
        egg = next(row for row in market["rows"] if row["item"] == "egg")
        self.assertEqual(egg["base_price"], 5)
        self.assertIn(f"（5｜{egg['change_percent']:+.1f}%）", first)

    def test_sell_requires_explicit_item_and_quantity(self):
        self.send("/加入")
        self.market_stock(amount=3)
        for command in ("/出售", "/出售 鸡蛋", "/出售 全部", "/牧场 出售 鸡蛋", "/牧场 出售 全部 3"):
            with self.subTest(command=command):
                self.send(command)
                self.assertIn("必须填写商品和数量", self.reply())
        self.assertEqual(next(row["quantity"] for row in self.rows(stocks) if row["item_code"] == "egg"), 3)
        self.assertEqual([row for row in self.rows(transactions) if row["kind"] == "sell"], [])

    def test_help_lists_current_commands_by_feature(self):
        self.send("/帮助")
        message = self.reply()
        for section in ("账号：", "资讯：", "牧场：", "交易：", "农场：", "加工：", "马厩：", "拍卖："):
            self.assertIn(section, message)
        detail = help_text("full", "交易")
        self.assertIn("/配方", message)
        self.assertIn("/出售 商品 数量", detail)
        self.assertNotIn("/出售 全部", detail)

    def test_sell_inventory_decrease(self):
        self.send("/加入")
        self.market_stock(amount=2)
        self.send("/出售 鸡蛋 2")
        self.assertEqual(self.rows(stocks)[0]["quantity"], 0)

    def test_sell_balance_increase(self):
        self.send("/加入")
        before = self.rows(accounts)[0]["balance"]
        self.market_stock(amount=2)
        self.send("/出售 鸡蛋 2")
        self.assertGreater(self.rows(accounts)[0]["balance"], before)

    def test_sell_fee_5_percent(self):
        self.send("/加入")
        self.market_stock(amount=3)
        self.send("/出售 鸡蛋 3")
        row = self.rows(transactions)[0]
        self.assertEqual(row["tax_amount"], int((Decimal(row["gross_amount"]) * Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))

    def test_sell_ledger(self):
        self.send("/加入")
        self.market_stock(amount=1)
        self.send("/出售 鸡蛋 1")
        self.assertIn("sell", [r["reason"] for r in self.rows(currency)])
        self.assert_reconciled()

    def test_buy_inventory_increase(self):
        self.send("/加入")
        self.send("/买入 鸡蛋 2")
        self.assertEqual(self.rows(stocks)[0]["quantity"], 2)

    def test_buy_balance_decrease(self):
        self.send("/加入")
        before = self.rows(accounts)[0]["balance"]
        self.send("/买入 鸡蛋 2")
        self.assertLess(self.rows(accounts)[0]["balance"], before)

    def test_buy_markup_5_percent(self):
        self.send("/加入")
        self.send("/买入 鸡蛋 1")
        row = next(row for row in self.rows(transactions) if row["market_window_id"] is not None)
        self.assertEqual(row["tax_amount"], int((Decimal(row["gross_amount"]) * Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
        self.assertEqual(row["unit_price_cents"], to_minor(market_price("egg", self.now, self._market_config())))

    def test_buy_ledger(self):
        self.send("/加入")
        self.send("/买入 鸡蛋 1")
        self.assertIn("buy", [row["reason"] for row in self.rows(currency)])
        self.assert_reconciled()

    def test_market_buy_does_not_create_money(self):
        self.send("/加入")
        before = self.rows(accounts)[0]["balance"] + self.rows(world)[0]["lottery_pool_balance"]
        self.send("/买入 鸡蛋 1")
        row = next(row for row in self.rows(transactions) if row["market_window_id"] is not None)
        after = self.rows(accounts)[0]["balance"] + self.rows(world)[0]["lottery_pool_balance"]
        self.assertEqual(before - after, row["gross_amount"])

    def _market_config(self):
        with self.store.engine.connect() as db:
            version = db.execute(select(world.c.config_version)).scalar_one()
            payload = db.execute(select(configs.c.payload_json).where(configs.c.version == version)).scalar_one()
        return json.loads(payload)

    def test_buy_window_limit_200(self):
        self.send("/加入")
        self.compensate(1000)
        for _ in range(8):
            self.send("/买入 鸡蛋 10")
        spent = sum(-row["net_amount"] for row in self.rows(transactions) if row["market_window_id"] is not None)
        self.assertLessEqual(spent, 20000)
        self.assertIn("限额", self.reply())

    def test_buy_limit_persists_in_window(self):
        self.send("/加入")
        self.compensate(1000)
        for _ in range(10):
            self.send("/买入 鸡蛋 10")
        before = len([row for row in self.rows(transactions) if row["market_window_id"] is not None])
        self.send("/买入 鸡蛋 10")
        self.assertEqual(len([row for row in self.rows(transactions) if row["market_window_id"] is not None]), before)
        self.assertIn("限额", self.reply())

    def test_buy_limit_resets_next_window(self):
        self.send("/加入")
        self.compensate(1000)
        for _ in range(10):
            self.send("/买入 鸡蛋 10")
        self.now = (int(self.now // 1800) + 1) * 1800
        self.send("/买入 鸡蛋 1")
        self.assertIn("购买完成", self.reply())

    def test_buy_insufficient_balance(self):
        self.send("/加入")
        price = market_price("egg", self.now, self._market_config())
        count = int(Decimal("150") / (price * Decimal("1.05")))
        self.send(f"/买入 鸡蛋 {count}")
        self.assertIn("余额不足", self.reply())

    def test_sell_insufficient_inventory(self):
        self.send("/加入")
        self.send("/出售 鸡蛋 1")
        self.assertIn("不足", self.reply())

    def test_invalid_quantity(self):
        self.send("/加入")
        self.send("/买入 鸡蛋 0")
        self.assertIn("正整数", self.reply())

    def test_unknown_market_item(self):
        self.send("/加入")
        self.send("/买入 宝石 1")
        self.assertIn("不是可交易", self.reply())
        self.send("/出售 宝石 1")
        self.assertIn("不是可交易", self.reply())

    def test_duplicate_market_message_is_idempotent(self):
        self.send("/加入")
        first = self.send("/买入 鸡蛋 1", mid="same-market-message")
        duplicate = self.send("/买入 鸡蛋 1", mid="same-market-message")
        self.assertTrue(first["queued"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(len([row for row in self.rows(transactions) if row["market_window_id"] is not None]), 1)
        self.assertEqual(next(row for row in self.rows(stocks) if row["item_code"] == "egg")["quantity"], 1)

    def test_decimal_precision(self):
        self.assertEqual(to_minor(Decimal("1.005")), 101)
        self.assertEqual(from_minor(101), Decimal("1.01"))

    def test_transaction_rollback(self):
        self.send("/加入")
        before = self.rows(accounts)[0]["balance"]
        with patch.object(GameService, "event", side_effect=RuntimeError("rollback")):
            with self.assertRaises(RuntimeError):
                self.send("/买入 鸡蛋 1", mid="rollback-buy")
        self.assertEqual(self.rows(accounts)[0]["balance"], before)
        self.assertEqual(self.rows(stocks), [])
        self.assertEqual(self.rows(transactions), [])

    def test_ranch_panel_market_value(self):
        self.send("/加入")
        self.market_stock(amount=10)
        self.send("/牧场")
        expected = market_price("egg", self.now, self._market_config()) * 10
        self.assertIn(money(expected), self.reply())

    def test_market_concurrent_buy_limit(self):
        self.send("/加入")
        self.compensate(1000)
        events = [Inbound(room="g1", sender="u", message_id=f"market-race-{i}", text="/买入 鸡蛋 15") for i in range(5)]
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(self.store.receive, events))
        spent = sum(-row["net_amount"] for row in self.rows(transactions) if row["market_window_id"] is not None)
        self.assertLessEqual(spent, 20000)
        self.assert_reconciled()

    def test_market_buy_then_sell_same_window_has_no_arbitrage(self):
        self.send("/加入")
        before = self.rows(accounts)[0]["balance"]
        self.send("/买入 鸡蛋 1")
        self.send("/出售 鸡蛋 1")
        after = self.rows(accounts)[0]["balance"]
        self.assertLess(after, before)
        self.assertEqual(next(row for row in self.rows(stocks) if row["item_code"] == "egg")["quantity"], 0)
        self.assert_reconciled()

    def test_p2_p3_ranch_collect_market_sell_roundtrip(self):
        self.send("/加入")
        self.send("/买动物 鸡")
        self.now += 12 * 3600
        self.send("/收取")
        self.assertGreater(self.rows(stocks)[0]["quantity"], 0)
        self.send("/行情")
        self.assertIn("鸡蛋", self.reply())
        count = next(row["quantity"] for row in self.rows(stocks) if row["item_code"] == "egg")
        self.send(f"/出售 鸡蛋 {count}")
        self.assertIn("出售完成", self.reply())
        self.assertEqual(next(row for row in self.rows(stocks) if row["item_code"] == "egg")["quantity"], 0)
        self.assert_reconciled()


class MigrationTests(unittest.TestCase):
    def test_legacy_whole_coin_balances_migrate_to_cents_without_value_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "sqlite:///" + str(Path(tmp) / "legacy-coins.db")
            engine = create_engine(url)
            cfg = AlembicConfig()
            cfg.set_main_option("script_location", str(Path(__file__).parent / "dzmm_bot" / "persistence" / "migrations"))
            with engine.begin() as db:
                cfg.attributes["connection"] = db
                alembic_command.upgrade(cfg, "0004_ruihe_ranch")
                db.execute(text("INSERT INTO players(id, name, created) VALUES ('old', '旧玩家', 1)"))
                db.execute(text("INSERT INTO game_accounts(player_id, balance, joined_at, status, version) VALUES ('old', 100, 1, 'active', 1)"))
                db.execute(text("INSERT INTO currency_ledger(id, player_id, amount, reason, reference_type, reference_id, created_at) VALUES ('l1', 'old', 100, 'join', 'join', 'old-join', 1)"))
                db.execute(text("UPDATE world_state SET lottery_pool_balance = 5 WHERE id = 1"))
                db.execute(text("INSERT INTO market_transactions(id, player_id, kind, gross_amount, tax_amount, net_amount, item_code, quantity, config_version, created_at) VALUES ('t1', 'old', 'sell', 50, 3, 47, 'egg', 10, 'm1-v1', 1)"))
            engine.dispose()
            store = Store(url)
            with store.engine.connect() as db:
                self.assertEqual(db.execute(text("SELECT balance FROM game_accounts WHERE player_id='old'")).scalar_one(), 10000)
                self.assertEqual(db.execute(text("SELECT amount FROM currency_ledger WHERE id='l1'")).scalar_one(), 10000)
                self.assertEqual(db.execute(text("SELECT lottery_pool_balance FROM world_state WHERE id=1")).scalar_one(), 500)
                self.assertEqual(db.execute(text("SELECT gross_amount, tax_amount, net_amount FROM market_transactions WHERE id='t1'")).one(), (5000, 300, 4700))
            store.engine.dispose()

    def test_legacy_database_preserved_and_repeated_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "sqlite:///" + str(Path(tmp) / "legacy.db")
            engine = create_engine(url)
            transport.create_all(engine)
            with engine.begin() as db:
                db.execute(players.insert().values(id="old", name="旧玩家", created=1))
                db.execute(inbox.insert().values(id="old", room="g", message_id="m", sender="old", text="/join", created=1))
                db.execute(outbox.insert().values(id="old", room="g", kind="group", text="旧回复", status="pending", created=1, available=1, attempts=0))
            engine.dispose()
            from alembic.script import ScriptDirectory
            cfg = AlembicConfig()
            cfg.set_main_option("script_location", str(Path(__file__).parent / "dzmm_bot" / "persistence" / "migrations"))
            expected_head = ScriptDirectory.from_config(cfg).get_current_head()
            for _ in range(2):
                store = Store(url)
                try:
                    with store.engine.connect() as db:
                        self.assertEqual(db.execute(select(players.c.name)).scalar_one(), "旧玩家")
                        self.assertEqual(db.execute(select(func.count()).select_from(inbox)).scalar_one(), 1)
                        self.assertEqual(db.execute(select(outbox.c.text)).scalar_one(), "旧回复")
                        self.assertEqual(db.execute(text("SELECT version_num FROM alembic_version")).scalar_one(), expected_head)
                finally:
                    store.engine.dispose()


class RuleTests(unittest.TestCase):
    def test_saved_message_snapshots(self):
        fixture_path = Path(__file__).parent / "tests" / "fixtures" / "game-messages.json"
        for fixture in json.loads(fixture_path.read_text(encoding="utf-8")):
            with self.subTest(fixture.get("error") or fixture["result"]["kind"]):
                actual = error(fixture["error"], **fixture["details"]) if "error" in fixture else render(fixture["result"], "12345678-reference")
                self.assertEqual(actual, fixture["expected"])

    def test_ruihe_ranch_snapshot(self):
        from dzmm_bot.presentation.ruihe import render_ranch
        from dzmm_bot.presentation.formatters import bar, duration, money, name_escape
        fixture_path = Path(__file__).parent / "tests" / "fixtures" / "ruihe-ranch.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(render_ranch(fixture["result"]), fixture["expected"])
        self.assertIn("暂无动物", render_ranch({**fixture["result"], "animals": []}))
        self.assertEqual(bar(-1, 10), "░░░░░░")
        self.assertEqual(bar(11, 10), "▓▓▓▓▓▓")
        self.assertEqual(duration(float("nan")), "已完成/待收取")
        self.assertEqual(money(4.345), "4.35")
        self.assertEqual(name_escape("玩家|A\nB"), "玩家丨A B")

    def test_integer_tax_capacity_upgrade_and_seed_boundaries(self):
        for gross, fee in [(0, 0), (1, 1), (19, 1), (20, 1), (21, 2), (50, 3), (120, 6), (300, 15)]:
            self.assertEqual(tax(gross, RANCH_CONFIG), fee)
        for level in range(1, 10):
            self.assertEqual(capacity(level, RANCH_CONFIG), 3 + 2 * level)
            self.assertEqual(upgrade_price(level, RANCH_CONFIG), round(200 * 1.5 ** (level - 1)))
            for animal, rule in RANCH_CONFIG["animals"].items():
                for seq in range(20):
                    base, seconds = interval("fixed", seq, animal, level, RANCH_CONFIG)
                    self.assertTrue(rule["hours"][0] <= base <= rule["hours"][1])
                    self.assertAlmostEqual(seconds, base * 3600 * (1 - 0.05 * (level - 1)))
        self.assertEqual(seed_for("s", "id", "v1", "n"), seed_for("s", "id", "v1", "n"))
        self.assertNotEqual(seed_for("s", "id", "v1", "n"), seed_for("s", "id", "v2", "n"))

    def test_production_multiplier_and_rounding(self):
        self.assertAlmostEqual(production_multiplier(9, 3, True, "sunny", "egg"), 1.1 * 1.2)
        self.assertEqual(production_quantity(1, 1.5), 2)
        self.assertEqual(production_quantity(1, 1.49), 1)

    def test_market_phases_and_midnight(self):
        midnight = datetime(2026, 9, 1, 16, tzinfo=timezone.utc).timestamp()
        self.assertEqual(local_date(midnight - 1), "2026-09-01")
        self.assertEqual(local_date(midnight), "2026-09-02")
        prices = [market_price(p, midnight, RANCH_CONFIG) for p in ["egg", "wool", "milk"]]
        self.assertTrue(all(Decimal(RANCH_CONFIG["products"][p]["price"]) * Decimal("0.5") <= price <=
                            Decimal(RANCH_CONFIG["products"][p]["price"]) * Decimal("1.6")
                            for p, price in zip(["egg", "wool", "milk"], prices)))
        for product in ["egg", "wool", "milk"]:
            self.assertEqual(market_price(product, midnight, RANCH_CONFIG), market_price(product, midnight + 1799, RANCH_CONFIG))
            self.assertNotEqual(market_price(product, midnight, RANCH_CONFIG), market_price(product, midnight + 1800, RANCH_CONFIG))

    def test_templates(self):
        self.assertEqual(render({"kind": "join", "amount": 100, "balance": 100}, "12345678-rest"),
            "✅ 注册成功\n🪙 获得：100\n当前余额：100\n\n输入 /我的")
        for code in ["not_joined", "joined", "group_join", "frozen", "relief_used", "relief_balance", "balance",
                     "capacity", "stock", "animal", "expired", "settlement_pending", "no_products", "max_level",
                     "quantity", "unknown", "syntax", "reference_conflict", "amount", "system"]:
            reply = error(code)
            self.assertTrue(reply.startswith("⚠️"))
            self.assertEqual(reply.count("输入 /"), 1)
        for english in ["/join", "/help", "/profile", "/start"]:
            self.assertNotIn(english, help_text("full"))
        self.assertIn("/资料", help_text("full"))
        self.assertNotIn("/出售", help_text("full", "牧场"))


class AdminTests(unittest.TestCase):
    def test_admin_permissions_audit_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(database_url="sqlite:///" + str(Path(tmp) / "api.db"), core_token="c" * 32, admin_token="a" * 32)
            with TestClient(create_app(cfg)) as client:
                admin = {"X-Admin-Token": cfg.admin_token}
                client.put("/admin/rooms", headers=admin, json={"id": "g"})
                client.post("/internal/inbound", headers={"X-Core-Token": cfg.core_token},
                    json={"room": "g", "message_id": "1", "sender": "u", "name": "平台昵称", "text": "/加入"})
                endpoint = "/admin/players/u/compensate"
                body = {"amount": 10, "reason": "补偿", "ticket": "T1"}
                self.assertEqual(client.post(endpoint, json=body).status_code, 401)
                self.assertEqual(client.post(endpoint, headers=admin, json=body).json()["balance"], 110)
                self.assertTrue(client.post(endpoint, headers=admin, json=body).json()["duplicate"])
                self.assertEqual(client.post(endpoint, headers=admin, json={**body, "amount": 20}).status_code, 409)
                self.assertEqual(client.post(endpoint, headers=admin, json={**body, "amount": -111, "ticket": "T2"}).status_code, 409)
                self.assertEqual(client.post(endpoint, headers=admin, json={**body, "amount": True}).status_code, 422)
                for action in ["freeze", "unfreeze"]:
                    self.assertEqual(client.post(f"/admin/players/u/{action}", headers=admin).status_code, 200)
                self.assertEqual(len(client.get("/admin/players/u/ledger", headers=admin).json()), 2)
                self.assertEqual(client.get("/admin/status", headers=admin).json()["game"]["balance_difference"], 0)


if __name__ == "__main__":
    unittest.main()

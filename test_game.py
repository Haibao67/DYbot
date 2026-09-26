"""M0/M1 integration tests run only against isolated temporary databases."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.exc import IntegrityError
from fastapi.testclient import TestClient
from dzmm_bot.core import Inbound, create_app
from dzmm_bot.settings import Settings
from dzmm_bot.store import Store
from dzmm_bot.persistence.transport import meta as transport, rooms, players, inbox, outbox
from dzmm_bot.persistence.schema import (accounts, currency, stocks, inventory, world, animals,
    products, transactions, ranches, audit, configs, events)
from dzmm_bot.application.services import GameService, metrics
from dzmm_bot.domain.economy import (RANCH_CONFIG, GameError, tax, capacity, upgrade_price,
    local_date, interval, market_price, seed_for)
from dzmm_bot.presentation.messages import error, render, HELP
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

    def send(self, command, player="u", room="g1", mid=None):
        self.number += 1
        result = self.store.receive(Inbound(room=room, sender=player, name="玩家" + player,
            message_id=mid or str(self.number), text=command))
        return result

    def rows(self, table):
        with self.store.engine.connect() as db:
            return [dict(r) for r in db.execute(select(table)).mappings()]

    def reply(self):
        return self.rows(outbox)[-1]["text"]

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
        event = Inbound(room="g1", sender="u", message_id="same", text="/加入")
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: self.store.receive(event), range(100)))
        self.assertEqual(sum(r.get("queued", False) for r in results), 1)
        self.send("/加入", room="g2")
        self.assertIn("已加入全局", self.reply())
        self.send("/我的", room="dm")
        self.assertIn("⨀ 100", self.reply())
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
                room="g1" if i % 2 else "g2", sender="u", message_id=str(i), text="/加入"))
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
            self.assertEqual(found["balance"], max(balance, 100))
            self.assertEqual(found["relief_claimed_on"], local_date(self.now) if balance < 100 else None)
        self.compensate(-50, "0", "spend")
        self.send("/救济", player="0")
        self.assertIn("今天已领取", self.reply())
        self.now += 16 * 3600
        self.send("/救济", player="0")
        self.assertIn("补足：⨀ 50", self.reply())
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
        self.assertEqual(self.rows(accounts)[0]["balance"], 47)
        self.assertEqual(self.rows(world)[0]["lottery_pool_balance"], 3)
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
        self.assertEqual(before, self.rows(animals))
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
        self.assertEqual(fed["production_until"], self.now + 48 * 3600)
        self.assertGreater(fed["next_production_at"], self.now)
        self.assertEqual(self.rows(products)[0]["collected_at"], None)
        self.now += 100 * 3600
        old = self.store
        self.store = Store(self.url, clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)
        old.engine.dispose()
        self.send("/牧场 收获")
        count = len(self.rows(products))
        self.assertGreater(count, 10)
        self.send("/牧场 收获")
        self.assertEqual(len(self.rows(products)), count)
        self.send("/牧场 出售 全部", mid="sale")
        self.assertIn("实际到账", self.reply())
        self.assertIn("奖池累计税额", self.reply())
        self.assertTrue(self.send("/牧场 出售 全部", mid="sale")["duplicate"])
        self.assertEqual(next(r for r in self.rows(stocks) if r["item_code"] == "egg")["quantity"], 0)
        self.send("/牧场 喂食 " + animal["display_id"])
        self.assertIn("已停产", self.reply())
        self.assert_reconciled()

    def test_bad_quantities_and_cross_player_access(self):
        self.send("/加入")
        self.send("/牧场 购买 鸡")
        animal = self.rows(animals)[0]
        for qty in ["0", "-1", "1.5", "99999999999999999", "一", "１"]:
            self.send("/牧场 购买 饲料 " + qty)
            self.assertIn("正整数", self.reply())
        self.send("/加入", player="v")
        self.send("/牧场 喂食 " + animal["display_id"], player="v")
        self.assertIn("属于你", self.reply())
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
        self.assertEqual(self.rows(accounts)[0]["balance"], 100)

    def test_ledger_idempotency_immutability_and_compensation(self):
        self.send("/加入")
        self.compensate(50, ticket="T1")
        self.assertTrue(self.compensate(50, ticket="T1")["duplicate"])
        with self.assertRaises(GameError):
            self.compensate(51, ticket="T1")
        with self.assertRaises(GameError):
            self.compensate(-151, ticket="T2")
        self.assertEqual(self.rows(accounts)[0]["balance"], 150)
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
        self.send("/牧场 出售 全部")
        sales = [r for r in self.rows(transactions) if r["kind"] == "sell"]
        self.assertEqual(len(sales), 3)
        for sale in sales:
            self.assertEqual(sale["tax_amount"], tax(sale["gross_amount"], RANCH_CONFIG))
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
        config = json.loads(json.dumps(RANCH_CONFIG))
        config["animals"]["chicken"]["hours"] = [0.04, 0.04]
        with self.store.engine.begin() as db:
            db.execute(configs.insert().values(version="fast-test", payload_json=json.dumps(config), status="published", published_at=self.now))
            db.execute(world.update().values(config_version="fast-test"))
        self.send("/加入")
        self.send("/牧场 购买 鸡")
        self.now += 48 * 3600
        self.send("/牧场 查看")
        self.assertEqual(len(self.rows(products)), 1000)
        self.assertIn("仍有待结算", self.reply())
        self.send("/牧场 收获")
        self.assertEqual(len(self.rows(products)), 1200)
        self.assertEqual(self.rows(animals)[0]["sequence"], 1200)
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
        def command(i):
            return self.store.receive(Inbound(room="g1", sender="u", message_id=f"parallel-{i}",
                text="/牧场 收获" if i % 2 else "/牧场 出售 全部"))
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
        self.assertIn("饲料不足", self.reply())
        self.assertEqual(self.rows(products), [])
        with self.store.engine.begin() as db:
            GameService(db, self.now, "s", "u").admin_action("freeze", "test")
        self.send("/牧场 查看")
        self.assertEqual(self.rows(products), [])

    def test_pagination_and_balance_ranking(self):
        self.send("/加入")
        self.compensate(10000)
        self.send("/牧场 升级")
        self.send("/牧场 购买 鸡 7")
        self.send("/牧场 查看")
        self.assertEqual(sum(a["display_id"] in self.reply() for a in self.rows(animals)), 5)
        self.assertIn("/牧场 查看 2", self.reply())
        self.send("/牧场 查看 2")
        self.assertEqual(sum(a["display_id"] in self.reply() for a in self.rows(animals)), 2)
        self.send("/排行 总榜")
        self.assertIn("玩家u", self.reply())

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


class MigrationTests(unittest.TestCase):
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
            for _ in range(2):
                store = Store(url)
                with store.engine.connect() as db:
                    self.assertEqual(db.execute(select(players.c.name)).scalar_one(), "旧玩家")
                    self.assertEqual(db.execute(select(func.count()).select_from(inbox)).scalar_one(), 1)
                    self.assertEqual(db.execute(select(outbox.c.text)).scalar_one(), "旧回复")
                    self.assertEqual(db.execute(text("SELECT version_num FROM alembic_version")).scalar_one(), "0002_ranch")
                store.engine.dispose()


class RuleTests(unittest.TestCase):
    def test_saved_message_snapshots(self):
        fixture_path = Path(__file__).parent / "tests" / "fixtures" / "game-messages.json"
        for fixture in json.loads(fixture_path.read_text(encoding="utf-8")):
            with self.subTest(fixture.get("error") or fixture["result"]["kind"]):
                actual = error(fixture["error"], **fixture["details"]) if "error" in fixture else render(fixture["result"], "12345678-reference")
                self.assertEqual(actual, fixture["expected"])

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
                    self.assertAlmostEqual(seconds, base * 3600 / (1 + 0.1 * (level - 1)))
        self.assertEqual(seed_for("s", "id", "v1", "n"), seed_for("s", "id", "v1", "n"))
        self.assertNotEqual(seed_for("s", "id", "v1", "n"), seed_for("s", "id", "v2", "n"))

    def test_market_phases_and_midnight(self):
        midnight = datetime(2026, 9, 1, 16, tzinfo=timezone.utc).timestamp()
        self.assertEqual(local_date(midnight - 1), "2026-09-01")
        self.assertEqual(local_date(midnight), "2026-09-02")
        self.assertEqual([market_price(p, midnight, RANCH_CONFIG) for p in ["egg", "wool", "milk"]], [5, 22, 16])
        for product in ["egg", "wool", "milk"]:
            self.assertEqual(market_price(product, midnight, RANCH_CONFIG), market_price(product, midnight + 86400, RANCH_CONFIG))

    def test_templates(self):
        self.assertEqual(render({"kind": "join", "amount": 100, "balance": 100}, "12345678-rest"),
            "✅ 已加入冬宴游戏\n🪙 获得：⨀ 100\n当前余额：⨀ 100\n操作编号：12345678\n\n输入 /我的")
        for code in ["not_joined", "joined", "group_join", "frozen", "relief_used", "relief_balance", "balance",
                     "capacity", "stock", "animal", "expired", "settlement_pending", "no_products", "max_level",
                     "quantity", "unknown", "syntax", "reference_conflict", "amount", "system"]:
            reply = error(code)
            self.assertTrue(reply.startswith("⚠️"))
            self.assertEqual(reply.count("输入 /"), 1)
        for english in ["/join", "/help", "/profile", "/start"]:
            self.assertNotIn(english, HELP)


class AdminTests(unittest.TestCase):
    def test_admin_permissions_audit_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Settings(database_url="sqlite:///" + str(Path(tmp) / "api.db"), core_token="c" * 32, admin_token="a" * 32)
            with TestClient(create_app(cfg)) as client:
                admin = {"X-Admin-Token": cfg.admin_token}
                client.put("/admin/rooms", headers=admin, json={"id": "g"})
                client.post("/internal/inbound", headers={"X-Core-Token": cfg.core_token},
                    json={"room": "g", "message_id": "1", "sender": "u", "text": "/加入"})
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

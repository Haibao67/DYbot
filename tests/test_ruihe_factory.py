import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from dzmm_bot.core import Inbound
from dzmm_bot.store import Store
from dzmm_bot.persistence.transport import rooms, outbox
from dzmm_bot.persistence.schema import factories, factory_jobs, stocks, transactions
from dzmm_bot.application.factory_service import FactoryService
from dzmm_bot.application.services import GameService
from dzmm_bot.domain.economy import GameError
from dzmm_bot.domain.factory_rules import FACTORY_LEVELS, RECIPES


class FactoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
        self.store = Store("sqlite:///" + str(Path(self.tmp.name) / "factory.db"),
                           secret="factory-test", clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert().values(id="g", kind="group", enabled=1))
        self.store.receive(Inbound(room="g", sender="p", name="Player", message_id="join", text="/注册"))
        with self.store.engine.begin() as db:
            svc = GameService(db, self.now, "factory-test", "p", "g", "credit")
            svc.ledger(10000, "test_credit", "test", "factory-credit")
            svc.inventory_change("egg", 30, "test_stock", "eggs")
            svc.inventory_change("milk", 20, "test_stock", "milk")

    def service(self, ref, roll=0.5, db=None):
        return FactoryService(db if db is not None else self.db, self.now, "factory-test", "p", "g", ref,
                              roll_provider=lambda *_: roll)

    def test_factory_tables_migrated_and_panel_command_replies(self):
        result = self.store.receive(Inbound(room="g", sender="p", name="Player", message_id="panel", text="/加工厂"))
        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            text = db.execute(select(outbox.c.text).where(outbox.c.reply_to_message_id == "panel")).scalar_one()
            row = db.execute(select(factories)).mappings().one()
        self.assertIn("加工厂 Lv.1", text)
        self.assertIn("/配方", text)
        self.assertNotIn("鸡蛋×3 →", text)
        self.assertEqual(row["line_count"], 1)

    def test_recipe_details_move_to_recipe_command(self):
        self.store.receive(Inbound(room="g", sender="p", name="Player", message_id="recipes", text="/配方"))
        with self.store.engine.connect() as db:
            text = db.execute(select(outbox.c.text).where(outbox.c.reply_to_message_id == "recipes")).scalar_one()
        self.assertIn("加工配方", text)
        self.assertIn("蛋糕：鸡蛋×3", text)
        self.assertIn("🔒Lv.2 礼盒", text)

    def test_factory_start_reply_contains_only_job_and_finish_time(self):
        result = self.store.receive(Inbound(room="g", sender="p", name="Player",
            message_id="start-cake", text="/投产 蛋糕 1"))
        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            text = db.execute(select(outbox.c.text).where(
                outbox.c.reply_to_message_id == "start-cake")).scalar_one()
        self.assertTrue(text.startswith("✅ 已投产：🍰蛋糕 ×1\n生产线：1｜预计完成："))
        self.assertNotIn("已扣材料", text)
        self.assertNotIn("失败率", text)
        self.assertNotIn("暴击率", text)
        self.assertNotIn("输入 /加工厂", text)
        self.assertNotIn("下一步：", text)

    def test_collected_factory_goods_appear_in_ranch_inventory(self):
        with self.store.engine.begin() as self.db:
            self.service("panel-cake-start").start("cake", 2)
        self.now += 3600
        with self.store.engine.begin() as self.db:
            self.service("panel-cake-collect").collect_factory()
        self.store.receive(Inbound(room="g", sender="p", name="Player", message_id="ranch-goods", text="/牧场"))
        with self.store.engine.connect() as db:
            text = db.execute(select(outbox.c.text).where(outbox.c.reply_to_message_id == "ranch-goods")).scalar_one()
        self.assertIn("蛋糕 ×2｜现价 18", text)
        self.assertIn("｜约值 36", text)

    def test_t1_materials_duration_collect_and_idempotency(self):
        with self.store.engine.begin() as self.db:
            svc = self.service("cake-job")
            started = svc.start("cake", 2)
            self.assertEqual(started["finish_at"] - self.now, 3600)
            self.assertEqual(svc.stock("egg"), 24)
        self.now += 3600
        with self.store.engine.begin() as self.db:
            svc = self.service("take-cake")
            got = svc.collect_factory()
            self.assertEqual(got["totals"], {"cake": 2})
        with self.store.engine.begin() as self.db:
            self.assertEqual(self.service("take-again").stock("cake"), 2)
            with self.assertRaises(GameError):
                self.service("take-again2").collect_factory()

    def test_multi_recipe_command_starts_one_job_per_open_line(self):
        with self.store.engine.begin() as db:
            self.service("factory-upgrade-multi", db=db).upgrade_factory()
        result = self.store.receive(Inbound(room="g", sender="p", name="Player",
            message_id="multi-factory-command", text="/加工 蛋糕 奶酪"))
        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            jobs = db.execute(select(factory_jobs).where(factory_jobs.c.player_id == "p")
                              .order_by(factory_jobs.c.line_no)).mappings().all()
            text = db.execute(select(outbox.c.text).where(
                outbox.c.reply_to_message_id == "multi-factory-command")).scalar_one()
        self.assertEqual(len(jobs), 2)
        self.assertEqual([job["line_no"] for job in jobs], [1, 2])
        self.assertEqual([job["recipe_id"] for job in jobs], ["cake", "cheese"])
        self.assertEqual(len({job["id"] for job in jobs}), 2)
        self.assertIn("\u0031\u53f7\u7ebf", text)
        self.assertIn("\u86cb\u7cd5", text)
        self.assertIn("\u5976\u916a", text)

    def test_legacy_factory_quantity_uses_one_line_per_item(self):
        with self.store.engine.begin() as db:
            self.service("factory-upgrade-repeat", db=db).upgrade_factory()
        command = "\u002f\u52a0\u5de5 \u86cb\u7cd5 2"
        result = self.store.receive(Inbound(room="g", sender="p", name="Player",
            message_id="repeat-factory-command", text=command))
        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            jobs = db.execute(select(factory_jobs).where(factory_jobs.c.player_id == "p")
                              .order_by(factory_jobs.c.line_no)).mappings().all()
            reply = db.execute(select(outbox.c.text).where(
                outbox.c.reply_to_message_id == "repeat-factory-command")).scalar_one()
        self.assertEqual([job["line_no"] for job in jobs], [1, 2])
        self.assertEqual([job["batch_quantity"] for job in jobs], [1, 1])
        self.assertEqual([job["recipe_id"] for job in jobs], ["cake", "cake"])
        self.assertIn("2", reply)

    def test_factory_output_uses_p3_market_and_sell_ledger(self):
        with self.store.engine.begin() as self.db:
            self.service("integration-start").start("cake")
        self.now += 3600
        with self.store.engine.begin() as self.db:
            self.service("integration-take").collect_factory()
        self.store.receive(Inbound(room="g", sender="p", name="Player", message_id="sell-cake", text="/出售 蛋糕 1"))
        with self.store.engine.connect() as db:
            self.assertEqual(db.execute(select(stocks.c.quantity).where(stocks.c.player_id == "p",
                stocks.c.item_code == "cake")).scalar_one(), 0)
            tx = db.execute(select(transactions).where(transactions.c.item_code == "cake")).mappings().one()
            text = db.execute(select(outbox.c.text).where(outbox.c.reply_to_message_id == "sell-cake")).scalar_one()
        self.assertEqual(tx["kind"], "sell")
        self.assertEqual(tx["gross_amount"], 1800)
        self.assertIn("实际到账：17.1", text)

    def test_t2_quote_uses_global_dynamic_market_and_can_be_sold(self):
        from dzmm_bot.domain.economy import market_price
        with self.store.engine.begin() as db:
            svc = self.service("t2-market", db=db)
            market = svc.market()
            quote = next(row for row in market["rows"] if row["item"] == "gift_box")
            price = market_price("gift_box", self.now, svc.config)
            self.assertEqual(quote["price"], price)
            self.assertGreaterEqual(price, Decimal("60"))
            self.assertLessEqual(price, Decimal("192"))
            svc.inventory_change("gift_box", 1, "test_stock", "market-gift")
            receipt = svc.sell("gift_box", 1)
            self.assertEqual(receipt["lines"][0]["item"], "gift_box")

    def test_missing_material_rolls_back_and_factory_lines_fill_lowest(self):
        with self.store.engine.begin() as self.db:
            svc = self.service("start1")
            svc.start("cake")
            with self.assertRaises(GameError):
                svc.start("cake")
            row = self.db.execute(select(factory_jobs)).mappings().one()
            self.assertEqual(row["line_no"], 1)
            self.assertEqual(svc.stock("egg"), 27)

    def test_failure_and_critical_are_snapshotted(self):
        with self.store.engine.begin() as self.db:
            svc = self.service("success", roll=Decimal("0.06"))
            # T1 has 0% critical and 4% failure; inject roll 0.06 for a normal output.
            svc.start("cake")
            row = self.db.execute(select(factory_jobs)).mappings().one()
            self.assertFalse(row["failed"])
            self.assertFalse(row["critical"])
            self.assertEqual(row["recipe_version"], "ruihe-factory-v1")

    def test_upgrade_progression_and_active_finish_is_frozen(self):
        with self.store.engine.begin() as self.db:
            svc = self.service("first")
            svc.start("cake")
            finish = self.db.execute(select(factory_jobs.c.finish_at)).scalar_one()
            svc.ledger(5000, "test_funds", "test", "upgrade-funds")
            for level in range(2, 6):
                svc = self.service(f"upgrade-{level}")
                svc.upgrade_factory()
            self.assertEqual(self.db.execute(select(factory_jobs.c.finish_at)).scalar_one(), finish)
            self.assertEqual(self.db.execute(select(factories.c.level)).scalar_one(), 5)

    def test_p2_p3_factory_path_keeps_recipe_registry_consistent(self):
        self.assertEqual(RECIPES["grand_gift"]["factory_level_required"], 4)
        self.assertEqual(FACTORY_LEVELS[5]["duration_multiplier"], Decimal("0.6"))
        self.assertEqual(FACTORY_LEVELS[5]["line_count"], 5)

    def test_t2_and_t3_recipes_unlock_with_expected_locked_duration(self):
        with self.store.engine.begin() as db:
            svc = self.service("t2-locked", db=db)
            with self.assertRaises(GameError):
                svc.start("gift_box")
            svc.ledger(5000, "test_funds", "test", "tier-funds")
            svc.inventory_change("cake", 10, "test_stock", "cake")
            svc.inventory_change("cheese", 10, "test_stock", "cheese")
            for level in (2, 3, 4):
                svc = self.service(f"tier-up-{level}", db=db)
                svc.upgrade_factory()
            svc = self.service("t2-start", db=db)
            t2 = svc.start("gift_box")
            self.assertEqual(t2["finish_at"] - self.now, 7200 * 0.7)
            self.assertEqual(svc.stock("cake"), 8)
            self.assertEqual(svc.stock("cheese"), 9)
            svc.inventory_change("gift_box", 1, "test_stock", "gift")
            svc.inventory_change("down_coat", 1, "test_stock", "coat")
            svc.inventory_change("cheese_platter", 1, "test_stock", "platter")
            svc = self.service("t3-start", db=db)
            t3 = svc.start("grand_gift")
            self.assertEqual(t3["finish_at"] - self.now, 14400 * 0.7)

    def test_t2_critical_doubles_output_and_failed_job_returns_no_product(self):
        with self.store.engine.begin() as db:
            svc = self.service("advance", roll=0.5, db=db)
            svc.ledger(3000, "test_funds", "test", "crit-funds")
            svc.inventory_change("cake", 2, "test_stock", "crit-cake")
            svc.inventory_change("cheese", 1, "test_stock", "crit-cheese")
            svc.inventory_change("cake", 2, "test_stock", "fail-cake")
            svc.inventory_change("cheese", 1, "test_stock", "fail-cheese")
            for level in (2,):
                self.service(f"crit-up-{level}", db=db).upgrade_factory()
            svc = FactoryService(db, self.now, "factory-test", "p", "g", "critical-job",
                roll_provider=lambda _j, _v, purpose: Decimal("0.01") if purpose == "critical" else Decimal("0.5"))
            svc.start("gift_box")
        self.now += 7200 * 0.9
        with self.store.engine.begin() as db:
            got = FactoryService(db, self.now, "factory-test", "p", "g", "critical-take",
                                 roll_provider=lambda *_: Decimal("0.5")).collect_factory()
        self.assertEqual(got["totals"], {"gift_box": 2})

        with self.store.engine.begin() as db:
            svc = FactoryService(db, self.now, "factory-test", "p", "g", "fail-job",
                roll_provider=lambda _j, _v, purpose: Decimal("0.01") if purpose == "failure" else Decimal("0.5"))
            svc.start("gift_box")
        self.now += 7200 * 0.9
        with self.store.engine.begin() as db:
            failed = FactoryService(db, self.now, "factory-test", "p", "g", "fail-take").collect_factory()
        self.assertEqual(failed["totals"], {})
        self.assertTrue(failed["results"][0]["failed"])


if __name__ == "__main__":
    unittest.main()

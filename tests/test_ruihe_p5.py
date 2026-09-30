"""P5 Buffs and advanced factory controls on isolated temporary databases."""
import tempfile
import unittest
import importlib.util
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select, text

from dzmm_bot.application.factory_service import FactoryService
from dzmm_bot.application.services import GameService, metrics
from dzmm_bot.core import Inbound
from dzmm_bot.domain.economy import GameError
from dzmm_bot.domain.factory_rules import RECIPES
from dzmm_bot.persistence.migrate import migrate
from dzmm_bot.persistence.schema import (accounts, buffs, currency, factories, factory_jobs,
                                         inventory, stocks)
from dzmm_bot.persistence.transport import outbox, rooms
from dzmm_bot.store import Store


class P5Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
        self.store = Store("sqlite:///" + str(Path(self.tmp.name) / "p5.db"),
                           secret="p5-test-secret", clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert().values(id="g", kind="group", enabled=1))
        self.store.receive(Inbound(room="g", sender="p", name="玩家", message_id="join", text="/注册 玩家"))
        with self.store.engine.begin() as db:
            game = self.game(db, "funds")
            game.ledger(10000, "test_credit", "test", "p5-funds")
            for item, amount in {"egg": 300, "wool": 100, "milk": 100, "cake": 100,
                                 "cheese": 100, "cheese_platter": 10, "down_coat": 10,
                                 "gift_box": 10, "sweater": 10, "fish": 10,
                                 "brush": 20, "copper_bell": 5, "greenhouse": 5,
                                 "opening_feast": 10, "master_meal": 5, "dress": 5,
                                 "work_apron": 5, "automation_core": 5}.items():
                game.inventory_change(item, amount, "test_stock", f"initial-{item}")

    def game(self, db, ref):
        return GameService(db, self.now, "p5-test-secret", "p", "g", ref)

    def test_pg_status_constraint_detection(self):
        migration_path = (Path(__file__).parent.parent / "dzmm_bot" / "persistence" / "migrations"
                          / "versions" / "0007_buffs_factory_advanced.py")
        spec = importlib.util.spec_from_file_location("p5_migration_status_check", migration_path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        current = [{"name": "ck_processing_jobs_status", "sqltext": migration.STATUS_SQL}]
        old = [{"name": "ck_processing_jobs_status",
                "sqltext": "status IN ('processing', 'collected', 'failed')"}]
        self.assertTrue(migration._has_current_status_check(current))
        self.assertFalse(migration._has_current_status_check(old))

    def factory(self, db, ref, roll=Decimal("0.5")):
        return FactoryService(db, self.now, "p5-test-secret", "p", "g", ref,
                              roll_provider=lambda _job, _version, _purpose: roll)

    def set_factory_level(self, db, level):
        svc = self.factory(db, f"upgrade-to-{level}")
        current = self.game(db, "read-factory").db.execute(select(factories.c.level)
            .where(factories.c.player_id == "p")).scalar()
        if current is None:
            svc._factory()
            current = 1
        for next_level in range(current + 1, level + 1):
            self.factory(db, f"upgrade-level-{next_level}").upgrade_factory()

    def add_buff_row(self, db, buff_type, started=None, expires=None):
        rule = __import__("dzmm_bot.domain.buff_rules", fromlist=["BUFFS"]).BUFFS[buff_type]
        self.game(db, f"stock-{buff_type}-{self.now}").inventory_change(buff_type, 1,
            "test_buff", f"{buff_type}-{self.now}")
        game = self.game(db, f"activate-{buff_type}-{self.now}")
        game.buffs.activate(buff_type)
        if started is not None or expires is not None:
            db.execute(buffs.update().where(buffs.c.player_id == "p", buffs.c.buff_type == buff_type).values(
                started_at=self.now if started is None else started,
                expires_at=self.now + rule["duration"] if expires is None else expires))

    def test_buff_activation_extension_expiry_and_remaining_time(self):
        with self.store.engine.begin() as db:
            game = self.game(db, "brush-use-1")
            first = game.buffs.activate("brush")
            self.assertTrue(game.buffs.is_active("brush"))
            self.assertEqual(game.buffs.remaining_time("brush"), 3 * 86400)
            game = self.game(db, "brush-use-2")
            second = game.buffs.activate("brush")
            self.assertEqual(second["expires_at"] - first["expires_at"], 3 * 86400)
            self.assertEqual(game.buffs.remaining_time("brush"), 6 * 86400)
            self.now = second["expires_at"]
            expired_game = self.game(db, "brush-expired-check")
            self.assertFalse(expired_game.buffs.is_active("brush"))
            self.assertEqual(expired_game.buffs.remaining_time("brush"), 0)

    def test_buff_active_time_boundaries(self):
        with self.store.engine.begin() as db:
            self.add_buff_row(db, "opening_feast", started=self.now + 30, expires=self.now + 90)
            game = self.game(db, "boundary")
            self.assertFalse(game.buffs.is_active("opening_feast"))
            self.assertEqual(game.buffs.get_multiplier("ranch_output", at=self.now + 30), Decimal("1.30"))
            self.assertFalse(game.buffs.is_active("opening_feast", at=self.now + 90))

    def test_brush_doubles_only_timely_base_affection_gain(self):
        with self.store.engine.begin() as db:
            game = self.game(db, "buy-chicken")
            game.buy("chicken", 1)
            game.inventory_change("feed", 4, "test_feed", "feed-for-brush")
            self.game(db, "brush-use-after-buy").buffs.activate("brush")
        self.now += 12 * 3600
        with self.store.engine.begin() as db:
            game = self.game(db, "feed-brush")
            game.feed()
            affection = db.execute(select(__import__("dzmm_bot.persistence.schema", fromlist=["animals"]).animals.c.affection)).scalar_one()
        self.assertEqual(affection, 2)

    def test_output_buffs_multiply_and_opening_feast_extends(self):
        with self.store.engine.begin() as db:
            game = self.game(db, "bell-feast")
            game.buffs.activate("copper_bell")
            self.game(db, "feast-use-1").buffs.activate("opening_feast")
            self.assertEqual(game.buffs.get_multiplier("ranch_output"), Decimal("1.456"))
            expires = game.buffs.remaining_time("opening_feast")
            self.game(db, "feast-use-2").buffs.activate("opening_feast")
            self.assertEqual(game.buffs.remaining_time("opening_feast"), expires + 86400)

    def test_greenhouse_blocks_only_negative_weather(self):
        with self.store.engine.begin() as db:
            game = self.game(db, "greenhouse")
            game.buffs.activate("greenhouse")
            self.assertEqual(game.buffs.weather_multiplier("drought", "egg"), Decimal("1"))
            self.assertEqual(game.buffs.weather_multiplier("humid", "milk"), Decimal("1"))
            self.assertEqual(game.buffs.weather_multiplier("sunny", "egg"), Decimal("1.2"))
            self.assertEqual(game.buffs.weather_multiplier("rainy", "wool"), Decimal("1.5"))

    def test_master_meal_zeroes_failure_but_not_critical(self):
        with self.store.engine.begin() as db:
            self.set_factory_level(db, 4)
            game = self.game(db, "master-use")
            game.buffs.activate("master_meal")
            self.assertEqual(game.buffs.calculate_failure_rate(Decimal("0.04"), 4, 3), Decimal("0"))
            job = FactoryService(db, self.now, "p5-test-secret", "p", "g", "master-job",
                roll_provider=lambda _j, _v, purpose: Decimal("0.01") if purpose == "failure" else Decimal("0.01"))
            result = job.start("grand_gift")
            row = db.execute(select(factory_jobs).where(factory_jobs.c.id == "master-job")).mappings().one()
            self.assertFalse(row["failed"])
            self.assertTrue(row["critical"])
            self.assertEqual(row["failure_rate"], Decimal("0.000000"))

    def test_work_apron_affects_new_job_only(self):
        with self.store.engine.begin() as db:
            job = self.factory(db, "before-apron")
            job.start("cake")
            first_finish = db.execute(select(factory_jobs.c.finish_at).where(factory_jobs.c.id == "before-apron")).scalar_one()
            self.factory(db, "level-up").upgrade_factory()
            self.game(db, "apron-use").buffs.activate("work_apron")
            self.factory(db, "after-apron").start("cake")
            second_finish = db.execute(select(factory_jobs.c.finish_at).where(factory_jobs.c.id == "after-apron")).scalar_one()
            self.assertEqual(first_finish, self.now + 3600)
            self.assertEqual(second_finish - self.now, 3600 * 0.9 * 0.75)
            self.assertEqual(db.execute(select(factory_jobs.c.finish_at).where(factory_jobs.c.id == "before-apron")).scalar_one(), first_finish)

    def test_dress_sell_modifier_is_channel_scoped(self):
        with self.store.engine.begin() as db:
            game = self.game(db, "dress-use")
            game.buffs.activate("dress")
            self.assertEqual(game.buffs.calculate_sell_price(Decimal("100"), "foreign_trade"), Decimal("110.00"))
            self.assertEqual(game.buffs.calculate_sell_price(Decimal("100"), "market"), Decimal("100.00"))

    def test_buff_craft_is_atomic_and_missing_material_rejected(self):
        with self.store.engine.begin() as db:
            game = self.game(db, "craft-brush")
            wool_before = game.stock("wool")
            result = game.buffs.craft_buff_item("brush")
            self.assertEqual(result["quantity"], 21)
            self.assertEqual(game.stock("wool"), wool_before - 2)
            self.assertEqual(game.stock("brush"), 21)
            self.assertEqual(metrics(db)["balance_difference"], 0)
        self.store.receive(Inbound(room="g", sender="q", name="Q", message_id="q-join", text="/注册 Q"))
        with self.store.engine.begin() as db:
            game = GameService(db, self.now, "p5-test-secret", "q", "g", "q-craft")
            with self.assertRaises(GameError):
                game.buffs.craft_buff_item("copper_bell")

    def test_profile_lists_only_active_buffs(self):
        with self.store.engine.begin() as db:
            game = self.game(db, "profile-buff")
            game.buffs.activate("brush")
            profile = game.profile()
            self.assertEqual([row["item"] for row in profile["buffs"]], ["brush"])
            db.execute(buffs.update().where(buffs.c.buff_type == "brush").values(expires_at=self.now))
            self.assertEqual(game.profile()["buffs"], [])

    def _start_cake(self, db, ref):
        self.factory(db, ref).start("cake", 10)

    def test_expedite_first_second_third_cost_and_ledger(self):
        with self.store.engine.begin() as db:
            self.set_factory_level(db, 3)
            for index, expected_cost in enumerate((10, 20, 40), 1):
                self._start_cake(db, f"rush-job-{index}")
                result = self.factory(db, f"rush-{index}").expedite(1)
                self.assertEqual(result["cost"], expected_cost)
                row = db.execute(select(factory_jobs).where(factory_jobs.c.id == f"rush-job-{index}")).mappings().one()
                self.assertEqual(row["status"], "completed_pending_collect")
                ledger_row = db.execute(select(currency).where(currency.c.reference_type == "factory_expedite",
                    currency.c.reference_id == f"rush-{index}")).mappings().one()
                self.assertEqual(ledger_row["amount"], -expected_cost * 100)
                self.factory(db, f"rush-collect-{index}").collect_factory()
                self.assertEqual(db.execute(select(factory_jobs.c.status).where(
                    factory_jobs.c.id == f"rush-job-{index}")).scalar_one(), "collected")
            self._start_cake(db, "rush-job-limit")
            with self.assertRaises(GameError):
                self.factory(db, "rush-limit").expedite(1)

    def test_expedite_daily_limit_and_atomic_balance_failure(self):
        with self.store.engine.begin() as db:
            self._start_cake(db, "atomic-job")
            db.execute(accounts.update().where(accounts.c.player_id == "p").values(balance=0))
            with self.assertRaises(GameError):
                self.factory(db, "atomic-rush").expedite(1)
            job = db.execute(select(factory_jobs).where(factory_jobs.c.id == "atomic-job")).mappings().one()
            self.assertEqual(job["status"], "processing")
            factory = db.execute(select(factories).where(factories.c.player_id == "p")).mappings().one()
            self.assertEqual(factory["rush_count"], 0)

    def test_expedite_limit_after_three_successes(self):
        with self.store.engine.begin() as db:
            self.set_factory_level(db, 3)
            db.execute(factories.update().where(factories.c.player_id == "p").values(
                rush_used_on="2026-09-01", rush_count=3))
            self._start_cake(db, "limited-rush-job")
            with self.assertRaises(GameError):
                self.factory(db, "limited-rush").expedite(1)

    def test_cancel_refund_80_50_20_and_snapshot_idempotency(self):
        for label, ratio, expected in (("early", 0.2, 24), ("mid", 0.5, 15), ("late", 0.8, 6)):
            with self.subTest(label=label):
                self.now = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
                with self.store.engine.begin() as db:
                    before = self.game(db, f"stock-before-{label}").stock("egg")
                    self._start_cake(db, f"cancel-{label}")
                    self.now += 3600 * ratio
                    with patch.dict(RECIPES["cake"], {"ingredients": {"milk": 999}}):
                        result = self.factory(db, f"cancel-op-{label}").cancel(1)
                        self.assertEqual(result["refund"]["egg"], expected)
                    self.assertEqual(self.game(db, f"read-after-{label}").stock("egg"), before - 30 + expected)
                    with self.assertRaises(GameError):
                        self.factory(db, f"cancel-repeat-{label}").cancel(1)

    def test_automation_core_upgrade_interface_consumes_inventory(self):
        with self.store.engine.begin() as db:
            before = self.game(db, "core-before").stock("automation_core")
            result = self.factory(db, "core-upgrade").upgrade_with_core()
            self.assertEqual(result["automation_level"], 1)
            self.assertEqual(self.game(db, "core-after").stock("automation_core"), before - 1)
            self.assertEqual(db.execute(select(factories.c.level).where(factories.c.player_id == "p")).scalar_one(), 1)

    def test_advanced_panel_and_buff_commands_are_replied(self):
        self.store.receive(Inbound(room="g", sender="p", name="玩家", message_id="craft-cmd", text="/补货 毛刷"))
        self.store.receive(Inbound(room="g", sender="p", name="玩家", message_id="use-cmd", text="/使用 毛刷"))
        self.store.receive(Inbound(room="g", sender="p", name="玩家", message_id="profile-cmd", text="/资料"))
        with self.store.engine.begin() as db:
            self.factory(db, "route-job").start("cake")
        rush_event = Inbound(room="g", sender="p", name="玩家", message_id="rush-cmd", text="/加急 1")
        before = self.game_balance()
        self.store.receive(rush_event)
        duplicate = self.store.receive(rush_event)
        self.assertTrue(duplicate["duplicate"])
        after = self.game_balance()
        self.store.receive(Inbound(room="g", sender="p", name="玩家", message_id="take-cmd", text="/取货"))
        self.store.receive(Inbound(room="g", sender="p", name="玩家", message_id="core-cmd", text="/加工厂升级 核心"))
        self.store.receive(Inbound(room="g", sender="p", name="玩家", message_id="panel-cmd", text="/加工厂"))
        with self.store.engine.connect() as db:
            replies = {row["reply_to_message_id"]: row["text"] for row in db.execute(select(outbox)).mappings()}
        self.assertIn("补货完成", replies["craft-cmd"])
        self.assertIn("已启用", replies["use-cmd"])
        self.assertIn("毛刷", replies["profile-cmd"])
        self.assertIn("已完成，等待取货", replies["rush-cmd"])
        self.assertEqual(before - after, 1000)
        self.assertIn("加工收货", replies["take-cmd"])
        self.assertIn("自动化核心已消耗并记录", replies["core-cmd"])
        self.assertIn("加急今日 1/3", replies["panel-cmd"])
        self.assertIn("/取消投产", replies["panel-cmd"])

    def game_balance(self):
        with self.store.engine.connect() as db:
            return db.execute(select(accounts.c.balance).where(accounts.c.player_id == "p")).scalar_one()

    def test_p4_jobs_migrate_to_advanced_statuses_without_loss(self):
        engine = create_engine("sqlite:///" + str(Path(self.tmp.name) / "legacy-p4.db"))
        with engine.begin() as db:
            db.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
            db.exec_driver_sql("INSERT INTO alembic_version VALUES ('0006_factory')")
            db.exec_driver_sql("CREATE TABLE processing_factories (player_id VARCHAR(200) PRIMARY KEY, level INTEGER NOT NULL, line_count INTEGER NOT NULL, created_at FLOAT NOT NULL, updated_at FLOAT NOT NULL, CHECK(level >= 1 AND level <= 5), CHECK(line_count >= 1 AND line_count <= 5))")
            db.exec_driver_sql("INSERT INTO processing_factories VALUES ('p', 1, 1, 10, 10)")
            db.exec_driver_sql("CREATE TABLE processing_jobs (id VARCHAR(200) PRIMARY KEY, player_id VARCHAR(200), line_no INTEGER NOT NULL, recipe_id VARCHAR(40) NOT NULL, recipe_version VARCHAR(40) NOT NULL, batch_quantity INTEGER NOT NULL, started_at FLOAT NOT NULL, finish_at FLOAT NOT NULL, status VARCHAR(20) NOT NULL, input_snapshot_json TEXT NOT NULL, output_snapshot_json TEXT NOT NULL, failure_roll VARCHAR(80) NOT NULL, critical_roll VARCHAR(80) NOT NULL, failed BOOLEAN NOT NULL, critical BOOLEAN NOT NULL, collected_at FLOAT, market_window_id BIGINT, price_snapshot_json TEXT NOT NULL, CHECK(line_no >= 1 AND batch_quantity >= 1), CHECK(status IN ('processing', 'collected', 'failed'))) ")
            db.exec_driver_sql("CREATE UNIQUE INDEX uq_processing_active_line ON processing_jobs(player_id, line_no) WHERE status='processing'")
            db.exec_driver_sql("INSERT INTO processing_jobs VALUES ('job-p4', 'p', 1, 'cake', 'ruihe-factory-v1', 1, 10, 100, 'processing', '{\"egg\": 3}', '{\"item\": \"cake\"}', '0.5', '0.5', 0, 0, NULL, 1, '{}')")
        migrate(engine)
        with engine.connect() as db:
            row = db.execute(text("SELECT id, status, input_snapshot_json, failure_rate FROM processing_jobs WHERE id='job-p4'")).one()
            self.assertEqual(row[0:3], ("job-p4", "processing", '{"egg": 3}'))
            self.assertIsNone(row[3])
            self.assertEqual(db.execute(text("SELECT version_num FROM alembic_version")).scalar_one(), "0012_outbound_scheduler")
            self.assertTrue(db.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='player_buffs'")).first())
        engine.dispose()


if __name__ == "__main__":
    unittest.main()

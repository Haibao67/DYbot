import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text

from dzmm_bot.application.outbound_metrics import outbound_performance
from dzmm_bot.persistence.transport import outbox, rooms
from dzmm_bot.store import Store


class OutboundTimingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.url = "sqlite:///" + str(Path(self.tmp.name) / "timing.db")
        self.now = 1000.0
        self.store = Store(self.url, clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert().values(id="g", kind="group", enabled=1))

    def add(self, task_id="task", **values):
        with self.store.engine.begin() as db:
            db.execute(outbox.insert().values(id=task_id, room="g", kind="group", text="secret body",
                reply_to_message_id="player-message", reply_to_sender_id="player", reply_to_text="/帮助",
                status="pending", created=1000.0, available=1000.0, attempts=0, **values))

    def row(self, task_id="task"):
        with self.store.engine.connect() as db:
            return db.execute(select(outbox).where(outbox.c.id == task_id)).mappings().one()

    def metrics(self, limit=200):
        with self.store.engine.connect() as db:
            return outbound_performance(db, self.now, limit)

    def test_success_times_and_native_reference(self):
        self.add()
        self.now = 1003
        task = self.store.claim()
        self.assertEqual((task["reply_to_message_id"], task["reply_to_text"]), ("player-message", "/帮助"))
        self.now = 1004
        self.assertTrue(self.store.transition(task["id"], task["lease"], "begin"))
        self.now = 1006
        self.assertTrue(self.store.transition(task["id"], task["lease"], "sent", platform_id="ack"))
        row = self.row()
        self.assertEqual((row["created"], row["last_claimed_at"], row["last_send_started_at"],
                          row["last_result_at"]), (1000, 1003, 1004, 1006))
        self.assertEqual((row["reply_to_message_id"], row["reply_to_text"]), ("player-message", "/帮助"))
        latency = self.metrics()["latency"]
        self.assertEqual((latency["queue_wait"]["p50_seconds"], latency["send_attempt"]["p50_seconds"],
                          latency["sent_end_to_end"]["p50_seconds"]), (3, 2, 6))
        self.now = 1010
        self.assertTrue(self.store.transition(task["id"], task["lease"], "sent", platform_id="ack"))
        self.assertEqual(self.row()["last_result_at"], 1006)

    def test_retry_outcome_is_terminal_and_never_replayed(self):
        self.add()
        self.now = 1001
        task = self.store.claim()
        self.now = 1002
        self.store.transition(task["id"], task["lease"], "begin")
        self.now = 1003
        self.store.transition(task["id"], task["lease"], "retry", error="rate_limited")
        self.assertEqual(self.metrics()["latency"]["sent_end_to_end"]["samples"], 0)
        row = self.row()
        self.assertEqual((row["status"], row["created"], row["last_claimed_at"],
                          row["last_send_started_at"], row["last_result_at"]),
                         ("failed", 1000, 1001, 1002, 1003))
        self.now = 1100
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.metrics()["failed_count"], 1)
        self.assertEqual(self.metrics()["latency"]["sent_end_to_end"]["samples"], 0)

    def test_expired_lease_and_uncertain_are_not_success(self):
        self.add()
        task = self.store.claim()
        self.now = 1061
        self.assertFalse(self.store.transition(task["id"], task["lease"], "begin"))
        self.assertIsNone(self.row()["last_send_started_at"])
        task = self.store.claim()
        self.store.transition(task["id"], task["lease"], "begin")
        self.now = 1122
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.row()["status"], "uncertain")
        self.assertEqual(self.metrics()["uncertain_count"], 1)
        self.assertEqual(self.metrics()["latency"]["sent_end_to_end"]["samples"], 0)
        self.assertEqual(self.row()["last_result_at"], 1122)

    def test_empty_old_and_invalid_samples(self):
        self.assertIsNone(self.metrics()["oldest_pending_seconds"])
        self.assertIsNone(self.metrics()["latency"]["queue_wait"]["p50_seconds"])
        self.add()
        self.assertIsNone(self.row()["last_claimed_at"])
        with self.store.engine.begin() as db:
            db.execute(outbox.update().where(outbox.c.id == "task").values(
                status="sent", last_result_at=999, last_claimed_at=999))
        result = self.metrics()
        self.assertEqual(result["latency"]["queue_wait"]["invalid_samples"], 1)
        self.assertEqual(result["latency"]["sent_end_to_end"]["invalid_samples"], 1)
        self.assertEqual(result["latency"]["send_attempt"]["invalid_samples"], 1)

    def test_bounded_fake_load(self):
        for count in (10, 100, 500):
            with self.subTest(count=count):
                with self.store.engine.begin() as db:
                    db.execute(outbox.delete())
                    db.execute(outbox.insert(), [dict(id=f"p{i}", room="g", kind="group", text="secret",
                        status="pending", created=1000.0, available=1000.0, attempts=0)
                        for i in range(count)])
                self.now = 2000
                result = self.metrics(20)
                self.assertEqual(result["pending_count"], count)
                self.assertEqual(result["oldest_pending_seconds"], 1000)
                self.assertEqual(result["recent_results"], 0)
                with self.store.engine.begin() as db:
                    db.execute(outbox.update().values(status="sent", last_claimed_at=1001,
                        last_send_started_at=1002, last_result_at=1004))
                result = self.metrics(20)
                self.assertEqual(result["recent_results"], min(count, 20))
                self.assertEqual(result["latency"]["send_attempt"]["p95_seconds"], 2)
                self.assertEqual(result["latency"]["sent_end_to_end"]["samples"], min(count, 20))
                self.assertNotIn("secret", str(result))

    def test_channels_rate_limit_and_nearest_rank(self):
        with self.store.engine.begin() as db:
            db.execute(rooms.insert().values(id="dm", kind="private", enabled=1))
            db.execute(outbox.insert(), [
                dict(id="group", room="g", kind="group", text="g", status="sent", created=1000,
                     available=1000, attempts=1, reply_to_message_id="m", last_claimed_at=1001,
                     last_send_started_at=1002, last_result_at=1003, error=None),
                dict(id="private", room="dm", kind="private", text="p", status="sent", created=1000,
                     available=1000, attempts=1, reply_to_message_id="m", last_claimed_at=1002,
                     last_send_started_at=1002, last_result_at=1005, error=None),
                dict(id="broadcast", room="g", kind="group", text="b", status="sent", created=1000,
                     available=1000, attempts=1, reply_to_message_id=None, last_claimed_at=1003,
                     last_send_started_at=1004, last_result_at=1007, error=None),
                dict(id="limited", room="g", kind="group", text="r", status="pending", created=1000,
                     available=1060, attempts=1, reply_to_message_id="m", last_claimed_at=None,
                     last_send_started_at=None, last_result_at=None, error="rate_limited"),
            ])
        result = self.metrics()
        self.assertEqual(result["channels"]["group"]["sent"], 1)
        self.assertEqual(result["channels"]["private"]["sent"], 1)
        self.assertEqual(result["channels"]["broadcast"]["sent"], 1)
        self.assertEqual(result["rate_limited_current"], 1)
        self.assertEqual(result["latency"]["sent_end_to_end"]["p50_seconds"], 5)
        self.assertEqual(result["latency"]["sent_end_to_end"]["p95_seconds"], 7)


class OutboundTimingMigrationTests(unittest.TestCase):
    def test_upgrade_from_0010_preserves_old_data_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            url = "sqlite:///" + str(Path(tmp) / "old.db")
            engine = create_engine(url)
            cfg = Config()
            cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] /
                "dzmm_bot" / "persistence" / "migrations"))
            with engine.begin() as db:
                cfg.attributes["connection"] = db
                command.upgrade(cfg, "0010_group_invites")
                db.execute(text("INSERT INTO players(id, name, created) VALUES ('old', '旧玩家', 1)"))
                db.execute(text("INSERT INTO outbound_messages(id, room, kind, text, status, created, available, attempts) "
                                "VALUES ('old', 'g', 'group', '旧回复', 'failed', 1, 1, 1)"))
            engine.dispose()
            for _ in range(2):
                store = Store(url)
                with store.engine.connect() as db:
                    self.assertEqual(db.execute(text("SELECT version_num FROM alembic_version")).scalar_one(),
                                     "0012_outbound_scheduler")
                    self.assertEqual(db.execute(select(outbox.c.text)).scalar_one(), "旧回复")
                    self.assertIsNone(db.execute(select(outbox.c.last_result_at)).scalar_one())
                    self.assertEqual(db.execute(text("SELECT name FROM players WHERE id='old'")).scalar_one(), "旧玩家")
                store.engine.dispose()

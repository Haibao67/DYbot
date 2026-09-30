import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine
from sqlalchemy import select

from dzmm_bot.application.red_packet_service import RedPacketService
from dzmm_bot.core import Inbound
from dzmm_bot.persistence.schema import accounts, red_packets
from dzmm_bot.persistence.transport import outbox, rooms
from dzmm_bot.store import Store


class RedPacketDurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime(2026, 9, 29, tzinfo=timezone.utc).timestamp()
        self.store = Store("sqlite:///" + str(Path(self.tmp.name) / "red-packets.db"),
                           secret="red-packet-test", clock=lambda: self.now, admins={"sender"})
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert().values(id="g", kind="group", enabled=1))
        for player_id in ("sender", "claimer"):
            self.store.receive(Inbound(room="g", sender=player_id, name=player_id,
                message_id="join-" + player_id, text="/\u6ce8\u518c"))

    def test_command_persists_requested_inactivity_minutes(self):
        result = self.store.receive(Inbound(room="g", sender="sender", name="sender",
            message_id="send-duration", text="/\u53d1\u7ea2\u5305 10 4 7"))
        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            packet = db.execute(select(red_packets)).mappings().one()
            reply = db.execute(select(outbox.c.text).where(
                outbox.c.reply_to_message_id == "send-duration")).scalar_one()
        self.assertEqual(packet["quantity"], 4)
        self.assertEqual(packet["duration_seconds"], 7 * 60)
        self.assertEqual(packet["expires_at"], self.now + 7 * 60)
        self.assertIn("7", reply)

    def test_legacy_send_defaults_to_ten_claims_and_two_minutes(self):
        result = self.store.receive(Inbound(room="g", sender="sender", name="sender",
            message_id="send-defaults", text="/\u53d1\u7ea2\u5305 10"))
        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            packet = db.execute(select(red_packets)).mappings().one()
        self.assertEqual(packet["quantity"], 10)
        self.assertEqual(packet["duration_seconds"], 120)

    def test_non_admin_cannot_end_packet(self):
        with self.store.engine.begin() as db:
            RedPacketService(db, self.now, "red-packet-test", "sender", "g", "send").send(10, 4, 7)
        result = self.store.receive(Inbound(room="g", sender="claimer", name="claimer",
            message_id="unauthorized-end", text="/\u7ed3\u675f\u7ea2\u5305"))
        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            status = db.execute(select(red_packets.c.status)).scalar_one()
        self.assertEqual(status, "active")

    def test_claim_resets_expiry_using_packet_specific_duration(self):
        with self.store.engine.begin() as db:
            RedPacketService(db, self.now, "red-packet-test", "sender", "g", "send").send(10, 4, 7)
        self.now += 120
        with self.store.engine.begin() as db:
            RedPacketService(db, self.now, "red-packet-test", "claimer", "g", "claim").claim()
        with self.store.engine.connect() as db:
            packet = db.execute(select(red_packets)).mappings().one()
        self.assertEqual(packet["expires_at"], self.now + 7 * 60)
        self.assertEqual(packet["duration_seconds"], 7 * 60)

    def test_existing_packet_migrates_with_two_minute_default(self):
        url = "sqlite:///" + str(Path(self.tmp.name) / "legacy-packet.db")
        engine = create_engine(url)
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(Path(__file__).parent.parent /
                            "dzmm_bot" / "persistence" / "migrations"))
        with engine.begin() as db:
            cfg.attributes["connection"] = db
            alembic_command.upgrade(cfg, "0026_daily_weather")
            db.exec_driver_sql("INSERT INTO red_packets (id, room_id, sender_id, total_amount, quantity, "
                "remaining_amount, remaining_count, status, created_at, expires_at) "
                "VALUES ('legacy', 'g', 'p', 1000, 2, 1000, 2, 'active', 1, 121)")
        with engine.begin() as db:
            cfg.attributes["connection"] = db
            alembic_command.upgrade(cfg, "head")
            duration = db.exec_driver_sql(
                "SELECT duration_seconds FROM red_packets WHERE id='legacy'").scalar_one()
        self.assertEqual(duration, 120)
        engine.dispose()

    def test_admin_end_refunds_unclaimed_balance(self):
        with self.store.engine.begin() as db:
            RedPacketService(db, self.now, "red-packet-test", "sender", "g", "send").send(10, 4, 7)
        result = self.store.receive(Inbound(room="g", sender="sender", name="sender",
            message_id="end-packet", text="/\u7ed3\u675f\u7ea2\u5305"))
        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            packet = db.execute(select(red_packets)).mappings().one()
            balance = db.execute(select(accounts.c.balance).where(accounts.c.player_id == "sender")).scalar_one()
            reply = db.execute(select(outbox.c.text).where(
                outbox.c.reply_to_message_id == "end-packet")).scalar_one()
        self.assertEqual(packet["status"], "expired")
        self.assertEqual(packet["remaining_amount"], 0)
        self.assertEqual(balance, 10000)
        self.assertIn("10", reply)


if __name__ == "__main__":
    unittest.main()


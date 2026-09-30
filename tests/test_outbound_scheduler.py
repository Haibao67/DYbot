import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from dzmm_bot.persistence.transport import outbox, rooms
from dzmm_bot.store import Store


class OutboundSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.url = "sqlite:///" + str(Path(self.tmp.name) / "scheduler.db")
        self.now = 1000.0
        self.store = Store(self.url, clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)

    def add(self, task_id, room, *, reply=True, created=1000, status="pending", available=1000):
        with self.store.engine.begin() as db:
            if not db.execute(select(rooms.c.id).where(rooms.c.id == room)).first():
                db.execute(rooms.insert().values(id=room, kind="group", enabled=1))
            db.execute(outbox.insert().values(id=task_id, room=room, kind="group",
                text="reply", reply_to_message_id=task_id if reply else None,
                reply_to_sender_id="player" if reply else None,
                reply_to_text="/牧场" if reply else None,
                status=status, created=created, available=available, attempts=0))

    def finish(self, task):
        self.assertTrue(self.store.transition(task["id"], task["lease"], "begin"))
        self.assertTrue(self.store.transition(task["id"], task["lease"], "sent", platform_id=task["id"]))

    def test_room_head_and_native_reply(self):
        self.add("old", "a", reply=False)
        self.add("new", "a", created=1001)
        self.add("other", "b")
        first = self.store.claim()
        self.assertEqual(first["id"], "other")
        self.assertEqual(first["reply_to_message_id"], "other")
        self.now += 1.0
        self.assertEqual(self.store.claim()["id"], "old")
        self.now += 1.0
        self.assertIsNone(self.store.claim())

    def test_busy_room_rotates_among_room_heads(self):
        for room in ("a", "b", "c"):
            for index in range(3):
                self.add(f"{room}{index}", room, created=1000 + index)
        order = []
        for _ in range(9):
            task = self.store.claim()
            order.append(task["room"])
            self.finish(task)
            self.now += 1.0
        self.assertEqual(order, ["a", "b", "c"] * 3)

    def test_announcements_get_one_turn_after_eight_replies(self):
        self.add("broadcast", "announcement", reply=False)
        for index in range(10):
            self.add(f"reply-{index:02}", f"room-{index:02}")
        claimed = []
        for _ in range(11):
            claimed.append(self.store.claim())
            self.now += 1.0
        self.assertEqual(claimed[8]["id"], "broadcast")
        self.assertEqual(sum(task["id"] == "broadcast" for task in claimed), 1)

    def test_uncertain_does_not_block_but_future_head_does(self):
        self.add("uncertain", "a", status="uncertain")
        self.add("later", "a", created=1001)
        self.add("future", "b", available=1100)
        self.add("later-b", "b", created=1001)
        self.assertEqual(self.store.claim()["id"], "later")
        self.now += 1.0
        self.assertIsNone(self.store.claim())

    def test_global_dispatch_limit_is_one_every_second(self):
        self.add("one", "a")
        self.add("two", "b")
        self.assertEqual(self.store.claim()["id"], "one")
        self.assertIsNone(self.store.claim())
        self.now += 0.999
        self.assertIsNone(self.store.claim())
        self.now += 0.001
        self.assertEqual(self.store.claim()["id"], "two")

    def test_clear_queue_cancels_active_tasks_and_keeps_rows(self):
        self.add("queued", "a")
        self.add("uncertain", "b", status="uncertain")
        counts = self.store.clear_outbound_queue()
        self.assertEqual(counts, {"pending": 1, "uncertain": 1})
        with self.store.engine.connect() as db:
            rows = db.execute(select(outbox.c.id, outbox.c.status, outbox.c.error)).all()
        self.assertEqual({(row.id, row.status, row.error) for row in rows}, {
            ("queued", "cancelled", "operator_queue_cleared"),
            ("uncertain", "cancelled", "operator_queue_cleared"),
        })
        self.assertEqual(self.store.clear_outbound_queue(), {})

    def test_two_store_claims_are_unique(self):
        self.add("one", "a")
        self.add("two", "b")
        other = Store(self.url, clock=lambda: self.now)
        self.addCleanup(other.engine.dispose)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tasks = list(pool.map(lambda store: store.claim(), (self.store, other)))
        self.assertEqual(sum(task is not None for task in tasks), 1)
        self.now += 1.0
        remaining = self.store.claim() or other.claim()
        self.assertEqual({task["id"] for task in tasks if task} | {remaining["id"]}, {"one", "two"})
        self.assertIsNone(self.store.claim())

    def test_two_store_claims_do_not_overlap_one_room(self):
        self.add("one", "a")
        self.add("two", "a", created=1001)
        other = Store(self.url, clock=lambda: self.now)
        self.addCleanup(other.engine.dispose)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            tasks = list(pool.map(lambda store: store.claim(), (self.store, other)))
        self.assertEqual([task["id"] for task in tasks if task], ["one"])
        self.assertIsNone(self.store.claim())

    def test_500_rooms_are_claimed_once(self):
        with self.store.engine.begin() as db:
            db.execute(rooms.insert(), [dict(id=f"r{i:03}", kind="group", enabled=1) for i in range(500)])
            db.execute(outbox.insert(), [dict(id=f"t{i:03}", room=f"r{i:03}", kind="group",
                text="text", reply_to_message_id=f"m{i:03}", status="pending", created=1000,
                available=1000, attempts=0) for i in range(500)])
        ids = set()
        for _ in range(500):
            task = self.store.claim()
            self.assertIsNotNone(task)
            ids.add(task["id"])
            self.finish(task)
            self.now += 1.0
        self.assertEqual(len(ids), 500)
        self.assertIsNone(self.store.claim())


if __name__ == "__main__":
    unittest.main()

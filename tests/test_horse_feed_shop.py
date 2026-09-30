import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from dzmm_bot.application.farm_service import FarmService
from dzmm_bot.application.services import GameService
from dzmm_bot.core import Inbound
from dzmm_bot.persistence.schema import currency, farm_plots, products, stocks
from dzmm_bot.presentation.farm import render_farm
from dzmm_bot.persistence.transport import rooms, outbox
from dzmm_bot.store import Store


class HorseFeedShopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
        self.store = Store("sqlite:///" + str(Path(self.tmp.name) / "feed-shop.db"),
                           secret="feed-shop-test", clock=lambda: self.now)
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert().values(id="g", kind="group", enabled=1))
        self.store.receive(Inbound(room="g", sender="p", name="Player", message_id="join", text="/注册"))
        with self.store.engine.begin() as db:
            GameService(db, self.now, "feed-shop-test", "p", "g", "credit").ledger(
                5000, "test_credit", "test", "feed-shop-credit")

    def test_shop_displays_direct_buy_prices_times_one_hundred(self):
        text = render_farm({"kind": "feed_shop"})
        self.assertIn("普通草料 1200币", text)
        self.assertIn("疾风燕麦 2800币", text)
        self.assertIn("优质牧草 5500币", text)

    def test_direct_purchase_charges_twenty_times_reference_price(self):
        with self.store.engine.begin() as db:
            service = FarmService(db, self.now, "feed-shop-test", "p", "g", "buy-hay")
            result = service.buy_feed("hay", 2)
            self.assertEqual(result["cost"], 2400)
            self.assertEqual(service.stock("hay"), 2)
            self.assertEqual(service.balance(), 100 + 5000 - 2400)
            paid = db.execute(select(currency.c.amount).where(
                currency.c.reference_id == "buy-hay")).scalar_one()
            self.assertEqual(paid, -240000)

    def test_sowing_automatically_harvests_already_mature_crops(self):
        with self.store.engine.begin() as db:
            service = FarmService(db, self.now, "feed-shop-test", "p", "g", "prepare-ripe-crop")
            service.farm(create=True)
            plot = db.execute(select(farm_plots).where(farm_plots.c.plot_no == 1)).mappings().one()
            db.execute(farm_plots.update().where(farm_plots.c.id == plot["id"]).values(
                crop_code="grass", batch_id="already-ripe-grass", planted_at=self.now - 3600,
                ready_at=self.now, yield_quantity=4, yield_min=3, yield_max=5,
                base_duration_seconds=3600, weather_snapshot="disabled", duration_multiplier=1,
                rule_version="ruihe-farm-v1:level-v2"))

        result = self.store.receive(Inbound(room="g", sender="p", name="Player",
            message_id="plant-and-auto-harvest", text="/播种 燕麦 1"))

        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            stock = db.execute(select(stocks.c.quantity).where(
                stocks.c.player_id == "p", stocks.c.item_code == "grass")).scalar_one()
            ripe_plot = db.execute(select(farm_plots).where(
                farm_plots.c.id == plot["id"])).mappings().one()
            reply = db.execute(select(outbox.c.text).where(
                outbox.c.reply_to_message_id == "plant-and-auto-harvest")).scalar_one()
        self.assertEqual(stock, 4)
        self.assertIsNone(ripe_plot["crop_code"])
        expected = "".join(map(chr, (0x81ea, 0x52a8, 0x6536, 0x83b7, 0xff1a)))
        self.assertIn(expected, reply)
        self.assertIn("".join(map(chr, (0x7267, 0x8349, 0x00d7, 0x34))), reply)

    def test_feeding_automatically_collects_ranch_batches(self):
        with self.store.engine.begin() as db:
            GameService(db, self.now, "feed-shop-test", "p", "g", "buy-chicken").buy("chicken", 1)
        self.now += 6 * 3600

        result = self.store.receive(Inbound(room="g", sender="p", name="Player",
            message_id="feed-and-auto-collect", text="/喂食"))

        self.assertTrue(result["queued"])
        with self.store.engine.connect() as db:
            product_rows = db.execute(select(products)).mappings().all()
            egg_stock = db.execute(select(stocks.c.quantity).where(
                stocks.c.player_id == "p", stocks.c.item_code == "egg")).scalar_one()
            reply = db.execute(select(outbox.c.text).where(
                outbox.c.reply_to_message_id == "feed-and-auto-collect")).scalar_one()
        self.assertTrue(product_rows)
        self.assertTrue(all(row["collected_at"] == self.now for row in product_rows))
        self.assertGreater(egg_stock, 0)
        expected = "".join(map(chr, (0x81ea, 0x52a8, 0x6536, 0x53d6, 0xff1a)))
        self.assertIn(expected, reply)
        self.assertIn("".join(map(chr, (0x9e21, 0x86cb, 0x00d7))), reply)


if __name__ == "__main__":
    unittest.main()

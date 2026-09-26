"""Run a reproducible 28-day economy rehearsal on an isolated temporary database."""
import argparse
import hashlib
import itertools
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import select, func
from .core import Inbound
from .store import Store
from .domain.economy import RANCH_CONFIG, capacity, tax, upgrade_price
from .application.services import metrics
from .application.verification import verify_production
from .persistence.transport import rooms, inbox
from .persistence.schema import accounts, animals, ranches, stocks, currency, inventory, transactions, events, products


def run(days=28, population=12):
    start = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
    clock = [start]
    serial = itertools.count()
    ids = itertools.count()
    def deterministic_uuid():
        return uuid.UUID(hashlib.sha256(f"economy-simulation-v1:{next(ids)}".encode()).hexdigest()[:32])
    with tempfile.TemporaryDirectory() as tmp:
        store = Store("sqlite:///" + str(Path(tmp) / "simulation.db"), secret="simulation-v1", clock=lambda: clock[0])
        try:
            with store.engine.begin() as db:
                # This database is disposable; disable disk sync only for this rehearsal.
                db.exec_driver_sql("PRAGMA synchronous=OFF")
                db.execute(rooms.insert().values(id="simulation", kind="group", enabled=1))
            def send(player, command):
                return store.receive(Inbound(room="simulation", sender=player, name=player,
                    message_id=str(next(serial)), text=command))
            daily = []
            with patch("uuid.uuid4", side_effect=deterministic_uuid):
                for i in range(population):
                    send(str(i), "/加入")
                    send(str(i), "/牧场 购买 鸡")
                for day in range(days):
                    clock[0] = start + (day + 1) * 86400
                    for i in range(population):
                        player = str(i)
                        send(player, "/牧场 出售 全部")
                        send(player, "/救济")
                        with store.engine.connect() as db:
                            herd = db.execute(select(animals).where(animals.c.player_id == player)).mappings().all()
                            stock = db.execute(select(stocks.c.quantity).where(stocks.c.player_id == player, stocks.c.item_code == "feed")).scalar() or 0
                        needed = sum(a["feed_cost"] for a in herd) - stock
                        if needed > 0:
                            send(player, f"/牧场 购买 饲料 {needed}")
                        for animal in herd:
                            send(player, "/牧场 喂食 " + animal["display_id"])
                        with store.engine.connect() as db:
                            balance = db.execute(select(accounts.c.balance).where(accounts.c.player_id == player)).scalar_one()
                            ranch = db.execute(select(ranches).where(ranches.c.player_id == player)).mappings().one()
                        name, code = [("鸡", "chicken"), ("羊", "sheep"), ("牛", "cow")][i % 3]
                        rule = RANCH_CONFIG["animals"][code]
                        if ranch["used_capacity"] + rule["space"] > capacity(ranch["level"], RANCH_CONFIG):
                            price = upgrade_price(ranch["level"], RANCH_CONFIG)
                            if balance >= price + tax(price, RANCH_CONFIG) + 30:
                                send(player, "/牧场 升级")
                        elif balance >= rule["price"] + tax(rule["price"], RANCH_CONFIG) + 30:
                            send(player, f"/牧场 购买 {name}")
                    with store.engine.connect() as db:
                        report = metrics(db)
                        for key in ["balance_difference", "pool_tax_difference", "pool_ledger_difference"]:
                            if report[key] != 0:
                                raise AssertionError((day, key, report[key]))
                        if report["account_mismatches"]:
                            raise AssertionError(report)
                        since = start + day * 86400
                        credits = db.execute(select(func.coalesce(func.sum(currency.c.amount), 0)).where(
                            currency.c.player_id.is_not(None), currency.c.amount > 0, currency.c.created_at > since)).scalar_one()
                        spending = -db.execute(select(func.coalesce(func.sum(currency.c.amount), 0)).where(
                            currency.c.player_id.is_not(None), currency.c.amount < 0, currency.c.created_at > since)).scalar_one()
                        feed_used = -db.execute(select(func.coalesce(func.sum(inventory.c.delta), 0)).where(
                            inventory.c.item_code == "feed", inventory.c.delta < 0, inventory.c.created_at > since)).scalar_one()
                        taxes = db.execute(select(func.coalesce(func.sum(transactions.c.tax_amount), 0)).where(transactions.c.created_at > since)).scalar_one()
                        gross = db.execute(select(func.coalesce(func.sum(transactions.c.gross_amount), 0)).where(transactions.c.created_at > since)).scalar_one()
                        balances = list(db.execute(select(accounts.c.balance).order_by(accounts.c.player_id)).scalars())
                        distribution = dict(db.execute(select(animals.c.animal_type, func.count()).group_by(animals.c.animal_type)).all())
                        day_report = {"day": day + 1, **report, "currency_created": credits,
                            "currency_spent": spending, "feed_consumed": feed_used, "daily_tax": taxes,
                            "tax_share": round(taxes / gross, 4) if gross else 0,
                            "balances": balances, "animals": distribution,
                            "levels": list(db.execute(select(ranches.c.level).order_by(ranches.c.player_id)).scalars())}
                        daily.append(day_report)
                # Check every animal's production history against frozen purchase/feed snapshots.
                with store.engine.connect() as db:
                    verification = verify_production(db, clock[0])
                    assert not verification["mismatches"], verification
                    product_count = db.execute(select(func.count()).select_from(products)).scalar_one()
                return {"days": days, "players": population, "config_version": "m1-v1", "seed": "economy-simulation-v1",
                        "policy": "Daily harvest/sell/relief/feed; reserve 30 coins, buy preferred species or upgrade.",
                        "opening_grant": population * 100, "products_created": product_count, "production_verification": verification,
                        "first_week_balance_change": [v - 100 for v in daily[min(6, days - 1)]["balances"]], "daily": daily}
        finally:
            store.engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="docs/economy-simulation-28d.json")
    args = parser.parse_args()
    report = run()
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"PASS: {report['days']} days, {report['players']} players; all daily balances and taxes reconcile. Report: {args.output}")


if __name__ == "__main__":
    main()

"""Read-only verification using immutable business snapshots."""
import json
from sqlalchemy import select
from dzmm_bot.domain.economy import interval, RUIHE_RANCH_VERSION, production_multiplier, production_quantity
from dzmm_bot.persistence.schema import events, products, configs, animals


def verify_production(db, now, player=None):
    configurations = {r["version"]: json.loads(r["payload_json"]) for r in db.execute(select(configs)).mappings()}
    query = select(events).where(events.c.kind.in_(["buy", "feed"])).order_by(events.c.created_at, events.c.id)
    if player is not None:
        query = query.where(events.c.player_id == player)
    timelines = {}
    for event in db.execute(query).mappings():
        payload = json.loads(event["payload_json"])
        if event["kind"] == "buy":
            for snapshot in payload.get("animals", []):
                timelines.setdefault(snapshot["id"], []).append((event["created_at"], snapshot))
        else:
            old = payload["previous"]
            version = payload.get("rule_version", event["config_version"])
            config = configurations[version]
            progress = old.get("cycle_progress", 0) if old.get("rule_version") == version else 0
            base, delay = interval(payload["seed"], old["sequence"], old["animal_type"], payload["level"], config)
            snapshot = {**old, "seed": payload["seed"], "interval_level": payload["level"],
                "rule_version": version, "next_production_at": event["created_at"] + delay * (1 - progress),
                "base_interval_hours": base, "production_until": payload["until"],
                "last_settled_at": event["created_at"], "cycle_progress": progress}
            timelines.setdefault(old["id"], []).append((event["created_at"], snapshot))
    mismatches = []
    verified = 0
    for animal_id, segments in timelines.items():
        # Sequence is monotonic and also orders multiple feeds at the same clock tick.
        segments.sort(key=lambda pair: (pair[0], pair[1]["sequence"]))
        expected = {}
        for i, (start, snapshot) in enumerate(segments):
            end = min(now, snapshot["production_until"], segments[i + 1][0] if i + 1 < len(segments) else now)
            due, sequence = snapshot["next_production_at"], snapshot["sequence"]
            config = configurations[snapshot["rule_version"]]
            if snapshot["rule_version"] == RUIHE_RANCH_VERSION:
                cursor = snapshot.get("last_settled_at", start)
                progress = snapshot.get("cycle_progress", 0)
                while cursor < end:
                    _, delay = interval(snapshot["seed"], sequence, snapshot["animal_type"], snapshot["interval_level"], config)
                    completion = cursor + delay * (1 - progress)
                    if completion > end:
                        break
                    quantity = production_quantity(1, production_multiplier(snapshot.get("affection", 0),
                        snapshot.get("feed_streak", 0), snapshot.get("premium_feed_active", False),
                        "breeze", config["animals"][snapshot["animal_type"]]["product"]))
                    expected[sequence] = (completion, config["animals"][snapshot["animal_type"]]["product"],
                                          snapshot["rule_version"], quantity)
                    sequence += 1
                    cursor, progress = completion, 0
            else:
                while due <= end:
                    expected[sequence] = (due, config["animals"][snapshot["animal_type"]]["product"],
                                          snapshot["rule_version"], 1)
                    sequence += 1
                    _, delay = interval(snapshot["seed"], sequence, snapshot["animal_type"], snapshot["interval_level"], config)
                    due += delay
        actual = {r["sequence"]: (r["produced_at"], r["product_type"], r["config_version"], r["quantity"]) for r in
                  db.execute(select(products).where(products.c.source_animal_id == animal_id)).mappings()}
        # Unsettled due batches are valid lazy state; persisted batches must match exactly.
        settled_sequence = db.execute(select(animals.c.sequence).where(animals.c.id == animal_id)).scalar_one()
        invalid = [sequence for sequence, row in actual.items() if expected.get(sequence) != row]
        invalid.extend(sequence for sequence in expected if sequence < settled_sequence and sequence not in actual)
        if invalid:
            mismatches.append({"animal": animal_id, "sequences": invalid})
        verified += len(actual)
    product_query = select(products.c.source_animal_id).distinct()
    if player is not None:
        product_query = product_query.where(products.c.player_id == player)
    for animal_id in db.execute(product_query).scalars():
        if animal_id not in timelines:
            mismatches.append({"animal": animal_id, "error": "missing_business_snapshot"})
    return {"animals_verified": len(timelines), "products_verified": verified, "mismatches": mismatches}

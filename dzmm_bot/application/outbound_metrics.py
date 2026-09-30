"""Bounded, content-free outbound latency view for administrators."""
import math

from sqlalchemy import case, func, select

from ..persistence.transport import outbox


def _percentiles(values):
    """Nearest-rank percentile in seconds; no interpolation of observed timings."""
    if not values:
        return {"samples": 0, "p50_seconds": None, "p95_seconds": None}
    values.sort()
    return {"samples": len(values),
            "p50_seconds": round(values[math.ceil(.50 * len(values)) - 1], 3),
            "p95_seconds": round(values[math.ceil(.95 * len(values)) - 1], 3)}


def outbound_performance(db, now, sample_limit=200):
    sample_limit = max(1, min(int(sample_limit), 1000))
    channel = case((outbox.c.reply_to_message_id.is_(None), "broadcast"),
                   (outbox.c.kind == "private", "private"), else_="group")
    state_counts = {"group": {}, "private": {}, "broadcast": {}}
    for name, status, count in db.execute(select(channel, outbox.c.status, func.count())
                                           .group_by(channel, outbox.c.status)):
        state_counts[name][status] = count
    waiting = ("pending", "leased", "sending")
    pending_count = sum(counts.get(status, 0) for counts in state_counts.values() for status in waiting)
    oldest = db.execute(select(func.min(outbox.c.created)).where(outbox.c.status.in_(waiting))).scalar_one()
    oldest_age = None if oldest is None else max(0, round(now - oldest, 3))
    recent = db.execute(select(outbox.c.status, outbox.c.created, outbox.c.last_claimed_at,
                               outbox.c.last_send_started_at, outbox.c.last_result_at)
                        .where(outbox.c.last_result_at.is_not(None))
                        .order_by(outbox.c.last_result_at.desc(), outbox.c.id.desc())
                        .limit(sample_limit)).mappings().all()
    durations = {"queue_wait": [], "send_attempt": [], "sent_end_to_end": [],
                 "simulated_end_to_end": []}
    invalid = {name: 0 for name in durations}

    def add(name, end, start):
        if end is None or start is None or not math.isfinite(end) or not math.isfinite(start) or end < start:
            invalid[name] += 1
        else:
            durations[name].append(end - start)

    for row in recent:
        add("queue_wait", row["last_claimed_at"], row["created"])
        add("send_attempt", row["last_result_at"], row["last_send_started_at"])
        if row["status"] in ("sent", "simulated"):
            add(row["status"] + "_end_to_end", row["last_result_at"], row["created"])
    rate_limited = db.execute(select(func.count()).select_from(outbox)
                              .where(outbox.c.error == "rate_limited")).scalar_one()
    return {"sample_limit": sample_limit, "recent_results": len(recent),
            "pending_count": pending_count, "oldest_pending_seconds": oldest_age,
            "channels": state_counts, "rate_limited_current": rate_limited,
            "failed_count": sum(v.get("failed", 0) for v in state_counts.values()),
            "uncertain_count": sum(v.get("uncertain", 0) for v in state_counts.values()),
            "latency": {name: {**_percentiles(values), "invalid_samples": invalid[name]}
                        for name, values in durations.items()}}

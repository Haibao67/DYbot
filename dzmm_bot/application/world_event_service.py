"""Cooldown-limited game highlights, delivered through the durable broadcast queue."""
import json
import uuid

from sqlalchemy import select

from dzmm_bot.persistence.schema import world_event_log
from dzmm_bot.persistence.transport import players, rooms
from dzmm_bot.presentation.information import render_world_event
from .announcement_service import AnnouncementService


class WorldEventService:
    def __init__(self, db, now, harvest_threshold=0, player_cooldown=0, global_cooldown=0):
        self.db, self.now = db, now
        self.harvest_threshold = int(harvest_threshold)
        self.player_cooldown, self.global_cooldown = int(player_cooldown), int(global_cooldown)
        self.enabled = self.harvest_threshold > 0 and self.player_cooldown > 0 and self.global_cooldown > 0

    def record_harvest(self, player_id, room_id, totals, scope="room"):
        count = sum(int(value) for value in totals.values())
        if not self.enabled or count < self.harvest_threshold:
            return False
        item = max(totals, key=totals.get)
        content = render_world_event("large_harvest", self._player_name(player_id), quantity=count, item=item)
        return self.publish("large_harvest", player_id, room_id,
            {"quantity": count, "item": item}, content, scope)

    def record_factory_critical(self, player_id, room_id, results, scope="room"):
        hit = next((row for row in results if row.get("critical") and row.get("quantity", 0) > 0), None)
        if not self.enabled or not hit:
            return False
        content = render_world_event("factory_critical", self._player_name(player_id),
                                     product=hit["recipe"]["display_name"])
        return self.publish("factory_critical", player_id, room_id,
            {"recipe_id": hit["recipe"]["id"], "quantity": hit["quantity"]}, content, scope)

    def publish(self, event_type, player_id, room_id, payload, content, scope="room"):
        """Extension point for cooldown-limited room or global world events."""
        return self._broadcast(event_type, player_id, room_id, payload, content, scope)

    def _player_name(self, player_id):
        return self.db.execute(select(players.c.name).where(players.c.id == player_id)).scalar() or "一位玩家"

    def _broadcast(self, event_type, player_id, room_id, payload, content, scope):
        if not self.enabled or scope not in ("room", "global") or (scope == "room" and not room_id):
            return False
        targets = self.db.execute(select(rooms.c.id).where(rooms.c.kind == "group", rooms.c.enabled == 1))
        target_ids = set(targets.scalars())
        if not target_ids or (scope == "room" and room_id not in target_ids):
            return False
        last_player = self.db.execute(select(world_event_log.c.created_at).where(
            world_event_log.c.event_type == event_type,
            world_event_log.c.player_id == player_id
            ).order_by(world_event_log.c.created_at.desc()).limit(1)).scalar()
        if last_player is not None and self.now - last_player < self.player_cooldown:
            return False
        last_global = self.db.execute(select(world_event_log.c.created_at).where(
            world_event_log.c.created_at.is_not(None)
        ).order_by(world_event_log.c.created_at.desc()).limit(1)).scalar()
        if last_global is not None and self.now - last_global < self.global_cooldown:
            return False
        event_id = str(uuid.uuid4())
        target_room = room_id if scope == "room" else None
        job_id, _ = AnnouncementService(self.db, self.now, player_id)._queue_job(
            None, "world_event", content, scope, target_room)
        self.db.execute(world_event_log.insert().values(id=event_id, event_type=event_type,
            player_id=player_id, room_id=room_id, payload_json=json.dumps(payload, ensure_ascii=False),
            created_at=self.now, broadcasted_at=None, scope=scope, broadcast_job_id=job_id))
        return bool(job_id)

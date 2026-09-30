"""Transactional inbox/outbox. One Core process; PostgreSQL or local SQLite."""
import time
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import and_, create_engine, exists, func, or_, select, update
from sqlalchemy.engine import make_url

from threading import RLock
from .persistence.transport import meta, rooms, players, members, inbox, outbox
from .persistence.migrate import migrate
from .application.command_router import CommandRouter
from .application.invite_approval_service import approve_invitation
from .application.auction_service import finalize_due
from .persistence.schema import pending_group_invites, audit


class Store:
    def __init__(self, url, secret="local-development-only", clock=None, game_stage="full", whitelist=None,
                 admins=None, world_event_policy=None, admin_login_password_hash="",
                 auction_cooldown_seconds=300, competition_options=None, race_auto_enabled=False,
                 race_timezone='Asia/Shanghai', weather_enabled=False, race_commentary_options=None):
        parsed = make_url(url)
        if parsed.drivername.startswith("sqlite") and parsed.database not in (None, ":memory:"):
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url)
        self.lock = RLock()
        self.clock = clock or time.time
        self.outbound_global_interval_seconds = 0.5
        self._reply_claim_streak = 0
        self.secret = secret
        if game_stage not in ("m0", "ranch", "full"):
            raise ValueError("DZMM_GAME_STAGE must be m0, ranch or full")
        self.game_stage, self.whitelist, self.admins = game_stage, set(whitelist or []), set(admins or [])
        self.admin_login_password_hash = admin_login_password_hash
        self.auction_cooldown_seconds = auction_cooldown_seconds
        self.competition_options = dict(competition_options or {})
        self.race_commentary_options = dict(race_commentary_options or {})
        self.race_auto_enabled=race_auto_enabled
        self.weather_enabled=bool(weather_enabled)
        from .domain.race_runtime import DailyRaceTemplate
        self.race_template=DailyRaceTemplate(timezone=race_timezone,enabled=race_auto_enabled)
        self.world_event_policy = dict(world_event_policy or {})
        migrate(self.engine)
        if self.weather_enabled and self.game_stage == 'full':
            try:
                self.reconcile_weather()
            except Exception:
                import logging
                logging.getLogger(__name__).exception('daily weather bootstrap failed; game services remain available')
        if race_auto_enabled:
            from .application.daily_race_service import RaceBootstrap
            import logging
            with self.engine.begin() as db:
                try:
                    with db.begin_nested(): RaceBootstrap.initialize(db,self.clock(),self.race_template)
                except Exception:
                    logging.getLogger(__name__).exception('RUNTIME_VALIDATION_FAILED at bootstrap')
            self.reconcile_races()

    def reconcile_races(self):
        self.expire_red_packets()
        if not self.race_auto_enabled or self.game_stage!='full': return {}
        from .application.daily_race_service import DailyRaceScheduler, RaceStateReconciler
        with self.lock,self.engine.begin() as db:
            if self.engine.dialect.name=='sqlite': db.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                from .persistence.schema import world
                db.execute(select(world.c.id).where(world.c.id==1).with_for_update()).first()
            now=self.clock()
            DailyRaceScheduler(db,now,self.race_template).ensure_upcoming()
            outcomes=RaceStateReconciler(db,now,self.secret,self.race_commentary_options).reconcile()
        return outcomes

    def process_pending_race_commentary(self):
        """Claim durable narration jobs, call the API outside DB transactions, then queue outbox."""
        if not self.race_commentary_options.get('enabled'):
            return 0
        import json
        import logging
        import secrets
        from .application.race_commentary import RaceCommentaryService, build_items
        from .application.competition_service import CompetitionService, dumps
        from .application.race_service import RaceService
        from .domain.race_engine import RaceContext
        from .persistence.schema import race_definitions as races
        from sqlalchemy import select
        logger=logging.getLogger(__name__)
        with self.engine.connect() as db:
            rows=db.execute(select(races).where(races.c.status=='FINISHED',races.c.settled_at.is_not(None))
                .order_by(races.c.settled_at).limit(100)).mappings().all()
        completed=0
        for candidate in rows:
            claim=None
            now=self.clock()
            with self.lock,self.engine.begin() as db:
                if db.dialect.name=='sqlite':
                    db.exec_driver_sql('BEGIN IMMEDIATE')
                row=db.execute(select(races).where(races.c.race_id==candidate['race_id']).with_for_update()).mappings().first()
                if not row:
                    continue
                definition=json.loads(row['definition_json'])
                if definition.get('race_broadcast_task_ids'):
                    continue
                state=definition.get('race_commentary_status')
                if state not in {'pending','generating','ready'}:
                    continue
                if state=='generating':
                    age=now-float(definition.get('race_commentary_started_at',now))
                    if age < 60:
                        continue
                    fallbacks=definition.get('race_commentary_fallbacks',{})
                    definition['race_commentary']=fallbacks
                    definition['race_commentary_status']='ready'
                    definition.pop('race_commentary_started_at',None)
                    db.execute(races.update().where(races.c.race_id==row['race_id']).values(
                        definition_json=dumps(definition)))
                    self._queue_prepared_race_broadcast(db,row,definition,now)
                    completed+=1
                    continue
                if state=='ready':
                    self._queue_prepared_race_broadcast(db,row,definition,now)
                    completed+=1
                    continue
                result=RaceService(db,now,None).get_result(row['race_id'])
                if result is None or not row['snapshot_json']:
                    logger.error('race commentary preparation missing result ref=%s',secrets.token_hex(4))
                    continue
                context=RaceContext(**json.loads(row['snapshot_json']))
                name=row['name']
                items=build_items(name,context,result)
                fallback={"checkpoints":{item['key']:item['fallback'] for item in items[:-1]},
                          "final":items[-1]['fallback']}
                definition['race_commentary_status']='generating'
                definition['race_commentary_started_at']=now
                definition['race_commentary_fallbacks']=fallback
                db.execute(races.update().where(races.c.race_id==row['race_id']).values(
                    definition_json=dumps(definition)))
                claim={"race_id":row['race_id'],"name":name,"context":context,
                       "result":result,"items":items}
            # No database connection/transaction is held while waiting for DeepSeek.
            try:
                options=self.race_commentary_options
                generator=RaceCommentaryService(enabled=options.get('enabled',False),
                    api_key=options.get('api_key',''),base_url=options.get('base_url','https://api.deepseek.com'),
                    model=options.get('model','deepseek-flash'),timeout_seconds=options.get('timeout_seconds',10),
                    max_chars=options.get('max_chars',100),max_calls_per_race=options.get('max_calls_per_race',20),
                    transport=options.get('transport'))
                outputs=generator.generate_sync(claim['items'])
                commentary={"checkpoints":{item['key']:output['text'] for item,output in
                                             zip(claim['items'][:-1],outputs[:-1])},
                            "final":outputs[-1]['text']}
                failures={output.get('failure') for output in outputs if output.get('failure')}
            except Exception:
                logger.error('race commentary batch failed; deterministic fallback selected ref=%s',secrets.token_hex(4))
                commentary={"checkpoints":{item['key']:item['fallback'] for item in claim['items'][:-1]},
                            "final":claim['items'][-1]['fallback']}
                failures={'batch_error'}
            with self.lock,self.engine.begin() as db:
                if db.dialect.name=='sqlite':
                    db.exec_driver_sql('BEGIN IMMEDIATE')
                row=db.execute(select(races).where(races.c.race_id==claim['race_id']).with_for_update()).mappings().first()
                if not row:
                    continue
                definition=json.loads(row['definition_json'])
                if definition.get('race_commentary_status')!='generating':
                    continue
                definition['race_commentary']=commentary
                definition['race_commentary_status']='ready'
                definition.pop('race_commentary_started_at',None)
                definition.pop('race_commentary_fallbacks',None)
                definition['race_commentary_failures']=sorted(failures)
                db.execute(races.update().where(races.c.race_id==claim['race_id']).values(
                    definition_json=dumps(definition)))
                self._queue_prepared_race_broadcast(db,row,definition,self.clock())
                completed+=1
        return completed

    def _queue_prepared_race_broadcast(self, db, row, definition, now):
        from .application.competition_service import CompetitionService
        from .application.race_service import RaceService
        from .domain.race_engine import RaceContext
        import json
        context=RaceContext(**json.loads(row['snapshot_json']))
        result=RaceService(db,now,None).get_result(row['race_id'])
        if result is None:
            return False
        CompetitionService(db,now,self.secret).queue_race_broadcast(row,definition,context,result)
        return True

    def expire_red_packets(self):
        from .application.red_packet_service import expire_packets
        with self.lock,self.engine.begin() as db:
            if self.engine.dialect.name=='sqlite':
                db.exec_driver_sql('BEGIN IMMEDIATE')
            return expire_packets(db,self.clock(),self.secret)

    def reconcile_weather(self):
        if not self.weather_enabled or self.game_stage != 'full':
            return {"created": 0, "broadcasts": 0}
        from .application.weather_service import WeatherService
        with self.lock, self.engine.begin() as db:
            if self.engine.dialect.name == 'sqlite':
                db.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                from .persistence.schema import world
                db.execute(select(world.c.id).where(world.c.id == 1).with_for_update()).first()
            return WeatherService(db,self.clock(),enabled=True).reconcile()

    def receive(self, event):
        now = self.clock()
        words = event.text.strip().split(maxsplit=1)
        should_process_commentary=bool(words and words[0] in {'/强制开赛','/強制開賽'})
        stored_text = ("/管理员登陆 [已隐藏]" if words and words[0] in
                       {"/管理员登陆", "/管理员登录", "/管理員登錄", "/管理員登入"} else event.text)
        with self.lock, self.engine.begin() as db:
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                from .persistence.schema import world
                db.execute(select(world).where(world.c.id == 1).with_for_update()).first()
            room = db.execute(select(rooms).where(rooms.c.id == event.room, rooms.c.enabled == 1)).mappings().first()
            if room is None and event.kind == "private":
                existing = db.execute(select(rooms.c.id).where(rooms.c.id == event.room)).first()
                if existing:
                    return {"ok": True, "ignored": True}
                db.execute(rooms.insert().values(id=event.room, kind="private", enabled=1))
                room = db.execute(select(rooms).where(rooms.c.id == event.room)).mappings().one()
            if not room:
                return {"ok": True, "ignored": True}
            if event.kind == "private" and room["kind"] != "private":
                return {"ok": True, "ignored": True}
            if db.execute(select(inbox.c.id).where(inbox.c.room == event.room, inbox.c.message_id == event.message_id)).first():
                return {"ok": True, "duplicate": True}
            finalize_due(db, now, self.secret)
            from .application.red_packet_service import expire_packets
            expire_packets(db,now,self.secret)
            db.execute(inbox.insert().values(id=str(uuid.uuid4()), room=event.room, message_id=event.message_id,
                                            sender=event.sender, text=stored_text, created=now))
            reply = CommandRouter(db, now, self.secret, self.game_stage, self.whitelist, self.admins,
                                  self.world_event_policy, self.admin_login_password_hash,
                                  self.auction_cooldown_seconds, self.competition_options,
                                  self.weather_enabled,self.race_commentary_options).dispatch(event, room)
            if reply:
                replies = reply if isinstance(reply, list) else [reply]
                batch_id = uuid.uuid4().hex[:24]
                for index, body in enumerate(replies):
                    db.execute(outbox.insert().values(id=str(uuid.UUID(hex=f'{batch_id}{index:08x}')), room=event.room, kind=room["kind"],
                               reply_to_message_id=event.message_id, reply_to_sender_id=event.sender,
                               reply_to_text=stored_text,
                               text=body, status="pending", created=now, available=now, attempts=0))
        receive_result={"ok": True, "queued": reply is not None,
                        "commentary_pending": bool(self.race_commentary_options.get('enabled')
                                                   and should_process_commentary)}
        return receive_result

    def finalize_auction(self):
        now = self.clock()
        with self.lock, self.engine.begin() as db:
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                from .persistence.schema import world
                db.execute(select(world.c.id).where(world.c.id == 1).with_for_update()).first()
            auction_changed = finalize_due(db, now, self.secret) is not None
            from .application.red_packet_service import expire_packets
            expired=expire_packets(db,now,self.secret)
            if self.competition_options:
                from .application.competition_service import CompetitionService
                CompetitionService(db,now,self.secret,race_commentary_options=self.race_commentary_options,
                                   **self.competition_options).tick()
            changed=auction_changed or bool(expired)
        return changed

    def record_invite(self, event, code, group_name, source_content=None):
        """Persist one private invitation and its native-reference acknowledgement."""
        now = self.clock()
        with self.lock, self.engine.begin() as db:
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            if db.execute(select(inbox.c.id).where(inbox.c.room == event.room,
                                                    inbox.c.message_id == event.message_id)).first():
                return {"ok": True, "duplicate": True}
            room = db.execute(select(rooms).where(rooms.c.id == event.room)).mappings().first()
            if room and (room["kind"] != "private" or room["enabled"] != 1):
                return {"ok": True, "ignored": True}
            if not room:
                db.execute(rooms.insert().values(id=event.room, kind="private", enabled=1))
            db.execute(inbox.insert().values(id=str(uuid.uuid4()), room=event.room,
                       message_id=event.message_id, sender=event.sender, text=event.text, created=now))
            existing = db.execute(select(pending_group_invites.c.id).where(
                pending_group_invites.c.invite_code == code,
                pending_group_invites.c.status.in_(["pending", "approved", "joining", "joined"]))).first()
            if not existing:
                db.execute(pending_group_invites.insert().values(id=str(uuid.uuid4()),
                           source_room_id=event.room, source_message_id=event.message_id,
                           inviter_id=event.sender, invite_code=code, group_name=group_name,
                           source_content_json=json.dumps(source_content) if source_content else None,
                           status="pending", created_at=now))
            db.execute(outbox.insert().values(id=str(uuid.uuid4()), room=event.room, kind="private",
                       reply_to_message_id=event.message_id, reply_to_sender_id=event.sender,
                       reply_to_text=event.text,
                       reply_to_content_json=json.dumps(source_content) if source_content else None,
                       text="已记录群聊邀请，等待管理员同意。",
                       status="pending", created=now, available=now, attempts=0))
            return {"ok": True, "queued": True, "pending": not bool(existing)}

    def approve_invite(self, invite_id):
        now = self.clock()
        with self.lock, self.engine.begin() as db:
            return approve_invitation(db, now, "admin_token", invite_id)

    def claim_invite(self):
        """A join attempt is never retried automatically after an uncertain exit."""
        with self.lock, self.engine.begin() as db:
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            invitation = db.execute(select(pending_group_invites).where(
                pending_group_invites.c.status == "approved").order_by(
                pending_group_invites.c.approved_at).with_for_update()).mappings().first()
            if not invitation:
                return None
            db.execute(pending_group_invites.update().where(pending_group_invites.c.id == invitation["id"])
                       .values(status="joining"))
            return {"id": invitation["id"], "invite_code": invitation["invite_code"]}

    def finish_invite(self, invite_id, status, room_id=None, error=None):
        if status == "joined" and not room_id:
            return False
        now = self.clock()
        with self.lock, self.engine.begin() as db:
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            invitation = db.execute(select(pending_group_invites).where(
                pending_group_invites.c.id == invite_id).with_for_update()).mappings().first()
            if not invitation or invitation["status"] != "joining":
                return False
            if status == "joined":
                room = db.execute(select(rooms).where(rooms.c.id == room_id)).mappings().first()
                if room and room["kind"] != "group":
                    return False
                if room:
                    db.execute(rooms.update().where(rooms.c.id == room_id).values(enabled=1))
                else:
                    db.execute(rooms.insert().values(id=room_id, kind="group", enabled=1))
                group_name = " ".join(invitation["group_name"].split())[:80] or "群聊"
                original_text = db.execute(select(inbox.c.text).where(
                    inbox.c.room == invitation["source_room_id"],
                    inbox.c.message_id == invitation["source_message_id"])).scalar_one()
                db.execute(outbox.insert().values(
                    id=str(uuid.uuid4()), room=invitation["source_room_id"], kind="private",
                    reply_to_message_id=invitation["source_message_id"],
                    reply_to_sender_id=invitation["inviter_id"],
                    reply_to_text=original_text,
                    reply_to_content_json=invitation["source_content_json"],
                    text=f"✅ 已接受邀请，现已加入「{group_name}」。",
                    status="pending", created=now, available=now, attempts=0))
            db.execute(pending_group_invites.update().where(pending_group_invites.c.id == invite_id)
                       .values(status=status, joined_room_id=room_id,
                               joined_at=now if status == "joined" else None,
                               last_error=(error or "")[:200] or None))
            return True

    def claim(self):
        now = self.clock()
        with self.lock, self.engine.begin() as db:
            # A single transaction arbitrates claims across Core processes as well as threads.
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                from .persistence.schema import world
                db.execute(select(world.c.id).where(world.c.id == 1).with_for_update()).first()
            # Persist a single global, no-burst dispatch bucket in existing room
            # timestamps. A claim reserves one slot per second across all rooms.
            last_dispatch = db.execute(select(func.max(rooms.c.last_dispatched_at))).scalar()
            if (last_dispatch is not None and now <
                    last_dispatch + self.outbound_global_interval_seconds):
                return None
            # Never automatically resend a task whose previous worker may have sent it.
            db.execute(update(outbox).where(outbox.c.status == "sending", outbox.c.lease_until < now)
                       .values(status="uncertain", error="worker_lost_after_send_started", last_result_at=now))
            db.execute(update(outbox).where(outbox.c.status == "leased", outbox.c.lease_until < now)
                       .values(status="pending", lease=None))
            earlier = outbox.alias("earlier")
            is_earlier = or_(earlier.c.created < outbox.c.created,
                             and_(earlier.c.created == outbox.c.created, earlier.c.id < outbox.c.id))
            # Race checkpoint messages deliberately pause between checkpoints. During that
            # scheduled gap, let player replies use the room instead of treating the future
            # race broadcast as a FIFO barrier. Ordinary broadcasts and currently-sendable
            # race pages continue to preserve strict per-room order.
            reply_may_pass_waiting_race = and_(
                outbox.c.reply_to_message_id.is_not(None),
                earlier.c.id.like("acebca57-%"),
                earlier.c.status == "pending",
                earlier.c.available > now,
            )
            room_head = ~exists(select(earlier.c.id).where(
                earlier.c.room == outbox.c.room,
                earlier.c.status.in_(("pending", "leased", "sending")), is_earlier,
                ~reply_may_pass_waiting_race))
            base = (select(outbox).join(rooms, rooms.c.id == outbox.c.room)
                    .where(rooms.c.enabled == 1, outbox.c.status == "pending",
                           outbox.c.available <= now, room_head)
                    .order_by(func.coalesce(rooms.c.last_dispatched_at, -1), outbox.c.created, outbox.c.id)
                    .limit(1))
            # One aged announcement per eight replies prevents starvation while keeping
            # replies ahead of announcements in the normal case. Only room heads compete.
            broadcast = self._reply_claim_streak >= 8
            classes = (False, True) if broadcast else (True, False)
            task = None
            for is_reply in classes:
                condition = (outbox.c.reply_to_message_id.is_not(None) if is_reply else
                             outbox.c.reply_to_message_id.is_(None))
                task = db.execute(base.where(condition)).mappings().first()
                if task:
                    break
            if not task:
                return None
            lease = str(uuid.uuid4())
            changed = db.execute(outbox.update().where(outbox.c.id == task["id"], outbox.c.status == "pending")
                                 .values(status="leased", lease=lease, lease_until=now + 60,
                                         attempts=task["attempts"] + 1, last_claimed_at=now))
            if changed.rowcount != 1:
                return None
            last = db.execute(select(func.max(rooms.c.last_dispatched_at))).scalar()
            dispatched_at = max(now, (last or now))
            db.execute(rooms.update().where(rooms.c.id == task["room"]).values(last_dispatched_at=dispatched_at))
            self._reply_claim_streak = min(8, self._reply_claim_streak + 1) if is_reply else 0
            return {**dict(task), "lease": lease, "status": "leased"}

    def clear_outbound_queue(self):
        """Cancel all active deliveries without deleting their audit records."""
        from collections import Counter
        from .persistence.schema import broadcast_deliveries, broadcast_jobs
        now = self.clock()
        counts = Counter()
        with self.lock, self.engine.begin() as db:
            if self.engine.dialect.name == "sqlite":
                db.exec_driver_sql("BEGIN IMMEDIATE")
            active = db.execute(select(outbox.c.id, outbox.c.status).where(
                outbox.c.status.in_(("pending", "leased", "sending", "uncertain"))
            ).with_for_update()).all()
            if not active:
                return {}
            ids = [row.id for row in active]
            for row in active:
                counts[row.status] += 1
            db.execute(outbox.update().where(outbox.c.id.in_(ids)).values(
                status="cancelled", error="operator_queue_cleared", lease=None,
                lease_until=None, last_result_at=now))
            deliveries = db.execute(select(broadcast_deliveries).where(
                broadcast_deliveries.c.outbox_id.in_(ids)).with_for_update()).mappings().all()
            affected_jobs = set()
            for delivery in deliveries:
                affected_jobs.add(delivery["broadcast_job_id"])
                db.execute(broadcast_deliveries.update().where(
                    broadcast_deliveries.c.id == delivery["id"]).values(
                        status="failed", last_error="operator_queue_cleared"))
            for job_id in affected_jobs:
                states = db.execute(select(broadcast_deliveries.c.status).where(
                    broadcast_deliveries.c.broadcast_job_id == job_id)).scalars().all()
                succeeded = states.count("success")
                failed = states.count("failed")
                unfinished = len(states) - succeeded - failed
                job_status = ("running" if succeeded or failed else "queued") if unfinished else (
                    "failed" if failed == len(states) else "partial_failed" if failed else "completed")
                db.execute(broadcast_jobs.update().where(broadcast_jobs.c.id == job_id).values(
                    status=job_status, success_count=succeeded, failure_count=failed,
                    finished_at=None if unfinished else now))
        return dict(counts)

    def transition(self, task_id, lease, action, platform_id=None, error=None, error_detail=None):
        now = self.clock()
        with self.engine.begin() as db:
            task = db.execute(select(outbox).where(outbox.c.id == task_id)).mappings().first()
            if not task or task["lease"] != lease:
                return False
            if action == "begin":
                if task["status"] != "leased" or task["lease_until"] < now:
                    return False
                enabled = db.execute(select(rooms.c.id).where(rooms.c.id == task["room"], rooms.c.enabled == 1)).first()
                if not enabled:
                    return False
                values = {"status": "sending", "lease_until": now + 60,
                          "last_send_started_at": now, "last_result_at": None}
            else:
                if task["status"] not in ("sending", "uncertain"):
                    return task["status"] == action and task["platform_id"] == platform_id
                status = action
                if status == "retry":
                    # A platform send is never replayed automatically. "retry" is
                    # retained as a gateway outcome for compatibility, but recorded
                    # as terminal failure so the room queue can continue safely.
                    status = "failed"
                values = {"status": status, "platform_id": platform_id, "error": error,
                          "available": now + 60, "lease_until": None, "last_result_at": now}
                if status in ("sent", "simulated"):
                    values.update(error=None, error_detail=None)
                else:
                    history = []
                    if task.get("error_detail"):
                        try:
                            history = json.loads(task["error_detail"])
                        except (TypeError, ValueError):
                            history = []
                    if not isinstance(history, list):
                        history = []
                    detail = dict(error_detail or {})
                    detail.update({"recorded_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
                                   "task_id": task_id, "attempt": task["attempts"],
                                   "channel": task["kind"], "reply_chars": len(task["text"]),
                                   "reply_line_breaks": task["text"].count("\n"),
                                   "category": error or action})
                    history.append(detail)
                    values["error_detail"] = json.dumps(history[-5:], ensure_ascii=False, separators=(",", ":"))
            db.execute(outbox.update().where(outbox.c.id == task_id).values(**values))
            if action in ('sent','simulated') and task_id.startswith('acebca57-'):
                import uuid
                raw=uuid.UUID(task_id).hex
                following=str(uuid.UUID(hex=raw[:24]+f'{int(raw[24:],16)+1:08x}'))
                next_task=db.execute(select(outbox).where(outbox.c.id==following,outbox.c.status=='pending')).mappings().first()
                if next_task:
                    same_checkpoint=task['text'].splitlines()[0]==next_task['text'].splitlines()[0]
                    db.execute(outbox.update().where(outbox.c.id==following,outbox.c.status=='pending').values(
                        available=now+(0 if same_checkpoint else 10)))

            from .persistence.schema import broadcast_deliveries, broadcast_jobs, world_event_log
            from sqlalchemy import func
            delivery = db.execute(select(broadcast_deliveries).where(
                broadcast_deliveries.c.outbox_id == task_id).with_for_update()).mappings().first()
            if delivery:
                delivery_status = "sending" if action in ("begin", "uncertain") else (
                    "success" if values.get("status") in ("sent", "simulated") else
                    "queued" if values.get("status") == "pending" else "failed")
                delivery_values = {"status": delivery_status}
                if action == "begin":
                    delivery_values["attempt_count"] = delivery["attempt_count"] + 1
                if delivery_status == "success":
                    delivery_values.update(sent_at=now, last_error=None)
                    db.execute(world_event_log.update().where(
                        world_event_log.c.broadcast_job_id == delivery["broadcast_job_id"],
                        world_event_log.c.broadcasted_at.is_(None)).values(broadcasted_at=now))
                elif delivery_status == "failed":
                    delivery_values["last_error"] = (error or action or "send_failed")[:300]
                db.execute(broadcast_deliveries.update().where(broadcast_deliveries.c.id == delivery["id"])
                           .values(**delivery_values))
                siblings = db.execute(select(broadcast_deliveries.c.status).where(
                    broadcast_deliveries.c.broadcast_job_id == delivery["broadcast_job_id"])).scalars().all()
                success_count = siblings.count("success")
                failure_count = siblings.count("failed")
                unfinished = len(siblings) - success_count - failure_count
                if unfinished:
                    job_status, finished_at = ("running" if "sending" in siblings or success_count or failure_count else "queued"), None
                else:
                    job_status = "failed" if failure_count == len(siblings) else (
                        "partial_failed" if failure_count else "completed")
                    finished_at = now
                db.execute(broadcast_jobs.update().where(broadcast_jobs.c.id == delivery["broadcast_job_id"]).values(
                    status=job_status, success_count=success_count, failure_count=failure_count,
                    started_at=func.coalesce(broadcast_jobs.c.started_at, now) if action == "begin" else broadcast_jobs.c.started_at,
                    finished_at=finished_at))
            return True

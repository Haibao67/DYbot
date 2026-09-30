"""Transactional update registry and broadcasts queued through the existing outbox."""
import json
import uuid

from sqlalchemy import select

from dzmm_bot.domain.update_manifest import load_manifest, VERSION_RE
from dzmm_bot.persistence.schema import game_updates, broadcast_jobs, broadcast_deliveries
from dzmm_bot.persistence.transport import rooms, outbox
from dzmm_bot.presentation.information import render_announcement, render_update, render_broadcast_status


class AnnouncementError(ValueError):
    pass


class AnnouncementService:
    def __init__(self, db, now, sender):
        self.db, self.now, self.sender = db, now, sender

    def _update(self, version, published_only=False):
        query = select(game_updates).where(game_updates.c.version == version)
        if published_only:
            query = query.where(game_updates.c.status == "published")
        row = self.db.execute(query).mappings().first()
        if not row:
            return None
        return {**dict(row), "manifest": json.loads(row["manifest_json"]),
                "changelog": json.loads(row["changelog_json"])}

    def create_update(self, version):
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise AnnouncementError("版本号格式应为 x.y.z。")
        manifest = load_manifest(version)
        if not manifest:
            raise AnnouncementError(f"找不到或无法验证 v{version} 的更新清单；请先添加对应的 JSON manifest。")
        if self._update(version):
            raise AnnouncementError(f"v{version} 的更新记录已存在。")
        preview = render_announcement(manifest)
        row = {"id": str(uuid.uuid4()), "version": version, "title": manifest["title"],
               "theme": manifest["theme"], "summary": manifest["summary"], "content": preview,
               "status": "draft", "created_at": self.now, "published_at": None,
               "created_by": self.sender, "manifest_json": json.dumps(manifest, ensure_ascii=False),
               "changelog_json": json.dumps(manifest.get("changelog", {}), ensure_ascii=False)}
        self.db.execute(game_updates.insert().values(**row))
        return f"✅ 已创建 v{version} 更新草稿。\n预览：/预览更新 {version}"

    def preview(self, version):
        update = self._update(version)
        if not update:
            return f"找不到 v{version} 更新。"
        return "👁️ 公告预览\n" + render_announcement(update["manifest"])

    def _queue_job(self, update_id, job_type, content, scope="global", room_id=None):
        from dzmm_bot.presentation.compact import compact_text, decorate, fits
        content = compact_text(decorate(content))
        if not fits(content, reserve=5):
            raise AnnouncementError("公告压缩后仍超过1000字或10次换行，请缩短内容后重新发布。")
        targets = self.db.execute(select(rooms).where(rooms.c.enabled == 1, rooms.c.kind == "group")
                                  .order_by(rooms.c.id)).mappings().all()
        if scope == "room":
            targets = [room for room in targets if room["id"] == room_id]
        if not targets:
            raise AnnouncementError("没有可用的已启用群聊目标，未创建广播。")
        job_id = str(uuid.uuid4())
        self.db.execute(broadcast_jobs.insert().values(id=job_id, update_id=update_id, type=job_type,
            status="queued", target_count=len(targets), success_count=0, failure_count=0,
            created_at=self.now, started_at=None, finished_at=None, content=content, scope=scope))
        for room in targets:
            task_id = str(uuid.uuid4())
            delivery_id = str(uuid.uuid4())
            self.db.execute(broadcast_deliveries.insert().values(id=delivery_id, broadcast_job_id=job_id,
                room_id=room["id"], status="queued", attempt_count=0, last_error=None,
                sent_at=None, created_at=self.now, outbox_id=task_id))
            self.db.execute(outbox.insert().values(id=task_id, room=room["id"], kind="group",
                reply_to_message_id=None, reply_to_sender_id=None, reply_to_text=None,
                text=content, status="pending", created=self.now, available=self.now, attempts=0))
        return job_id, len(targets)

    def publish(self, version):
        update = self.db.execute(select(game_updates).where(game_updates.c.version == version)
                                 .with_for_update()).mappings().first()
        if not update:
            raise AnnouncementError(f"找不到 v{version} 更新。")
        if update["status"] != "draft":
            raise AnnouncementError(f"v{version} 当前状态为 {update['status']}，不能重复发布。")
        manifest = json.loads(update["manifest_json"])
        content = render_announcement(manifest)
        if not content or len(content) > 10000:
            raise AnnouncementError("公告内容为空或超过平台限制。")
        job_id, target_count = self._queue_job(update["id"], "update", content)
        self.db.execute(game_updates.update().where(game_updates.c.id == update["id"],
            game_updates.c.status == "draft").values(status="published", published_at=self.now, content=content))
        return f"✅ v{version} 已发布并进入广播队列。\n广播任务：{job_id[:8]}｜目标群：{target_count}"

    def announce(self, content, room_id=None):
        text = content.strip()
        if not text:
            raise AnnouncementError("公告内容不能为空。")
        body = "📢 铃露公告\n\n" + text
        from dzmm_bot.presentation.compact import compact_text, decorate, fits, text_size
        body = compact_text(decorate(body))
        if not fits(body, reserve=5):
            size = text_size(body)
            newlines = body.count("\n")
            problems = []
            if size + 5 > 1000:
                problems.append(f"最终字数 {size}（含安全余量后 {size + 5}/1000，超出 {size + 5 - 1000}）")
            if newlines > 10:
                problems.append(f"换行 {newlines}/10，超出 {newlines - 10} 次")
            raise AnnouncementError("⚠️ 公告未发送，未创建群发任务：" + "；".join(problems) + "。请缩短正文或减少换行后重发。")
        job_id, target_count = self._queue_job(None, "announcement", body,
                                               "room" if room_id else "global", room_id)
        return f"✅ 公告已加入群发队列。\n目标群：{target_count}"

    def status(self, version):
        update = self._update(version)
        if not update:
            return f"找不到 v{version} 更新。"
        job = self.db.execute(select(broadcast_jobs).where(broadcast_jobs.c.update_id == update["id"])
                              .order_by(broadcast_jobs.c.created_at.desc()).limit(1)).mappings().first()
        rows = self.db.execute(select(broadcast_deliveries).where(
            broadcast_deliveries.c.broadcast_job_id == job["id"])).mappings().all() if job else []
        return render_broadcast_status(version, job, rows)

    def failed_rooms(self, version):
        update = self._update(version)
        if not update:
            return f"找不到 v{version} 更新。"
        job = self.db.execute(select(broadcast_jobs).where(broadcast_jobs.c.update_id == update["id"])
                              .order_by(broadcast_jobs.c.created_at.desc()).limit(1)).mappings().first()
        if not job:
            return f"v{version} 尚无广播任务。"
        rows = self.db.execute(select(broadcast_deliveries.c.room_id).where(
            broadcast_deliveries.c.broadcast_job_id == job["id"],
            broadcast_deliveries.c.status == "failed").order_by(broadcast_deliveries.c.room_id)).scalars().all()
        return "失败群列表：\n" + "\n".join(rows[:50]) if rows else "当前没有失败群。"

    def retry_failed(self, version):
        update = self._update(version)
        if not update or update["status"] != "published":
            raise AnnouncementError(f"v{version} 没有可重试的已发布更新。")
        job = self.db.execute(select(broadcast_jobs).where(broadcast_jobs.c.update_id == update["id"])
                              .order_by(broadcast_jobs.c.created_at.desc()).limit(1).with_for_update()).mappings().first()
        if not job:
            return "没有可重试的广播任务。"
        failed = self.db.execute(select(broadcast_deliveries).where(
            broadcast_deliveries.c.broadcast_job_id == job["id"],
            broadcast_deliveries.c.status == "failed").with_for_update()).mappings().all()
        count = 0
        for delivery in failed:
            room = self.db.execute(select(rooms).where(rooms.c.id == delivery["room_id"],
                rooms.c.kind == "group", rooms.c.enabled == 1)).mappings().first()
            if not room:
                continue
            task_id = str(uuid.uuid4())
            self.db.execute(outbox.insert().values(id=task_id, room=delivery["room_id"], kind="group",
                reply_to_message_id=None, reply_to_sender_id=None, reply_to_text=None,
                text=job["content"], status="pending", created=self.now, available=self.now, attempts=0))
            self.db.execute(broadcast_deliveries.update().where(broadcast_deliveries.c.id == delivery["id"])
                .values(status="queued", last_error=None, outbox_id=task_id))
            count += 1
        self.db.execute(broadcast_jobs.update().where(broadcast_jobs.c.id == job["id"]).values(
            status="queued" if count else job["status"], finished_at=None if count else job["finished_at"]))
        return f"已重新入队 {count} 个失败群；成功群不会重复发送。"

    def latest(self):
        row = self.db.execute(select(game_updates).where(game_updates.c.status == "published")
                              .order_by(game_updates.c.published_at.desc()).limit(1)).mappings().first()
        if not row:
            return "目前还没有已发布的版本更新。"
        return render_update(self._update(row["version"], published_only=True))

    def get_update(self, version):
        update = self._update(version, published_only=True)
        return render_update(update) if update else f"找不到已发布的 v{version} 更新。"

    def history(self, limit=8):
        rows = self.db.execute(select(game_updates.c.version, game_updates.c.theme, game_updates.c.title,
            game_updates.c.published_at).where(game_updates.c.status == "published")
            .order_by(game_updates.c.published_at.desc()).limit(limit)).mappings().all()
        from dzmm_bot.presentation.information import render_update_history
        return render_update_history(rows)

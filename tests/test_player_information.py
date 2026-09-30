import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from dzmm_bot.application.announcement_service import AnnouncementService, AnnouncementError
from dzmm_bot.application.guidance_service import DailyBriefService, FactoryTodoProvider, GuidanceService, TodoService
from dzmm_bot.application.world_event_service import WorldEventService
from dzmm_bot.core import Inbound, create_app
from dzmm_bot.domain.update_manifest import load_manifest
from dzmm_bot.persistence.schema import (broadcast_deliveries, broadcast_jobs, game_updates,
    player_feature_tips, world_event_log, animals, products, factories, factory_jobs)
from dzmm_bot.persistence.transport import outbox, rooms
from dzmm_bot.settings import Settings
from dzmm_bot.store import Store


def manifest(version):
    return {"version": version, "title": "新玩法", "theme": "✨", "summary": "现在可以体验新玩法。",
            "features": [{"id": "new_feature", "name": "新玩法", "emoji": "✨",
                          "short_description": "体验新玩法", "details": "先查看，再开始体验。",
                          "commands": ["/新玩法"], "tips": ["先了解规则。"],
                          "trigger_commands": ["/牧场"], "onboarding_tip": "试试新玩法吧。"}],
            "commands": [{"command": "/新玩法", "description": "开始"}], "tips": ["注意规则"],
            "changelog": {"added": ["internal entry"], "changed": [], "fixed": [],
                          "migration": [], "known_issues": []}}


class InformationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 9, 26, tzinfo=timezone.utc).timestamp()
        self.store = Store("sqlite:///" + str(Path(self.temp.name) / "information.db"),
                           clock=lambda: self.now, admins={"admin"})
        self.addCleanup(self.store.engine.dispose)
        with self.store.engine.begin() as db:
            db.execute(rooms.insert(), [{"id": "g1", "kind": "group", "enabled": 1},
                {"id": "g2", "kind": "group", "enabled": 1},
                {"id": "dm", "kind": "private", "enabled": 1},
                {"id": "off", "kind": "group", "enabled": 0}])

    def send(self, text, sender="admin", mid=None):
        return self.store.receive(Inbound(room="g1", sender=sender, name=sender,
            message_id=mid or text + str(time.monotonic_ns()), text=text))

    def send_dm(self, text, sender="admin", mid=None):
        return self.store.receive(Inbound(room="dm", kind="private", sender=sender, name=sender,
            message_id=mid or text + str(time.monotonic_ns()), text=text))

    def rows(self, table):
        with self.store.engine.connect() as db:
            return [dict(row) for row in db.execute(select(table)).mappings()]

    def _create_publish(self, version="1.6.0"):
        with patch("dzmm_bot.application.announcement_service.load_manifest", return_value=manifest(version)):
            with self.store.engine.begin() as db:
                service = AnnouncementService(db, self.now, "admin")
                service.create_update(version)
                job_id = service.publish(version).split("广播任务：")[1].split("｜")[0]
        return next(row for row in self.rows(broadcast_jobs) if row["id"].startswith(job_id))

    def test_update_create_unique_preview_and_publish_rooms(self):
        with patch("dzmm_bot.application.announcement_service.load_manifest", return_value=manifest("1.6.0")):
            result = self.send("/创建更新 1.6.0")
            self.assertTrue(result["queued"])
            preview = self.send("/预览更新 1.6.0")
            self.assertTrue(preview["queued"])
            with self.store.engine.begin() as db:
                service = AnnouncementService(db, self.now, "admin")
                expected = service._update("1.6.0")
                preview_body = service.preview("1.6.0").split("——\n", 1)[1]
            self.assertEqual(preview_body, expected["content"])
            self.send("/发布更新 1.6.0")
            self.assertEqual(len(self.rows(broadcast_jobs)), 1)
            delivery_rows = self.rows(broadcast_deliveries)
            self.assertEqual({row["room_id"] for row in delivery_rows}, {"g1", "g2"})
            broadcast_outbox = [row for row in self.rows(outbox) if row["reply_to_message_id"] is None]
            self.assertEqual(len(broadcast_outbox), 2)
            self.assertTrue(all(row["text"] == expected["content"] for row in broadcast_outbox))
            with self.assertRaises(AnnouncementError):
                with self.store.engine.begin() as db:
                    AnnouncementService(db, self.now, "admin").create_update("1.6.0")

    def test_documented_manifest_template_loads_with_optional_bom(self):
        template = Path("docs/update-manifest-template.json").read_text(encoding="utf-8")
        value = json.loads(template)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (value["version"] + ".json")
            path.write_text(template, encoding="utf-8-sig")
            with patch("dzmm_bot.domain.update_manifest.MANIFEST_DIR", Path(directory)):
                self.assertEqual(load_manifest(value["version"])["title"], value["title"])

    def test_latest_specific_update_history_and_admin_changelog(self):
        for version, title, offset in (("1.4.0", "旧版本", 86400), ("1.5.0", "新版本", 10)):
            raw = json.dumps(manifest(version), ensure_ascii=False)
            with self.store.engine.begin() as db:
                db.execute(game_updates.insert().values(id="u" + version, version=version, title=title, theme="✨",
                    summary=title, content="notice", status="published", created_at=self.now - offset,
                    published_at=self.now - offset, created_by="admin", manifest_json=raw,
                    changelog_json=json.dumps({"added": ["private technical detail"]})))
        with self.store.engine.begin() as db:
            service = AnnouncementService(db, self.now, "player")
            self.assertIn("v1.5.0", service.latest())
            self.assertIn("v1.4.0", service.get_update("1.4.0"))
            self.assertEqual(service.history().count("v1."), 2)
            router_output = __import__("dzmm_bot.application.command_router", fromlist=["CommandRouter"]).CommandRouter(
                db, self.now, "secret", admins={"admin"})
            from types import SimpleNamespace
            self.assertNotIn("private technical detail", router_output.execute_information(
                ["/更新日志", "1.5.0"], SimpleNamespace(sender="player", text="/更新日志 1.5.0")))
            self.assertIn("private technical detail", router_output.execute_information(
                ["/更新日志", "1.5.0"], SimpleNamespace(sender="admin", text="/更新日志 1.5.0")))

    def test_admin_permission_and_player_native_reply(self):
        self.send("/发布更新 1.6.0", sender="player", mid="unauthorized")
        self.assertIn("仅限管理员", next(row["text"] for row in self.rows(outbox)
            if row["reply_to_message_id"] == "unauthorized"))
        self.send("/更新", sender="player", mid="latest")
        reply = next(row for row in self.rows(outbox) if row["reply_to_message_id"] == "latest")
        self.assertEqual(reply["reply_to_sender_id"], "player")
        self.assertIn("还没有已发布", reply["text"])

    def test_admin_private_announcement_command_broadcasts_to_enabled_groups(self):
        result = self.send_dm("/公告 今晚有新活动，请来参加。", mid="dm-announcement")
        self.assertTrue(result["queued"])
        group_tasks = [row for row in self.rows(outbox) if row["kind"] == "group"]
        self.assertEqual({row["room"] for row in group_tasks}, {"g1", "g2"})
        self.assertTrue(all(row["reply_to_message_id"] is None for row in group_tasks))
        self.assertTrue(all("今晚有新活动" in row["text"] for row in group_tasks))
        dm_reply = next(row for row in self.rows(outbox) if row["reply_to_message_id"] == "dm-announcement")
        self.assertEqual(dm_reply["kind"], "private")
        self.assertEqual(dm_reply["reply_to_sender_id"], "admin")
        self.assertIn("目标群：2", dm_reply["text"])

    def test_empty_private_announcement_command_returns_usage_without_broadcast(self):
        result = self.send_dm("/\u516c\u544a", mid="dm-announcement-empty")
        self.assertTrue(result["queued"])
        dm_reply = next(row for row in self.rows(outbox)
                        if row["reply_to_message_id"] == "dm-announcement-empty")
        self.assertEqual(dm_reply["kind"], "private")
        self.assertIn("\u516c\u544a\u683c\u5f0f", dm_reply["text"])
        self.assertEqual(self.rows(broadcast_jobs), [])

    def test_oversized_admin_private_announcement_reports_limits_without_broadcast(self):
        result = self.send_dm("/公告 " + "字" * 1100, mid="dm-announcement-too-long")
        self.assertTrue(result["queued"])
        self.assertEqual(self.rows(broadcast_jobs), [])
        self.assertFalse(any(row["kind"] == "group" for row in self.rows(outbox)))
        dm_reply = next(row for row in self.rows(outbox)
                        if row["reply_to_message_id"] == "dm-announcement-too-long")
        self.assertIn("最终字数", dm_reply["text"])
        self.assertIn("超出", dm_reply["text"])
        self.assertIn("未创建群发任务", dm_reply["text"])
        self.assertEqual(dm_reply["reply_to_sender_id"], "admin")
        self.assertEqual(dm_reply["room"], "dm")

    def test_admin_plain_private_text_is_not_broadcast(self):
        result = self.send_dm("这不是公告", mid="dm-plain-text")
        self.assertFalse(result["queued"])
        self.assertEqual(self.rows(broadcast_jobs), [])

    def test_non_admin_private_announcement_command_is_not_broadcast(self):
        result = self.send_dm("/公告 不是公告", sender="player", mid="dm-player-text")
        self.assertTrue(result["queued"])
        dm_reply = next(row for row in self.rows(outbox) if row["reply_to_message_id"] == "dm-player-text")
        self.assertIn("仅限管理员", dm_reply["text"])
        self.assertEqual(self.rows(broadcast_jobs), [])

    def test_announcement_command_requires_private_chat(self):
        result = self.send("/公告 不应在群里直接群发", mid="group-announcement")
        self.assertTrue(result["queued"])
        self.assertEqual(self.rows(broadcast_jobs), [])
        reply = next(row for row in self.rows(outbox) if row["reply_to_message_id"] == "group-announcement")
        self.assertIn("请私聊 Bot", reply["text"])

    def test_player_information_commands_keep_native_message_reference(self):
        commands = ("/更新", "/更新日志", "/怎么玩", "/今日", "/待办", "/预览更新 1.0.0",
                    "/公告状态 1.0.0", "/未知指令")
        for index, command in enumerate(commands):
            self.send(command, sender="player", mid=f"native-{index}")
        replies = {row["reply_to_message_id"]: row for row in self.rows(outbox)
                   if row["reply_to_message_id"] is not None}
        for index in range(len(commands)):
            self.assertEqual(replies[f"native-{index}"]["reply_to_sender_id"], "player")

    def test_publish_without_active_room_rolls_back_as_draft(self):
        with patch("dzmm_bot.application.announcement_service.load_manifest", return_value=manifest("1.6.0")):
            with self.store.engine.begin() as db:
                AnnouncementService(db, self.now, "admin").create_update("1.6.0")
            with self.store.engine.begin() as db:
                db.execute(rooms.update().where(rooms.c.kind == "group").values(enabled=0))
            with self.assertRaises(AnnouncementError):
                with self.store.engine.begin() as db:
                    AnnouncementService(db, self.now, "admin").publish("1.6.0")
            with self.store.engine.connect() as db:
                self.assertEqual(db.execute(select(game_updates.c.status)).scalar_one(), "draft")
                self.assertEqual(db.execute(select(func.count()).select_from(broadcast_jobs)).scalar_one(), 0)

    def test_broadcast_result_retry_failed_only_and_status(self):
        job = self._create_publish()
        deliveries = self.rows(broadcast_deliveries)
        first, second = deliveries
        for delivery, action, platform_id, error in ((first, "sent", "platform-1", None),
                                                       (second, "failed", None, "rejected")):
            task = next(row for row in self.rows(outbox) if row["id"] == delivery["outbox_id"])
            with self.store.engine.begin() as db:
                db.execute(outbox.update().where(outbox.c.id == task["id"]).values(
                    status="sending", lease="test-lease", lease_until=time.time() + 120, attempts=1))
            self.assertTrue(self.store.transition(task["id"], "test-lease", action,
                platform_id=platform_id, error=error))
        with self.store.engine.begin() as db:
            service = AnnouncementService(db, self.now, "admin")
            status = service.status("1.6.0")
            self.assertIn("成功：1", status)
            self.assertIn("失败：1", status)
            self.assertIn("已重新入队 1 个失败群", service.retry_failed("1.6.0"))
        rows = self.rows(broadcast_deliveries)
        success = next(row for row in rows if row["room_id"] == first["room_id"])
        failed = next(row for row in rows if row["room_id"] == second["room_id"])
        self.assertEqual(success["status"], "success")
        self.assertEqual(failed["status"], "queued")
        self.assertEqual(len(self.rows(broadcast_jobs)), 1)

    def test_broadcast_queue_scale_10_100_500(self):
        counts = []
        version_idx = 0
        for target in (10, 100, 500):
            with self.store.engine.begin() as db:
                existing = db.execute(select(func.count()).select_from(rooms).where(
                    rooms.c.kind == "group", rooms.c.enabled == 1)).scalar_one()
                db.execute(rooms.insert(), [{"id": f"load-{n}", "kind": "group", "enabled": 1}
                    for n in range(existing, target)])
            version_idx += 1
            version = f"2.0.{version_idx}"
            with patch("dzmm_bot.application.announcement_service.load_manifest", return_value=manifest(version)):
                with self.store.engine.begin() as db:
                    svc = AnnouncementService(db, self.now + version_idx, "admin")
                    svc.create_update(version)
                    svc.publish(version)
            job = next(row for row in self.rows(broadcast_jobs) if row["update_id"] and
                       row["content"].startswith(f"🎉 铃露更新公告 · v{version}"))
            count = sum(row["broadcast_job_id"] == job["id"] for row in self.rows(broadcast_deliveries))
            counts.append(count)
        self.assertEqual(counts, [10, 100, 500])

    def test_how_to_enabled_and_unreleased_hidden(self):
        with self.store.engine.begin() as db:
            guidance = GuidanceService(db)
            self.assertIn("牧场", guidance.how_to())
            self.assertIn("加工厂", guidance.how_to())
            self.assertIn("找不到", guidance.how_to("宠物"))
            detail = guidance.how_to("牧场")
            self.assertIn("/牧场", detail)
            self.assertIn("/喂食", detail)

    def test_published_feature_override_and_stage_gating(self):
        older, newer = manifest("1.4.0"), manifest("1.5.0")
        newer["features"][0]["details"] = "更新后的指南"
        with self.store.engine.begin() as db:
            for idx, value in enumerate((older, newer)):
                db.execute(game_updates.insert().values(id=f"feature-{idx}", version=value["version"],
                    title=value["title"], theme=value["theme"], summary=value["summary"], content="notice",
                    status="published", created_at=self.now + idx, published_at=self.now + idx,
                    created_by="admin", manifest_json=json.dumps(value, ensure_ascii=False), changelog_json="{}"))
            self.assertIn("更新后的指南", GuidanceService(db).how_to("新玩法"))
            ranch_only = GuidanceService(db, stage="ranch")
            self.assertNotIn("加工厂", ranch_only.how_to())
            self.assertIn("新玩法", ranch_only.how_to())
            self.assertEqual(GuidanceService(db, stage="m0").how_to(), "目前没有已开放的玩法指南。")

    def test_feature_tip_first_time_trigger_and_only_once(self):
        raw = json.dumps(manifest("1.6.0"), ensure_ascii=False)
        with self.store.engine.begin() as db:
            db.execute(game_updates.insert().values(id="u1", version="1.6.0", title="新玩法", theme="✨",
                summary="新功能", content="notice", status="published", created_at=self.now,
                published_at=self.now, created_by="admin", manifest_json=raw, changelog_json="{}"))
            guidance = GuidanceService(db, now=self.now)
            self.assertIsNone(guidance.maybe_tip("p1", "/行情"))
            self.assertIn("新玩法", guidance.maybe_tip("p1", "/牧场"))
            self.assertIsNone(guidance.maybe_tip("p1", "/牧场"))
            self.assertEqual(db.execute(select(func.count()).select_from(player_feature_tips)).scalar_one(), 1)

    def test_feature_tip_is_appended_after_successful_trigger_command(self):
        value = manifest("1.6.0")
        value["features"][0]["trigger_commands"] = ["/牧场"]
        with self.store.engine.begin() as db:
            db.execute(game_updates.insert().values(id="tip-update", version=value["version"],
                title=value["title"], theme=value["theme"], summary=value["summary"], content="notice",
                status="published", created_at=self.now, published_at=self.now, created_by="admin",
                manifest_json=json.dumps(value, ensure_ascii=False), changelog_json="{}"))
        self.send("/注册 新玩家", sender="tip-player", mid="tip-register")
        self.send("/牧场", sender="tip-player", mid="tip-first")
        first = next(row["text"] for row in self.rows(outbox) if row["reply_to_message_id"] == "tip-first")
        self.assertIn("🆕", first)
        self.send("/牧场", sender="tip-player", mid="tip-second")
        second = next(row["text"] for row in self.rows(outbox) if row["reply_to_message_id"] == "tip-second")
        self.assertNotIn("🆕", second)

    def test_suggestion_is_conservative_and_excludes_admin(self):
        from dzmm_bot.application.command_router import CommandRouter
        self.assertEqual(CommandRouter.suggest_command("/投厂 蛋糕"), "/投产 蛋糕")
        self.assertIsNone(CommandRouter.suggest_command("/完全胡言乱语"))
        self.assertNotIn("/发布更新", str(CommandRouter.suggest_command("/发布更")))

    def test_todo_provider_registration_and_empty(self):
        class Provider:
            def items(self):
                return [{"priority": 1, "due_at": 2, "text": "自定义待办", "action_command": "/行动"}]
        with self.store.engine.begin() as db:
            todo = TodoService(db, "p", self.now, providers=[])
            self.assertIn("没有待处理", todo.render())
            todo.register_provider(Provider())
            self.assertIn("自定义待办", todo.render())
            with self.assertRaises(TypeError):
                todo.register_provider(object())
            class RegisteredProvider:
                def __init__(self, db, player, now):
                    self.db, self.player, self.now = db, player, now
                def items(self):
                    return []
            TodoService.register_provider_type(RegisteredProvider)
            self.addCleanup(TodoService.PROVIDER_TYPES.remove, RegisteredProvider)
            self.assertIn(RegisteredProvider, TodoService.PROVIDER_TYPES)

    def test_todo_ranch_collect_and_feed_expiry(self):
        with self.store.engine.begin() as db:
            db.execute(animals.insert().values(id="a1", player_id="p", display_id="a1", animal_type="chicken",
                space=1, feed_cost=1, base_interval_hours=4, interval_level=1, production_until=self.now + 1800,
                next_production_at=self.now + 120, last_settled_at=self.now, status="active", rule_version="v1",
                nonce="n", seed="s", seed_commitment="c", sequence=1, affection=0, feed_streak=0,
                premium_feed_active=False, cycle_progress=0, last_feed_reward_at=0))
            todo = TodoService(db, "p", self.now).render()
            self.assertIn("饲料", todo)
            db.execute(products.insert().values(id="pr1", player_id="p", product_type="egg", quantity=2,
                source_animal_id="a1", produced_at=self.now, collected_at=None, sequence=1, config_version="v1"))
            self.assertIn("待收取", TodoService(db, "p", self.now).render())

    def test_todo_factory_ready_and_processing(self):
        with self.store.engine.begin() as db:
            db.execute(factories.insert().values(player_id="p", level=1, line_count=1, created_at=self.now,
                updated_at=self.now, rush_used_on=None, rush_count=0))
            db.execute(factory_jobs.insert().values(id="fj1", player_id="p", line_no=1, recipe_id="cake",
                recipe_version="v1", batch_quantity=1, started_at=self.now, finish_at=self.now + 120,
                status="processing", input_snapshot_json="{}", output_snapshot_json="{}", failure_roll="0.5",
                critical_roll="0.5", failed=False, critical=False, collected_at=None, market_window_id=None,
                price_snapshot_json="{}", failure_rate=0, critical_rate=0, duration_multiplier=1,
                expedited_at=None, cancelled_at=None, cancel_refund_json=None))
            self.assertIn("分钟后完成", FactoryTodoProvider(db, "p", self.now).items()[0]["text"])
            db.execute(factory_jobs.update().where(factory_jobs.c.id == "fj1").values(finish_at=self.now - 1))
            self.assertIn("可以取货", FactoryTodoProvider(db, "p", self.now).items()[0]["text"])

    def test_daily_brief_only_current_modules_and_recent_update(self):
        with self.store.engine.begin() as db:
            self.assertIn("当前行情", DailyBriefService(db, self.now).render())
            self.assertNotIn("天气", DailyBriefService(db, self.now).render())
            db.execute(game_updates.insert().values(id="old", version="1.0.0", title="旧", theme="✨",
                summary="旧版本", content="old", status="published", created_at=self.now - 10 * 86400,
                published_at=self.now - 10 * 86400, created_by="admin", manifest_json="{}", changelog_json="{}"))
            self.assertNotIn("旧版本", DailyBriefService(db, self.now).render())

    def test_daily_market_recommendation_uses_market_service_label(self):
        from decimal import Decimal
        with patch("dzmm_bot.domain.economy.market_price", return_value=Decimal("99")), \
             patch("dzmm_bot.domain.economy.market_trend", return_value=("📈", "走强")):
            with self.store.engine.begin() as db:
                brief = DailyBriefService(db, self.now).render()
        self.assertIn("今日建议", brief)
        self.assertIn("行情走强", brief)

    def test_world_event_threshold_cooldowns_scopes_and_queue(self):
        with self.store.engine.begin() as db:
            events = WorldEventService(db, self.now, harvest_threshold=100, player_cooldown=3600, global_cooldown=300)
            self.assertFalse(events.record_harvest("p1", "g1", {"egg": 99}))
            self.assertTrue(events.record_harvest("p1", "g1", {"egg": 100}))
            self.assertFalse(events.record_harvest("p1", "g1", {"egg": 120}))
            self.assertFalse(WorldEventService(db, self.now + 1, 100, 3600, 300).record_harvest("p2", "g1", {"egg": 100}))
            critical = {"critical": True, "quantity": 2, "recipe": {"id": "cake", "display_name": "蛋糕"}}
            self.assertFalse(WorldEventService(db, self.now + 1, 100, 3600, 300)
                .record_factory_critical("p2", "g1", [critical]))
            self.assertFalse(WorldEventService(db, self.now + 1).record_harvest("p2", "g1", {"egg": 100}))
            self.assertEqual(db.execute(select(func.count()).select_from(world_event_log)).scalar_one(), 1)
            second = WorldEventService(db, self.now + 3601, 100, 3600, 300)
            self.assertTrue(second.record_harvest("p1", "g1", {"egg": 100}, scope="global"))
            log = db.execute(select(world_event_log).order_by(world_event_log.c.created_at.desc())).mappings().first()
            job = db.execute(select(broadcast_jobs).where(broadcast_jobs.c.id ==
                db.execute(select(broadcast_deliveries.c.broadcast_job_id).where(
                    broadcast_deliveries.c.created_at == self.now + 3601).limit(1)).scalar_one())).mappings().one()
            self.assertEqual(log["scope"], "global")
            self.assertIsNone(log["broadcasted_at"])
            self.assertEqual(job["scope"], "global")
            self.assertEqual(job["target_count"], 2)

    def test_world_events_disabled_until_policy_is_configured(self):
        with self.store.engine.begin() as db:
            self.assertFalse(WorldEventService(db, self.now).record_harvest("p", "g1", {"egg": 1000}))
            self.assertEqual(db.execute(select(func.count()).select_from(world_event_log)).scalar_one(), 0)

    def test_world_event_factory_critical_uses_room_and_renderer(self):
        result = {"critical": True, "quantity": 2,
                  "recipe": {"id": "grand_gift", "display_name": "大礼包"}}
        with self.store.engine.begin() as db:
            self.assertTrue(WorldEventService(db, self.now, 100, 3600, 300).record_factory_critical("p1", "g1", [result]))
            event = db.execute(select(world_event_log)).mappings().one()
            job = db.execute(select(broadcast_jobs)).mappings().one()
            self.assertEqual(event["event_type"], "factory_critical")
            self.assertEqual((event["scope"], job["scope"], job["target_count"]), ("room", "room", 1))
            self.assertIn("大礼包", job["content"])
        task = next(row for row in self.rows(outbox) if row["reply_to_message_id"] is None)
        with self.store.engine.begin() as db:
            db.execute(outbox.update().where(outbox.c.id == task["id"]).values(
                status="sending", lease="world-lease", lease_until=time.time() + 120, attempts=1))
        self.assertTrue(self.store.transition(task["id"], "world-lease", "sent", platform_id="world-ack"))
        self.assertIsNotNone(self.rows(world_event_log)[0]["broadcasted_at"])

    def test_admin_manual_resolution_updates_broadcast_delivery(self):
        cfg = Settings(database_url="sqlite:///" + str(Path(self.temp.name) / "api.db"),
                       core_token="c" * 32, admin_token="a" * 32)
        from fastapi.testclient import TestClient
        with TestClient(create_app(cfg)) as client:
            client.put("/admin/rooms", headers={"X-Admin-Token": cfg.admin_token},
                       json={"id": "target", "kind": "group", "enabled": True})
            now = time.time()
            with client.app.state.store.engine.begin() as db:
                job_id, delivery_id, task_id = "job", "delivery", "task"
                db.execute(broadcast_jobs.insert().values(id=job_id, update_id=None, type="announcement",
                    status="running", target_count=1, success_count=0, failure_count=0, created_at=now,
                    started_at=now, finished_at=None, content="announcement", scope="global"))
                db.execute(broadcast_deliveries.insert().values(id=delivery_id, broadcast_job_id=job_id,
                    room_id="target", status="sending", attempt_count=1, last_error=None, sent_at=None,
                    created_at=now, outbox_id=task_id))
                db.execute(outbox.insert().values(id=task_id, room="target", kind="group", text="announcement",
                    status="uncertain", created=now, available=now, attempts=1, lease="lease"))
            response = client.post(f"/admin/outbound/{task_id}/resolve", headers={"X-Admin-Token": cfg.admin_token},
                                   json={"status": "sent", "platform_id": "verified"})
            self.assertEqual(response.status_code, 200)
            with client.app.state.store.engine.connect() as db:
                self.assertEqual(db.execute(select(broadcast_deliveries.c.status)).scalar_one(), "success")
                job = db.execute(select(broadcast_jobs)).mappings().one()
                self.assertEqual((job["status"], job["success_count"], job["failure_count"]), ("completed", 1, 0))


if __name__ == "__main__":
    unittest.main()

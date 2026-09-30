import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from dzmm_bot.application.outbound_metrics import _percentiles
from dzmm_bot.gateway import Gateway, split_long_reply
from dzmm_bot.settings import Settings
from dzmm_bot.socket_limiter import SocketLimiter
from dzmm_bot.worker import Worker


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.delays = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.delays.append(seconds)
        self.now += seconds


def task(room="g1", kind="group", text="reply"):
    return {"id": "task", "room": room, "kind": kind, "text": text,
            "reply_to_message_id": "source", "reply_to_sender_id": "player",
            "reply_to_text": "/帮助"}


class SocketLimiterTests(unittest.IsolatedAsyncioTestCase):
    def make_gateway(self, clock, replies=None, **config):
        limiter = SocketLimiter(clock=clock.monotonic, sleeper=clock.sleep,
                                wall_clock=lambda: 1000 + clock.now, **config)
        gateway = Gateway(Settings(), None, AsyncMock(), limiter=limiter)
        gateway.own_id = "bot"
        gateway.allowed = {"g1", "g2", "dm"}
        gateway.socket = Mock(connected=True)
        times, payloads = [], []
        outcomes = iter(replies or [])

        async def call(event, payload, timeout):
            self.assertEqual(event, "message:send")
            times.append(clock.now)
            payloads.append(payload)
            result = next(outcomes, {"success": True})
            if isinstance(result, Exception):
                raise result
            return result

        gateway.socket.call = AsyncMock(side_effect=call)
        return gateway, times, payloads

    async def test_room_pacing_is_independent_and_private_floor_is_preserved(self):
        clock = FakeClock()
        gateway, times, _ = self.make_gateway(clock)
        for item in (task("g1"), task("g2"), task("dm", "private"), task("dm", "private")):
            self.assertEqual((await gateway.send_socket(item))["action"], "sent")
        self.assertEqual(times, [0, 0, 0, 5])
        self.assertEqual(clock.delays, [5])
        self.assertEqual(gateway.limiter.snapshot()["current_interval_seconds"], 0.2)

    async def test_platform_ack_timing_separates_socket_lock_wait(self):
        clock = FakeClock()
        gateway, _, _ = self.make_gateway(clock)
        await gateway.socket_lock.acquire()
        with patch("dzmm_bot.gateway.log_platform_timing") as timing:
            send = asyncio.create_task(gateway.send_socket(task()))
            await asyncio.sleep(0.02)
            gateway.socket_lock.release()
            self.assertEqual((await send)["action"], "sent")
        phases = timing.call_args.kwargs["phase_ms"]
        self.assertGreaterEqual(phases["socket_lock_wait"], 15)
        self.assertLess(phases["socket_call"], 10)
        self.assertIn("limiter_wait", phases)
        self.assertIn("room_lock_wait", phases)

    async def test_rate_limit_backoff_is_scoped_to_affected_room(self):
        clock = FakeClock()
        limiter = SocketLimiter(clock=clock.monotonic, sleeper=clock.sleep)
        limiter.rate_limited("g1")
        self.assertEqual(limiter.snapshot("g1")["current_interval_seconds"], 0.4)
        self.assertEqual(limiter.snapshot("g2")["current_interval_seconds"], 0.2)
        self.assertEqual(await limiter.wait("g2", False), 0)
        self.assertEqual(await limiter.wait("g1", False), 60)

    async def test_long_split_paces_each_part_and_preserves_reference(self):
        clock = FakeClock()
        gateway, times, payloads = self.make_gateway(clock, [
            {"success": False, "error": "换行太多了"}, {"success": True}, {"success": True}])
        original_text = "\n".join(f"line {i}" for i in range(13))
        result = await gateway.send_socket(task(text=original_text))
        self.assertEqual(result["action"], "sent")
        self.assertEqual(times, [0, 0.2, 0.4])
        self.assertEqual([payload["message"]["content"]["text"] for payload in payloads],
                         [original_text, *split_long_reply(original_text)])
        for payload in payloads:
            content = payload["message"]["content"]
            self.assertEqual(content["reference"]["id"], "source")
        self.assertEqual(gateway.limiter.snapshot()["success_streak"], 2)

    async def test_partial_split_timeout_is_uncertain_and_never_resends(self):
        clock = FakeClock()
        import socketio
        gateway, times, _ = self.make_gateway(clock, [
            {"success": False, "error": "换行太多了"}, {"success": True},
            socketio.exceptions.SocketIOError("lost ack")])
        gateway.socket.disconnect = AsyncMock()
        outcome = await gateway.send_socket(task(text="\n".join(f"line {i}" for i in range(13))))
        self.assertEqual(outcome["action"], "uncertain")
        self.assertEqual(times, [0, 0.2, 0.4])
        self.assertEqual(gateway.socket.call.await_count, 3)
        self.assertEqual(gateway.limiter.snapshot()["current_interval_seconds"], 0.2)

    async def test_split_rate_limit_retries_only_before_any_part_is_sent(self):
        text = "\n".join(f"line {i}" for i in range(13))
        for first_part_sent, expected in ((False, "retry"), (True, "uncertain")):
            with self.subTest(first_part_sent=first_part_sent):
                clock = FakeClock()
                replies = [{"success": False, "error": "换行太多了"}]
                if first_part_sent:
                    replies.append({"success": True})
                replies.append({"success": False, "error": "请稍后再试"})
                gateway, times, _ = self.make_gateway(clock, replies)
                outcome = await gateway.send_socket(task(text=text))
                self.assertEqual(outcome["action"], expected)
                self.assertEqual(len(times), 3 if first_part_sent else 2)
                self.assertEqual(gateway.limiter.snapshot()["rate_limit_events"], 1)
                self.assertEqual(gateway.limiter.snapshot()["current_interval_seconds"], 0.4)

    async def test_explicit_rate_limit_backs_off_and_success_recovers_gradually(self):
        clock = FakeClock()
        gateway, times, _ = self.make_gateway(clock, [
            {"success": False, "error": "请稍后再试"},
            {"success": True}, {"success": True}, {"success": True}, {"success": True}],
            minimum=1, initial=1, maximum=60, success_window=2, success_step=.5,
            cooldown=60, backoff_factor=2)
        limited = await gateway.send_socket(task())
        self.assertEqual((limited["action"], limited["error"]), ("retry", "rate_limited"))
        self.assertEqual(gateway.limiter.snapshot()["current_interval_seconds"], 2)
        self.assertEqual(gateway.limiter.snapshot()["last_rate_limited_at"], 1000)
        self.assertEqual(gateway.limiter.snapshot()["rate_limit_events"], 1)
        for _ in range(4):
            self.assertEqual((await gateway.send_socket(task()))["action"], "sent")
        self.assertEqual(times, [0, 60, 62, 64, 65.5])
        self.assertEqual(gateway.limiter.snapshot()["current_interval_seconds"], 1)
        self.assertEqual(gateway.limiter.snapshot()["adjustments"], 3)

    async def test_rejection_and_unknown_ack_do_not_backoff_or_create_success(self):
        clock = FakeClock()
        gateway, times, _ = self.make_gateway(clock, [
            {"success": False, "error": "请勿发送重复内容"},
            {"unexpected": True}, {"success": True}])
        self.assertEqual((await gateway.send_socket(task()))["action"], "failed")
        self.assertEqual((await gateway.send_socket(task()))["action"], "uncertain")
        self.assertEqual((await gateway.send_socket(task()))["action"], "sent")
        self.assertEqual(times, [0, 0.2, 0.4])
        self.assertEqual(gateway.limiter.snapshot()["adjustments"], 0)
        self.assertEqual(gateway.limiter.snapshot()["success_streak"], 1)

    async def test_primary_timeout_does_not_resend_or_treat_as_success(self):
        import socketio
        clock = FakeClock()
        gateway, times, _ = self.make_gateway(clock, [socketio.exceptions.SocketIOError("lost ack")])
        gateway.socket.disconnect = AsyncMock()
        outcome = await gateway.send_socket(task())
        self.assertEqual((outcome["action"], outcome["error"]), ("uncertain", "transport_unknown"))
        self.assertEqual(times, [0])
        self.assertEqual(gateway.socket.call.await_count, 1)
        self.assertEqual(gateway.limiter.snapshot()["success_streak"], 0)

    async def test_success_recovery_never_crosses_configured_minimum(self):
        clock = FakeClock()
        limiter = SocketLimiter(initial=2, minimum=1.5, success_window=1, success_step=.3,
                                clock=clock.monotonic, sleeper=clock.sleep)
        for _ in range(10):
            await limiter.wait("g", False)
            limiter.complete_attempt("g", False)
            limiter.accepted()
        self.assertEqual(limiter.current_interval, 1.5)

    async def test_restart_resets_to_initial_and_ten_hundred_five_hundred_baselines(self):
        for count, baseline_p95, trial_p95 in ((10, 18, 13.5), (100, 188, 141), (500, 948, 711)):
            with self.subTest(count=count):
                results = []
                for interval in (2, 1.5):
                    clock = FakeClock()
                    limiter = SocketLimiter(initial=interval, minimum=interval, clock=clock.monotonic,
                                            sleeper=clock.sleep, success_window=1000)
                    starts = []
                    for _ in range(count):
                        await limiter.wait("g", False)
                        starts.append(clock.now)
                        limiter.complete_attempt("g", False)
                        limiter.accepted()
                    results.append(_percentiles(starts))
                self.assertEqual(results[0]["samples"], count)
                self.assertEqual(results[0]["p95_seconds"], baseline_p95)
                self.assertEqual(results[1]["p95_seconds"], trial_p95)
                self.assertLess(results[1]["p95_seconds"], results[0]["p95_seconds"])
        first = SocketLimiter()
        first.rate_limited()
        self.assertGreater(first.current_interval, 0.2)
        self.assertEqual(SocketLimiter().current_interval, 0.2)

    async def test_worker_heartbeat_reports_limiter_without_sending(self):
        clock = FakeClock()
        gateway, _, _ = self.make_gateway(clock)
        core = Mock()
        core.post = AsyncMock(return_value=Mock(raise_for_status=Mock()))
        worker = Worker(Settings(mode="live"), core, Mock(), gateway)
        worker.drain_invite_once = AsyncMock(return_value=False)
        worker.drain_once = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await worker.outbound_loop()
        heartbeat = next(call for call in core.post.await_args_list if call.args[0] == "/internal/heartbeat")
        self.assertEqual(heartbeat.kwargs["json"]["limiter"]["current_interval_seconds"], 0.2)
        gateway.socket.call.assert_not_awaited()


class SocketSettingsTests(unittest.TestCase):
    def test_safe_defaults_and_missing_environment(self):
        cfg = Settings()
        self.assertEqual((cfg.socket_initial_interval, cfg.socket_min_interval,
                          cfg.socket_max_interval, cfg.socket_private_interval), (0.2, 0.2, 60, 5))
        with patch.dict("os.environ", {"DZMM_SOCKET_INITIAL_INTERVAL": "1.5",
                                        "DZMM_SOCKET_MIN_INTERVAL": "0.2"}):
            loaded = Settings.load()
            self.assertEqual((loaded.socket_initial_interval, loaded.socket_min_interval), (1.5, 0.2))

    def test_invalid_values_fail_at_startup(self):
        for values in ({"socket_min_interval": 3}, {"socket_initial_interval": 0},
                       {"socket_success_window": 0}, {"socket_rate_cooldown": 10},
                       {"socket_max_interval": float("nan")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings(**values)
        with patch.dict("os.environ", {"DZMM_SOCKET_MIN_INTERVAL": "oops"}):
            with self.assertRaises(ValueError):
                Settings.load()

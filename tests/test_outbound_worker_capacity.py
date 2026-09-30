import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from dzmm_bot.settings import Settings
from dzmm_bot.worker import Worker


class WorkerCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_gateway_limit_is_bounded(self):
        cfg = Settings(mode="simulate", outbound_inflight_limit=3)
        core = Mock(post=AsyncMock(return_value=Mock(raise_for_status=Mock())))
        worker = Worker(cfg, core, Mock())
        worker.drain_invite_once = AsyncMock(return_value=False)
        counts = {"active": 0, "peak": 0}
        all_started = asyncio.Event()

        async def drain():
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
            if counts["active"] == 3:
                all_started.set()
            await all_started.wait()
            counts["active"] -= 1
            return True

        worker.drain_once = drain
        with patch("dzmm_bot.worker.asyncio.sleep", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await worker.outbound_loop()
        self.assertEqual(counts, {"active": 0, "peak": 3})


class WorkerCapacitySettingsTests(unittest.TestCase):
    def test_default_and_bounds(self):
        self.assertEqual(Settings().outbound_inflight_limit, 1)
        for limit in (0, 5, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                Settings(outbound_inflight_limit=limit)

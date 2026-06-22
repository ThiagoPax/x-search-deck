import asyncio
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class DummyScheduler:
    def ingest(self, *args, **kwargs):
        pass

    def dispatch_scheduled(self):
        pass


class RefreshTestApp(server.XDeckApp):
    def __init__(self):
        super().__init__()
        self.subscriptions = {"col": {"query": "from:test"}}
        self.column_calls = 0
        self.cycles_started = 0
        async def fake_fetch_many(urls, column_ids=None):
            return [{"tweets": []} for _url in urls]
        self.bm.fetch_many = fake_fetch_many

    async def _apply_column_results(self, col_id, cfg, tweets, generation=None):
        self.column_calls += 1

    async def _run_refresh_cycle(self, source: str):
        self.cycles_started += 1
        await super()._run_refresh_cycle(source)


class RaisingRefreshApp(RefreshTestApp):
    async def _apply_column_results(self, col_id, cfg, tweets, generation=None):
        self.column_calls += 1
        raise RuntimeError("boom during refresh")


class PendingRefreshApp(RefreshTestApp):
    async def _apply_column_results(self, col_id, cfg, tweets, generation=None):
        self.column_calls += 1
        if self.column_calls == 1:
            self.schedule_refresh_all(source="live")


class RefreshCoalescingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [
            patch.object(server, "STAGGER_SECONDS", 0),
            patch.object(server, "REFRESH_GLOBAL_TIMEOUT", 1),
            patch.object(server, "is_critical_window_now", return_value=True),
            patch.object(server, "get_scheduler", return_value=DummyScheduler()),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        for item in reversed(self.patches):
            item.stop()

    async def test_exception_during_refresh_unlocks_global_state(self):
        app = RaisingRefreshApp()
        task = asyncio.create_task(app.refresh_all(source="auto"))
        app._refresh_task = task
        app._refresh_started_at = asyncio.get_running_loop().time()

        await task

        self.assertIsNone(app._refresh_task)
        self.assertIsNone(app._refresh_started_at)
        self.assertFalse(app._refresh_again)
        self.assertEqual(app.column_calls, 1)

    async def test_pending_refresh_executes_once_after_current_cycle(self):
        app = PendingRefreshApp()
        task = asyncio.create_task(app.refresh_all(source="live"))
        app._refresh_task = task
        app._refresh_started_at = asyncio.get_running_loop().time()

        await task

        self.assertEqual(app.cycles_started, 2)
        self.assertEqual(app.column_calls, 2)
        self.assertIsNone(app._refresh_task)
        self.assertFalse(app._refresh_again)

    async def test_manual_refresh_does_not_cancel_running_cycle(self):
        app = RefreshTestApp()
        old_task = asyncio.create_task(asyncio.sleep(60))
        app._refresh_task = old_task
        app._refresh_started_at = asyncio.get_running_loop().time() - 5

        app.schedule_refresh_all(source="manual")

        self.assertIs(app._refresh_task, old_task)
        self.assertFalse(old_task.cancelled())
        self.assertTrue(app._refresh_again)
        old_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await old_task


class MergeConflictResolutionTests(unittest.TestCase):
    def test_conflicted_refresh_files_have_no_merge_markers(self):
        for path in (
            Path("server.py"),
            Path("tests/test_refresh_coalescing.py"),
            Path("tests/test_runtime_cost_hotfix.py"),
        ):
            content = path.read_text(encoding="utf-8")
            for marker in ("<" * 7, "=" * 7, ">" * 7):
                self.assertNotIn(marker, content)


if __name__ == "__main__":
    unittest.main()

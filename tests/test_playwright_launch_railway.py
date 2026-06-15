import asyncio
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import server


class LaunchArgsStaticTests(unittest.TestCase):
    def test_single_process_removed_and_safe_args_kept(self):
        source = Path("server.py").read_text(encoding="utf-8")

        self.assertNotIn("--single-process", source)
        for arg in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--no-zygote",
        ):
            self.assertIn(arg, server.LAUNCH_ARGS)

    def test_launch_failure_path_logs_closes_and_raises_friendly_error(self):
        source = Path("server.py").read_text(encoding="utf-8")

        self.assertIn("playwright_launch_start", source)
        self.assertIn("playwright_launch_success", source)
        self.assertIn("playwright_launch_error type=%s message=%s rss_mb=%.1f", source)
        self.assertIn("await self._close(page, context, browser)", source)
        self.assertIn("Falha ao iniciar navegador temporário para esta coleta", source)


class DummyChromium:
    async def launch(self, **kwargs):
        raise RuntimeError("Target page, context or browser has been closed")


class DummyPlaywright:
    def __init__(self):
        self.chromium = DummyChromium()


class PlaywrightLaunchFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_failure_stops_playwright_and_returns_friendly_error(self):
        bm = server.BrowserManager()
        pw = DummyPlaywright()
        starter = AsyncMock(return_value=pw)
        stopper = AsyncMock()
        pw.stop = stopper

        class FakeAsyncPlaywright:
            async def start(self):
                return await starter()

        with patch.object(server, "async_playwright", return_value=FakeAsyncPlaywright()):
            with self.assertRaisesRegex(RuntimeError, "navegador temporário"):
                await bm.fetch("https://x.com/search?q=test")

        starter.assert_awaited_once()
        stopper.assert_awaited_once()

    async def test_launch_failure_during_column_does_not_lock_global_refresh(self):
        app = server.XDeckApp()
        app.subscriptions = {"col": {"query": "from:test"}}
        app.bm.fetch = AsyncMock(side_effect=RuntimeError("Falha ao iniciar navegador temporário para esta coleta"))
        messages = []

        async def capture(message):
            messages.append(message)

        app.broadcast = capture

        with patch.object(server, "STAGGER_SECONDS", 0), \
                patch.object(server, "is_critical_window_now", return_value=True):
            task = asyncio.create_task(app.refresh_all(source="manual"))
            app._refresh_task = task
            app._refresh_started_at = asyncio.get_running_loop().time()
            await task

        self.assertIsNone(app._refresh_task)
        self.assertIsNone(app._refresh_started_at)
        self.assertFalse(app._refresh_again)
        self.assertTrue(any(
            msg.get("type") == "status"
            and msg.get("status") == "error"
            and "Não foi possível abrir o navegador" in msg.get("message", "")
            for msg in messages
        ))


if __name__ == "__main__":
    unittest.main()

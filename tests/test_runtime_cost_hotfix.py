import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class RuntimeCostHotfixTests(unittest.IsolatedAsyncioTestCase):
    async def test_debug_runtime_payload_does_not_expose_env_or_cookies(self):
        os.environ["X_COOKIES_JSON"] = "super-secret-cookie-value"
        os.environ["RESEND_API_KEY"] = "super-secret-resend-key"
        app = server.XDeckApp()
        with patch.object(server, "chromium_process_summary", return_value={
            "chromium_process_count": 0,
            "chrome_process_count": 0,
            "browser_processes": [],
        }):
            response = await app.runtime_debug_handler(None)

        payload_text = response.text
        payload = json.loads(payload_text)

        self.assertIn("rss_mb", payload)
        self.assertIn("asyncio_tasks_count", payload)
        self.assertIn("refresh_in_progress", payload)
        self.assertIn("refresh_pending", payload)
        self.assertIn("operational_mode", payload)
        self.assertIn("critical_window", payload)
        self.assertIn("subscriptions_count", payload)
        self.assertIn("clients_count", payload)
        self.assertIn("timestamp", payload)
        self.assertIn("chromium_process_count", payload)
        self.assertIn("chrome_process_count", payload)
        self.assertIn("browser_processes", payload)
        self.assertNotIn("super-secret-cookie-value", payload_text)
        self.assertNotIn("super-secret-resend-key", payload_text)
        self.assertNotIn("X_COOKIES_JSON", payload_text)
        self.assertNotIn("RESEND_API_KEY", payload_text)

    async def test_manual_refresh_still_allowed_outside_critical_window(self):
        app = server.XDeckApp()
        app.subscriptions = {"col": {"query": "from:test"}}
        calls = []

        async def fake_fetch_many(urls, column_ids=None):
            calls.extend(urls)
            return [{"tweets": []} for _url in urls]

        with patch.object(server, "is_critical_window_now", return_value=False), \
                patch.object(server, "STAGGER_SECONDS", 0), \
                patch.object(app.bm, "fetch_many", side_effect=fake_fetch_many):
            app.schedule_refresh_all(source="manual")
            await app._refresh_task

        self.assertEqual(len(calls), 1)
        self.assertIsNone(app._refresh_task)
        self.assertFalse(app._refresh_again)


    async def test_five_column_cycle_uses_dynamic_watchdog_above_120s(self):
        self.assertEqual(server.cycle_watchdog_timeout(5), 375)

    async def test_column_error_preserves_old_results_and_continues(self):
        app = server.XDeckApp()
        app.subscriptions = {f"col{i}": {"query": f"from:test{i}"} for i in range(5)}
        app.results = {"col1": [{"url": "old", "text": "old tweet"}]}
        messages = []

        async def fake_broadcast(message):
            messages.append(message)

        async def fake_fetch_many(urls, column_ids=None):
            return [
                {"tweets": [{"url": "new0", "text": "new 0"}]},
                {"tweets": [], "error": "timeout test"},
                {"tweets": [{"url": "new2", "text": "new 2"}]},
                {"tweets": [{"url": "new3", "text": "new 3"}]},
                {"tweets": [{"url": "new4", "text": "new 4"}]},
            ]

        app.broadcast = fake_broadcast
        with patch.object(server, "is_critical_window_now", return_value=True), \
                patch.object(server, "STAGGER_SECONDS", 0), \
                patch.object(app.bm, "fetch_many", side_effect=fake_fetch_many):
            await app._run_refresh_cycle(source="live")

        self.assertEqual(app.results["col1"], [{"url": "old", "text": "old tweet"}])
        self.assertIn("col4", app.results)
        self.assertTrue(any(m.get("column") == "col1" and m.get("status") == "error" for m in messages))

    async def test_duplicate_pending_refresh_executes_at_most_one_extra_cycle(self):
        app = server.XDeckApp()
        app.subscriptions = {"col": {"query": "from:test"}}
        calls = 0

        async def fake_run(source):
            nonlocal calls
            calls += 1
            if calls == 1:
                app.schedule_refresh_all(source="auto")
                app.schedule_refresh_all(source="auto")

        with patch.object(server, "is_critical_window_now", return_value=True), \
                patch.object(app, "_run_refresh_cycle_with_timeout", side_effect=fake_run):
            task = asyncio.create_task(app.refresh_all(source="live"))
            app._refresh_task = task
            app._refresh_started_at = asyncio.get_running_loop().time()
            await task

        self.assertEqual(calls, 2)
        self.assertIsNone(app._refresh_task)
        self.assertFalse(app._refresh_again)

    async def test_auto_refresh_blocked_outside_critical_window(self):
        app = server.XDeckApp()
        app.subscriptions = {"col": {"query": "from:test"}}
        with patch.object(server, "is_critical_window_now", return_value=False):
            app.schedule_refresh_all(source="auto")
        self.assertIsNone(app._refresh_task)


class FrontendPollingStaticTests(unittest.TestCase):
    def test_operational_mode_polling_reduced_when_idle_or_hidden(self):
        html = Path("interface.html").read_text(encoding="utf-8")

        self.assertIn("OPERATIONAL_MODE_POLL_CRITICAL_MS = 60 * 1000", html)
        self.assertIn("OPERATIONAL_MODE_POLL_IDLE_MS = 60 * 1000", html)
        self.assertIn("OPERATIONAL_MODE_POLL_HIDDEN_MS = 30 * 60 * 1000", html)
        self.assertIn("document.hidden", html)
        self.assertIn("visibilitychange", html)
        self.assertIn("operational_mode_poll_interval", html)
        self.assertNotIn("setInterval(refreshOperationalMode, 60 * 1000)", html)
        self.assertIn("refreshOperationalMode({ force:true })", html)


if __name__ == "__main__":
    unittest.main()

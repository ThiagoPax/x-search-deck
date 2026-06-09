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

        async def fake_refresh_column(col_id, cfg=None, generation=None):
            calls.append((col_id, cfg, generation))

        with patch.object(server, "is_critical_window_now", return_value=False), \
                patch.object(server, "STAGGER_SECONDS", 0), \
                patch.object(app, "refresh_column", side_effect=fake_refresh_column):
            app.schedule_refresh_all(source="manual")
            await app._refresh_task

        self.assertEqual(len(calls), 1)
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
        self.assertIn("OPERATIONAL_MODE_POLL_IDLE_MS = 10 * 60 * 1000", html)
        self.assertIn("OPERATIONAL_MODE_POLL_HIDDEN_MS = 30 * 60 * 1000", html)
        self.assertIn("document.hidden", html)
        self.assertIn("visibilitychange", html)
        self.assertIn("operational_mode_poll_interval", html)
        self.assertNotIn("setInterval(refreshOperationalMode, 60 * 1000)", html)
        self.assertIn("refreshOperationalMode({ force:true })", html)


if __name__ == "__main__":
    unittest.main()
